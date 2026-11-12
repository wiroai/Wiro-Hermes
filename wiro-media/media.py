"""Input files for Wiro AI runs and the files a run returns, in Hermes' own media layout.

Inputs: an http(s) URL goes to Wiro AI as it is (Wiro AI fetches public URLs itself). A local
path or a ``data:`` URL is uploaded to the project's Wiro AI storage first. Local files are read
only when they are media of the kind the argument takes (checked by their bytes, not their
name), and only from these places:

* Hermes' media caches under ``$HERMES_HOME`` (``cache/``, ``images/`` and the legacy
  ``*_cache`` folders), where files sent in a chat and earlier results land;
* anywhere else the user can read, when the profile's terminal backend is ``local``, except
  system folders and credential folders (``~/.ssh``, ``~/.aws``, ``~/.config`` ...).

With a sandboxed terminal backend (Docker, SSH, Modal ...), paths outside the media caches are
refused, because the agent's paths then name files inside the sandbox.

Wiro AI takes jpg, png, gif, webp and heic images, mp3, wav, m4a and webm audio, and mp4, webm and
mov video. Audio in another format (a Telegram or WhatsApp voice note is Ogg Opus) is converted
to m4a, and a bmp, tiff or avif image to png, with the local ffmpeg when it is installed.

Outputs are saved under ``$HERMES_HOME/cache/generated/<kind>/`` as
``wiro_<YYYYMMDD_HHMMSS>_<uuid8>.<ext>``, the folder and naming Hermes' own image and video
providers use, so the gateway delivers them as ``MEDIA:<path>`` attachments.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

MAX_INPUT_BYTES = 100 * 1024 * 1024  # Wiro AI's upload limit
MEDIA_CACHE_DIRS = ("cache", "images", "image_cache", "audio_cache", "video_cache", "document_cache",
                    "browser_screenshots")
DENIED_PREFIXES = ("/etc", "/proc", "/sys", "/dev", "/root", "/boot", "/var/log", "/var/lib", "/var/run",
                   "/private/etc", "/private/var/db", "/private/var/root")
DENIED_HOME_PARTS = (".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config", ".azure", ".gcloud",
                     ".netrc", ".pgpass", ".password-store", "Library/Keychains", "Library/Cookies")
KIND_DIRS = {"image": "images", "video": "videos", "audio": "audio", "3d": "3d", "text": "files", "file": "files"}
TEXT_EXTENSIONS = frozenset({"txt", "srt", "vtt", "json", "md", "csv", "tsv", "lrc"})
MODEL_EXTENSIONS = frozenset({"glb", "gltf", "obj", "ply", "stl", "fbx", "usdz", "3mf"})
OFFICE_EXTENSIONS = frozenset({"docx", "xlsx", "pptx", "epub"})
EXTRA_MIME = {
    "glb": "model/gltf-binary", "gltf": "model/gltf+json", "obj": "model/obj", "ply": "application/ply",
    "stl": "model/stl", "fbx": "application/octet-stream", "usdz": "model/vnd.usdz+zip",
    "srt": "application/x-subrip", "vtt": "text/vtt", "opus": "audio/ogg", "m4a": "audio/mp4",
    "webp": "image/webp", "heic": "image/heic", "avif": "image/avif",
}
CONTENT_TYPE_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp", "image/gif": "gif",
    "image/bmp": "bmp", "image/tiff": "tiff", "image/heic": "heic", "image/avif": "avif",
    "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "video/x-matroska": "mkv",
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
    "audio/ogg": "ogg", "audio/opus": "opus", "audio/flac": "flac", "audio/mp4": "m4a", "audio/aac": "aac",
    "audio/webm": "webm", "model/gltf-binary": "glb", "model/gltf+json": "gltf", "application/json": "json",
    "text/plain": "txt", "text/vtt": "vtt", "application/x-subrip": "srt", "application/pdf": "pdf",
}
# What Wiro AI's storage (File/Upload) and its media models take, by kind. Audio and images in
# other formats are converted with the local ffmpeg, when there is one, before the upload.
UPLOAD_EXTENSIONS = {
    "image": frozenset({"jpg", "png", "gif", "webp", "heic"}),
    "audio": frozenset({"mp3", "wav", "m4a", "webm"}),
    "video": frozenset({"mp4", "webm", "mov"}),
    "document": frozenset({"pdf", "docx", "doc", "pptx", "ppt", "xlsx", "xls", "epub"}),
    "3d": frozenset(),
}
CONVERT_TO = {"audio": "m4a", "image": "png"}
CONVERT_TIMEOUT = 180
_SAFE_EXT = re.compile(r"^[a-z0-9]{1,8}$")
_FTYP_IMAGE = {b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}
_FTYP_AUDIO = {b"M4A ", b"M4B ", b"M4P ", b"F4A "}


class InputRefused(ValueError):
    """A file argument could not be used. The message is safe to show to the user."""


@dataclass
class Blob:
    data: bytes
    name: str
    mime: str
    kind: str


def sniff(data: bytes, name: str = "") -> set[str]:
    """The media kinds ``data`` can be, from its first bytes (and the name for ambiguous containers)."""
    head = data[:64]
    ext = _ext_of(name)
    if head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff") or head[:6] in (b"GIF87a", b"GIF89a"):
        return {"image"}
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return {"image"}
    if (head[:2] == b"BM" and len(data) > 26) or head[:4] in (b"II*\x00", b"MM\x00*"):
        return {"image"}
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return {"audio"}
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return {"video"}
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return {"audio"}
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _FTYP_IMAGE:
            return {"image"}
        if brand in _FTYP_AUDIO or ext in ("m4a", "m4b", "aac"):
            return {"audio"}
        if brand == b"qt  " or ext in ("mp4", "mov", "m4v"):
            return {"video"}
        return {"video", "audio"}
    if head[:4] == b"\x1aE\xdf\xa3":  # Matroska / WebM
        return {"audio"} if ext in ("weba", "mka") else {"video", "audio"}
    if head[:4] in (b"OggS", b"fLaC") or head[:3] == b"ID3" or head[:5] == b"#!AMR":
        return {"audio"}
    if len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0 and ext not in ("jpg", "jpeg"):
        return {"audio"}  # MPEG audio / ADTS frame sync
    if head[:4] == b"glTF":
        return {"3d"}
    if head[:4] == b"ply\n" or head[:5] == b"ply\r\n" or head.startswith(b"Kaydara FBX Binary"):
        return {"3d"}
    if head[:5] == b"%PDF-":
        return {"document"}
    if head[:4] == b"PK\x03\x04":
        if ext in OFFICE_EXTENSIONS:
            return {"document"}
        if ext in ("usdz", "3mf"):
            return {"3d"}
    return set()


def _ext_of(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def guess_mime(name: str, kind: str = "") -> str:
    ext = _ext_of(name)
    if ext in EXTRA_MIME:
        return EXTRA_MIME[ext]
    guessed = mimetypes.guess_type(f"file.{ext}")[0] if ext else None
    if guessed:
        return guessed
    return {"image": "image/png", "audio": "audio/mpeg", "video": "video/mp4", "3d": "model/gltf-binary"}.get(
        kind, "application/octet-stream")


def _hermes_home() -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def media_cache_roots() -> list[Path]:
    home = _resolved(_hermes_home())
    return [home / name for name in MEDIA_CACHE_DIRS]


def local_terminal_backend() -> bool:
    """Whether the active profile's terminal backend runs on this machine (``terminal.backend``)."""
    backend = ""
    try:
        from hermes_cli.config import load_config_readonly

        terminal = (load_config_readonly() or {}).get("terminal") or {}
        if isinstance(terminal, dict):
            backend = str(terminal.get("backend") or terminal.get("env_type") or "")
    except Exception:
        backend = ""
    if not backend.strip():
        backend = os.environ.get("TERMINAL_ENV", "")
    return backend.strip().lower() in ("", "local")


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except ValueError:
        return False


