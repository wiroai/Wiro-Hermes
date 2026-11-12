"""Model parameters as Wiro AI's Tool/Detail describes them, and how the tools fill them.

A run refuses a required parameter that is left out, while optional ones get the catalog value
on Wiro AI's side. The catalog value of a "sample" parameter (file inputs, ids that contain
"input", the prompt and the LLM chat fields) is an example from the model's page, so it is never
sent on the user's behalf. See https://wiro.ai/docs/model-parameters.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

FILE_TYPES = frozenset({"fileinput", "multifileinput", "multifileinputdrop", "combinefileinput", "fileexplorer"})
SAMPLE_IDS = frozenset({"prompt", "messages", "tools", "tool_choice", "previousTaskToken", "toolOutputs",
                        "response_format"})
CHECKBOX_OFF = frozenset({"", "false", "0", "no", "off", "null", "undefined"})
NUMBER_TYPES = frozenset({"number", "float", "range"})
SECONDARY_IMAGE_WORDS = ("mask", "last", "middle", "end", "tail", "style", "reference", "ref")

PROMPT_IDS = ("prompt",)
TEXT_IDS = ("prompt", "text", "input_text", "inputText", "script")
NEGATIVE_IDS = ("negative_prompt", "negativePrompt", "negative")
RATIO_IDS = ("aspectRatio", "aspect_ratio", "ratio", "aspect", "size")
RESOLUTION_IDS = ("resolution", "quality", "videoQuality", "size")
DURATION_IDS = ("duration", "durationSeconds", "duration_seconds", "seconds", "length", "videoLength")
VOICE_IDS = ("voice", "voiceId", "voice_id", "speaker")
LANGUAGE_IDS = ("language", "languageCode", "language_code", "lang")
COUNT_IDS = ("samples", "numImages", "num_images", "numberOfImages", "n", "count", "maxImages")
SEED_IDS = ("seed",)
SCALE_IDS = ("outscale", "upscale", "scale", "upscaleFactor", "upscale_factor", "scaleFactor", "scale_factor")
AUDIO_FLAG_IDS = ("generateAudio", "generate_audio", "audio", "audioSwitch", "withAudio", "sound")
INSTRUMENTAL_IDS = ("instrumental", "isInstrumental", "instrumentalOnly")
LYRICS_IDS = ("lyrics", "lyric", "songLyrics")
LAST_IMAGE_IDS = ("inputImageLast", "lastFrame", "last_frame", "endImage", "end_image", "inputImageEnd", "tailImage")

# Hermes' image_generate aspect names.
NAMED_RATIOS = {"landscape": "16:9", "wide": "16:9", "square": "1:1", "portrait": "9:16", "tall": "9:16"}


def param_items(detail: Mapping[str, Any]) -> list[dict]:
    items: list[dict] = []
    for group in detail.get("parameters") or []:
        for item in (group or {}).get("items") or [] if isinstance(group, Mapping) else []:
            if isinstance(item, dict) and item.get("id"):
                items.append(item)
    return items


def is_required(item: Mapping[str, Any]) -> bool:
    return item.get("required") is True or str(item.get("required")).lower() == "true"


def is_file(item: Mapping[str, Any]) -> bool:
    return str(item.get("type")) in FILE_TYPES


def is_sample(item: Mapping[str, Any]) -> bool:
    item_id = str(item.get("id"))
    return is_file(item) or "input" in item_id.lower() or item_id in SAMPLE_IDS


def option_values(item: Mapping[str, Any]) -> list[str]:
    values = []
    for option in item.get("options") or []:
        value = option.get("value", option.get("id")) if isinstance(option, Mapping) else option
        if value is not None and str(value) != "":
            values.append(str(value))
    return values


def option_rows(item: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(value, label) for each option of a select."""
    rows = []
    for option in item.get("options") or []:
        if isinstance(option, Mapping):
            value = option.get("value", option.get("id"))
            label = option.get("text") or option.get("label") or option.get("name") or value
        else:
            value = label = option
        if value is not None and str(value) != "":
            rows.append((str(value), str(label)))
    return rows


def default_value(item: Mapping[str, Any]) -> Any:
    """The catalog value of a parameter, or None. Checkboxes come back as "true"/"false"."""
    if item.get("type") == "checkbox":
        initial = item["value"] if "value" in item else item.get("defaultvalue")
        if isinstance(initial, bool):
            return "true" if initial else "false"
        text = str(initial if initial is not None else "").strip().lower()
        return "false" if text in CHECKBOX_OFF else "true"
    raw = item.get("defaultvalue")
    if raw in (None, "") or raw == []:
        raw = item.get("value")
    if raw in (None, "") or raw == []:
        return None
    return raw


