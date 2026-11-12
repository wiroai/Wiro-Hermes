"""The wiro_* tool handlers. Each takes the model's arguments and returns a JSON string; none raises.

A media tool maps its few arguments onto the chosen model's own parameters (from Tool/Detail),
uploads local input files, starts the task, waits up to ``wait_seconds``, then saves the output
files under Hermes' media cache and reports them with their cost. A task still running when the
wait ends keeps running on Wiro AI and is collected later with ``wiro_task``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from . import catalog, media, pricing, workflows
from . import params as P
from .client import (
    TERMINAL_STATUSES,
    TaskStillRunning,
    WiroClient,
    WiroError,
    WorkflowStillRunning,
    is_wiro_url,
    parse_model,
    split_credentials,
)

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 250 * 1024 * 1024
MAX_TEXT_FILE_BYTES = 1024 * 1024
MAX_TEXT_CHARS = 20000
MAX_INPUT_FILES = 16
IMAGE = ("image",)
AUDIO_OR_VIDEO = ("audio", "video")
ANY_MEDIA = ("image", "audio", "video", "3d", "document")
AUTO_RATIOS = ("", "auto", "adaptive", "match_input_image", "match_input", "default")
# The same bytes uploaded again in this process reuse the first upload's URL.
MAX_REMEMBERED_UPLOADS = 256
_UPLOADED: dict[tuple[str, str], str] = {}
_UPLOAD_LOCK = threading.Lock()


def dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def error(message: str, **extra: Any) -> str:
    return dumps({"success": False, "error": message, **extra})


def as_list(value: Any) -> list:
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, "")]
    return [value]


class Runtime:
    """What every handler needs at call time: credentials, settings and the session's folder."""

    def __init__(self, ctx: Any = None) -> None:
        self.ctx = ctx

    @staticmethod
    def credentials() -> tuple[str, str]:
        from agent.secret_scope import get_secret

        return split_credentials(get_secret("WIRO_API_KEY"))

    def available(self) -> bool:
        try:
            return bool(self.credentials()[0])
        except Exception:
            return False

    def client(self) -> WiroClient:
        key, secret = self.credentials()
        return WiroClient(key, secret)

    def setting(self, key: str, default: Any = None) -> Any:
        if self.ctx is None:
            return default
        try:
            value = self.ctx.get_config(key, default)
        except Exception:
            value = default
        return default if value in (None, "") else value

    def model_for(self, key: str, given: Any) -> str:
        text = str(given or "").strip()
        return text or str(self.setting(key, catalog.DEFAULT_MODELS[key]))

    def wait_for(self, value: Any, default: float | None = None) -> float:
        fallback = default if default is not None else self.setting("wait_seconds", catalog.DEFAULT_WAIT_SECONDS)
        try:
            seconds = float(fallback if value in (None, "") else value)
        except (TypeError, ValueError):
            seconds = float(catalog.DEFAULT_WAIT_SECONDS)
        return max(0.0, min(float(catalog.MAX_WAIT_SECONDS), seconds))

    def session_cwd(self) -> str | None:
        try:
            return self.ctx.session_cwd() if self.ctx is not None else None
        except Exception:
            return None

    def input_url(self, client: WiroClient, ref: Any, kinds: Iterable[str], uploads: list[dict],
                  *, trusted: bool = False) -> str:
        """A URL Wiro AI can fetch for ``ref``: a URL as it is, else the uploaded copy of the file."""
        text = str(ref or "").strip()
        if not text:
            raise ValueError("An empty file reference was given.")
        if media.is_remote(text):
            return text
        if text.startswith("data:"):
            blob = media.read_data_url(text, kinds)
            source = "data URL"
        else:
            path = text
            if not text.startswith(("/", "~", "file://")) and not re.match(r"^[A-Za-z]:[\\/]", text):
                base = self.session_cwd()
                if base:
                    path = str(Path(base) / text)
            blob = media.read_local(path, kinds, trusted=trusted)
            source = text
        key = (client.identity, hashlib.sha256(blob.data).hexdigest())
        with _UPLOAD_LOCK:
            url = _UPLOADED.get(key)
        if url is None:
            url = str(client.upload(blob.name, blob.data, blob.mime)["url"])
            with _UPLOAD_LOCK:
                if len(_UPLOADED) >= MAX_REMEMBERED_UPLOADS:
                    _UPLOADED.pop(next(iter(_UPLOADED)))
                _UPLOADED[key] = url
        uploads.append({"source": source, "url": url})
        return url


