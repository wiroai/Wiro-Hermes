"""The media plugin inside a real Hermes: installed into a temporary HERMES_HOME, enabled, discovered.

Each check runs in a fresh interpreter, the state ``hermes plugins install wiro-media`` plus
``hermes plugins enable wiro-media`` leaves. Network calls go to the in-memory Wiro AI API from
fake_wiro.py, so nothing here talks to Wiro AI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import providers
from conftest import MEDIA_DIR

HERMES_ROOT = Path(providers.__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
PLACEHOLDER_KEY = "wiro-test-not-a-real-key"
CONFIG = """
plugins:
  enabled: [wiro-media]
image_gen:
  provider: wiro
  model: google/nano-banana-2-1
video_gen:
  provider: wiro
tts:
  provider: wiro
  voice: bill
stt:
  enabled: true
  provider: wiro
"""

FAKE = r"""
import json, sys
from fake_wiro import FakeClock, FakeWiro, MP3, MP4, PNG, item, model, task_row
from hermes_cli.plugins import discover_plugins
discover_plugins()
plugin = next(m for name, m in sorted(sys.modules.items())
              if name.startswith("hermes_plugins.") and hasattr(m, "HANDLERS") and "wiro" in name)
tools_mod = sys.modules[plugin.__name__ + ".tools"]
client_mod = sys.modules[plugin.__name__ + ".client"]
fake, clock = FakeWiro(), FakeClock()
tools_mod.Runtime.client = lambda self: client_mod.WiroClient("k", "", opener=fake, sleep=clock.sleep, clock=clock)

def done(token, url, data, ctype, name):
    fake.cdn[url] = (data, ctype)
    fake.tasks[token] = [task_row(token, "task_postprocess_end", pexit="0", totalcost="0.05", chargedcost="0.05",
                                  outputs=[{"name": name, "contenttype": ctype, "url": url}])]
"""


def _run(tmp_path: Path, code: str) -> dict:
    home = tmp_path / "hermes-home"
    shutil.copytree(MEDIA_DIR, home / "plugins" / "wiro-media", ignore=shutil.ignore_patterns("__pycache__"))
    (home / ".env").write_text(f"WIRO_API_KEY={PLACEHOLDER_KEY}\n", encoding="utf-8")
    (home / "config.yaml").write_text(CONFIG, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WIRO_", "HERMES_", "OPENAI", "OPENROUTER", "FAL_"))}
    env.update(HERMES_HOME=str(home), PYTHONPATH=os.pathsep.join([str(HERMES_ROOT), str(TESTS_DIR)]), NO_COLOR="1")
    bootstrap = "from hermes_cli.env_loader import load_hermes_dotenv; load_hermes_dotenv()\n"
    proc = subprocess.run(
        [sys.executable, "-c", bootstrap + code],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=240, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_hermes_loads_the_tools_skill_and_backends(tmp_path):
    found = _run(tmp_path, """
import json
from hermes_cli.plugins import discover_plugins
discover_plugins()
from tools.registry import registry
names = sorted(registry.get_tool_names_for_toolset("wiro"))
entries = [registry.get_entry(n) for n in names]
from tools.skills_tool import skill_view
skill = json.loads(skill_view("wiro-media:wiro"))
from agent import image_gen_registry, video_gen_registry, tts_registry, transcription_registry
picks = [getattr(r.get_provider("wiro"), "display_name", None)
         for r in (image_gen_registry, video_gen_registry, tts_registry, transcription_registry)]
print(json.dumps({"names": names, "available": [bool(e.check_fn()) for e in entries],
                  "skill": [skill.get("success"), "Wiro AI media" in json.dumps(skill)],
                  "backends": picks, "active_image": getattr(image_gen_registry.get_active_provider(), "name", None)}))
""")
    assert found["names"] == sorted([
        "wiro_generate_image", "wiro_edit_image", "wiro_generate_video", "wiro_text_to_speech", "wiro_generate_music",
        "wiro_transcribe", "wiro_generate_3d", "wiro_search_models", "wiro_model_info", "wiro_run_model", "wiro_task",
        "wiro_workflows", "wiro_run_workflow"])
    assert all(found["available"])
    assert found["skill"] == [True, True]
    assert found["backends"] == ["Wiro AI"] * 4
    assert found["active_image"] == "wiro"


def test_image_generate_runs_on_wiro_and_lands_in_the_hermes_cache(tmp_path):
    found = _run(tmp_path, FAKE + """
