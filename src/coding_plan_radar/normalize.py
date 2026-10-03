"""Turn listed prices and quota statements into comparable numbers.

Two things happen here: prices are reduced to one currency and one period, and
each plan's usage wording is reduced to a monthly capacity estimate. Both are
pure functions so the ranking is reproducible and unit-testable without network.
"""

from __future__ import annotations

from .models import CapacityEstimate, Plan

# Convert a published quota period into an approximate per-month factor. The 5h
# figure is aggressive on purpose: it is the theoretical window ceiling for a
# full month, which is how vendors sell "unlimited-ish" tiers. Heavy assumptions
# are surfaced in the estimate's `basis` string, never hidden.
_PERIOD_MONTH_FACTOR = {
    "month": 1.0,
    "year": 1.0 / 12.0,
    "week": 4.348,
    "day": 30.44,
    "5h": 144.0,
    "total": 1.0,
    "unknown": 1.0,
    "free": 1.0,
}


def effective_monthly_usd(plan: Plan, fx: dict, amortize_months: int = 12) -> float | None:
    """Monthly price in USD, or None when price or currency rate is unavailable."""
    if plan.price is None:
        return None
    currency = (plan.currency or "USD").upper()
    try:
        rate = float(fx.get(currency) or 0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0:
        return None
    usd = plan.price / rate
    period = (plan.billing_period or "month").lower()
    if period == "year":
        usd /= 12.0
    elif period == "one_time":
        usd /= float(max(1, amortize_months))
    return round(usd, 2)


def estimate_capacity(plan: Plan) -> CapacityEstimate | None:
    """Monthly capacity in common units, from an AI estimate or the raw quotas."""
    if plan.capacity and (
        plan.capacity.premium_requests_month
        or plan.capacity.tokens_month
        or plan.capacity.multiplier
    ):
        return plan.capacity

    requests = 0.0
    tokens = 0.0
    multiplier: float | None = None
    basis: list[str] = []
    for quota in plan.quotas:
        if quota.amount is None or quota.amount <= 0:
            continue
        period = (quota.period or "month").lower()
        factor = _PERIOD_MONTH_FACTOR.get(period, 1.0)
        if quota.kind == "tokens":
            tokens += quota.amount * factor
            basis.append(f"{quota.amount:g} tokens/{period}")
        elif quota.kind == "requests":
            requests += quota.amount * factor
            if period == "5h":
                basis.append(f"{quota.amount:g} req/5h × 144 windows/mo (assumes heavy use)")
            else:
                basis.append(f"{quota.amount:g} req/{period}")
        elif quota.kind == "multiplier" and multiplier is None:
            multiplier = quota.amount
            basis.append(f"{quota.amount:g}× usage")
        elif quota.kind == "credits":
            basis.append(f"{quota.amount:g} credits (not converted)")

    if not requests and not tokens and multiplier is None:
        return None
    return CapacityEstimate(
        premium_requests_month=requests or None,
        tokens_month=tokens or None,
        multiplier=multiplier,
        basis="; ".join(basis),
        source="heuristic",
        confidence=0.5,
    )


def cost_metrics(
    monthly_usd: float | None, capacity: CapacityEstimate | None
) -> tuple[float | None, float | None]:
    """Return ``(usd per 1k requests, usd per 1M tokens)`` where computable."""
    if monthly_usd is None or capacity is None:
        return None, None
    per_1k_requests = None
    per_1m_tokens = None
    if capacity.premium_requests_month:
        per_1k_requests = round(monthly_usd / (capacity.premium_requests_month / 1000.0), 2)
    if capacity.tokens_month:
        per_1m_tokens = round(monthly_usd / (capacity.tokens_month / 1_000_000.0), 2)
    return per_1k_requests, per_1m_tokens
