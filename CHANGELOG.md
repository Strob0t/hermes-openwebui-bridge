# Changelog

All noteworthy changes to this project are documented in this file.

## [Unreleased]

### Changed
- Runs-Pipe 0.3.0: all user-facing texts are English and live in one `TEXTS` table at the top of the module; the valve `TEXTS` (JSON object) overrides single keys. Protocol constants (headers, choices, statuses, limits) are module constants; the module is split into sections (constants, pure helpers, request context, `Pipe`). New test `test_texts_valve_override`.
- Embeds filter 0.2.0: valve defaults, HTML wrapper parts and skip reasons are module constants; default `replacement_text` is English (`*(chart embedded above)*`).
- Documentation translated to English; research files renamed to English names.

### Added
- Runs-Pipe 0.2.0: Model discovery from `GET /models` (`MODEL_DISCOVERY`, `MODEL_FILTER`, `OWNED_BY_FILTER`, `MODELS_CACHE_TTL`), approval rules via regex (`MODEL_POLICIES`), selected ID as `model` in run body, status line from `ROUTE_HEADER`. Replaces the static valve `MODELS`.
- Runs-Pipe 0.1.0 (manifold, approvals, session header) with tests (`functions/hermes_runs_pipe.py`, `tests/test_hermes_runs_pipe.py`).
- ADR 0002 and specification of the Runs-Pipe (`/v1/runs`, approvals, session mapping).
- Project structure, documentation, and research documents (`docs/research/`, `docs/decisions/0001-filter-not-pipe.md`).
- Hermes skill for visualization format rules (`skills/openwebui-visuals/SKILL.md`).
- Open WebUI filter `functions/hermes_embeds_filter.py` (v0.1.0) for viz-html embeds and full unit tests (`tests/test_hermes_embeds_filter.py`).
- Documentation for functions and valves (`functions/README.md`).
- Basic documents (`README.md`, `THIRD_PARTY.md`, `.gitignore`, `.editorconfig`).
