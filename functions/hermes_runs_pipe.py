"""
title: Hermes Runs Pipe
author: Strob0t
version: 0.5.0
license: AGPL-3.0
description: Runs Open WebUI chats as Hermes runs over /v1/runs, with model discovery, images, tool progress, approval dialogs and one Hermes session per chat.
required_open_webui_version: 0.12.0
"""

import asyncio
import json
import logging
import re
import string
import time
from contextlib import aclosing
from typing import Any, AsyncIterator, Awaitable, Callable, Optional
from urllib.parse import quote

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    from pydantic import BaseModel, Field
except ImportError:
    class BaseModel:
        def __init__(self, **kwargs):
            for cls in reversed(self.__class__.__mro__):
                for k, v in vars(cls).items():
                    if not k.startswith("_") and not callable(v):
                        setattr(self, k, v)
            for k, v in kwargs.items():
                setattr(self, k, v)

    def Field(default=None, **kwargs):
        return default

log = logging.getLogger(__name__)
LOG_PREFIX = "hermes runs pipe"

# Open WebUI loads a function from one file, so this module keeps all parts. Sections, top to bottom:
# constants and UI texts, pure helpers (policies, models, SSE, text), request context, the Pipe class.

# --- Protocol constants -------------------------------------------------------------------------
DEFAULT_MODEL_ID = "hermes-default"
MODEL_OPTIONS_FIELD = "model_options"
EFFORT_OPTION = "reasoning_effort"
EFFORT_OFF_VALUES = ("none", "off")
EFFORT_NONE = "none"
POLICY_KEYS = ("approval_mode", "allowed_approval_choices", "auto_choice", "allowed_roles")
APPROVAL_CHOICES = ("once", "session", "always", "deny")
APPROVAL_MODES = ("ask", "auto", "deny")
DEFAULT_EVENT_CHOICES = ["once", "deny"]
CHOICE_DENY = "deny"
CHOICE_ONCE = "once"
CHOICE_ALIASES = {"approve": CHOICE_ONCE, "approved": CHOICE_ONCE, "allow": CHOICE_ONCE}
SLASH_COMMANDS = ("reset", "approve", "deny")
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted")
RUN_END_EVENTS = ("run.failed", "run.cancelled", "run.interrupted")
FATAL_STREAM_STATUSES = (401, 403, 404)
MAX_RECONNECTS = 3
NETWORK_ERRORS: tuple = (OSError, asyncio.TimeoutError) + ((aiohttp.ClientError,) if aiohttp else ())

HEADER_SESSION_KEY = "X-Hermes-Session-Key"
HEADER_USER_ROLE = "X-Hermes-User-Role"
HEADER_USER_NAME = "X-Hermes-User-Name"
HEADER_SESSION_ID = "X-Hermes-Session-Id"
HEADER_IDEMPOTENCY = "Idempotency-Key"
HEADER_LAST_EVENT_ID = "Last-Event-ID"
SESSION_KEY_PREFIX = "owui"
IDEMPOTENCY_KEY_LIMIT = 255

# --- Image input --------------------------------------------------------------------------------
# Open WebUI v0.12.4 puts the images of a user message into its content as image_url parts with data URLs
# (utils/middleware.py). Image files in __files__ or body["files"] are a fallback (type "image" or a raster
# content type, like the middleware). A local file URL is a file id or /api/v1/files/<id>/content.
IMAGE_PART_TYPES = ("image_url", "input_image")
IMAGE_FILE_TYPE = "image"
IMAGE_MIME_PREFIX = "image/"
SVG_MIME = "image/svg+xml"
DATA_IMAGE_PREFIX = "data:image/"
HTTPS_PREFIX = "https://"
BASE64_MARK = ";base64"
LOCAL_FILE_URL = re.compile(r"^(?:/api/v1/files/[^/?#]+/content(?:[?#].*)?|[0-9A-Za-z-]+)$")
BYTES_PER_MB = 1024 * 1024
DEFAULT_IMAGE_MAX_COUNT = 4
DEFAULT_IMAGE_MAX_BYTES = 8 * BYTES_PER_MB

# --- Display limits (characters) ----------------------------------------------------------------
ERROR_TEXT_LIMIT = 300
STATUS_LINE_LIMIT = 120
ROUTE_STATUS_LIMIT = 100
INVALID_INPUT_PREVIEW = 40
ANSWER_PREVIEW = 200

# --- UI texts -----------------------------------------------------------------------------------
# Every text that a user sees. The valve TEXTS (JSON object with the same keys) overrides single
# entries, for example for a German UI. Placeholders in {braces} are filled with str.format().
TEXTS = {
    "default_model_name": "Hermes (gateway default)",
    "approval_title": "Hermes needs an approval",
    "denied": "This model is not enabled for your account",
    "reset": "Session reset",
    "no_pending": "No open approval in this chat.",
    "no_input": "No user message found.",
    "images_disabled": "*({count} image(s) not sent: images are off.)*\n\n",
    "images_too_many": "*({count} image(s) not sent: at most {max_count} images per message.)*\n\n",
    "images_too_big": "*({count} image(s) not sent: larger than {max_mb} MB.)*\n\n",
    "images_unreadable": "*({count} image(s) not sent: unsupported or unreadable source.)*\n\n",
    "unreachable": "Hermes is not reachable ({base})",
    "unreachable_detail": "Hermes is not reachable ({base}): {error}",
    "run_rejected": "Hermes rejected the run (HTTP {status}: {detail})",
    "no_run_id": "no run_id",
    "pipe_error": "\n\nError in the Hermes runs pipe: {error}",
    "working": "Hermes is working",
    "route": "Route: {route}",
    "invalid_choice": "Invalid choice `{choice}`. {hint}",
    "approve_hint": "Answer with `/approve [{choices}]` or `/deny`.",
    "deny_hint": "Answer with `/deny`.",
    "approval_sent": "\n\n*(Approval: {choice}{reason})*\n\n",
    "approval_not_sent": "\n\n*(Approval `{choice}` not sent: {error})*\n\n",
    "reason_separator": ", ",
    "waiting_approval": "Waiting for approval",
    "approval_answer": "{message}\n\nAnswer: {choices}",
    "invalid_input": "invalid input `{raw}`",
    "automatic": "automatic",
    "dialog_timeout": "timed out",
    "dialog_failed": "dialog failed",
    "dialog_no_answer": "dialog without answer",
    "idle_status": "Hermes does not respond",
    "idle_text": "\n\nNo data from Hermes for {seconds} s, run stopped.",
    "lost_status": "Lost the connection to the progress",
    "lost_text": "\n\nLost the connection to the progress (run `{run_id}`, status {state}). "
                 "The run may continue in Hermes.",
    "run_ended": "\n\nRun {state}: {error}",
    "no_error_text": "no error text",
    "unknown": "unknown",
    "tool_started": "{tool}: {preview}",
    "tool_done": "{tool} done ({duration} s{error})",
    "tool_error_suffix": ", error",
    "tool_fallback_name": "tool",
    "subagent_start": "Subagent started",
    "subagent_done": "Subagent done",
    "replay_truncated": "\n\n*(Parts of the progress are missing)*\n\n",
    "done": "Done",
    "run.failed": "Run failed",
    "run.cancelled": "Run cancelled",
    "run.interrupted": "Run interrupted",
    "run_end_with_error": "\n\n{label}: {error}",
    "run_end_plain": "\n\n{label}.",
    "clarify_title": "Question from Hermes",
    "clarify_default": "Hermes has a question.",
    "clarify_options": "{question}\n\nOptions: {choices}",
    "clarify_no_dialog": "\n\n**Question from Hermes:** {message}\n\n*(No answer possible without a dialog.)*\n\n",
    "clarify_unanswered": "\n\n*(Question not answered: {reason})*\n\n",
    "empty_answer": "empty answer",
    "clarify_not_sent": "\n\n*(Answer to Hermes not sent: {error})*\n\n",
    "clarify_sent": "\n\n*(Answer to Hermes: {answer})*\n\n",
    "or": " or ",
}
DEFAULT_MODEL = {"id": DEFAULT_MODEL_ID, "name": TEXTS["default_model_name"], "default": True}


