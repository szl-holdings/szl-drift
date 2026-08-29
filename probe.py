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
            except json.JSONDecodeError:
                body = None
            return {"ok": True, "status": res.status, "body": body, "error": None}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "body": None, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status": None, "body": None, "error": str(exc)}


def main() -> int:
    honest = get(f"{PRODUCT}/api/a11oy/v1/honest")
    readyz = get(f"{PRODUCT}/readyz")
    health = get(f"{PROOF}/health.json")
    body = honest.get("body") or {}
    doctrine = body.get("doctrine_lock") or {}
    report = {
        "schema": "szl.origin-drift/v1",
        "certified_production_ready": False,
        "product_honest": {
            "status": honest["status"],
            "locked_formula_count": body.get("locked_formula_count") or doctrine.get("locked_formula_count"),
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
            "signer": (health.get("body") or {}).get("signer") if isinstance(health.get("body"), dict) else None,
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
