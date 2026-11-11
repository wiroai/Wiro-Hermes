"""Wiro AI as a Hermes model provider: pick Wiro AI in ``hermes model`` or pass ``--provider wiro``.

The Wiro AI LLM gateway serves GPT, Claude, Gemini, DeepSeek, GLM, Kimi and Grok models behind one
OpenAI-compatible API at https://llm.wiro.ai/v1, paid as you go from the Wiro wallet. What this
plugin adds over pointing a custom endpoint there:

* The context window, tool calling and image input of each listed model are declared from the
  gateway's ``GET /v1/models`` catalog, so Hermes does not fall back to a guessed context window.
* Hermes' reasoning level goes out as the top-level ``reasoning_effort`` field the gateway reads.
  When reasoning is off, models that can switch it off get ``none`` and the others get their
  lowest level. With no level set nothing is sent and the model's own default applies.
* Background tasks and image description run on a lower-cost model with image input, so they
  still work when the chat model reads text only.
"""

from __future__ import annotations

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

API_KEY_ENV = "WIRO_API_KEY"
BASE_URL = "https://llm.wiro.ai/v1"

# Lower-cost model with image input for background tasks (titles, compression) and images.
SMALL_MODEL = "openai/gpt-6-luna"
VISION_MODEL = SMALL_MODEL

# Levels sent as given. "none" and Hermes' "ultra" are mapped in reasoning_effort_for().
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")

# id -> (context window in tokens, accepts image input, level sent when reasoning is off).
# Context and image input come from https://llm.wiro.ai/v1/models (2026-11-10); every model listed
# here calls tools and takes reasoning_effort there. The off levels were checked with live requests
# on the same day: "none" where the model can switch reasoning off, else its lowest level.
# Order sets the picker order; the first entry is the setup default.
MODELS: dict[str, tuple[int, bool, str]] = {
    "openai/gpt-5-4-mini": (400_000, True, "none"),
    "claude/sonnet-5": (1_000_000, True, "low"),
    "google/gemini-3-flash": (1_048_576, True, "minimal"),
    "deepseek/v4-flash": (1_048_576, False, "minimal"),
    "glm/5-3": (1_000_000, True, "minimal"),
    "moonshot/kimi-k3-1-pro": (1_048_576, True, "none"),
    "xai/grok-4-5": (500_000, True, "low"),
    "openai/gpt-6-sol": (1_050_000, True, "none"),
    "openai/gpt-5-4": (1_050_000, True, "none"),
    "claude/opus-5-5": (1_000_000, True, "low"),
    "google/gemini-3-pro": (1_048_576, True, "low"),
    "deepseek/v4-pro": (1_048_576, False, "minimal"),
    "openai/gpt-6-luna": (1_050_000, True, "none"),
    "openai/gpt-5-4-nano": (400_000, True, "none"),
}


def reasoning_effort_for(model: Any, reasoning_config: Any) -> str | None:
    """The ``reasoning_effort`` value to send for *model*, or None to leave the field out.

    Reasoning controls are model-specific, so only models in ``MODELS`` get the field. Unset sends
    nothing. Off (or an explicit ``none``) sends the model's off level from ``MODELS``. Hermes'
    ``ultra`` is sent as ``max``. The gateway moves a level a model does not offer to the nearest
    one it does and names the change in the ``x-wiro-adjusted-parameters`` response header.
    """
    if model not in MODELS or not isinstance(reasoning_config, dict):
        return None
    effort = str(reasoning_config.get("effort") or "").strip().lower()
    if reasoning_config.get("enabled") is False or effort == "none":
        return MODELS[model][2]
    if effort == "ultra":
        return "max"
    return effort if effort in EFFORTS else None


class WiroProfile(ProviderProfile):
    """Wiro AI's OpenAI-compatible gateway with a per-model reasoning_effort mapping."""

    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, **context: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        effort = reasoning_effort_for(context.get("model"), reasoning_config)
        return {}, ({"reasoning_effort": effort} if effort else {})

    def default_vision_model(self) -> str | None:
        return VISION_MODEL


wiro = WiroProfile(
    name="wiro",
    aliases=("wiro-ai", "wiroai"),
    display_name="Wiro AI",
    description="Wiro AI LLM gateway: GPT, Claude, Gemini, DeepSeek and more on one key",
    signup_url="https://wiro.ai/panel/project",
    env_vars=(API_KEY_ENV,),
    base_url=BASE_URL,
    auth_type="api_key",
    default_aux_model=SMALL_MODEL,
    fallback_models=tuple(MODELS),
    model_capabilities={
        model_id: {
            "context_window": context_window,
            "supports_tools": True,
            "supports_reasoning": True,
            "supports_vision": vision,
        }
        for model_id, (context_window, vision, _off) in MODELS.items()
    },
)

register_provider(wiro)
