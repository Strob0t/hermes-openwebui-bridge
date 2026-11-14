# Hermes OpenWebUI Bridge

Bridge between the **Hermes Agent** (as API backend and tool runner) and **Open WebUI** (as chat interface).

## Purpose & Background

Hermes executes tools independently on the server side. By default, Open WebUI expects tools in the user interface to run directly via WebUI tools or integrated pipes/actions. Since Hermes tools and open visualizations (such as `MEDIA:` file paths or interactive dashboards) do not arrive in the Open WebUI stream without adjustment, this project connects both worlds without losing the advantages of the router (`hermes-models`) and existing filters (such as `hermes_reasoning_filter.py`).

Detailed analyses and decisions can be found in the [`docs/`](docs/README.md) folder.

## Components & Status

| Component | Type | Status | Description |
|---|---|---|---|
| `skills/openwebui-visuals` | Hermes Skill | **in progress** | Format instructions for Hermes (Mermaid, SVG, HTML-Artifacts, `MEDIA:`-fallback) |
| `functions/hermes_embeds_filter.py` | Open WebUI Filter | **0.2.0, installed in stack `ki`** | Converts ```viz-html blocks to native Open-WebUI `embeds` (iframe with Chart.js / dashboards) |
| `functions/hermes_runs_pipe.py` | Open WebUI Pipe (Manifold) | **0.5.1, model discovery, images (`IMAGES_ENABLED`, `IMAGE_MAX_COUNT`, `IMAGE_MAX_BYTES`), thinking switch, `model_options` pass-through (`MODEL_OPTIONS_KEYS`), multi-model setup, tool durations from event timestamps** | Integration of `/v1/runs`, approvals, and session headers (ADR [0002](docs/decisions/0002-runs-pipe.md), specification [`docs/spec/hermes-runs-pipe.md`](docs/spec/hermes-runs-pipe.md)) |
| `router/` / Router integration | AIOHTTP Relay | **monitored** | Buffering of SSE frames, reasoning conversion, planned MEDIA inlining |

## Installation & Notes

- **Hermes Skill:** Copy to `$HERMES_HOME/skills/creative/openwebui-visuals/` (see [`skills/openwebui-visuals/README.md`](skills/openwebui-visuals/SKILL.md)).
- **Open WebUI Functions:** Import via Open WebUI admin area (see [`functions/README.md`](functions/README.md), with the section "Multi-Model Setup" for the pipe).

## Tests & Work Directory

- Unit tests (no network, no extra packages): `python3 -I -m unittest discover -s tests`.
- Work directory: `.devdata/` in the clone (git-ignored). It holds local reference copies (for example Open WebUI sources in `.devdata/owui/`) and the fake-server smoke `.devdata/smoke/runs_pipe_smoke.py`. Nothing in `.devdata/` goes into a commit.
- Smoke: `python -I .devdata/smoke/runs_pipe_smoke.py functions/hermes_runs_pipe.py` with `aiohttp` installed. It starts a fake Hermes on `127.0.0.1:0` and checks the `POST /v1/runs` body (`model`, `model_options`). Exit 0 = all checks pass.

## License

This project is licensed under the **GNU AGPL v3.0** (see [LICENSE](LICENSE)).

## Third Party & Patterns

See [`THIRD_PARTY.md`](THIRD_PARTY.md) for used patterns and sources of inspiration (MIT licenses).