def check_location(path: Path) -> None:
    """Raise InputRefused when Hermes' file policy keeps ``path`` from being read."""
    if any(_inside(path, root) for root in media_cache_roots()):
        return
    home = _resolved(_hermes_home())
    if _inside(path, home):
        raise InputRefused(
            f"{path} is inside the Hermes home. Only its media caches (cache/, images/) are read; "
            "pass a file from the chat, an earlier result, or a URL.")
    if not local_terminal_backend():
        raise InputRefused(
            f"{path} cannot be read: with a sandboxed terminal backend only files Hermes received in the chat "
            "or saved as results are read. Pass one of those, or a public URL.")
    text = str(path)
    if any(text == prefix or text.startswith(prefix + "/") for prefix in DENIED_PREFIXES):
        raise InputRefused(f"{path} is in a system folder, which is not read.")
    user_home = _resolved(Path.home())
    for part in DENIED_HOME_PARTS:
        if _inside(path, user_home / part):
            raise InputRefused(f"{path} is in {part}, a folder for credentials or settings, which is not read.")


def read_local(ref: str, kinds: Iterable[str], *, trusted: bool = False) -> Blob:
    """Read a local media file for upload, under the rules in this module's docstring.

    ``trusted`` is for a path Hermes itself hands a backend (a voice message to transcribe, a source
    image after Hermes' own sandbox check), which skips the location rules but not the media check."""
    raw = ref[len("file://"):] if ref.lower().startswith("file://") else ref
    path = _resolved(Path(os.path.expanduser(unquote(raw) if ref.lower().startswith("file://") else raw)))
    if not trusted:
        check_location(path)
    if not path.is_file():
        raise InputRefused(f"No file at {path}.")
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise InputRefused(f"{path.name} is larger than the 100 MB upload limit.")
    if size == 0:
        raise InputRefused(f"{path.name} is empty.")
    with path.open("rb") as handle:
        data = handle.read(MAX_INPUT_BYTES + 1)
    return _checked(data, path.name, kinds)


