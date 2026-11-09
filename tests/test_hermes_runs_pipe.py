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


if __name__ == "__main__":
    unittest.main()
