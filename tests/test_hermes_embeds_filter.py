import asyncio
import sys
import unittest
from pathlib import Path

# Add functions directory to sys.path
FUNCTIONS_DIR = Path(__file__).resolve().parent.parent / "functions"
sys.path.insert(0, str(FUNCTIONS_DIR))

import hermes_embeds_filter as filter_mod


class TestHermesEmbedsFilter(unittest.TestCase):
    def test_wrap_html(self):
        inner = "<div>Hello</div>"
        wrapped = filter_mod.wrap_html(inner)
        self.assertIn("<!DOCTYPE html>", wrapped)
        self.assertIn("postMessage", wrapped)
        self.assertIn("iframe:resize", wrapped)
        self.assertIn("ResizeObserver", wrapped)
        self.assertIn(inner, wrapped)

    def test_extract_fences_zero(self):
        text = "No fences here."
        fences = filter_mod.extract_fences(text)
        self.assertEqual(fences, [])

    def test_extract_fences_single(self):
        text = "Hello\n```viz-html\n<p>Chart</p>\n```\nWorld"
        fences = filter_mod.extract_fences(text)
        self.assertEqual(len(fences), 1)
        start, end, content = fences[0]
        self.assertEqual(content.strip(), "<p>Chart</p>")

    def test_extract_fences_three(self):
        text = "A\n```viz-html\n1\n```\nB\n```viz-html\n2\n```\nC\n```viz-html\n3\n```\nD"
        fences = filter_mod.extract_fences(text)
        self.assertEqual(len(fences), 3)
        self.assertEqual(fences[0][2].strip(), "1")
        self.assertEqual(fences[1][2].strip(), "2")
        self.assertEqual(fences[2][2].strip(), "3")

    def test_extract_fences_crlf(self):
        text = "Line\r\n```viz-html\r\n<p>CRLF</p>\r\n```\r\nEnd"
        fences = filter_mod.extract_fences(text)
        self.assertEqual(len(fences), 1)
        self.assertIn("CRLF", fences[0][2])

    def test_extract_fences_unterminated(self):
        text = "Start\n```viz-html\nUnterminated block"
        fences = filter_mod.extract_fences(text)
        self.assertEqual(fences, [])

    def test_max_bytes_limit(self):
        valves = filter_mod.Filter.Valves()
        valves.max_bytes = 10
        text = "```viz-html\nthis content is way too long for max_bytes\n```"
        new_text, embeds, skipped = filter_mod.build_embeds(text, valves)
        self.assertEqual(embeds, [])
        self.assertTrue(len(skipped) > 0)
        self.assertIn("```viz-html", new_text)

    def test_max_embeds_limit(self):
        valves = filter_mod.Filter.Valves()
        valves.max_embeds = 1
        text = "```viz-html\n1\n```\n```viz-html\n2\n```"
        new_text, embeds, skipped = filter_mod.build_embeds(text, valves)
        self.assertEqual(len(embeds), 1)
        self.assertIn("exceeds max_embeds", skipped[0])

    def test_skip_prefixes(self):
        self.assertFalse(filter_mod.is_hermes_model("bifrost.llama", "bifrost."))
        self.assertFalse(filter_mod.is_hermes_model("BIFROST.test", "bifrost."))
        self.assertTrue(filter_mod.is_hermes_model("hermes-agent", "bifrost."))
        self.assertTrue(filter_mod.is_hermes_model("openai.gpt-4", "bifrost., custom."))

    def test_keep_code(self):
        valves = filter_mod.Filter.Valves()
        valves.keep_code = True
        text = "Before\n```viz-html\n<canvas></canvas>\n```\nAfter"
        new_text, embeds, skipped = filter_mod.build_embeds(text, valves)
        self.assertEqual(len(embeds), 1)
        self.assertIn("```viz-html", new_text)

    def test_outlet_success(self):
        f = filter_mod.Filter()
        body = {
            "model": "hermes-agent",
            "messages": [
                {"role": "user", "content": "Show me a chart"},
                {"role": "assistant", "content": "Here:\n```viz-html\n<canvas id='c'></canvas>\n```\nDone"}
            ]
        }
        emitted = []
        async def mock_emitter(data):
            emitted.append(data)

        async def run():
            return await f.outlet(body, __event_emitter__=mock_emitter)

        res_body = asyncio.run(run())
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["type"], "embeds")
        self.assertEqual(len(emitted[0]["data"]["embeds"]), 1)
        self.assertTrue(emitted[0]["data"]["replace"])
        self.assertIn("*(Diagramm oben eingebettet)*", res_body["messages"][-1]["content"])

    def test_outlet_idempotent(self):
        f = filter_mod.Filter()
        body = {
            "model": "hermes-agent",
            "messages": [
                {"role": "assistant", "content": "Already embedded: *(Diagramm oben eingebettet)*"}
            ]
        }
        emitted = []
        async def mock_emitter(data):
            emitted.append(data)

        async def run():
            return await f.outlet(body, __event_emitter__=mock_emitter)

        res_body = asyncio.run(run())
        self.assertEqual(len(emitted), 0)
        self.assertEqual(res_body["messages"][-1]["content"], "Already embedded: *(Diagramm oben eingebettet)*")

    def test_outlet_task_metadata(self):
        f = filter_mod.Filter()
        body = {
            "model": "hermes-agent",
            "messages": [
                {"role": "assistant", "content": "```viz-html\n<canvas></canvas>\n```"}
            ]
        }
        emitted = []
        async def mock_emitter(data):
            emitted.append(data)

        async def run():
            return await f.outlet(body, __event_emitter__=mock_emitter, __metadata__={"task": "title_generation"})

        res_body = asyncio.run(run())
        self.assertEqual(len(emitted), 0)
        self.assertIn("```viz-html", res_body["messages"][-1]["content"])

    def test_outlet_no_emitter(self):
        f = filter_mod.Filter()
        body = {
            "model": "hermes-agent",
            "messages": [
                {"role": "assistant", "content": "```viz-html\n<canvas></canvas>\n```"}
            ]
        }
        async def run():
            return await f.outlet(body, __event_emitter__=None)

        res_body = asyncio.run(run())
        self.assertIn("```viz-html", res_body["messages"][-1]["content"])

    def test_outlet_exception_fail_open(self):
        f = filter_mod.Filter()
        body = {
            "model": "hermes-agent",
            "messages": [
                {"role": "assistant", "content": "```viz-html\n<canvas></canvas>\n```"}
            ]
        }
        orig_content = body["messages"][-1]["content"]

        # Monkeypatch build_embeds to raise an exception
        original_build = filter_mod.build_embeds
        filter_mod.build_embeds = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fail"))

        emitted = []
        async def mock_emitter(data):
            emitted.append(data)

        try:
            async def run():
                return await f.outlet(body, __event_emitter__=mock_emitter)

            res_body = asyncio.run(run())
            # Should fail open, returning body unchanged
            self.assertEqual(res_body["messages"][-1]["content"], orig_content)
            self.assertEqual(len(emitted), 0)
        finally:
            filter_mod.build_embeds = original_build


if __name__ == "__main__":
    unittest.main()
