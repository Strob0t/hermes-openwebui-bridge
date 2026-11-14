# Specification: `functions/hermes_runs_pipe.py`

As of 2026-11-09. Source for the Hermes facts: source code v0.23.0, `gateway/platforms/api_server_runs.py` (R) and `gateway/platforms/api_server.py` (A). Line references refer to tag v0.23.0. Points marked "unverified" need part 3b to be checked against the live API before the code.

## 1. Purpose

Open WebUI pipe that runs a chat as a Hermes run via `/v1/runs`, streams tool progress, presents approvals as a dialog to the user, and holds one Hermes session per Open WebUI chat.

## 2. Valves

| Valve | Type | Default | Purpose |
|---|---|---|---|
| `HERMES_BASE_URL` | str | `http://hermes-gw.hermes:8642/v1` | Base URL of the Hermes API (in stack `ki`: direct or via `hermes-models`, part 3b decides) |
| `HERMES_API_KEY` | str | `""` | Bearer token; empty = no header |
| `MODEL_NAME` | str | `hermes-runs` | Display name in Open WebUI |
| `REQUEST_TIMEOUT` | int | `30` | Seconds for `POST /runs`, `/approval`, `/stop` |
| `STREAM_IDLE_TIMEOUT` | int | `900` | Seconds without an SSE frame, then abort with error message |
| `APPROVAL_MODE` | `ask\|auto\|deny` | `ask` | `ask` = dialog, `auto` = always `once`, `deny` = always `deny` |
| `APPROVAL_TIMEOUT` | int | `300` | Seconds until automatic `deny` |
| `SHOW_TOOL_PROGRESS` | bool | `true` | Emit tool events as `reasoning_content` block |
| `SESSION_TTL_HOURS` | int | `72` | Discard mapping entries older than TTL |
| `SESSION_FILE` | str | `""` | empty = memory only; otherwise JSON file under the Open WebUI data directory |

## 3. Session Mapping

- Key: Open WebUI `chat_id` from `__metadata__["chat_id"]`. Fallback if empty: `user_id` plus SHA-256 of the first user message (pattern from MartianInGreen, no code adoption).
- Value: `{"session_id": "owui-<chat_id>", "updated_at": <epoch>}`.
- First call: `session_id = "owui-" + chat_id` is chosen by the pipe itself and sent to Hermes. Hermes accepts a client `session_id` (R l.801: `body.get("session_id") or stored_session_id`). Unverified: whether Hermes creates a new session for an unknown `session_id` or returns 404.
- Thereafter: the same `session_id`. The history from Open WebUI is not sent again, only the last user message (`input`).
- Alternative if custom `session_id` does not work: `previous_response_id` = last `run_id` (R l.800).
- Persistence: in-memory `dict`; if `SESSION_FILE` is set, write atomically (`tmp` + `os.replace`). On load, discard entries older than `SESSION_TTL_HOURS`.
- Slash command `/reset` deletes the entry for the chat and answers "Session reset".

## 4. Flow of a Run

1. `POST {base}/runs` with `{"input": <text>, "session_id": <sid>, "model": <optional>, "stream": false}`. Response `202` with `{"run_id", "status"}` (R l.550). Idempotency: header `Idempotency-Key = chat_id + ":" + message_id` (R l.786, field name unverified: header or body).
2. `GET {base}/runs/{run_id}/events` as SSE. Frames: `id: <seq>` and `data: <json>` with fields `object`, `run_id`, `event`, `seq` (R l.1297-1300). On reconnect, header `Last-Event-ID = <seq>` (R l.1273).
3. Events and pipe reaction:

