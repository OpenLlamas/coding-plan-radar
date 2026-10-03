from __future__ import annotations

from pathlib import Path

import pytest

from coding_plan_radar.config import (
    ConfigError,
    load_scoring,
    load_vendors,
    resolve_config_dir,
)

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"


def test_resolve_config_dir_finds_repo_config():
    assert resolve_config_dir(None, REPO) == CONFIG


def test_shipped_vendors_are_valid():
    vendors = load_vendors(CONFIG / "vendors.yaml")
    assert len(vendors) >= 10
    ids = [v.id for v in vendors]
    assert len(ids) == len(set(ids)), "vendor ids must be unique"
    for vendor in vendors:
        assert vendor.pricing_url.startswith("http"), vendor.id
        assert vendor.name, vendor.id


def test_shipped_scoring_is_sane():
    scoring = load_scoring(CONFIG / "scoring.yaml")
    assert scoring["fx_to_usd"]["USD"] == 1.0
    weights = scoring["features"]["weights"]
    assert weights and all(w > 0 for w in weights.values())
    assert scoring["price"]["free_floor_usd"] > 0


def test_duplicate_id_rejected(tmp_path):
    bad = tmp_path / "vendors.yaml"
    bad.write_text(
        "vendors:\n  - {id: a, name: A, pricing_url: 'http://a'}\n"
        "  - {id: a, name: A2, pricing_url: 'http://a2'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_vendors(bad)


def test_missing_config_raises():
    with pytest.raises(ConfigError):
        load_vendors(REPO / "does-not-exist.yaml")


def test_resolve_explicit_dir_wins(tmp_path):
    (tmp_path / "vendors.yaml").write_text("vendors:\n  - {id: a, name: A}\n", encoding="utf-8")
    assert resolve_config_dir(tmp_path, REPO) == tmp_path
