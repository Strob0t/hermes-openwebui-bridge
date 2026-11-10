# Hermes + Open WebUI: Visualization – Source Collection and Agent Prompt

As of 2026-11-09. Source: web research (SearXNG), no tests against the live instance.
Status of statements: "proven" = read in doc/issue/code. "assumption" = deduction by me, not tested.

## 1. Core Finding

- Open WebUI calls Hermes via POST /v1/chat/completions (proven, Hermes docs).
- Hermes builds a separate agent per request and executes its tools itself. Only progress lines and final text are returned (proven).
- Open WebUI tools (Inline Visualizer, OpenUI, MCP App Bridge) are not called by Hermes. Hermes issue #57431 (open) requests per-request tools. Issue #84652: the API server even ignores `tools: []` (proven).
- Rich UI embeds in Open WebUI need an HTMLResponse from an Open WebUI tool or an "embeds" event via `__event_emitter__` from a pipe (proven, Open WebUI docs).
- Consequence: Embeds for Hermes only work via a pipe/filter in Open WebUI that transforms Hermes text (assumption, not built yet).

## 2. Options (by Effort)

| No | Option | Effort | Benefit | Status |
|----|--------|--------|---------|--------|
| 1 | Mermaid / SVG / HTML code blocks in response text, Open WebUI renders (Mermaid inline, HTML/SVG/JS in artifacts panel). Hermes skill sets format rules. | very low | medium | proven for Open WebUI, not tested live |
| 2 | Pipe/filter in Open WebUI: Markers in Hermes text (e.g. block `viz-html`) -> `__event_emitter__` type "embeds" -> inline iframe. Template: Inline Visualizer v2 (@@@VIZ-START/@@@VIZ-END). | medium (custom build) | high | assumption |
| 3 | hermes-rich-ui plugin (rich_present, 26 components, directive ::richui). Renders only in Hermes Desktop. Bridge via `$HERMES_HOME/rich-ui/cards/` possible. | medium-high | medium | bridge = construction |
| 4 | Images: PNG via MEDIA tag (issue #94177: Open WebUI shows raw text, issue closed, fix unverified). Workaround nicolasramos: static server for ~/.hermes/cache/images + markdown image. | low | medium | proven |
| 5 | Second model/profile in Open WebUI with active Inline Visualizer as "actor" for Hermes results. | low | low-medium | assumption; context only via chat history |
| 6 | Responses mode ("Responses (Experimental)"): structured function_call items, reasoning_content as thought block. Tool display only. | low | low | proven |
| 7 | Alternative frontends: hermes-webui (nesquena, artifacts/Mermaid/SVG/HTML built-in), hermes-web-ui, Hermes-Studio, hermes-ui. | migration | high | as comparison candidate |

## 3. Sources

Connection Hermes <-> Open WebUI
- MartianInGreen/openwebui-hermes (MIT, May 2026): Gateway plugin (channel polling every 3 s) + pipe function via /v1/runs with SSE, slash commands, approval dialogs. README mentions "HTML tool rendering issues". Pipe delivers markdown status lines. Embeds in code not seen (code only half read). Best basis for a custom pipe because function = access to `__event_emitter__`.
- nicolasramos/hermes-openwebui-pipeline (MIT, Aug 2026): runs in pipelines server. hermes.tool.progress -> reasoning_content; images from ~/.hermes/cache/images as markdown. No HTML/embed.
- Hermes docs: Open WebUI integration, API server, responses mode.

Hermes Issues
- #57431 Per-request tools for API clients (open)
- #84652 API server ignores `tools: []`
- #94177 MEDIA tag in Open WebUI as raw text (closed)

Open WebUI
- Docs Rich UI Embedding (HTMLResponse, Content-Disposition inline, postMessage height, window.args, tuple with context, "embeds" event for pipes).
- Artifacts (HTML/SVG/JS, versions, fullscreen), Mermaid rendering.
- Env variable IFRAME_CSP (CSP of artifact iframe); user setting "iframe Sandbox Allow Same Origin" (default off).
- Community plugins: Inline Visualizer v2 (streaming, sendPrompt(), Chart.js, skill), OpenUI plugin (render_openui), MCP App Bridge.

Hermes Skills (Building Blocks)
- concept-diagrams (official, optional, creative): flat SVG diagrams with CSS classes. Seen as snippet only.
- live-dashboard (official, optional): self-updating HTML dashboards via cron; output = file. SKILL.md read.
- mermaid-diagrams (awknode/hermes-agent-skills, MIT; copy at hosseinmahdevar): Mermaid syntax, focus PDF/CLI. SKILL.md read.
- excalidraw, archify, system-atlas, tldraw-offline: seen in list only, not opened.
- Catalogs, not searched: awesome-hermes-agent (Anil-matcha, 0xNyk, mergisi), ZeroPointRepo/awesome-hermes-skills, hermesatlas.com/lists/top-skills, hermes-agent-lab.com, codesstar/hermes-skill-atlas (skills hub delivered JSON error), dazfx/hermes-skills (installation only).

Hermes Plugins/Dashboards
- hermes-rich-ui (rich_present, ::richui directive, desktop only).
- hermes-web-ui (7.9k stars), mojomast/hermesdashboard, perejaslav/hermes-agent-dashboard (evaluation of Hermes itself).

Research Gaps
- No ready skill/pipe code Hermes -> Open WebUI embeds found.
- Reddit blocked; Brave/DDG offline at times; skillsmp and parts of GitHub not readable.
- Nothing tested against the live instance.

## 4. Agent Prompt (for Copying)

```
ROLE
You are an analysis agent for my AI stack (Hermes + Open WebUI + associated services). You compare the current state with the source collection below and derive concrete, prioritized improvements. You change nothing on the system.

GOAL
Better visualization (charts, graphs, dashboards, images) in Open WebUI with Hermes as backend. Secondary goal: briefly name other gaps in the stack that you notice during the comparison.

RULES
- Read-only. No write actions, no restarts, no deployments, no config changes.
- Always state claims with status: proven (file/command output/source read), assumption, unverified.
- No invented versions, paths, env variables, or findings. If info is missing: report as gap.
- Do not output credentials.
- Answer in English, direct, without cost notes and without clarifying questions that you can resolve yourself.

STEP 1 – GATHER CURRENT STATE (live, not from memory)
Check and note, each with proof:
a) Open WebUI: version, deployment (compose/Komodo stack in komodo/resources.toml, repo Strob0t/homelab), env variables for rendering (IFRAME_CSP, artifacts, same-origin setting), installed functions/pipes/filters/tools, connected models and API type (chat completions or responses).
b) Hermes: version, API server configuration, profiles, installed skills (visualization skills: concept-diagrams, live-dashboard, mermaid-diagrams, etc.), plugins, handling of MEDIA tags and image cache.
c) Connection: How does Open WebUI call Hermes? Is there already a pipe/pipeline? What happens in a test with Mermaid block, SVG block, HTML block, and PNG? (Read-only: check logs and configuration; live test only if it changes no state.)
d) Network: Which hosts is the agent allowed to reach (Squid allowlist)? What blocks downloads (e.g. files.pythonhosted.org)?

STEP 2 – CHECK SOURCES
Take the source list from section 3 of the collection. For each option 1–7:
- Check the source again for currency (new commits, releases, issue status, e.g. #94177, #57431, #84652). Does the core finding change, e.g. if Hermes now supports per-request tools?
- Additionally search for new sources since 2026-11-09 (Hermes skills for charts/Plotly/HTML, Open WebUI pipes for Hermes with embeds, awesome lists).
- Check license, maintenance state, security risk (scripts in iframes, CSP, same-origin).

STEP 3 – GAP ANALYSIS
Table: Option | Current State | Gap | Effort (low/medium/high) | Benefit | Risk | Proof.

STEP 4 – CRYSTALLIZE IMPROVEMENTS
Deliver a maximum of 7 measures, sorted by benefit per effort. For each:
- What exactly to change (file/service/setting, as far as proven).
- Expected result in Open WebUI (e.g. "Mermaid inline", "Chart as embed").
- Prerequisites and dependencies.
- How to test it (acceptance test, concrete).
- Rollback.
- Which measure can be implemented without a decision from me and what decision I need from you (real decisions only).
Additionally provide a draft for a Hermes skill "Format Rules for Open WebUI" (when Mermaid, SVG, HTML/Chart.js, PNG) and a sketch of the pipe from option 2 (marker, event, error case), each as a draft, not applied.

STEP 5 – FINAL REPORT
- 5 lines summary.
- The list of measures from step 4.
- Open gaps and what could not be tested.
- Recommendation: keep Open WebUI or test hermes-webui as comparison – with justification from steps 1–3.
```

## 5. Notes on Using the Prompt
- The agent should run on the workbench (repo and hosts are reachable there), not in the Hermes terminal (/opt/data only).
- Store results in `/opt/data/home/work/research-additions/` and enter them in PROGRESS.md.
