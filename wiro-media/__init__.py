"""Wiro AI media tools for Hermes Agent: image, video, speech, music, transcription and 3D models.

Registers the ``wiro`` toolset (thirteen ``wiro_*`` tools: seven media tools, model search and
details, running any model, task follow-up, and workflows), the ``wiro-media:wiro`` skill, and
Wiro AI as an opt-in backend for Hermes' own ``image_generate``, ``video_generate``,
``text_to_speech`` and speech-to-text. Runs go to https://api.wiro.ai/v1 with WIRO_API_KEY and
are paid from the Wiro AI wallet. The model provider for chat is the companion ``wiro`` plugin.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import schemas, tools
from .tools import Runtime

logger = logging.getLogger(__name__)

TOOLSET = "wiro"

HANDLERS = {
    "wiro_generate_image": tools.generate_image,
    "wiro_edit_image": tools.edit_image,
    "wiro_generate_video": tools.generate_video,
    "wiro_text_to_speech": tools.text_to_speech,
    "wiro_generate_music": tools.generate_music,
    "wiro_transcribe": tools.transcribe,
    "wiro_generate_3d": tools.generate_3d,
    "wiro_search_models": tools.search_models,
    "wiro_model_info": tools.model_info,
    "wiro_run_model": tools.run_model,
    "wiro_task": tools.task_tool,
    "wiro_workflows": tools.list_workflows,
    "wiro_run_workflow": tools.run_workflow,
}

EMOJI = {
    "wiro_generate_image": "🖼️", "wiro_edit_image": "🪄", "wiro_generate_video": "🎬",
    "wiro_text_to_speech": "🔊", "wiro_generate_music": "🎵", "wiro_transcribe": "📝",
    "wiro_generate_3d": "🧊", "wiro_search_models": "🔎", "wiro_model_info": "📋",
    "wiro_run_model": "▶️", "wiro_task": "⏳", "wiro_workflows": "🧩", "wiro_run_workflow": "🧩",
}


def _bind(runtime: Runtime, name: str):
    fn = HANDLERS[name]
    return tools.guarded(name, lambda args: fn(runtime, args))


def register(ctx: Any) -> None:
    runtime = Runtime(ctx)
    for schema in schemas.ALL:
        name = schema["name"]
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=_bind(runtime, name),
            check_fn=runtime.available,
            requires_env=["WIRO_API_KEY"],
            description=schema["description"],
            emoji=EMOJI.get(name, ""),
        )
    skills = Path(__file__).resolve().parent / "skills"
    for folder in sorted(skills.iterdir()) if skills.is_dir() else []:
        if folder.is_dir() and (folder / "SKILL.md").is_file():
            ctx.register_skill(folder.name, folder / "SKILL.md")

    from . import backends

    backends.register(ctx, runtime)