def read_data_url(ref: str, kinds: Iterable[str]) -> Blob:
    header, sep, payload = ref.partition(",")
    if not sep or ";base64" not in header.lower():
        raise InputRefused("A data: URL must be base64-encoded.")
    try:
        data = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        raise InputRefused("The data: URL is not valid base64.") from None
    if len(data) > MAX_INPUT_BYTES:
        raise InputRefused("The data: URL is larger than the 100 MB upload limit.")
    mime = header[5:].split(";", 1)[0].strip().lower()
    ext = CONTENT_TYPE_EXT.get(mime, "bin")
    return _checked(data, f"input.{ext}", kinds)


def _checked(data: bytes, name: str, kinds: Iterable[str]) -> Blob:
    wanted = set(kinds)
    found = sniff(data, name)
    usable = found & wanted
    if not usable:
        what = " or ".join(sorted(wanted - {"document"}) + (["document (PDF, Office)"] if "document" in wanted else []))
        raise InputRefused(f"{name} is not {('an ' if what[:1] in 'aeiou' else 'a ')}{what} file.")
    kind = "video" if "video" in usable else sorted(usable)[0]
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "input"
    stem = clean.rsplit(".", 1)[0] if _ext_of(clean) else clean
    ext = canonical_extension(data, clean, kind)
    blob = Blob(data=data, name=f"{stem[-100:]}.{ext}", mime=guess_mime(f"f.{ext}", kind), kind=kind)
    return blob if ext in UPLOAD_EXTENSIONS.get(kind, ()) else convert(blob, ext)


