# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SZL Holdings
"""Offline tests for the origin drift probe.

These tests never touch the public network. `probe.get` is exercised against a
stubbed `probe._OPENER.open` and against a throwaway HTTP server bound to
127.0.0.1, and `probe.main` is exercised with `probe.get` itself replaced, so
the suite is deterministic and safe to run in CI on a fork.

The properties under test are the two the probe exists to guarantee:

1. **Fail closed.** Every failure path returns a well-formed dict with
   ``ok=False`` rather than raising, so a dead or malformed endpoint degrades
   the report instead of crashing the run or being recorded as healthy.
2. **Never fabricates a certificate.** ``certified_production_ready`` is
   ``False`` in the report unconditionally — including when every upstream
   endpoint returns a healthy 200.
"""

from __future__ import annotations

import io
import json
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import probe


class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._buf = io.BytesIO(payload)
        self.status = status

    def read(self, amt: int | None = None) -> bytes:
        return self._buf.read(amt)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def test_get_parses_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe._OPENER,
        "open",
        lambda *_a, **_k: _FakeResponse(b'{"doctrine_lock": {"lambda": 14}}'),
    )
    result = probe.get("https://example.test/x")
    assert result["ok"] is True
    assert result["status"] == 200
    assert result["body"] == {"doctrine_lock": {"lambda": 14}}
    assert result["error"] is None


def test_get_fails_closed_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe._OPENER,
        "open",
        lambda *_a, **_k: _FakeResponse(b"<!doctype html><title>nope</title>"),
    )
    result = probe.get("https://example.test/x")
    assert result["ok"] is False
    assert result["status"] == 200
    assert result["body"] is None
    assert result["error"].startswith("invalid JSON response:")


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (b'["not", "an", "object"]', "list"),
        (b'"a string"', "str"),
        (b"42", "int"),
        (b"true", "bool"),
        (b"null", "NoneType"),
    ],
)
def test_get_fails_closed_on_json_that_is_not_an_object(
    monkeypatch: pytest.MonkeyPatch, payload: bytes, kind: str
) -> None:
    monkeypatch.setattr(
        probe._OPENER,
        "open",
        lambda *_a, **_k: _FakeResponse(payload),
    )
    result = probe.get("https://example.test/x")
    assert result["ok"] is False
    assert result["status"] == 200
    assert result["body"] is None
    assert result["error"] == f"invalid JSON object: got {kind}"


def test_get_fails_closed_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_k: object) -> None:
        raise urllib.error.HTTPError(  # type: ignore[arg-type]
            "https://example.test/x", 503, "boom", {}, None
        )

    monkeypatch.setattr(probe._OPENER, "open", _raise)
    result = probe.get("https://example.test/x")
    assert result["ok"] is False
    assert result["status"] == 503
    assert result["body"] is None
    assert result["error"]


def test_get_fails_closed_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_k: object) -> None:
        raise OSError("name or service not known")

    monkeypatch.setattr(probe._OPENER, "open", _raise)
    result = probe.get("https://example.test/x")
    assert result["ok"] is False
    assert result["status"] is None
    assert result["error"]


class _EndlessResponse:
    """A body that never ends: an unbounded read() would never return."""

    status = 200

    def __init__(self) -> None:
        self.requested = 0

    def read(self, amt: int | None = None) -> bytes:
        assert amt is not None, "get() must never issue an unbounded read"
        self.requested += amt
        return b" " * amt

    def __enter__(self) -> _EndlessResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def test_get_reads_at_most_cap_plus_one_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    endless = _EndlessResponse()
    monkeypatch.setattr(probe, "MAX_BODY_BYTES", 1024)
    monkeypatch.setattr(probe._OPENER, "open", lambda *_a, **_k: endless)
    result = probe.get("https://example.test/x")
    assert result == {
        "ok": False,
        "status": 200,
        "body": None,
        "error": "response body exceeds 1024 bytes",
    }
    assert endless.requested == 1025


