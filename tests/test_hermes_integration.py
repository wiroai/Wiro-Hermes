"""End-to-end checks against a real Hermes, with the plugin installed into a temporary HERMES_HOME.

Each check runs in a fresh interpreter whose ``$HERMES_HOME/plugins/wiro`` holds a copy of the
plugin and whose ``.env`` holds a placeholder key, the state ``hermes plugins install`` leaves.
Nothing here talks to Wiro AI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import providers
from conftest import PLUGIN_DIR

HERMES_ROOT = Path(providers.__file__).resolve().parents[1]
PLACEHOLDER_KEY = "wiro-test-not-a-real-key"


def _run(tmp_path: Path, code: str) -> dict:
    home = tmp_path / "hermes-home"
    shutil.copytree(PLUGIN_DIR, home / "plugins" / "wiro", ignore=shutil.ignore_patterns("__pycache__"))
    (home / ".env").write_text(f"WIRO_API_KEY={PLACEHOLDER_KEY}\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WIRO_", "HERMES_", "OPENAI", "OPENROUTER"))}
    env.update(HERMES_HOME=str(home), PYTHONPATH=str(HERMES_ROOT), NO_COLOR="1")
    bootstrap = "from hermes_cli.env_loader import load_hermes_dotenv; load_hermes_dotenv()\n"
    proc = subprocess.run(
        [sys.executable, "-c", bootstrap + code],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_hermes_discovers_and_resolves_the_installed_plugin(tmp_path):
    found = _run(
        tmp_path,
        """
import json
from providers import get_provider_profile
from hermes_cli.auth import PROVIDER_REGISTRY
from hermes_cli.runtime_provider import resolve_runtime_provider
profile = get_provider_profile("wiro")
row = PROVIDER_REGISTRY["wiro"]
runtime = resolve_runtime_provider(requested="wiro-ai")
print(json.dumps({
    "class": type(profile).__name__,
    "aliases": [get_provider_profile(alias) is profile for alias in ("wiro-ai", "wiroai")],
    "row": [list(row.api_key_env_vars), row.inference_base_url],
    "runtime": [runtime["provider"], runtime["base_url"], runtime["api_mode"],
                runtime["api_key"] == "wiro-test-not-a-real-key"],
}))
""",
    )
    assert found["class"] == "WiroProfile"
    assert found["aliases"] == [True, True]
    assert found["row"] == [["WIRO_API_KEY"], "https://llm.wiro.ai/v1"]
    assert found["runtime"] == ["wiro", "https://llm.wiro.ai/v1", "chat_completions", True]


def test_hermes_reads_declared_capabilities(tmp_path):
    found = _run(
        tmp_path,
        """
import json
from agent.models_dev import get_model_capabilities
out = {}
for model in ("openai/gpt-5-4-mini", "claude/sonnet-5", "deepseek/v4-flash"):
    caps = get_model_capabilities("wiro", model)
    out[model] = [caps.context_window, caps.supports_vision, caps.supports_tools, caps.supports_reasoning]
print(json.dumps(out))
""",
    )
    assert found == {
        "openai/gpt-5-4-mini": [400000, True, True, True],
        "claude/sonnet-5": [1000000, True, True, True],
        "deepseek/v4-flash": [1048576, False, True, True],
    }


def test_chat_request_carries_the_reasoning_effort(tmp_path):
    found = _run(
        tmp_path,
        """
import json
from providers import get_provider_profile
from agent.transports.chat_completions import ChatCompletionsTransport
profile = get_provider_profile("wiro")
transport = ChatCompletionsTransport()
out = {}
for label, model, config in (
    ("gpt off", "openai/gpt-5-4-mini", {"enabled": False}),
    ("claude off", "claude/sonnet-5", {"enabled": False}),
    ("gemini high", "google/gemini-3-flash", {"enabled": True, "effort": "high"}),
    ("gpt ultra", "openai/gpt-5-4-mini", {"enabled": True, "effort": "ultra"}),
    ("unset", "openai/gpt-5-4-mini", None),
    ("unlisted high", "some/unlisted-model", {"enabled": True, "effort": "high"}),
):
    kwargs = transport.build_kwargs(model, [{"role": "user", "content": "hi"}], None, provider_profile=profile,
                                    reasoning_config=config, base_url=profile.base_url, provider_name="wiro")
    out[label] = [kwargs.get("reasoning_effort"), "reasoning" in kwargs.get("extra_body", {})]
print(json.dumps(out))
""",
    )
    assert found == {
        "gpt off": ["none", False],
        "claude off": ["low", False],
        "gemini high": ["high", False],
        "gpt ultra": ["max", False],
        "unset": [None, False],
        "unlisted high": [None, False],
    }