def canonical_extension(data: bytes, name: str, kind: str) -> str:
    """The extension the bytes really have (a mislabelled file gets its true one)."""
    head = data[:64]
    ext = _ext_of(name)
    if kind == "image":
        if head.startswith(b"\x89PNG"):
            return "png"
        if head.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if head[:6] in (b"GIF87a", b"GIF89a"):
            return "gif"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "webp"
        if head[4:8] == b"ftyp":
            return "avif" if head[8:12] in (b"avif", b"avis") else "heic"
        if head[:2] == b"BM":
            return "bmp"
        if head[:4] in (b"II*\x00", b"MM\x00*"):
            return "tiff"
        return ext or "png"
    if kind == "audio":
        if head[:4] == b"RIFF":
            return "wav"
        if head[:4] == b"OggS":
            return "ogg"
        if head[:4] == b"fLaC":
            return "flac"
        if head[:5] == b"#!AMR":
            return "amr"
        if head[:4] == b"FORM":
            return "aiff"
        if head[4:8] == b"ftyp":
            return "m4a"
        if head[:4] == b"\x1aE\xdf\xa3":
            return "webm"
        if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] not in (0xF1, 0xF9)):
            return "mp3"
        if len(head) > 1 and head[0] == 0xFF:
            return "aac"
        return ext or "mp3"
    if kind == "video":
        if head[:4] == b"RIFF":
            return "avi"
        if head[:4] == b"\x1aE\xdf\xa3":
            return "webm" if ext == "webm" else "mkv"
        if head[4:8] == b"ftyp":
            return "mov" if head[8:12] == b"qt  " else "mp4"
        return ext or "mp4"
    if kind == "3d":
        return "glb" if head[:4] == b"glTF" else (ext or "glb")
    if head[:5] == b"%PDF-":
        return "pdf"
    return ext or "bin"


def convert(blob: Blob, ext: str) -> Blob:
    """Convert audio to m4a or an image to png with the local ffmpeg, for formats Wiro AI does not take."""
    target = CONVERT_TO.get(blob.kind)
    accepted = ", ".join(sorted(UPLOAD_EXTENSIONS.get(blob.kind, ()))) or "a public URL"
    if target is None:
        raise InputRefused(f"Wiro AI does not take .{ext} files as {blob.kind} input. Use {accepted}, or a public URL.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise InputRefused(
            f"Wiro AI does not take .{ext} files. Install ffmpeg so the plugin can convert it to .{target}, "
            f"or pass a {accepted} file.")
    with tempfile.TemporaryDirectory(prefix="wiro-hermes-") as folder:
        source = Path(folder) / f"input.{ext}"
        output = Path(folder) / f"output.{target}"
        source.write_bytes(blob.data)
        options = ["-vn", "-c:a", "aac", "-b:a", "96k"] if target == "m4a" else ["-frames:v", "1"]
        try:
            done = subprocess.run(
                [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), *options, str(output)],
                capture_output=True, timeout=CONVERT_TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise InputRefused(f"ffmpeg could not convert {blob.name} ({exc}).") from None
        if done.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            raise InputRefused(f"ffmpeg could not convert {blob.name} to .{target}. Pass a {accepted} file.")
        data = output.read_bytes()
    stem = blob.name.rsplit(".", 1)[0]
    return Blob(data=data, name=f"{stem}.{target}", mime=guess_mime(f"f.{target}", blob.kind), kind=blob.kind)


def is_remote(ref: str) -> bool:
    try:
        parsed = urlparse(ref)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


# -- outputs ------------------------------------------------------------------------------------


def output_extension(name: str, url: str, content_type: str) -> str:
    for candidate in (_ext_of(name), _ext_of(urlparse(url).path)):
        if candidate and _SAFE_EXT.match(candidate):
            return "jpg" if candidate == "jpeg" else candidate
    return CONTENT_TYPE_EXT.get((content_type or "").split(";", 1)[0].strip().lower(), "bin")


def output_kind(ext: str, content_type: str = "") -> str:
    ctype = (content_type or "").lower()
    if ext in MODEL_EXTENSIONS or ctype.startswith("model/"):
        return "3d"
    if ext in TEXT_EXTENSIONS or ctype.startswith("text/"):
        return "text"
    for kind in ("image", "video", "audio"):
        if ctype.startswith(kind + "/"):
            return kind
    guessed = (mimetypes.guess_type(f"file.{ext}")[0] or "") if ext else ""
    for kind in ("image", "video", "audio"):
        if guessed.startswith(kind + "/"):
            return kind
    if ext in ("opus", "m4a", "flac", "aac"):
        return "audio"
    return "file"


def save_output(data: bytes, kind: str, ext: str, prefix: str = "wiro") -> Path:
    folder = _hermes_home() / "cache" / "generated" / KIND_DIRS.get(kind, "files")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = folder / f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}.{ext if _SAFE_EXT.match(ext) else 'bin'}"
    path.write_bytes(data)
    return path


def media_tag(path: Path | str) -> str:
    return f"MEDIA:{path}"
