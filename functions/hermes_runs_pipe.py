"""
title: Hermes Runs Pipe
author: Strob0t
version: 0.1.0
license: AGPL-3.0
description: Runs Open WebUI chats as Hermes runs over /v1/runs, with tool progress, approval dialogs and one Hermes session per chat.
required_open_webui_version: 0.12.0
"""

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

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

DEFAULT_MODELS = [{"id": "hermes-runs", "name": "Hermes Runs"}]
APPROVAL_CHOICES = ("once", "session", "always", "deny")
APPROVAL_MODES = ("ask", "auto", "deny")
CHOICE_ALIASES = {"approve": "once", "approved": "once", "allow": "once"}
SLASH_COMMANDS = ("reset", "approve", "deny")
TERMINAL_EVENTS = ("run.completed", "run.failed", "run.cancelled", "run.interrupted")
MAX_RECONNECTS = 3


def _csv(value: Any) -> list[str]:
    """Returns the lower-case, unique, non-empty items of a comma-separated string or a list."""
    items = value.split(",") if isinstance(value, str) else (value or [])
    out = []
    for item in items:
        item = str(item).strip().lower()
        if item and item not in out:
            out.append(item)
    return out


def parse_models(raw: Any) -> list[dict]:
    """Parses the MODELS valve (JSON list of objects with id, name and optional overrides).
    Returns the default entry and logs a warning if the value has no usable entry.
    """
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as e:
        log.warning("hermes runs pipe: MODELS is not valid JSON, using default: %s", e)
        return [dict(m) for m in DEFAULT_MODELS]
    models = []
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"].strip():
                model_id = entry["id"].strip()
                models.append({**entry, "id": model_id, "name": str(entry.get("name") or model_id)})
    if not models:
        log.warning("hermes runs pipe: MODELS has no usable entry, using default")
        return [dict(m) for m in DEFAULT_MODELS]
    return models


def session_key(user_id: str, chat_id: str, reset_count: int = 0) -> str:
    """Returns the X-Hermes-Session-Key value owui:<user_id>:<chat_id>, with :r<n> after a /reset."""
    key = f"owui:{user_id}:{chat_id}"
    return f"{key}:r{reset_count}" if reset_count else key


def idempotency_key(chat_id: str, message_id: str) -> str:
    """Returns owui:<chat_id>:<message_id> with only visible ASCII characters, at most 255.
    Returns "" if an id is missing; then the pipe sends no Idempotency-Key header.
    """
    if not chat_id or not message_id:
        return ""
    return "".join(c for c in f"owui:{chat_id}:{message_id}" if "!" <= c <= "~")[:255]


def _event_choices(event_choices: Any) -> list[str]:
    if not isinstance(event_choices, (list, tuple)) or not event_choices:
        return ["once", "deny"]
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
    if "once" in choices:
        return "once"
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


def last_user_text(messages: Any) -> str:
    """Returns the text of the last user message (string content or the text parts of a list)."""
    if not isinstance(messages, list):
        return ""
    for item in reversed(messages):
        if isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
            return ""
    return ""


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


def _header_value(value: Any, limit: int = 200) -> str:
    """Returns the value as a header-safe string without control characters."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()[:limit]


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
        MODELS: str = Field(
            default='[{"id":"hermes-runs","name":"Hermes Runs"}]',
            description="JSON list of models; an entry can override approval_mode, allowed_approval_choices, auto_choice and allowed_roles",
        )
        REQUEST_TIMEOUT: int = Field(
            default=30,
            description="Seconds for POST /runs, /approval, /clarify, /stop and GET /runs/{id}",
        )
        STREAM_IDLE_TIMEOUT: int = Field(
            default=900,
            description="Seconds without data on the event stream, then stop and error",
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
        DEBUG: bool = Field(
            default=False,
            description="Enable debug logging",
        )

    def __init__(self):
        self.type = "manifold"
        self.valves = self.Valves()
        self._client_factory: Callable[[], Any] = self._default_client
        self._resets: dict[str, int] = {}
        self._pending: dict[str, dict] = {}

    @staticmethod
    def _default_client() -> Any:
        if aiohttp is None:
            raise RuntimeError("aiohttp is not installed")
        return aiohttp.ClientSession()

    def pipes(self) -> list[dict]:
        return [{"id": m["id"], "name": m["name"]} for m in parse_models(self.valves.MODELS)]

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
        __event_call__: Optional[Callable[[dict], Awaitable[Any]]] = None,
    ) -> AsyncIterator[Any]:
        yield "Hermes Runs Pipe: der Run-Ablauf folgt im naechsten Commit."
