"""Shared setup: a Hermes source checkout on ``sys.path`` and both plugins imported against it.

CI passes ``PYTHONPATH`` pointing at a Hermes checkout. Locally, ``HERMES_SOURCE`` or a sibling
``hermes-agent`` directory works. Without Hermes the whole suite is skipped.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "wiro"
MEDIA_DIR = ROOT / "wiro-media"
CATALOG = json.loads((Path(__file__).resolve().parent / "wiro_models.json").read_text(encoding="utf-8"))

_source = os.environ.get("HERMES_SOURCE") or str(ROOT.parent / "hermes-agent")
if (Path(_source) / "providers" / "__init__.py").is_file() and _source not in sys.path:
    sys.path.insert(0, _source)

if importlib.util.find_spec("providers") is None:
    pytest.skip("Hermes is not importable (set HERMES_SOURCE or PYTHONPATH)", allow_module_level=True)


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "wiro_provider_under_test", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_media():
    """The media plugin as a package, the way Hermes imports it (relative imports need the package)."""
    name = "wiro_media_under_test"
    spec = importlib.util.spec_from_file_location(
        name, MEDIA_DIR / "__init__.py", submodule_search_locations=[str(MEDIA_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plugin = _load_plugin()
media_plugin = _load_media()
