"""The media plugin's pure parts: parameter mapping, prices, and the file rules."""

from __future__ import annotations

import base64
import json
import re

import pytest
from conftest import MEDIA_DIR, media_plugin
from fake_wiro import GLB, JPEG, MP3, MP4, OGG, PNG, item

P = media_plugin.params
pricing = media_plugin.pricing
media = media_plugin.media


# -- parameters ---------------------------------------------------------------------------------


def test_files_go_where_each_field_type_takes_them():
    items = [item("inputImage", "combinefileinput"), item("inputAudio", "fileinput"), item("inputAudioUrl"),
             item("inputVideo", "fileinput"), item("inputDocumentMultiple", "multifileinput")]
    params: dict = {}
    P.put_files(items[0], ["u1", "u2"], params, items)
    P.put_files(items[1], ["a1"], params, items)
    P.put_files(items[3], ["v1"], params, items)
    P.put_files(items[4], ["d1", "d2"], params, items)
    assert params == {"inputImage": ["u1", "u2"], "inputAudioUrl": "a1", "inputVideo": "v1",
                      "inputDocumentUrlMultiple": "d1,d2"}
    with pytest.raises(ValueError):
        P.put_files(items[1], ["a1", "a2"], {}, items)


def test_fill_required_uses_catalog_values_but_never_samples():
    items = [
        item("prompt", "textarea", True, "a sample prompt"),
        item("resolution", "select", True, "1k", ["1k", "2k"]),
        item("samples", "number", True, "1"),
        item("ratio", "select", True, "1:1", ["1:1", "16:9"]),
        item("inputImage", "combinefileinput", False, "https://cdn.wiro.ai/sample.jpg"),
        item("watermark", "select", True, "FALSE", ["false", "true"]),
    ]
    params, added = P.fill_required(items, {"prompt": "a fox"})
    assert params == {"prompt": "a fox", "resolution": "1k", "samples": "1", "ratio": "1:1", "watermark": "false"}
    assert "inputImage" not in params and set(added) == {"resolution", "samples", "ratio", "watermark"}
    with pytest.raises(ValueError, match="prompt"):
        P.fill_required(items, {})
    required_file = [item("inputImage", "combinefileinput", True, "https://cdn.wiro.ai/sample.jpg")]
    with pytest.raises(ValueError, match="inputImage"):
        P.fill_required(required_file, {})
    assert P.fill_required(required_file, {"inputImage": ["u"]})[0] == {"inputImage": ["u"]}
    companion = [item("inputAudio", "fileinput", True), item("inputAudioUrl")]
    assert P.fill_required(companion, {"inputAudioUrl": "u"})[0] == {"inputAudioUrl": "u"}


def test_ratio_matching():
    ratio = item("aspectRatio", "select", False, "auto", ["auto", "1:1", "3:2", "16:9", "9:16"])
    assert P.match_ratio(ratio, "16:9") == "16:9"
    assert P.match_ratio(ratio, "landscape") == "16:9"
    assert P.match_ratio(ratio, "portrait") == "9:16"
    assert P.match_ratio(ratio, "1.78:1") == "16:9"  # nearest within 15%
    assert P.match_ratio(ratio, "21:9") is None  # too far from anything offered
    assert P.match_ratio(item("size", "select", options=["1K", "2K"]), "16:9") is None


def test_number_matching_snaps_to_options_and_clamps_to_ranges():
    duration = item("duration", "select", True, "5", ["5", "10"])
    assert P.match_number(duration, 7) == "5"
    assert P.match_number(duration, 9) == "10"
    seconds = item("duration", "number", True, "5", minvalue="4", maxvalue="15")
    assert P.match_number(seconds, 30) == 15
    assert P.match_number(seconds, 6) == 6
    assert P.match_number(item("scale", "float", minvalue="1", maxvalue="10"), 4) == 4.0
    assert P.match_number(seconds, "x") is None


def test_flags_and_options():
    assert P.flag_value(item("instrumental", "select", options=["false", "true"]), True) == "true"
    assert P.flag_value(item("faceEnhance", "checkbox"), False) is False
    assert P.flag_value(item("audioSwitch", "select", options=["False", "True"]), False) == "False"
    voice = item("voice", "select", True, "alice", [{"value": "alice", "text": "Alice"}, {"value": "bill", "text": "Bill"}])
    assert P.match_option(voice, "BILL") == "bill"
    assert P.match_option(voice, "Alice") == "alice"
    assert P.match_option(voice, "nobody") is None
    assert P.match_option(item("seed"), 7) == "7"