def parse_texts(raw: Any) -> dict:
    """Parses the TEXTS valve: JSON object of text overrides. Unknown keys and non-string values are dropped.
    Returns the merged texts; on invalid JSON the defaults with a warning.
    """
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
    except ValueError as e:
        log.warning("%s: TEXTS is not valid JSON, using the defaults: %s", LOG_PREFIX, e)
        return dict(TEXTS)
    if not isinstance(data, dict):
        log.warning("%s: TEXTS is not a JSON object, using the defaults", LOG_PREFIX)
        return dict(TEXTS)
    return {**TEXTS, **{k: v for k, v in data.items() if k in TEXTS and isinstance(v, str)}}


def _csv(value: Any) -> list[str]:
    """Returns the lower-case, unique, non-empty items of a comma-separated string or a list."""
    items = value.split(",") if isinstance(value, str) else (value or [])
    out = []
    for item in items:
        item = str(item).strip().lower()
        if item and item not in out:
            out.append(item)
    return out


def parse_policies(raw: Any) -> list[dict]:
    """Parses the MODEL_POLICIES valve: JSON list of {"match": <regex>, <policy keys>...}.
    Returns [] and logs a warning if the value is not valid JSON or not a list.
    Entries without a usable "match" regex are dropped.
    """
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or [])
    except ValueError as e:
        log.warning("%s: MODEL_POLICIES is not valid JSON, using none: %s", LOG_PREFIX, e)
        return []
    if not isinstance(data, list):
        log.warning("%s: MODEL_POLICIES is not a JSON list, using none", LOG_PREFIX)
        return []
    policies = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("match"), str):
            continue
        try:
            pattern = re.compile(entry["match"])
        except re.error as e:
            log.warning("%s: MODEL_POLICIES regex %r invalid: %s", LOG_PREFIX, entry["match"], e)
            continue
        policies.append({"match": pattern, **{k: entry[k] for k in POLICY_KEYS if entry.get(k)}})
    return policies


def policy_for(model_id: str, policies: list[dict]) -> dict:
    """Returns the policy keys of the first policy whose regex matches the model id (re.search), else {}."""
    for policy in policies:
        if policy["match"].search(model_id):
            return {k: v for k, v in policy.items() if k != "match"}
    return {}


def parse_model_list(data: Any, id_filter: str = "", owned_by_csv: str = "") -> list[dict]:
    """Builds the manifold entries from an OpenAI-style /models response.
    Keeps id, name (else id), owned_by and provider. Applies the id regex (re.search) and the owned_by list.
    """
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    pattern = None
    if id_filter and id_filter.strip():
        try:
            pattern = re.compile(id_filter.strip())
        except re.error as e:
            log.warning("%s: MODEL_FILTER regex invalid, ignoring it: %s", LOG_PREFIX, e)
    owners = _csv(owned_by_csv)
    models = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            continue
        model_id = item["id"].strip()
        owned_by = str(item.get("owned_by") or "")
        if pattern is not None and not pattern.search(model_id):
            continue
        if owners and owned_by.lower() not in owners:
            continue
        entry = {"id": model_id, "name": str(item.get("name") or model_id), "owned_by": owned_by}
        if isinstance(item.get("provider"), str) and item["provider"].strip():
            entry["provider"] = item["provider"].strip()
        models.append(entry)
    return models


def run_model_options(body: Any, keys_csv: Any) -> dict:
    """Returns the model_options for POST /runs: the keys of body.model_options in the allowlist, values as given.
    A top-level reasoning_effort (the Open WebUI slider of connection models) fills model_options.reasoning_effort
    if model_options has none. Keys outside the allowlist are dropped; a top-level None counts as absent.
    The thinking switch: a reasoning_effort "none" or "off" (any case) goes as "none", which turns thinking off.
    """
    if not isinstance(body, dict):
        return {}
    allowed = _csv(keys_csv)
    raw = body.get(MODEL_OPTIONS_FIELD)
    options = {k: v for k, v in raw.items() if k in allowed} if isinstance(raw, dict) else {}
    if EFFORT_OPTION in allowed and EFFORT_OPTION not in options and body.get(EFFORT_OPTION) is not None:
        options[EFFORT_OPTION] = body[EFFORT_OPTION]
    effort = options.get(EFFORT_OPTION)
    if isinstance(effort, str) and effort.strip().lower() in EFFORT_OFF_VALUES:
        options[EFFORT_OPTION] = EFFORT_NONE
    return options


def session_key(user_id: str, chat_id: str, reset_count: int = 0) -> str:
    """Returns the X-Hermes-Session-Key value owui:<user_id>:<chat_id>, with :r<n> after a /reset."""
    key = f"{SESSION_KEY_PREFIX}:{user_id}:{chat_id}"
    return f"{key}:r{reset_count}" if reset_count else key


def idempotency_key(chat_id: str, message_id: str) -> str:
    """Returns owui:<chat_id>:<message_id> with only visible ASCII characters, at most IDEMPOTENCY_KEY_LIMIT.
    Returns "" if an id is missing; then the pipe sends no Idempotency-Key header.
    """
    if not chat_id or not message_id:
        return ""
    raw = f"{SESSION_KEY_PREFIX}:{chat_id}:{message_id}"
    return "".join(c for c in raw if "!" <= c <= "~")[:IDEMPOTENCY_KEY_LIMIT]


