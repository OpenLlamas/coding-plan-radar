from __future__ import annotations

import json

import pytest

from coding_plan_radar.ai import AIClient, AIError, AISettings, parse_json_loose, settings_from_env
from coding_plan_radar.config import Vendor


def test_parse_json_loose_plain_and_fenced():
    assert parse_json_loose('{"plans": []}') == {"plans": []}
    assert parse_json_loose('```json\n{"plans": [1, 2]}\n```') == {"plans": [1, 2]}


def test_parse_json_loose_salvages_surrounding_prose():
    payload = 'Sure! Here you go:\n{"plans": [{"plan_name": "Pro"}]}\nHope that helps.'
    assert parse_json_loose(payload) == {"plans": [{"plan_name": "Pro"}]}


def test_parse_json_loose_raises_on_garbage():
    with pytest.raises(AIError):
        parse_json_loose("I cannot help with that.")


def test_settings_from_env_requires_all(monkeypatch):
    for key in ("CPR_AI_BASE_URL", "CPR_AI_API_KEY", "CPR_AI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert settings_from_env() is None
    monkeypatch.setenv("CPR_AI_BASE_URL", "https://x/v1")
    monkeypatch.setenv("CPR_AI_API_KEY", "k")
    monkeypatch.setenv("CPR_AI_MODEL", "m")
    settings = settings_from_env()
    assert settings.base_url == "https://x/v1"
    assert settings.model == "m"


def test_extract_plans_maps_ai_estimate(monkeypatch):
    client = AIClient(AISettings(base_url="https://x/v1", api_key="k", model="m"))
    canned = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "plans": [
                                {
                                    "plan_name": "Pro",
                                    "price": 20,
                                    "currency": "USD",
                                    "billing_period": "month",
                                    "features": ["cli", "agent"],
                                    "capacity_estimate": {
                                        "tokens_month": 5000000,
                                        "premium_requests_month": 400,
                                        "basis": "from prose",
                                    },
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }
    monkeypatch.setattr(client, "_post", lambda payload: canned)
    plans = client.extract_plans(
        Vendor(id="acme", name="Acme"), "https://acme.test/p", "text " * 20
    )
    assert len(plans) == 1
    plan = plans[0]
    assert plan.vendor_id == "acme"
    assert plan.extracted_by == "ai"
    assert plan.capacity.source == "ai_estimate"
    assert plan.capacity.tokens_month == 5_000_000
    assert "cli" in plan.features