| Event (R) | Fields | Pipe |
|---|---|---|
| `run.queued`, `run.running` | `status` | Status event "Hermes is working" |
| `message.delta` | `delta` | stream as `content` chunk (R l.1072) |
| `message.interim` | `text`, `already_streamed` | only emit if `already_streamed` false (R l.1081) |
| `tool.started` | `tool`, `preview`, `tool_call_id` | `reasoning_content`: `> tool: preview` (R l.141) |
| `tool.completed` | `tool`, `duration`, `error`, `preview` | `reasoning_content`: duration and error flag (R l.142); duration 0 is measured from `tool.started` (10.9) |
| `reasoning.available` | `text` | `reasoning_content` (R l.145) |
| `subagent.start`, `subagent.complete` | `preview` | Status event |
| `approval.request` | `command`, `description`, `choices`, `request_id` | Section 5 (A l.90-102) |
| `approval.responded` | `choice`, `resolved` | Note line `(choice)` |
| `clarify.request` | prompt fields (R l.1115) | like approval, but `__event_call__` type `input`; answer to `POST /runs/{id}/clarify`. Fields unverified |
| `run.steered` | `accepted` | ignore |
| `replay.truncated` | `oldest_retained_seq` | Note "Parts of the progress are missing" |
| `run.completed` | `output`, `usage` | emit the rest of `output` not yet streamed; `done` (R l.1163) |
| `run.failed` | `error` | Error text; `done` |
| `run.cancelled`, `run.interrupted` | `error` optional | Note; `done` (R l.1087-1092) |

4. Open WebUI output: OpenAI-compatible SSE chunks (`choices[0].delta.content` or `delta.reasoning_content`), at the end `finish_reason: "stop"` and `data: [DONE]`. Additionally `__event_emitter__` type `status` for state changes.

## 5. Approvals

- `APPROVAL_MODE=ask`: `__event_call__({"type": "confirmation", "data": {"title": "Hermes needs an approval", "message": "<description>\n\n```\n<command>\n```"}})`. Result true = `once`, false = `deny`. Type `confirmation` is mentioned in the Open WebUI docs (Events, `__event_call__`); behavior in v0.12.4 unverified, live test in part 3b.
- No `__event_call__` available (for example API call without UI): the pipe writes the command into the stream and ends it with the note to answer with `/approve` or `/deny`. The next user message with this command goes to `POST /runs/{run_id}/approval`; afterwards the pipe reattaches to `/events` with `Last-Event-ID`.
- Answer to Hermes: `POST {base}/runs/{run_id}/approval` with `{"choice": "once"|"session"|"always"|"deny", "request_id": <from event>}` (R l.1372-1405). `409` = no open approval, `400` = invalid choice. The pipe sends only `once` or `deny`; `session` and `always` only if Steffen enables them later.
- `APPROVAL_TIMEOUT` expired: send `deny`, note in the stream.
- `APPROVAL_MODE=auto` for testing purposes only; default remains `ask`.

## 6. Abort and Errors

- Client aborts (generator is closed, `GeneratorExit`/`CancelledError`): `POST {base}/runs/{run_id}/stop` (R l.1523, response `{"status": "stopping"}`).
- Hermes not reachable (connection error, timeout on `POST /runs`): one line "Hermes is not reachable (<base>)", `done`, no retry.
- SSE aborts without terminal event: up to 3 reconnects with `Last-Event-ID`, then read `GET /runs/{run_id}` and take `output` from the status (R l.330 contains `output`).
- `STREAM_IDLE_TIMEOUT` reached: send `stop`, error message.
- Hermes `400` route conflict (R l.806-811): show error text, do not delete mapping.

## 7. Delimitation from the Router `hermes-models`

| Task | Router | Pipe |
|---|---|---|
| Chat Completions, model routing, `served-model` marker | yes | no |
| `hermes.tool.progress` to `reasoning_content` | yes (Chat Completions) | itself, from `tool.*` events |
| Stop on client abort | yes | yes, via `/stop` |
| Approvals | no | yes |
| Session per chat | no (header-based, unverified per report) | yes, mapping |
| Embeds | Filter `hermes_embeds` | same filter, if assigned to the model |

## 8. Tests (unittest, no network)

HTTP is mocked via an injectable client factory (default `aiohttp`, Open WebUI ships it).

1. `test_session_mapping_new_chat`: new `chat_id` yields `owui-<chat_id>`, entry stored.
2. `test_session_mapping_ttl`: entry older than TTL is discarded.
3. `test_session_file_roundtrip`: write and load the JSON file, atomic.
4. `test_message_delta_stream`: `message.delta` frames become `content` chunks in order.
5. `test_tool_progress_as_reasoning`: `tool.started`/`tool.completed` yield `reasoning_content`; with `SHOW_TOOL_PROGRESS=false` nothing.
6. `test_run_completed_remaining_output`: `output` is only supplemented by the part not yet streamed.
7. `test_approval_confirmation_once`: `approval.request` calls `__event_call__`; `True` sends `{"choice":"once","request_id":...}`.
8. `test_approval_timeout_deny`: no result until timeout sends `deny`.
9. `test_approval_mode_deny`: no dialog, directly `deny`.
10. `test_approval_fallback_without_event_call`: stream ends with note; `/approve` as next message sends the choice and reattaches.
11. `test_client_cancel_sends_stop`: closing the generator calls `POST /stop`.
12. `test_reconnect_with_last_event_id`: abort after `seq=4` sets header `Last-Event-ID: 4`.
13. `test_hermes_unreachable`: connection error yields one error line and `done`.
14. `test_reset_command`: `/reset` deletes the entry.

