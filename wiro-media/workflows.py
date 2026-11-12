"""Wiro AI workflows: their inputs, and what a finished run returns.

A workflow's ``contents`` hold its Start fields (``schema.input``), the saved Start values
(``metadata.input.values``) and the End node (``nodes.output.fields``), whose fields reference
step outputs as ``$node-<id>.outputs`` (a model step's files) or ``$node-<id>.<port>`` (a
utility's port), or a Start value as ``$input.<key>``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote, urlparse

PAGE_URL = "https://wiro.ai/workflows/"
SENSITIVE_KEY = re.compile(
    r"(^|[-_])(password|secret|api[-_]?key|authorization|credentials?|private[-_]?key|access[-_]?token"
    r"|refresh[-_]?token|client[-_]?secret|bearer[-_]?token|token)([-_]|$)", re.IGNORECASE)
KEY_SHAPES = (
    re.compile(r"^sk-[A-Za-z0-9_-]{20,}$"), re.compile(r"^AIza[0-9A-Za-z_-]{30,}$"),
    re.compile(r"^(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}$"), re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$"),
    re.compile(r"^xox[abprs]-[A-Za-z0-9-]{10,}$"), re.compile(r"^AKIA[0-9A-Z]{16}$"),
    re.compile(r"^hf_[A-Za-z0-9]{30,}$"), re.compile(r"^r8_[A-Za-z0-9]{30,}$"),
    re.compile(r"^(sk|rk)_(live|test)_[A-Za-z0-9]{16,}$"),
)
HIDDEN = "[hidden]"


def parse_slug(value: str) -> str:
    """A full workflow slug (``<name>-<5 character id>``) from the slug or the workflow page URL."""
    text = (value or "").strip()
    if text.startswith(("http://", "https://")):
        parts = [part for part in urlparse(text).path.split("/") if part]
        text = parts[-1] if parts else ""
    text = unquote(text.split("?", 1)[0].split("#", 1)[0])
    name, sep, slug_id = text.rpartition("-")
    if not sep or not name or not re.fullmatch(r"[A-Za-z0-9]{5}", slug_id):
        raise ValueError(
            f'"{value}" is not a full workflow slug. Use the slug wiro_workflows lists (it ends with a dash and '
            "a 5-character id) or the workflow page URL.")
    return text


def full_slug(row: Mapping[str, Any]) -> str:
    slug, slug_id = str(row.get("slug") or ""), str(row.get("slugid") or "")
    return f"{slug}-{slug_id}" if slug and slug_id else str(row.get("fullslug") or row.get("workflowslug") or slug)


def contents_of(row: Mapping[str, Any]) -> dict:
    contents = row.get("contents")
    if isinstance(contents, str):
        try:
            contents = json.loads(contents or "{}")
        except json.JSONDecodeError:
            return {}
    return contents if isinstance(contents, dict) else {}


def _hide(key: str, value: Any) -> Any:
    if value in (None, ""):
        return value
    if SENSITIVE_KEY.search(key) or (isinstance(value, str) and any(s.match(value.strip()) for s in KEY_SHAPES)):
        return HIDDEN
    return value


def inputs(contents: Mapping[str, Any]) -> list[dict]:
    """The Start fields a run takes, keyed as /Workflow/Run expects, with secret-looking values hidden."""
    schema = contents.get("schema") if isinstance(contents.get("schema"), dict) else {}
    fields = schema.get("input") if isinstance(schema.get("input"), dict) else {}
    metadata = contents.get("metadata") if isinstance(contents.get("metadata"), dict) else {}
    meta_input = metadata.get("input") if isinstance(metadata.get("input"), dict) else {}
    saved = meta_input.get("values") if isinstance(meta_input.get("values"), dict) else {}
    rows = []
    for key, field in fields.items():
        field = field if isinstance(field, dict) else {}
        ui = field.get("ui") if isinstance(field.get("ui"), dict) else {}
        row: dict[str, Any] = {
            "key": key,
            "label": str(field.get("label") or field.get("name") or key)[:120],
            "type": str(field.get("type") or "string"),
        }
        if ui.get("field"):
            row["field"] = str(ui["field"])
        options = [str(o.get("value")) for o in ui.get("options") or [] if isinstance(o, dict) and o.get("value") not in (None, "")]
        if options:
            row["options"] = options[:40]
        if "defaultValue" in field and field["defaultValue"] not in (None, ""):
            row["default"] = _short(_hide(key, field["defaultValue"]))
        if key in saved and saved[key] not in (None, ""):
            row["saved_value"] = _short(_hide(key, saved[key]))
        rows.append(row)
    return rows


def _short(value: Any, limit: int = 300) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "..."
    return value


def output_fields(contents: Mapping[str, Any]) -> dict:
    nodes = contents.get("nodes") if isinstance(contents.get("nodes"), dict) else {}
    end = nodes.get("output") if isinstance(nodes.get("output"), dict) else {}
    return dict(end.get("fields") or {}) if isinstance(end.get("fields"), dict) else {}


def summary(row: Mapping[str, Any]) -> dict:
    slug = full_slug(row)
    entry: dict[str, Any] = {"workflow": slug, "title": str(row.get("title") or slug)[:160]}
    description = " ".join(str(row.get("description") or "").split())
    if description:
        entry["description"] = description[:300]
    models = [str(item.get("slug")) for item in row.get("toolitems") or [] if isinstance(item, dict) and item.get("slug")]
    if not models:
        models = [str(m) for m in row.get("tools") or [] if isinstance(m, str)]
    if models:
        entry["models"] = models[:12]
    if str(row.get("isprotected") or "0") == "1":
        entry["protected"] = True
    entry["page"] = PAGE_URL + slug
    return entry


def _flatten(value: Any, out: list[dict], depth: int = 0) -> None:
    if depth > 6 or value is None:
        return
    if isinstance(value, list):
        for entry in value:
            _flatten(entry, out, depth + 1)
        return
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"https?://\S+", text):
            out.append({"url": text})
        elif text:
            out.append({"text": value[:20000]})
        return
    if isinstance(value, (int, float, bool)):
        out.append({"text": str(value)})
        return
    if not isinstance(value, dict):
        return
    if isinstance(value.get("children"), list) and value["children"]:
        _flatten(value["children"], out, depth + 1)
        return
    if value.get("contenttype") == "raw" and isinstance(value.get("content"), dict):
        answer = value["content"].get("answer")
        if isinstance(answer, list):
            answer = "".join(part if isinstance(part, str) else str((part or {}).get("text") or "") for part in answer)
        if answer:
            out.append({"text": str(answer)[:20000]})
        return
    url = value.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        item = {"url": url}
        if value.get("name"):
            item["name"] = str(value["name"])
        if value.get("contenttype"):
            item["mime_type"] = str(value["contenttype"])
        out.append(item)


def run_outputs(contents: Mapping[str, Any], nodes: list[dict]) -> tuple[dict[str, list[dict]], list[str]]:
    """The End node's fields resolved against the run's steps: field -> items (url or text)."""
    by_id = {str(node.get("nodeId") or ""): node for node in nodes if isinstance(node, dict)}
    schema = contents.get("schema") if isinstance(contents.get("schema"), dict) else {}
    fields_def = schema.get("input") if isinstance(schema.get("input"), dict) else {}
    metadata = contents.get("metadata") if isinstance(contents.get("metadata"), dict) else {}
    meta_input = metadata.get("input") if isinstance(metadata.get("input"), dict) else {}
    saved = meta_input.get("values") if isinstance(meta_input.get("values"), dict) else {}
    outputs: dict[str, list[dict]] = {}
    missing: list[str] = []
    for field, reference in output_fields(contents).items():
        items: list[dict] = []
        for ref in reference if isinstance(reference, list) else [reference]:
            if not isinstance(ref, str) or not ref.startswith("$"):
                _flatten(ref, items)
                continue
            if ref.startswith("$input."):
                key = ref[len("$input."):]
                stored = saved.get(key)
                default = (fields_def.get(key) or {}).get("defaultValue") if isinstance(fields_def.get(key), dict) else None
                _flatten(stored if stored not in (None, "", [], {}) else default, items)
                continue
            match = re.fullmatch(r"\$(node-[^.]+)\.(.+)", ref)
            node = by_id.get(match.group(1)) if match else None
            if not match or node is None:
                missing.append(f"{field}: its step is not part of this run")
                continue
            if node.get("status") != "completed":
                missing.append(f"{field}: step {match.group(1)} is {node.get('status')}")
                continue
            step_outputs = node.get("outputs")
            port = match.group(2)
            if port == "outputs" and isinstance(step_outputs, list):
                _flatten(step_outputs, items)
            elif isinstance(step_outputs, dict) and port in step_outputs:
                _flatten(step_outputs[port], items)
            else:
                missing.append(f"{field}: step {match.group(1)} has no output {port}")
        outputs[field] = items
    return outputs, missing


def failed_steps(nodes: list[dict]) -> list[dict]:
    rows = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("status") not in ("failed", "aborted", "cancelled"):
            continue
        row = {"step": str(node.get("nodeId") or ""), "status": str(node.get("status"))}
        if node.get("tool"):
            row["model"] = str(node["tool"])
        cause = str(node.get("cause") or "")
        if cause and cause != row["status"] and re.fullmatch(r"[A-Za-z0-9_.:-]{1,60}", cause):
            row["cause"] = cause
        rows.append(row)
    return rows
