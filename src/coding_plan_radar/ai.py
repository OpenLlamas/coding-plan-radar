"""AI-assisted semantic extraction via any OpenAI-compatible endpoint.

This is the part that can read a messy pricing page ("500 premium requests every
5 hours, unlimited Tab completion…") and turn it into structured quotas. It is
optional: with no API key configured the pipeline falls back to rule extraction.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import httpx

from .config import Vendor
from .models import CapacityEstimate, Plan, coerce_float, coerce_str

_ALLOWED_FEATURES = "cli, ide, agent, mcp, model_choice, team, api_access"


@dataclass
class AISettings:
    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0


class AIError(RuntimeError):
    pass


def settings_from_env() -> AISettings | None:
    """All three of base URL / key / model are required to enable AI mode."""
    base_url = os.environ.get("CPR_AI_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("CPR_AI_API_KEY", "").strip()
    model = os.environ.get("CPR_AI_MODEL", "").strip()
    if not (base_url and api_key and model):
        return None
    try:
        timeout = float(os.environ.get("CPR_AI_TIMEOUT", "120"))
    except ValueError:
        timeout = 120.0
    return AISettings(base_url=base_url, api_key=api_key, model=model, timeout=timeout)


def parse_json_loose(text: str):
    """Salvage JSON from model output that wraps it in prose or code fences."""
    candidate = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise AIError(f"model did not return parseable JSON: {candidate[:200]!r}")


_SYSTEM_PROMPT = f"""You extract software subscription pricing from a vendor's public page.

Return STRICT JSON only: {{"plans": [ ... ]}} with one object per purchasable tier
shown on the page, including free tiers. Each plan object:
  plan_name        short display name exactly as shown
  price            number as displayed, or null when only "usage based" is shown
  currency         ISO code (USD, CNY, EUR, GBP, ...)
  billing_period   month | year | one_time | free | unknown
  quotas           list of usage statements:
                   {{ kind: tokens|requests|credits|other, amount: number|null,
                      unit: str, period: month|year|week|day|5h|total|unknown,
                      model_tier: str, text: the source wording }}
  capacity_estimate  {{ premium_requests_month: number|null, tokens_month: number|null,
                       multiplier: number|null, basis: str, confidence: 0..1 }}
  features         only these keys: {_ALLOWED_FEATURES}
  models           model names mentioned for this tier
  seat_type        individual | team | enterprise | ""
  notes            anything a buyer must know (overage price, promo window, tax...)
  confidence       0..1, how sure you are this tier is complete

Hard rules:
- Never invent numbers. Copy what the page states; when a page shows annual
  billing next to a monthly price, emit ONE plan with the monthly price.
- Convert nothing except obvious unit suffixes (K/M/B, 万/亿). Keep source
  wording in each quota's `text`.
- capacity_estimate is your explicit best-effort monthly capacity in common
  units, with `basis` quoting which quota lines produced it. For usage that is
  throttled (e.g. "500 requests per 5 hours"), estimate a realistic
  heavy-use month and say so in `basis`. Set fields you cannot estimate to null.
- Feature mapping: CLI/终端→cli, IDE/VS Code/JetBrains→ide, agent/智能体→agent,
  MCP→mcp, 切换模型/model choice→model_choice, 团队/席位/team/seat→team, API→api_access.
- If the page contains no plans at all, return {{"plans": []}}."""


class AIClient:
    def __init__(self, settings: AISettings) -> None:
        self.settings = settings

    # ----------------------------------------------------------------- transport

    def _post(self, payload: dict) -> dict:
        url = f"{self.settings.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.settings.timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code == 400 and "response_format" in payload:
                # some compatible gateways reject structured-output requests
                payload = {k: v for k, v in payload.items() if k != "response_format"}
                response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    def chat_json(self, system: str, user: str):
        payload = {
            "model": self.settings.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        data = self._post(payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError(f"unexpected chat completion response: {str(data)[:200]}") from exc
        return parse_json_loose(content)

    # ------------------------------------------------------------------ analysis

    def extract_plans(self, vendor: Vendor, url: str, text: str) -> list[Plan]:
        if len(text.strip()) < 40:
            return []
        user = (
            f"Vendor: {vendor.name} (id={vendor.id}, default currency {vendor.currency})\n"
            f"Page URL: {url}\n\nPricing page text:\n----\n{text[:24000]}\n----"
        )
        data = self.chat_json(_SYSTEM_PROMPT, user)
        raw_plans = data.get("plans") if isinstance(data, dict) else data
        if not isinstance(raw_plans, list):
            raise AIError("AI response has no plans list")
        plans: list[Plan] = []
        for item in raw_plans:
            if not isinstance(item, dict):
                continue
            plan = Plan.from_dict(item)
            plan.vendor_id = vendor.id
            plan.source_url = url
            plan.extracted_by = "ai"
            if not plan.currency:
                plan.currency = vendor.currency or "USD"
            plan.capacity = _capacity_from_item(item, plan.capacity)
            plans.append(plan)
        return plans

    def narrate(self, summary: str, lang: str = "en") -> str:
        language = "Simplified Chinese" if lang == "zh" else "English"
        system = (
            "You are a pragmatic analyst. From the table of AI coding subscription plans, "
            f"write 3 to 5 short bullets of actionable insights in {language}. Use ONLY numbers "
            "present in the table, flag estimates as estimates, and prefer concrete advice "
            f'(who should buy what, where the traps are). Return JSON: {{"insights": [str]}}'
        )
        data = self.chat_json(system, summary[:12000])
        items = data.get("insights") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return ""
        lines = [f"- {coerce_str(item)}" for item in items if coerce_str(item)]
        return "\n".join(lines)


def _capacity_from_item(item: dict, existing: CapacityEstimate | None) -> CapacityEstimate | None:
    raw = item.get("capacity_estimate")
    if not isinstance(raw, dict):
        return existing
    estimate = CapacityEstimate.from_dict(raw)
    if estimate is None:
        return existing
    estimate.source = "ai_estimate"
    if not estimate.confidence:
        estimate.confidence = coerce_float(item.get("confidence")) or 0.5
    if not estimate.basis:
        estimate.basis = "model estimate"
    return estimate
