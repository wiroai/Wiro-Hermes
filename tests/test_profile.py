"""The profile's model table and reasoning mapping."""

from __future__ import annotations

import json
import os
import urllib.request

import pytest
from conftest import CATALOG, plugin


def _check_against(catalog: list[dict]) -> None:
    rows = {row["id"]: row for row in catalog}
    assert set(plugin.MODELS) <= set(rows)
    for model_id, (context_window, vision, off_level) in plugin.MODELS.items():
        row = rows[model_id]
        caps = row["capabilities"]
        assert context_window == row["context_length"], model_id
        assert vision == ("image" in caps["input_modalities"]), model_id
        assert caps["function_tools"] is True, model_id
        assert "reasoning_effort" in caps["generation_controls"], model_id
        assert off_level in ("none", "minimal", "low"), model_id


def test_models_match_the_recorded_catalog():
    assert set(plugin.MODELS) == {row["id"] for row in CATALOG["data"]}
    _check_against(CATALOG["data"])


@pytest.mark.skipif(
    not (os.environ.get("WIRO_LIVE_CATALOG") and os.environ.get("WIRO_API_KEY")),
    reason="set WIRO_LIVE_CATALOG=1 and WIRO_API_KEY to compare against the live catalog",
)
def test_models_match_the_live_catalog():
    request = urllib.request.Request(
        plugin.BASE_URL + "/models",
        headers={"Authorization": "Bearer " + os.environ["WIRO_API_KEY"], "User-Agent": "hermes-wiro-provider-tests"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        _check_against(json.load(response)["data"])


def test_profile_fields():
    profile = plugin.wiro
    assert profile.name == "wiro"
    assert profile.base_url == "https://llm.wiro.ai/v1"
    assert profile.env_vars == ("WIRO_API_KEY",)
    assert profile.fallback_models[0] == "openai/gpt-5-4-mini"
    assert profile.fallback_models == tuple(plugin.MODELS)
    assert profile.default_aux_model in plugin.MODELS
    assert profile.model_capabilities[plugin.VISION_MODEL]["supports_vision"] is True
    assert profile.default_vision_model() == plugin.VISION_MODEL


@pytest.mark.parametrize(
    ("model", "config", "expected"),
    [
        ("openai/gpt-5-4-mini", None, None),
        ("openai/gpt-5-4-mini", {"enabled": True}, None),
        ("openai/gpt-5-4-mini", {"enabled": False}, "none"),
        ("openai/gpt-5-4-mini", {"enabled": True, "effort": "none"}, "none"),
        ("openai/gpt-5-4-mini", {"enabled": True, "effort": "high"}, "high"),
        ("openai/gpt-5-4-mini", {"enabled": True, "effort": "ultra"}, "max"),
        ("claude/sonnet-5", {"enabled": False}, "low"),
        ("claude/sonnet-5", {"enabled": True, "effort": "none"}, "low"),
        ("claude/sonnet-5", {"enabled": True, "effort": "max"}, "max"),
        ("google/gemini-3-flash", {"enabled": False}, "minimal"),
        ("deepseek/v4-flash", {"enabled": True, "effort": "xhigh"}, "xhigh"),
        ("openai/gpt-5-4-mini", {"enabled": True, "effort": "bogus"}, None),
        ("some/unlisted-model", {"enabled": True, "effort": "high"}, None),
        ("some/unlisted-model", {"enabled": False}, None),
    ],
)
def test_reasoning_effort_for(model, config, expected):
    assert plugin.reasoning_effort_for(model, config) == expected


def test_reasoning_goes_top_level():
    body, top = plugin.wiro.build_api_kwargs_extras(reasoning_config={"enabled": False}, model="claude/sonnet-5")
    assert body == {}
    assert top == {"reasoning_effort": "low"}
    assert plugin.wiro.build_api_kwargs_extras(reasoning_config=None, model="openai/gpt-5-4-mini") == ({}, {})