class Build:
    """Run parameters for one model, filled from the tool's arguments."""

    def __init__(self, rt: Runtime, client: WiroClient, model: str) -> None:
        self.rt, self.client = rt, client
        self.model = "/".join(parse_model(model))
        self.detail = client.model_detail(self.model)
        self.items = P.param_items(self.detail)
        self.values = [item for item in self.items if not P.is_file(item)]
        self.params: dict[str, Any] = {}
        self.notes: list[str] = []
        self.uploads: list[dict] = []
        self.defaults_used: dict[str, Any] = {}
        self.trusted = False  # set by the backends: their file paths come from Hermes, not the model

    def _missing(self, label: str) -> None:
        self.notes.append(f"{self.model} has no {label} setting, so it was left out.")

    def text(self, value: Any, ids: tuple[str, ...] = P.TEXT_IDS, label: str = "prompt") -> None:
        if value in (None, ""):
            return
        item = P.text_param(self.items, ids)
        if item is None:
            raise ValueError(f"{self.model} takes no {label}. Choose another model with wiro_search_models.")
        self.params[str(item["id"])] = str(value)

    def optional_text(self, ids: tuple[str, ...], value: Any, label: str) -> None:
        if value in (None, ""):
            return
        item = P.find(self.values, *ids)
        if item is None:
            self._missing(label)
            return
        self.params[str(item["id"])] = str(value)

    def has(self, ids: tuple[str, ...]) -> bool:
        return P.find(self.values, *ids) is not None

    def files(self, refs: Any, finder: Callable[[list[dict]], dict | None], kinds: Iterable[str], label: str,
              *, required: bool = False) -> bool:
        refs = as_list(refs)
        if not refs:
            if required:
                raise ValueError(f"Give the {label} as a URL or a local file path.")
            return False
        if len(refs) > MAX_INPUT_FILES:
            raise ValueError(f"Send at most {MAX_INPUT_FILES} files at once.")
        item = finder(self.items)
        if item is None:
            raise ValueError(f"{self.model} takes no {label}. Choose another model with wiro_search_models.")
        urls = [self.rt.input_url(self.client, ref, kinds, self.uploads, trusted=self.trusted) for ref in refs]
        P.put_files(item, urls, self.params, self.items)
        return True

    def option(self, ids: tuple[str, ...], value: Any, label: str, *, strict: bool = False) -> None:
        if value in (None, ""):
            return
        item = P.find(self.values, *ids)
        if item is None:
            self._missing(label)
            return
        choice = P.match_option(item, value)
        if choice is None:
            offered = ", ".join(P.option_values(item)[:40])
            if strict:
                raise ValueError(f"{self.model} has no {label} {value}. It offers: {offered}.")
            self.notes.append(f"{self.model} has no {label} {value} (it offers {offered}), so it used its default.")
            return
        self.params[str(item["id"])] = choice

    def ratio(self, value: Any, *, fallback: str | None = None) -> None:
        item = P.find(self.values, *P.RATIO_IDS)
        if value not in (None, ""):
            if item is None:
                self._missing("aspect ratio")
                return
            choice = P.match_ratio(item, str(value))
            if choice is None:
                offered = ", ".join(P.option_values(item)[:30])
                self.notes.append(f"{self.model} has no {value} aspect ratio (it offers {offered}), so it used its default.")
                return
            self.params[str(item["id"])] = choice
            return
        # With no input image an "auto" ratio has nothing to follow: send a real one.
        if fallback and item is not None and str(P.default_text(item) or "").strip().lower() in AUTO_RATIOS:
            choice = P.match_ratio(item, fallback)
            if choice is not None:
                self.params[str(item["id"])] = choice

    def number(self, ids: tuple[str, ...], value: Any, label: str) -> None:
        if value in (None, ""):
            return
        item = P.find(self.values, *ids)
        if item is None:
            self._missing(label)
            return
        choice = P.match_number(item, value)
        if choice is None:
            self.notes.append(f"{label} {value} does not fit {self.model}, so it used its default.")
            return
        try:
            snapped = float(choice) != float(value)
        except (TypeError, ValueError):
            snapped = str(choice) != str(value)
        if snapped:
            self.notes.append(f"{self.model} takes no {label} of {value}; it used {choice}.")
        self.params[str(item["id"])] = choice

    def flag(self, ids: tuple[str, ...], on: Any, label: str) -> None:
        if on is None:
            return
        item = P.find(self.values, *ids)
        if item is None:
            self._missing(label)
            return
        self.params[str(item["id"])] = P.flag_value(item, bool(on))

    def extra(self, extra: Any, kinds: Iterable[str] = ANY_MEDIA) -> None:
        """Raw model parameters from the caller; they win over the values the tool filled in."""
        if extra in (None, "", {}):
            return
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                raise ValueError('params must be an object of parameter ids and values, like {"resolution": "2K"}.') from None
        if not isinstance(extra, Mapping):
            raise ValueError('params must be an object of parameter ids and values, like {"resolution": "2K"}.')
        known = P.known_ids(self.items)
        for key, value in extra.items():
            key = str(key)
            item = P.find(self.items, key)
            if item is not None and P.is_file(item) and str(item["id"]) == key:
                refs = as_list(value)
                if refs:
                    self._drop_file_fields(item)
                    urls = [self.rt.input_url(self.client, ref, kinds, self.uploads) for ref in refs]
                    P.put_files(item, urls, self.params, self.items)
                continue
            if key not in known:
                self.notes.append(f"{self.model} lists no parameter {key}; it was sent as given.")
            self.params[key] = value

    def _drop_file_fields(self, item: Mapping[str, Any]) -> None:
        item_id = str(item["id"])
        for key in (item_id, f"{item_id}Url", item_id.replace("Multiple", "UrlMultiple")):
            self.params.pop(key, None)

    def finish(self) -> dict[str, Any]:
        self.params, self.defaults_used = P.fill_required(self.items, self.params)
        return self.params