def test_finders_pick_the_main_inputs():
    items = [item("inputImageMask", "combinefileinput"), item("inputImageLast", "combinefileinput"),
             item("startImage", "fileinput"), item("text", "textarea", True), item("inputAudio", "fileinput")]
    assert P.image_param(items)["id"] == "startImage"
    assert P.last_image_param(items)["id"] == "inputImageLast"
    assert P.text_param(items)["id"] == "text"
    assert P.media_param(items, "audio")["id"] == "inputAudio"
    assert P.media_param(items, "video") is None
    assert P.find(items, "TEXT")["id"] == "text"


def test_describe_item_hides_samples_and_shows_how_files_are_passed():
    prompt = P.describe_item(item("prompt", "textarea", True, "a sample"))
    assert "default" not in prompt and prompt["example"] == "a sample"
    image = P.describe_item(item("inputImage", "combinefileinput", True, "https://cdn.wiro.ai/s.jpg"))
    assert "default" not in image and "example" not in image and "list" in image["takes"]
    res = P.describe_item(item("resolution", "select", True, "1K", ["1K", "2K"]))
    assert res["default"] == "1K" and res["options"] == ["1K", "2K"]


# -- prices -------------------------------------------------------------------------------------


def rows(*entries):
    return {"dynamicprice": json.dumps(list(entries))}


def test_price_summary_forms():
    assert pricing.price_summary(rows({"price": 0.0336, "priceMethod": "cpr", "inputs": {"resolution": "1K"}},
                                      {"price": 0.113, "priceMethod": "cpr", "inputs": {"resolution": "4K"}})) \
        == "$0.0336 to $0.113 per run"
    assert pricing.price_summary(rows({"price": 0.04, "priceMethod": "cpo"})) == "$0.04 per output"
    assert pricing.price_summary(rows({"price": 4e-05, "priceMethod": "cp-promptlength"})) \
        == "$0.00004 per prompt character"
    metered = pricing.price_summary(rows({"price": 0, "priceMethod": "cp-readoutput", "note": "Input $1 per 1M tokens."}))
    assert metered == "set by the run when it ends: Input $1 per 1M tokens."
    assert pricing.price_summary(rows({"price": 0.1, "priceMethod": "cp-realtimeturn", "note": "rates"})) \
        == "billed turn by turn in a live session: rates"
    assert pricing.price_summary({"approximatelycost": "0.0121", "dynamicprice": ""}) \
        == "about $0.0121 per run (billed for run time)"
    assert pricing.price_summary({"cps": "0", "quegroupcps": "0.0006"}) == "$0.0006 per second of run time"
    assert pricing.price_summary(rows({"price": 1, "priceMethod": "weird"})).startswith("no fixed price")


def test_expected_price_matches_the_row_for_the_settings():
    detail = rows({"price": 0.05, "priceMethod": "cpr", "inputs": {"duration": "5", "resolution": "480p"}},
                  {"price": 0.11, "priceMethod": "cpr", "inputs": {"duration": "5", "resolution": "720p"}},
                  {"price": 0.03, "priceMethod": "cpo", "inputs": {}})
    assert pricing.expected_price(detail, {"duration": "5", "resolution": "720p"}) == 0.11
    assert pricing.expected_price(detail, {"duration": "10"}, outputs=2) == 0.06  # per-output row
    assert pricing.expected_price(rows({"price": 0, "priceMethod": "cp-readoutput"}), {}) is None
    quantity = rows({"price": 0.02, "priceMethod": "cpr", "inputs": {"inputImage": "QUANTITY:0"}},
                    {"price": 0.05, "priceMethod": "cpr", "inputs": {"inputImage": "QUANTITY:1"}})
    assert pricing.expected_price(quantity, {"inputImage": ["u"]}) == 0.05


def test_task_cost_reads_the_charge_first():
    assert pricing.task_cost({"status": "task_postprocess_end", "totalcost": "0.0336", "chargedcost": "0.0336"}) \
        == (0.0336, "$0.0336 charged")
    assert pricing.task_cost({"status": "task_postprocess_end", "totalcost": "0.05", "chargedcost": "0.04"})[1] \
        == "$0.04 charged (list price $0.05)"
    assert pricing.task_cost({"status": "task_postprocess_end", "totalcost": "0.05"})[1].startswith("$0.05 list price")
    assert pricing.task_cost({"status": "task_cancel", "accepttime": "0"}) == (0.0, "$0 (cancelled while queued, not charged)")
    assert pricing.task_cost({"status": "task_cancel", "accepttime": "1731300000"})[0] is None
    assert pricing.money(0.00004) == "$0.00004"


