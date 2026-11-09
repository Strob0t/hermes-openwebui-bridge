"""
title: Hermes Runs Pipe
author: Strob0t
version: 0.2.0
license: AGPL-3.0
description: Runs Open WebUI chats as Hermes runs over /v1/runs, with model discovery, tool progress, approval dialogs and one Hermes session per chat.
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

DEFAULT_MODEL = {"id": "hermes-default", "name": "Hermes (Gateway-Default)", "default": True}
POLICY_KEYS = ("approval_mode", "allowed_approval_choices", "auto_choice", "allowed_roles")
APPROVAL_CHOICES = ("once", "session", "always", "deny")
APPROVAL_MODES = ("ask", "auto", "deny")
CHOICE_ALIASES = {"approve": "once", "approved": "once", "allow": "once"}
SLASH_COMMANDS = ("reset", "approve", "deny")
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted")
MAX_RECONNECTS = 3
NETWORK_ERRORS: tuple = (OSError, asyncio.TimeoutError) + ((aiohttp.ClientError,) if aiohttp else ())
APPROVAL_TITLE = "Hermes braucht eine Freigabe"
TEXT_DENIED = "Dieses Modell ist für dein Konto nicht freigegeben"
TEXT_RESET = "Session zurückgesetzt"
TEXT_NO_PENDING = "Keine offene Freigabe in diesem Chat."
TEXT_NO_INPUT = "Keine Nutzernachricht gefunden."


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
        log.warning("hermes runs pipe: MODEL_POLICIES is not valid JSON, using none: %s", e)
        return []
    if not isinstance(data, list):
        log.warning("hermes runs pipe: MODEL_POLICIES is not a JSON list, using none")
        return []
    policies = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("match"), str):
            continue
        try:
            pattern = re.compile(entry["match"])
        except re.error as e:
            log.warning("hermes runs pipe: MODEL_POLICIES regex %r invalid: %s", entry["match"], e)
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
            log.warning("hermes runs pipe: MODEL_FILTER regex invalid, ignoring it: %s", e)
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


def _header_text(value: Any) -> str:
    """Returns the value URL-quoted like Open WebUI custom headers such as {{USER_NAME}} (utils/headers.py)."""
    return quote(str(value or "").strip(), safe=string.punctuation + " ")


def _error_text(data: Any, limit: int = 300) -> str:
    """Returns the error message of a Hermes/OpenAI error body, shortened."""
    if isinstance(data, dict):
        err = data.get("error")
        data = err.get("message") if isinstance(err, dict) else err or data.get("message") or ""
    return re.sub(r"\s+", " ", str(data or "")).strip()[:limit]


def _or_list(items: list[str]) -> str:
    """Returns "a, b oder c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " oder " + items[-1]


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
        policy = policy_for(self.short_model, pipe.policies())
        mode = str(policy.get("approval_mode") or v.APPROVAL_MODE or "").strip().lower()
        if mode not in APPROVAL_MODES:
            log.warning("hermes runs pipe: unknown approval mode %r, using ask", mode)
            mode = "ask"
        self.approval_mode = mode
        self.allowed_choices = policy.get("allowed_approval_choices") or v.ALLOWED_APPROVAL_CHOICES
        self.auto_choice = str(policy.get("auto_choice") or v.AUTO_CHOICE)
        self.allowed_roles = policy.get("allowed_roles") or v.ALLOWED_ROLES

    def run_body(self, text: str) -> dict:
        """Returns the POST /runs body; model and provider only for a discovered model, not for the default entry."""
        body: dict = {"input": text, "stream": False}
        if self.run_model:
            body["model"] = self.run_model
            if self.run_provider:
                body["provider"] = self.run_provider
        return body

    def headers(self, pipe: "Pipe", **extra: str) -> dict:
        h = {"Accept-Encoding": "identity"}
        if pipe.valves.HERMES_API_KEY:
            h["Authorization"] = f"Bearer {pipe.valves.HERMES_API_KEY}"
        if self.chat_id:
            h["X-Hermes-Session-Key"] = session_key(self.user_id, self.chat_id, pipe._resets.get(self.key, 0))
        if self.role:
            h["X-Hermes-User-Role"] = _header_text(self.role)
        if self.name:
            h["X-Hermes-User-Name"] = _header_text(self.name)
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
        DEBUG: bool = Field(
            default=False,
            description="Enable debug logging",
        )

    def __init__(self):
        self.type = "manifold"
        self.valves = self.Valves()
        self._client_factory: Callable[[], Any] = self._default_client
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

    def policies(self) -> list[dict]:
        return parse_policies(self.valves.MODEL_POLICIES)

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
                    log.warning("hermes runs pipe: model discovery failed (%s), %s", e,
                                "keeping the last list" if self._models else "using the default entry")
                else:
                    if not models:
                        log.warning("hermes runs pipe: model discovery returned no model after the filters")
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
            return 0, f"Hermes ist nicht erreichbar ({ctx.base}): {type(e).__name__}"
        if status >= 300:
            return status, f"HTTP {status}: {_error_text(data)}".rstrip(": ")
        return status, ""

    async def _stop(self, client: Any, ctx: _Ctx, run: _Run) -> None:
        try:
            await self._request(client, "post", ctx, f"/runs/{run.run_id}/stop", {})
        except Exception as e:
            log.warning("hermes runs pipe: stop of run %s failed: %s", run.run_id, e)

    @staticmethod
    async def _status(emit: Optional[Callable], description: str, done: bool = False) -> None:
        if emit is None:
            return
        try:
            await emit({"type": "status", "data": {"description": description, "done": done}})
        except Exception as e:
            log.debug("hermes runs pipe: status event failed: %s", e)

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
    ) -> AsyncIterator[Any]:
        try:
            body = body if isinstance(body, dict) else {}
            ctx = _Ctx(self, body, __user__ or {}, __metadata__ or {})
            if (__metadata__ or {}).get("task"):
                # Title, tag and follow-up tasks would add turns to the Hermes session of the chat.
                yield ""
                return
            if not role_allowed(ctx.role, ctx.allowed_roles):
                yield TEXT_DENIED
                return
            text = last_user_text(body.get("messages"))
            slash = parse_slash(text)
            if slash and slash[0] == "reset":
                self._resets[ctx.key] = self._resets.get(ctx.key, 0) + 1
                self._pending.pop(ctx.key, None)
                yield TEXT_RESET
                return
            if slash and ctx.key not in self._pending:
                yield TEXT_NO_PENDING
                return
            if not slash and not text.strip():
                yield TEXT_NO_INPUT
                return
            async with self._client_factory() as client:
                if slash:
                    chunks = self._answer_pending(client, ctx, slash, __event_emitter__, __event_call__)
                else:
                    chunks = self._start(client, ctx, text, __event_emitter__, __event_call__)
                async with aclosing(chunks):
                    async for chunk in chunks:
                        if ctx.stream and isinstance(chunk, str) and chunk.startswith("data:"):
                            # Open WebUI passes a str chunk that starts with "data:" on as a raw SSE line.
                            chunk = self._chunk(ctx, "content", chunk)
                        yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            raise
        except Exception as e:
            log.exception("hermes runs pipe failed")
            yield f"\n\nFehler in der Hermes-Runs-Pipe: {type(e).__name__}: {e}"

    async def _start(self, client: Any, ctx: _Ctx, text: str, emit, call) -> AsyncIterator[Any]:
        """Starts a run with the last user message and follows it."""
        try:
            status, data, headers = await self._request(
                client, "post", ctx, "/runs", ctx.run_body(text),
                **{"Idempotency-Key": idempotency_key(ctx.chat_id, ctx.message_id)})
        except NETWORK_ERRORS as e:
            log.warning("hermes runs pipe: POST /runs failed: %s", e)
            yield f"Hermes ist nicht erreichbar ({ctx.base})"
            return
        run_id = data.get("run_id") if isinstance(data, dict) else None
        if status >= 300 or not isinstance(run_id, str) or not run_id:
            yield f"Hermes hat den Run abgelehnt (HTTP {status}: {_error_text(data) or 'keine run_id'})"
            return
        if self.valves.DEBUG:
            log.debug("hermes runs pipe: run %s, session %s", run_id, headers.get("X-Hermes-Session-Id"))
        route_header = str(self.valves.ROUTE_HEADER or "").strip()
        route = headers.get(route_header) if route_header else None
        if isinstance(route, str) and route.strip():
            await self._status(emit, f"Route: {route.strip()[:100]}")
        await self._status(emit, "Hermes arbeitet")
        async with aclosing(self._follow(client, ctx, _Run(run_id), emit, call)) as chunks:
            async for chunk in chunks:
                yield chunk

    async def _answer_pending(self, client: Any, ctx: _Ctx, slash: tuple[str, str], emit, call) -> AsyncIterator[Any]:
        """Sends /approve or /deny for the open approval of this chat and follows the run again."""
        pending = self._pending[ctx.key]
        command, arg = slash
        choice = "deny" if command == "deny" else CHOICE_ALIASES.get(arg.lower(), arg.lower()) or "once"
        if choice != "deny" and choice not in pending["offered"]:
            yield f"Ungültige Wahl `{arg}`. {self._approve_hint(pending['offered'])}"
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
        await self._status(emit, "Hermes arbeitet")
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
            await self._status(emit, "Hermes antwortet nicht", done=True)
            yield f"\n\nKeine Daten von Hermes seit {self.valves.STREAM_IDLE_TIMEOUT} s, Run gestoppt."

    async def _events(self, client: Any, ctx: _Ctx, run: _Run, emit, call) -> AsyncIterator[Any]:
        """Reads the event stream; reconnects up to MAX_RECONNECTS times, then reads the run status."""
        for _ in range(1 + MAX_RECONNECTS):
            try:
                async with aclosing(self._stream_once(client, ctx, run, emit, call)) as chunks:
                    async for chunk in chunks:
                        yield chunk
            except NETWORK_ERRORS as e:
                log.warning("hermes runs pipe: event stream of run %s broke: %s", run.run_id, e)
            except _HttpError as e:
                log.warning("hermes runs pipe: event stream of run %s: %s", run.run_id, e)
                if e.status in (401, 403, 404):
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
            headers["Last-Event-ID"] = run.last_id
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
            log.warning("hermes runs pipe: GET run %s failed: %s", run.run_id, e)
            status, data = 0, {}
        state = data.get("status") if isinstance(data, dict) else None
        await self._status(emit, "Verbindung zum Fortschritt verloren", done=True)
        if status == 200 and state == "completed":
            yield remaining_output(data.get("output"), run.streamed)
        elif status == 200 and state in TERMINAL_STATUSES:
            yield f"\n\nRun {state}: {_error_text(data.get('error')) or 'ohne Fehlertext'}"
        else:
            yield (f"\n\nVerbindung zum Fortschritt verloren (Run `{run.run_id}`, Status "
                   f"{state or status or 'unbekannt'}). Der Run läuft in Hermes eventuell weiter.")

    async def _on_event(self, client: Any, ctx: _Ctx, run: _Run, data: dict, emit, call) -> AsyncIterator[Any]:
        name = str(data.get("event") or "")
        show = self.valves.SHOW_TOOL_PROGRESS
        if name in ("run.queued", "run.running"):
            await self._status(emit, "Hermes arbeitet")
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
            line = f"{data.get('tool') or 'tool'}: {data.get('preview') or ''}".rstrip(": ")
            await self._status(emit, line[:120])
            chunk = self._reasoning(ctx, f"> {line}\n")
            if chunk:
                yield chunk
        elif name == "tool.completed" and show:
            line = f"{data.get('tool') or 'tool'} fertig ({data.get('duration') or 0} s{', Fehler' if data.get('error') else ''})"
            await self._status(emit, line[:120])
            chunk = self._reasoning(ctx, f"> {line}\n")
            if chunk:
                yield chunk
        elif name == "reasoning.available":
            text = str(data.get("text") or "")
            chunk = self._reasoning(ctx, f"{text}\n") if text.strip() else None
            if chunk:
                yield chunk
        elif name in ("subagent.start", "subagent.complete") and show:
            label = "Subagent startet" if name == "subagent.start" else "Subagent fertig"
            await self._status(emit, f"{label}: {data.get('preview') or ''}".rstrip(": ")[:120])
        elif name == "approval.request":
            async with aclosing(self._approval(client, ctx, run, data, emit, call)) as chunks:
                async for chunk in chunks:
                    yield chunk
        elif name == "approval.responded":
            choice = str(data.get("choice") or "")
            if choice and choice == run.sent_choice:
                run.sent_choice = ""
            elif choice:
                yield f"\n\n*(Freigabe: {choice})*\n\n"
        elif name == "clarify.request":
            async with aclosing(self._clarify(client, ctx, run, data, call)) as chunks:
                async for chunk in chunks:
                    yield chunk
        elif name == "replay.truncated":
            yield "\n\n*(Teile des Fortschritts fehlen)*\n\n"
        elif name == "run.completed":
            run.done = True
            await self._status(emit, "Fertig", done=True)
            rest = remaining_output(data.get("output"), run.streamed)
            if rest:
                yield rest
        elif name in ("run.failed", "run.cancelled", "run.interrupted"):
            run.done = True
            label = {"run.failed": "Run fehlgeschlagen", "run.cancelled": "Run abgebrochen",
                     "run.interrupted": "Run unterbrochen"}[name]
            await self._status(emit, label, done=True)
            error = _error_text(data.get("error"))
            yield f"\n\n{label}: {error}" if error else f"\n\n{label}."
        elif self.valves.DEBUG:
            log.debug("hermes runs pipe: event %s ignored", name)

    @staticmethod
    def _approve_hint(offered: list[str]) -> str:
        approve = [c for c in offered if c != "deny"]
        return f"Antworte mit `/approve [{'|'.join(approve)}]` oder `/deny`." if approve else "Antworte mit `/deny`."

    @staticmethod
    def _approval_line(choice: str, reason: str, err: str) -> str:
        if err:
            return f"\n\n*(Freigabe `{choice}` nicht gesendet: {err})*\n\n"
        return f"\n\n*(Freigabe: {choice}{', ' + reason if reason else ''})*\n\n"

    async def _ask(self, call, event: dict) -> tuple[Any, str]:
        """Runs one __event_call__ dialog. Returns (result, "") or (None, reason)."""
        try:
            result = await asyncio.wait_for(call(event), self.valves.APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            return None, "Zeit abgelaufen"
        except Exception as e:
            log.warning("hermes runs pipe: dialog failed: %s", e)
            return None, "Dialog fehlgeschlagen"
        if isinstance(result, dict) and result.get("error"):
            return None, "Dialog ohne Antwort"
        return result, ""

    async def _approval(self, client: Any, ctx: _Ctx, run: _Run, data: dict, emit, call) -> AsyncIterator[Any]:
        rid = data.get("request_id") or data.get("prompt_id")
        answer = {"request_id": rid} if isinstance(rid, str) and rid else {}
        if isinstance(data.get("execution_generation"), int):
            answer["execution_generation"] = data["execution_generation"]
        offered = offered_choices(data.get("choices"), ctx.allowed_choices)
        if "deny" not in offered:
            offered.append("deny")
        command, description = str(data.get("command") or ""), str(data.get("description") or "")
        fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", command)), default=0) + 1)
        message = f"{description}\n\n{fence}\n{command}\n{fence}".strip()
        reason = ""
        if ctx.approval_mode == "deny" or offered == ["deny"]:
            choice, reason = "deny", "automatisch"
        elif ctx.approval_mode == "auto":
            choice, reason = auto_choice(offered, ctx.auto_choice), "automatisch"
        elif call is None:
            run.parked = True
            self._pending[ctx.key] = {"run_id": run.run_id, "last_id": run.last_id, "streamed": run.streamed,
                                      "answer": answer, "offered": offered}
            await self._status(emit, "Wartet auf Freigabe", done=True)
            yield f"\n\n**{APPROVAL_TITLE}**\n\n{message}\n\n{self._approve_hint(offered)}"
            return
        else:
            await self._status(emit, "Wartet auf Freigabe")
            if set(offered) <= {"once", "deny"}:
                result, reason = await self._ask(call, {"type": "confirmation", "data": {
                    "title": APPROVAL_TITLE, "message": message}})
                choice = "once" if result is True else "deny"
            else:
                result, reason = await self._ask(call, {"type": "input", "data": {
                    "title": APPROVAL_TITLE, "message": f"{message}\n\nAntwort: {_or_list(offered)}",
                    "placeholder": "once", "value": "once"}})
                raw = str(result or "").strip().lower()
                choice = CHOICE_ALIASES.get(raw, raw)
                if result is not None and choice not in offered:
                    reason = f"ungültige Eingabe `{raw[:40]}`"
                if choice not in offered:
                    choice = "deny"
        _, err = await self._send(client, ctx, run, "approval", {**answer, "choice": choice})
        run.sent_choice = choice
        yield self._approval_line(choice, reason, err)
        await self._status(emit, "Hermes arbeitet")

    async def _clarify(self, client: Any, ctx: _Ctx, run: _Run, data: dict, call) -> AsyncIterator[Any]:
        rid = data.get("request_id") or data.get("prompt_id")
        answer = {"request_id": rid} if isinstance(rid, str) and rid else {}
        if isinstance(data.get("execution_generation"), int):
            answer["execution_generation"] = data["execution_generation"]
        choices = [str(c) for c in data.get("choices") or [] if isinstance(c, (str, int, float))]
        question = str(data.get("question") or data.get("prompt") or "Hermes hat eine Rückfrage.")
        message = f"{question}\n\nOptionen: {', '.join(choices)}" if choices else question
        if call is None:
            yield f"\n\n**Rückfrage von Hermes:** {message}\n\n*(Ohne Dialog keine Antwort möglich.)*\n\n"
            return
        result, reason = await self._ask(call, {"type": "input", "data": {
            "title": "Rückfrage von Hermes", "message": message, "placeholder": choices[0] if choices else ""}})
        text = result.strip() if isinstance(result, str) else ""
        if not text:
            yield f"\n\n*(Rückfrage nicht beantwortet: {reason or 'leere Antwort'})*\n\n"
            return
        _, err = await self._send(client, ctx, run, "clarify", {**answer, "answer": text})
        yield f"\n\n*(Antwort an Hermes nicht gesendet: {err})*\n\n" if err else f"\n\n*(Antwort an Hermes: {text[:200]})*\n\n"
