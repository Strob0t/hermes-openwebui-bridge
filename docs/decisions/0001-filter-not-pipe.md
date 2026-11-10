# ADR 0001: Open WebUI Filter instead of Pipe for Visual Embeds

- **Status:** Accepted (2026-11-09)
- **Context:** For interactive HTML/Chart.js visualizations from Hermes responses, it was necessary to choose whether Open WebUI is connected via a pipe or a filter.
- **Decision:** An Open WebUI **filter** (`hermes_embeds_filter.py`) is used instead of a pipe.
- **Rationale:**
  1. The existing connection via the `hermes-models` router (including SSE streaming and `hermes_reasoning_filter.py`) is fully preserved.
  2. A filter accesses the completed message in the `outlet` hook and emits `embeds` events via `__event_emitter__`, while unsupported clients or error situations provide an automatic artifact fallback through the ```viz-html code block (fail-open).
- **Consequences:** No change to upstream model routing; seamless integration alongside existing filters.
