# Report: Visualization in Open WebUI with Hermes as Backend

As of 2026-11-09. Analysis agent: Hermes (read-only). Source collection: `hermes-openwebui-visualisierung.md` (sections 1 to 3 read).
Status labels: **proven** = file, command output or source read in this run; **assumption** = conclusion from proven facts; **unverified** = not reachable or not read.

## 0. Summary (5 lines)

1. Hermes v0.23.0 (latest release, proven) runs all tools server-side; per-request tools (#57431) and `tools: []` (#84652) remain open. The core finding of the collection stands: Open WebUI tools and automatic Rich UI embedding do not apply to Hermes (proven).
2. Images: Hermes inlines `MEDIA:` tags only in non-streaming responses. Open WebUI streams, so the raw path arrives. Fix PR #103239 is open, last updated 2026-11-08 (proven). Immediate workaround: disable streaming for the model `hermes-agent` in Open WebUI (source: issue comments, not tested locally).
3. The own router `hermes-models` (Relay class, already buffers SSE frames and appends a marker line) is the natural place to convert `MEDIA:` tags to base64 in the stream itself, without waiting for upstream (assumption from code read).
4. Mermaid, SVG and HTML as code blocks are usable today without changes; no installed Hermes skill uses them, `architecture-diagram` even claims Open WebUI shows only PNG (proven). A format-rules skill is the cheapest measure.
5. Real inline embeds (Chart.js dashboards) need a small Open WebUI filter function that converts a marker block in the Hermes text into an `embeds` event (Open WebUI docs proven, implementation is a draft). `IFRAME_CSP` is not set (proven) and should arrive with the first HTML output.

## 1. Current State

### a) Open WebUI

| Item | Value | Status / Evidence |
|---|---|---|
| Version | `ghcr.io/open-webui/open-webui:v0.12.4` (release 2026-10-25) | proven: `homeserver/ki/compose.yaml` l.453, Komodo inspect `open-webui` running, GitHub release v0.12.4 |
| Upstream current | v0.13.3 | proven: GitHub `releases/latest` |
| Deployment | Stack `ki` (Komodo, `main`), networks `assistant`, `ki-ui-chat`, `OFFLINE_MODE=true`, egress only via `squid-assistant` | proven: compose l.452-509 |
| Rendering env | `IFRAME_CSP` not set; no artifacts/sandbox variables in compose | proven: grep in compose without hits |
| Same-origin iframe user setting, artifacts toggle | in the DB (`/srv/...` blocked for me) | unverified (gap) |
| Functions in the repo | 4 filters: `hermes_reasoning_filter.py` (1.0.0), `chat_condense_filter.py` (1.1.1), `context_budget_filter.py` (1.1.0), `claude_usage_filter.py` | proven: `appdata/open-webui/functions/`; live status (is_active) only per README |
| Pipes / tools / actions | none | proven: no pipe/tool code in the repo; live DB unverified |
| Connected models | Connection `http://hermes-gw.hermes:8642/v1` (hermes-models): `hermes-agent`, `auto`, `auto-medium`, `auto-strong`, `aux-cheap`, `aux-side`, released Bifrost models; second connection directly to Bifrost (`bifrost.*`, task model) | proven: `ki-assistant/README.md` l.429-449, `ki/README.md` l.57-77 |
| API type | Chat Completions (router also handles `/v1/responses`, Open WebUI uses Chat Completions per README) | proven for router code; live setting in DB unverified |

### b) Hermes

| Item | Value | Status / Evidence |
|---|---|---|
| Version | `ki-hermes:v0.23.0-3`, `FROM nousresearch/hermes-agent:v0.23.0` | proven: `ki-assistant/compose.yaml` l.307, `ki/build/hermes/Dockerfile` l.3 |
| Upstream current | v0.23.0 (2026-11-05) → up to date | proven: GitHub `releases/latest` |
| API server | Run API `:8642` only on `internal`, key `hermes_api_key`; chain Open WebUI → `owui-relay` → `hermes-models:8080` → `hermes:8642` | proven: `ki-assistant/README.md` l.27-43, 423-427 |
| Profiles | only `default` visible under `/opt/data` | proven: `/opt/data/profiles` not present |
| Skills `creative/` | architecture-diagram, ascii-video, baoyu-infographic, claude-design, design-md, humanizer, manim-video, p5js, popular-web-designs, songwriting-and-ai-music | proven: `ls /opt/data/skills/creative` = `ki-assistant/appdata/hermes/skills/creative` |
| Visualization skills from the collection | `concept-diagrams`, `live-dashboard`, `mermaid-diagrams`: **not installed** | proven (ls); whether `concept-diagrams` is upstream in v0.23.0: unverified (API limit) |
| Plugins | `/opt/data/plugins` not present | proven |
| MEDIA handling | `architecture-diagram/SKILL.md` l.55-57 and `references/png-export-without-libs.md` l.3: "Open WebUI shows only PNG via `MEDIA:`"; `ki-assistant/README.md` l.390 says the same. `hermes-models/app.py` contains no MEDIA handling | proven |
| Image cache | `/opt/data/cache/images`: 0 files | proven |
| Logs | `open-webui` and `hermes-models`: 0 hits for `MEDIA` in the last 5000 lines | proven (Komodo search_logs) |

### c) Connection and Behavior per Format

- Call: Open WebUI → `hermes-models` (aiohttp router, image `ki-hermes-models:1.2.0-1`) → Hermes `/v1/chat/completions` or `/v1/runs`. The router converts `hermes.tool.progress`/`hermes.status` into `reasoning_content`, appends the marker `<!-- served-model: ... -->` and stops runs on client abort (proven: `app.py` l.594-660, 704-745; `ki/README.md` l.62-66).
- No pipe, no pipeline. Only filters (proven).
- Mermaid block: Open WebUI renders Mermaid code blocks natively (proven: Open WebUI docs Artifacts/Chat; live test not done).
- SVG/HTML block: Artifacts panel (srcdoc iframe, sandboxed) (proven: docs; live test not done).
- PNG via `MEDIA:`: the raw path arrives in the stream. Cause per code analysis in issue #94177: `_resolve_media_to_data_urls()` runs only in `_compute_completion`, `_compute_response`, session chat; not in the SSE writers (proven: issue comment of 2026-08-24; PR #103239 "Fix api media streaming" open, updated 2026-11-08). Local code is at v0.23.0; whether something is already fixed there: unverified (source not readable due to API limit). The local skill docs (`architecture-diagram`) effectively say that PNG arrives inline → contradiction to the issue; **live test needed** (changes no state, but was not possible here: terminal cannot reach the Hermes API).

### d) Network

- Hermes egress (`ki-assistant/config/squid-hermes.conf`): `ntfy.home.lan`, `api.github.com`, `export.arxiv.org`, `pypi.org`, `hub.docker.com`, `lesezeichen.home.lan` (proven). `files.pythonhosted.org` missing → no `pip install` of wheels (proven by allowlist; memory confirms 403).
- Open WebUI: `OFFLINE_MODE=true`, proxy `squid-assistant`, `NO_PROXY` only internal services (proven). **Consequence for visualization:** HTML that loads Chart.js or D3 from a CDN runs in the user's browser, not in the container. Whether the browser (Steffen's network) reaches CDNs: assumption yes; if `IFRAME_CSP` is set with `connect-src 'none'`, CDN scripts still have to be allowed (`script-src`), otherwise Chart.js breaks.
- GitHub API anonymous: 60 requests/h; exhausted in this run (proven: `rate_limit` remaining 0).

