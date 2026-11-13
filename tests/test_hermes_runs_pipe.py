import asyncio
import base64
import inspect
import json
import sys
import unittest
from pathlib import Path

# Add functions directory to sys.path
FUNCTIONS_DIR = Path(__file__).resolve().parent.parent / "functions"
sys.path.insert(0, str(FUNCTIONS_DIR))

import hermes_runs_pipe as pipe_mod


async def _chunks(*parts):
    for part in parts:
        yield part


async def _collect(agen):
    return [item async for item in agen]


BASE = "http://hermes.test/v1"
USER = {"id": "u1", "name": "Owner", "role": "admin"}
META = {"chat_id": "c1", "message_id": "m1", "session_id": "s1"}
MODEL = "hermes_runs_pipe.auto"
HANG = object()

MODELS_RESP = {"object": "list", "data": [
    {"id": "auto", "object": "model", "owned_by": "hermes", "name": "Hermes · Auto"},
    {"id": "auto-medium", "object": "model", "owned_by": "hermes", "name": "Hermes · Mittel"},
    {"id": "auto-strong", "object": "model", "owned_by": "hermes"},
    {"id": "anthropic/claude", "object": "model", "owned_by": "bifrost", "name": "Claude", "provider": "bifrost"},
]}

# Multi-model setup of the README: Hermes classes, the Claude subscription and premiumize models via Bifrost.
MULTI_RESP = {"object": "list", "data": [
    {"id": "auto", "owned_by": "hermes"},
    {"id": "auto-medium", "owned_by": "hermes"},
    {"id": "claude-sub/claude-opus-5-5", "owned_by": "Bifrost"},
    {"id": "premiumize/gpt-6.1-sol", "owned_by": "bifrost"},
    {"id": "premiumize/gpt-6.1-sol", "owned_by": "other"},
    {"id": "openai/gpt-6.1", "owned_by": "bifrost"},
    {"id": "claude-sub/claude.x", "owned_by": "bifrost"},
]}
MULTI_FILTER = r"^(auto|claude-sub/claude-[a-z0-9-]+|premiumize/.+)$"


def data_image(size, mime="image/png"):
    """Returns a base64 data URL with size decoded bytes."""
    return f"data:{mime};base64,{base64.b64encode(bytes(size)).decode()}"


IMG_A = data_image(10)
IMG_B = data_image(20, "image/jpeg")


def ev(name, **fields):
    return name, fields


def sse(*events, start=1):
    """Returns SSE chunks: an open comment, then one frame per (event, fields) with id = seq."""
    chunks = [b": open\n\n"]
    for seq, (name, fields) in enumerate(events, start):
        data = {"object": "hermes.run.event", "run_id": "r1", "event": name, "seq": seq, **fields}
        chunks.append(f"id: {seq}\ndata: {json.dumps(data)}\n\n".encode())
    return chunks


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_any(self):
        for chunk in self.chunks:
            if chunk is HANG:
                await asyncio.Event().wait()
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


class FakeResp:
    def __init__(self, status=200, data=None, chunks=(), exc=None, headers=None):
        self.status = status
        self.data = data if data is not None else {}
        self.content = FakeContent(list(chunks))
        self.exc = exc
        self.headers = headers or {}

    async def __aenter__(self):
        if self.exc is not None:
            raise self.exc
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self.data


