"""Wiro AI as the backend of Hermes' own media tools.

Each is opt-in through Hermes' config, and none is active until it is chosen:

* ``image_gen.provider: wiro`` serves ``image_generate`` (model: ``image_gen.model``);
* ``video_gen.provider: wiro`` serves ``video_generate`` (model: ``video_gen.model``);
* ``tts.provider: wiro`` serves ``text_to_speech`` (``tts.model``, ``tts.voice``);
* ``stt.provider: wiro`` transcribes voice messages and recordings (``stt.wiro.model``).

``hermes tools`` lists Wiro AI in each picker. Any Wiro AI model ID works in the model settings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.image_gen_provider import ImageGenProvider
from agent.image_gen_provider import error_response as image_error
from agent.image_gen_provider import normalize_reference_images, resolve_aspect_ratio
from agent.image_gen_provider import success_response as image_success
from agent.transcription_provider import TranscriptionProvider
from agent.tts_provider import TTSProvider
from agent.video_gen_provider import VideoGenProvider
from agent.video_gen_provider import error_response as video_error
from agent.video_gen_provider import success_response as video_success

from . import catalog, media
from . import params as P
from .client import KEYS_URL, TaskStillRunning, WiroError, is_wiro_url
from .tools import AUDIO_OR_VIDEO, IMAGE, Build, Runtime, collect, run_build, start_and_wait

logger = logging.getLogger(__name__)

NAME = "wiro"
LABEL = "Wiro AI"
MAX_SOURCE_IMAGES = 4
NAMED_RATIO_CHOICES = {"landscape": ("16:9", "3:2", "4:3"), "portrait": ("9:16", "2:3", "3:4"), "square": ("1:1",)}
SETUP = {
    "name": LABEL,
    "badge": "paid",
    "tag": "Image, video and audio models on one key, paid as you go",
    "env_vars": [{"key": "WIRO_API_KEY", "prompt": "Wiro AI API key (KEY, or KEY:SECRET)", "url": KEYS_URL}],
}


def _still_running(task: dict) -> str:
    token = task.get("socketaccesstoken") or task.get("id") or ""
    return (f"The Wiro AI task is still running. Collect it with wiro_task and task \"{token}\" "
            "instead of starting it again.")


def _first(files: list[dict], kind: str) -> dict | None:
    return next((row for row in files if row.get("kind") == kind), None)


class WiroImageGenProvider(ImageGenProvider):
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    @property
    def name(self) -> str:
        return NAME

    @property
    def display_name(self) -> str:
        return LABEL

    def is_available(self) -> bool:
        return self.rt.available()

    def list_models(self) -> list[dict[str, Any]]:
        return [dict(row) for row in catalog.IMAGE_MODELS]

    def default_model(self) -> str | None:
        return str(self.rt.setting("image_model", catalog.DEFAULT_MODELS["image_model"]))

    def get_setup_schema(self) -> dict[str, Any]:
        return dict(SETUP)

    def capabilities(self) -> dict[str, Any]:
        return {"modalities": ["text", "image"], "max_reference_images": MAX_SOURCE_IMAGES}

    def generate(self, prompt: str, aspect_ratio: str = "landscape", *, image_url: str | None = None,
                 reference_image_urls: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        aspect = resolve_aspect_ratio(aspect_ratio)
        model = str(kwargs.get("model") or self.default_model())
        prompt = (prompt or "").strip()
        if not prompt:
            return image_error(error="Prompt is required", error_type="invalid_input", provider=NAME,
                               model=model, aspect_ratio=aspect)
        sources = ([image_url] if image_url else []) + (normalize_reference_images(reference_image_urls) or [])
        try:
            build = Build(self.rt, self.rt.client(), model)
            build.trusted = True  # Hermes' image_generate already confined the source paths
            build.text(prompt)
            has_image = build.files(sources[:MAX_SOURCE_IMAGES], P.image_param, IMAGE, "input image")
            _named_ratio(build, aspect)
            result = run_build(self.rt, build, float(catalog.MAX_WAIT_SECONDS))
        except (WiroError, ValueError) as exc:
            return image_error(error=str(exc), provider=NAME, model=model, prompt=prompt, aspect_ratio=aspect)
        if result.get("status") == "running":
            return image_error(error=_still_running({"socketaccesstoken": result.get("task_token")}),
                               error_type="still_running", provider=NAME, model=model, prompt=prompt,
                               aspect_ratio=aspect)
        if not result.get("success"):
            return image_error(error=str(result.get("error")), provider=NAME, model=model, prompt=prompt,
                               aspect_ratio=aspect)
        images = [row for row in result.get("files") or [] if row.get("kind") == "image"]
        if not images:
            return image_error(error=f"{model} returned no image.", provider=NAME, model=model, prompt=prompt,
                               aspect_ratio=aspect)
        extra = {"cost_usd": result.get("cost_usd"), "cost": result.get("cost"), "task_id": result.get("task_id"),
                 "images": [row.get("path") or row["url"] for row in images]}
        return image_success(image=images[0].get("path") or images[0]["url"], model=build.model, prompt=prompt,
                             aspect_ratio=aspect, provider=NAME, modality="image" if has_image else "text",
                             extra=extra)


def _named_ratio(build: Build, aspect: str) -> None:
    item = P.find(build.values, *P.RATIO_IDS)
    if item is None:
        return
    for candidate in NAMED_RATIO_CHOICES.get(aspect, ()):
        choice = P.match_ratio(item, candidate)
        if choice is not None:
            build.params[str(item["id"])] = choice
            return


class WiroVideoGenProvider(VideoGenProvider):
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    @property
    def name(self) -> str:
        return NAME

    @property
    def display_name(self) -> str:
        return LABEL

    def is_available(self) -> bool:
        return self.rt.available()

    def list_models(self) -> list[dict[str, Any]]:
        return [dict(row) for row in catalog.VIDEO_MODELS]

    def default_model(self) -> str | None:
        return str(self.rt.setting("video_model", catalog.DEFAULT_MODELS["video_model"]))

    def get_setup_schema(self) -> dict[str, Any]:
        return dict(SETUP)

    def capabilities(self) -> dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
            "resolutions": ["480p", "720p", "1080p"],
            "max_duration": 15,
            "min_duration": 3,
            "supports_audio": True,
            "supports_negative_prompt": True,
            "supports_seed": True,
            "supports_upscale": False,
            "max_reference_images": 0,
        }

    def generate(self, prompt: str, *, model: str | None = None, image_url: str | None = None,
                 reference_image_urls: list[str] | None = None, duration: int | None = None,
                 aspect_ratio: str = "16:9", resolution: str = "720p", negative_prompt: str | None = None,
                 audio: bool | None = None, seed: int | None = None, **kwargs: Any) -> dict[str, Any]:
        model = str(model or self.default_model())
        prompt = (prompt or "").strip()
        try:
            build = Build(self.rt, self.rt.client(), model)
            build.trusted = True  # Hermes' video_generate already confined the source path
            build.text(prompt)
            has_image = build.files([image_url] if image_url else [], P.image_param, IMAGE, "start image")
            build.number(P.DURATION_IDS, duration, "duration")
            build.ratio(aspect_ratio or None, fallback=None if has_image else "16:9")
            build.option(P.RESOLUTION_IDS, resolution, "resolution")
            build.flag(P.AUDIO_FLAG_IDS, audio, "audio")
            build.optional_text(P.NEGATIVE_IDS, negative_prompt, "negative prompt")
            build.number(P.SEED_IDS, seed, "seed")
            result = run_build(self.rt, build, float(catalog.MAX_WAIT_SECONDS))
        except (WiroError, ValueError) as exc:
            return video_error(error=str(exc), provider=NAME, model=model, prompt=prompt, aspect_ratio=aspect_ratio)
        if result.get("status") == "running":
            return video_error(error=_still_running({"socketaccesstoken": result.get("task_token")}),
                               error_type="still_running", provider=NAME, model=model, prompt=prompt,
                               aspect_ratio=aspect_ratio)
        if not result.get("success"):
            return video_error(error=str(result.get("error")), provider=NAME, model=model, prompt=prompt,
                               aspect_ratio=aspect_ratio)
        video = _first(result.get("files") or [], "video")
        if video is None:
            return video_error(error=f"{model} returned no video.", provider=NAME, model=model, prompt=prompt,
                               aspect_ratio=aspect_ratio)
        try:
            length = int(float(duration)) if duration else 0
        except (TypeError, ValueError):
            length = 0
        return video_success(video=video.get("path") or video["url"], model=build.model, prompt=prompt,
                             modality="image" if has_image else "text", aspect_ratio=aspect_ratio or "",
                             duration=length, provider=NAME,
                             extra={"cost_usd": result.get("cost_usd"), "cost": result.get("cost"),
                                    "task_id": result.get("task_id")})


class WiroTTSProvider(TTSProvider):
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    @property
    def name(self) -> str:
        return NAME

    @property
    def display_name(self) -> str:
        return LABEL

    def is_available(self) -> bool:
        return self.rt.available()

    def list_models(self) -> list[dict[str, Any]]:
        return [dict(row) for row in catalog.SPEECH_MODELS]

    def default_model(self) -> str | None:
        return str(self.rt.setting("speech_model", catalog.DEFAULT_MODELS["speech_model"]))

    def get_setup_schema(self) -> dict[str, Any]:
        return dict(SETUP)

    @property
    def voice_compatible(self) -> bool:
        return True  # speech; the gateway converts it to Opus for voice bubbles when needed

    def synthesize(self, text: str, output_path: str, *, voice: str | None = None, model: str | None = None,
                   speed: float | None = None, format: str = "mp3", **extra: Any) -> str:
        build = Build(self.rt, self.rt.client(), str(model or self.default_model()))
        build.text(text, P.TEXT_IDS, "text to read")
        build.option(P.VOICE_IDS, voice, "voice", strict=True)
        try:
            task = start_and_wait(build, float(catalog.MAX_WAIT_SECONDS))
        except TaskStillRunning as still:
            raise WiroError(_still_running(still.task)) from None
        status, pexit = task.get("status"), str(task.get("pexit") if task.get("pexit") is not None else "").strip()
        if status == "task_cancel" or pexit not in ("", "0"):
            raise WiroError(f"{build.model} could not read the text aloud (task {task.get('id')}).")
        for output in task.get("outputs") or []:
            if not isinstance(output, dict) or not output.get("url"):
                continue
            ext = media.output_extension(str(output.get("name") or ""), str(output["url"]),
                                         str(output.get("contenttype") or ""))
            if media.output_kind(ext, str(output.get("contenttype") or "")) != "audio":
                continue
            if not is_wiro_url(str(output["url"])):
                continue
            got = build.client.download(str(output["url"]), 100 * 1024 * 1024)
            if got is None:
                raise WiroError("The speech file is too large to save.")
            target = Path(output_path)
            if target.suffix.lower().lstrip(".") != ext:
                target = target.with_suffix(f".{ext}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(got[0])
            return str(target)
        raise WiroError(f"{build.model} returned no audio.")


class WiroTranscriptionProvider(TranscriptionProvider):
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    @property
    def name(self) -> str:
        return NAME

    @property
    def display_name(self) -> str:
        return LABEL

    def is_available(self) -> bool:
        return self.rt.available()

    def list_models(self) -> list[dict[str, Any]]:
        return [dict(row) for row in catalog.TRANSCRIBE_MODELS]

    def default_model(self) -> str | None:
        return str(self.rt.setting("transcribe_model", catalog.DEFAULT_MODELS["transcribe_model"]))

    def get_setup_schema(self) -> dict[str, Any]:
        return dict(SETUP)

    def transcribe(self, file_path: str, *, model: str | None = None, language: str | None = None,
                   **extra: Any) -> dict[str, Any]:
        try:
            build = Build(self.rt, self.rt.client(), str(model or self.default_model()))
            build.trusted = True  # the recording comes from Hermes, not from the model
            build.files([file_path], lambda items: P.media_param(items, "audio") or P.media_param(items, "video"),
                        AUDIO_OR_VIDEO, "audio input", required=True)
            build.option(P.LANGUAGE_IDS, language, "language")
            try:
                task = start_and_wait(build, float(catalog.MAX_WAIT_SECONDS))
            except TaskStillRunning as still:
                return {"success": False, "transcript": "", "provider": NAME, "error": _still_running(still.task)}
            result = collect(build.client, task, build.model, keep_files=False)
        except Exception as exc:  # the contract: never raise
            return {"success": False, "transcript": "", "provider": NAME, "error": str(exc)}
        if not result.get("success"):
            return {"success": False, "transcript": "", "provider": NAME, "error": str(result.get("error"))}
        return {"success": True, "transcript": str(result.get("text") or ""), "provider": NAME}


def register(ctx: Any, rt: Runtime) -> None:
    for method, provider in (
        ("register_image_gen_provider", WiroImageGenProvider(rt)),
        ("register_video_gen_provider", WiroVideoGenProvider(rt)),
        ("register_tts_provider", WiroTTSProvider(rt)),
        ("register_transcription_provider", WiroTranscriptionProvider(rt)),
    ):
        registrar = getattr(ctx, method, None)
        if registrar is None:
            continue
        try:
            registrar(provider)
        except Exception as exc:  # one backend failing to register must not take the tools down
            logger.warning("Wiro AI: %s failed: %s", method, exc)