# -- files --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("data", "name", "kinds"), [
    (PNG, "a.png", {"image"}), (JPEG, "a.jpg", {"image"}), (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "a.webp", {"image"}),
    (MP3, "a.mp3", {"audio"}), (b"RIFF\x00\x00\x00\x00WAVEfmt ", "a.wav", {"audio"}), (OGG, "voice.ogg", {"audio"}),
    (MP4, "a.mp4", {"video"}), (b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 8, "a.m4a", {"audio"}),
    (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8, "a.heic", {"image"}), (GLB, "a.glb", {"3d"}),
    (b"%PDF-1.7", "a.pdf", {"document"}), (b"WIRO_API_KEY=abc\n", ".env", set()), (b"PK\x03\x04", "a.zip", set()),
])
def test_sniff(data, name, kinds):
    assert media.sniff(data, name) == kinds


@pytest.fixture
def home(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "cache" / "images").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


def test_local_files_from_the_media_cache_are_read(home, monkeypatch):
    monkeypatch.setattr(media, "local_terminal_backend", lambda: False)  # even with a sandbox
    photo = home / "cache" / "images" / "photo.png"
    photo.write_bytes(PNG)
    blob = media.read_local(str(photo), ["image"])
    assert blob.kind == "image" and blob.mime == "image/png" and blob.name == "photo.png"


def test_hermes_home_outside_the_caches_is_refused(home):
    secret = home / ".env"
    secret.write_bytes(PNG)  # even a real image there is refused by location
    with pytest.raises(media.InputRefused, match="Hermes home"):
        media.read_local(str(secret), ["image"])


def test_sandboxed_backend_refuses_other_paths(home, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "local_terminal_backend", lambda: False)
    photo = tmp_path / "photo.png"
    photo.write_bytes(PNG)
    with pytest.raises(media.InputRefused, match="sandboxed"):
        media.read_local(str(photo), ["image"])


def test_local_backend_reads_workspace_files_but_checks_their_bytes(home, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "local_terminal_backend", lambda: True)
    photo = tmp_path / "work" / "photo.png"
    photo.parent.mkdir()
    photo.write_bytes(PNG)
    assert media.read_local(str(photo), ["image"]).kind == "image"
    notes = tmp_path / "work" / "notes.png"
    notes.write_bytes(b"WIRO_API_KEY=abc")
    with pytest.raises(media.InputRefused, match="not an image"):
        media.read_local(str(notes), ["image"])
    with pytest.raises(media.InputRefused, match="not an audio"):
        media.read_local(str(photo), ["audio"])
    with pytest.raises(media.InputRefused, match="No file"):
        media.read_local(str(tmp_path / "work" / "missing.png"), ["image"])


def test_credential_folders_are_refused(home, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "local_terminal_backend", lambda: True)
    monkeypatch.setenv("HOME", str(tmp_path))
    key_dir = tmp_path / ".ssh"
    key_dir.mkdir()
    (key_dir / "id.png").write_bytes(PNG)
    with pytest.raises(media.InputRefused, match=".ssh"):
        media.read_local(str(key_dir / "id.png"), ["image"])
    with pytest.raises(media.InputRefused, match="system folder"):
        media.read_local("/etc/hosts", ["image"])


def test_trusted_paths_skip_the_location_rules_but_not_the_bytes(home, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "local_terminal_backend", lambda: False)
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(MP3)
    assert media.read_local(str(voice), ["audio", "video"], trusted=True).kind == "audio"
    with pytest.raises(media.InputRefused):
        media.read_local(str(voice), ["image"], trusted=True)


def test_mislabelled_files_get_their_real_extension(home):
    blob = media.read_local(str(_write(home / "cache" / "images" / "photo.jpg", PNG)), ["image"])
    assert blob.name == "photo.png" and blob.mime == "image/png"
    clip = media.read_local(str(_write(home / "cache" / "clip.mov", MP4)), ["video"])
    assert clip.name == "clip.mp4" and clip.mime == "video/mp4"


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _ffmpeg(*args):
    import shutil
    import subprocess

    tool = shutil.which("ffmpeg")
    if tool is None:
        pytest.skip("ffmpeg is not installed")
    done = subprocess.run([tool, "-nostdin", "-loglevel", "error", "-y", *args], capture_output=True, check=False)
    if done.returncode != 0:
        pytest.skip(f"this ffmpeg cannot make the test file: {done.stderr[-200:]!r}")