# -- results -----------------------------------------------------------------------------------


def _shown_settings(params: Mapping[str, Any]) -> dict[str, Any]:
    shown = {}
    for key, value in params.items():
        if isinstance(value, str) and len(value) > 160:
            value = value[:160] + "..."
        shown[key] = value
    return shown


def _raw_answer(outputs: list) -> str:
    for output in outputs:
        if isinstance(output, dict) and output.get("contenttype") == "raw" and isinstance(output.get("content"), dict):
            content = output["content"]
            answer = content.get("answer")
            if isinstance(answer, list):
                answer = "".join(part if isinstance(part, str) else str((part or {}).get("text") or "") for part in answer)
            text = str(answer or content.get("raw") or "").strip()
            if text:
                return text
    return ""


def save_outputs(client: WiroClient, outputs: Iterable[Any], notes: list[str], *,
                 keep_files: bool = True) -> tuple[list[dict], list[str]]:
    """Download each output file into Hermes' media cache. Returns (file rows, texts of text files).
    With ``keep_files`` off, text files are only read and other files are left at their URLs."""
    files: list[dict] = []
    texts: list[str] = []
    for output in outputs:
        if not isinstance(output, dict) or output.get("contenttype") == "raw":
            continue
        url = str(output.get("url") or "").strip()
        if not url:
            continue
        name = str(output.get("name") or "")
        declared = str(output.get("contenttype") or "")
        ext = media.output_extension(name, url, declared)
        kind = media.output_kind(ext, declared)
        row: dict[str, Any] = {"kind": kind, "url": url}
        mime = declared if declared and declared != "application/octet-stream" else media.guess_mime(f"f.{ext}", kind)
        row["mime_type"] = mime
        if not keep_files and kind != "text":
            files.append(row)
            continue
        if not is_wiro_url(url):
            notes.append(f"{name or url} is not on a Wiro AI host, so it was not downloaded; open the URL.")
            files.append(row)
            continue
        try:
            got = client.download(url, MAX_TEXT_FILE_BYTES if kind == "text" else MAX_OUTPUT_BYTES)
        except WiroError as exc:
            notes.append(f"{exc} Open the URL instead.")
            files.append(row)
            continue
        if got is None:
            notes.append(f"{name or 'An output file'} is too large to save here; open the URL.")
            files.append(row)
            continue
        data, _content_type = got
        if not keep_files:
            texts.append(data.decode("utf-8", "replace").strip())
            files.append(row)
            continue
        try:
            path = media.save_output(data, kind, ext)
        except OSError as exc:
            notes.append(f"Could not save {name or 'an output file'} ({exc}); open the URL.")
            files.append(row)
            continue
        row["path"] = str(path)
        row["size_bytes"] = len(data)
        files.append(row)
        if kind == "text":
            texts.append(data.decode("utf-8", "replace").strip())
    return files, texts


def _clip(text: str, notes: list[str]) -> str:
    if len(text) > MAX_TEXT_CHARS:
        notes.append(f"The text was cut to {MAX_TEXT_CHARS} characters; the full text is in the saved file or URL.")
        return text[:MAX_TEXT_CHARS]
    return text


def _media_tag(files: list[dict]) -> str:
    return "\n".join(media.media_tag(row["path"]) for row in files if row.get("path") and row.get("kind") != "text")


def running(task: Mapping[str, Any], model: str, expected: float | None = None) -> dict[str, Any]:
    token = str(task.get("socketaccesstoken") or "")
    taskid = str(task.get("id") or "")
    payload: dict[str, Any] = {
        "success": True,
        "status": "running",
        "model": model,
        "task_id": taskid,
        "task_token": token,
        "last_status": str(task.get("status") or "task_queue"),
    }
    if expected is not None:
        payload["expected_cost"] = f"{pricing.money(expected)} list price for these settings"
    payload["next"] = (
        f'The task keeps running on Wiro AI. Call wiro_task with task "{token or taskid}" to wait for it and '
        "get the files. Do not start the same job again: a second run is charged separately.")
    return payload


