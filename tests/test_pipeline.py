from __future__ import annotations

import json
from pathlib import Path

from coding_plan_radar.config import RuntimeSettings, Vendor, load_scoring
from coding_plan_radar.pipeline import Pipeline

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def _settings(tmp_path: Path, vendors: list[Vendor]) -> RuntimeSettings:
    return RuntimeSettings(
        root=tmp_path,
        config_dir=REPO / "config",
        data_dir=tmp_path / "data",
        reports_dir=tmp_path / "reports",
        lang="en",
        offline=True,  # reuse seeded snapshot; no network
        no_ai=True,  # rule-based only, deterministic
        vendors=vendors,
        scoring=load_scoring(REPO / "config" / "scoring.yaml"),
    )


def _seed_snapshot(tmp_path: Path, vendor_id: str, fixture: str) -> None:
    snap_dir = tmp_path / "data" / "snapshots" / vendor_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "20260101T000000Z.html").write_text(
        (FIXTURES / fixture).read_text(encoding="utf-8"), encoding="utf-8"
    )


def test_offline_run_produces_reports_and_first_run_diff(tmp_path):
    vendors = [
        Vendor(
            id="vendor_a",
            name="Vendor A",
            region="global",
            currency="USD",
            pricing_url="https://acme.test/pricing",
        )
    ]
    _seed_snapshot(tmp_path, "vendor_a", "vendor_a.html")

    summary = Pipeline(_settings(tmp_path, vendors)).run(["vendor_a"])

    assert summary.vendors_ok == 1
    assert summary.plans == 3
    assert summary.changes == 3  # first run: everything is a new plan
    md = (tmp_path / "reports" / "latest.md").read_text(encoding="utf-8")
    assert "Vendor A" in md and "Pro" in md
    payload = json.loads((tmp_path / "reports" / "latest.json").read_text(encoding="utf-8"))
    assert payload["plan_count"] == 3


def test_second_offline_run_detects_no_change(tmp_path):
    vendors = [
        Vendor(
            id="vendor_a",
            name="Vendor A",
            region="global",
            currency="USD",
            pricing_url="https://acme.test/pricing",
        )
    ]
    _seed_snapshot(tmp_path, "vendor_a", "vendor_a.html")
    settings = _settings(tmp_path, vendors)
    Pipeline(settings).run(["vendor_a"])
    second = Pipeline(settings).run(["vendor_a"])
    assert second.changes == 0


def test_unknown_vendor_is_skipped(tmp_path):
    vendors = [
        Vendor(
            id="vendor_a",
            name="Vendor A",
            region="global",
            currency="USD",
            pricing_url="https://acme.test/pricing",
        )
    ]
    settings = _settings(tmp_path, vendors)
    summary = Pipeline(settings).run(["does-not-exist"])
    assert summary.vendors_total == 0
    assert summary.plans == 0
