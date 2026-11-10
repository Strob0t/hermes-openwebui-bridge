"""
title: Hermes Embeds Filter
author: Strob0t
version: 0.2.0
license: AGPL-3.0
description: Converts viz-html code blocks in Hermes responses into native Open WebUI embeds (interactive charts and dashboards).
required_open_webui_version: 0.12.0
"""

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Optional

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
LOG_PREFIX = "hermes embeds filter"

# --- Defaults (also the valve defaults) -----------------------------------------------------------
DEFAULT_FENCE_LANG = "viz-html"
DEFAULT_MAX_BYTES = 60000
DEFAULT_MAX_EMBEDS = 3
DEFAULT_KEEP_CODE = False
DEFAULT_SKIP_PREFIXES = "bifrost."
DEFAULT_PRIORITY = 5
DEFAULT_REPLACEMENT_TEXT = "*(chart embedded above)*"

# --- HTML wrapper parts ---------------------------------------------------------------------------
HTML_HEAD = '<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{margin:0;font-family:system-ui}</style></head>'
HTML_RESIZE_SCRIPT = ("<script>new ResizeObserver(()=>parent.postMessage({type:'iframe:resize',"
                      "height:document.body.scrollHeight},'*')).observe(document.body)</script>")
HTML_TAIL = "</body></html>"

# --- Skip reasons (log only) ----------------------------------------------------------------------
SKIP_TOO_LARGE = "block size {size} bytes exceeds max_bytes ({limit})"
SKIP_TOO_MANY = "exceeds max_embeds limit ({limit})"


def extract_fences(text: str, lang: str = DEFAULT_FENCE_LANG) -> list[tuple[int, int, str]]:
    """Extracts fenced code blocks matching the given language tag.
    Returns a list of (start_index, end_index, inner_content).
    Note: Nested backticks inside HTML are not supported by simple fence matching.
    """
    if not text:
        return []
    pattern = re.compile(
        r"(?m)^([ \t]*)(`{3,}|~{3,})([ \t]*)([^\r\n]*)\r?\n(.*?)\r?\n\1(\2)[ \t]*(?:\r?\n|$)",
        re.DOTALL,
    )
    fences = []
    for m in pattern.finditer(text):
        indent, fence_char, space, info, content, close_chars = m.groups()
        info_lang = info.strip().lower()
        if info_lang == lang.lower():
            if len(close_chars) >= len(fence_char) and close_chars[0] == fence_char[0]:
                fences.append((m.start(), m.end(), content))
    return fences


def wrap_html(inner: str) -> str:
    """Wraps HTML inner content into a complete document with DOCTYPE, UTF-8 meta, minimal CSS, and ResizeObserver postMessage script."""
    return f"{HTML_HEAD}<body>{inner}{HTML_RESIZE_SCRIPT}{HTML_TAIL}"


def replace_fences(
    text: str,
    spans: list[tuple[int, int, str]],
    replacement: str,
    keep_code: bool,
) -> str:
    """Replaces extracted fence spans with replacement_text if keep_code is False.
    Spans are sorted by start index descending to avoid index shifting.
    """
    if keep_code or not spans:
        return text
    sorted_spans = sorted(spans, key=lambda s: s[0], reverse=True)
    res = text
    for start, end, _ in sorted_spans:
        res = res[:start] + replacement + res[end:]
    return res


def is_hermes_model(model_id: str, skip_prefixes: str) -> bool:
    """Returns True if the model is a Hermes model (does not start with any skip prefix)."""
    if not model_id:
        return True
    skips = tuple(p.strip().lower() for p in skip_prefixes.split(",") if p.strip())
    mid = model_id.lower()
    return not any(mid.startswith(s) for s in skips)


