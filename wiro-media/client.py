"""A small client for the Wiro AI Run API (https://api.wiro.ai/v1), on the standard library.

It covers what the tools need: the model catalog, file uploads, starting a run and following
its task until it ends, workflow runs, and downloading output files. Every request goes to
api.wiro.ai, and output files are only downloaded from Wiro AI hosts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

VERSION = "2.0.0"
API_URL = "https://api.wiro.ai/v1"
USER_AGENT = f"wiro-hermes/{VERSION} (+https://github.com/wiroai/Wiro-Hermes)"
DEPOSIT_URL = "https://wiro.ai/panel/wallet/deposit/credit-card"
KEYS_URL = "https://wiro.ai/panel/project"

# Task/Detail statuses that close a task. Every other status, a persisted "task_error"
# included, means the task is still in progress.
TERMINAL_STATUSES = frozenset({"task_postprocess_end", "task_cancel"})
# Statuses before a worker has a GPU for the task: Task/Cancel stops these, Task/Kill the rest.
QUEUED_STATUSES = frozenset({"task_queue", "task_accept", "task_preprocess_start", "task_preprocess_end"})
WORKFLOW_FINAL = frozenset({"completed", "failed", "cancelled", "stopped"})

# Tool/Detail and Task/Detail answers when nothing matches in the key's workspace.
MODEL_MISSING_ERRORS = ("tool-not-accessible", "tool-not-founded")
TASK_MISSING_ERRORS = ("task-not-founded",)  # also matches "coretask-not-founded"

REQUEST_TIMEOUT = 60.0
UPLOAD_TIMEOUT = 300.0
DOWNLOAD_TIMEOUT = 300.0
UPLOAD_MAX_BYTES = 100 * 1024 * 1024
DETAIL_CACHE_SECONDS = 600.0
MAX_POLL_ERRORS = 5


class WiroError(Exception):
    """A Wiro AI call failed. The message is safe to show to the user."""


class TaskStillRunning(WiroError):
    """The task did not finish within the wait. It keeps running on Wiro AI."""

    def __init__(self, task: Mapping[str, Any]) -> None:
        super().__init__("The task is still running.")
        self.task = dict(task)


class WorkflowStillRunning(WiroError):
    """The workflow run did not finish within the wait. It keeps running on Wiro AI."""

    def __init__(self, body: Mapping[str, Any]) -> None:
        super().__init__("The workflow run is still running.")
        self.body = dict(body)


class RunOutcomeUnknown(WiroError):
    """A run request went out but no answer came back, so the task may exist."""


class WiroAuthError(WiroError):
    """Wiro AI did not accept the key or the signature. Asking again cannot help."""


class TaskMissing(WiroError):
    """No task with this reference in the key's workspace. Asking again cannot find one."""


def split_credentials(raw: str | None) -> tuple[str, str]:
    """``KEY`` or ``KEY:SECRET`` (a signature project), the form the Wiro AI gateway also takes."""
    value = (raw or "").strip()
    key, _, secret = value.partition(":")
    return key.strip(), secret.strip()


def parse_model(model: str) -> tuple[str, str]:
    """``owner/project``, or a wiro.ai model page URL, as its two slugs."""
    text = (model or "").strip()
    if text.startswith(("http://", "https://")):
        parts = [p for p in urlparse(text).path.split("/") if p]
        if len(parts) >= 3 and parts[0] == "models":
            parts = parts[1:3]
        text = "/".join(parts[-2:])
    owner, _, project = text.strip("/").partition("/")
    if not owner or not project or "/" in project or not _slug_ok(owner) or not _slug_ok(project):
        raise WiroError(f'"{model}" is not a Wiro AI model ID. Use owner/model, for example google/nano-banana-2-1.')
    return owner, project


def _slug_ok(part: str) -> bool:
    return all(ch.isalnum() or ch in "._-" for ch in part)


