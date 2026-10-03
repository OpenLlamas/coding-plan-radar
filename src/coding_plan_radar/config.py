"""Configuration loading: vendor catalog, scoring knobs, runtime paths."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    pass


@dataclass
class Vendor:
    id: str
    name: str
    region: str = "global"
    category: str = "subscription"
    currency: str = "USD"
    pricing_url: str = ""
    homepage: str = ""
    notes: str = ""
    enabled: bool = True


@dataclass
class RuntimeSettings:
    root: Path
    config_dir: Path
    data_dir: Path
    reports_dir: Path
    lang: str = "en"
    verbose: bool = False
    render: bool = False
    offline: bool = False
    ignore_robots: bool = False
    no_ai: bool = False
    fetch_delay: float = 1.5
    fetch_timeout: float = 25.0
    vendors: list[Vendor] = field(default_factory=list)
    scoring: dict[str, Any] = field(default_factory=dict)


def _bundled_config_dir() -> Path | None:
    """Config shipped inside the installed package (wheel force-include)."""
    try:
        from importlib import resources

        candidate = Path(str(resources.files("coding_plan_radar"))) / "config"
    except Exception:
        return None
    return candidate if (candidate / "vendors.yaml").is_file() else None


def resolve_config_dir(explicit: str | os.PathLike | None, root: Path) -> Path:
    """Explicit flag wins, then env, then ./config, then the packaged copy."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_dir = os.environ.get("CPR_CONFIG_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path(root) / "config")
    bundled = _bundled_config_dir()
    if bundled:
        candidates.append(bundled)
    for candidate in candidates:
        if (candidate / "vendors.yaml").is_file():
            return candidate
    tried = ", ".join(str(c) for c in candidates)
    raise ConfigError(f"vendors.yaml not found. Looked in: {tried}")


def load_vendors(path: Path) -> list[Vendor]:
    payload = _read_yaml(path)
    entries = payload.get("vendors") if isinstance(payload, dict) else payload
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path}: expected a non-empty 'vendors' list")
    vendors: list[Vendor] = []
    seen: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ConfigError(f"{path}: vendors[{index}] must be a mapping")
        vendor_id = str(item.get("id") or "").strip()
        if not vendor_id:
            raise ConfigError(f"{path}: vendors[{index}] is missing 'id'")
        if vendor_id in seen:
            raise ConfigError(f"{path}: duplicate vendor id '{vendor_id}'")
        seen.add(vendor_id)
        vendors.append(
            Vendor(
                id=vendor_id,
                name=str(item.get("name") or vendor_id).strip(),
                region=str(item.get("region") or "global").strip(),
                category=str(item.get("category") or "subscription").strip(),
                currency=(str(item.get("currency") or "USD").strip() or "USD").upper(),
                pricing_url=str(item.get("pricing_url") or "").strip(),
                homepage=str(item.get("homepage") or "").strip(),
                notes=str(item.get("notes") or "").strip(),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return vendors


def load_scoring(path: Path) -> dict[str, Any]:
    if not Path(path).is_file():
        return _default_scoring()
    payload = _read_yaml(path)
    scoring = _default_scoring()
    for key, value in payload.items():
        if isinstance(value, dict) and isinstance(scoring.get(key), dict):
            scoring[key] = {**scoring[key], **value}
        else:
            scoring[key] = value
    fx = scoring.get("fx_to_usd") or {}
    fx.setdefault("USD", 1.0)
    scoring["fx_to_usd"] = fx
    return scoring


def _default_scoring() -> dict[str, Any]:
    return {
        "fx_to_usd": {"USD": 1.0},
        "price": {"free_floor_usd": 0.5, "amortize_one_time_months": 12},
        "features": {
            "weights": {
                "agent": 1.0,
                "cli": 1.0,
                "api_access": 0.8,
                "ide": 0.6,
                "model_choice": 0.6,
                "mcp": 0.5,
                "team": 0.4,
            }
        },
        "capacity": {"weights": {"tokens": 0.6, "requests": 0.4}},
        "value": {"weights": {"capacity": 0.7, "features": 0.3}},
    }


def _read_yaml(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML ({exc})") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path}: expected a YAML mapping at the top level")
    return payload