## 2. Source Review

| Source | As of 2026-11-09 | Change to the core finding | License / maintenance / risk |
|---|---|---|---|
| Hermes #57431 per-request tools | open, updated 2026-10-20 | no | – |
| Hermes #84652 `tools: []` ignored | open, P3 | no | – |
| Hermes #94177 MEDIA raw in Open WebUI | closed 2026-08-24 (`needs-repro`), **not fixed**; comment proves cause (streaming writers); 2nd comment: workaround connection "external", Chat Completions, stream off → images inline | option 4 becomes more concrete | – |
| Hermes PR #103239 "fix(api-server): Fix api media streaming" | open, updated 2026-11-08; adds a streaming-capable media processor and providers `base64`/`none` | option 4 can be solved by an upstream update, timing open | – |
| Hermes v0.23.0 | current release (2026-11-05) | – | – |
| Open WebUI Rich UI docs (`/features/extensibility/plugin/development/rich-ui/`) | read; a pipe has to emit embeds itself via `__event_emitter__` | confirms option 2 | scripts in iframe: sandbox + `IFRAME_CSP` |
| Open WebUI events docs (`embeds`) | read; strings = URL or raw HTML; `replace: True` for live widgets | confirms option 2 | – |
| Open WebUI `IFRAME_CSP` | documented in `docs/reference/env-configuration.mdx`: CSP as `<meta>` in all srcdoc iframes (artifacts, HTML preview, MCP apps); example `default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; connect-src 'none'` | – | whether present in v0.12.4: unverified (env.py not readable, API limit) |
| Open WebUI v0.13.3 | latest release | update gap v0.12.4 → v0.13.3 | changelog unverified |
| MartianInGreen/openwebui-hermes | MIT, 1 star, 1 fork, 0 issues | – | low maintenance; code only partly read |
| nicolasramos/hermes-openwebui-pipeline | MIT, 0 stars, last push 2026-08-08 | – | inactive |
| Skills concept-diagrams / live-dashboard / mermaid-diagrams | content not readable again in this run (404/API limit) | – | unverified |
| New sources since 2026-11-09 | search (SearXNG, Brave partly blocked): no new Hermes→Open WebUI embed pipe; newly seen: `uhub/awesome-agent-skills`, `0xNyk/awesome-hermes-agent`, `Gabebac/Pipe-Plugin` (Hermes desktop plugin, not Open WebUI), `nesquena/hermes-webui` active with roadmap | no | not opened |

