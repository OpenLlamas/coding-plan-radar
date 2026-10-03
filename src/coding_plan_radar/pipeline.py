"""End-to-end orchestration: crawl → extract → diff → score → report.

State lives under ``data/``:
  snapshots/<vendor>/<ts>.html   every fetch, for offline replay and audit
  extracted/<vendor>.json        last structured extraction per vendor
  latest.json / previous.json    the two most recent whole runs, for diffing
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .ai import AIClient
from .config import RuntimeSettings, Vendor
from .diff import diff_runs
from .extract import extract_rules_plans, html_to_text
from .fetch import Fetcher
from .models import Plan
from .report import write_reports
from .score import build_metrics

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    run_id: str = ""
    vendors_total: int = 0
    vendors_ok: int = 0
    fetch_failed: list[str] = field(default_factory=list)
    plans: int = 0
    changes: int = 0
    reports: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, settings: RuntimeSettings, ai: AIClient | None = None) -> None:
        self.settings = settings
        self.ai = None if settings.no_ai else ai
        self.data_dir = Path(settings.data_dir)

    def select_vendors(self, vendor_ids: list[str] | None) -> list[Vendor]:
        vendors = [vendor for vendor in self.settings.vendors if vendor.enabled]
        if not vendor_ids:
            return vendors
        wanted = [v.strip().lower() for v in vendor_ids if v.strip()]
        lookup = {vendor.id.lower(): vendor for vendor in vendors}
        missing = [v for v in wanted if v not in lookup]
        if missing:
            logger.warning("unknown or disabled vendors skipped: %s", ", ".join(missing))
        return [lookup[v] for v in wanted if v in lookup]

    def run(self, vendor_ids: list[str] | None = None) -> RunSummary:
        now = dt.datetime.now(dt.timezone.utc)
        summary = RunSummary(run_id=now.strftime("%Y%m%dT%H%M%SZ"))
        vendors = self.select_vendors(vendor_ids)
        summary.vendors_total = len(vendors)
        if not vendors:
            summary.warnings.append("no vendors selected")

        fetcher = Fetcher(
            self.data_dir,
            timeout=self.settings.fetch_timeout,
            delay=self.settings.fetch_delay,
            render=self.settings.render,
            offline=self.settings.offline,
            ignore_robots=self.settings.ignore_robots,
        )
        plans: list[Plan] = []
        try:
            for vendor in vendors:
                result = fetcher.fetch(vendor)
                if not result.ok:
                    summary.fetch_failed.append(f"{vendor.id}: {result.error}")
                    logger.warning("fetch failed: %s (%s)", vendor.id, result.error)
                    continue
                summary.vendors_ok += 1
                text = html_to_text(result.html)
                vendor_plans = self._extract(vendor, result.url, text, summary)
                self._write_extracted(vendor, result, vendor_plans)
                plans.extend(vendor_plans)
                logger.info(
                    "%s: %d plans (%s)",
                    vendor.id,
                    len(vendor_plans),
                    vendor_plans[0].extracted_by if vendor_plans else "none",
                )
        finally:
            fetcher.close()

        summary.plans = len(plans)
        previous = self._load_previous()
        self._write_latest(summary.run_id, now.isoformat(timespec="seconds"), plans)
        changes = diff_runs(previous, [plan.to_dict() for plan in plans])
        summary.changes = len(changes)

        metrics = build_metrics(plans, self._vendor_map(), self.settings.scoring)
        narrative = ""
        if self.ai is not None and metrics:
            try:
                narrative = self.ai.narrate(self._summary_table(metrics), self.settings.lang)
            except Exception as exc:  # narration is a bonus, never fatal
                summary.warnings.append(f"AI narrative failed: {exc}")

        run_meta = {
            "run_id": summary.run_id,
            "generated_at": now.isoformat(timespec="seconds"),
            "vendors_total": summary.vendors_total,
            "vendors_ok": summary.vendors_ok,
            "fetch_failed": summary.fetch_failed,
            "ai_enabled": bool(self.ai),
            "offline": self.settings.offline,
        }
        summary.reports = write_reports(
            self.settings.reports_dir,
            metrics,
            changes,
            self._vendor_map(),
            narrative,
            self.settings.lang,
            run_meta,
        )
        return summary

    # ----------------------------------------------------------------- extraction

    def _extract(self, vendor: Vendor, url: str, text: str, summary: RunSummary) -> list[Plan]:
        rules_plans = extract_rules_plans(vendor, text, url)
        if self.ai is None:
            return rules_plans
        try:
            ai_plans = self.ai.extract_plans(vendor, url, text)
        except Exception as exc:
            summary.warnings.append(f"{vendor.id}: AI extraction failed ({exc}); rules used")
            return rules_plans
        if ai_plans:
            return ai_plans
        summary.warnings.append(f"{vendor.id}: AI returned no plans; rules used")
        return rules_plans

    def _vendor_map(self) -> dict[str, Vendor]:
        return {vendor.id: vendor for vendor in self.settings.vendors}

    # ------------------------------------------------------------- state helpers

    def _write_extracted(self, vendor: Vendor, result, plans: list[Plan]) -> None:
        directory = self.data_dir / "extracted"
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "vendor_id": vendor.id,
            "url": result.url,
            "fetched_at": result.fetched_at,
            "extracted_by": plans[0].extracted_by if plans else "none",
            "plans": [plan.to_dict() for plan in plans],
        }
        path = directory / f"{vendor.id}.json"
        _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))

    def _load_previous(self) -> list[dict]:
        latest = self.data_dir / "latest.json"
        if not latest.is_file():
            return []
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        plans = payload.get("plans", []) if isinstance(payload, dict) else []
        _atomic_write(
            self.data_dir / "previous.json",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        return [p for p in plans if isinstance(p, dict)]

    def _write_latest(self, run_id: str, generated_at: str, plans: list[Plan]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "generated_at": generated_at,
            "plans": [plan.to_dict() for plan in plans],
        }
        _atomic_write(
            self.data_dir / "latest.json",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    def _summary_table(self, metrics) -> str:
        rows = [
            "| vendor | plan | usd/mo | tokens/mo | req/mo | feature | value |",
            "|---|---|---|---|---|---|---|",
        ]
        for metric in metrics[:24]:
            capacity = metric.capacity
            rows.append(
                f"| {metric.vendor_name} | {metric.plan.plan_name} "
                f"| {metric.effective_monthly_usd} "
                f"| {getattr(capacity, 'tokens_month', None)} "
                f"| {getattr(capacity, 'premium_requests_month', None)} "
                f"| {metric.feature_score} | {metric.value_score} |"
            )
        return "\n".join(rows)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)