fake.models["google/nano-banana-2-1"] = model("google", "nano-banana-2-1", [
    item("inputImage", "combinefileinput"), item("prompt", "textarea", True),
    item("resolution", "select", True, "1K", ["1K", "2K"]),
    item("aspectRatio", "select", False, "auto", ["auto", "1:1", "3:2", "2:3", "16:9", "9:16"])])
done("tok-1", "https://cdn1.wiro.ai/i.png", PNG, "image/png", "i.png")
from tools.image_generation_tool import _handle_image_generate
result = json.loads(_handle_image_generate({"prompt": "a lighthouse", "aspect_ratio": "portrait"}, task_id="t"))
print(json.dumps({"result": result, "run": fake.runs[0]}))
""")
    result = found["result"]
    assert result["success"] is True and result["provider"] == "wiro" and result["model"] == "google/nano-banana-2-1"
    assert "/cache/generated/images/wiro_" in result["image"] and result["cost"] == "$0.05 charged"
    assert found["run"] == ["google/nano-banana-2-1", {"prompt": "a lighthouse", "aspectRatio": "9:16", "resolution": "1K"}]


def test_text_to_speech_and_voice_transcription_run_on_wiro(tmp_path):
    found = _run(tmp_path, FAKE + """
fake.models["elevenlabs/eleven-v4-turbo"] = model("elevenlabs", "eleven-v4-turbo", [
    item("prompt", "textarea", True), item("voice", "select", True, "alice", ["alice", "bill"])])
fake.models["elevenlabs/speech-to-text"] = model("elevenlabs", "speech-to-text", [
    item("inputAudio", "fileinput", True), item("inputAudioUrl")])
fake.next_task = ["tts", "stt"]
done("tts", "https://cdn1.wiro.ai/s.mp3", MP3, "audio/mpeg", "speech.mp3")
fake.tasks["stt"] = [task_row("stt", "task_postprocess_end", pexit="0", debugoutput="hello from wiro")]
from tools.tts_tool import text_to_speech_tool
speech = json.loads(text_to_speech_tool("Hello there, this is a test."))
from tools.transcription_tools import transcribe_audio
import os, tempfile
folder = tempfile.mkdtemp()
clip = os.path.join(folder, "note.mp3")
open(clip, "wb").write(MP3 * 20)
heard = transcribe_audio(clip)
print(json.dumps({"speech": speech, "heard": heard, "runs": fake.runs, "uploads": [u["name"] for u in fake.uploads]}))
""")
    speech, heard = found["speech"], found["heard"]
    assert speech["success"] is True and speech["provider"] == "wiro"
    assert speech["file_path"].endswith(".mp3") and "MEDIA:" in speech["media_tag"]
    assert found["runs"][0] == ["elevenlabs/eleven-v4-turbo", {"prompt": "Hello there, this is a test.", "voice": "bill"}]
    assert heard["success"] is True and heard["transcript"] == "hello from wiro"
    assert found["runs"][1][0] == "elevenlabs/speech-to-text" and set(found["runs"][1][1]) == {"inputAudioUrl"}
    assert found["uploads"][0].startswith("note-") and found["uploads"][0].endswith(".mp3")


def test_video_generate_runs_on_wiro(tmp_path):
    found = _run(tmp_path, FAKE + """
fake.models["bytedance/seedance-v1-pro-fast"] = model("bytedance", "seedance-v1-pro-fast", [
    item("inputImage", "combinefileinput"), item("prompt", "textarea"),
    item("resolution", "select", True, "480p", ["480p", "720p", "1080p"]),
    item("ratio", "select", True, "adaptive", ["adaptive", "16:9", "9:16", "1:1"]),
    item("duration", "select", True, "5", ["5", "10"])])
done("tok-1", "https://cdn1.wiro.ai/v.mp4", MP4, "video/mp4", "v.mp4")
from tools.video_generation_tool import _handle_video_generate
result = json.loads(_handle_video_generate({"prompt": "waves at dusk", "duration": 10, "aspect_ratio": "9:16"}))
print(json.dumps({"result": result, "run": fake.runs[0]}))
""")
    result = found["result"]
    assert result["success"] is True and result["provider"] == "wiro"
    assert "/cache/generated/videos/wiro_" in result["video"]
    assert found["run"][1] == {"prompt": "waves at dusk", "duration": "10", "ratio": "9:16", "resolution": "720p"}
