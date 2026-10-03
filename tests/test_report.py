from __future__ import annotations

import json

from coding_plan_radar.config import Vendor
from coding_plan_radar.models import CapacityEstimate, Plan, PlanMetrics
from coding_plan_radar.report import format_capacity, render_csv, render_markdown, write_reports


def _metric(rank, name, price, tokens, value):
    plan = Plan(
        vendor_id="acme", plan_name=name, price=price, currency="USD", billing_period="month"
    )
    return PlanMetrics(
        plan=plan,
        vendor_name="Acme",
        region="global",
        capacity=CapacityEstimate(tokens_month=tokens, source="heuristic"),
        effective_monthly_usd=price,
        cost_per_1m_tokens_usd=round(price / (tokens / 1e6), 2),
        capacity_score=value,
        feature_score=50,
        value_score=value,
        rank=rank,
    )


VENDORS = {"acme": Vendor(id="acme", name="Acme", region="global")}


def test_format_capacity_shows_tokens():
    text = format_capacity(_metric(1, "Pro", 20, 1_000_000, 100))
    assert "1.0M tok" in text


def test_markdown_english_and_chinese_headers():
    metrics = [_metric(1, "Pro", 20, 1_000_000, 100), _metric(2, "Free", 0, 500_000, 60)]
    en = render_markdown(
        metrics, [], VENDORS, "", "en", {"generated_at": "X", "vendors_ok": 1, "vendors_total": 1}
    )
    assert "Value ranking" in en and "Acme" in en and "Pro" in en
    zh = render_markdown(
        metrics, [], VENDORS, "", "zh", {"generated_at": "X", "vendors_ok": 1, "vendors_total": 1}
    )
    assert "性价比排行" in zh
    assert "_OpenLlamas_" in zh


def test_markdown_change_line():
    change = {
        "type": "price_changed",
        "vendor_id": "acme",
        "plan_name": "Pro",
        "old": 20,
        "new": 25,
        "pct": 25.0,
        "currency": "USD",
    }
    md = render_markdown([_metric(1, "Pro", 25, 1_000_000, 100)], [change], VENDORS, "", "en", {})
    assert "price: 20 → 25 USD (+25.0%)" in md


def test_csv_rows():
    csv_text = render_csv([_metric(1, "Pro", 20, 1_000_000, 100)])
    assert "usd_per_month" in csv_text
    assert csv_text.count("\n") >= 2


def test_write_reports_outputs(tmp_path):
    metrics = [_metric(1, "Pro", 20, 1_000_000, 100)]
    paths = write_reports(tmp_path, metrics, [], VENDORS, "insight line", "en", {})
    assert set(paths) == {"markdown", "json", "csv", "changes"}
    payload = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert payload["plan_count"] == 1
    assert payload["ai_insights"] == "insight line"
    assert "No changes" in (tmp_path / "changes.md").read_text(encoding="utf-8")
