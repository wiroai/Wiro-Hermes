"""The wiro_* tools end to end against an in-memory Wiro AI API (the real WiroClient, a fake network)."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path

import pytest
from conftest import media_plugin
from fake_wiro import GLB, MP3, MP4, PNG, FakeClock, FakeWiro, item, model, task_row

tools = media_plugin.tools
client_mod = media_plugin.client
HANDLERS = media_plugin.HANDLERS

NANO = model("google", "nano-banana-2-1", [
    item("inputImage", "combinefileinput", default="https://cdn.wiro.ai/sample.jpg"),
    item("prompt", "textarea", True, "sample prompt"),
    item("resolution", "select", True, "1K", ["1K", "2K", "4K"]),
    item("aspectRatio", "select", False, "auto", ["auto", "1:1", "3:2", "16:9", "9:16"]),
    item("outputFormat", "select", False, "jpeg", ["jpeg", "png"]),
], dynamicprice=[{"inputs": {"resolution": "1K"}, "price": 0.0336, "priceMethod": "cpr"},
                 {"inputs": {"resolution": "2K"}, "price": 0.0504, "priceMethod": "cpr"}])


@pytest.fixture
def wiro(monkeypatch, tmp_path):
    fake = FakeWiro()
    clock = FakeClock()
    home = tmp_path / "hermes"
    (home / "cache" / "images").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(tools.Runtime, "credentials", staticmethod(lambda: fake_credentials[0]))
    fake_credentials = [("test-key", "")]

    def make_client(_self):
        key, secret = fake_credentials[0]
        return client_mod.WiroClient(key, secret, opener=fake, sleep=clock.sleep, clock=clock)

    monkeypatch.setattr(tools.Runtime, "client", make_client)
    client_mod.WiroClient._detail_cache.clear()
    tools._UPLOADED.clear()
    fake.home = home
    fake.clock = clock
    fake.credentials = fake_credentials
    fake.models["google/nano-banana-2-1"] = NANO
    return fake


def call(name: str, args: dict, runtime=None) -> dict:
    handler = tools.guarded(name, lambda a: HANDLERS[name](runtime or tools.Runtime(None), a))
    return json.loads(handler(args, task_id="t1"))


def finish(fake: FakeWiro, token: str, outputs: list, **extra) -> None:
    fake.tasks[token] = [task_row(token, "task_queue"), task_row(token, "task_start"),
                         task_row(token, "task_postprocess_end", pexit="0", outputs=outputs, **extra)]


def test_generate_image_maps_arguments_runs_and_saves_the_file(wiro):
    wiro.cdn["https://cdn1.wiro.ai/out/0.jpeg"] = (PNG, "image/jpeg")
    finish(wiro, "tok-1", [{"name": "0.jpeg", "contenttype": "image/jpeg", "url": "https://cdn1.wiro.ai/out/0.jpeg"}],
           totalcost="0.0336", chargedcost="0.0336")
    result = call("wiro_generate_image", {"prompt": "a red fox", "aspect_ratio": "landscape"})
    assert result["success"] and result["status"] == "completed"
    slug, params = wiro.runs[0]
    assert slug == "google/nano-banana-2-1"
    assert params == {"prompt": "a red fox", "aspectRatio": "16:9", "resolution": "1K"}
    [saved] = result["files"]
    path = Path(saved["path"])
    assert path.parent == wiro.home / "cache" / "generated" / "images" and path.suffix == ".jpg"
    assert path.read_bytes() == PNG
    assert result["media_tag"] == f"MEDIA:{path}"
    assert result["cost_usd"] == 0.0336 and result["cost"] == "$0.0336 charged"
    assert result["defaults_used"] == {"resolution": "1K"}
    assert "secret stderr" not in json.dumps(result)  # debugerror is never shown


def test_text_to_image_sends_a_real_ratio_instead_of_auto(wiro):
    finish(wiro, "tok-1", [])
    call("wiro_generate_image", {"prompt": "a fox"})
    assert wiro.runs[0][1]["aspectRatio"] == "1:1"


def test_only_a_nonzero_exit_code_is_a_failure(wiro):
    wiro.tasks["tok-1"] = [task_row("tok-1", "task_postprocess_end", pexit="1")]
    failed = call("wiro_generate_image", {"prompt": "x"})
    assert failed["success"] is False and failed["status"] == "failed" and failed["exit_code"] == "1"
    assert "not charged" in failed["error"] and "secret stderr" not in json.dumps(failed)

    wiro.next_task = ["tok-2"]
    wiro.cdn["https://cdn1.wiro.ai/o.png"] = (PNG, "image/png")
    wiro.tasks["tok-2"] = [task_row("tok-2", "task_postprocess_end", pexit="",
                                    outputs=[{"name": "o.png", "url": "https://cdn1.wiro.ai/o.png"}])]
    empty_exit = call("wiro_generate_image", {"prompt": "x"})
    assert empty_exit["success"] and empty_exit["status"] == "completed"

    wiro.next_task = ["tok-3"]
    wiro.tasks["tok-3"] = [task_row("tok-3", "task_cancel", accepttime="0")]
    cancelled = call("wiro_generate_image", {"prompt": "x"})
    assert cancelled["status"] == "cancelled" and cancelled["cost_usd"] == 0.0


def test_persisted_task_error_status_is_still_running(wiro):
    wiro.tasks["tok-1"] = [task_row("tok-1", "task_error")]
    result = call("wiro_generate_image", {"prompt": "x", "wait_seconds": 30})
    assert result["status"] == "running" and result["task_token"] == "tok-1"
    assert "wiro_task" in result["next"] and "charged separately" in result["next"]
    assert result["expected_cost"] == "$0.0336 list price for these settings"


def test_a_long_task_is_collected_with_wiro_task(wiro):
    wiro.models["bytedance/seedance-v1-pro-fast"] = model("bytedance", "seedance-v1-pro-fast", [
        item("inputImage", "combinefileinput", default="https://cdn.wiro.ai/s.jpg"),
        item("prompt", "textarea", False, "sample"),
        item("resolution", "select", True, "480p", ["480p", "720p", "1080p"]),
        item("ratio", "select", True, "adaptive", ["adaptive", "16:9", "9:16", "1:1"]),
        item("duration", "select", True, "5", ["5", "10"]),
    ])
    wiro.tasks["tok-1"] = [task_row("tok-1", "task_start")]
    started = call("wiro_generate_video", {"prompt": "waves", "duration_seconds": 8, "resolution": "720P",
                                           "wait_seconds": 20})
    assert started["status"] == "running"
    assert wiro.runs[0][1] == {"prompt": "waves", "duration": "10", "ratio": "16:9", "resolution": "720p"}
    assert any("duration of 8" in note for note in started["notes"])
    wiro.cdn["https://cdn1.wiro.ai/v.mp4"] = (MP4, "video/mp4")
    wiro.tasks["tok-1"] = [task_row("tok-1", "task_postprocess_end", pexit="0", totalcost="0.11",
                                    outputs=[{"name": "v.mp4", "url": "https://cdn1.wiro.ai/v.mp4"}])]
    done = call("wiro_task", {"task": "tok-1"})
    assert done["status"] == "completed" and done["files"][0]["kind"] == "video"
    assert Path(done["files"][0]["path"]).parent.name == "videos"
    assert len(wiro.runs) == 1  # waiting never starts a second run


def test_edit_image_uploads_a_file_from_the_media_cache(wiro, monkeypatch):
    monkeypatch.setattr(media_plugin.media, "local_terminal_backend", lambda: False)
    wiro.models["xinntao/real-esrgan"] = model("xinntao", "real-esrgan", [
        item("inputImage", "combinefileinput", True, "https://cdn.wiro.ai/sample.jpg"),
        item("modelName", "select", True, "RealESRGAN_x4plus", ["RealESRGAN_x4plus", "RealESRGAN_x2plus"]),
        item("outscale", "float", False, "2.0", minvalue="1", maxvalue="10"),
    ])
    photo = wiro.home / "cache" / "images" / "from-telegram.png"
    photo.write_bytes(PNG)
    finish(wiro, "tok-1", [])
    result = call("wiro_edit_image", {"image": str(photo), "operation": "upscale", "scale": 4})
    assert result["success"]
    [upload] = wiro.uploads
    assert re.fullmatch(r"from-telegram-[0-9a-f]{8}\.png", upload["name"])
    assert upload["data"] == PNG and upload["content_type"] == "image/png"
    assert wiro.runs[0][1] == {"inputImage": [upload["url"]], "outscale": 4.0, "modelName": "RealESRGAN_x4plus"}
    assert result["uploaded_inputs"] == [{"source": str(photo), "url": upload["url"]}]


def test_edit_needs_a_prompt_and_refuses_paths_outside_the_rules(wiro, monkeypatch, tmp_path):
    assert "prompt is required" in call("wiro_edit_image", {"image": "https://example.com/a.png"})["error"]
    monkeypatch.setattr(media_plugin.media, "local_terminal_backend", lambda: False)
    outside = tmp_path / "desk.png"
    outside.write_bytes(PNG)
    refused = call("wiro_edit_image", {"image": str(outside), "prompt": "make it blue"})
    assert refused["success"] is False and "sandboxed" in refused["error"]
    assert not wiro.runs and not wiro.uploads


def test_relative_paths_resolve_against_the_session_folder(wiro, monkeypatch, tmp_path):
    monkeypatch.setattr(media_plugin.media, "local_terminal_backend", lambda: True)
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "cat.png").write_bytes(PNG)

    class Ctx:
        def session_cwd(self):
            return str(tmp_path / "proj")

        def get_config(self, key, default=None):
            return default

    finish(wiro, "tok-1", [])
    result = call("wiro_edit_image", {"image": "cat.png", "prompt": "add a hat"}, tools.Runtime(Ctx()))
    assert result["success"] and wiro.uploads[0]["name"].startswith("cat-")


def test_transcribe_puts_the_link_in_the_url_field_and_returns_the_text(wiro, monkeypatch):
    monkeypatch.setattr(media_plugin.media, "local_terminal_backend", lambda: False)
    wiro.models["elevenlabs/speech-to-text"] = model("elevenlabs", "speech-to-text", [
        item("inputAudio", "fileinput", True, "https://cdn.wiro.ai/sample.mp3"), item("inputAudioUrl")])
    voice = wiro.home / "cache" / "images" / ".." / "voice.mp3"
    voice.write_bytes(MP3)
    wiro.cdn["https://cdn1.wiro.ai/t.txt"] = (b"hello from the voice note", "text/plain")
    finish(wiro, "tok-1", [{"name": "transcript.txt", "contenttype": "text/plain", "url": "https://cdn1.wiro.ai/t.txt"}])
    result = call("wiro_transcribe", {"audio": str(voice), "language": "en"})
    assert result["text"] == "hello from the voice note"
    assert set(wiro.runs[0][1]) == {"inputAudioUrl"}
    assert "media_tag" not in result  # a transcript is text, not an attachment
    assert any("no language setting" in note for note in result["notes"])


def test_debugoutput_is_the_answer_when_there_are_no_files(wiro):
    wiro.models["qwen/qwen3-asr-1-7b"] = model("qwen", "qwen3-asr-1-7b", [item("inputAudio", "combinefileinput", True)])
    wiro.tasks["tok-1"] = [task_row("tok-1", "task_postprocess_end", pexit="0", debugoutput="the words")]
    result = call("wiro_transcribe", {"audio": "https://example.com/a.mp3", "model": "qwen/qwen3-asr-1-7b"})
    assert result["text"] == "the words"
    assert wiro.runs[0][1] == {"inputAudio": ["https://example.com/a.mp3"]}


def test_speech_voice_must_exist(wiro):
    wiro.models["elevenlabs/eleven-v4-turbo"] = model("elevenlabs", "eleven-v4-turbo", [
        item("prompt", "textarea", True, "sample"), item("voice", "select", True, "alice", ["alice", "bill"]),
        item("outputFormat", "select", True, "mp3_44100_128", ["mp3_44100_128"])])
    bad = call("wiro_text_to_speech", {"text": "hi", "voice": "zed"})
    assert bad["success"] is False and "alice, bill" in bad["error"] and not wiro.runs
    wiro.cdn["https://cdn1.wiro.ai/s.mp3"] = (MP3, "audio/mpeg")
    finish(wiro, "tok-1", [{"name": "s.mp3", "url": "https://cdn1.wiro.ai/s.mp3"}])
    ok = call("wiro_text_to_speech", {"text": "hi", "voice": "Bill"})
    assert wiro.runs[0][1] == {"prompt": "hi", "voice": "bill", "outputFormat": "mp3_44100_128"}
    assert Path(ok["files"][0]["path"]).parent.name == "audio"


def test_music_with_lyrics_turns_vocals_on(wiro):
    wiro.models["elevenlabs/text-to-music-v2"] = model("elevenlabs", "text-to-music-v2", [
        item("prompt", "textarea", True), item("duration", "select", True, "30", ["10", "30", "60"]),
        item("instrumental", "select", False, "true", ["false", "true"])])
    finish(wiro, "tok-1", [])
    call("wiro_generate_music", {"prompt": "upbeat pop", "lyrics": "la la la", "duration_seconds": 12})
    assert wiro.runs[0][1] == {"prompt": "upbeat pop\n\nLyrics:\nla la la", "instrumental": "false", "duration": "10"}


def test_3d_saves_glb_in_its_own_folder(wiro):
    wiro.models["microsoft/trellis-2"] = model("microsoft", "trellis-2", [
        item("inputImage", "combinefileinput", True, "https://cdn.wiro.ai/s.webp")])
    wiro.cdn["https://cdn1.wiro.ai/m.glb"] = (GLB, "application/octet-stream")
    finish(wiro, "tok-1", [{"name": "model.glb", "url": "https://cdn1.wiro.ai/m.glb"}])
    result = call("wiro_generate_3d", {"image": "https://cdn1.wiro.ai/in.png"})
    assert result["files"][0]["kind"] == "3d" and Path(result["files"][0]["path"]).suffix == ".glb"
    assert Path(result["files"][0]["path"]).parent.name == "3d"


def test_outputs_off_wiro_hosts_are_not_downloaded(wiro):
    finish(wiro, "tok-1", [{"name": "x.png", "url": "https://elsewhere.example/x.png"}])
    result = call("wiro_generate_image", {"prompt": "x"})
    assert "path" not in result["files"][0] and "not on a Wiro AI host" in result["notes"][0]
    assert not [c for c in wiro.calls if c[0] == "GET"]


def test_run_model_takes_files_and_raw_params(wiro, monkeypatch):
    monkeypatch.setattr(media_plugin.media, "local_terminal_backend", lambda: True)
    wiro.models["some/lipsync"] = model("some", "lipsync", [
        item("inputVideo", "fileinput", True), item("inputVideoUrl"), item("inputAudio", "combinefileinput", True),
        item("syncMode", "select", True, "loop", ["loop", "cut"])])
    finish(wiro, "tok-1", [])
    result = call("wiro_run_model", {"model": "some/lipsync", "params": {"syncMode": "cut"},
                                     "files": {"inputVideo": "https://cdn1.wiro.ai/v.mp4",
                                               "inputAudio": ["https://cdn1.wiro.ai/a.mp3"]}})
    assert result["success"]
    assert wiro.runs[0][1] == {"inputVideoUrl": "https://cdn1.wiro.ai/v.mp4", "inputAudio": ["https://cdn1.wiro.ai/a.mp3"],
                               "syncMode": "cut"}
    bad = call("wiro_run_model", {"model": "some/lipsync", "files": {"inputImage": "https://x/y.png"}})
    assert "no file parameter inputImage" in bad["error"] and "inputVideo, inputAudio" in bad["error"]


def test_llm_answer_comes_back_as_text(wiro):
    wiro.models["openai/gpt-5-4-nano"] = model("openai", "gpt-5-4-nano", [item("prompt", "textarea", True)])
    wiro.tasks["tok-1"] = [task_row("tok-1", "task_postprocess_end", pexit="0", outputs=[
        {"contenttype": "raw", "content": {"answer": ["Hello", " there"]}}])]
    result = call("wiro_run_model", {"model": "openai/gpt-5-4-nano", "params": {"prompt": "hi"}})
    assert result["text"] == "Hello there" and "files" not in result


def test_search_and_model_info(wiro):
    wiro.list_rows = [dict(NANO, taskstat={"runcount": 27044}, categories=["partner", "text-to-image", "image-editing"])]
    found = call("wiro_search_models", {"query": "banana", "category": "text-to-image"})
    assert wiro.api_calls("/Tool/List")[0]["categories"] == ["text-to-image"]
    [row] = found["models"]
    assert row == {"model": "google/nano-banana-2-1", "title": "google nano-banana-2-1",
                   "categories": ["text-to-image", "image-editing"], "price": "$0.0336 to $0.0504 per run",
                   "description": "nano-banana-2-1 model", "runs": 27044,
                   "page": "https://wiro.ai/models/google/nano-banana-2-1"}
    info = call("wiro_model_info", {"model": "https://wiro.ai/models/google/nano-banana-2-1"})
    assert info["model"] == "google/nano-banana-2-1"
    assert [p["id"] for p in info["parameters"]] == ["inputImage", "prompt", "resolution", "aspectRatio", "outputFormat"]
    assert info["price_table"][0] == {"settings": {"resolution": "1K"}, "price_usd": 0.0336, "unit": "per run"}
    assert "error" in call("wiro_search_models", {})


def test_signature_projects_sign_every_request(wiro):
    wiro.credentials[0] = ("the-key", "the-secret")
    call("wiro_model_info", {"model": "google/nano-banana-2-1"})
    _path, _body, headers = wiro.calls[-1]
    assert headers["x-api-key"] == "the-key"
    expected = hmac.new(b"the-key", ("the-secret" + headers["x-nonce"]).encode(), hashlib.sha256).hexdigest()
    assert headers["x-signature"] == expected
    assert headers["user-agent"].startswith("wiro-hermes/")
    assert tools.split_credentials("k:s") == ("k", "s") and tools.split_credentials(" k ") == ("k", "")


def test_api_refusals_are_explained(wiro):
    wiro.handlers["/Run/google/nano-banana-2-1"] = lambda body: {
        "result": False, "errors": [{"code": 97, "message": "Insufficient balance"}]}
    result = call("wiro_generate_image", {"prompt": "x"})
    assert result["success"] is False
    assert "Insufficient balance" in result["error"] and "wiro.ai/panel/wallet/deposit/credit-card" in result["error"]
    unknown = call("wiro_model_info", {"model": "no/such-model"})
    assert "has no model no/such-model" in unknown["error"]
    assert "is not a Wiro AI model ID" in call("wiro_model_info", {"model": "nonsense"})["error"]


def test_task_list_status_and_cancel(wiro):
    wiro.task_list = [task_row("tok-9", "task_postprocess_end", taskid="9", pexit="0", totalcost="0.05",
                               chargedcost="0.05")]
    listed = call("wiro_task", {"action": "list"})
    assert listed["tasks"][0]["result"] == "completed" and listed["tasks"][0]["cost"] == "$0.05 charged"
    wiro.tasks["tok-5"] = [task_row("tok-5", "task_queue", taskid="5"), task_row("tok-5", "task_cancel", taskid="5")]
    stopped = call("wiro_task", {"action": "cancel", "task": "tok-5"})
    assert wiro.stopped == [("/Task/Cancel", {"taskid": "5"})] and stopped["status"] == "task_cancel"
    wiro.tasks["tok-6"] = [task_row("tok-6", "task_start", taskid="6")]
    call("wiro_task", {"action": "cancel", "task": "6"})
    assert wiro.stopped[-1] == ("/Task/Kill", {"taskid": "6"})
    status = call("wiro_task", {"action": "status", "task": "tok-9x"})
    assert status["success"] is False and "No task" in status["error"]


def test_workflow_run_resolves_and_saves_outputs(wiro, monkeypatch):
    contents = {
        "schema": {"input": {"prompt": {"label": "Prompt", "type": "string", "defaultValue": "a cat"},
                             "openai_api_key": {"label": "Key", "type": "string", "defaultValue": "sk-" + "a" * 30}}},
        "metadata": {"input": {"values": {"prompt": "a dog"}}},
        "nodes": {"output": {"fields": {"image": "$node-1.outputs", "caption": "$node-2.text", "echo": "$input.prompt"}}},
    }
    wiro.workflows["poster-abc12"] = {"slug": "poster", "slugid": "abc12", "title": "Poster", "contents": json.dumps(contents)}
    got = call("wiro_workflows", {"action": "get", "workflow": "https://wiro.ai/workflows/poster-abc12"})
    assert got["workflow"] == "poster-abc12" and got["outputs"] == ["caption", "echo", "image"]
    keys = {row["key"]: row for row in got["inputs"]}
    assert keys["prompt"]["saved_value"] == "a dog" and keys["openai_api_key"]["default"] == "[hidden]"
    wiro.cdn["https://cdn1.wiro.ai/w.png"] = (PNG, "image/png")
    wiro.workflow_runs["wf-1"] = [
        {"workflowrun": {"workflowtoken": "wf-1", "status": "running"}, "workflowrunnodes": []},
        {"workflowrun": {"workflowtoken": "wf-1", "workflowslug": "poster-abc12", "status": "completed",
                         "contents": contents, "chargedcost": "0.07", "elapsedseconds": "21"},
         "workflowrunnodes": [
             {"nodeId": "node-1", "status": "completed", "outputs": [{"url": "https://cdn1.wiro.ai/w.png", "name": "w.png"}]},
             {"nodeId": "node-2", "status": "completed", "outputs": {"text": "A dog in a hat"}}]},
    ]
    done = call("wiro_run_workflow", {"workflow": "poster-abc12", "inputs": {"prompt": "a dog"}})
    assert wiro.workflow_started == [("poster-abc12", {"prompt": "a dog"})]
    assert done["success"] and done["cost"] == "$0.07 charged"
    assert done["outputs"]["caption"] == {"text": "A dog in a hat"}
    assert done["outputs"]["echo"] == {"text": "a dog"}
    assert Path(done["outputs"]["image"]["files"][0]["path"]).read_bytes() == PNG
    assert "[hidden]" in call("wiro_run_workflow", {"workflow": "poster-abc12",
                                                    "inputs": {"openai_api_key": "[hidden]"}})["error"]
    with pytest.raises(ValueError):
        media_plugin.workflows.parse_slug("poster")


def test_a_handler_never_raises(wiro, monkeypatch):
    monkeypatch.setattr(tools.Runtime, "client", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    result = call("wiro_search_models", {"query": "x"})
    assert result == {"success": False, "error": "wiro_search_models failed: RuntimeError: boom"}


def test_a_bad_key_is_not_reported_as_a_missing_task(wiro):
    wiro.handlers["/Task/Detail"] = lambda body: {
        "result": False, "errors": [{"code": 0, "message": "Project authorization is not founded."}]}
    result = call("wiro_task", {"action": "status", "task": "tok-1"})
    assert "authorization" in result["error"] and "WIRO_API_KEY" in result["error"]
    assert "No task" not in result["error"]


def test_the_same_file_is_uploaded_once_under_a_unique_name(wiro, monkeypatch):
    monkeypatch.setattr(media_plugin.media, "local_terminal_backend", lambda: False)
    photo = wiro.home / "cache" / "images" / "photo.png"
    photo.write_bytes(PNG)
    wiro.next_task = ["tok-1", "tok-2"]
    finish(wiro, "tok-1", [])
    finish(wiro, "tok-2", [])
    first = call("wiro_edit_image", {"image": str(photo), "prompt": "make it blue"})
    second = call("wiro_generate_video", {"prompt": "spin", "image": str(photo), "model": "google/nano-banana-2-1"})
    assert first["success"] and second["success"]
    assert len(wiro.uploads) == 1 and re.fullmatch(r"photo-[0-9a-f]{8}\.png", wiro.uploads[0]["name"])
    assert wiro.runs[0][1]["inputImage"] == wiro.runs[1][1]["inputImage"] == [wiro.uploads[0]["url"]]
    copy = wiro.home / "cache" / "images" / "copy.png"
    copy.write_bytes(PNG + b"other")
    call("wiro_edit_image", {"image": str(copy), "prompt": "x"})
    assert len(wiro.uploads) == 2  # other bytes, a second upload


def test_a_missing_task_is_not_polled_again(wiro):
    result = call("wiro_task", {"task": "gone-token", "wait_seconds": 60})
    assert "No task" in result["error"]
    assert len(wiro.api_calls("/Task/Detail")) == 1