Security frame (proven from docs): artifacts and embeds run in `sandbox` iframes; `IFRAME_CSP` is an additional layer. "Allow Same Origin" is a user setting and opens the iframe to the Open WebUI origin → set only if an embed needs `sendPrompt()` or similar.

## 3. Gap Analysis

| Option | Current state | Gap | Effort | Benefit | Risk | Evidence |
|---|---|---|---|---|---|---|
| 1 Mermaid/SVG/HTML in text | Open WebUI can do it; no skill/SOUL rule uses it; `architecture-diagram` advises PNG | format rules missing; skill docs wrong | low | medium-high | low (sandbox) | compose, skill l.55-57, docs |
| 2 Filter/pipe → `embeds` | nothing present; `__event_emitter__` is available to filters in `outlet`/`stream` | own build needed | medium | high | medium (HTML from LLM in iframe; set CSP) | Rich UI docs, events docs |
| 3 hermes-rich-ui | no plugin installed; desktop only | bridge would be an own construction | high | medium | medium | `/opt/data/plugins` missing |
| 4 PNG via MEDIA | stream path gives raw text; `hermes-models` touches the stream anyway | inlining missing in the stream | low (stream off) / medium (router) | medium | low | #94177, PR #103239, app.py Relay |
| 5 Second "presenter" model with Inline Visualizer | Bifrost models directly available (`bifrost.*`) | tool plugin not installed; context only via chat | low | low-medium | medium (same-origin needed) | README ki l.72-77 |
| 6 Responses mode | router supports `/v1/responses`; OWUI uses Chat Completions | only presentation of tool calls | low | low | low | app.py l.56 |
| 7 hermes-webui | not installed | parallel operation, auth (Authelia), family chat rules missing there | high | high (artifacts built in) | high (second UI, permissions) | search results |