def build_embeds(text: str, valves: Any) -> tuple[str, list[str], list[str]]:
    """Extracts fence blocks, validates size and max count, wraps HTML, replaces text.
    Returns (new_text, html_embeds, skipped_reasons).
    """
    lang = getattr(valves, "fence_lang", DEFAULT_FENCE_LANG)
    max_bytes = getattr(valves, "max_bytes", DEFAULT_MAX_BYTES)
    max_embeds = getattr(valves, "max_embeds", DEFAULT_MAX_EMBEDS)
    keep_code = getattr(valves, "keep_code", DEFAULT_KEEP_CODE)
    replacement_text = getattr(valves, "replacement_text", DEFAULT_REPLACEMENT_TEXT)

    fences = extract_fences(text, lang)
    if not fences:
        return text, [], []

    valid_spans = []
    html_embeds = []
    skipped_reasons = []

    for start, end, content in fences:
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > max_bytes:
            skipped_reasons.append(SKIP_TOO_LARGE.format(size=len(content_bytes), limit=max_bytes))
            continue
        if len(html_embeds) >= max_embeds:
            skipped_reasons.append(SKIP_TOO_MANY.format(limit=max_embeds))
            continue

        wrapped = wrap_html(content)
        html_embeds.append(wrapped)
        valid_spans.append((start, end, content))

    new_text = replace_fences(text, valid_spans, replacement_text, keep_code)
    return new_text, html_embeds, skipped_reasons


class Filter:
    class Valves(BaseModel):
        enabled: bool = Field(
            default=True,
            description="Enable or disable the Hermes embeds filter",
        )
        priority: int = Field(
            default=DEFAULT_PRIORITY,
            description="Filter priority",
        )
        skip_prefixes: str = Field(
            default=DEFAULT_SKIP_PREFIXES,
            description="Comma-separated model ID prefixes to skip (direct Bifrost connection)",
        )
        max_embeds: int = Field(
            default=DEFAULT_MAX_EMBEDS,
            description="Maximum number of embeds per message",
        )
        max_bytes: int = Field(
            default=DEFAULT_MAX_BYTES,
            description="Maximum byte size per embed block",
        )
        keep_code: bool = Field(
            default=DEFAULT_KEEP_CODE,
            description="Keep the code block in text alongside the embed",
        )
        fence_lang: str = Field(
            default=DEFAULT_FENCE_LANG,
            description="Fence language tag for embed blocks",
        )
        replacement_text: str = Field(
            default=DEFAULT_REPLACEMENT_TEXT,
            description="Text replacing the embedded code block (Markdown), e.g. a translation",
        )
        debug: bool = Field(
            default=False,
            description="Enable debug logging",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __model__: Optional[dict] = None) -> dict:
        return body

    async def outlet(
        self,
        body: dict,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
        __model__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict:
        try:
            if not self.valves.enabled:
                return body
            if __event_emitter__ is None:
                return body

            # Task detection
            is_task = False
            if __metadata__ and isinstance(__metadata__, dict):
                if __metadata__.get("task") or __metadata__.get("command"):
                    is_task = True
            if isinstance(body, dict) and body.get("task"):
                is_task = True
            if is_task:
                return body

            # Skip check for model id
            model_id = str(body.get("model") or "")
            if not is_hermes_model(model_id, self.valves.skip_prefixes):
                return body

            messages = body.get("messages")
            if not isinstance(messages, list) or not messages:
                return body

            last_msg = messages[-1]
            if not isinstance(last_msg, dict):
                return body

            content = last_msg.get("content")
            if not isinstance(content, str) or not content.strip():
                return body

            new_text, html_embeds, skipped = build_embeds(content, self.valves)
            if not html_embeds:
                return body

            if self.valves.debug:
                log.debug("%s: emitting %d embeds, skipped: %s", LOG_PREFIX, len(html_embeds), skipped)

            await __event_emitter__({
                "type": "embeds",
                "data": {
                    "embeds": html_embeds,
                    "replace": True,
                },
            })

            last_msg["content"] = new_text

        except Exception as e:
            log.warning("%s failed: %s", LOG_PREFIX, e)

        return body
