#!/usr/bin/env python3
"""Origin drift probe. Fail closed. Never fabricates a certificate."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

PRODUCT = "https://a-11-oy.com"
PROOF = "https://a11oy.net"
TIMEOUT = 8


def get(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "szl-drift/1", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            raw = res.read().decode("utf-8", "replace")
            try:
                body: Any = json.loads(raw)
            except json.JSONDecodeError as exc:
                return {
                    "ok": False,
                    "status": res.status,
                    "body": None,
                    "error": f"invalid JSON response: {exc.msg}",
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
