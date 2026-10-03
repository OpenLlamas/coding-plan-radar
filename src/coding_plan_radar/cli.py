"""Command line interface.

coding-plan-radar run          crawl → extract → diff → score → report
coding-plan-radar report       re-render reports from the last stored run
coding-plan-radar list-vendors print the configured catalog
coding-plan-radar check-urls   HEAD every pricing_url and flag drift (404/403/…)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .ai import AIClient, settings_from_env
from .config import ConfigError, RuntimeSettings, load_scoring, load_vendors, resolve_config_dir
from .diff import diff_runs
from .fetch import Fetcher
from .models import Plan
from .pipeline import Pipeline
from .report import write_reports
from .score import build_metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coding-plan-radar",
        description="Crawl AI coding plan pricing, extract tiers, score value, track changes.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    def common(command):
        command.add_argument(
            "--config-dir", default=None, help="directory holding vendors.yaml / scoring.yaml"
        )
        command.add_argument("--data-dir", default="data")
        command.add_argument("--reports-dir", default="reports")
        command.add_argument("--lang", choices=["en", "zh"], default="en")
        command.add_argument("--verbose", action="store_true")
        return command

    run = common(sub.add_parser("run", help="run the full pipeline"))
    run.add_argument("--vendors", default=None, help="comma separated vendor ids to limit the run")
    run.add_argument("--no-ai", action="store_true", help="rule-based extraction only")
    run.add_argument("--render", action="store_true", help="use Playwright for JS-heavy pages")
    run.add_argument("--offline", action="store_true", help="reuse local snapshots, no network")
    run.add_argument("--ignore-robots", action="store_true")
    run.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    run.add_argument("--timeout", type=float, default=25.0)

    common(sub.add_parser("report", help="re-render reports from data/latest.json"))
    common(sub.add_parser("list-vendors", help="show the vendor catalog"))

    check = common(sub.add_parser("check-urls", help="HEAD every pricing_url"))
    check.add_argument("--vendors", default=None)
    check.add_argument("--timeout", type=float, default=20.0)
    check.add_argument("--delay", type=float, default=1.0)
    return parser


def _settings(args) -> RuntimeSettings:
    root = Path.cwd()
    config_dir = resolve_config_dir(args.config_dir, root)
    settings = RuntimeSettings(
        root=root,
        config_dir=config_dir,
        data_dir=Path(args.data_dir),
        reports_dir=Path(args.reports_dir),
        lang=args.lang,
        verbose=args.verbose,
        render=getattr(args, "render", False),
        offline=getattr(args, "offline", False),
        ignore_robots=getattr(args, "ignore_robots", False),
        no_ai=getattr(args, "no_ai", False),
        fetch_delay=getattr(args, "delay", 1.5),
        fetch_timeout=getattr(args, "timeout", 25.0),
    )
    settings.vendors = load_vendors(config_dir / "vendors.yaml")
    settings.scoring = load_scoring(config_dir / "scoring.yaml")
    return settings


def _command_run(args) -> int:
    settings = _settings(args)
    ai_client = None
    if not settings.no_ai:
        ai_settings = settings_from_env()
        if ai_settings:
            ai_client = AIClient(ai_settings)
            logging.info("AI extraction enabled (%s)", ai_settings.model)
        else:
            logging.info("AI not configured; using rule-based extraction (see .env.example)")
    pipeline = Pipeline(settings, ai=ai_client)
    vendor_ids = [v for v in (args.vendors or "").split(",") if v.strip()] or None
    summary = pipeline.run(vendor_ids)

    print(
        f"run {summary.run_id}: {summary.vendors_ok}/{summary.vendors_total} vendors ok, "
        f"{summary.plans} plans, {summary.changes} changes"
    )
    for failure in summary.fetch_failed:
        print(f"  fetch failed: {failure}")
    for warning in summary.warnings:
        print(f"  warning: {warning}")
    for name, path in summary.reports.items():
        print(f"  report {name}: {path}")
    return 0 if summary.plans or summary.vendors_ok else 1


def _command_report(args) -> int:
    settings = _settings(args)
    latest = settings.data_dir / "latest.json"
    if not latest.is_file():
        print(f"no {latest}; run `coding-plan-radar run` first", file=sys.stderr)
        return 1
    payload = json.loads(latest.read_text(encoding="utf-8"))
    plans = [Plan.from_dict(item) for item in payload.get("plans", [])]
    previous_path = settings.data_dir / "previous.json"
    previous = []
    if previous_path.is_file():
        previous = json.loads(previous_path.read_text(encoding="utf-8")).get("plans", [])
    changes = diff_runs(previous, [plan.to_dict() for plan in plans])
    vendor_map = {vendor.id: vendor for vendor in settings.vendors}
    metrics = build_metrics(plans, vendor_map, settings.scoring)
    paths = write_reports(
        settings.reports_dir,
        metrics,
        changes,
        vendor_map,
        "",
        settings.lang,
        {
            "run_id": payload.get("run_id", ""),
            "generated_at": payload.get("generated_at"),
            "vendors_total": len(vendor_map),
            "vendors_ok": len({p.vendor_id for p in plans}),
        },
    )
    print("wrote " + ", ".join(paths.values()))
    return 0


def _command_list_vendors(args) -> int:
    settings = _settings(args)
    for vendor in settings.vendors:
        flag = "" if vendor.enabled else " (disabled)"
        print(
            f"{vendor.id}\t{vendor.region}\t{vendor.category}\t{vendor.name}{flag}\t"
            f"{vendor.pricing_url}"
        )
    return 0


def _command_check_urls(args) -> int:
    settings = _settings(args)
    wanted = {v.strip().lower() for v in (args.vendors or "").split(",") if v.strip()}
    fetcher = Fetcher(
        settings.data_dir,
        timeout=args.timeout,
        delay=args.delay,
        ignore_robots=True,
    )
    problems = 0
    try:
        for vendor in settings.vendors:
            if wanted and vendor.id.lower() not in wanted:
                continue
            status = fetcher.check_url(vendor.pricing_url)
            marker = "ok  " if isinstance(status, int) and 200 <= status < 400 else "WARN"
            if marker == "WARN":
                problems += 1
            print(f"{marker} {vendor.id}: {status}  {vendor.pricing_url}")
    finally:
        fetcher.close()
    print(f"{problems} url(s) need attention")
    return 0


_COMMANDS = {
    "run": _command_run,
    "report": _command_report,
    "list-vendors": _command_list_vendors,
    "check-urls": _command_check_urls,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    logging.basicConfig(
        level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    try:
        return _COMMANDS[args.command](args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