class FakeClient:
    """Scripted per (method, path): a list of responses is used in order, the last one repeats.
    Records each call as (method, path, headers, json)."""

    def __init__(self, script):
        self.script = {k: list(v) if isinstance(v, list) else [v] for k, v in script.items()}
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _call(self, method, url, headers=None, json=None, timeout=None):
        path = url[len(BASE):]
        self.calls.append((method, path, dict(headers or {}), json))
        queue = self.script.get((method, path))
        if not queue:
            raise AssertionError(f"unexpected request {method} {path}")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def get(self, url, **kwargs):
        return self._call("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._call("POST", url, **kwargs)

    def find(self, method, path):
        return [c for c in self.calls if c[0] == method and c[1] == path]


def make_pipe(*streams, extra=None, models=None, **valves):
    """Returns (pipe, client); each stream is the chunk list of one GET /runs/r1/events.
    The model cache is filled with MODELS_RESP (or models) so that the run body carries the model."""
    p = pipe_mod.Pipe()
    p.valves.HERMES_BASE_URL = BASE
    for key, value in valves.items():
        setattr(p.valves, key, value)
    script = {
        ("GET", "/models"): FakeResp(200, MODELS_RESP),
        ("POST", "/runs"): FakeResp(202, {"run_id": "r1", "status": "queued"}),
        ("GET", "/runs/r1/events"): [FakeResp(chunks=s) for s in streams] or [FakeResp(chunks=[])],
        ("POST", "/runs/r1/approval"): FakeResp(200, {"object": "hermes.run.approval_response", "resolved": 1}),
        ("POST", "/runs/r1/clarify"): FakeResp(200, {"status": "resolved"}),
        ("POST", "/runs/r1/stop"): FakeResp(200, {"run_id": "r1", "status": "stopping"}),
    }
    script.update(extra or {})
    client = FakeClient(script)
    p._client_factory = lambda: client
    p._models = pipe_mod.parse_model_list(MODELS_RESP if models is None else models)
    p._models_at = p._now()
    return p, client


def body_for(text, stream=True, model=MODEL, **fields):
    return {"model": model, "stream": stream, "messages": [{"role": "user", "content": text}], **fields}


def run_pipe(p, text="Hello", stream=True, user=None, meta=None, call=None, model=MODEL, owui_files=None, **fields):
    """Runs the pipe to its end. Returns (chunks, emitted events). fields go into the request body,
    owui_files is the __files__ argument."""
    emitted = []

    async def emit(event):
        emitted.append(event)

    async def go():
        agen = p.pipe(body_for(text, stream, model, **fields), __user__=dict(user or USER),
                      __metadata__=dict(meta or META), __event_emitter__=emit, __event_call__=call,
                      __files__=owui_files)
        return await _collect(agen)

    return asyncio.run(go()), emitted


def text_of(chunks):
    return "".join(c for c in chunks if isinstance(c, str))


def reasoning_of(chunks):
    return "".join(c["choices"][0]["delta"]["reasoning_content"] for c in chunks if isinstance(c, dict))


class Dialog:
    """Fake __event_call__: records the events and returns the scripted answers in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.events = []

    async def __call__(self, event):
        self.events.append(event)
        return self.answers.pop(0)


class TestHelpers(unittest.TestCase):
    def test_parse_policies(self):
        policies = pipe_mod.parse_policies('[{"match":"^auto","approval_mode":"auto"},{"match":".*","allowed_roles":"admin,user"}]')
        self.assertEqual(len(policies), 2)
        self.assertEqual(pipe_mod.policy_for("auto-strong", policies), {"approval_mode": "auto"})
        self.assertEqual(pipe_mod.policy_for("anthropic/claude", policies), {"allowed_roles": "admin,user"})
        self.assertEqual(pipe_mod.policy_for("x", []), {})
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual(pipe_mod.parse_policies("{not json"), [])
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual(pipe_mod.parse_policies('[{"match":"(","approval_mode":"auto"}]'), [])
        self.assertEqual(pipe_mod.parse_policies(""), [])
        self.assertEqual(pipe_mod.parse_policies('[{"approval_mode":"auto"}]'), [])

    def test_parse_model_list(self):
        models = pipe_mod.parse_model_list(MODELS_RESP)
        self.assertEqual([m["id"] for m in models], ["auto", "auto-medium", "auto-strong", "anthropic/claude"])
        self.assertEqual(models[0]["name"], "Hermes · Auto")
        self.assertEqual(models[2]["name"], "auto-strong")
        self.assertEqual(models[3]["provider"], "bifrost")
        self.assertNotIn("provider", models[0])
        self.assertEqual([m["id"] for m in pipe_mod.parse_model_list(MODELS_RESP, id_filter="^auto")],
                         ["auto", "auto-medium", "auto-strong"])
        self.assertEqual([m["id"] for m in pipe_mod.parse_model_list(MODELS_RESP, owned_by_csv="bifrost")],
                         ["anthropic/claude"])
        self.assertEqual(pipe_mod.parse_model_list({"data": "x"}), [])
        self.assertEqual(pipe_mod.parse_model_list({"data": [{"name": "no id"}]}), [])
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual(len(pipe_mod.parse_model_list(MODELS_RESP, id_filter="(")), 4)

    def test_owned_by_comma_list(self):
        # 0.4.0: OWNED_BY_FILTER is a comma list (case and spaces do not matter); MODEL_FILTER stays a regex.
        models = pipe_mod.parse_model_list(MULTI_RESP, MULTI_FILTER, " hermes , BIFROST ")
        self.assertEqual([(m["id"], m["owned_by"]) for m in models], [
            ("auto", "hermes"), ("claude-sub/claude-opus-5-5", "Bifrost"), ("premiumize/gpt-6.1-sol", "bifrost")])
        self.assertEqual([m["id"] for m in pipe_mod.parse_model_list(MULTI_RESP, owned_by_csv="hermes")],
                         ["auto", "auto-medium"])
        self.assertEqual(len(pipe_mod.parse_model_list(MULTI_RESP, owned_by_csv=",")), len(MULTI_RESP["data"]))

    def test_short_model_id_dots_and_slashes(self):
        short = pipe_mod.Pipe.short_model_id
        self.assertEqual(short("hermes_runs_pipe.premiumize/gpt-6.1-sol"), "premiumize/gpt-6.1-sol")
        self.assertEqual(short("hermes_runs_pipe.claude-sub/claude-opus-5-5"), "claude-sub/claude-opus-5-5")
        self.assertEqual(short("hermes_runs_pipe.auto"), "auto")
        self.assertEqual(short("auto"), "auto")

    def test_run_model_options(self):
        keys = "reasoning_effort"
        self.assertEqual(pipe_mod.run_model_options({"model_options": {"reasoning_effort": "high"}}, keys),
                         {"reasoning_effort": "high"})
        # Values go through as given; keys outside the allowlist are dropped.
        self.assertEqual(pipe_mod.run_model_options(
            {"model_options": {"reasoning_effort": "Max ", "service_tier": "priority", "fast": True}}, keys),
            {"reasoning_effort": "Max "})
        self.assertEqual(pipe_mod.run_model_options(
            {"model_options": {"reasoning_effort": "low", "service_tier": "priority"}}, "reasoning_effort, service_tier"),
            {"reasoning_effort": "low", "service_tier": "priority"})
        # Top-level slider value only if model_options has no reasoning_effort.
        self.assertEqual(pipe_mod.run_model_options({"reasoning_effort": "medium"}, keys), {"reasoning_effort": "medium"})
        self.assertEqual(pipe_mod.run_model_options(
            {"reasoning_effort": "medium", "model_options": {"reasoning_effort": "xhigh"}}, keys),
            {"reasoning_effort": "xhigh"})
        self.assertEqual(pipe_mod.run_model_options({"reasoning_effort": "medium"}, "service_tier"), {})
        self.assertEqual(pipe_mod.run_model_options({"reasoning_effort": None}, keys), {})
        self.assertEqual(pipe_mod.run_model_options({"model_options": {"reasoning_effort": "high"}}, ""), {})
        self.assertEqual(pipe_mod.run_model_options({"model_options": "high"}, keys), {})
        self.assertEqual(pipe_mod.run_model_options({}, keys), {})
        self.assertEqual(pipe_mod.run_model_options(None, keys), {})

    def test_session_key(self):
        self.assertEqual(pipe_mod.session_key("u1", "c1"), "owui:u1:c1")
        self.assertEqual(pipe_mod.session_key("u1", "c1", 0), "owui:u1:c1")
        self.assertEqual(pipe_mod.session_key("u1", "c1", 2), "owui:u1:c1:r2")

    def test_idempotency_header(self):
        # Spec test 17 (helper part): owui:<chat>:<message_id>, visible ASCII only, at most 255.
        self.assertEqual(pipe_mod.idempotency_key("c1", "m1"), "owui:c1:m1")
        key = pipe_mod.idempotency_key("c 1ä\n", "m\t1" + "x" * 300)
        self.assertTrue(key.startswith("owui:c1:m1"))
        self.assertLessEqual(len(key), 255)
        self.assertTrue(all("!" <= c <= "~" for c in key))
        self.assertEqual(pipe_mod.idempotency_key("", "m1"), "")
        self.assertEqual(pipe_mod.idempotency_key("c1", None), "")

    def test_choices_intersection(self):
        # Spec test 18.
        self.assertEqual(pipe_mod.offered_choices(["once", "session", "always", "deny"], "once,deny,session"),
                         ["once", "deny", "session"])
        self.assertEqual(pipe_mod.offered_choices(["once", "deny"], "once,deny,session"), ["once", "deny"])
        self.assertEqual(pipe_mod.offered_choices(None, "once,deny,session"), ["once", "deny"])
        self.assertEqual(pipe_mod.offered_choices(["once", "deny"], "always, foo"), [])

    def test_auto_choice(self):
        self.assertEqual(pipe_mod.auto_choice(["once", "session", "deny"], "session"), "session")
        self.assertEqual(pipe_mod.auto_choice(["once", "deny"], "session"), "once")
        self.assertEqual(pipe_mod.auto_choice(["deny"], "session"), "deny")
        self.assertEqual(pipe_mod.auto_choice(None, "always"), "once")

    def test_parse_sse(self):
        stream = _chunks(
            b": open\n\n",
            b"id: 1\ndata: {\"event\": \"run.running\", \"seq\": 1}\n\n",
            b"id: 2\r\ndata: {\"event\": \"message.delta\",",
            b" \"delta\": \"Hal\"}\r\n\r\n",
            b"data: {\"event\": \"replay.truncated\"}\n\n",
            b"data: not json\n\n",
            b"id: 3\ndata: {\"event\": \"cut\"}",
        )
        frames = asyncio.run(_collect(pipe_mod.parse_sse(stream)))
        self.assertEqual(frames, [
            ("1", {"event": "run.running", "seq": 1}),
            ("2", {"event": "message.delta", "delta": "Hal"}),
            (None, {"event": "replay.truncated"}),
        ])

    def test_last_user_text(self):
        messages = [
            {"role": "user", "content": "erste"},
            {"role": "assistant", "content": "antwort"},
            {"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "image_url"}, {"type": "text", "text": "b"}]},
        ]
        self.assertEqual(pipe_mod.last_user_text(messages), "a\nb")
        self.assertEqual(pipe_mod.last_user_text(messages[:2]), "erste")
        self.assertEqual(pipe_mod.last_user_text([{"role": "assistant", "content": "x"}]), "")
        self.assertEqual(pipe_mod.last_user_text(None), "")

    def test_image_urls(self):
        # 0.5.0: image parts of the last user message, then image files; unique, in this order.
        messages = [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,old"}}]},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": IMG_A}},
                {"type": "input_image", "image_url": "https://example.test/b.png"},
                {"type": "image_url", "image_url": {"url": IMG_A}},
                {"type": "image_url", "image_url": {}},
            ]},
        ]
        files = [
            {"type": "image", "url": IMG_B},
            {"type": "file", "id": "f1", "url": "f1", "content_type": "image/jpeg"},
            {"type": "file", "id": "f2", "url": "f2", "content_type": "image/svg+xml"},
            {"type": "file", "id": "f3", "url": "f3", "content_type": "application/pdf"},
            "not a dict",
        ]
        self.assertEqual(pipe_mod.image_urls(messages, files), [IMG_A, "https://example.test/b.png", IMG_B, "f1"])
        self.assertEqual(pipe_mod.image_urls(messages[:2]), ["data:image/png;base64,old"])
        self.assertEqual(pipe_mod.image_urls([{"role": "user", "content": "text only"}], None), [])
        self.assertEqual(pipe_mod.image_urls(None, "x"), [])

    def test_image_url_size(self):
        self.assertEqual(pipe_mod.image_url_size(data_image(30)), 30)
        self.assertEqual(pipe_mod.image_url_size(data_image(31)), 31)
        self.assertEqual(pipe_mod.image_url_size(data_image(32)), 32)
        self.assertEqual(pipe_mod.image_url_size("DATA:IMAGE/PNG;BASE64,QUJD"), 3)
        self.assertEqual(pipe_mod.image_url_size("data:image/svg+xml,<svg/>"), 6)
        self.assertEqual(pipe_mod.image_url_size("https://example.test/a.png"), 0)
        self.assertIsNone(pipe_mod.image_url_size("http://example.test/a.png"))
        self.assertIsNone(pipe_mod.image_url_size("data:text/plain;base64,QUJD"))
        self.assertIsNone(pipe_mod.image_url_size("data:image/png;base64"))
        self.assertIsNone(pipe_mod.image_url_size("/api/v1/files/f1/content"))
        self.assertIsNone(pipe_mod.image_url_size(None))

    def test_run_input(self):
        self.assertEqual(pipe_mod.run_input("Hello", []), "Hello")
        self.assertEqual(pipe_mod.run_input("Hello", [IMG_A]), [{"role": "user", "content": [
            {"type": "text", "text": "Hello"}, {"type": "image_url", "image_url": {"url": IMG_A}}]}])
        self.assertEqual(pipe_mod.run_input("  ", [IMG_A]), [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": IMG_A}}]}])

    def test_thinking_switch(self):
        # 0.5.0: "none" or "off" (any case, top level or model_options) goes as "none"; Hermes turns thinking off.
        keys = "reasoning_effort"
        for value in ("none", "off", "OFF", " None "):
            self.assertEqual(pipe_mod.run_model_options({"reasoning_effort": value}, keys), {"reasoning_effort": "none"})
            self.assertEqual(pipe_mod.run_model_options({"model_options": {"reasoning_effort": value}}, keys),
                             {"reasoning_effort": "none"})
        self.assertEqual(pipe_mod.run_model_options(
            {"reasoning_effort": "high", "model_options": {"reasoning_effort": "off"}}, keys), {"reasoning_effort": "none"})
        self.assertEqual(pipe_mod.run_model_options(
            {"reasoning_effort": "off", "model_options": {"reasoning_effort": "low"}}, keys), {"reasoning_effort": "low"})
        self.assertEqual(pipe_mod.run_model_options({"reasoning_effort": "offline"}, keys), {"reasoning_effort": "offline"})
        self.assertEqual(pipe_mod.run_model_options({"reasoning_effort": "off"}, "service_tier"), {})

    def test_role_allowed(self):
        self.assertTrue(pipe_mod.role_allowed("admin", "admin"))
        self.assertTrue(pipe_mod.role_allowed("User", "admin, user"))
        self.assertFalse(pipe_mod.role_allowed("user", "admin"))
        self.assertFalse(pipe_mod.role_allowed(None, "admin"))
        self.assertTrue(pipe_mod.role_allowed("pending", "*"))

    def test_parse_slash(self):
        self.assertEqual(pipe_mod.parse_slash("/reset"), ("reset", ""))
        self.assertEqual(pipe_mod.parse_slash("  /approve session "), ("approve", "session"))
        self.assertEqual(pipe_mod.parse_slash("/APPROVE"), ("approve", ""))
        self.assertEqual(pipe_mod.parse_slash("/deny"), ("deny", ""))
        self.assertIsNone(pipe_mod.parse_slash("/other"))
        self.assertIsNone(pipe_mod.parse_slash("bitte /reset"))
        self.assertIsNone(pipe_mod.parse_slash(None))

    def test_remaining_output(self):
        self.assertEqual(pipe_mod.remaining_output("Hello world", "Hello"), " world")
        self.assertEqual(pipe_mod.remaining_output("Hello world", "Hello world"), "")
        self.assertEqual(pipe_mod.remaining_output("world!", "Hello world"), "!")
        self.assertEqual(pipe_mod.remaining_output("Neu", ""), "Neu")
        self.assertEqual(pipe_mod.remaining_output(None, "x"), "")

    def test_manifold_type(self):
        p = pipe_mod.Pipe()
        self.assertEqual(p.type, "manifold")
        self.assertTrue(inspect.iscoroutinefunction(p.pipes))


class TestDiscovery(unittest.TestCase):
    def _pipe(self, **valves):
        p, client = make_pipe(**valves)
        p._models, p._models_at = [], None
        p._clock = [1000.0]
        p._now = lambda: p._clock[0]
        return p, client

    def test_pipes_from_discovery_with_filters(self):
        p, client = self._pipe()
        self.assertEqual(asyncio.run(p.pipes()), [
            {"id": "auto", "name": "Hermes · Auto"}, {"id": "auto-medium", "name": "Hermes · Mittel"},
            {"id": "auto-strong", "name": "auto-strong"}, {"id": "anthropic/claude", "name": "Claude"}])
        self.assertEqual(client.calls[0][:2], ("GET", "/models"))
        self.assertEqual(client.calls[0][2]["Accept"], "application/json")
        p, _ = self._pipe(OWNED_BY_FILTER="hermes", MODEL_FILTER="^auto(-strong)?$")
        self.assertEqual([m["id"] for m in asyncio.run(p.pipes())], ["auto", "auto-strong"])
        p, _ = self._pipe(HERMES_API_KEY="k1")
        asyncio.run(p.pipes())

    def test_cache_ttl(self):
        p, client = self._pipe(MODELS_CACHE_TTL=300)
        asyncio.run(p.pipes())
        p._clock[0] += 299
        asyncio.run(p.pipes())
        self.assertEqual(len(client.find("GET", "/models")), 1)
        p._clock[0] += 2
        asyncio.run(p.pipes())
        self.assertEqual(len(client.find("GET", "/models")), 2)

    def test_fetch_error_keeps_last_list_or_default(self):
        p, client = self._pipe(MODELS_CACHE_TTL=0)
        asyncio.run(p.pipes())
        client.script[("GET", "/models")] = [FakeResp(exc=ConnectionRefusedError("down"))]
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual([m["id"] for m in asyncio.run(p.pipes())], ["auto", "auto-medium", "auto-strong", "anthropic/claude"])
        p, client = self._pipe(extra={("GET", "/models"): FakeResp(exc=ConnectionRefusedError("down"))})
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual(asyncio.run(p.pipes()), [{"id": "hermes-default", "name": "Hermes (gateway default)"}])
        p, client = self._pipe(extra={("GET", "/models"): FakeResp(503, {"error": "busy"})})
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual(asyncio.run(p.pipes())[0]["id"], "hermes-default")

    def test_discovery_off(self):
        p, client = self._pipe(MODEL_DISCOVERY=False)
        self.assertEqual(asyncio.run(p.pipes()), [{"id": "hermes-default", "name": "Hermes (gateway default)"}])
        self.assertEqual(client.calls, [])

    def test_run_body_model_and_provider(self):
        p, client = make_pipe(sse(DONE))
        run_pipe(p, model="hermes_runs_pipe.auto")
        self.assertEqual(client.find("POST", "/runs")[0][3], {"input": "Hello", "stream": False, "model": "auto"})
        p, client = make_pipe(sse(DONE))
        run_pipe(p, model="hermes_runs_pipe.anthropic/claude")
        self.assertEqual(client.find("POST", "/runs")[0][3],
                         {"input": "Hello", "stream": False, "model": "anthropic/claude", "provider": "bifrost"})
        p, client = make_pipe(sse(DONE))
        run_pipe(p, model="hermes_runs_pipe.hermes-default")
        self.assertEqual(client.find("POST", "/runs")[0][3], {"input": "Hello", "stream": False})
        # A model that is not in the cache (for example after a restart) is passed on as given.
        p, client = make_pipe(sse(DONE), models={"data": []})
        run_pipe(p, model="hermes_runs_pipe.auto-strong")
        self.assertEqual(client.find("POST", "/runs")[0][3]["model"], "auto-strong")

    def test_run_body_model_options(self):
        # 0.4.0: options pass-through (filter hermes_reasoning sets model_options.reasoning_effort).
        p, client = make_pipe(sse(DONE), models=MULTI_RESP)
        run_pipe(p, model="hermes_runs_pipe.premiumize/gpt-6.1-sol", model_options={"reasoning_effort": "high"})
        self.assertEqual(client.find("POST", "/runs")[0][3], {
            "input": "Hello", "stream": False, "model": "premiumize/gpt-6.1-sol",
            "model_options": {"reasoning_effort": "high"}})
        # Allowlist drop: only MODEL_OPTIONS_KEYS go through.
        p, client = make_pipe(sse(DONE))
        run_pipe(p, model_options={"reasoning_effort": "low", "service_tier": "priority", "api_key": "x"})
        self.assertEqual(client.find("POST", "/runs")[0][3]["model_options"], {"reasoning_effort": "low"})
        p, client = make_pipe(sse(DONE), MODEL_OPTIONS_KEYS="")
        run_pipe(p, model_options={"reasoning_effort": "low"}, reasoning_effort="high")
        self.assertNotIn("model_options", client.find("POST", "/runs")[0][3])
        # Slider mapping: top-level reasoning_effort of a connection model.
        p, client = make_pipe(sse(DONE))
        run_pipe(p, model="hermes_runs_pipe.claude-sub/claude-opus-5-5", reasoning_effort="max")
        self.assertEqual(client.find("POST", "/runs")[0][3]["model_options"], {"reasoning_effort": "max"})
        p, client = make_pipe(sse(DONE))
        run_pipe(p, reasoning_effort="low", model_options={"reasoning_effort": "xhigh"})
        self.assertEqual(client.find("POST", "/runs")[0][3]["model_options"], {"reasoning_effort": "xhigh"})
        # No options: no model_options key, so config.yaml decides the effort.
        p, client = make_pipe(sse(DONE))
        run_pipe(p)
        self.assertEqual(client.find("POST", "/runs")[0][3], {"input": "Hello", "stream": False, "model": "auto"})
        p, client = make_pipe(sse(DONE))
        run_pipe(p, model_options={"service_tier": "priority"})
        self.assertNotIn("model_options", client.find("POST", "/runs")[0][3])

    def test_run_body_text_only(self):
        # 0.5.0: text only gives the same string input as 0.4.0, also for a list content without images.
        p, client = make_pipe(sse(DONE))
        chunks, _ = run_pipe(p, text=[{"type": "text", "text": "Hello"}])
        self.assertEqual(client.find("POST", "/runs")[0][3], {"input": "Hello", "stream": False, "model": "auto"})
        self.assertEqual(text_of(chunks), "done")

    def test_run_body_text_and_data_image(self):
        p, client = make_pipe(sse(DONE))
        content = [{"type": "text", "text": "What is this?"}, {"type": "image_url", "image_url": {"url": IMG_A}}]
        chunks, _ = run_pipe(p, text=content)
        self.assertEqual(client.find("POST", "/runs")[0][3], {
            "input": [{"role": "user", "content": [
                {"type": "text", "text": "What is this?"}, {"type": "image_url", "image_url": {"url": IMG_A}}]}],
            "stream": False, "model": "auto"})
        self.assertEqual(text_of(chunks), "done")
        # Image only: the run starts without a text part.
        p, client = make_pipe(sse(DONE))
        run_pipe(p, text=[{"type": "image_url", "image_url": {"url": IMG_A}}])
        self.assertEqual(client.find("POST", "/runs")[0][3]["input"],
                         [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": IMG_A}}]}])

    def test_images_too_many(self):
        p, client = make_pipe(sse(DONE), IMAGE_MAX_COUNT=2)
        urls = [data_image(n) for n in (1, 2, 3, 4)]
        content = [{"type": "text", "text": "four"}] + [{"type": "image_url", "image_url": {"url": u}} for u in urls]
        chunks, _ = run_pipe(p, text=content)
        parts = client.find("POST", "/runs")[0][3]["input"][0]["content"]
        self.assertEqual([x["image_url"]["url"] for x in parts[1:]], urls[:2])
        self.assertEqual(chunks[0], "*(2 image(s) not sent: at most 2 images per message.)*\n\n")
        self.assertTrue(text_of(chunks).endswith("done"))

    def test_images_too_big_disabled_and_unreadable(self):
        half_mb = pipe_mod.BYTES_PER_MB // 2
        p, client = make_pipe(sse(DONE), IMAGE_MAX_BYTES=half_mb)
        content = [{"type": "text", "text": "x"}, {"type": "image_url", "image_url": {"url": IMG_A}},
                   {"type": "image_url", "image_url": {"url": data_image(half_mb + 1)}},
                   {"type": "image_url", "image_url": {"url": "ftp://example.test/a.png"}}]
        chunks, _ = run_pipe(p, text=content)
        parts = client.find("POST", "/runs")[0][3]["input"][0]["content"]
        self.assertEqual([x.get("image_url", {}).get("url") for x in parts], [None, IMG_A])
        self.assertIn("1 image(s) not sent: larger than 0.5 MB.", chunks[0])
        self.assertIn("1 image(s) not sent: unsupported or unreadable source.", chunks[1])
        # IMAGES_ENABLED off: text only as a string, one note.
        p, client = make_pipe(sse(DONE), IMAGES_ENABLED=False)
        chunks, _ = run_pipe(p, text=content)
        self.assertEqual(client.find("POST", "/runs")[0][3]["input"], "x")
        self.assertEqual(chunks[0], "*(3 image(s) not sent: images are off.)*\n\n")
        # Only dropped images and no text: no run.
        p, client = make_pipe(sse(DONE), IMAGES_ENABLED=False)
        chunks, _ = run_pipe(p, text=[{"type": "image_url", "image_url": {"url": IMG_A}}])
        self.assertEqual(text_of(chunks), "*(1 image(s) not sent: images are off.)*\n\nNo user message found.")
        self.assertEqual(client.find("POST", "/runs"), [])

    def test_image_files(self):
        # Image files in __files__ and body["files"]: a data URL goes as is, a local file URL through the resolver.
        p, client = make_pipe(sse(DONE))
        asked = []

        async def resolver(url, user_id):
            asked.append((url, user_id))
            return IMG_B if url == "/api/v1/files/f1/content" else None

        p._file_resolver = resolver
        files = [{"type": "image", "url": IMG_A}, {"type": "file", "url": "/api/v1/files/f1/content",
                                                   "content_type": "image/png"}]
        chunks, _ = run_pipe(p, owui_files=files, files=[{"type": "file", "url": "f9", "content_type": "image/gif"}])
        parts = client.find("POST", "/runs")[0][3]["input"][0]["content"]
        self.assertEqual(parts, [{"type": "text", "text": "Hello"}, {"type": "image_url", "image_url": {"url": IMG_A}},
                                 {"type": "image_url", "image_url": {"url": IMG_B}}])
        self.assertEqual(asked, [("/api/v1/files/f1/content", "u1"), ("f9", "u1")])
        self.assertIn("1 image(s) not sent: unsupported or unreadable source.", chunks[0])
        # Outside Open WebUI the default resolver finds no file.
        self.assertIsNone(asyncio.run(pipe_mod.Pipe._owui_file_image("f1", "u1")))

    def test_run_body_thinking_switch(self):
        p, client = make_pipe(sse(DONE))
        run_pipe(p, reasoning_effort="off")
        self.assertEqual(client.find("POST", "/runs")[0][3]["model_options"], {"reasoning_effort": "none"})
        p, client = make_pipe(sse(DONE))
        run_pipe(p, model_options={"reasoning_effort": "Off"})
        self.assertEqual(client.find("POST", "/runs")[0][3]["model_options"], {"reasoning_effort": "none"})
        p, client = make_pipe(sse(DONE))
        run_pipe(p, model_options={"reasoning_effort": "none"})
        self.assertEqual(client.find("POST", "/runs")[0][3]["model_options"], {"reasoning_effort": "none"})

    def test_policy_first_match(self):
        policies = json.dumps([{"match": "^auto", "approval_mode": "auto", "auto_choice": "session"},
                               {"match": ".*", "approval_mode": "ask", "allowed_approval_choices": "once,deny",
                                "allowed_roles": "admin,user"}])
        p, client = make_pipe(sse(APPROVAL, DONE), MODEL_POLICIES=policies)
        dialog = Dialog()
        run_pipe(p, call=dialog, model="hermes_runs_pipe.auto-strong")
        self.assertEqual(dialog.events, [])
        self.assertEqual(client.find("POST", "/runs/r1/approval")[-1][3]["choice"], "session")
        dialog = Dialog(True)
        run_pipe(p, call=dialog, model="hermes_runs_pipe.anthropic/claude",
                 user={"id": "u2", "name": "Child", "role": "user"})
        self.assertEqual(dialog.events[0]["type"], "confirmation")
        self.assertEqual(client.find("POST", "/runs/r1/approval")[-1][3]["choice"], "once")
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            p, client = make_pipe(sse(APPROVAL_ONCE_DENY, DONE), MODEL_POLICIES="{bad")
            run_pipe(p, call=Dialog(True))
        self.assertEqual(client.find("POST", "/runs/r1/approval")[-1][3]["choice"], "once")

    def test_route_header_status(self):
        p, _ = make_pipe(sse(DONE), extra={("POST", "/runs"): FakeResp(
            202, {"run_id": "r1"}, headers={"X-Hermes-Route": "tier-complex via chat-floor"})})
        _, emitted = run_pipe(p)
        descriptions = [e["data"]["description"] for e in emitted]
        self.assertEqual(descriptions[0], "Route: tier-complex via chat-floor")
        self.assertEqual(descriptions[1], "Hermes is working")
        p, _ = make_pipe(sse(DONE))
        _, emitted = run_pipe(p)
        self.assertFalse(any(e["data"]["description"].startswith("Route:") for e in emitted))
        p, _ = make_pipe(sse(DONE), ROUTE_HEADER="", extra={("POST", "/runs"): FakeResp(
            202, {"run_id": "r1"}, headers={"X-Hermes-Route": "x"})})
        _, emitted = run_pipe(p)
        self.assertFalse(any(e["data"]["description"].startswith("Route:") for e in emitted))


APPROVAL = ev("approval.request", request_id="a1", command="rm -rf /tmp/x", description="Deletes files",
              choices=["once", "session", "always", "deny"])
APPROVAL_ONCE_DENY = ev("approval.request", request_id="a1", command="rm -rf /tmp/x",
                        description="Deletes files", choices=["once", "deny"])
DONE = ev("run.completed", output="done")


class TestPipe(unittest.TestCase):
    def test_message_delta_stream(self):
        # Spec test 4.
        p, client = make_pipe(sse(ev("run.running", status="running"), ev("message.delta", delta="Hel"),
                                  ev("message.delta", delta="lo"), ev("run.completed", output="Hello")))
        chunks, emitted = run_pipe(p)
        self.assertEqual(chunks, ["Hel", "lo"])
        self.assertIn("Hermes is working", [e["data"]["description"] for e in emitted])
        self.assertTrue(emitted[-1]["data"]["done"])

    def test_tool_progress_as_reasoning(self):
        # Spec test 5.
        stream = sse(ev("tool.started", tool="terminal", preview="ls -la", tool_call_id="t1"),
                     ev("tool.completed", tool="terminal", duration=1.5, error=True, tool_call_id="t1"),
                     ev("reasoning.available", text="thinking"), ev("run.completed", output="ok"))
        p, _ = make_pipe(stream)
        chunks, emitted = run_pipe(p)
        reasoning = reasoning_of(chunks)
        self.assertIn("> terminal: ls -la", reasoning)
        self.assertIn("terminal done (1.5 s, error)", reasoning)
        self.assertIn("thinking", reasoning)
        self.assertEqual(text_of(chunks), "ok")
        self.assertIn("terminal: ls -la", [e["data"]["description"] for e in emitted])

        p, _ = make_pipe(stream, SHOW_TOOL_PROGRESS=False)
        chunks, emitted = run_pipe(p)
        self.assertNotIn("terminal", reasoning_of(chunks))
        self.assertNotIn("terminal: ls -la", [e["data"]["description"] for e in emitted])

        # Without stream Open WebUI joins str() of the chunks: no dict chunks then.
        p, _ = make_pipe(stream)
        chunks, _ = run_pipe(p, stream=False)
        self.assertEqual(chunks, ["ok"])

    def test_run_completed_remaining_output(self):
        # Spec test 6.
        p, _ = make_pipe(sse(ev("message.delta", delta="Hello"), ev("run.completed", output="Hello world")))
        chunks, _ = run_pipe(p)
        self.assertEqual(chunks, ["Hello", " world"])

    def test_approval_confirmation_once(self):
        # Spec test 7.
        p, client = make_pipe(sse(APPROVAL_ONCE_DENY, ev("approval.responded", choice="once", resolved=1), DONE))
        dialog = Dialog(True)
        chunks, _ = run_pipe(p, call=dialog)
        self.assertEqual(dialog.events[0]["type"], "confirmation")
        self.assertEqual(dialog.events[0]["data"]["title"], "Hermes needs an approval")
        self.assertIn("rm -rf /tmp/x", dialog.events[0]["data"]["message"])
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3], {"request_id": "a1", "choice": "once"})
        self.assertEqual(text_of(chunks).count("Approval: once"), 1)
        self.assertTrue(text_of(chunks).endswith("done"))

    def test_approval_timeout_deny(self):
        # Spec test 8.
        async def never(event):
            await asyncio.Event().wait()

        p, client = make_pipe(sse(APPROVAL_ONCE_DENY, DONE), APPROVAL_TIMEOUT=0.05)
        chunks, _ = run_pipe(p, call=never)
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3]["choice"], "deny")
        self.assertIn("timed out", text_of(chunks))

    def test_approval_mode_deny(self):
        # Spec test 9.
        dialog = Dialog()
        p, client = make_pipe(sse(APPROVAL, DONE), APPROVAL_MODE="deny")
        run_pipe(p, call=dialog)
        self.assertEqual(dialog.events, [])
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3], {"request_id": "a1", "choice": "deny"})

    def test_approval_fallback_without_event_call(self):
        # Spec test 10.
        first = sse(ev("run.running"), APPROVAL, ev("message.delta", delta="nicht gelesen"))
        second = sse(ev("approval.responded", choice="once", resolved=1), ev("message.delta", delta="weiter"),
                     DONE, start=3)
        p, client = make_pipe(first, second)
        chunks, _ = run_pipe(p, call=None)
        text = text_of(chunks)
        self.assertIn("rm -rf /tmp/x", text)
        self.assertIn("/approve [once|session]", text)
        self.assertNotIn("nicht gelesen", text)
        self.assertEqual(client.find("POST", "/runs/r1/approval"), [])
        self.assertEqual(client.find("POST", "/runs/r1/stop"), [])

        chunks, _ = run_pipe(p, text="/approve", meta={**META, "message_id": "m2"}, call=None)
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3], {"request_id": "a1", "choice": "once"})
        self.assertEqual(len(client.find("POST", "/runs")), 1)
        self.assertEqual(client.find("GET", "/runs/r1/events")[1][2]["Last-Event-ID"], "2")
        self.assertIn("weiter", text_of(chunks))
        self.assertTrue(text_of(chunks).endswith("done"))
        self.assertEqual(p._pending, {})

    def test_client_cancel_sends_stop(self):
        # Spec test 11.
        p, client = make_pipe(sse(ev("message.delta", delta="a"), ev("message.delta", delta="b"), DONE))

        async def go():
            agen = p.pipe(body_for("Hello"), __user__=dict(USER), __metadata__=dict(META))
            first = await agen.__anext__()
            await agen.aclose()
            return first

        self.assertEqual(asyncio.run(go()), "a")
        self.assertEqual(len(client.find("POST", "/runs/r1/stop")), 1)

    def test_reconnect_with_last_event_id(self):
        # Spec test 12.
        first = sse(ev("run.running"), ev("message.delta", delta="a"), ev("message.delta", delta="b"),
                    ev("message.delta", delta="c"))
        p, client = make_pipe(first, sse(ev("run.completed", output="abcd"), start=5))
        chunks, _ = run_pipe(p)
        gets = client.find("GET", "/runs/r1/events")
        self.assertNotIn("Last-Event-ID", gets[0][2])
        self.assertEqual(gets[1][2]["Last-Event-ID"], "4")
        self.assertEqual(text_of(chunks), "abcd")

    def test_reconnect_limit_then_run_status(self):
        p, client = make_pipe(extra={
            ("GET", "/runs/r1/events"): FakeResp(exc=ConnectionResetError("reset")),
            ("GET", "/runs/r1"): FakeResp(200, {"run_id": "r1", "status": "completed", "output": "result"})})
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            chunks, _ = run_pipe(p)
        self.assertEqual(len(client.find("GET", "/runs/r1/events")), 1 + pipe_mod.MAX_RECONNECTS)
        self.assertEqual(text_of(chunks), "result")

    def test_hermes_unreachable(self):
        # Spec test 13.
        p, client = make_pipe(extra={("POST", "/runs"): FakeResp(exc=ConnectionRefusedError("refused"))})
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            chunks, _ = run_pipe(p)
        self.assertEqual(chunks, [f"Hermes is not reachable ({BASE})"])
        self.assertEqual(len(client.calls), 1)

    def test_reset_command(self):
        # Spec test 14 (10.2: /reset counts per chat, no HTTP).
        p, client = make_pipe()
        p._pending[("u1", "c1")] = {"run_id": "r0"}
        chunks, _ = run_pipe(p, text="/reset")
        self.assertEqual(chunks, ["Session reset"])
        self.assertEqual(client.calls, [])
        self.assertEqual(p._resets, {("u1", "c1"): 1})
        self.assertEqual(p._pending, {})

    def test_session_key_header(self):
        # Spec test 15.
        p, client = make_pipe(sse(DONE), HERMES_API_KEY="k123")
        run_pipe(p, user={"id": "u1", "name": "Jörg Ü", "role": "admin"})
        method, path, headers, payload = client.calls[0]
        self.assertEqual((method, path), ("POST", "/runs"))
        self.assertEqual(payload, {"input": "Hello", "stream": False, "model": "auto"})
        self.assertEqual(headers["X-Hermes-Session-Key"], "owui:u1:c1")
        self.assertEqual(headers["X-Hermes-User-Role"], "admin")
        self.assertEqual(headers["X-Hermes-User-Name"], "J%C3%B6rg %C3%9C")
        self.assertEqual(headers["Authorization"], "Bearer k123")
        self.assertEqual(headers["Accept-Encoding"], "identity")
        for _, _, h, _ in client.calls:
            self.assertEqual(h["X-Hermes-Session-Key"], "owui:u1:c1")

        p, client = make_pipe(sse(DONE))
        run_pipe(p)
        self.assertNotIn("Authorization", client.calls[0][2])

    def test_reset_suffix(self):
        # Spec test 16.
        p, client = make_pipe(sse(DONE))
        run_pipe(p, text="/reset")
        run_pipe(p)
        self.assertEqual(client.calls[-2][2]["X-Hermes-Session-Key"], "owui:u1:c1:r1")
        run_pipe(p, text="/reset")
        run_pipe(p)
        self.assertEqual(client.calls[-2][2]["X-Hermes-Session-Key"], "owui:u1:c1:r2")
        run_pipe(p, meta={**META, "chat_id": "c2"})
        self.assertEqual(client.calls[-2][2]["X-Hermes-Session-Key"], "owui:u1:c2")

    def test_idempotency_header(self):
        # Spec test 17 (request part).
        p, client = make_pipe(sse(DONE))
        run_pipe(p, meta={**META, "message_id": "m 1ä"})
        key = client.calls[0][2]["Idempotency-Key"]
        self.assertEqual(key, "owui:c1:m1")
        self.assertNotIn("Idempotency-Key", client.calls[1][2])

    def test_input_dialog_multi_choice(self):
        # Spec test 19.
        p, client = make_pipe(sse(APPROVAL, DONE))
        dialog = Dialog("session")
        run_pipe(p, call=dialog)
        self.assertEqual(dialog.events[0]["type"], "input")
        self.assertIn("Answer: once, deny or session", dialog.events[0]["data"]["message"])
        self.assertEqual(dialog.events[0]["data"]["value"], "once")
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3]["choice"], "session")

        p, client = make_pipe(sse(APPROVAL, DONE))
        chunks, _ = run_pipe(p, call=Dialog("foo"))
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3]["choice"], "deny")
        self.assertIn("invalid input", text_of(chunks))

        # always is offered only if the admin allows it.
        p, client = make_pipe(sse(APPROVAL, DONE))
        run_pipe(p, call=Dialog("always"))
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3]["choice"], "deny")

    def test_auto_mode_choice_fallback(self):
        # Spec test 20.
        dialog = Dialog()
        p, client = make_pipe(sse(APPROVAL_ONCE_DENY, DONE), APPROVAL_MODE="auto", AUTO_CHOICE="session")
        run_pipe(p, call=dialog)
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3]["choice"], "once")
        p, client = make_pipe(sse(APPROVAL, DONE), APPROVAL_MODE="auto", AUTO_CHOICE="session")
        run_pipe(p, call=dialog)
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3]["choice"], "session")
        self.assertEqual(dialog.events, [])

    def test_role_denied(self):
        # Spec test 21.
        p, client = make_pipe(sse(DONE))
        chunks, _ = run_pipe(p, user={"id": "u2", "name": "Child", "role": "user"})
        self.assertEqual(chunks, ["This model is not enabled for your account"])
        self.assertEqual(client.calls, [])

    def test_texts_valve_override(self):
        # 0.3.0: the valve TEXTS overrides UI texts by key; unknown keys and non-string values are dropped.
        p, client = make_pipe(sse(DONE), TEXTS='{"denied": "Nicht freigegeben", "unknown_key": "x", "working": 5}')
        chunks, _ = run_pipe(p, user={"id": "u2", "name": "Child", "role": "user"})
        self.assertEqual(chunks, ["Nicht freigegeben"])
        self.assertEqual(client.calls, [])
        self.assertEqual(p._t("working"), pipe_mod.TEXTS["working"])
        self.assertEqual(pipe_mod.parse_texts("")["working"], pipe_mod.TEXTS["working"])
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual(pipe_mod.parse_texts("{not json"), pipe_mod.TEXTS)
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual(pipe_mod.parse_texts("[1, 2]"), pipe_mod.TEXTS)
        p.valves.TEXTS = '{"route": "Pfad {nope}"}'
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual(p._t("route", route="x"), "Route: x")

    def test_manifold_policies_per_model(self):
        # Spec test 22 (0.2.0: policies per regex instead of MODELS entries).
        policies = json.dumps([{"match": "^auto$", "approval_mode": "auto"},
                               {"match": "guarded|claude", "approval_mode": "ask",
                                "allowed_approval_choices": "once,deny", "allowed_roles": "admin,user"}])
        p, client = make_pipe(sse(APPROVAL, DONE), MODEL_POLICIES=policies)
        self.assertEqual([m["id"] for m in asyncio.run(p.pipes())],
                         ["auto", "auto-medium", "auto-strong", "anthropic/claude"])
        dialog = Dialog()
        run_pipe(p, call=dialog, model="hermes_runs_pipe.auto")
        self.assertEqual(dialog.events, [])
        self.assertEqual(client.find("POST", "/runs/r1/approval")[-1][3]["choice"], "session")

        dialog = Dialog(True)
        run_pipe(p, call=dialog, model="hermes_runs_pipe.anthropic/claude",
                 user={"id": "u2", "name": "Child", "role": "user"})
        self.assertEqual(dialog.events[0]["type"], "confirmation")
        self.assertEqual(client.find("POST", "/runs/r1/approval")[-1][3]["choice"], "once")

    def test_clarify_input_dialog(self):
        p, client = make_pipe(sse(ev("clarify.request", prompt_id="q1", execution_generation=3,
                                     question="Which file?", choices=["a.txt", "b.txt"]), DONE))
        dialog = Dialog("a.txt")
        run_pipe(p, call=dialog)
        self.assertEqual(dialog.events[0]["type"], "input")
        self.assertIn("Which file?", dialog.events[0]["data"]["message"])
        self.assertEqual(client.find("POST", "/runs/r1/clarify")[0][3],
                         {"request_id": "q1", "execution_generation": 3, "answer": "a.txt"})

    def test_failed_and_truncated(self):
        p, client = make_pipe(sse(ev("replay.truncated", oldest_retained_seq=5),
                                  ev("run.failed", error="boom")))
        chunks, _ = run_pipe(p)
        self.assertIn("Parts of the progress are missing", text_of(chunks))
        self.assertTrue(text_of(chunks).endswith("Run failed: boom"))
        self.assertEqual(client.find("POST", "/runs/r1/stop"), [])

    def test_idle_timeout_stops_run(self):
        p, client = make_pipe(sse(ev("message.delta", delta="a")) + [HANG], STREAM_IDLE_TIMEOUT=0.05)
        chunks, _ = run_pipe(p)
        self.assertIn("No data from Hermes", text_of(chunks))
        self.assertEqual(len(client.find("POST", "/runs/r1/stop")), 1)

    def test_unexpected_error_one_line(self):
        p, _ = make_pipe(extra={("POST", "/runs"): FakeResp(exc=ValueError("broken"))})
        with self.assertLogs(pipe_mod.log, level="ERROR"):
            chunks, _ = run_pipe(p)
        self.assertEqual(len(chunks), 1)
        self.assertIn("ValueError: broken", chunks[0])

    def test_data_prefix_text_and_approval_conflict(self):
        p, client = make_pipe(sse(ev("message.delta", delta="data: x"), APPROVAL_ONCE_DENY, DONE),
                              extra={("POST", "/runs/r1/approval"): FakeResp(409, {"error": {"message": "no pending"}})})
        chunks, _ = run_pipe(p, call=Dialog(True))
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "data: x")
        self.assertIn("not sent: HTTP 409: no pending", text_of(chunks))
        self.assertTrue(text_of(chunks).endswith("done"))

    def test_slash_without_pending_and_task(self):
        p, client = make_pipe(sse(DONE))
        chunks, _ = run_pipe(p, text="/approve")
        self.assertEqual(chunks, ["No open approval in this chat."])
        chunks, _ = run_pipe(p, meta={**META, "task": "title_generation"})
        self.assertEqual(chunks, [""])
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
