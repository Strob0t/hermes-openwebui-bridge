# Changelog

All noteworthy changes to this project are documented in this file.

## [Unreleased]

### Fixed
- Runs-Pipe 0.5.1: tool lines show the real duration. Hermes v0.23.1 sends `tool.completed` on `/v1/runs` with `duration` 0 (`gateway/platforms/api_server_runs.py`, `_tool_complete` gives no duration), so every line was `<tool> done (0 s)`. The pipe stores the start of each `tool.started` by `tool_call_id` (event `timestamp`, and the receipt time) and on `tool.completed` uses: the event `duration` if it is > 0, else completed `timestamp` minus started `timestamp`, else the difference of the receipt times. Without a start the line has no duration (new `TEXTS` key `tool_done_no_duration`: `{tool} done{error}`). At most `TOOL_STARTS_LIMIT` (64) open starts per run. Format: below 10 s with 1 decimal (`3.0 s`), below 60 s whole seconds (`42 s`), else `2 min 5 s` (new `TEXTS` keys `duration_decimal`, `duration_seconds`, `duration_minutes`). Changed: the placeholder `{duration}` of `tool_done` now holds the unit (`{tool} done ({duration}{error})`); a `TEXTS` override of `tool_done` must drop its own ` s`. New tests `test_tool_duration_from_timestamps`, `test_tool_duration_event_wins`, `test_tool_duration_receipt_time_fallback`, `test_tool_duration_missing_start`, `test_tool_duration_formats`, `test_tool_duration_parallel_calls`, `test_tool_starts_bounded`.

### Added
- Runs-Pipe 0.5.0: images reach Hermes. The run input comes from the last user message: text only stays the same string as in 0.4.0; with images it is `[{"role": "user", "content": [{"type": "text", ...}, {"type": "image_url", "image_url": {"url": ...}}]}]`. Sources: `image_url` and `input_image` parts of the message, and image files in `__files__` or `body["files"]` (data URL as is; an Open WebUI file id or `/api/v1/files/<id>/content` URL through the Open WebUI file helper with its access check). New valves `IMAGES_ENABLED` (default `True`), `IMAGE_MAX_COUNT` (default `4`) and `IMAGE_MAX_BYTES` (default 8 MB, decoded size of a data URL); dropped images give a short note (new `TEXTS` keys `images_disabled`, `images_too_many`, `images_too_big`, `images_unreadable`). A message with only images starts a run. Thinking switch: `reasoning_effort` `none` or `off` (any case, top level or `model_options`) goes as `model_options.reasoning_effort = "none"`, which turns thinking off in Hermes. Docs: section "Images" and "Model Options" in `functions/README.md`, spec 10.8. New tests `test_image_urls`, `test_image_url_size`, `test_run_input`, `test_thinking_switch`, `test_run_body_text_only`, `test_run_body_text_and_data_image`, `test_images_too_many`, `test_images_too_big_disabled_and_unreadable`, `test_image_files`, `test_run_body_thinking_switch`.
- Runs-Pipe 0.4.0: run body carries `model_options` from the request (new valve `MODEL_OPTIONS_KEYS`, comma allowlist, default `reasoning_effort`; values unchanged, other keys dropped). A top-level `reasoning_effort` (Open WebUI slider of connection models) fills `model_options.reasoning_effort` if `model_options` has none. Without an allowed option the body has no `model_options`. Fixes: the filter `hermes_reasoning` had no effect on pipe models. Docs: sections "Model Options" and "Multi-Model Setup" in `functions/README.md`, spec 10.7, work directory in `README.md`. New tests `test_run_model_options`, `test_run_body_model_options`, `test_owned_by_comma_list`, `test_short_model_id_dots_and_slashes`.

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
