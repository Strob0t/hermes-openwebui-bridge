# Notes: Analysis Hermes + Open WebUI Visualization (2026-11-09)

## Current state (proven)
- Open WebUI: ghcr.io/open-webui/open-webui:v0.12.4 (compose.yaml ki l.453, komodo inspect running). Upstream latest: v0.13.3 (GitHub releases/latest).
- Open WebUI env (compose ki l.470-509): WEBUI_URL https://chat.home.lan, OPENAI_API_BASE_URL http://hermes-gw.hermes:8642/v1 (seed, live in DB), DEFAULT_MODELS hermes-agent, OFFLINE_MODE true, CORS_ALLOW_ORIGIN, proxy squid-assistant. NO IFRAME_CSP, NO ENABLE_ARTIFACTS or similar in compose.
- Functions in the repo (appdata/open-webui/functions): hermes_reasoning_filter.py, chat_condense_filter.py (1.1.1), context_budget_filter.py (1.1.0), claude_usage_filter.py. All filters, no pipe, no tool. Live status (is_active) only from README: chat_condense + context_budget globally active.
- Open WebUI user settings (same-origin iframe, artifacts) live in the DB -> not readable (/srv blocked) -> gap.
- Connection: Open WebUI -> hermes-models (ki-hermes-models:1.2.0-1, ki-assistant compose l.237) -> Hermes Run API (/v1/runs). hermes-models: aiohttp router, converts hermes.tool.progress/hermes.status into reasoning_content (README ki l.64-65), STRIP_HERMES_USAGE. No MEDIA handling in app.py (grep without hits).
- Hermes: ki-hermes:v0.23.0-3 (FROM nousresearch/hermes-agent:v0.23.0). Upstream latest release: v0.23.0 -> current.
- Hermes skills (appdata/hermes/skills = /opt/data/skills): creative/: architecture-diagram, ascii-video, baoyu-infographic, claude-design, design-md, humanizer, manim-video, p5js, popular-web-designs, songwriting. NO concept-diagrams, mermaid-diagrams, live-dashboard.
- architecture-diagram SKILL.md l.55-57: Open WebUI shows only PNG via MEDIA:, not HTML. ki-assistant README l.390 says the same.
- /opt/data/cache/images: 0 files. /opt/data/plugins: not present/empty.
- Squid hermes (ki-assistant/config/squid-hermes.conf): ntfy.home.lan, api.github.com, export.arxiv.org, pypi.org, hub.docker.com, lesezeichen.home.lan. files.pythonhosted.org not allowed -> no wheels.
- config.yaml: tool servers read-only via mcp-relay, terminal local uid 1000, skills guard_agent_created.

## Sources (proven)
- #94177 closed 2026-08-24 (needs-repro, area/streaming). Comment liuhao1024: MEDIA->base64 only in non-streaming paths (_compute_completion, _compute_response, session chat). Streaming writers _write_sse_chat_completion/_write_sse_responses pass MEDIA: through raw. Workaround: stream:false. Fix status open -> to be checked.
- #84652 open (P3): tools: [] is ignored. As of 2026-08-12.
- #57431 open (updated 2026-10-20): per-request tools feature request. Not implemented -> core finding stands.
- MartianInGreen/openwebui-hermes: MIT, 1 star, 1 fork, 0 issues.
- nicolasramos/hermes-openwebui-pipeline: MIT, 0 stars, last push 2026-08-08.
- Open WebUI docs Rich UI: https://docs.openwebui.com/features/extensibility/plugin/development/rich-ui/ ; Events: .../events/ (embeds: list of strings -> iframe src/srcdoc).
- Search for new sources: uhub/awesome-agent-skills, 0xNyk/awesome-hermes-agent, nesquena/hermes-webui (active, roadmap). No new Hermes->OWUI embed pipe found. Brave rate-limited.
