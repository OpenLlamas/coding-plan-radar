"""Value scoring: rank plans by how much they appear to give per dollar.

The score is a *relative* index inside one report — the best plan gets 100 and
everything else is scaled against it. It is a heuristic for comparison, not an
absolute measure of quality. See the README methodology section.
"""

from __future__ import annotations

import math

from .config import Vendor
from .models import Plan, PlanMetrics
from .normalize import cost_metrics, effective_monthly_usd, estimate_capacity


def _log_normalize(values: list[float | None]) -> list[float | None]:
    """Log-scale normalize positive values to 0..100; None stays None."""
    known = [v for v in values if v and v > 0]
    if not known:
        return [None for _ in values]
    logs = [math.log10(v + 1.0) for v in values if v and v > 0]
    low, high = min(logs), max(logs)
    span = high - low
    result: list[float | None] = []
    iterator = iter(logs)
    for value in values:
        if not value or value <= 0:
            result.append(None)
            continue
        log_value = next(iterator)
        result.append(100.0 if span < 1e-9 else 100.0 * (log_value - low) / span)
    return result


def _composite(capacity_score: float, feature_score: float, weights: dict) -> float:
    return (
        weights.get("capacity", 1.0) * capacity_score + weights.get("features", 0.0) * feature_score
    )


def build_metrics(
    plans: list[Plan], vendors: dict[str, Vendor], scoring: dict
) -> list[PlanMetrics]:
    """Score every plan and return the list sorted best-value-first."""
    if not plans:
        return []

    fx = scoring.get("fx_to_usd") or {"USD": 1.0}
    price_cfg = scoring.get("price") or {}
    feature_weights = (scoring.get("features") or {}).get("weights") or {}
    capacity_weights = (scoring.get("capacity") or {}).get("weights") or {"tokens": 1.0}
    value_weights = (scoring.get("value") or {}).get("weights") or {"capacity": 1.0}
    floor = float(price_cfg.get("free_floor_usd") or 0.5)
    amortize = int(price_cfg.get("amortize_one_time_months") or 12)

    capacities = [estimate_capacity(plan) for plan in plans]
    token_scores = _log_normalize([c.tokens_month if c else None for c in capacities])
    request_scores = _log_normalize([c.premium_requests_month if c else None for c in capacities])
    feature_denominator = sum(feature_weights.values()) or 1.0

    metrics: list[PlanMetrics] = []
    for plan, capacity, token_score, request_score in zip(
        plans, capacities, token_scores, request_scores, strict=True
    ):
        monthly = effective_monthly_usd(plan, fx, amortize)
        per_1k_requests, per_1m_tokens = cost_metrics(monthly, capacity)

        weighted = 0.0
        weight_sum = 0.0
        if token_score is not None:
            weighted += capacity_weights.get("tokens", 0) * token_score
            weight_sum += capacity_weights.get("tokens", 0)
        if request_score is not None:
            weighted += capacity_weights.get("requests", 0) * request_score
            weight_sum += capacity_weights.get("requests", 0)
        capacity_score = weighted / weight_sum if weight_sum else 0.0

        feature_score = (
            100.0
            * sum(w for name, w in feature_weights.items() if name in plan.features)
            / feature_denominator
        )

        notes: list[str] = []
        if monthly is None:
            notes.append("price or FX rate unavailable")
        if capacity is None:
            notes.append("no comparable capacity disclosed")
        if plan.price == 0:
            notes.append("free tier")

        vendor = vendors.get(plan.vendor_id)
        metrics.append(
            PlanMetrics(
                plan=plan,
                vendor_name=vendor.name if vendor else plan.vendor_id,
                region=vendor.region if vendor else "",
                capacity=capacity,
                effective_monthly_usd=monthly,
                cost_per_1k_requests_usd=per_1k_requests,
                cost_per_1m_tokens_usd=per_1m_tokens,
                capacity_score=round(capacity_score, 1),
                feature_score=round(feature_score, 1),
                notes=notes,
            )
        )

    raw_values: list[float] = []
    for metric in metrics:
        composite = _composite(metric.capacity_score, metric.feature_score, value_weights)
        price = metric.effective_monthly_usd
        if price is None or composite <= 0:
            raw_values.append(0.0)
        else:
            raw_values.append(composite / max(price, floor))
    top = max(raw_values) if raw_values else 0.0
    for metric, raw in zip(metrics, raw_values, strict=True):
        metric.value_score = round(100.0 * raw / top, 1) if top > 0 else 0.0

    metrics.sort(
        key=lambda m: (
            -m.value_score,
            m.effective_monthly_usd if m.effective_monthly_usd is not None else math.inf,
            m.vendor_name,
            m.plan.plan_name,
        )
    )
    for rank, metric in enumerate(metrics, start=1):
        metric.rank = rank
    return metrics
