from __future__ import annotations

import pytest

from coding_plan_radar.config import Vendor
from coding_plan_radar.models import Plan, Quota
from coding_plan_radar.score import build_metrics

SCORING = {
    "fx_to_usd": {"USD": 1.0},
    "price": {"free_floor_usd": 0.5, "amortize_one_time_months": 12},
    "features": {
        "weights": {
            "agent": 1.0,
            "cli": 1.0,
            "api_access": 0.8,
            "ide": 0.6,
            "model_choice": 0.6,
            "mcp": 0.5,
            "team": 0.4,
        }
    },
    "capacity": {"weights": {"tokens": 0.6, "requests": 0.4}},
    "value": {"weights": {"capacity": 0.7, "features": 0.3}},
}


def _vendor(vid: str) -> Vendor:
    return Vendor(id=vid, name=vid.upper(), region="global", currency="USD")


def _token_plan(vid: str, name: str, price: float, tokens: float, features=()) -> Plan:
    return Plan(
        vendor_id=vid,
        plan_name=name,
        price=price,
        currency="USD",
        billing_period="month",
        quotas=[Quota(kind="tokens", amount=tokens, period="month")],
        features=list(features),
    )


def _vendors(*ids):
    return {i: _vendor(i) for i in ids}


def test_best_value_ranks_first_and_normalizes_to_100():
    plans = [
        _token_plan("a", "A", 10, 1_000_000),  # smallest capacity → floors to 0
        _token_plan("b", "B", 20, 100_000_000),  # big capacity, mid price → best value
        _token_plan("c", "C", 100, 100_000_000),  # same capacity, 5x price → same-ish
    ]
    metrics = build_metrics(plans, _vendors("a", "b", "c"), SCORING)
    assert [m.plan.vendor_id for m in metrics] == ["b", "c", "a"]
    assert metrics[0].value_score == 100.0
    assert metrics[1].value_score == pytest.approx(20.0)
    assert metrics[-1].value_score == 0.0
    assert [m.rank for m in metrics] == [1, 2, 3]


def test_more_features_wins_capacity_tie():
    plans = [
        _token_plan("a", "Rich", 10, 1_000_000, features=["cli", "agent", "mcp"]),
        _token_plan("b", "Plain", 10, 1_000_000, features=[]),
    ]
    metrics = build_metrics(plans, _vendors("a", "b"), SCORING)
    assert metrics[0].plan.plan_name == "Rich"
    assert metrics[0].feature_score > metrics[1].feature_score


def test_missing_price_is_penalised_to_zero_value():
    plans = [
        _token_plan("a", "Priced", 10, 1_000_000),
        Plan(
            vendor_id="b",
            plan_name="Usage-based",
            price=None,
            quotas=[Quota(kind="tokens", amount=1_000_000, period="month")],
        ),
    ]
    metrics = build_metrics(plans, _vendors("a", "b"), SCORING)
    unpriced = next(m for m in metrics if m.plan.plan_name == "Usage-based")
    assert unpriced.value_score == 0.0
    assert unpriced.effective_monthly_usd is None
    assert any("price or FX" in n for n in unpriced.notes)


def test_empty_input_returns_empty():
    assert build_metrics([], {}, SCORING) == []
