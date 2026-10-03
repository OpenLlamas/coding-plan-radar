from __future__ import annotations

from coding_plan_radar.extract import (
    detect_period,
    extract_rules_plans,
    html_to_text,
    parse_price,
)


def _quota(plans, name, kind):
    plan = next(p for p in plans if p.plan_name == name)
    return next((q for q in plan.quotas if q.kind == kind), None)


def test_html_to_text_drops_scripts_and_style(page_a):
    text = html_to_text(page_a)
    assert "$999" not in text  # inside <script>
    assert "var hidden" not in text
    assert "color: red" not in text  # inside <style>
    assert "500 premium requests" in text


def test_parse_price_variants():
    assert parse_price("$20 per month") == (20.0, "USD", "month")
    assert parse_price("$0/mo") == (0.0, "USD", "free")
    assert parse_price("¥199 /月") == (199.0, "CNY", "month")
    assert parse_price("年付 ¥19990 /年") == (19990.0, "CNY", "year")
    assert parse_price("30 USD per month")[0] == 30.0
    assert parse_price("no numbers here") is None


def test_detect_period_windows():
    assert detect_period("500 requests every 5 hours") == "5h"
    assert detect_period("10 credits / week") == "week"
    assert detect_period("plain quota with no period") == "month"


def test_extract_rules_vendor_a(vendor_a, page_a):
    plans = extract_rules_plans(vendor_a, html_to_text(page_a))
    names = [p.plan_name for p in plans]
    assert names == ["Free", "Pro", "Max"]

    free = next(p for p in plans if p.plan_name == "Free")
    assert free.price == 0.0 and free.billing_period == "free"

    pro = next(p for p in plans if p.plan_name == "Pro")
    assert pro.price == 20.0 and pro.currency == "USD"
    assert _quota(plans, "Pro", "requests").amount == 500.0
    assert _quota(plans, "Pro", "tokens").amount == 1_000_000.0
    assert {"cli", "ide", "mcp"} <= set(pro.features)

    mx = next(p for p in plans if p.plan_name == "Max")
    assert _quota(plans, "Max", "tokens").amount == 10_000_000.0
    assert _quota(plans, "Max", "multiplier").amount == 3.0
    assert {"model_choice", "team", "api_access"} <= set(mx.features)


def test_extract_rules_vendor_b(vendor_b, page_b):
    plans = extract_rules_plans(vendor_b, html_to_text(page_b))
    names = [p.plan_name for p in plans]
    assert "基础版" in names and "专业版" in names and "企业版" in names

    pro = next(p for p in plans if p.plan_name == "专业版")
    assert pro.price == 199.0 and pro.currency == "CNY"
    assert _quota(plans, "专业版", "tokens").amount == 10_000_000.0
    assert {"cli", "team"} <= set(pro.features)

    ent = next(p for p in plans if p.plan_name == "企业版")
    assert ent.billing_period == "year"
    assert _quota(plans, "企业版", "tokens").period == "year"


def test_extract_is_empty_on_bare_free_page(vendor_a):
    # A page that only says "Free" with no "$0" yields nothing to the conservative
    # rules engine (that is the AI extractor's job); assert it degrades gracefully.
    plans = extract_rules_plans(vendor_a, "Free\nSign up today")
    assert plans == []