Side gaps noticed during the comparison:
- Open WebUI v0.12.4 is 1 minor behind v0.13.3 (proven). Changelog unverified.
- `architecture-diagram/SKILL.md` and `references/png-export-without-libs.md` contain the statement "Open WebUI shows only PNG"; this is wrong for HTML/SVG artifacts and exactly inverted for PNG in the stream (proven vs. issue). The skill docs actively steer Hermes into the worst variant.
- No `pip` access: skills that want to install matplotlib/plotly fail (proven allowlist). Check what is already in the image (unverified).

## 4. Measures (by benefit per effort)

### M1 Hermes skill "Format rules for Open WebUI" (draft below)
- Change: new skill under `/opt/data/skills/creative/openwebui-visuals/SKILL.md` (repo path `homeserver/ki-assistant/appdata/hermes/skills/creative/...`); correct `architecture-diagram/SKILL.md` l.55-57 and `references/png-export-without-libs.md` l.3.
- Result: Mermaid inline, SVG/HTML in the Artifacts panel, PNG only as fallback.
- Prerequisites: none. Depends on M3 for PNG.
- Acceptance test: chat "Draw the chat path Open WebUI → hermes-models → Hermes as a diagram": the answer contains a ```mermaid block, Open WebUI renders it (screenshot). Second test "Bar chart of the container count per host as HTML": the Artifacts panel opens.
- Rollback: remove the skill folder, skill docs via `git revert`.
- Decision: none; a skill change via `skill_manage` is my standard path. The path into the image/repo runs through the workbench.

### M2 Set `IFRAME_CSP`
- Change: `homeserver/ki/compose.yaml`, service `open-webui`, `IFRAME_CSP: "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; img-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; connect-src 'none'"`. Deploy via Komodo (recreate).
- Result: artifacts/embeds may load Chart.js from jsDelivr but cannot send data outside.
- Prerequisite: variable present in v0.12.4 (unverified; otherwise combine with M5). Browser reaches jsDelivr (assumption).
- Acceptance test: an artifact with `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>` renders a chart; an artifact with `fetch('https://example.com')` shows a CSP error in the browser console.
- Rollback: remove the line, deploy.
- Decision: none (security layer, no loss of function). Note: if Steffen wants it CDN-free, Chart.js has to live as a file under Open WebUI `static/` → that would be a decision.

### M3 PNG in the stream: inlining in the router `hermes-models`
- Change: `homeserver/ki/build/hermes-models/app.py`, class `Relay`: buffer content deltas, detect the pattern `MEDIA:/<path>.(png|jpg|jpeg|gif|webp)` (also across frame boundaries, analogous to the existing `held`), read the file from the shared volume and emit it as `![](data:image/png;base64,...)`. The non-stream path is already covered by Hermes. New image tag `ki-hermes-models:1.3.0-1`, tests in `test_app.py`.
- Prerequisite: `hermes-models` has to see `/opt/data/cache/images` (Hermes data volume) read-only — not mounted today (unverified, compose ki-assistant l.237 ff. not fully read) → add the volume read-only. First clarify with a **live test** whether v0.23.0 still has the problem at all: `curl` with `stream:true` against `hermes-models` and prompt "create a 10x10 PNG and show it" (workbench, read-only except the cache file).
- Alternative without code: in Open WebUI, model `hermes-agent`, Advanced Params "Stream Chat Response" off (source: #94177 comment). Side effect: thinking blocks (`reasoning_content` from tool progress) and the stop behavior are lost → not recommended as a permanent solution.
- Result: Hermes-generated PNGs (matplotlib, ffmpeg renderer, screenshots) appear inline.
- Acceptance test: chat "Create a PNG with a red square and show it": image visible; `docker logs hermes-models` contains a line `media inlined ... bytes`.
- Rollback: old image tag in compose, deploy.
- Decision: none, provided the live test confirms the error. If PR #103239 is merged before and Hermes is updated, M3 is dropped.

### M4 Open WebUI filter "hermes_embeds" (sketch below)
- Change: new function `homeserver/ki/appdata/open-webui/functions/hermes_embeds_filter.py`, globally active, installation like `hermes_reasoning` (admin API, README ki l.181-199).
- Result: a block ```viz-html ... ``` in the Hermes answer is shown as an interactive inline iframe in the chat (Chart.js, tables, maps); the code block itself is removed from the text.
- Prerequisites: M1 (skill teaches Hermes the block), M2 (CSP). Open WebUI v0.12.4 supports the `embeds` event (docs are current; version unverified → clarify in the test).
- Acceptance test: prompt "Dashboard of the 3 hosts as a chart" → iframe visible, no code block in the text, still visible after reload (persisted).
- Rollback: disable or delete the filter (Admin, Functions).
- Decision: none. Only HTML from Hermes (own agent) is embedded; foreign models (`bifrost.*`) excluded via `skip_prefixes`.

### M5 Raise Open WebUI to v0.13.x
- Change: `compose.yaml` l.453 image tag; read changelog 0.12.5 to 0.13.3 (unverified); run the filter tests (`tests/owui_context`, 112 tests) beforehand.
- Result: current artifacts/embeds state; possibly `IFRAME_CSP`.
- Prerequisite: DB backup (`webui.db`), quiet window.
- Acceptance test: login via Authelia, chat with `hermes-agent`, filters active (log lines `chat condense`/`context budget`), Mermaid renders.
- Rollback: old tag + restore DB backup.
- Decision: **yes, Steffen**: minor update with DB migration on the central UI, family chat affected. Proposal: after M1-M4, not before.

### M6 Check missing official skills (`concept-diagrams`, `live-dashboard`)
- Change: workbench checkout `NousResearch/hermes-agent` tag v0.23.0, check paths `skills/creative/concept-diagrams`, `skills/productivity/live-dashboard` (in the image under `/opt/hermes/skills`?); if present, add them as optional skills in `/opt/data/skills` and link them with M1 (SVG output → artifact).
- Result: ready-made diagram templates.
- Acceptance test: `skills_list` shows them; a test prompt yields an SVG artifact.
- Rollback: remove the folder.
- Decision: none. Status today: unverified (API limit).

### M7 hermes-webui as comparison (test instance only)
- Change: own stack draft, only behind Authelia, admin group only, check read-only access to Hermes data (the tool reads Hermes state directly, "coupled to Agent internals").
- Result: comparison of artifacts/Mermaid out of the box.
- Decision: **yes, Steffen** (new UI, permissions, maintenance of a second UI). Recommendation below: not now.

## 4a. Draft skill "Format rules for Open WebUI" (not applied)

```markdown
---
name: openwebui-visuals
description: Use when the reader is in Open WebUI and asks for a diagram, chart, table or image. Pick the output format.
---