## 9. Open for Part 3b

- Check live: `__event_call__` type `confirmation` in Open WebUI v0.12.4.
- Check live: forwarding of `/v1/runs/*` through `owui-relay` and `hermes-models` (Section 10.1).
- Approvals live: only with a Hermes profile whose `approvals.mode` is not `off` (Section 10.3).

## 10. Addendum 2026-11-09 (Decisions Steffen, Code Facts)

The points below supplement or replace Sections 2, 3, and 5.

### 10.1 Path to Hermes (replaces the open question in Section 9)

- Decision: direct path without own code in the router. Proven (`homeserver/ki/build/hermes-models/app.py`): the router forwards every path except `/v1/chat/completions` and `/v1/responses` via `proxy()` (`other`, l.747-750). For `text/event-stream`, `Relay` only buffers until the frame end (`FRAME_END`), rewrites only frames with `event: hermes.tool.progress|hermes.status`, and returns all other frames unchanged (`Relay.frame`, l.270-271). Request headers except hop-by-hop pass through (`HOP`, l.57), including `Idempotency-Key` and `Last-Event-ID`. `owui-relay` is TCP HAProxy with a 15 min timeout.
- Default `HERMES_BASE_URL = http://hermes-gw.hermes:8642/v1` (alias is in `NO_PROXY` of Open WebUI). No network redesign. Hermes remains in the `internal` network only.
- `Idempotency-Key` is a request header (proven R l.781-785: 1-255 visible ASCII characters). Value: `owui:<chat_id>:<message_id>`.

### 10.2 Session (replaces Section 3)

- No custom `session_id` and no mapping file. The pipe sends the header `X-Hermes-Session-Key: owui:<user_id>:<chat_id>`, like the existing chat path (reference `owui-hermes-api.md`). Hermes resolves the key to a session itself (`_declared_conversation_session`, A l.1603; R l.843-847: `selected_session_id = session_id or declared(gateway_session_key)`) and loads the history from `state.db`. Body without `session_id`, `input` = only the last user message.
- `/reset`: the pipe appends a suffix `:r<n>` to the key (in-memory counter per `chat_id`). Hermes starts a new session. Valves `SESSION_TTL_HOURS` and `SESSION_FILE` are removed.
- Response header `X-Hermes-Session-Id` is only logged (`debug`).

### 10.3 Approvals (replaces Section 5, part "only once or deny")

Proven (A l.84-101): Hermes sends the field `choices` in the `approval.request` event, a subset of `once, session, always, deny`; `always` is missing when `allow_permanent=false`, `session` and `always` are missing when `smart_denied`. `POST /approval` accepts all four (R l.1377), aliases `approve|approved|allow` = `once` (R l.1353).

Valves (admin valves, no UserValves):

| Valve | Default | Purpose |
|---|---|---|
| `APPROVAL_MODE` | `ask` | `ask` dialog; `auto` without dialog with `AUTO_CHOICE`; `deny` always `deny` |
| `ALLOWED_APPROVAL_CHOICES` | `once,deny,session` | Offer = intersection of event `choices` and this list, in this order |
| `AUTO_CHOICE` | `session` | Choice for `APPROVAL_MODE=auto`; falls back to `once` if not in event `choices` |
| `APPROVAL_TIMEOUT` | `300` | afterwards `deny` |

Dialog: intersection has only `once,deny` → `__event_call__` type `confirmation` (true = `once`). Intersection has more → `__event_call__` type `input` with text "Answer: once, session, always or deny" (default value `once`); invalid input = `deny`. Without `__event_call__`: fallback via chat command `/approve [once|session|always]` or `/deny` (Section 5 remains). `always` is only reachable if the admin enters it in `ALLOWED_APPROVAL_CHOICES`; the README documents the risk (permanent approval, revocation only in Hermes).