def collect(client: WiroClient, task: Mapping[str, Any], model: str = "", notes: Iterable[str] = (), *,
            keep_files: bool = True) -> dict[str, Any]:
    """The tool result for a task: its files and text when it succeeded, else why it did not."""
    notes = list(notes)
    model = model or "/".join(filter(None, (str(task.get("modelslugowner") or ""), str(task.get("modelslugproject") or ""))))
    status = str(task.get("status") or "")
    pexit = "" if task.get("pexit") is None else str(task.get("pexit")).strip()
    base: dict[str, Any] = {"model": model, "task_id": str(task.get("id") or ""),
                            "task_token": str(task.get("socketaccesstoken") or "")}
    cost_usd, cost_text = pricing.task_cost(task)
    if status not in TERMINAL_STATUSES:
        return running(task, model)
    if status == "task_cancel":
        return {"success": False, "status": "cancelled", **base, "cost_usd": cost_usd, "cost": cost_text,
                "error": f"Task {base['task_id']} was cancelled before it finished."}
    # Only a finished task with an exit code other than 0 failed; an empty exit code is not a failure.
    if pexit not in ("", "0"):
        return {"success": False, "status": "failed", **base, "exit_code": pexit, "cost_usd": cost_usd,
                "cost": cost_text,
                "error": f"{model} could not finish task {base['task_id']} (exit code {pexit}). Failed tasks are "
                         "not charged. Try other settings or another model."}
    outputs = [o for o in task.get("outputs") or [] if isinstance(o, dict)]
    files, texts = save_outputs(client, outputs, notes, keep_files=keep_files)
    text = _raw_answer(outputs) or "\n\n".join(t for t in texts if t)
    if not text and not files:
        text = str(task.get("debugoutput") or "").strip()
    payload: dict[str, Any] = {"success": True, "status": "completed", **base}
    if files:
        payload["files"] = files
        tag = _media_tag(files)
        if tag:
            payload["media_tag"] = tag
    if text:
        payload["text"] = _clip(text, notes)
    if not files and not text:
        notes.append("The task finished without output files or text.")
    payload["cost_usd"] = cost_usd
    payload["cost"] = cost_text
    try:
        payload["elapsed_seconds"] = round(float(task.get("elapsedseconds")), 1)
    except (TypeError, ValueError):
        pass
    if notes:
        payload["notes"] = notes
    return payload


def start_and_wait(build: Build, wait: float) -> dict[str, Any]:
    """Start the task and wait for it to end. Raises TaskStillRunning when the wait runs out."""
    params = build.finish()
    taskid, token = build.client.run(build.model, params)
    try:
        task = build.client.wait(token, wait, taskid=taskid)
    except TaskStillRunning as still:
        still.task = {**still.task, "id": still.task.get("id") or taskid, "socketaccesstoken": token}
        raise
    return {**task, "socketaccesstoken": task.get("socketaccesstoken") or token}


def run_build(rt: Runtime, build: Build, wait: float, outputs: int = 1) -> dict[str, Any]:
    params = build.finish()
    expected = pricing.expected_price(build.detail, params, outputs)
    taskid, token = build.client.run(build.model, params)
    extra: dict[str, Any] = {"settings": _shown_settings({k: v for k, v in params.items()
                                                           if k not in build.defaults_used})}
    if build.defaults_used:
        extra["defaults_used"] = build.defaults_used
    if build.uploads:
        extra["uploaded_inputs"] = build.uploads
    if wait <= 0:
        payload = running({"id": taskid, "socketaccesstoken": token, "status": "task_queue"}, build.model, expected)
        return {**payload, **extra, **({"notes": build.notes} if build.notes else {})}
    try:
        task = build.client.wait(token, wait, taskid=taskid)
    except TaskStillRunning as still:
        task = {**still.task, "id": still.task.get("id") or taskid, "socketaccesstoken": token}
        payload = running(task, build.model, expected)
        return {**payload, **extra, **({"notes": build.notes} if build.notes else {})}
    task = {**task, "socketaccesstoken": task.get("socketaccesstoken") or token}
    result = collect(build.client, task, build.model, build.notes)
    if result.get("success"):
        result.update(extra)
    return result


def guarded(name: str, fn: Callable[[dict], dict]) -> Callable[..., str]:
    """Turn a handler that may raise into one that always returns JSON (Hermes' tool contract)."""

    def handler(args: dict | None = None, **_kwargs: Any) -> str:
        try:
            return dumps(fn(dict(args or {})))
        except (WiroError, ValueError, media.InputRefused) as exc:
            return error(str(exc))
        except Exception as exc:  # never let a tool call crash the turn
            logger.warning("%s failed: %s", name, exc, exc_info=True)
            return error(f"{name} failed: {type(exc).__name__}: {exc}")

    handler.__name__ = name
    return handler


# -- media tools -------------------------------------------------------------------------------


def generate_image(rt: Runtime, args: dict) -> dict:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required.")
    client = rt.client()
    build = Build(rt, client, rt.model_for("image_model", args.get("model")))
    build.text(prompt)
    refs = build.files(args.get("reference_images"), P.image_param, IMAGE, "reference image")
    build.ratio(args.get("aspect_ratio"), fallback=None if refs else "1:1")
    build.option(P.RESOLUTION_IDS, args.get("resolution"), "resolution")
    build.number(P.COUNT_IDS, args.get("count"), "image count")
    build.number(P.SEED_IDS, args.get("seed"), "seed")
    build.optional_text(P.NEGATIVE_IDS, args.get("negative_prompt"), "negative prompt")
    build.extra(args.get("params"), IMAGE)
    return run_build(rt, build, rt.wait_for(args.get("wait_seconds")), _count(args.get("count")))


