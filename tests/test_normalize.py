from __future__ import annotations

from coding_plan_radar.models import CapacityEstimate, Plan, Quota
from coding_plan_radar.normalize import (
    cost_metrics,
    effective_monthly_usd,
    estimate_capacity,
)

FX = {"USD": 1.0, "CNY": 7.1, "EUR": 0.92}


def test_effective_monthly_usd_annual_and_fx():
    assert effective_monthly_usd(Plan(price=240, currency="USD", billing_period="year"), FX) == 20.0
    assert (
        effective_monthly_usd(Plan(price=199, currency="CNY", billing_period="month"), FX) == 28.03
    )
    assert effective_monthly_usd(Plan(price=0, currency="USD", billing_period="free"), FX) == 0.0


def test_effective_monthly_usd_missing_data():
    assert effective_monthly_usd(Plan(price=None), FX) is None
    assert effective_monthly_usd(Plan(price=50, currency="XYZ"), FX) is None


def test_estimate_capacity_from_quotas():
    plan = Plan(
        quotas=[
            Quota(kind="tokens", amount=1_000_000, period="month"),
            Quota(kind="requests", amount=500, period="5h"),
            Quota(kind="multiplier", amount=3, period="unknown"),
        ]
    )
    cap = estimate_capacity(plan)
    assert cap.tokens_month == 1_000_000
    assert cap.premium_requests_month == 500 * 144
    assert cap.multiplier == 3
    assert "heavy use" in cap.basis.lower()


def test_estimate_capacity_prefers_ai_estimate():
    plan = Plan(
        capacity=CapacityEstimate(tokens_month=9_999, source="ai_estimate"),
        quotas=[Quota(kind="tokens", amount=1, period="month")],
    )
    assert estimate_capacity(plan).tokens_month == 9_999


def test_estimate_capacity_none_without_signal():
    assert estimate_capacity(Plan(quotas=[Quota(kind="credits", amount=10)])) is None


def test_cost_metrics():
    cap = CapacityEstimate(premium_requests_month=2000, tokens_month=1_000_000)
    per_1k, per_1m = cost_metrics(20.0, cap)
    assert per_1k == 10.0
    assert per_1m == 20.0