Stack `ki` today: `config.yaml` has `approvals.mode: "off"` (proven). Hermes sends no `approval.request` there. The approval path is for profiles with `smart` or `manual` and is only tested with mocks.

### 10.4 Manifold and Roles

- The pipe is a manifold (`type = "manifold"`, async method `pipes()`). Models via discovery from `GET <HERMES_BASE_URL>/models` (OpenAI format; `name` as display name, otherwise `id`; `provider` is adopted). Valves: `MODEL_DISCOVERY` (default on), `MODEL_FILTER` (regex on the ID), `OWNED_BY_FILTER` (list), `MODELS_CACHE_TTL` (default 300 s). On load error: last list remains, otherwise the single built-in entry `hermes-default` (`Hermes (Gateway-Default)`), which sends no `model`. The selected ID goes as `model` into the run body. Approval rules per model via `MODEL_POLICIES` (JSON list, `match` = regex on the ID, first match wins; overrides `approval_mode`, `allowed_approval_choices`, `auto_choice`, `allowed_roles`). Visibility per group is controlled by Open WebUI (model permissions). The response header from `ROUTE_HEADER` (default `X-Hermes-Route`) of `POST /runs` appears as a status line.
- Valve `ALLOWED_ROLES` default `admin`. Role from `__user__["role"]`. Other role: one line "This model is not enabled for your account", no Hermes call. Reason: the router's family note (`family_note`) only affects chat paths, not `/v1/runs`.
- The pipe sends `X-Hermes-User-Role` and `X-Hermes-User-Name` from `__user__` (format as the router expects; today no effect on `/v1/runs`, for later router logic).

### 10.5 Tests (supplements Section 8)

Removed: 1-3 (mapping, TTL, file). New:

15. `test_session_key_header`: request carries `X-Hermes-Session-Key: owui:<user>:<chat>`, body without `session_id`.
16. `test_reset_suffix`: after `/reset` the key ends with `:r1`, then `:r2`.
17. `test_idempotency_header`: `Idempotency-Key` = `owui:<chat>:<message_id>`, only visible ASCII.
18. `test_choices_intersection`: event `["once","session","always","deny"]` with valve `once,deny,session` yields `["once","deny","session"]`; event `["once","deny"]` yields `["once","deny"]`.
19. `test_input_dialog_multi_choice`: intersection > 2 calls `__event_call__` type `input`; answer `session` sends `{"choice":"session"}`; answer `foo` sends `deny`.
20. `test_auto_mode_choice_fallback`: `auto` with `AUTO_CHOICE=session`, event without `session` sends `once`.
21. `test_role_denied`: `__user__["role"]="user"` with default `ALLOWED_ROLES` yields the note line, no HTTP call.
22. `test_manifold_policies_per_model`: `pipes()` returns the discovery entries; `MODEL_POLICIES` applies per model (first regex match).
23. `test_pipes_from_discovery_with_filters`: `GET /models` is read; `MODEL_FILTER` and `OWNED_BY_FILTER` filter.
24. `test_cache_ttl`: within `MODELS_CACHE_TTL` no second fetch, afterwards one.
25. `test_fetch_error_keeps_last_list_or_default`: network error or HTTP error keeps the last list; without a list, the entry `hermes-default`.
26. `test_discovery_off`: `MODEL_DISCOVERY=False` yields only `hermes-default`, no HTTP call.
27. `test_run_body_model_and_provider`: selected ID as `model` in the body, `provider` if present, no `model` for `hermes-default`; unknown ID passes through unchanged.
28. `test_policy_first_match`: first matching policy wins; invalid JSON yields no policies and a warning.
29. `test_route_header_status`: header from `ROUTE_HEADER` appears as status line `Route: ...` before "Hermes is working"; empty valve disables it.
30. `test_texts_valve_override`: the valve `TEXTS` overrides one key; unknown keys, non-string values and invalid JSON are dropped with a warning; a missing placeholder falls back to the default text.

### 10.6 Texts and constants (0.3.0)

- All user-facing texts are English and live in one table `TEXTS` at the top of the module. The valve `TEXTS` (JSON object) overrides single keys; unknown keys and non-string values are dropped, invalid JSON keeps the defaults and logs a warning. A format placeholder that the override does not provide falls back to the default text.
- Protocol constants (header names, choices, statuses, limits) are module constants. Open WebUI loads a function from one file, so the module stays one file with sections: constants and texts, pure helpers, request context, `Pipe`.