def _count(value: Any) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


EDIT_OPERATIONS = {"edit": "edit_model", "upscale": "upscale_model", "remove_background": "background_model"}


def edit_image(rt: Runtime, args: dict) -> dict:
    operation = str(args.get("operation") or "edit").strip().lower().replace("-", "_").replace(" ", "_")
    if operation in ("background", "remove_bg", "rembg", "background_removal"):
        operation = "remove_background"
    if operation not in EDIT_OPERATIONS:
        raise ValueError("operation must be edit, upscale or remove_background.")
    images = as_list(args.get("image")) + as_list(args.get("more_images"))
    if not images:
        raise ValueError("image is required: a URL or a local file path.")
    prompt = str(args.get("prompt") or "").strip()
    if operation == "edit" and not prompt:
        raise ValueError("prompt is required for operation edit: describe the change.")
    client = rt.client()
    build = Build(rt, client, rt.model_for(EDIT_OPERATIONS[operation], args.get("model")))
    if operation == "edit":
        build.text(prompt)
        build.files(images, P.image_param, IMAGE, "input image", required=True)
        build.ratio(args.get("aspect_ratio"))
        build.option(P.RESOLUTION_IDS, args.get("resolution"), "resolution")
    else:
        if len(images) > 1:
            raise ValueError(f"{operation} takes one image per call.")
        build.files(images, P.image_param, IMAGE, "input image", required=True)
        if operation == "upscale":
            build.number(P.SCALE_IDS, args.get("scale"), "upscale factor")
        if prompt:
            build.optional_text(P.PROMPT_IDS, prompt, "prompt")
    build.extra(args.get("params"), IMAGE)
    result = run_build(rt, build, rt.wait_for(args.get("wait_seconds")))
    result.setdefault("operation", operation)
    return result


def generate_video(rt: Runtime, args: dict) -> dict:
    prompt = str(args.get("prompt") or "").strip()
    image = args.get("image")
    if not prompt and not image:
        raise ValueError("prompt is required (and image, to animate a picture).")
    client = rt.client()
    build = Build(rt, client, rt.model_for("video_model", args.get("model")))
    build.text(prompt)
    has_image = build.files(as_list(image)[:1], P.image_param, IMAGE, "start image")
    build.files(as_list(args.get("end_image"))[:1], P.last_image_param, IMAGE, "end image")
    build.number(P.DURATION_IDS, args.get("duration_seconds"), "duration")
    build.ratio(args.get("aspect_ratio"), fallback=None if has_image else "16:9")
    build.option(P.RESOLUTION_IDS, args.get("resolution"), "resolution")
    build.flag(P.AUDIO_FLAG_IDS, args.get("audio"), "audio")
    build.optional_text(P.NEGATIVE_IDS, args.get("negative_prompt"), "negative prompt")
    build.number(P.SEED_IDS, args.get("seed"), "seed")
    build.extra(args.get("params"), IMAGE)
    return run_build(rt, build, rt.wait_for(args.get("wait_seconds")))


def text_to_speech(rt: Runtime, args: dict) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        raise ValueError("text is required.")
    client = rt.client()
    build = Build(rt, client, rt.model_for("speech_model", args.get("model")))
    build.text(text, P.TEXT_IDS, "text to read")
    build.option(P.VOICE_IDS, args.get("voice"), "voice", strict=True)
    build.option(P.LANGUAGE_IDS, args.get("language"), "language")
    build.extra(args.get("params"), AUDIO_OR_VIDEO)
    return run_build(rt, build, rt.wait_for(args.get("wait_seconds")))


def generate_music(rt: Runtime, args: dict) -> dict:
    prompt = str(args.get("prompt") or "").strip()
    lyrics = str(args.get("lyrics") or "").strip()
    if not prompt and not lyrics:
        raise ValueError("prompt is required: describe the style, mood and instruments.")
    client = rt.client()
    build = Build(rt, client, rt.model_for("music_model", args.get("model")))
    instrumental = args.get("instrumental")
    if lyrics:
        if build.has(P.LYRICS_IDS):
            build.optional_text(P.LYRICS_IDS, lyrics, "lyrics")
        else:
            prompt = f"{prompt}\n\nLyrics:\n{lyrics}".strip()
        if instrumental is None:
            instrumental = False
    build.text(prompt)
    build.flag(P.INSTRUMENTAL_IDS, instrumental, "instrumental")
    build.number(P.DURATION_IDS, args.get("duration_seconds"), "duration")
    build.extra(args.get("params"), AUDIO_OR_VIDEO + IMAGE)
    return run_build(rt, build, rt.wait_for(args.get("wait_seconds")))


