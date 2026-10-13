#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SZL Holdings
"""Availability verdict for a drift report. Reads a report; never probes.

probe.py always exits 0 so a degraded report is still published. This verdict
is the separate, scheduled-only alert: it exits 1 when any probed origin did
not answer HTTP 200, so an outage turns the scheduled run red instead of being
archived as a green artifact. It checks reachability only. It does not judge
doctrine values, and a pass is not a production certificate.
"""

from __future__ import annotations

import json
import sys
from typing import Any

ORIGINS = ("product_honest", "product_readyz", "proof_health")
ERROR_KEYS = {"product_honest": "honest", "product_readyz": "readyz", "proof_health": "health"}


def reasons(report: Any) -> list[str]:
    """Return one line per unavailable origin; an empty list means all answered 200."""
    if not isinstance(report, dict) or report.get("schema") != "szl.origin-drift/v1":
        return ["report is missing or not szl.origin-drift/v1"]
    errors = report.get("errors") if isinstance(report.get("errors"), dict) else {}
    out = []
    for name in ORIGINS:
        section = report.get(name)
        status = section.get("status") if isinstance(section, dict) else None
        if status != 200:
            error = errors.get(ERROR_KEYS[name])
            out.append(f"{name}: status {status!r}" + (f" ({error})" if error else ""))
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: verdict.py DRIFT_REPORT.json", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError) as exc:
        report = None
        print(f"cannot read report: {exc}", file=sys.stderr)
    failed = reasons(report)
    if failed:
        print("ORIGIN UNAVAILABLE")
        for line in failed:
            print(f"- {line}")
        return 1
    print("All probed origins answered HTTP 200. Reachability only; not a production certificate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
