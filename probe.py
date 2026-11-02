#!/usr/bin/env python3
"""Origin drift probe. Fail closed. Never fabricates a certificate."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PRODUCT = "https://a-11-oy.com"
PROOF = "https://a11oy.net"
TIMEOUT = 8
# Observed origin bodies are under 2 KB; anything past 1 MiB is not a probe response.
MAX_BODY_BYTES = 1024 * 1024
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int | None]:
    """Return (scheme, host, port) with the default port filled in. Raises ValueError on a bad port."""
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port if parts.port is not None else _DEFAULT_PORTS.get(scheme)
    return scheme, (parts.hostname or "").lower(), port


def _origin_label(url: str) -> str:
    try:
        scheme, host, port = _origin(url)
    except ValueError:
        return "an unparseable origin"
    return f"{scheme}://{host}" if port == _DEFAULT_PORTS.get(scheme) else f"{scheme}://{host}:{port}"


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only when it stays on the origin that was requested."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            same_origin = _origin(newurl) == _origin(req.full_url)
        except ValueError:
            same_origin = False
        # Close the 3xx response first: urllib otherwise drains its body with an
        # unbounded read() before following the redirect.
        fp.close()
        if not same_origin:
            reason = (
                f"cross-origin redirect refused: {_origin_label(req.full_url)} -> {_origin_label(newurl)}"
            )
            raise urllib.error.HTTPError(req.full_url, code, reason, headers, None)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(SameOriginRedirectHandler)


def _read_capped(res: Any, cap: int) -> bytes:
    """Read at most cap + 1 bytes, so an oversized body is detectable without buffering it."""
    chunks: list[bytes] = []
    total = 0
    while total <= cap:
        chunk = res.read(cap + 1 - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _reject_nonfinite_constant(_value: str) -> Any:
    raise ValueError("non-finite JSON number")


def _parse_finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _strict_json(raw: str) -> Any:
    return json.loads(
        raw,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite_constant,
        parse_float=_parse_finite_float,
    )


def get(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "szl-drift/1", "Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as res:
            cap = MAX_BODY_BYTES
            data = _read_capped(res, cap)
            if len(data) > cap:
                return {
                    "ok": False,
                    "status": res.status,
                    "body": None,
                    "error": f"response body exceeds {cap} bytes",
                }
            try:
                raw = data.decode("utf-8")
                body: Any = _strict_json(raw)
            except (UnicodeDecodeError, ValueError) as exc:
                message = (
                    exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
                )
                return {
                    "ok": False,
                    "status": res.status,
                    "body": None,
                    "error": f"invalid JSON response: {message}",
                }
            if not isinstance(body, dict):
                return {
                    "ok": False,
                    "status": res.status,
                    "body": None,
                    "error": f"invalid JSON object: got {type(body).__name__}",
                }
            return {"ok": True, "status": res.status, "body": body, "error": None}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "body": None, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status": None, "body": None, "error": str(exc)}


def main() -> int:
    honest = get(f"{PRODUCT}/api/a11oy/v1/honest")
    readyz = get(f"{PRODUCT}/readyz")
    health = get(f"{PROOF}/health.json")
    # Keep the defensive shape guard even though get() rejects non-object JSON:
    # tests and future callers can replace get(), and main must still degrade
    # rather than crash on a malformed result.
    raw_body = honest.get("body")
    body = raw_body if isinstance(raw_body, dict) else {}
    raw_doctrine = body.get("doctrine_lock")
    doctrine = raw_doctrine if isinstance(raw_doctrine, dict) else {}
    # Presence, not truthiness, selects the authoritative top-level observation.
    # An explicit 0/null must not be replaced by a nested fallback and thereby
    # hide drift in the source response.
    locked_formula_count = (
        body["locked_formula_count"]
        if "locked_formula_count" in body
        else doctrine.get("locked_formula_count")
    )
    report = {
        "schema": "szl.origin-drift/v1",
        "certified_production_ready": False,
        "product_honest": {
            "status": honest["status"],
            "locked_formula_count": locked_formula_count,
            "lambda": doctrine.get("lambda"),
            "kernel": doctrine.get("commit"),
            "git_sha": body.get("git_sha"),
        },
        "product_readyz": {
            "status": readyz["status"],
            "body": readyz.get("body"),
            "note": "200 ready is not a production certificate.",
        },
        "proof_health": {
            "status": health["status"],
            "sha": (health.get("body") or {}).get("sha") if isinstance(health.get("body"), dict) else None,
            "signer": (health.get("body") or {}).get("signer")
            if isinstance(health.get("body"), dict)
            else None,
            "note": "Static document. Not DSSE-LIVE.",
        },
        "errors": {
            "honest": honest["error"],
            "readyz": readyz["error"],
            "health": health["error"],
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
