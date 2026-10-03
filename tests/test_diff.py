from __future__ import annotations

from coding_plan_radar.diff import diff_runs


def _plan(vendor, name, price, currency="USD", quotas=None, features=None, period="month"):
    return {
        "vendor_id": vendor,
        "plan_name": name,
        "price": price,
        "currency": currency,
        "billing_period": period,
        "quotas": quotas or [],
        "features": features or [],
    }


def test_detects_price_change_with_percent():
    prev = [_plan("acme", "Pro", 20, quotas=[{"kind": "tokens", "amount": 1, "period": "month"}])]
    cur = [_plan("acme", "Pro", 25, quotas=[{"kind": "tokens", "amount": 1, "period": "month"}])]
    events = diff_runs(prev, cur)
    price = [e for e in events if e["type"] == "price_changed"]
    assert len(price) == 1
    assert price[0]["old"] == 20 and price[0]["new"] == 25
    assert price[0]["pct"] == 25.0


def test_added_removed_and_unchanged():
    prev = [_plan("acme", "Pro", 20), _plan("acme", "Old", 5)]
    cur = [_plan("acme", "Pro", 20), _plan("acme", "New", 30)]
    types = {e["type"] for e in diff_runs(prev, cur)}
    assert types == {"plan_added", "plan_removed"}


def test_quota_and_feature_changes_detected():
    prev = [
        _plan(
            "acme",
            "Pro",
            20,
            quotas=[{"kind": "tokens", "amount": 100, "period": "month"}],
            features=["cli"],
        )
    ]
    cur = [
        _plan(
            "acme",
            "Pro",
            20,
            quotas=[{"kind": "tokens", "amount": 50, "period": "month"}],
            features=["cli", "mcp"],
        )
    ]
    types = {e["type"] for e in diff_runs(prev, cur)}
    assert {"quota_changed", "feature_changed"} <= types
    feature = next(e for e in diff_runs(prev, cur) if e["type"] == "feature_changed")
    assert feature["added"] == ["mcp"]


def test_no_change_yields_no_events():
    plan = _plan("acme", "Pro", 20, quotas=[{"kind": "tokens", "amount": 1, "period": "month"}])
    assert diff_runs([plan], [dict(plan)]) == []


def test_case_insensitive_matching_does_not_fake_rename():
    prev = [_plan("acme", "Pro", 20)]
    cur = [_plan("acme", "pro", 20)]
    assert diff_runs(prev, cur) == []
