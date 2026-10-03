"""HTML → text and rule-based plan extraction.

The rule path is deliberately conservative: it only produces a plan when it can
see a tier-like heading next to a price. Anything fancier is the AI extractor's
job (``ai.py``). This keeps the no-API-key mode honest instead of guessing.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .config import Vendor
from .models import Plan, Quota, coerce_float

_CURRENCY_CODES = {
    "US$": "USD",
    "$": "USD",
    "USD": "USD",
    "¥": "CNY",
    "￥": "CNY",
    "RMB": "CNY",
    "CNY": "CNY",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "美元": "USD",
    "元": "CNY",
    "人民币": "CNY",
}

# Anchors are numeric prices only. A tier literally named "Free" is found as the
# *name* of the "$0" line below it; making the bare word an anchor would both
# create a nameless section and block that backwards name-scan. Pure-"Free"
# pages with no "$0" are handled by the AI extractor instead.
_PRICE_RE = re.compile(
    r"(?P<sym>US\$|\$|USD|¥|￥|RMB|CNY|€|EUR|£|GBP)\s*(?P<amt>\d[\d,]*(?:\.\d+)?)"
    r"|(?P<amt2>\d[\d,]*(?:\.\d+)?)\s*(?P<cur2>美元|人民币|元|USD|EUR|GBP|CNY)",
    re.IGNORECASE,
)

_TIER_WORDS = re.compile(
    r"\b(free|hobby|student|pro|plus|max|ultra|team|business|enterprise|starter|basic|standard"
    r"|premium|individual|personal|lite|turbo|community|developer|cloud|solo|solo)\b"
    r"|免费|基础|专业|标准|高级|团队|企业|旗舰|个人|开发",
    re.IGNORECASE,
)

_PERIOD_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("5h", (r"5[- ]?\s*hour", r"every\s+5\s*hours?", r"5\s*小时")),
    ("day", (r"/\s*day", r"per\s+day", r"每天", r"每日", r"/\s*日")),
    ("week", (r"/\s*week", r"per\s+week", r"每周", r"/\s*周")),
    ("month", (r"/\s*mo\b", r"/\s*month", r"per\s+month", r"monthly", r"每月", r"/\s*月")),
    (
        "year",
        (
            r"/\s*yr\b",
            r"/\s*year",
            r"per\s+year",
            r"annually",
            r"annual",
            r"每年",
            r"/\s*年",
            r"年付",
        ),
    ),
]

_MAGNITUDES = {
    "k": 1e3,
    "m": 1e6,
    "b": 1e9,
    "千": 1e3,
    "万": 1e4,
    "亿": 1e8,
    "million": 1e6,
    "billion": 1e9,
}

_TOKEN_RE = re.compile(
    r"(?P<amt>\d[\d,]*(?:\.\d+)?)\s*(?P<mag>k|m|b|千|万|亿|million|billion)?\s*"
    r"(?P<unit>tokens?|令牌|词元)",
    re.IGNORECASE,
)

_REQUEST_RE = re.compile(
    r"(?P<amt>\d[\d,]*(?:\.\d+)?)\s*(?P<mag>k|m|千|万)?\s*(?:premium\s+)?"
    r"(?P<unit>requests?|prompts?|messages?|asks?|次|请求|提示|问答)",
    re.IGNORECASE,
)

_CREDIT_RE = re.compile(
    r"(?P<amt>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>credits?|积分|点数)",
    re.IGNORECASE,
)

_MULTIPLIER_RE = re.compile(
    r"(?P<amt>\d+(?:\.\d+)?)\s*[x×]\s*(?P<unit>usage|limits?|requests?|额度|用量)?",
    re.IGNORECASE,
)

_FEATURE_PATTERNS: dict[str, tuple[str, ...]] = {
    "agent": (r"\bagent", r"智能体", r"autonomous"),
    "cli": (r"\bcli\b", r"command[- ]line", r"命令行", r"终端"),
    "ide": (r"\bides?\b", r"vs ?code", r"visual studio", r"jetbrains", r"编辑器"),
    "mcp": (r"\bmcp\b",),
    "model_choice": (
        r"model choice",
        r"choose .{0,24}model",
        r"select .{0,24}model",
        r"切换模型",
        r"模型选择",
    ),
    "api_access": (r"\bapi\b",),
    "team": (r"\bteam\b", r"\bseats?\b", r"团队", r"席位"),
}

_MODEL_NAMES = (
    "GPT",
    "Claude",
    "Gemini",
    "GLM",
    "Kimi",
    "Qwen",
    "DeepSeek",
    "Llama",
    "Mistral",
    "Grok",
    "Sonnet",
    "Opus",
    "Haiku",
    "o3",
    "o4",
)

_MAX_BLOCK_LINES = 30
_MAX_NAME_SCAN = 12


# --------------------------------------------------------------------- text prep


def html_to_text(html: str, max_chars: int = 200_000) -> str:
    """Strip markup to readable lines: one text node per line, deduped, capped."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    raw = soup.get_text("\n")
    lines: list[str] = []
    for chunk in raw.splitlines():
        line = re.sub(r"\s+", " ", chunk).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    text = "\n".join(lines)
    return text[:max_chars]


