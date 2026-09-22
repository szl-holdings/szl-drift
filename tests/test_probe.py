# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SZL Holdings
"""Offline tests for the origin drift probe.

These tests never touch the network. `probe.get` is exercised against a stubbed
`urllib.request.urlopen`, and `probe.main` is exercised with `probe.get` itself
replaced, so the suite is deterministic and safe to run in CI on a fork.

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
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import probe


class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._buf = io.BytesIO(payload)
        self.status = status

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def test_get_parses_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe.urllib.request,
        "urlopen",
        lambda *_a, **_k: _FakeResponse(b'{"doctrine_lock": {"lambda": 14}}'),
    )
    result = probe.get("https://example.test/x")
    assert result["ok"] is True
    assert result["status"] == 200
    assert result["body"] == {"doctrine_lock": {"lambda": 14}}
    assert result["error"] is None


def test_get_fails_closed_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe.urllib.request,
        "urlopen",
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
        probe.urllib.request,
        "urlopen",
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

    monkeypatch.setattr(probe.urllib.request, "urlopen", _raise)
    result = probe.get("https://example.test/x")
    assert result["ok"] is False
    assert result["status"] == 503
    assert result["body"] is None
    assert result["error"]


def test_get_fails_closed_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_k: object) -> None:
        raise OSError("name or service not known")

    monkeypatch.setattr(probe.urllib.request, "urlopen", _raise)
    result = probe.get("https://example.test/x")
    assert result["ok"] is False
    assert result["status"] is None
    assert result["error"]


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
