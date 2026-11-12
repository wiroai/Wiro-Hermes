"""An in-memory stand-in for api.wiro.ai and its CDN, plugged into the real WiroClient as its opener."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP3 = b"ID3\x04\x00" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
OGG = b"OggS\x00\x02" + b"\x00" * 64
GLB = b"glTF\x02\x00\x00\x00" + b"\x00" * 64


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self._body.read() if size is None or size < 0 else self._body.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def item(id: str, type: str = "text", required: bool = False, default: Any = None, options: list | None = None,
         **extra: Any) -> dict:
    row: dict[str, Any] = {"id": id, "type": type, "required": required}
    if default is not None:
        row["defaultvalue"] = default
    if options is not None:
        row["options"] = [{"value": o, "text": o} if not isinstance(o, dict) else o for o in options]
    row.update(extra)
    return row


def model(owner: str, project: str, items: list[dict], dynamicprice: Any = None, **extra: Any) -> dict:
    row = {
        "cleanslugowner": owner, "cleanslugproject": project, "slugowner": owner, "slugproject": project,
        "title": f"{owner} {project}", "seodescription": f"{project} model", "categories": ["text-to-image"],
        "parameters": [{"title": "Inputs", "items": items}],
        "dynamicprice": json.dumps(dynamicprice) if dynamicprice is not None else "",
    }
    row.update(extra)
    return row


def task_row(token: str, status: str, *, taskid: str = "101", pexit: str = "", outputs: list | None = None,
             **extra: Any) -> dict:
    row = {"id": taskid, "socketaccesstoken": token, "status": status, "pexit": pexit,
           "outputs": outputs or [], "debugoutput": "", "debugerror": "Traceback: secret stderr",
           "totalcost": "0", "elapsedseconds": "3.2", "modelslugowner": "owner", "modelslugproject": "model"}
    row.update(extra)
    return row


class FakeWiro:
    """Routes the client's requests. ``tasks[token]`` is a list of Task/Detail rows returned in turn
    (the last one repeats); ``cdn[url]`` is (bytes, content type) for downloads."""

    def __init__(self) -> None:
        self.models: dict[str, dict] = {}
        self.tasks: dict[str, list[dict]] = {}
        self.cdn: dict[str, tuple[bytes, str]] = {}
        self.calls: list[tuple[str, Any, dict]] = []
        self.uploads: list[dict] = []
        self.runs: list[tuple[str, dict]] = []
        self.next_task: list[str] = []
        self.list_rows: list[dict] = []
        self.task_list: list[dict] = []
        self.workflows: dict[str, dict] = {}
        self.workflow_runs: dict[str, list[dict]] = {}
        self.workflow_started: list[tuple[str, dict]] = []
        self.handlers: dict[str, Callable[[dict], dict]] = {}
        self.stopped: list[tuple[str, dict]] = []

    # -- opener protocol ----------------------------------------------------------------------

    def open(self, request: Any, timeout: float | None = None) -> FakeResponse:
        url = request.full_url
        parsed = urlparse(url)
        headers = {k.lower(): v for k, v in request.header_items()}
        if parsed.hostname and parsed.hostname != "api.wiro.ai":
            if url not in self.cdn:
                return FakeResponse(404, b"not found")
            data, ctype = self.cdn[url]
            self.calls.append(("GET", url, headers))
            return FakeResponse(200, data, {"Content-Type": ctype, "Content-Length": str(len(data))})
        path = parsed.path.removeprefix("/v1")
        body: Any = request.data
        if headers.get("content-type", "").startswith("application/json"):
            body = json.loads(body.decode("utf-8"))
        self.calls.append((path, body, headers))
        if path in self.handlers:
            return self._json(self.handlers[path](body))
        if path == "/Tool/Detail":
            key = f"{body['slugowner']}/{body['slugproject']}"
            if key not in self.models:
                return self._json({"result": False, "errors": [{"code": 0, "message": "tool-not-accessible"}], "tool": []})
            return self._json({"result": True, "errors": [], "tool": [self.models[key]]})
        if path == "/Tool/List":
            return self._json({"result": True, "errors": [], "total": str(len(self.list_rows)), "tool": self.list_rows})
        if path.startswith("/Run/"):
            slug = path[len("/Run/"):]
            self.runs.append((slug, body))
            token = self.next_task.pop(0) if self.next_task else "tok-1"
            return self._json({"result": True, "errors": [], "taskid": "101", "socketaccesstoken": token})
        if path == "/Task/Detail":
            token = body.get("tasktoken") or self._token_of(body.get("taskid"))
            states = self.tasks.get(token)
            if not states:
                return self._json({"result": False, "errors": [{"message": "task-not-founded"}], "tasklist": []})
            row = states.pop(0) if len(states) > 1 else states[0]
            return self._json({"result": True, "errors": [], "tasklist": [row]})
        if path == "/Task/List":
            return self._json({"result": True, "errors": [], "tasklist": self.task_list})
        if path in ("/Task/Cancel", "/Task/Kill"):
            self.stopped.append((path, body))
            return self._json({"result": True, "errors": []})
        if path == "/File/Upload":
            name = re.search(rb'filename="([^"]+)"', body).group(1).decode()
            ctype = re.search(rb"Content-Type: ([^\r\n]+)", body).group(1).decode()
            payload = body.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
            if any(upload["name"] == name for upload in self.uploads):  # what the real File/Upload does
                return self._json({"result": False, "errors": [{"code": 0, "message": "filesystem-already-exist"}]})
            url = f"https://cdn1.wiro.ai/uploads/{len(self.uploads) + 1}/{name}"
            self.uploads.append({"name": name, "content_type": ctype, "data": payload, "url": url})
            return self._json({"result": True, "errors": [], "list": [{"name": name, "url": url}]})
        if path == "/Workflow/Detail":
            row = self.workflows.get(body["slug"])
            return self._json({"result": bool(row), "errors": [], "workflow": [row] if row else []})
        if path == "/Workflow/List":
            return self._json({"result": True, "errors": [], "total": len(self.workflows),
                               "workflow": list(self.workflows.values())})
        if path == "/Workflow/Run":
            self.workflow_started.append((body["slug"], body.get("input") or {}))
            return self._json({"result": True, "errors": [], "workflowtoken": "wf-1"})
        if path == "/Workflow/RunDetail":
            states = self.workflow_runs[body["workflowtoken"]]
            row = states.pop(0) if len(states) > 1 else states[0]
            return self._json({"result": True, "errors": [], **row})
        return self._json({"result": False, "errors": [{"message": f"unknown path {path}"}]})

    def _token_of(self, taskid: Any) -> str:
        for token, states in self.tasks.items():
            if states and str(states[0].get("id")) == str(taskid):
                return token
        return ""

    @staticmethod
    def _json(payload: dict, status: int = 200) -> FakeResponse:
        return FakeResponse(status, json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"})

    def api_calls(self, path: str) -> list:
        return [body for call_path, body, _headers in self.calls if call_path == path]
