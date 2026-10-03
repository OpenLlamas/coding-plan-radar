"""Typed structures shared across the whole pipeline.

Every structure here is JSON round-trippable: scraped state is persisted between
runs so change detection can compare one week against the next.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_AMOUNT_NOISE_RE = re.compile(r"(US\$|\$|¥|￥|€|£|美元|人民币|元)")


def coerce_float(value: Any) -> float | None:
    """Best-effort float for values coming from regex groups or model output."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = _AMOUNT_NOISE_RE.sub("", value.replace(",", ""))
        match = _NUM_RE.search(cleaned)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value)


def coerce_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [coerce_str(item) for item in value if coerce_str(item)]


@dataclass
class Quota:
    """One published usage statement, kept verbatim in ``text`` for audit."""

    kind: str = "other"  # tokens | requests | credits | multiplier | other
    amount: float | None = None
    unit: str = ""
    period: str = "month"  # month | year | week | day | 5h | total | unknown
    model_tier: str = ""
    text: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Quota:
        return cls(
            kind=coerce_str(data.get("kind")) or "other",
            amount=coerce_float(data.get("amount")),
            unit=coerce_str(data.get("unit")),
            period=coerce_str(data.get("period")) or "month",
            model_tier=coerce_str(data.get("model_tier")),
            text=coerce_str(data.get("text"))[:400],
        )


@dataclass
class CapacityEstimate:
    """Monthly capacity expressed in comparable units."""

    premium_requests_month: float | None = None
    tokens_month: float | None = None
    multiplier: float | None = None
    basis: str = ""
    source: str = "heuristic"  # published | ai_estimate | heuristic
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, data: Any) -> CapacityEstimate | None:
        if not isinstance(data, dict):
            return None
        estimate = cls(
            premium_requests_month=coerce_float(data.get("premium_requests_month")),
            tokens_month=coerce_float(data.get("tokens_month")),
            multiplier=coerce_float(data.get("multiplier")),
            basis=coerce_str(data.get("basis")),
            source=coerce_str(data.get("source")) or "heuristic",
            confidence=coerce_float(data.get("confidence")) or 0.0,
        )
        if not (estimate.premium_requests_month or estimate.tokens_month or estimate.multiplier):
            return None
        return estimate


@dataclass
class Plan:
    """A single purchasable tier as published by one vendor."""

    vendor_id: str = ""
    plan_name: str = ""
    price: float | None = None
    currency: str = "USD"
    billing_period: str = "month"  # month | year | one_time | free | unknown
    quotas: list[Quota] = field(default_factory=list)
    capacity: CapacityEstimate | None = None
    features: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    seat_type: str = ""
    notes: str = ""
    source_url: str = ""
    extracted_by: str = "rules"  # rules | ai
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        raw_quotas = data.get("quotas")
        quotas: list[Quota] = []
        if isinstance(raw_quotas, list):
            for item in raw_quotas:
                if isinstance(item, dict):
                    quotas.append(Quota.from_dict(item))
        return cls(
            vendor_id=coerce_str(data.get("vendor_id")),
            plan_name=coerce_str(data.get("plan_name")),
            price=coerce_float(data.get("price")),
            currency=(coerce_str(data.get("currency")) or "USD").upper(),
            billing_period=coerce_str(data.get("billing_period")) or "month",
            quotas=quotas,
            capacity=CapacityEstimate.from_dict(data.get("capacity")),
            features=coerce_list(data.get("features")),
            models=coerce_list(data.get("models")),
            seat_type=coerce_str(data.get("seat_type")),
            notes=coerce_str(data.get("notes")),
            source_url=coerce_str(data.get("source_url")),
            extracted_by=coerce_str(data.get("extracted_by")) or "rules",
            confidence=coerce_float(data.get("confidence")) or 0.0,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanMetrics:
    """A plan plus everything the scoring pass derived from it."""

    plan: Plan
    vendor_name: str = ""
    region: str = ""
    capacity: CapacityEstimate | None = None
    effective_monthly_usd: float | None = None
    cost_per_1k_requests_usd: float | None = None
    cost_per_1m_tokens_usd: float | None = None
    capacity_score: float = 0.0
    feature_score: float = 0.0
    value_score: float = 0.0
    rank: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "rank": self.rank,
            "vendor_id": self.plan.vendor_id,
            "vendor_name": self.vendor_name,
            "region": self.region,
            "plan_name": self.plan.plan_name,
            "price": self.plan.price,
            "currency": self.plan.currency,
            "billing_period": self.plan.billing_period,
            "effective_monthly_usd": self.effective_monthly_usd,
            "capacity": asdict(self.capacity) if self.capacity else None,
            "cost_per_1k_requests_usd": self.cost_per_1k_requests_usd,
            "cost_per_1m_tokens_usd": self.cost_per_1m_tokens_usd,
            "capacity_score": self.capacity_score,
            "feature_score": self.feature_score,
            "value_score": self.value_score,
            "features": list(self.plan.features),
            "models": list(self.plan.models),
            "quotas": [asdict(quota) for quota in self.plan.quotas],
            "source_url": self.plan.source_url,
            "extracted_by": self.plan.extracted_by,
            "confidence": self.plan.confidence,
            "notes": list(self.notes),
        }
        if self.plan.notes:
            data["plan_notes"] = self.plan.notes
        return data
