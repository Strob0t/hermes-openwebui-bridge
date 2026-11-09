import asyncio
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
MODEL = "hermes_runs_pipe.hermes-runs"
HANG = object()


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


def make_pipe(*streams, extra=None, **valves):
    """Returns (pipe, client); each stream is the chunk list of one GET /runs/r1/events."""
    p = pipe_mod.Pipe()
    p.valves.HERMES_BASE_URL = BASE
    for key, value in valves.items():
        setattr(p.valves, key, value)
    script = {
        ("POST", "/runs"): FakeResp(202, {"run_id": "r1", "status": "queued"}),
        ("GET", "/runs/r1/events"): [FakeResp(chunks=s) for s in streams] or [FakeResp(chunks=[])],
        ("POST", "/runs/r1/approval"): FakeResp(200, {"object": "hermes.run.approval_response", "resolved": 1}),
        ("POST", "/runs/r1/clarify"): FakeResp(200, {"status": "resolved"}),
        ("POST", "/runs/r1/stop"): FakeResp(200, {"run_id": "r1", "status": "stopping"}),
    }
    script.update(extra or {})
    client = FakeClient(script)
    p._client_factory = lambda: client
    return p, client


def body_for(text, stream=True, model=MODEL):
    return {"model": model, "stream": stream, "messages": [{"role": "user", "content": text}]}


def run_pipe(p, text="Hallo", stream=True, user=None, meta=None, call=None, model=MODEL):
    """Runs the pipe to its end. Returns (chunks, emitted events)."""
    emitted = []

    async def emit(event):
        emitted.append(event)

    async def go():
        agen = p.pipe(body_for(text, stream, model), __user__=dict(user or USER),
                      __metadata__=dict(meta or META), __event_emitter__=emit, __event_call__=call)
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
    def test_parse_models_default_and_bad_json(self):
        self.assertEqual(pipe_mod.parse_models('[{"id":"hermes-runs","name":"Hermes Runs"}]'),
                         [{"id": "hermes-runs", "name": "Hermes Runs"}])
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual(pipe_mod.parse_models("{not json"), pipe_mod.DEFAULT_MODELS)
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            self.assertEqual(pipe_mod.parse_models('[{"name":"no id"}]'), pipe_mod.DEFAULT_MODELS)
        models = pipe_mod.parse_models('[{"id":" x ","approval_mode":"deny"}]')
        self.assertEqual(models, [{"id": "x", "name": "x", "approval_mode": "deny"}])

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
        self.assertEqual(pipe_mod.remaining_output("Hallo Welt", "Hallo"), " Welt")
        self.assertEqual(pipe_mod.remaining_output("Hallo Welt", "Hallo Welt"), "")
        self.assertEqual(pipe_mod.remaining_output("Welt!", "Hallo Welt"), "!")
        self.assertEqual(pipe_mod.remaining_output("Neu", ""), "Neu")
        self.assertEqual(pipe_mod.remaining_output(None, "x"), "")

    def test_manifold_pipes_list(self):
        p = pipe_mod.Pipe()
        self.assertEqual(p.type, "manifold")
        self.assertEqual(p.pipes(), [{"id": "hermes-runs", "name": "Hermes Runs"}])
        p.valves.MODELS = json.dumps([{"id": "a", "name": "A", "approval_mode": "auto"}, {"id": "b"}])
        self.assertEqual(p.pipes(), [{"id": "a", "name": "A"}, {"id": "b", "name": "b"}])


APPROVAL = ev("approval.request", request_id="a1", command="rm -rf /tmp/x", description="Löscht Dateien",
              choices=["once", "session", "always", "deny"])
APPROVAL_ONCE_DENY = ev("approval.request", request_id="a1", command="rm -rf /tmp/x",
                        description="Löscht Dateien", choices=["once", "deny"])
DONE = ev("run.completed", output="fertig")