def transcribe(rt: Runtime, args: dict) -> dict:
    audio = as_list(args.get("audio"))
    if not audio:
        raise ValueError("audio is required: a URL or a local file path (a voice note, mp3, wav, m4a or a video).")
    client = rt.client()
    build = Build(rt, client, rt.model_for("transcribe_model", args.get("model")))
    build.files(audio[:1], lambda items: P.media_param(items, "audio") or P.media_param(items, "video"),
                AUDIO_OR_VIDEO, "audio input", required=True)
    build.option(P.LANGUAGE_IDS, args.get("language"), "language")
    build.extra(args.get("params"), AUDIO_OR_VIDEO)
    return run_build(rt, build, rt.wait_for(args.get("wait_seconds")))


def generate_3d(rt: Runtime, args: dict) -> dict:
    images = as_list(args.get("image"))
    if not images:
        raise ValueError("image is required: a picture of the object (make one first with wiro_generate_image).")
    client = rt.client()
    build = Build(rt, client, rt.model_for("model3d_model", args.get("model")))
    build.files(images, P.image_param, IMAGE, "input image", required=True)
    build.optional_text(P.PROMPT_IDS, args.get("prompt"), "prompt")
    build.number(P.SEED_IDS, args.get("seed"), "seed")
    build.extra(args.get("params"), IMAGE)
    return run_build(rt, build, rt.wait_for(args.get("wait_seconds"), 300))


# -- catalog tools -------------------------------------------------------------------------------


def _model_id(row: Mapping[str, Any]) -> str:
    owner = row.get("cleanslugowner") or row.get("slugowner") or ""
    project = row.get("cleanslugproject") or row.get("slugproject") or ""
    return f"{owner}/{project}"


def _public_categories(row: Mapping[str, Any]) -> list[str]:
    return [str(c) for c in row.get("categories") or [] if "-to-" in str(c) or str(c) in catalog.PUBLIC_CATEGORIES]


def _short_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3].rsplit(" ", 1)[0] + "..."