# ------------------------------------------------------------------ field parsing


def detect_period(text: str, default: str = "month") -> str:
    lowered = text.lower()
    for period, patterns in _PERIOD_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, lowered):
                return period
    return default


def parse_price(text: str) -> tuple[float, str, str] | None:
    """Return ``(amount, currency, billing_period)`` for a numeric price line."""
    match = _PRICE_RE.search(text)
    if match is None:
        return None
    amount = coerce_float(match.group("amt") or match.group("amt2"))
    if amount is None:
        return None
    raw_currency = match.group("sym") or match.group("cur2") or "USD"
    currency = _CURRENCY_CODES.get(raw_currency) or _CURRENCY_CODES.get(raw_currency.upper(), "USD")
    period = detect_period(text)
    if amount == 0:
        period = "free"
    return amount, currency, period


def _looks_like_plan_name(line: str) -> bool:
    if not line or len(line) > 60:
        return False
    if _PRICE_RE.search(line):
        return False
    return bool(_TIER_WORDS.search(line))


def _scaled_amount(raw: str | None, magnitude: str | None) -> float | None:
    value = coerce_float(raw)
    if value is None:
        return None
    if magnitude:
        value *= _MAGNITUDES.get(magnitude.lower(), 1.0)
    return value


def find_quotas(text: str) -> list[Quota]:
    """Pull machine-readable usage statements out of a plan block."""
    quotas: list[Quota] = []
    seen: set[tuple[str, float, str]] = set()

    def add(kind: str, amount: float | None, unit: str, line: str, period: str | None) -> None:
        if amount is None or amount <= 0:
            return
        resolved = period or detect_period(line)
        key = (kind, amount, resolved)
        if key in seen:
            return
        seen.add(key)
        quotas.append(
            Quota(kind=kind, amount=amount, unit=unit, period=resolved, text=line.strip()[:400])
        )

    for line in text.splitlines():
        for match in _TOKEN_RE.finditer(line):
            add(
                "tokens",
                _scaled_amount(match.group("amt"), match.group("mag")),
                "tokens",
                line,
                None,
            )
        for match in _REQUEST_RE.finditer(line):
            premium = "premium" in match.group(0).lower()
            add(
                "requests",
                _scaled_amount(match.group("amt"), match.group("mag")),
                "premium requests" if premium else "requests",
                line,
                None,
            )
        for match in _CREDIT_RE.finditer(line):
            add("credits", _scaled_amount(match.group("amt"), None), "credits", line, None)
        for match in _MULTIPLIER_RE.finditer(line):
            amount = coerce_float(match.group("amt"))
            if amount and 1 < amount <= 100:
                unit = (match.group("unit") or "usage").lower()
                add("multiplier", amount, unit, line, "unknown")
    return quotas


def find_features(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    for feature, patterns in _FEATURE_PATTERNS.items():
        if any(re.search(pattern, lowered) for pattern in patterns):
            found.append(feature)
    return sorted(found)


def find_models(text: str) -> list[str]:
    found = []
    for name in _MODEL_NAMES:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text, re.IGNORECASE):
            found.append(name)
    return found


# ------------------------------------------------------------------ plan sections


def split_plan_sections(text: str) -> list[dict]:
    """Locate ``(plan heading, price line, body block)`` triples in page text."""
    lines = text.splitlines()
    price_positions: list[int] = []
    for index, line in enumerate(lines):
        if len(line) > 140:
            continue
        if parse_price(line) is not None:
            price_positions.append(index)

    sections: list[dict] = []
    seen: set[tuple[str, float, str]] = set()
    for position, index in enumerate(price_positions):
        price = parse_price(lines[index])
        if price is None:
            continue
        previous_price = price_positions[position - 1] + 1 if position else 0
        lower_bound = max(previous_price, index - _MAX_NAME_SCAN, 0)
        name = ""
        for back in range(index - 1, lower_bound - 1, -1):
            if _looks_like_plan_name(lines[back]):
                name = lines[back]
                break
        if not name:
            continue
        next_price = (
            price_positions[position + 1] if position + 1 < len(price_positions) else len(lines)
        )
        end = min(next_price, index + _MAX_BLOCK_LINES)
        key = (name.strip().lower(), price[0], price[1])
        if key in seen:
            continue
        seen.add(key)
        sections.append(
            {
                "name": name,
                "price_line": lines[index],
                "price": price,
                "block": "\n".join(lines[index:end]),
            }
        )
    return sections


def extract_rules_plans(vendor: Vendor, text: str, url: str = "") -> list[Plan]:
    """Rule-based extraction pass. Returns 0..n plans, never raises."""
    source = url or vendor.pricing_url
    plans: list[Plan] = []
    for section in split_plan_sections(text):
        amount, currency, period = section["price"]
        block: str = section["block"]
        plans.append(
            Plan(
                vendor_id=vendor.id,
                plan_name=section["name"].strip(),
                price=amount,
                currency=currency or vendor.currency or "USD",
                billing_period=period,
                quotas=find_quotas(block),
                features=find_features(block),
                models=find_models(block),
                source_url=source,
                extracted_by="rules",
                confidence=0.35,
                notes=f"price line: {section['price_line'].strip()[:120]}",
            )
        )
    return plans
