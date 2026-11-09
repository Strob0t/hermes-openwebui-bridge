# Notizen Analyse Hermes + Open WebUI Visualisierung (2026-11-09)

## IST (belegt)
- Open WebUI: ghcr.io/open-webui/open-webui:v0.12.4 (compose.yaml ki Z.453, komodo inspect running). Upstream latest: v0.13.3 (GitHub releases/latest).
- Open WebUI env (compose ki Z.470-509): WEBUI_URL https://chat.home.lan, OPENAI_API_BASE_URL http://hermes-gw.hermes:8642/v1 (Seed, live in DB), DEFAULT_MODELS hermes-agent, OFFLINE_MODE true, CORS_ALLOW_ORIGIN, Proxy squid-assistant. KEIN IFRAME_CSP, KEIN ENABLE_ARTIFACTS o.ae. in compose.
- Functions im Repo (appdata/open-webui/functions): hermes_reasoning_filter.py, chat_condense_filter.py (1.1.1), context_budget_filter.py (1.1.0), claude_usage_filter.py. Alles Filter, keine Pipe, kein Tool. Live-Status (is_active) nur aus README: chat_condense + context_budget global aktiv.
- Open WebUI Nutzer-Settings (Same-Origin iframe, Artifacts) liegen in DB -> nicht lesbar (/srv gesperrt) -> Luecke.
- Verbindung: Open WebUI -> hermes-models (ki-hermes-models:1.2.0-1, ki-assistant compose Z.237) -> Hermes Run API (/v1/runs). hermes-models: aiohttp router, wandelt hermes.tool.progress/hermes.status in reasoning_content um (README ki Z.64-65), STRIP_HERMES_USAGE. Kein MEDIA-Handling in app.py (grep ohne Treffer).
- Hermes: ki-hermes:v0.23.0-3 (FROM nousresearch/hermes-agent:v0.23.0). Upstream latest Release: v0.23.0 -> aktuell.
- Hermes Skills (appdata/hermes/skills = /opt/data/skills): creative/: architecture-diagram, ascii-video, baoyu-infographic, claude-design, design-md, humanizer, manim-video, p5js, popular-web-designs, songwriting. KEIN concept-diagrams, mermaid-diagrams, live-dashboard.
- architecture-diagram SKILL.md Z.55-57: Open WebUI zeigt nur PNG via MEDIA:, HTML nicht. ki-assistant README Z.390 gleich.
- /opt/data/cache/images: 0 Dateien. /opt/data/plugins: nicht vorhanden/leer.
- Squid hermes (ki-assistant/config/squid-hermes.conf): ntfy.home.lan, api.github.com, export.arxiv.org, pypi.org, hub.docker.com, lesezeichen.home.lan. files.pythonhosted.org nicht erlaubt -> keine Wheels.
- config.yaml: tool servers read-only via mcp-relay, terminal lokal uid 1000, skills guard_agent_created.

## Quellen (belegt)
- #94177 closed 2026-08-24 (needs-repro, area/streaming). Kommentar liuhao1024: MEDIA->base64 nur in nicht-streamenden Pfaden (_compute_completion, _compute_response, session chat). Streaming-Writer _write_sse_chat_completion/_write_sse_responses lassen MEDIA: roh durch. Workaround: stream:false. Fix-Status offen -> zu pruefen.
- #84652 open (P3): tools: [] wird ignoriert. Stand 2026-08-12.
- #57431 open (updated 2026-10-20): Per-Request-Tools Feature Request. Nicht umgesetzt -> Kernbefund bleibt.
- MartianInGreen/openwebui-hermes: MIT, 1 Stern, 1 Fork, 0 Issues.
- nicolasramos/hermes-openwebui-pipeline: MIT, 0 Sterne, letzter Push 2026-08-08.
- Open WebUI Doku Rich UI: https://docs.openwebui.com/features/extensibility/plugin/development/rich-ui/ ; Events: .../events/ (embeds: Liste von Strings -> iframe src/srcdoc).
- Suche nach neuen Quellen: uhub/awesome-agent-skills, 0xNyk/awesome-hermes-agent, nesquena/hermes-webui (aktiv, Roadmap). Keine neue Hermes->OWUI Embed-Pipe gefunden. Brave rate-limited.