def search_models(rt: Runtime, args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    category = str(args.get("category") or "").strip()
    try:
        limit = max(1, min(int(args.get("limit") or 10), 50))
        start = max(0, int(args.get("start") or 0))
    except (TypeError, ValueError):
        raise ValueError("limit and start must be whole numbers.") from None
    if not query and not category:
        raise ValueError("Give a query (one to three key words) or a category.")
    client = rt.client()
    rows, total = client.search_models(query, [category] if category else [], owner=str(args.get("owner") or ""),
                                       start=start, limit=limit)
    models = []
    for row in rows:
        stat = row.get("taskstat") or {}
        entry = {
            "model": _model_id(row),
            "title": _short_text(row.get("title"), 100),
            "categories": _public_categories(row),
            "price": pricing.price_summary(row),
            "description": _short_text(row.get("seodescription") or row.get("description"), 220),
        }
        try:
            if int(stat.get("runcount") or 0):
                entry["runs"] = int(stat["runcount"])
        except (TypeError, ValueError, AttributeError):
            pass
        entry["page"] = f"https://wiro.ai/models/{entry['model']}"
        models.append(entry)
    result: dict[str, Any] = {"success": True, "total": total, "start": start, "models": models}
    if start + len(models) < total:
        result["next_start"] = start + len(models)
    if not models:
        result["hint"] = "No model matched. Try fewer or other key words, or a category."
    else:
        result["hint"] = "wiro_model_info shows a model's settings and full price list before you run it."
    return result


def model_info(rt: Runtime, args: dict) -> dict:
    model = "/".join(parse_model(str(args.get("model") or "")))
    client = rt.client()
    detail = client.model_detail(model)
    items = P.param_items(detail)
    result: dict[str, Any] = {
        "success": True,
        "model": model,
        "title": _short_text(detail.get("title"), 120),
        "description": _short_text(detail.get("seodescription") or detail.get("description"), 400),
        "categories": _public_categories(detail),
        "price": pricing.price_summary(detail),
        "parameters": [P.describe_item(item) for item in items],
        "page": f"https://wiro.ai/models/{model}",
    }
    table = pricing.price_table(detail)
    if table:
        result["price_table"] = table
    result["how_to_run"] = (
        "Call wiro_run_model with this model and params (parameter id -> value). Required parameters without a "
        "default must be given; file parameters take URLs or local file paths. The media tools "
        "(wiro_generate_image and the rest) also take this model in their model argument.")
    return result


def run_model(rt: Runtime, args: dict) -> dict:
    model = str(args.get("model") or "").strip()
    if not model:
        raise ValueError("model is required, as owner/model (find one with wiro_search_models).")
    client = rt.client()
    build = Build(rt, client, model)
    files = args.get("files") or {}
    if isinstance(files, str):
        try:
            files = json.loads(files)
        except json.JSONDecodeError:
            raise ValueError("files must be an object of file parameter ids and URLs or paths.") from None
    if not isinstance(files, Mapping):
        raise ValueError("files must be an object of file parameter ids and URLs or paths.")
    for key, refs in files.items():
        item = P.find(build.items, str(key))
        if item is None or not P.is_file(item):
            names = ", ".join(str(i["id"]) for i in P.file_params(build.items)) or "none"
            raise ValueError(f"{build.model} has no file parameter {key}. Its file parameters: {names}.")
        refs = as_list(refs)
        if refs:
            urls = [rt.input_url(client, ref, ANY_MEDIA, build.uploads) for ref in refs]
            P.put_files(item, urls, build.params, build.items)
    build.extra(args.get("params"))
    return run_build(rt, build, rt.wait_for(args.get("wait_seconds")))


# -- tasks ---------------------------------------------------------------------------------------


def _task_ref(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("task is required: the task_token (or task_id) a wiro_* tool returned.")
    return ("", text) if text.isdigit() else (text, "")


def _task_row(task: Mapping[str, Any]) -> dict[str, Any]:
    cost_usd, cost_text = pricing.task_cost(task)
    row = {
        "task_id": str(task.get("id") or ""),
        "task_token": str(task.get("socketaccesstoken") or ""),
        "model": "/".join(filter(None, (str(task.get("modelslugowner") or ""), str(task.get("modelslugproject") or "")))),
        "status": str(task.get("status") or ""),
    }
    pexit = "" if task.get("pexit") is None else str(task.get("pexit")).strip()
    if row["status"] == "task_postprocess_end":
        row["result"] = "failed" if pexit not in ("", "0") else "completed"
    elif row["status"] == "task_cancel":
        row["result"] = "cancelled"
    else:
        row["result"] = "running"
    row["cost"] = cost_text
    if cost_usd is not None:
        row["cost_usd"] = cost_usd
    for key, label in (("starttime", "started"), ("elapsedseconds", "elapsed_seconds")):
        if task.get(key) not in (None, "", "0"):
            row[label] = task.get(key)
    return row


def task_tool(rt: Runtime, args: dict) -> dict:
    action = str(args.get("action") or "wait").strip().lower()
    client = rt.client()
    if action == "list":
        try:
            limit = max(1, min(int(args.get("limit") or 10), 50))
        except (TypeError, ValueError):
            limit = 10
        rows = [_task_row(t) for t in client.recent_tasks(limit, str(args.get("model") or ""))]
        return {"success": True, "tasks": rows}
    token, taskid = _task_ref(args.get("task"))
    if action == "status":
        current = client.task(token=token, taskid=taskid)
        row = _task_row(current)
        if row["result"] == "completed":
            row["next"] = "Call wiro_task with action wait to save the output files."
        return {"success": True, **row}
    if action == "cancel":
        current = client.task(token=token, taskid=taskid)
        if current.get("status") in TERMINAL_STATUSES:
            return {"success": True, **_task_row(current), "note": "The task had already finished."}
        client.stop(current)
        after = client.task(token=str(current.get("socketaccesstoken") or token), taskid="" if token else taskid)
        return {"success": True, **_task_row(after),
                "note": "Stop requested. A task that was already running can still be charged when its work ends."}
    if action != "wait":
        raise ValueError("action must be wait, status, cancel or list.")
    wait = rt.wait_for(args.get("wait_seconds"))
    try:
        current = client.wait(token, wait, taskid=taskid) if token else _wait_by_id(client, taskid, wait)
    except TaskStillRunning as still:
        return running({**still.task, "socketaccesstoken": still.task.get("socketaccesstoken") or token,
                        "id": still.task.get("id") or taskid}, _task_row(still.task)["model"])
    return collect(client, current)


def _wait_by_id(client: WiroClient, taskid: str, wait: float) -> dict:
    first = client.task(taskid=taskid)
    if first.get("status") in TERMINAL_STATUSES:
        return first
    return client.wait(str(first.get("socketaccesstoken") or ""), wait, taskid=taskid)


# -- workflows -----------------------------------------------------------------------------------


def list_workflows(rt: Runtime, args: dict) -> dict:
    action = str(args.get("action") or "list").strip().lower()
    client = rt.client()
    if action == "get":
        slug = workflows.parse_slug(str(args.get("workflow") or ""))
        row = client.workflow_detail(slug)
        contents = workflows.contents_of(row)
        entry = workflows.summary(row)
        entry["inputs"] = workflows.inputs(contents)
        entry["outputs"] = sorted(workflows.output_fields(contents))
        entry["how_to_run"] = ("Call wiro_run_workflow with this workflow and inputs keyed as listed. Inputs left out "
                               "use the saved value, then the default. File inputs take URLs or local file paths.")
        return {"success": True, **entry}
    if action != "list":
        raise ValueError("action must be list or get.")
    scope = "public" if str(args.get("scope") or "mine").strip().lower() == "public" else "mine"
    try:
        limit = max(1, min(int(args.get("limit") or 20), 100))
        start = max(0, int(args.get("start") or 0))
    except (TypeError, ValueError):
        raise ValueError("limit and start must be whole numbers.") from None
    rows, total = client.list_workflows(scope, str(args.get("search") or ""), start, limit)
    result: dict[str, Any] = {"success": True, "scope": scope, "total": total,
                              "workflows": [workflows.summary(row) for row in rows]}
    if start + len(rows) < total:
        result["next_start"] = start + len(rows)
    return result


def _workflow_inputs(rt: Runtime, client: WiroClient, inputs: Any, uploads: list[dict]) -> dict[str, Any]:
    if inputs in (None, ""):
        return {}
    if isinstance(inputs, str):
        try:
            inputs = json.loads(inputs)
        except json.JSONDecodeError:
            raise ValueError("inputs must be an object keyed as wiro_workflows action get lists them.") from None
    if not isinstance(inputs, Mapping):
        raise ValueError("inputs must be an object keyed as wiro_workflows action get lists them.")
    ready: dict[str, Any] = {}
    for key, value in inputs.items():
        if value == workflows.HIDDEN:
            raise ValueError(f'Input "{key}" is "{workflows.HIDDEN}", a placeholder for a hidden value. Leave it out '
                             "to use the saved value, or pass the real value.")
        if isinstance(value, str) and _looks_like_local_file(value):
            value = rt.input_url(client, value, ANY_MEDIA, uploads)
        elif isinstance(value, list) and value and all(isinstance(v, str) and _looks_like_local_file(v) for v in value):
            value = [rt.input_url(client, v, ANY_MEDIA, uploads) for v in value]
        ready[str(key)] = value
    return ready


def _looks_like_local_file(value: str) -> bool:
    text = value.strip()
    if not text or "\n" in text or len(text) > 1024:
        return False
    if text.startswith("file://"):
        return True
    if text.startswith(("/", "~/")):
        try:
            return Path(text).expanduser().is_file()
        except OSError:
            return False
    return False


def run_workflow(rt: Runtime, args: dict) -> dict:
    client = rt.client()
    wait = rt.wait_for(args.get("wait_seconds"))
    token = str(args.get("run_token") or "").strip()
    uploads: list[dict] = []
    slug = ""
    if not token:
        slug = workflows.parse_slug(str(args.get("workflow") or ""))
        inputs = _workflow_inputs(rt, client, args.get("inputs"), uploads)
        token = client.run_workflow(slug, inputs)
    try:
        body = client.wait_workflow(token, wait) if wait > 0 else client.workflow_run(token)
    except WorkflowStillRunning as still:
        body = still.body
    result = workflow_result(client, body, token, slug)
    if uploads:
        result["uploaded_inputs"] = uploads
    return result


def workflow_result(client: WiroClient, body: Mapping[str, Any], token: str, slug: str = "") -> dict[str, Any]:
    run = body.get("workflowrun") if isinstance(body.get("workflowrun"), dict) else {}
    nodes = [n for n in body.get("workflowrunnodes") or [] if isinstance(n, dict)]
    status = str(run.get("status") or "running")
    slug = str(run.get("workflowslug") or slug)
    base: dict[str, Any] = {"workflow": slug, "run_token": str(run.get("workflowtoken") or token), "status": status}
    charged = pricing.number(run.get("chargedcost"))
    listed = pricing.number(run.get("totalcost"))
    if status not in ("completed", "failed", "cancelled", "stopped"):
        done = sum(1 for n in nodes if n.get("status") == "completed")
        return {"success": True, **base, "steps_done": done, "steps_total": len(nodes),
                "next": (f'The run keeps going on Wiro AI. Call wiro_run_workflow with run_token "{base["run_token"]}" '
                         "to wait for it. Do not start the workflow again: a second run is charged separately.")}
    cost_usd = charged if charged is not None else listed
    base["cost_usd"] = cost_usd
    base["cost"] = (f"{pricing.money(charged)} charged" if charged is not None
                    else f"{pricing.money(listed)} list price" if listed is not None else "not reported")
    if status != "completed":
        return {"success": False, **base, "failed_steps": workflows.failed_steps(nodes),
                "error": f"The workflow run ended as {status}."}
    outputs, missing = workflows.run_outputs(workflows.contents_of(run), nodes)
    notes: list[str] = [f"Not available: {m}" for m in missing]
    results: dict[str, Any] = {}
    all_files: list[dict] = []
    for field, items in outputs.items():
        file_items = [{"url": i["url"], "name": i.get("name", ""), "contenttype": i.get("mime_type", "")}
                      for i in items if i.get("url")]
        files, texts = save_outputs(client, file_items, notes)
        text = "\n\n".join([i["text"] for i in items if i.get("text")] + [t for t in texts if t])
        entry: dict[str, Any] = {}
        if files:
            entry["files"] = files
            all_files.extend(files)
        if text:
            entry["text"] = _clip(text, notes)
        results[field] = entry
    result: dict[str, Any] = {"success": True, **base, "outputs": results}
    tag = _media_tag(all_files)
    if tag:
        result["media_tag"] = tag
    try:
        result["elapsed_seconds"] = round(float(run.get("elapsedseconds")), 1)
    except (TypeError, ValueError):
        pass
    if notes:
        result["notes"] = notes
    return result
