"""Compare two extraction runs and emit change events.

This is what turns a one-off scraper into a monitor: every run diffs against the
last persisted state, so price cuts, new tiers or quietly tightened quotas
surface on their own. Both inputs are plain plan dicts (pre- or post- extraction).
"""

from __future__ import annotations

from typing import Any


def _plan_key(plan: dict) -> tuple[str, str]:
    return (plan.get("vendor_id", ""), str(plan.get("plan_name", "")).strip().lower())


def _index(plans: list[dict]) -> dict[tuple[str, str], dict]:
    return {_plan_key(p): p for p in plans if isinstance(p, dict)}


def _quota_signature(plan: dict) -> tuple:
    lines = []
    for quota in plan.get("quotas") or []:
        if isinstance(quota, dict):
            lines.append((quota.get("kind"), quota.get("amount"), quota.get("period")))
    return tuple(sorted(repr(line) for line in lines))


def _features(plan: dict) -> set[str]:
    return {str(f) for f in (plan.get("features") or [])}


def _event(
    key: tuple[str, str],
    plan: dict,
    etype: str,
    *,
    field: str = "",
    old: Any = None,
    new: Any = None,
    pct: float | None = None,
    **extra: Any,
) -> dict:
    event: dict = {
        "type": etype,
        "vendor_id": key[0],
        "plan_name": plan.get("plan_name", key[1]),
        "field": field,
        "old": old,
        "new": new,
    }
    if pct is not None:
        event["pct"] = pct
    event.update(extra)
    return event


def diff_runs(previous: list[dict], current: list[dict]) -> list[dict]:
    """Return change events going from ``previous`` to ``current`` run."""
    old_index = _index(previous)
    new_index = _index(current)
    events: list[dict] = []

    for key, plan in new_index.items():
        old = old_index.get(key)
        if old is None:
            events.append(_event(key, plan, "plan_added", new=plan.get("price")))
            continue

        old_price, new_price = old.get("price"), plan.get("price")
        if old_price != new_price or (old.get("currency") or "") != (plan.get("currency") or ""):
            pct = None
            if (
                isinstance(old_price, (int, float))
                and old_price
                and isinstance(new_price, (int, float))
            ):
                pct = round((new_price - old_price) / old_price * 100.0, 1)
            events.append(
                _event(
                    key,
                    plan,
                    "price_changed",
                    field="price",
                    old=old_price,
                    new=new_price,
                    pct=pct,
                    currency=plan.get("currency") or old.get("currency"),
                )
            )

        if (old.get("billing_period") or "") != (plan.get("billing_period") or ""):
            events.append(
                _event(
                    key,
                    plan,
                    "billing_changed",
                    field="billing_period",
                    old=old.get("billing_period"),
                    new=plan.get("billing_period"),
                )
            )

        if _quota_signature(old) != _quota_signature(plan):
            events.append(
                _event(
                    key,
                    plan,
                    "quota_changed",
                    field="quotas",
                    old=list(_quota_signature(old)),
                    new=list(_quota_signature(plan)),
                )
            )

        old_features, new_features = _features(old), _features(plan)
        if old_features != new_features:
            events.append(
                _event(
                    key,
                    plan,
                    "feature_changed",
                    field="features",
                    added=sorted(new_features - old_features),
                    removed=sorted(old_features - new_features),
                )
            )

    for key, plan in old_index.items():
        if key not in new_index:
            events.append(_event(key, plan, "plan_removed", old=plan.get("price")))

    events.sort(key=lambda e: (e["vendor_id"], str(e["plan_name"]).lower(), e["type"]))
    return events
