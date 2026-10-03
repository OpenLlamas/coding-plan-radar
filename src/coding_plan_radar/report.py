"""Render scored plans into Markdown, JSON and CSV, plus a human change log."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from pathlib import Path
from typing import Any

from .config import Vendor
from .models import PlanMetrics

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Coding Plan Radar",
        "generated": "Generated",
        "vendors_ok": "vendors crawled",
        "vendors_total": "configured",
        "plans": "plans",
        "summary": "Value ranking",
        "rank": "#",
        "vendor": "Vendor",
        "plan": "Plan",
        "price_month": "USD/mo",
        "capacity": "Est. capacity / mo",
        "cost_requests": "$/1k req",
        "cost_tokens": "$/1M tok",
        "feature": "Feature",
        "value": "Value",
        "source": "Source",
        "changes": "Detected changes",
        "no_changes": "No changes since the previous run.",
        "ai_insights": "AI insights",
        "methodology": "Methodology",
        "disclaimer": "Disclaimer",
        "global": "Global",
        "cn": "China",
        "plan_added": "new plan",
        "plan_removed": "plan removed",
        "price_changed": "price",
        "billing_changed": "billing period",
        "quota_changed": "quota changed",
        "feature_changed": "features changed",
        "free": "free",
    },
    "zh": {
        "title": "编码套餐雷达",
        "generated": "生成时间",
        "vendors_ok": "成功抓取厂商",
        "vendors_total": "已配置",
        "plans": "套餐",
        "summary": "性价比排行",
        "rank": "#",
        "vendor": "厂商",
        "plan": "套餐",
        "price_month": "美元/月",
        "capacity": "估算月容量",
        "cost_requests": "美元/千请求",
        "cost_tokens": "美元/百万token",
        "feature": "功能分",
        "value": "性价比分",
        "source": "来源",
        "changes": "检测到的变化",
        "no_changes": "与上次运行相比无变化。",
        "ai_insights": "AI 洞察",
        "methodology": "评分方法",
        "disclaimer": "免责声明",
        "global": "国际",
        "cn": "中国",
        "plan_added": "新增套餐",
        "plan_removed": "套餐下架",
        "price_changed": "价格",
        "billing_changed": "计费周期",
        "quota_changed": "额度变化",
        "feature_changed": "功能变化",
        "free": "免费",
    },
}

METHODOLOGY = {
    "en": (
        "`value_score` is a **relative** index within one report (best plan = 100):\n\n"
        "1. `USD/mo` — listed price ÷ FX rate, ÷ 12 when billed annually.\n"
        "2. `capacity` — monthly tokens (weight per `scoring.yaml`) and premium requests, "
        "log-scaled across plans. Quotas published per 5h/day/week use fixed monthly factors "
        "(5h→×144) and are flagged as heavy-use estimates, not guarantees.\n"
        "3. `feature` — weighted checklist (agent, cli, api, ide, model-choice, mcp, team).\n"
        "4. `value` — `0.7·capacity + 0.3·feature`, divided by effective price, normalized to "
        "100. Free tiers use a small floor price so they cannot dominate by dividing by zero.\n\n"
        "Quotas shown as estimates come from an LLM reading the page or from unit conversion; "
        "treat them as directional. The JSON export keeps every raw field for audit."
    ),
    "zh": (
        "`性价比分` 是**单份报告内**的相对指数（最优套餐 = 100）：\n\n"
        "1. `美元/月` — 标价 ÷ 汇率，年付再 ÷ 12。\n"
        "2. `估算月容量` — 按 `scoring.yaml` 权重合并每月 token 与高级请求数，跨套餐取对数归一。"
        "以 5 小时/天/周计的量按固定系数折算为月值（5h→×144）并标注为重度高估，非保证值。\n"
        "3. `功能分` — 加权清单（agent、cli、api、ide、选模型、mcp、团队）。\n"
        "4. `性价比分` — `(0.7·容量 + 0.3·功能) ÷ 有效价格`，归一到 100。免费档用极小底价避免除零"
        "压倒榜单。\n\n"
        "估算类额度来自大模型读页或单位换算，仅供方向性参考。JSON 导出保留全部原始字段以便复核。"
    ),
}

DISCLAIMER = {
    "en": (
        "Prices, quotas and features change constantly and vary by region, tax and promotion. "
        "This is automated analysis from public pages, not purchasing advice — verify on the "
        "vendor's own site before you buy."
    ),
    "zh": (
        "价格、额度与功能经常变动，且随地区、税费、促销而不同。本报告为对公开页面的自动化分析，"
        "不构成购买建议，下单前请以厂商官网为准。"
    ),
}


def _compact(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1e9:
        return f"{value / 1e9:.1f}B"
    if value >= 1e6:
        return f"{value / 1e6:.1f}M"
    if value >= 1e3:
        return f"{value / 1e3:.0f}k"
    return f"{value:.0f}"


def _fmt_money(value: float | None, label: dict[str, str]) -> str:
    if value is None:
        return "—"
    if value == 0:
        return label["free"]
    return f"${value:.2f}"


def _fmt_cost(value: float | None) -> str:
    return "—" if value is None else f"${value:.2f}"


def format_capacity(metric: PlanMetrics) -> str:
    capacity = metric.capacity
    if capacity is None:
        return "—"
    parts = []
    if capacity.tokens_month:
        parts.append(f"{_compact(capacity.tokens_month)} tok")
    if capacity.premium_requests_month:
        parts.append(f"{_compact(capacity.premium_requests_month)} req")
    if not parts and capacity.multiplier:
        parts.append(f"{capacity.multiplier:g}×")
    return " + ".join(parts) if parts else "—"


def _esc(text: str) -> str:
    return str(text).replace("|", "\\|").strip() or "—"


def render_markdown(
    metrics: list[PlanMetrics],
    changes: list[dict],
    vendors: dict[str, Vendor],
    narrative: str,
    lang: str,
    run_meta: dict,
) -> str:
    label = LABELS.get(lang, LABELS["en"])
    generated = run_meta.get("generated_at") or dt.datetime.now(dt.timezone.utc).isoformat(
        timespec="seconds"
    )
    lines = [
        f"# {label['title']}",
        "",
        f"**{label['generated']}:** `{generated}` · {run_meta.get('vendors_ok', '?')}"
        f"/{run_meta.get('vendors_total', '?')} {label['vendors_ok']} · {len(metrics)}"
        f" {label['plans']}",
        "",
    ]

    lines += [
        f"## {label['summary']}",
        "",
        f"| {label['rank']} | {label['vendor']} | {label['plan']} | {label['price_month']} "
        f"| {label['capacity']} | {label['cost_requests']} | {label['cost_tokens']} "
        f"| {label['feature']} | {label['value']} |",
        "|---:|---|---|---:|---|---:|---:|---:|---:|",
    ]
    for metric in metrics:
        money = _fmt_money(metric.effective_monthly_usd, label)
        capacity = _esc(format_capacity(metric))
        lines.append(
            f"| {metric.rank} | {_esc(metric.vendor_name)} | {_esc(metric.plan.plan_name)} "
            f"| {money} | {capacity} "
            f"| {_fmt_cost(metric.cost_per_1k_requests_usd)} "
            f"| {_fmt_cost(metric.cost_per_1m_tokens_usd)} "
            f"| {metric.feature_score:.0f} | {metric.value_score:.0f} |"
        )
    lines.append("")

    lines += [f"## {label['changes']}", ""]
    if changes:
        lines += [f"- {row}" for row in _change_lines(changes, vendors, label)]
    else:
        lines.append(label["no_changes"])
    lines.append("")

    if narrative:
        lines += [f"## {label['ai_insights']}", "", narrative, ""]

    lines += [f"## {label['methodology']}", "", METHODOLOGY.get(lang, METHODOLOGY["en"]), ""]
    lines += [
        f"## {label['disclaimer']}",
        "",
        DISCLAIMER.get(lang, DISCLAIMER["en"]),
        "",
        "_OpenLlamas_",
        "",
    ]
    return "\n".join(lines)


def _change_lines(
    changes: list[dict], vendors: dict[str, Vendor], label: dict[str, str]
) -> list[str]:
    rows = []
    for change in changes:
        vendor = vendors.get(change["vendor_id"])
        name = vendor.name if vendor else change["vendor_id"]
        plan = change.get("plan_name", "")
        etype = change.get("type")
        kind = label.get(etype, etype)
        if etype == "price_changed":
            detail = (
                f"{change.get('old')} → {change.get('new')} {change.get('currency') or ''}".strip()
            )
            if change.get("pct") is not None:
                detail += f" ({change['pct']:+.1f}%)"
        else:
            detail = str(change.get("new") or change.get("old") or "")
        rows.append(f"**{name} · {plan}** — {kind}: {detail}")
    return rows


def build_json(
    metrics: list[PlanMetrics],
    changes: list[dict],
    narrative: str,
    run_meta: dict,
) -> dict:
    return {
        "schema": 1,
        "generated_at": run_meta.get("generated_at"),
        "run": {k: v for k, v in run_meta.items() if k != "generated_at"},
        "plan_count": len(metrics),
        "plans": [metric.to_dict() for metric in metrics],
        "changes": changes,
        "ai_insights": narrative,
    }


def render_csv(metrics: list[PlanMetrics]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "rank",
            "vendor",
            "region",
            "plan",
            "usd_per_month",
            "capacity_basis",
            "tokens_per_month",
            "requests_per_month",
            "usd_per_1k_requests",
            "usd_per_1m_tokens",
            "feature_score",
            "value_score",
            "source_url",
        ]
    )
    for metric in metrics:
        capacity = metric.capacity
        writer.writerow(
            [
                metric.rank,
                metric.vendor_name,
                metric.region,
                metric.plan.plan_name,
                metric.effective_monthly_usd,
                capacity.basis if capacity else "",
                capacity.tokens_month if capacity else "",
                capacity.premium_requests_month if capacity else "",
                metric.cost_per_1k_requests_usd,
                metric.cost_per_1m_tokens_usd,
                metric.feature_score,
                metric.value_score,
                metric.plan.source_url,
            ]
        )
    return buffer.getvalue()


def write_reports(
    reports_dir: Path,
    metrics: list[PlanMetrics],
    changes: list[dict],
    vendors: dict[str, Vendor],
    narrative: str,
    lang: str,
    run_meta: dict[str, Any],
) -> dict[str, str]:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    meta = {**run_meta}
    meta.setdefault("generated_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))

    markdown = render_markdown(metrics, changes, vendors, narrative, lang, meta)
    payload = build_json(metrics, changes, narrative, meta)

    paths = {
        "markdown": reports_dir / "latest.md",
        "json": reports_dir / "latest.json",
        "csv": reports_dir / "latest.csv",
        "changes": reports_dir / "changes.md",
    }
    paths["markdown"].write_text(markdown, encoding="utf-8")
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["csv"].write_text(render_csv(metrics), encoding="utf-8", newline="")

    label = LABELS.get(lang, LABELS["en"])
    change_rows = _change_lines(changes, vendors, label) if changes else [label["no_changes"]]
    paths["changes"].write_text(
        f"# {label['changes']}\n\n`{meta['generated_at']}`\n\n"
        + "\n".join(f"- {row}" for row in change_rows)
        + "\n",
        encoding="utf-8",
    )
    return {name: str(path) for name, path in paths.items()}