def is_wiro_url(url: str) -> bool:
    """An https URL on a Wiro AI host (wiro.ai or a subdomain such as cdn1.wiro.ai)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "wiro.ai" or host.endswith(".wiro.ai"))


def error_text(body: Any) -> tuple[str, set[int]]:
    """The messages and numeric codes of a Wiro AI ``errors`` array."""
    messages: list[str] = []
    codes: set[int] = set()
    errors = body.get("errors") if isinstance(body, Mapping) else None
    for item in errors or []:
        if isinstance(item, Mapping):
            message = str(item.get("message") or "").strip()
            code = item.get("code")
            if isinstance(code, int) or (isinstance(code, str) and code.strip().isdigit()):
                codes.add(int(code))
        else:
            message = str(item).strip()
        if message:
            messages.append(message)
    return "; ".join(messages).rstrip("."), codes


def _hint(codes: set[int], status: int, text: str) -> str:
    if 97 in codes or "insufficient" in text.lower():
        return f" Add credits at {DEPOSIT_URL} and run it again."
    if 96 in codes:
        return " Too many of your tasks are running at once: wait for one to finish, then run it again."
    if status == 401 or "authorization" in text.lower():
        return f" Check WIRO_API_KEY (KEY, or KEY:SECRET for a signature project) at {KEYS_URL}."
    return ""


class _WiroOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a download redirect only to another Wiro AI host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib signature
        if not is_wiro_url(newurl):
            raise WiroError("An output file redirected away from Wiro AI, so it was not downloaded.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class WiroClient:
    """Calls the Run API with one project's credentials. Thread-safe; holds no per-call state."""

    _detail_cache: dict[str, tuple[float, dict]] = {}
    _detail_lock = threading.Lock()

    def __init__(
        self,
        api_key: str,
        api_secret: str = "",
        *,
        base_url: str = API_URL,
        opener: urllib.request.OpenerDirector | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").strip()
        if not self.api_key:
            raise WiroError(f"WIRO_API_KEY is not set. Create a key at {KEYS_URL}.")
        self.base_url = base_url.rstrip("/")
        self._opener = opener or urllib.request.build_opener()
        self._download_opener = opener or urllib.request.build_opener(_WiroOnlyRedirects())
        self._sleep = sleep
        self._clock = clock

    def __repr__(self) -> str:  # never show the key
        return f"<WiroClient {'signature' if self.api_secret else 'api key'} auth>"

    @property
    def identity(self) -> str:
        """A short, non-reversible id of the key, for per-project bookkeeping."""
        return hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()[:16]

    # -- plumbing -------------------------------------------------------------------------

    def _headers(self, content_type: str | None = "application/json") -> dict[str, str]:
        headers = {"x-api-key": self.api_key, "User-Agent": USER_AGENT, "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.api_secret:
            nonce = str(int(time.time() * 1000))
            signature = hmac.new(
                self.api_key.encode("utf-8"), (self.api_secret + nonce).encode("utf-8"), hashlib.sha256
            ).hexdigest()
            headers["x-nonce"] = nonce
            headers["x-signature"] = signature
        return headers

    def _send(self, path: str, data: bytes, content_type: str, timeout: float) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method="POST", headers=self._headers(content_type)
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(str(getattr(exc, "reason", exc))) from None
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WiroError(f"Wiro AI answered {path} with HTTP {status} and no JSON body.") from None
        if not isinstance(body, dict):
            raise WiroError(f"Wiro AI answered {path} with HTTP {status} and an unexpected body.")
        # HTTP 200 alone is not success: Wiro AI reports failures as result: false.
        if status >= 400 or body.get("result") is False:
            text, codes = error_text(body)
            text = text or f"HTTP {status}"
            kind = WiroAuthError if status == 401 or "authorization" in text.lower() else WiroError
            raise kind(f"Wiro AI refused {path}: {text}.{_hint(codes, status, text)}")
        return body

    def post(self, path: str, body: Mapping[str, Any] | None = None, *, timeout: float = REQUEST_TIMEOUT) -> dict:
        try:
            return self._send(path, json.dumps(dict(body or {})).encode("utf-8"), "application/json", timeout)
        except ConnectionError as exc:
            raise WiroError(f"Could not reach Wiro AI ({path}): {exc}. Check the network and try again.") from None

    # -- catalog --------------------------------------------------------------------------

    def search_models(
        self, search: str = "", categories: list[str] | None = None, *, owner: str = "",
        start: int = 0, limit: int = 10, sort: str = "relevance",
    ) -> tuple[list[dict], int]:
        body: dict[str, Any] = {
            "start": str(max(0, int(start))),
            "limit": str(max(1, min(int(limit), 100))),
            "search": search or "",
            "categories": list(categories or []),
            "sort": sort,
            "hideworkflows": True,
            "summary": True,
        }
        if owner:
            body["slugowner"] = owner
        result = self.post("/Tool/List", body)
        rows = [row for row in result.get("tool") or [] if isinstance(row, dict)]
        try:
            total = int(str(result.get("total") or len(rows)))
        except ValueError:
            total = len(rows)
        return rows, total

    def model_detail(self, model: str) -> dict:
        owner, project = parse_model(model)
        key = f"{owner}/{project}"
        now = self._clock()
        with self._detail_lock:
            cached = self._detail_cache.get(key)
            if cached and now - cached[0] < DETAIL_CACHE_SECONDS:
                return cached[1]
        try:
            rows = self.post("/Tool/Detail", {"slugowner": owner, "slugproject": project}).get("tool") or []
        except WiroError as exc:
            if any(code in str(exc) for code in MODEL_MISSING_ERRORS):
                rows = []
            else:
                raise
        if not rows or not isinstance(rows[0], dict):
            raise WiroError(f"Wiro AI has no model {key}. Find model IDs with wiro_search_models.")
        with self._detail_lock:
            self._detail_cache[key] = (now, rows[0])
        return rows[0]

    # -- tasks ----------------------------------------------------------------------------

    def run(self, model: str, params: Mapping[str, Any]) -> tuple[str, str]:
        """Start a task. Returns (task id, task token)."""
        owner, project = parse_model(model)
        path = f"/Run/{owner}/{project}"
        try:
            body = self._send(path, json.dumps(dict(params)).encode("utf-8"), "application/json", REQUEST_TIMEOUT)
        except ConnectionError as exc:
            raise RunOutcomeUnknown(
                f"The run request for {owner}/{project} went out but no answer came back ({exc}). The task may "
                "have started and may be charged: look for it with wiro_task action list before running it again."
            ) from None
        taskid, token = str(body.get("taskid") or ""), str(body.get("socketaccesstoken") or "")
        if not token:
            raise WiroError(
                f"Wiro AI started task {taskid or '(no id)'} but returned no task token. Do not run it again: "
                "follow it with wiro_task, using the task ID."
            )
        return taskid, token

    def task(self, token: str = "", taskid: str = "") -> dict:
        body = {"tasktoken": token} if token else {"taskid": str(taskid)}
        try:
            rows = self.post("/Task/Detail", body).get("tasklist") or []
        except WiroAuthError:
            raise
        except WiroError as exc:
            if any(code in str(exc) for code in TASK_MISSING_ERRORS):
                raise TaskMissing("No task with this reference in your Wiro AI project.") from None
            raise
        if not rows or not isinstance(rows[0], dict):
            raise TaskMissing("No task with this reference in your Wiro AI project.")
        return rows[0]

    def wait(self, token: str, timeout: float, *, taskid: str = "") -> dict:
        """Poll Task/Detail until the task ends: every 2 s, then 5 s after 15 s, then 10 s after a minute."""
        started = self._clock()
        failures = 0
        current: dict = {"id": taskid, "socketaccesstoken": token, "status": "task_queue"}
        while True:
            try:
                current = self.task(token=token, taskid="" if token else taskid)
                failures = 0
                if current.get("status") in TERMINAL_STATUSES:
                    return current
            except (TaskMissing, WiroAuthError):
                raise
            except WiroError:
                failures += 1
                if failures >= MAX_POLL_ERRORS:
                    raise
            elapsed = self._clock() - started
            if elapsed >= timeout:
                raise TaskStillRunning(current)
            self._sleep(min(_poll_interval(elapsed), max(0.5, timeout - elapsed)))

    def stop(self, task: Mapping[str, Any]) -> dict:
        """Cancel a queued task, or kill one that already has a worker."""
        taskid = str(task.get("id") or "")
        token = str(task.get("socketaccesstoken") or "")
        if task.get("status") in QUEUED_STATUSES and taskid:
            try:
                return self.post("/Task/Cancel", {"taskid": taskid})
            except WiroError:
                pass  # it may have left the queue meanwhile; kill it instead
        body = {"taskid": taskid} if taskid else {"socketaccesstoken": token}
        return self.post("/Task/Kill", body)

    def recent_tasks(self, limit: int = 10, model: str = "") -> list[dict]:
        body: dict[str, Any] = {"type": "model", "sort": "id", "order": "DESC", "start": "0",
                                "limit": str(max(1, min(int(limit), 50)))}
        if model:
            body["modelName"] = model
        return [row for row in self.post("/Task/List", body).get("tasklist") or [] if isinstance(row, dict)]

    # -- files ----------------------------------------------------------------------------

    def upload(self, filename: str, data: bytes, mime: str) -> dict:
        """Upload a file to the project's Wiro AI storage. Returns the file row (``url`` among it)."""
        if len(data) > UPLOAD_MAX_BYTES:
            raise WiroError(f"{filename} is larger than the 100 MB upload limit.")
        boundary = f"wiro-hermes-{uuid.uuid4().hex}"
        clean = filename.replace('"', "").replace("\r", "").replace("\n", "").replace("/", "-") or "upload"
        # File/Upload refuses a name the project's storage already holds, so every upload gets its own.
        stem, dot, ext = clean.rpartition(".")
        safe_name = f"{stem}-{uuid.uuid4().hex[:8]}.{ext}" if dot and stem else f"{clean}-{uuid.uuid4().hex[:8]}"
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            f"Content-Type: {mime or 'application/octet-stream'}\r\n\r\n"
        ).encode()
        payload = head + data + f"\r\n--{boundary}--\r\n".encode()
        try:
            body = self._send("/File/Upload", payload, f"multipart/form-data; boundary={boundary}", UPLOAD_TIMEOUT)
        except ConnectionError as exc:
            raise WiroError(f"Could not upload {safe_name} to Wiro AI ({exc}). Check the network and try again.") from None
        rows = [row for row in body.get("list") or [] if isinstance(row, dict)]
        if not rows or not rows[0].get("url"):
            raise WiroError(f"Wiro AI accepted {safe_name} but returned no file URL.")
        return rows[0]

    def download(self, url: str, max_bytes: int) -> tuple[bytes, str] | None:
        """Download an output file from a Wiro AI host. None when it is larger than ``max_bytes``."""
        if not is_wiro_url(url):
            raise WiroError("The output file is not on a Wiro AI host, so it was not downloaded.")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        try:
            with self._download_opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response:
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > max_bytes:
                    return None
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        return None
                    chunks.append(chunk)
        except WiroError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise WiroError(f"Could not download the output file ({getattr(exc, 'reason', exc)}).") from None
        return b"".join(chunks), content_type

    # -- workflows ------------------------------------------------------------------------

    def list_workflows(self, scope: str = "mine", search: str = "", start: int = 0, limit: int = 20) -> tuple[list[dict], int]:
        body: dict[str, Any] = {
            "scope": "my" if scope == "mine" else "public",
            "order": "DESC",
            "start": str(max(0, int(start))),
            "limit": str(max(1, min(int(limit), 100))),
        }
        if search:
            body["search"] = search
        if scope == "mine":
            body["sort"] = "updatetime"
        else:
            body["toolpreview"] = "1"
        result = self.post("/Workflow/List", body)
        rows = [row for row in result.get("workflow") or [] if isinstance(row, dict)]
        try:
            total = int(str(result.get("total") or len(rows)))
        except ValueError:
            total = len(rows)
        return rows, total

    def workflow_detail(self, slug: str) -> dict:
        rows = self.post("/Workflow/Detail", {"slug": slug}).get("workflow") or []
        if not rows or not isinstance(rows[0], dict):
            raise WiroError(f"Wiro AI has no workflow {slug}. Find workflows with wiro_workflows.")
        return rows[0]

    def run_workflow(self, slug: str, inputs: Mapping[str, Any]) -> str:
        body: dict[str, Any] = {"slug": slug}
        if inputs:
            body["input"] = dict(inputs)
        try:
            token = self._send("/Workflow/Run", json.dumps(body).encode("utf-8"), "application/json",
                               REQUEST_TIMEOUT).get("workflowtoken")
        except ConnectionError as exc:
            raise RunOutcomeUnknown(
                f"The run request for workflow {slug} went out but no answer came back ({exc}). The run may have "
                "started and may be charged: check your runs on wiro.ai before starting it again."
            ) from None
        if not token:
            raise WiroError("Wiro AI started the workflow but returned no run token. Check the run on wiro.ai.")
        return str(token)

    def workflow_run(self, token: str) -> dict:
        return self.post("/Workflow/RunDetail", {"workflowtoken": token})

    def wait_workflow(self, token: str, timeout: float) -> dict:
        """Poll RunDetail until the run ends: every 2 s, then 5 s after 30 s, then 10 s after 2 minutes."""
        started = self._clock()
        failures = 0
        body: dict = {"workflowrun": {"workflowtoken": token, "status": "running"}}
        while True:
            try:
                body = self.workflow_run(token)
                failures = 0
                if (body.get("workflowrun") or {}).get("status") in WORKFLOW_FINAL:
                    return body
            except WiroError:
                failures += 1
                if failures >= MAX_POLL_ERRORS:
                    raise
            elapsed = self._clock() - started
            if elapsed >= timeout:
                raise WorkflowStillRunning(body)
            interval = 2 if elapsed < 30 else 5 if elapsed < 120 else 10
            self._sleep(min(interval, max(0.5, timeout - elapsed)))

    # -- account --------------------------------------------------------------------------

    def check_key(self) -> None:
        """One free Task/List read; it fails when the key or the signature is wrong."""
        self.post("/Task/List", {"type": "model", "start": "0", "limit": "1"})


def _poll_interval(elapsed: float) -> float:
    return 2.0 if elapsed < 15 else 5.0 if elapsed < 60 else 10.0