def test_voice_notes_in_ogg_are_converted_to_m4a(home):
    note = home / "cache" / "audio" / "voice.ogg"
    note.parent.mkdir(parents=True)
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "libopus", str(note))
    blob = media.read_local(str(note), ["audio", "video"])
    assert blob.name == "voice.m4a" and blob.mime == "audio/mp4" and blob.data[4:8] == b"ftyp"


def test_a_bmp_image_is_converted_to_png(home):
    picture = home / "cache" / "images" / "scan.bmp"
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", str(picture))
    blob = media.read_local(str(picture), ["image"])
    assert blob.name == "scan.png" and blob.data.startswith(b"\x89PNG")


def test_without_ffmpeg_unsupported_audio_is_refused_with_a_hint(home, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    note = _write(home / "cache" / "audio" / "voice.ogg", OGG)
    with pytest.raises(media.InputRefused, match="Install ffmpeg"):
        media.read_local(str(note), ["audio"])
    with pytest.raises(media.InputRefused, match="does not take .glb"):
        media.read_local(str(_write(home / "cache" / "m.glb", GLB)), ["3d"])


def test_data_urls(home):
    blob = media.read_data_url("data:image/png;base64," + base64.b64encode(PNG).decode(), ["image"])
    assert blob.kind == "image" and blob.name == "input.png"
    with pytest.raises(media.InputRefused):
        media.read_data_url("data:text/plain,hello", ["image"])


def test_outputs_land_in_hermes_generated_cache(home):
    path = media.save_output(PNG, "image", "png")
    assert path.parent == home / "cache" / "generated" / "images"
    assert re.fullmatch(r"wiro_\d{8}_\d{6}_[0-9a-f]{8}\.png", path.name)
    assert path.read_bytes() == PNG
    assert media.save_output(GLB, "3d", "glb").parent.name == "3d"
    assert media.save_output(b"x", "audio", "mp3").parent.name == "audio"
    assert media.media_tag(path) == f"MEDIA:{path}"


@pytest.mark.parametrize(("name", "url", "ctype", "ext", "kind"), [
    ("0.png", "https://cdn1.wiro.ai/x/0.png", "image/png", "png", "image"),
    ("", "https://cdn1.wiro.ai/x/out.jpeg", "", "jpg", "image"),
    ("model.glb", "https://cdn1.wiro.ai/x/model.glb", "application/octet-stream", "glb", "3d"),
    ("speech.mp3", "https://cdn1.wiro.ai/x/speech.mp3", "audio/mpeg", "mp3", "audio"),
    ("out", "https://cdn1.wiro.ai/x/out", "video/mp4", "mp4", "video"),
    ("transcript.json", "https://cdn1.wiro.ai/x/t.json", "application/json", "json", "text"),
])
def test_output_extension_and_kind(name, url, ctype, ext, kind):
    found = media.output_extension(name, url, ctype)
    assert found == ext
    assert media.output_kind(found, ctype) == kind


def test_manifest_lists_every_tool_and_the_version_matches():
    manifest = (MEDIA_DIR / "plugin.yaml").read_text(encoding="utf-8")
    declared = re.findall(r"^  - (wiro_\w+)$", manifest, re.MULTILINE)
    assert declared == [schema["name"] for schema in media_plugin.schemas.ALL]
    assert f"version: {media_plugin.client.VERSION}" in manifest
    assert f"version: {media_plugin.client.VERSION}" in (MEDIA_DIR / "skills" / "wiro" / "SKILL.md").read_text()
    for key, model in media_plugin.catalog.DEFAULT_MODELS.items():
        assert f"  {key}:\n    type: str\n    default: {model}\n" in manifest


def test_schemas_are_plain_json_schema():
    for schema in media_plugin.schemas.ALL:
        assert schema["name"].startswith("wiro_")
        assert schema["parameters"]["type"] == "object"
        text = json.dumps(schema)
        assert "anyOf" not in text and "\u2014" not in text  # portable schemas, no em dash
        for name in schema["parameters"].get("required", []):
            assert name in schema["parameters"]["properties"]