# Visual output for Open WebUI

Open WebUI renders three things from the answer text. Use them in this order.

## Rules

1. Flow, sequence, state, Gantt, class, ER, pie: write one ```mermaid block. Keep the syntax simple. Do not add text in the block. Test the syntax in your head: a bad block shows as code.
2. Numbers over categories or time (bar, line, scatter), dashboards, tables with sort: write one ```viz-html block (self-contained HTML, inline CSS, Chart.js from https://cdn.jsdelivr.net/npm/chart.js, no external data calls, no forms that send data). Max 60 KB. The filter in Open WebUI shows the block as an inline card. Without the filter, Open WebUI shows it as an HTML artifact.
3. Static drawings, icons, architecture pictures: write one ```svg block or one ```html block. Open WebUI opens it in the Artifacts panel.
4. Raster images (screenshots, plots from a file, photos): give the file as MEDIA:/absolute/path.png on its own line. Use this only when the image exists as a file. Do not write MEDIA: for diagrams that you can write as Mermaid or SVG.
5. One visual per answer unless the user asks for more. Put one sentence before it: what it shows. Put the data source after it.
6. Family chat: same rules; no links to internal hosts in the HTML.

## Do not

- Do not render a diagram as ASCII when Mermaid fits.
- Do not install packages (pip is blocked). Use ffmpeg or the HTML path.
- Do not put secrets, tokens or internal URLs into HTML.
```

Additionally replace in `architecture-diagram/SKILL.md` l.55-57: "Open WebUI shows HTML and SVG in the Artifacts panel; PNG only via MEDIA: and only when the stream inliner is active (M3)."

## 4b. Sketch filter "hermes_embeds" (option 2, not applied)

- Type: Open WebUI **filter** (not pipe): the connection to `hermes-models` stays, streaming and the `hermes_reasoning` filter stay. Hook: `stream()` collects content deltas; `outlet(body, __event_emitter__, __model__)` works on the finished message.
- Marker: ```viz-html\n...\n``` (fence language `viz-html`). Advantage over `@@@VIZ-START`: without the filter it stays a normal code block that Open WebUI offers as an HTML artifact (graceful degradation).
- Flow `outlet`:
  1. Check the model ID against `skip_prefixes` (`bifrost.`); Hermes models only.
  2. Search the last assistant message for all `viz-html` blocks (regex over fences, max `max_embeds` = 3, max `max_bytes` = 60000 per block).
  3. Per block: put the HTML into a skeleton (`<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{margin:0;font-family:system-ui}</style></head><body>…<script>new ResizeObserver(()=>parent.postMessage({type:'iframe:resize',height:document.body.scrollHeight},'*')).observe(document.body)</script></body></html>`). No `allow-same-origin` needed.
  4. `await __event_emitter__({"type":"embeds","data":{"embeds":[html,...],"replace":True}})`.
  5. Replace the block in the message text with a line `*(chart embedded above)*` so the code is not shown twice. Valve `keep_code` = false.
- Error case: every exception → log warning, message unchanged (fail-open, like the other filters). Block too large → not embedded, code block stays (artifacts fallback). No `__event_emitter__` (task call) → do nothing. `metadata.task` set → do nothing.
- Streaming variant (`replace: True` for live dashboards) only in version 2.
- Tests: like `tests/owui_context`: fence parsing, size limit, skip_prefixes, idempotence on re-run of the outlet.
- Security: the iframe stays `sandbox` without same-origin; `IFRAME_CSP` (M2) restricts the network; HTML comes only from the own agent. Residual risk: prompt injection via read web pages could inject HTML → `connect-src 'none'` prevents data exfiltration, forms without a target.

## 5. Open Gaps

- Live test of all four formats against the instance (Mermaid, SVG, HTML, PNG in the stream) is missing. My terminal cannot reach the Hermes API; needs a workbench task (read-only, creates only one cache file).
- Open WebUI DB: active functions, API type of the connection, user settings (same-origin, artifacts) not read.
- `IFRAME_CSP` and `embeds` event in **v0.12.4** not checked against the source code (GitHub API limit exhausted).
- Upstream content of the skills `concept-diagrams`, `live-dashboard` at tag v0.23.0 not read.
- Whether `hermes-models` sees the image volume: compose `ki-assistant` l.237 ff. not fully read.
- Open WebUI changelog 0.12.5 to 0.13.3 not read.
- No credentials viewed or disclosed.

## 6. Recommendation

Keep Open WebUI. Reason: auth (Authelia, roles `admins`/`familie`), family chat marking in the router, four established filters, cron delivery into chats and the model router are bound to Open WebUI (proven). hermes-webui brings artifacts out of the box but is tightly coupled to Hermes internals and would need auth, family rules and maintenance of a second UI (search results, not opened). The visualization benefit is reachable with M1 to M4 in the existing stack; M7 only if M4 fails.

Order: M1 (now, skill) → live test of the 4 formats (workbench, read-only) → M2 + M4 (one deploy) → M3 only if the live test confirms the PNG problem and PR #103239 is not merged → M6 → M5 after decision.
Real decisions for Steffen: M5 (Open WebUI update) and M7 (second UI). Everything else is routine.
