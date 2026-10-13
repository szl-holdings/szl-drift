# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SZL Holdings
"""Offline tests for the scheduled availability verdict.

The verdict reads a finished report and never touches the network. These
tests pin the property the scheduled job relies on: any probed origin that did
not answer 200 makes the verdict exit 1, and a healthy report exits 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import probe
import verdict


def _report(honest=200, readyz=200, health=200, errors=None):
    return {
        "schema": "szl.origin-drift/v1",
        "certified_production_ready": False,
        "product_honest": {"status": honest},
        "product_readyz": {"status": readyz},
        "proof_health": {"status": health},
        "errors": errors or {"honest": None, "readyz": None, "health": None},
    }


def test_all_200_passes():
    assert verdict.reasons(_report()) == []


@pytest.mark.parametrize("field", ["honest", "readyz", "health"])
@pytest.mark.parametrize("status", [503, 404, 301, None])
def test_any_unavailable_origin_fails(field, status):
    failed = verdict.reasons(_report(**{field: status}))
    assert len(failed) == 1
    assert repr(status) in failed[0]


def test_outage_reports_every_origin_and_its_error():
    # The shape recorded by the 2026-10-04..06 a11oy outage reports.
    outage = "HTTP Error 503: Service Unavailable"
    report = _report(honest=503, readyz=503, errors={"honest": outage, "readyz": outage, "health": None})
    failed = verdict.reasons(report)
    assert [line.split(":")[0] for line in failed] == ["product_honest", "product_readyz"]
    assert all("503: Service Unavailable" in line for line in failed)


@pytest.mark.parametrize("bad", [None, [], "x", {}, {"schema": "other/v1"}])
def test_missing_or_foreign_report_fails(bad):
    assert verdict.reasons(bad) == ["report is missing or not szl.origin-drift/v1"]


def test_missing_section_fails():
    report = _report()
    del report["proof_health"]
    assert verdict.reasons(report) == ["proof_health: status None"]


def test_cli_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_report()), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_report(readyz=503)), encoding="utf-8")
    assert verdict.main(["verdict.py", str(good)]) == 0
    assert verdict.main(["verdict.py", str(bad)]) == 1
    assert "ORIGIN UNAVAILABLE" in capsys.readouterr().out
    assert verdict.main(["verdict.py", str(tmp_path / "absent.json")]) == 1
    assert verdict.main(["verdict.py"]) == 2


def test_verdict_reads_real_probe_output(monkeypatch, capsys, tmp_path):
    # Drive probe.main with get() stubbed, then feed its exact stdout to the verdict.
    def fake_get(url):
        if url.endswith("/readyz"):
            return {"ok": False, "status": 503, "body": None, "error": "HTTP Error 503: Service Unavailable"}
        return {"ok": True, "status": 200, "body": {}, "error": None}

    monkeypatch.setattr(probe, "get", fake_get)
    assert probe.main() == 0
    path = tmp_path / "drift-report.json"
    path.write_text(capsys.readouterr().out, encoding="utf-8")
    assert verdict.main(["verdict.py", str(path)]) == 1
    assert "product_readyz: status 503 (HTTP Error 503: Service Unavailable)" in capsys.readouterr().out