class TestPipe(unittest.TestCase):
    def test_message_delta_stream(self):
        # Spec test 4.
        p, client = make_pipe(sse(ev("run.running", status="running"), ev("message.delta", delta="Hal"),
                                  ev("message.delta", delta="lo"), ev("run.completed", output="Hallo")))
        chunks, emitted = run_pipe(p)
        self.assertEqual(chunks, ["Hal", "lo"])
        self.assertIn("Hermes arbeitet", [e["data"]["description"] for e in emitted])
        self.assertTrue(emitted[-1]["data"]["done"])

    def test_tool_progress_as_reasoning(self):
        # Spec test 5.
        stream = sse(ev("tool.started", tool="terminal", preview="ls -la", tool_call_id="t1"),
                     ev("tool.completed", tool="terminal", duration=1.5, error=True, tool_call_id="t1"),
                     ev("reasoning.available", text="denke nach"), ev("run.completed", output="ok"))
        p, _ = make_pipe(stream)
        chunks, emitted = run_pipe(p)
        reasoning = reasoning_of(chunks)
        self.assertIn("> terminal: ls -la", reasoning)
        self.assertIn("terminal fertig (1.5 s, Fehler)", reasoning)
        self.assertIn("denke nach", reasoning)
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
        p, _ = make_pipe(sse(ev("message.delta", delta="Hallo"), ev("run.completed", output="Hallo Welt")))
        chunks, _ = run_pipe(p)
        self.assertEqual(chunks, ["Hallo", " Welt"])

    def test_approval_confirmation_once(self):
        # Spec test 7.
        p, client = make_pipe(sse(APPROVAL_ONCE_DENY, ev("approval.responded", choice="once", resolved=1), DONE))
        dialog = Dialog(True)
        chunks, _ = run_pipe(p, call=dialog)
        self.assertEqual(dialog.events[0]["type"], "confirmation")
        self.assertEqual(dialog.events[0]["data"]["title"], "Hermes braucht eine Freigabe")
        self.assertIn("rm -rf /tmp/x", dialog.events[0]["data"]["message"])
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3], {"request_id": "a1", "choice": "once"})
        self.assertEqual(text_of(chunks).count("Freigabe: once"), 1)
        self.assertTrue(text_of(chunks).endswith("fertig"))

    def test_approval_timeout_deny(self):
        # Spec test 8.
        async def never(event):
            await asyncio.Event().wait()

        p, client = make_pipe(sse(APPROVAL_ONCE_DENY, DONE), APPROVAL_TIMEOUT=0.05)
        chunks, _ = run_pipe(p, call=never)
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3]["choice"], "deny")
        self.assertIn("Zeit abgelaufen", text_of(chunks))

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
        self.assertTrue(text_of(chunks).endswith("fertig"))
        self.assertEqual(p._pending, {})

    def test_client_cancel_sends_stop(self):
        # Spec test 11.
        p, client = make_pipe(sse(ev("message.delta", delta="a"), ev("message.delta", delta="b"), DONE))

        async def go():
            agen = p.pipe(body_for("Hallo"), __user__=dict(USER), __metadata__=dict(META))
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
            ("GET", "/runs/r1"): FakeResp(200, {"run_id": "r1", "status": "completed", "output": "Ergebnis"})})
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            chunks, _ = run_pipe(p)
        self.assertEqual(len(client.find("GET", "/runs/r1/events")), 1 + pipe_mod.MAX_RECONNECTS)
        self.assertEqual(text_of(chunks), "Ergebnis")

    def test_hermes_unreachable(self):
        # Spec test 13.
        p, client = make_pipe(extra={("POST", "/runs"): FakeResp(exc=ConnectionRefusedError("refused"))})
        with self.assertLogs(pipe_mod.log, level="WARNING"):
            chunks, _ = run_pipe(p)
        self.assertEqual(chunks, [f"Hermes ist nicht erreichbar ({BASE})"])
        self.assertEqual(len(client.calls), 1)

    def test_reset_command(self):
        # Spec test 14 (10.2: /reset counts per chat, no HTTP).
        p, client = make_pipe()
        p._pending[("u1", "c1")] = {"run_id": "r0"}
        chunks, _ = run_pipe(p, text="/reset")
        self.assertEqual(chunks, ["Session zurückgesetzt"])
        self.assertEqual(client.calls, [])
        self.assertEqual(p._resets, {("u1", "c1"): 1})
        self.assertEqual(p._pending, {})

    def test_session_key_header(self):
        # Spec test 15.
        p, client = make_pipe(sse(DONE), HERMES_API_KEY="k123")
        run_pipe(p, user={"id": "u1", "name": "Jörg Ü", "role": "admin"})
        method, path, headers, payload = client.calls[0]
        self.assertEqual((method, path), ("POST", "/runs"))
        self.assertEqual(payload, {"input": "Hallo", "stream": False})
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
        self.assertIn("Antwort: once, deny oder session", dialog.events[0]["data"]["message"])
        self.assertEqual(dialog.events[0]["data"]["value"], "once")
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3]["choice"], "session")

        p, client = make_pipe(sse(APPROVAL, DONE))
        chunks, _ = run_pipe(p, call=Dialog("foo"))
        self.assertEqual(client.find("POST", "/runs/r1/approval")[0][3]["choice"], "deny")
        self.assertIn("ungültige Eingabe", text_of(chunks))

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
        chunks, _ = run_pipe(p, user={"id": "u2", "name": "Kind", "role": "user"})
        self.assertEqual(chunks, ["Dieses Modell ist für dein Konto nicht freigegeben"])
        self.assertEqual(client.calls, [])

    def test_manifold_pipes(self):
        # Spec test 22.
        models = [{"id": "hermes-runs", "name": "Hermes Runs", "approval_mode": "auto"},
                  {"id": "hermes-runs-guarded", "name": "Hermes Runs (Freigabe)", "approval_mode": "ask",
                   "allowed_approval_choices": "once,deny", "allowed_roles": "admin,user"}]
        p, client = make_pipe(sse(APPROVAL, DONE), MODELS=json.dumps(models))
        self.assertEqual(p.pipes(), [{"id": "hermes-runs", "name": "Hermes Runs"},
                                     {"id": "hermes-runs-guarded", "name": "Hermes Runs (Freigabe)"}])
        dialog = Dialog()
        run_pipe(p, call=dialog, model="hermes_runs_pipe.hermes-runs")
        self.assertEqual(dialog.events, [])
        self.assertEqual(client.find("POST", "/runs/r1/approval")[-1][3]["choice"], "session")

        dialog = Dialog(True)
        run_pipe(p, call=dialog, model="hermes_runs_pipe.hermes-runs-guarded",
                 user={"id": "u2", "name": "Kind", "role": "user"})
        self.assertEqual(dialog.events[0]["type"], "confirmation")
        self.assertEqual(client.find("POST", "/runs/r1/approval")[-1][3]["choice"], "once")

    def test_clarify_input_dialog(self):
        p, client = make_pipe(sse(ev("clarify.request", prompt_id="q1", execution_generation=3,
                                     question="Welche Datei?", choices=["a.txt", "b.txt"]), DONE))
        dialog = Dialog("a.txt")
        run_pipe(p, call=dialog)
        self.assertEqual(dialog.events[0]["type"], "input")
        self.assertIn("Welche Datei?", dialog.events[0]["data"]["message"])
        self.assertEqual(client.find("POST", "/runs/r1/clarify")[0][3],
                         {"request_id": "q1", "execution_generation": 3, "answer": "a.txt"})

    def test_failed_and_truncated(self):
        p, client = make_pipe(sse(ev("replay.truncated", oldest_retained_seq=5),
                                  ev("run.failed", error="boom")))
        chunks, _ = run_pipe(p)
        self.assertIn("Teile des Fortschritts fehlen", text_of(chunks))
        self.assertTrue(text_of(chunks).endswith("Run fehlgeschlagen: boom"))
        self.assertEqual(client.find("POST", "/runs/r1/stop"), [])

    def test_idle_timeout_stops_run(self):
        p, client = make_pipe(sse(ev("message.delta", delta="a")) + [HANG], STREAM_IDLE_TIMEOUT=0.05)
        chunks, _ = run_pipe(p)
        self.assertIn("Keine Daten von Hermes", text_of(chunks))
        self.assertEqual(len(client.find("POST", "/runs/r1/stop")), 1)

    def test_unexpected_error_one_line(self):
        p, _ = make_pipe(extra={("POST", "/runs"): FakeResp(exc=ValueError("kaputt"))})
        with self.assertLogs(pipe_mod.log, level="ERROR"):
            chunks, _ = run_pipe(p)
        self.assertEqual(len(chunks), 1)
        self.assertIn("ValueError: kaputt", chunks[0])

    def test_data_prefix_text_and_approval_conflict(self):
        p, client = make_pipe(sse(ev("message.delta", delta="data: x"), APPROVAL_ONCE_DENY, DONE),
                              extra={("POST", "/runs/r1/approval"): FakeResp(409, {"error": {"message": "no pending"}})})
        chunks, _ = run_pipe(p, call=Dialog(True))
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "data: x")
        self.assertIn("nicht gesendet: HTTP 409: no pending", text_of(chunks))
        self.assertTrue(text_of(chunks).endswith("fertig"))

    def test_slash_without_pending_and_task(self):
        p, client = make_pipe(sse(DONE))
        chunks, _ = run_pipe(p, text="/approve")
        self.assertEqual(chunks, ["Keine offene Freigabe in diesem Chat."])
        chunks, _ = run_pipe(p, meta={**META, "task": "title_generation"})
        self.assertEqual(chunks, [""])
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