### 10.7 Model options and multi-model setup (0.4.0)

- Owner decision 2026-11-12: all Open WebUI picker models run through this pipe (`/v1/runs`). The pipe lists them with `MODEL_FILTER` (regex, `re.search`) and `OWNED_BY_FILTER` (comma list, case-insensitive).
- New valve `MODEL_OPTIONS_KEYS` (comma list, default `reasoning_effort`). The run body gets `model_options` with the keys of `body.model_options` in this list; values go through as given; other keys are dropped. Hermes reads `model_options.reasoning_effort` (`gateway/platforms/api_server_request_options.py`, `_request_reasoning_config`).
- A top-level `reasoning_effort` in the request body (the Open WebUI slider of connection models) fills `model_options.reasoning_effort` if `model_options` has none and the key is in the list.
- No allowed option: no `model_options` in the run body; Hermes `config.yaml` decides.
- Tests: `test_run_model_options`, `test_run_body_model_options` (pass-through, allowlist drop, slider mapping, no options), `test_owned_by_comma_list`, `test_short_model_id_dots_and_slashes`.

### 10.8 Images and thinking switch (0.5.0)

- Run input from the last user message. Text only: the same string as in 0.4.0. With images: `[{"role": "user", "content": [{"type": "text", "text": ...}, {"type": "image_url", "image_url": {"url": ...}}]}]`; no text part for an empty text. Hermes v0.23.1 `/v1/runs` takes `input[-1].content` (`gateway/platforms/api_server_runs.py`, `_run_user_message`).
- Image sources: `image_url` and `input_image` parts of the last user message (Open WebUI v0.12.4 makes them data URLs, `utils/middleware.py`), then image files in `__files__` and `body["files"]` (type `image` or raster `content_type`). URLs: `data:image/...`, `https://`; an Open WebUI file id or `/api/v1/files/<id>/content` goes through the Open WebUI file helper with its access check.
- Valves `IMAGES_ENABLED` (`True`), `IMAGE_MAX_COUNT` (`4`), `IMAGE_MAX_BYTES` (8 MB decoded). Dropped images give one note per reason from `TEXTS` before the run output. A message with only images starts a run.
- Thinking switch: `reasoning_effort` `none` or `off` (any case, top level or `model_options`, key in `MODEL_OPTIONS_KEYS`) goes as `model_options.reasoning_effort = "none"`. Hermes maps `none` to `reasoning_config = {"enabled": false}` (`gateway/platforms/api_server_request_options.py`, `_request_reasoning_config`).
- Tests: `test_image_urls`, `test_image_url_size`, `test_run_input`, `test_thinking_switch`, `test_run_body_text_only`, `test_run_body_text_and_data_image`, `test_images_too_many`, `test_images_too_big_disabled_and_unreadable`, `test_image_files`, `test_run_body_thinking_switch`.

### 10.9 Tool durations (0.5.1)

- Hermes v0.23.1 `/v1/runs`: `tool.completed` has `duration` 0, because `_tool_complete` of `observe_api_turn` calls `tool_cb` without `duration` (`gateway/platforms/api_server_runs.py`, `_FIXED_EVENT_FIELDS`). `_run_event` adds `timestamp` (`time.time()`) to every event; `tool.started` and `tool.completed` carry the same `tool_call_id`.
- The pipe stores (event `timestamp`, receipt time) per `tool_call_id` on `tool.started` and pops it on `tool.completed`. Duration: event `duration` if > 0, else completed `timestamp` minus started `timestamp`, else the receipt time difference. No start: `tool_done_no_duration` (`<tool> done`). At most `TOOL_STARTS_LIMIT` (64) starts per run; the oldest goes first.
- Format: below 10 s with 1 decimal, below 60 s whole seconds, else `<m> min <s> s` (`TEXTS` keys `duration_decimal`, `duration_seconds`, `duration_minutes`). `{duration}` in `tool_done` holds the unit.
- Tests: `test_tool_duration_from_timestamps`, `test_tool_duration_event_wins`, `test_tool_duration_receipt_time_fallback`, `test_tool_duration_missing_start`, `test_tool_duration_formats`, `test_tool_duration_parallel_calls`, `test_tool_starts_bounded`.