def _event_choices(event_choices: Any) -> list[str]:
    if not isinstance(event_choices, (list, tuple)) or not event_choices:
        return list(DEFAULT_EVENT_CHOICES)
    return [str(c).strip().lower() for c in event_choices]


def offered_choices(event_choices: Any, allowed_csv: Any) -> list[str]:
    """Returns the choices in both the event and the allowed list, in the order of the allowed list."""
    event = _event_choices(event_choices)
    return [c for c in _csv(allowed_csv) if c in APPROVAL_CHOICES and c in event]


def auto_choice(event_choices: Any, wanted: str) -> str:
    """Returns wanted if the event offers it, else once, else the first event choice."""
    choices = _event_choices(event_choices)
    wanted = str(wanted or "").strip().lower()
    if wanted in choices:
        return wanted
    if CHOICE_ONCE in choices:
        return CHOICE_ONCE
    return choices[0]


def _sse_data(lines: list[str]) -> Optional[dict]:
    if not lines:
        return None
    try:
        data = json.loads("\n".join(lines))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def parse_sse(byte_iter: Any) -> AsyncIterator[tuple[Optional[str], dict]]:
    """Parses a text/event-stream byte stream.
    Yields (id, data) for each frame whose data is a JSON object; id is None if the frame has no id.
    Comments, other fields, data that is not a JSON object and an unterminated last frame are ignored.
    """
    buf = b""
    frame_id: Optional[str] = None
    data: list[str] = []
    async for chunk in byte_iter:
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for raw in lines:
            line = raw.rstrip(b"\r").decode("utf-8", "replace")
            if not line:
                parsed = _sse_data(data)
                if parsed is not None:
                    yield frame_id, parsed
                frame_id, data = None, []
            elif not line.startswith(":"):
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "id":
                    frame_id = value
                elif field == "data":
                    data.append(value)


def _last_user_content(messages: Any) -> Any:
    """Returns the content of the last user message, else None."""
    if not isinstance(messages, list):
        return None
    for item in reversed(messages):
        if isinstance(item, dict) and item.get("role") == "user":
            return item.get("content")
    return None


def last_user_text(messages: Any) -> str:
    """Returns the text of the last user message (string content or the text parts of a list)."""
    content = _last_user_content(messages)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def _part_url(part: dict) -> Any:
    """Returns the URL of an image part: image_url as {"url": ...} (Chat Completions) or as a string."""
    ref = part.get("image_url")
    return ref.get("url") if isinstance(ref, dict) else ref


def _is_image_file(item: dict) -> bool:
    """Returns True for a file of type "image" or with a raster image content type (not SVG)."""
    mime = str(item.get("content_type") or "").split(";")[0].strip().lower()
    return item.get("type") == IMAGE_FILE_TYPE or (mime.startswith(IMAGE_MIME_PREFIX) and mime != SVG_MIME)


def image_urls(messages: Any, files: Any = None) -> list[str]:
    """Returns the unique image URLs of the last user message (image_url and input_image parts), then of the
    image files, in this order. Empty and non-string URLs are dropped.
    """
    content = _last_user_content(messages)
    parts = content if isinstance(content, list) else []
    urls = [_part_url(p) for p in parts if isinstance(p, dict) and p.get("type") in IMAGE_PART_TYPES]
    urls += [f.get("url") for f in (files if isinstance(files, list) else []) if isinstance(f, dict) and _is_image_file(f)]
    out: list[str] = []
    for url in urls:
        if isinstance(url, str) and url and url not in out:
            out.append(url)
    return out


def image_url_size(url: Any) -> Optional[int]:
    """Returns the decoded byte size of a data:image URL, 0 for an https URL (size not known) and None for
    every other source. Only the prefix is lower-cased, so a large data URL is not copied.
    """
    if not isinstance(url, str):
        return None
    head = url[:len(DATA_IMAGE_PREFIX)].lower()
    if head.startswith(HTTPS_PREFIX):
        return 0
    comma = url.find(",")
    if not head.startswith(DATA_IMAGE_PREFIX) or comma < 0:
        return None
    size = len(url) - comma - 1
    if not url[:comma].lower().endswith(BASE64_MARK):
        return size
    padding = 2 if url.endswith("==") else 1 if url.endswith("=") else 0
    return (size - padding) * 3 // 4


def run_input(text: str, urls: list[str]) -> Any:
    """Returns the POST /runs input: the text as given without images, else one user message with a text part
    (only for a non-empty text) and one image_url part per URL.
    """
    if not urls:
        return text
    parts: list[dict] = [{"type": "text", "text": text}] if text.strip() else []
    parts += [{"type": "image_url", "image_url": {"url": url}} for url in urls]
    return [{"role": "user", "content": parts}]


def role_allowed(role: Any, allowed_csv: Any) -> bool:
    """Returns True if the role is in the comma-separated list; "*" allows all roles."""
    allowed = _csv(allowed_csv)
    return "*" in allowed or (isinstance(role, str) and role.strip().lower() in allowed)


def parse_slash(text: Any) -> Optional[tuple[str, str]]:
    """Returns ("reset"|"approve"|"deny", argument) for a pipe chat command, else None."""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(None, 1)
    if not parts or not parts[0].startswith("/"):
        return None
    command = parts[0][1:].lower()
    if command not in SLASH_COMMANDS:
        return None
    return command, parts[1].strip() if len(parts) > 1 else ""


def remaining_output(output: Any, streamed: str) -> str:
    """Returns the part of the final run output that the stream did not show yet."""
    if not isinstance(output, str) or not output or output in streamed:
        return ""
    if output.startswith(streamed):
        return output[len(streamed):]
    for k in range(min(len(output), len(streamed)), 0, -1):
        if streamed.endswith(output[:k]):
            return output[k:]
    return f"\n\n{output}" if streamed else output


def _header_text(value: Any) -> str:
    """Returns the value URL-quoted like Open WebUI custom headers such as {{USER_NAME}} (utils/headers.py)."""
    return quote(str(value or "").strip(), safe=string.punctuation + " ")


def _error_text(data: Any, limit: int = ERROR_TEXT_LIMIT) -> str:
    """Returns the error message of a Hermes/OpenAI error body, shortened."""
    if isinstance(data, dict):
        err = data.get("error")
        data = err.get("message") if isinstance(err, dict) else err or data.get("message") or ""
    return re.sub(r"\s+", " ", str(data or "")).strip()[:limit]