def default_text(item: Mapping[str, Any]) -> str | None:
    value = default_value(item)
    if value is None:
        return None
    return json.dumps(value) if isinstance(value, (list, dict)) else str(value)


def catalog_choice(item: Mapping[str, Any]) -> Any:
    """The catalog default, matched to the options when the item has a list (case aside)."""
    value = default_value(item)
    choices = option_values(item)
    if not choices or value is None or isinstance(value, list):
        return value
    if str(value) in choices:
        return str(value)
    folded = {c.lower(): c for c in choices}
    return folded.get(str(value).lower(), choices[0])


def number_range(item: Mapping[str, Any]) -> tuple[float | None, float | None]:
    def num(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    low, high = num(item.get("minvalue")), num(item.get("maxvalue"))
    if low is not None and high is not None and low == high == 0:
        return None, None  # 0..0 on Tool/Detail means no range
    return low, high


def find(items: Iterable[Mapping[str, Any]], *ids: str) -> dict | None:
    """The first item whose id is one of ``ids`` (exact, then ignoring case), in the order given."""
    items = list(items)
    by_id = {str(item["id"]): item for item in items}
    for item_id in ids:
        if item_id in by_id:
            return dict(by_id[item_id])
    folded = {str(item["id"]).lower(): item for item in items}
    for item_id in ids:
        if item_id.lower() in folded:
            return dict(folded[item_id.lower()])
    return None


def text_param(items: list[dict], ids: tuple[str, ...] = TEXT_IDS) -> dict | None:
    """The model's text input: the first of ``ids`` that is not a file, else its first textarea."""
    found = find([i for i in items if not is_file(i)], *ids)
    return found or next((item for item in items if item.get("type") == "textarea"), None)


def image_param(items: list[dict]) -> dict | None:
    """The main image input: inputImage, or the first image file field that is not a mask or end frame."""
    exact = find(items, "inputImage")
    if exact and is_file(exact):
        return exact
    for item in items:
        item_id = str(item["id"]).lower()
        if is_file(item) and "image" in item_id and not any(word in item_id for word in SECONDARY_IMAGE_WORDS):
            return item
    return next((item for item in items if is_file(item) and "image" in str(item["id"]).lower()
                 and "mask" not in str(item["id"]).lower()), None)


def last_image_param(items: list[dict]) -> dict | None:
    found = find(items, *LAST_IMAGE_IDS)
    if found and is_file(found):
        return found
    return next((item for item in items if is_file(item) and "image" in str(item["id"]).lower()
                 and any(word in str(item["id"]).lower() for word in ("last", "end", "tail"))), None)


def media_param(items: list[dict], word: str) -> dict | None:
    """The main audio or video file field: input<Word>, or the first file field with the word in its id."""
    exact = find(items, "input" + word.capitalize())
    if exact and is_file(exact):
        return exact
    return next((item for item in items if is_file(item) and word in str(item["id"]).lower()), None)


def file_params(items: list[dict]) -> list[dict]:
    return [item for item in items if is_file(item)]


def match_option(item: Mapping[str, Any], value: Any) -> str | None:
    """The option value that matches ``value`` by value or label, ignoring case; None if none does.
    Items without an option list take the value as text."""
    text = str(value).strip()
    rows = option_rows(item)
    if not rows:
        return text
    lowered = text.lower()
    for option_value, label in rows:
        if lowered in (option_value.lower(), label.lower()):
            return option_value
    return None


def ratio_value(value: str) -> str:
    text = str(value or "").strip().lower()
    return NAMED_RATIOS.get(text, text)


def _ratio_number(text: str) -> float | None:
    left, sep, right = text.partition(":")
    if not sep:
        left, sep, right = text.partition("x")
    try:
        width, height = float(left), float(right)
    except ValueError:
        return None
    return width / height if width > 0 and height > 0 else None


def match_ratio(item: Mapping[str, Any], value: str) -> str | None:
    """The option for an aspect ratio: exact, else the closest ratio the model offers (within 15%)."""
    wanted = ratio_value(value)
    exact = match_option(item, wanted)
    if exact is not None:
        return exact
    target = _ratio_number(wanted)
    if target is None:
        return None
    best, best_gap = None, None
    for option_value, _label in option_rows(item):
        ratio = _ratio_number(option_value.lower())
        if ratio is None:
            continue
        gap = abs(math.log(ratio / target))
        if best_gap is None or gap < best_gap:
            best, best_gap = option_value, gap
    return best if best_gap is not None and best_gap < math.log(1.15) else None


def match_number(item: Mapping[str, Any], value: Any) -> Any:
    """A number for a select of numbers (nearest option) or a numeric field (clamped to its range)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    options = option_values(item)
    if options:
        numeric = []
        for option in options:
            try:
                numeric.append((abs(float(option) - number), option))
            except ValueError:
                continue
        if not numeric:
            return match_option(item, value)
        return min(numeric)[1]
    low, high = number_range(item)
    if low is not None:
        number = max(low, number)
    if high is not None:
        number = min(high, number)
    if str(item.get("type")) == "float":
        return number
    return int(number) if number.is_integer() else number


def flag_value(item: Mapping[str, Any], on: bool) -> Any:
    """A yes/no setting in the form its field takes: a checkbox, or a select of true/false values."""
    if item.get("type") == "checkbox":
        return on
    options = {value.lower(): value for value in option_values(item)}
    for word in (("true", "yes", "on", "1") if on else ("false", "no", "off", "0")):
        if word in options:
            return options[word]
    return "true" if on else "false"


def put_files(item: Mapping[str, Any], urls: list[str], parameters: dict[str, Any], items: list[dict]) -> None:
    """Place file URLs the way each file field takes them: one URL for fileinput (in its <id>Url
    companion when there is one), a list for combinefileinput, comma-separated in the
    <..>UrlMultiple field for multifileinput."""
    item_id = str(item["id"])
    kind = str(item.get("type"))
    if kind == "combinefileinput":
        limit = 0
        try:
            limit = int(item.get("maxinputlenght") or item.get("maxinputlength") or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit and len(urls) > limit:
            raise ValueError(f"{item_id} takes at most {limit} files, and {len(urls)} were given.")
        parameters[item_id] = list(urls)
    elif kind in ("multifileinput", "multifileinputdrop"):
        url_field = item_id.replace("Multiple", "UrlMultiple") if item_id.endswith("Multiple") else item_id
        parameters[url_field] = ",".join(urls)
    else:
        if len(urls) > 1:
            raise ValueError(f"{item_id} takes one file, and {len(urls)} were given.")
        companion = find(items, f"{item_id}Url")
        parameters[str(companion["id"]) if companion else item_id] = urls[0]


def file_value_given(item: Mapping[str, Any], parameters: Mapping[str, Any]) -> bool:
    item_id = str(item["id"])
    keys = [item_id, f"{item_id}Url"]
    if item_id.endswith("Multiple"):
        keys.append(item_id.replace("Multiple", "UrlMultiple"))
    return any(parameters.get(key) not in (None, "", []) for key in keys)


def fill_required(items: list[dict], parameters: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fill required settings the caller left out with their catalog value.

    Returns (parameters, the values filled in). Raises ValueError naming the required inputs only
    the caller can give (prompts, files and other sample fields)."""
    filled = dict(parameters)
    added: dict[str, Any] = {}
    missing: list[str] = []
    for item in items:
        item_id = str(item["id"])
        if not is_required(item):
            continue
        if is_file(item):
            if not file_value_given(item, filled):
                missing.append(item_id)
            continue
        if filled.get(item_id) not in (None, "", []):
            continue
        value = None if is_sample(item) else catalog_choice(item)
        if value is None:
            missing.append(item_id)
        else:
            filled[item_id] = value
            added[item_id] = value
    if missing:
        raise ValueError(
            "This model needs a value for: " + ", ".join(missing)
            + ". wiro_model_info lists what each one takes; pass them in params (files in files or params)."
        )
    return filled, added


def known_ids(items: list[dict]) -> set[str]:
    ids = {str(item["id"]) for item in items}
    for item in items:
        if is_file(item):
            item_id = str(item["id"])
            ids.add(f"{item_id}Url")
            if item_id.endswith("Multiple"):
                ids.add(item_id.replace("Multiple", "UrlMultiple"))
    return ids


def describe_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """One parameter as wiro_model_info shows it."""
    row: dict[str, Any] = {
        "id": str(item["id"]),
        "type": str(item.get("type") or ""),
        "required": is_required(item),
    }
    label = " ".join(str(item.get("label") or "").split())
    if label and label != row["id"]:
        row["label"] = label[:120]
    if not is_sample(item):
        value = default_text(item)
        if value is not None:
            row["default"] = value[:200]
    elif not is_file(item) and default_text(item):
        row["example"] = (default_text(item) or "")[:160]
    options = option_values(item)
    if options:
        row["options"] = options[:60]
        if len(options) > 60:
            row["more_options"] = len(options) - 60
    low, high = number_range(item) if str(item.get("type")) in NUMBER_TYPES else (None, None)
    if low is not None or high is not None:
        row["range"] = [low, high]
    note = " ".join(str(item.get("note") or item.get("description") or "").split())
    if note:
        row["note"] = note[:240]
    kind = str(item.get("type"))
    if kind == "combinefileinput":
        row["takes"] = "a list of file URLs or local paths"
    elif kind in ("multifileinput", "multifileinputdrop"):
        row["takes"] = "several files: pass them in files under this id"
    elif kind in FILE_TYPES:
        row["takes"] = "one file URL or local path"
    return row