@pytest.fixture
def origin() -> Iterator[SimpleNamespace]:
    """A throwaway HTTP origin on 127.0.0.1 with scripted routes and a hit log."""
    routes: dict[str, tuple[int, dict[str, str], bytes]] = {}
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def do_GET(self) -> None:
            hits.append(self.path)
            status, headers, body = routes.get(self.path, (404, {}, b""))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(
            base=f"http://127.0.0.1:{server.server_port}",
            port=server.server_port,
            routes=routes,
            hits=hits,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _json_route(body: bytes) -> tuple[int, dict[str, str], bytes]:
    return 200, {"Content-Type": "application/json", "Content-Length": str(len(body))}, body


def test_get_accepts_a_valid_body_exactly_at_the_cap(
    monkeypatch: pytest.MonkeyPatch, origin: SimpleNamespace
) -> None:
    body = b'{"pad": "' + b"x" * 53 + b'"}'
    assert len(body) == 64
    monkeypatch.setattr(probe, "MAX_BODY_BYTES", 64)
    origin.routes["/ok"] = _json_route(body)
    result = probe.get(origin.base + "/ok")
    assert result == {"ok": True, "status": 200, "body": {"pad": "x" * 53}, "error": None}


@pytest.mark.parametrize("declared_length", [True, False], ids=["content-length", "close-delimited"])
def test_get_refuses_an_oversized_body(
    monkeypatch: pytest.MonkeyPatch, origin: SimpleNamespace, declared_length: bool
) -> None:
    body = b'{"pad": "' + b"x" * 4096 + b'"}'
    headers = {"Content-Type": "application/json"}
    if declared_length:
        headers["Content-Length"] = str(len(body))
    monkeypatch.setattr(probe, "MAX_BODY_BYTES", 64)
    origin.routes["/big"] = (200, headers, body)
    result = probe.get(origin.base + "/big")
    assert result == {
        "ok": False,
        "status": 200,
        "body": None,
        "error": "response body exceeds 64 bytes",
    }


def test_get_fails_closed_on_malformed_json_from_a_live_socket(origin: SimpleNamespace) -> None:
    origin.routes["/bad"] = _json_route(b'{"truncated": ')
    result = probe.get(origin.base + "/bad")
    assert result["ok"] is False
    assert result["status"] == 200
    assert result["body"] is None
    assert result["error"].startswith("invalid JSON response:")


def test_get_follows_a_same_origin_redirect(origin: SimpleNamespace) -> None:
    origin.routes["/hop"] = (302, {"Location": "/ok", "Content-Length": "0"}, b"")
    origin.routes["/ok"] = _json_route(b'{"ready": true}')
    result = probe.get(origin.base + "/hop")
    assert result == {"ok": True, "status": 200, "body": {"ready": True}, "error": None}
    assert origin.hits == ["/hop", "/ok"]


def test_get_does_not_drain_a_same_origin_redirect_body(origin: SimpleNamespace) -> None:
    # The 3xx claims a 1 GB body and sends none. urllib's default handler would
    # try to read all of it before following, and fail on the short read.
    origin.routes["/hop"] = (302, {"Location": "/ok", "Content-Length": str(10**9)}, b"")
    origin.routes["/ok"] = _json_route(b'{"ready": true}')
    result = probe.get(origin.base + "/hop")
    assert result == {"ok": True, "status": 200, "body": {"ready": True}, "error": None}


@pytest.mark.parametrize(
    ("location", "target_label"),
    [
        ("http://localhost:{port}/ok", "http://localhost:{port}"),
        ("http://127.0.0.1:1/ok", "http://127.0.0.1:1"),
        ("https://127.0.0.1:{port}/ok", "https://127.0.0.1:{port}"),
        ("//example.invalid/ok", "http://example.invalid"),
    ],
    ids=["other-host", "other-port", "other-scheme", "scheme-relative"],
)
def test_get_refuses_a_cross_origin_redirect(
    origin: SimpleNamespace, location: str, target_label: str
) -> None:
    origin.routes["/hop"] = (302, {"Location": location.format(port=origin.port), "Content-Length": "0"}, b"")
    origin.routes["/ok"] = _json_route(b'{"ready": true}')
    result = probe.get(origin.base + "/hop")
    assert result == {
        "ok": False,
        "status": 302,
        "body": None,
        "error": (
            "HTTP Error 302: cross-origin redirect refused: "
            f"{origin.base} -> {target_label.format(port=origin.port)}"
        ),
    }
    assert origin.hits == ["/hop"]


@pytest.mark.parametrize(
    ("newurl", "allowed"),
    [
        ("https://a-11-oy.com/readyz/", True),
        ("https://A-11-OY.com:443/readyz", True),
        ("https://a-11-oy.com:0/readyz", False),
        ("http://a-11-oy.com/readyz", False),
        ("https://a-11-oy.com:8443/readyz", False),
        ("https://a-11-oy.com.example.invalid/readyz", False),
        ("https://a-11-oy.com@example.invalid/readyz", False),
        ("https://a-11-oy.com:notaport/readyz", False),
    ],
)
def test_redirect_handler_allows_only_the_requested_origin(newurl: str, allowed: bool) -> None:
    handler = probe.SameOriginRedirectHandler()
    req = urllib.request.Request("https://a-11-oy.com/readyz")
    fp = io.BytesIO(b"redirect body")
    if allowed:
        follow = handler.redirect_request(req, fp, 301, "Moved", {}, newurl)
        assert follow is not None
        assert follow.full_url == newurl
    else:
        with pytest.raises(urllib.error.HTTPError) as refused:
            handler.redirect_request(req, fp, 301, "Moved", {}, newurl)
        assert refused.value.code == 301
        assert "cross-origin redirect refused" in str(refused.value)
    assert fp.closed


@pytest.mark.parametrize(("scheme", "default_port"), [("http", 80), ("https", 443)])
@pytest.mark.parametrize("port", [None, "default", 0, 8443])
def test_origin_defaults_only_an_omitted_port(
    scheme: str, default_port: int, port: int | str | None
) -> None:
    explicit_port = default_port if port == "default" else port
    suffix = "" if explicit_port is None else f":{explicit_port}"
    expected_port = default_port if explicit_port is None else explicit_port
    assert probe._origin(f"{scheme}://EXAMPLE.test{suffix}/readyz") == (
        scheme,
        "example.test",
        expected_port,
    )


def _run_main(capsys: pytest.CaptureFixture[str]) -> dict:
    assert probe.main() == 0
    return json.loads(capsys.readouterr().out)


def test_main_emits_schema_and_never_certifies_on_healthy_upstream(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy = {
        "ok": True,
        "status": 200,
        "body": {
            "git_sha": "a" * 40,
            "locked_formula_count": 749,
            "doctrine_lock": {"lambda": 14, "commit": "c7c0ba17", "locked_formula_count": 749},
            "sha": "b" * 40,
            "signer": "yachay",
        },
        "error": None,
    }
    monkeypatch.setattr(probe, "get", lambda _url: healthy)

    report = _run_main(capsys)

    assert report["schema"] == "szl.origin-drift/v1"
    # The load-bearing assertion: a fully healthy estate is still not certified.
    assert report["certified_production_ready"] is False
    assert report["product_honest"]["locked_formula_count"] == 749
    assert report["product_honest"]["lambda"] == 14
    assert report["product_honest"]["kernel"] == "c7c0ba17"
    assert report["proof_health"]["signer"] == "yachay"
    assert report["errors"] == {"honest": None, "readyz": None, "health": None}


@pytest.mark.parametrize("explicit_value", [0, None, False, ""])
def test_main_preserves_explicit_falsy_locked_formula_count(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    explicit_value: object,
) -> None:
    """An explicit top-level observation must never be hidden by fallback data."""

    observed = {
        "ok": True,
        "status": 200,
        "body": {
            "locked_formula_count": explicit_value,
            "doctrine_lock": {"locked_formula_count": 8},
        },
        "error": None,
    }
    monkeypatch.setattr(probe, "get", lambda _url: observed)

    report = _run_main(capsys)

    assert report["product_honest"]["locked_formula_count"] is explicit_value
    assert report["certified_production_ready"] is False


def test_main_uses_nested_locked_formula_count_only_when_top_level_is_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observed = {
        "ok": True,
        "status": 200,
        "body": {"doctrine_lock": {"locked_formula_count": 8}},
        "error": None,
    }
    monkeypatch.setattr(probe, "get", lambda _url: observed)

    report = _run_main(capsys)

    assert report["product_honest"]["locked_formula_count"] == 8


def test_main_degrades_without_raising_when_everything_is_down(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    down = {"ok": False, "status": None, "body": None, "error": "unreachable"}
    monkeypatch.setattr(probe, "get", lambda _url: down)

    report = _run_main(capsys)

    assert report["certified_production_ready"] is False
    assert report["product_honest"]["status"] is None
    assert report["product_honest"]["locked_formula_count"] is None
    assert report["proof_health"]["sha"] is None
    assert set(report["errors"].values()) == {"unreachable"}


@pytest.mark.parametrize("weird_body", [["not", "a", "dict"], "a string", 42, True])
def test_main_fails_closed_on_valid_json_that_is_not_an_object(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], weird_body: object
) -> None:
    """Main stays defensive even if a caller substitutes get() with bad data."""
    monkeypatch.setattr(
        probe,
        "get",
        lambda _url: {"ok": True, "status": 200, "body": weird_body, "error": None},
    )

    report = _run_main(capsys)

    assert report["certified_production_ready"] is False
    assert report["product_honest"]["locked_formula_count"] is None
    assert report["product_honest"]["lambda"] is None
    assert report["proof_health"]["sha"] is None
    assert report["proof_health"]["signer"] is None


def test_report_is_json_serialisable_and_sorted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(probe, "get", lambda _url: {"ok": True, "status": 200, "body": {}, "error": None})
    probe.main()
    out = capsys.readouterr().out
    assert json.loads(out)  # parses
    keys = list(json.loads(out).keys())
    assert keys == sorted(keys)  # main() prints with sort_keys=True