def _or_list(items: list[str], or_word: str = TEXTS["or"]) -> str:
    """Returns "a, b or c" (or_word carries the spaces)."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + or_word + items[-1]


class _IdleTimeout(Exception):
    pass


async def _idle_limited(frames: Any, idle: float) -> AsyncIterator[Any]:
    """Yields the items of frames; raises _IdleTimeout if no item comes in idle seconds."""
    it = frames.__aiter__()
    try:
        while True:
            try:
                item = await asyncio.wait_for(it.__anext__(), idle)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                raise _IdleTimeout() from None
            yield item
    finally:
        if hasattr(it, "aclose"):
            await it.aclose()


class _HttpError(Exception):
    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"HTTP {status}: {detail}" if detail else f"HTTP {status}")
        self.status = status
        self.detail = detail


class _Ctx:
    """Values of one pipe call: ids, user, effective model settings and request headers."""

    def __init__(self, pipe: "Pipe", body: dict, user: dict, metadata: dict):
        v = pipe.valves
        self.base = str(v.HERMES_BASE_URL or "").rstrip("/")
        self.model = str(body.get("model") or "")
        self.stream = bool(body.get("stream"))
        self.user_id = str(user.get("id") or "")
        self.chat_id = str(metadata.get("chat_id") or "")
        self.message_id = str(metadata.get("message_id") or "")
        self.role = str(user.get("role") or "")
        self.name = str(user.get("name") or "")
        self.key = (self.user_id, self.chat_id)
        self.short_model = pipe.short_model_id(self.model)
        entry = pipe.model_entry(self.short_model)
        self.run_model = "" if not entry or entry.get("default") else str(entry.get("id") or "")
        self.run_provider = str(entry.get("provider") or "") if entry else ""
        self.model_options = run_model_options(body, v.MODEL_OPTIONS_KEYS)
        policy = policy_for(self.short_model, pipe.policies())
        mode = str(policy.get("approval_mode") or v.APPROVAL_MODE or "").strip().lower()
        if mode not in APPROVAL_MODES:
            log.warning("%s: unknown approval mode %r, using ask", LOG_PREFIX, mode)
            mode = "ask"
        self.approval_mode = mode
        self.allowed_choices = policy.get("allowed_approval_choices") or v.ALLOWED_APPROVAL_CHOICES
        self.auto_choice = str(policy.get("auto_choice") or v.AUTO_CHOICE)
        self.allowed_roles = policy.get("allowed_roles") or v.ALLOWED_ROLES

    def run_body(self, user_input: Any) -> dict:
        """Returns the POST /runs body; model and provider only for a discovered model, not for the default entry.
        user_input is the text, or a list with one user message for images (see run_input).
        model_options (MODEL_OPTIONS_KEYS allowlist) only if the request has at least one allowed option.
        """
        body: dict = {"input": user_input, "stream": False}
        if self.run_model:
            body["model"] = self.run_model
            if self.run_provider:
                body["provider"] = self.run_provider
        if self.model_options:
            body[MODEL_OPTIONS_FIELD] = dict(self.model_options)
        return body

    def headers(self, pipe: "Pipe", **extra: str) -> dict:
        h = {"Accept-Encoding": "identity"}
        if pipe.valves.HERMES_API_KEY:
            h["Authorization"] = f"Bearer {pipe.valves.HERMES_API_KEY}"
        if self.chat_id:
            h[HEADER_SESSION_KEY] = session_key(self.user_id, self.chat_id, pipe._resets.get(self.key, 0))
        if self.role:
            h[HEADER_USER_ROLE] = _header_text(self.role)
        if self.name:
            h[HEADER_USER_NAME] = _header_text(self.name)
        h.update({k: val for k, val in extra.items() if val})
        return h


class _Run:
    """State of the Hermes run that one pipe call follows."""

    def __init__(self, run_id: str = "", last_id: Optional[str] = None, streamed: str = ""):
        self.run_id = run_id
        self.last_id = last_id
        self.streamed = streamed
        self.sent_choice = ""
        self.done = False
        self.parked = False


class Pipe:
    class Valves(BaseModel):
        HERMES_BASE_URL: str = Field(
            default="http://hermes-gw.hermes:8642/v1",
            description="Base URL of the Hermes API (with /v1)",
        )
        HERMES_API_KEY: str = Field(
            default="",
            description="Bearer token for Hermes; empty = no Authorization header",
        )
        MODEL_DISCOVERY: bool = Field(
            default=True,
            description="Load the manifold models from GET <HERMES_BASE_URL>/models; off = one entry with the gateway default",
        )
        MODEL_FILTER: str = Field(
            default="",
            description="Regex on the model id (search); only matching ids are listed; empty = all",
        )
        OWNED_BY_FILTER: str = Field(
            default="",
            description="Comma-separated owned_by values to list; empty = all",
        )
        MODELS_CACHE_TTL: int = Field(
            default=300,
            description="Seconds to cache the model list; on a fetch error the last list stays",
        )
        MODEL_POLICIES: str = Field(
            default="[]",
            description='JSON list of {"match": <regex on model id>, "approval_mode", "allowed_approval_choices", "auto_choice", "allowed_roles"}; first match wins',
        )
        MODEL_OPTIONS_KEYS: str = Field(
            default=EFFORT_OPTION,
            description="Comma-separated keys of body.model_options that go to POST /runs (a top-level reasoning_effort fills reasoning_effort; none or off turns thinking off); other keys are dropped; empty = none",
        )
        IMAGES_ENABLED: bool = Field(
            default=True,
            description="Send the images of the last user message to Hermes; off = drop them with a note",
        )
        IMAGE_MAX_COUNT: int = Field(
            default=DEFAULT_IMAGE_MAX_COUNT,
            description="Maximum images per message; more are dropped with a note",
        )
        IMAGE_MAX_BYTES: int = Field(
            default=DEFAULT_IMAGE_MAX_BYTES,
            description="Maximum decoded bytes per image (data URL); a larger image is dropped with a note",
        )
        ROUTE_HEADER: str = Field(
            default="X-Hermes-Route",
            description="Response header of POST /runs that is shown as a status line (routing proxies); empty = off",
        )
        REQUEST_TIMEOUT: int = Field(
            default=30,
            description="Seconds for POST /runs, /approval, /clarify, /stop and GET /runs/{id}",
        )
        STREAM_IDLE_TIMEOUT: int = Field(
            default=900,
            description="Seconds without an event on the event stream (keepalives do not count), then stop and error",
        )
        APPROVAL_MODE: str = Field(
            default="ask",
            description="ask = dialog, auto = AUTO_CHOICE without dialog, deny = always deny",
        )
        ALLOWED_APPROVAL_CHOICES: str = Field(
            default="once,deny,session",
            description="Approval choices the pipe offers (intersection with the Hermes choices, in this order)",
        )
        AUTO_CHOICE: str = Field(
            default="session",
            description="Choice for APPROVAL_MODE=auto; once if Hermes does not offer it",
        )
        APPROVAL_TIMEOUT: int = Field(
            default=300,
            description="Seconds for an approval or clarify dialog; an approval is denied after this time",
        )
        SHOW_TOOL_PROGRESS: bool = Field(
            default=True,
            description="Show tool events as status and as reasoning block",
        )
        ALLOWED_ROLES: str = Field(
            default="admin",
            description="Comma-separated Open WebUI roles that can use the pipe; * = all",
        )
        TEXTS: str = Field(
            default="{}",
            description='JSON object that overrides UI texts by key (see TEXTS in the code), e.g. {"working": "Hermes arbeitet"}',
        )
        DEBUG: bool = Field(
            default=False,
            description="Enable debug logging",
        )

    def __init__(self):
        self.type = "manifold"
        self.valves = self.Valves()
        self._client_factory: Callable[[], Any] = self._default_client
        self._file_resolver: Callable[[str, str], Awaitable[Optional[str]]] = self._owui_file_image
        self._now: Callable[[], float] = time.monotonic
        self._resets: dict[tuple, int] = {}
        self._pending: dict[tuple, dict] = {}
        self._models: list[dict] = []
        self._models_at: Optional[float] = None
        self._models_lock = asyncio.Lock()

    @staticmethod
    def _default_client() -> Any:
        if aiohttp is None:
            raise RuntimeError("aiohttp is not installed")
        return aiohttp.ClientSession()

    @staticmethod
    async def _owui_file_image(url: str, user_id: str) -> Optional[str]:
        """Returns the data URL of an Open WebUI file (file id or /api/v1/files/<id>/content) with the access check
        of Open WebUI v0.12.4 (utils/files.py, get_image_base64_from_url). None outside Open WebUI, for an unknown
        user or file, or without access.
        """
        try:
            from open_webui.models.users import Users
            from open_webui.utils.files import get_image_base64_from_url
        except ImportError:
            return None
        try:
            user = await Users.get_user_by_id(user_id) if user_id else None
            return await get_image_base64_from_url(url, user=user) if user else None
        except Exception as e:
            log.warning("%s: image file %s not readable: %s", LOG_PREFIX, url[:INVALID_INPUT_PREVIEW], e)
            return None

    def policies(self) -> list[dict]:
        return parse_policies(self.valves.MODEL_POLICIES)

    def _t(self, key: str, **values: Any) -> str:
        """Returns the UI text for key (valve TEXTS over the defaults) with the placeholders filled."""
        text = parse_texts(self.valves.TEXTS).get(key, TEXTS[key])
        try:
            return text.format(**values)
        except (KeyError, IndexError, ValueError) as e:
            log.warning("%s: TEXTS entry %r has a bad placeholder (%s), using the default", LOG_PREFIX, key, e)
            return TEXTS[key].format(**values)

    @staticmethod
    def short_model_id(model: str) -> str:
        """Returns the manifold entry id of an Open WebUI model id (<function id>.<entry id>)."""
        return model.split(".", 1)[1] if "." in model else model

    def _discovery_headers(self) -> dict:
        h = {"Accept": "application/json"}
        if self.valves.HERMES_API_KEY:
            h["Authorization"] = f"Bearer {self.valves.HERMES_API_KEY}"
        return h

    async def _fetch_models(self) -> list[dict]:
        """Loads GET <base>/models and applies the filters. Raises on network or HTTP errors."""
        base = str(self.valves.HERMES_BASE_URL or "").rstrip("/")
        async with self._client_factory() as client:
            async with client.get(f"{base}/models", headers=self._discovery_headers(),
                                  timeout=self._timeout()) as resp:
                if resp.status != 200:
                    raise _HttpError(resp.status, _error_text(await self._json(resp)))
                data = await self._json(resp)
        return parse_model_list(data, self.valves.MODEL_FILTER, self.valves.OWNED_BY_FILTER)

    async def models(self) -> list[dict]:
        """Returns the manifold entries: the discovered list (cached MODELS_CACHE_TTL seconds), the last
        list after a fetch error, else the single default entry (gateway default, no model in the run body).
        """
        if not self.valves.MODEL_DISCOVERY:
            return [dict(DEFAULT_MODEL)]
        async with self._models_lock:
            now = self._now()
            fresh = self._models_at is not None and now - self._models_at < max(0, self.valves.MODELS_CACHE_TTL)
            if not fresh:
                try:
                    models = await self._fetch_models()
                except Exception as e:
                    log.warning("%s: model discovery failed (%s), %s", LOG_PREFIX, e,
                                "keeping the last list" if self._models else "using the default entry")
                else:
                    if not models:
                        log.warning("%s: model discovery returned no model after the filters", LOG_PREFIX)
                    self._models, self._models_at = models, now
            return [dict(m) for m in self._models] or [dict(DEFAULT_MODEL)]

    async def pipes(self) -> list[dict]:
        return [{"id": m["id"], "name": m["name"]} for m in await self.models()]

    def model_entry(self, short_id: str) -> dict:
        """Returns the cached manifold entry for a short model id; the default entry for its id; else {}.
        A model that is not in the cache (for example after a restart) is sent to Hermes as given.
        """
        if short_id == DEFAULT_MODEL["id"]:
            return dict(DEFAULT_MODEL)
        for entry in self._models:
            if entry["id"] == short_id:
                return dict(entry)
        return {"id": short_id} if short_id else {}

    def _timeout(self, total: Optional[float] = None) -> Any:
        if total is None:
            total = self.valves.REQUEST_TIMEOUT
        if aiohttp is None:
            return total
        if total == 0:
            return aiohttp.ClientTimeout(total=None, sock_connect=self.valves.REQUEST_TIMEOUT)
        return aiohttp.ClientTimeout(total=total)

    @staticmethod
    async def _json(resp: Any) -> Any:
        try:
            return await resp.json(content_type=None)
        except Exception:
            return {}

    async def _request(self, client: Any, method: str, ctx: _Ctx, path: str,
                       payload: Optional[dict] = None, **headers: str) -> tuple[int, Any, Any]:
        """Sends one JSON request to Hermes. Returns (status, JSON body, response headers)."""
        kwargs = {"headers": ctx.headers(self, **headers), "timeout": self._timeout()}
        if payload is not None:
            kwargs["json"] = payload
        async with getattr(client, method)(f"{ctx.base}{path}", **kwargs) as resp:
            return resp.status, await self._json(resp), getattr(resp, "headers", {}) or {}

    async def _send(self, client: Any, ctx: _Ctx, run: _Run, kind: str, payload: dict) -> tuple[int, str]:
        """Posts an approval or clarify answer.
        Returns (HTTP status, "") on success, else (HTTP status or 0 for a network error, error text).
        """
        try:
            status, data, _ = await self._request(client, "post", ctx, f"/runs/{run.run_id}/{kind}", payload)
        except NETWORK_ERRORS as e:
            return 0, self._t("unreachable_detail", base=ctx.base, error=type(e).__name__)
        if status >= 300:
            return status, f"HTTP {status}: {_error_text(data)}".rstrip(": ")
        return status, ""

    async def _stop(self, client: Any, ctx: _Ctx, run: _Run) -> None:
        try:
            await self._request(client, "post", ctx, f"/runs/{run.run_id}/stop", {})
        except Exception as e:
            log.warning("%s: stop of run %s failed: %s", LOG_PREFIX, run.run_id, e)

    @staticmethod
    async def _status(emit: Optional[Callable], description: str, done: bool = False) -> None:
        if emit is None:
            return
        try:
            await emit({"type": "status", "data": {"description": description, "done": done}})
        except Exception as e:
            log.debug("%s: status event failed: %s", LOG_PREFIX, e)

    @staticmethod
    def _chunk(ctx: _Ctx, field: str, text: str) -> dict:
        """Returns an OpenAI chat.completion.chunk with delta.<field> = text."""
        return {
            "object": "chat.completion.chunk",
            "model": ctx.model,
            "choices": [{"index": 0, "delta": {field: text}, "finish_reason": None}],
        }

    def _reasoning(self, ctx: _Ctx, text: str) -> Optional[dict]:
        """Returns a delta.reasoning_content chunk; None if the request does not stream.
        Without stream, Open WebUI joins str() of all chunks, so a dict would show as text.
        """
        if not ctx.stream or not text:
            return None
        return self._chunk(ctx, "reasoning_content", text)

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
        __event_call__: Optional[Callable[[dict], Awaitable[Any]]] = None,
        __files__: Optional[list] = None,
    ) -> AsyncIterator[Any]:
        try:
            body = body if isinstance(body, dict) else {}
            ctx = _Ctx(self, body, __user__ or {}, __metadata__ or {})
            if (__metadata__ or {}).get("task"):
                # Title, tag and follow-up tasks would add turns to the Hermes session of the chat.
                yield ""
                return
            if not role_allowed(ctx.role, ctx.allowed_roles):
                yield self._t("denied")
                return
            text = last_user_text(body.get("messages"))
            slash = parse_slash(text)
            if slash and slash[0] == "reset":
                self._resets[ctx.key] = self._resets.get(ctx.key, 0) + 1
                self._pending.pop(ctx.key, None)
                yield self._t("reset")
                return
            if slash and ctx.key not in self._pending:
                yield self._t("no_pending")
                return
            urls: list[str] = []
            if not slash:
                files = (__files__ if isinstance(__files__, list) else []) + (
                    body["files"] if isinstance(body.get("files"), list) else [])
                urls, notes = await self._images(ctx, image_urls(body.get("messages"), files))
                for note in notes:
                    yield note
            if not slash and not text.strip() and not urls:
                yield self._t("no_input")
                return
            async with self._client_factory() as client:
                if slash:
                    chunks = self._answer_pending(client, ctx, slash, __event_emitter__, __event_call__)
                else:
                    chunks = self._start(client, ctx, run_input(text, urls), __event_emitter__, __event_call__)
                async with aclosing(chunks):
                    async for chunk in chunks:
                        if ctx.stream and isinstance(chunk, str) and chunk.startswith("data:"):
                            # Open WebUI passes a str chunk that starts with "data:" on as a raw SSE line.
                            chunk = self._chunk(ctx, "content", chunk)
                        yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            raise
        except Exception as e:
            log.exception("%s failed", LOG_PREFIX)
            yield self._t("pipe_error", error=f"{type(e).__name__}: {e}")

    async def _images(self, ctx: _Ctx, urls: list[str]) -> tuple[list[str], list[str]]:
        """Applies IMAGES_ENABLED, IMAGE_MAX_COUNT and IMAGE_MAX_BYTES to the image URLs.
        A local file URL becomes a data URL through _file_resolver. Returns (URLs for the run, notes about the
        dropped images, one per reason).
        """
        if not urls:
            return [], []
        if not self.valves.IMAGES_ENABLED:
            return [], [self._t("images_disabled", count=len(urls))]
        max_count = max(0, int(self.valves.IMAGE_MAX_COUNT))
        max_bytes = int(self.valves.IMAGE_MAX_BYTES)
        kept: list[str] = []
        dropped = {"images_too_many": 0, "images_too_big": 0, "images_unreadable": 0}
        for url in urls:
            if len(kept) >= max_count:
                dropped["images_too_many"] += 1
                continue
            if LOCAL_FILE_URL.match(url):
                url = await self._file_resolver(url, ctx.user_id) or ""
            size = image_url_size(url)
            if size is None:
                dropped["images_unreadable"] += 1
            elif size > max_bytes:
                dropped["images_too_big"] += 1
            elif url not in kept:
                kept.append(url)
        max_mb = f"{round(max_bytes / BYTES_PER_MB, 1):g}"
        notes = [self._t(key, count=count, max_count=max_count, max_mb=max_mb) for key, count in dropped.items() if count]
        if notes:
            log.info("%s: %s of %s image(s) dropped", LOG_PREFIX, sum(dropped.values()), len(urls))
        return kept, notes

    async def _start(self, client: Any, ctx: _Ctx, user_input: Any, emit, call) -> AsyncIterator[Any]:
        """Starts a run with the last user message (text, or text and images) and follows it."""
        try:
            status, data, headers = await self._request(
                client, "post", ctx, "/runs", ctx.run_body(user_input),
                **{HEADER_IDEMPOTENCY: idempotency_key(ctx.chat_id, ctx.message_id)})
        except NETWORK_ERRORS as e:
            log.warning("%s: POST /runs failed: %s", LOG_PREFIX, e)
            yield self._t("unreachable", base=ctx.base)
            return
        run_id = data.get("run_id") if isinstance(data, dict) else None
        if status >= 300 or not isinstance(run_id, str) or not run_id:
            yield self._t("run_rejected", status=status, detail=_error_text(data) or self._t("no_run_id"))
            return
        if self.valves.DEBUG:
            log.debug("%s: run %s, session %s", LOG_PREFIX, run_id, headers.get(HEADER_SESSION_ID))
        route_header = str(self.valves.ROUTE_HEADER or "").strip()
        route = headers.get(route_header) if route_header else None
        if isinstance(route, str) and route.strip():
            await self._status(emit, self._t("route", route=route.strip()[:ROUTE_STATUS_LIMIT]))
        await self._status(emit, self._t("working"))
        async with aclosing(self._follow(client, ctx, _Run(run_id), emit, call)) as chunks:
            async for chunk in chunks:
                yield chunk

    async def _answer_pending(self, client: Any, ctx: _Ctx, slash: tuple[str, str], emit, call) -> AsyncIterator[Any]:
        """Sends /approve or /deny for the open approval of this chat and follows the run again."""
        pending = self._pending[ctx.key]
        command, arg = slash
        if command == CHOICE_DENY:
            choice = CHOICE_DENY
        else:
            choice = CHOICE_ALIASES.get(arg.lower(), arg.lower()) or CHOICE_ONCE
        if choice != CHOICE_DENY and choice not in pending["offered"]:
            yield self._t("invalid_choice", choice=arg, hint=self._approve_hint(pending["offered"]))
            return
        del self._pending[ctx.key]
        run = _Run(pending["run_id"], pending["last_id"], pending["streamed"])
        status, err = await self._send(client, ctx, run, "approval", {**pending["answer"], "choice": choice})
        run.sent_choice = choice
        yield self._approval_line(choice, "", err)
        if err:
            if status == 0:
                # The answer did not reach Hermes: keep the approval open for a new try.
                self._pending[ctx.key] = pending
            return
        await self._status(emit, self._t("working"))
        async with aclosing(self._follow(client, ctx, run, emit, call)) as chunks:
            async for chunk in chunks:
                yield chunk

    async def _follow(self, client: Any, ctx: _Ctx, run: _Run, emit, call) -> AsyncIterator[Any]:
        """Follows the run events with reconnects; stops the run if the client ends the stream."""
        try:
            async with aclosing(self._events(client, ctx, run, emit, call)) as chunks:
                async for chunk in chunks:
                    yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            if not run.done and not run.parked:
                await asyncio.shield(self._stop(client, ctx, run))
            raise
        except _IdleTimeout:
            idle = True
        except Exception:
            # pipe() shows the error; do not leave the run without a reader.
            if not run.done and not run.parked:
                await self._stop(client, ctx, run)
            raise
        else:
            idle = False
        if idle:
            await self._stop(client, ctx, run)
            await self._status(emit, self._t("idle_status"), done=True)
            yield self._t("idle_text", seconds=self.valves.STREAM_IDLE_TIMEOUT)

    async def _events(self, client: Any, ctx: _Ctx, run: _Run, emit, call) -> AsyncIterator[Any]:
        """Reads the event stream; reconnects up to MAX_RECONNECTS times, then reads the run status."""
        for _ in range(1 + MAX_RECONNECTS):
            try:
                async with aclosing(self._stream_once(client, ctx, run, emit, call)) as chunks:
                    async for chunk in chunks:
                        yield chunk
            except NETWORK_ERRORS as e:
                log.warning("%s: event stream of run %s broke: %s", LOG_PREFIX, run.run_id, e)
            except _HttpError as e:
                log.warning("%s: event stream of run %s: %s", LOG_PREFIX, run.run_id, e)
                if e.status in FATAL_STREAM_STATUSES:
                    break
            if run.done or run.parked:
                return
        if not run.done:
            async with aclosing(self._final_status(client, ctx, run, emit)) as chunks:
                async for chunk in chunks:
                    yield chunk

    async def _stream_once(self, client: Any, ctx: _Ctx, run: _Run, emit, call) -> AsyncIterator[Any]:
        headers = {"Accept": "text/event-stream"}
        if run.last_id is not None:
            headers[HEADER_LAST_EVENT_ID] = run.last_id
        url = f"{ctx.base}/runs/{run.run_id}/events"
        async with client.get(url, headers=ctx.headers(self, **headers), timeout=self._timeout(0)) as resp:
            if resp.status != 200:
                raise _HttpError(resp.status, _error_text(await self._json(resp)))
            frames = _idle_limited(parse_sse(resp.content.iter_any()), self.valves.STREAM_IDLE_TIMEOUT)
            async with aclosing(frames):
                async for frame_id, data in frames:
                    if frame_id is not None:
                        run.last_id = frame_id
                    async with aclosing(self._on_event(client, ctx, run, data, emit, call)) as chunks:
                        async for chunk in chunks:
                            yield chunk
                    if run.done or run.parked:
                        return

    async def _final_status(self, client: Any, ctx: _Ctx, run: _Run, emit) -> AsyncIterator[Any]:
        """Reads GET /runs/{id} after the event stream is lost and shows the output."""
        run.done = True
        try:
            status, data, _ = await self._request(client, "get", ctx, f"/runs/{run.run_id}")
        except NETWORK_ERRORS as e:
            log.warning("%s: GET run %s failed: %s", LOG_PREFIX, run.run_id, e)
            status, data = 0, {}
        state = data.get("status") if isinstance(data, dict) else None
        await self._status(emit, self._t("lost_status"), done=True)
        if status == 200 and state == "completed":
            yield remaining_output(data.get("output"), run.streamed)
        elif status == 200 and state in TERMINAL_STATUSES:
            yield self._t("run_ended", state=state, error=_error_text(data.get("error")) or self._t("no_error_text"))
        else:
            yield self._t("lost_text", run_id=run.run_id, state=state or status or self._t("unknown"))

    async def _on_event(self, client: Any, ctx: _Ctx, run: _Run, data: dict, emit, call) -> AsyncIterator[Any]:
        name = str(data.get("event") or "")
        show = self.valves.SHOW_TOOL_PROGRESS
        if name in ("run.queued", "run.running"):
            await self._status(emit, self._t("working"))
        elif name == "message.delta":
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                run.streamed += delta
                yield delta
        elif name == "message.interim":
            text = data.get("text")
            if isinstance(text, str) and text.strip() and not data.get("already_streamed"):
                if run.streamed and not run.streamed.endswith("\n"):
                    text = "\n\n" + text
                run.streamed += text
                yield text
        elif name == "tool.started" and show:
            tool = data.get("tool") or self._t("tool_fallback_name")
            line = self._t("tool_started", tool=tool, preview=data.get("preview") or "").rstrip(": ")
            await self._status(emit, line[:STATUS_LINE_LIMIT])
            chunk = self._reasoning(ctx, f"> {line}\n")
            if chunk:
                yield chunk
        elif name == "tool.completed" and show:
            tool = data.get("tool") or self._t("tool_fallback_name")
            error = self._t("tool_error_suffix") if data.get("error") else ""
            line = self._t("tool_done", tool=tool, duration=data.get("duration") or 0, error=error)
            await self._status(emit, line[:STATUS_LINE_LIMIT])
            chunk = self._reasoning(ctx, f"> {line}\n")
            if chunk:
                yield chunk
        elif name == "reasoning.available":
            text = str(data.get("text") or "")
            chunk = self._reasoning(ctx, f"{text}\n") if text.strip() else None
            if chunk:
                yield chunk
        elif name in ("subagent.start", "subagent.complete") and show:
            label = self._t("subagent_start" if name == "subagent.start" else "subagent_done")
            await self._status(emit, f"{label}: {data.get('preview') or ''}".rstrip(": ")[:STATUS_LINE_LIMIT])
        elif name == "approval.request":
            async with aclosing(self._approval(client, ctx, run, data, emit, call)) as chunks:
                async for chunk in chunks:
                    yield chunk
        elif name == "approval.responded":
            choice = str(data.get("choice") or "")
            if choice and choice == run.sent_choice:
                run.sent_choice = ""
            elif choice:
                yield self._t("approval_sent", choice=choice, reason="")
        elif name == "clarify.request":
            async with aclosing(self._clarify(client, ctx, run, data, call)) as chunks:
                async for chunk in chunks:
                    yield chunk
        elif name == "replay.truncated":
            yield self._t("replay_truncated")
        elif name == "run.completed":
            run.done = True
            await self._status(emit, self._t("done"), done=True)
            rest = remaining_output(data.get("output"), run.streamed)
            if rest:
                yield rest
        elif name in RUN_END_EVENTS:
            run.done = True
            label = self._t(name)
            await self._status(emit, label, done=True)
            error = _error_text(data.get("error"))
            yield self._t("run_end_with_error", label=label, error=error) if error else self._t("run_end_plain", label=label)
        elif self.valves.DEBUG:
            log.debug("%s: event %s ignored", LOG_PREFIX, name)

    def _approve_hint(self, offered: list[str]) -> str:
        approve = [c for c in offered if c != CHOICE_DENY]
        return self._t("approve_hint", choices="|".join(approve)) if approve else self._t("deny_hint")

    def _approval_line(self, choice: str, reason: str, err: str) -> str:
        if err:
            return self._t("approval_not_sent", choice=choice, error=err)
        return self._t("approval_sent", choice=choice, reason=self._t("reason_separator") + reason if reason else "")

    async def _ask(self, call, event: dict) -> tuple[Any, str]:
        """Runs one __event_call__ dialog. Returns (result, "") or (None, reason)."""
        try:
            result = await asyncio.wait_for(call(event), self.valves.APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            return None, self._t("dialog_timeout")
        except Exception as e:
            log.warning("%s: dialog failed: %s", LOG_PREFIX, e)
            return None, self._t("dialog_failed")
        if isinstance(result, dict) and result.get("error"):
            return None, self._t("dialog_no_answer")
        return result, ""

    async def _approval(self, client: Any, ctx: _Ctx, run: _Run, data: dict, emit, call) -> AsyncIterator[Any]:
        rid = data.get("request_id") or data.get("prompt_id")
        answer = {"request_id": rid} if isinstance(rid, str) and rid else {}
        if isinstance(data.get("execution_generation"), int):
            answer["execution_generation"] = data["execution_generation"]
        offered = offered_choices(data.get("choices"), ctx.allowed_choices)
        if CHOICE_DENY not in offered:
            offered.append(CHOICE_DENY)
        command, description = str(data.get("command") or ""), str(data.get("description") or "")
        fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", command)), default=0) + 1)
        message = f"{description}\n\n{fence}\n{command}\n{fence}".strip()
        title = self._t("approval_title")
        reason = ""
        if ctx.approval_mode == CHOICE_DENY or offered == [CHOICE_DENY]:
            choice, reason = CHOICE_DENY, self._t("automatic")
        elif ctx.approval_mode == "auto":
            choice, reason = auto_choice(offered, ctx.auto_choice), self._t("automatic")
        elif call is None:
            run.parked = True
            self._pending[ctx.key] = {"run_id": run.run_id, "last_id": run.last_id, "streamed": run.streamed,
                                      "answer": answer, "offered": offered}
            await self._status(emit, self._t("waiting_approval"), done=True)
            yield f"\n\n**{title}**\n\n{message}\n\n{self._approve_hint(offered)}"
            return
        else:
            await self._status(emit, self._t("waiting_approval"))
            if set(offered) <= {CHOICE_ONCE, CHOICE_DENY}:
                result, reason = await self._ask(call, {"type": "confirmation", "data": {
                    "title": title, "message": message}})
                choice = CHOICE_ONCE if result is True else CHOICE_DENY
            else:
                choices = _or_list(offered, self._t("or"))
                result, reason = await self._ask(call, {"type": "input", "data": {
                    "title": title, "message": self._t("approval_answer", message=message, choices=choices),
                    "placeholder": CHOICE_ONCE, "value": CHOICE_ONCE}})
                raw = str(result or "").strip().lower()
                choice = CHOICE_ALIASES.get(raw, raw)
                if result is not None and choice not in offered:
                    reason = self._t("invalid_input", raw=raw[:INVALID_INPUT_PREVIEW])
                if choice not in offered:
                    choice = CHOICE_DENY
        _, err = await self._send(client, ctx, run, "approval", {**answer, "choice": choice})
        run.sent_choice = choice
        yield self._approval_line(choice, reason, err)
        await self._status(emit, self._t("working"))

    async def _clarify(self, client: Any, ctx: _Ctx, run: _Run, data: dict, call) -> AsyncIterator[Any]:
        rid = data.get("request_id") or data.get("prompt_id")
        answer = {"request_id": rid} if isinstance(rid, str) and rid else {}
        if isinstance(data.get("execution_generation"), int):
            answer["execution_generation"] = data["execution_generation"]
        choices = [str(c) for c in data.get("choices") or [] if isinstance(c, (str, int, float))]
        question = str(data.get("question") or data.get("prompt") or self._t("clarify_default"))
        message = self._t("clarify_options", question=question, choices=", ".join(choices)) if choices else question
        if call is None:
            yield self._t("clarify_no_dialog", message=message)
            return
        result, reason = await self._ask(call, {"type": "input", "data": {
            "title": self._t("clarify_title"), "message": message, "placeholder": choices[0] if choices else ""}})
        text = result.strip() if isinstance(result, str) else ""
        if not text:
            yield self._t("clarify_unanswered", reason=reason or self._t("empty_answer"))
            return
        _, err = await self._send(client, ctx, run, "clarify", {**answer, "answer": text})
        if err:
            yield self._t("clarify_not_sent", error=err)
        else:
            yield self._t("clarify_sent", answer=text[:ANSWER_PREVIEW])
