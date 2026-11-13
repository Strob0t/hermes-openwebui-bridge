# Open WebUI Functions

This folder contains Open WebUI functions (filters and pipes) to support the Hermes backend.

## Installation (Filter)

1. Log in to Open WebUI as administrator (`Admin > Functions`).
2. Click **+ (Import)** or upload/paste the code from `hermes_embeds_filter.py` directly.
3. Alternatively, import via the Open WebUI Admin API.
4. Enable the filter globally and set priority.

## Valves (`hermes_embeds_filter.py`)

| Valve | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `True` | Enable or disable filter |
| `priority` | `int` | `5` | Execution priority of the filter |
| `skip_prefixes` | `str` | `"bifrost."` | Comma-separated model IDs to be skipped |
| `max_embeds` | `int` | `3` | Maximum number of embeds per message |
| `max_bytes` | `int` | `60000` | Maximum byte size per embed block |
| `keep_code` | `bool` | `False` | Keep code block in text alongside the embed |
| `fence_lang` | `str` | `"viz-html"` | Markdown fence language identifier for embed blocks |
| `replacement_text` | `str` | `"*(chart embedded above)*"` | Text as replacement for the embedded code block |
| `debug` | `bool` | `False` | Enable debug logging |

## Behavior without Filter

If the filter is disabled or no event emitter is present (e.g. task calls), ```viz-html blocks remain as normal Markdown code blocks. Open WebUI automatically offers these as interactive HTML artifacts (fail-open behavior).

## Security Notes

- Embeds and artifacts run in isolated `sandbox` iframes without `allow-same-origin`.
- The environment variable `IFRAME_CSP` should be configured (e.g. `connect-src 'none'`) to prevent data leakage from iframes.
- HTML content originates exclusively from your own Hermes agent.

## Pipe `hermes_runs_pipe.py`

Manifold pipe: models arrive via discovery from `GET <HERMES_BASE_URL>/models` (OpenAI format), each appears as a separate model in Open WebUI. A chat runs as a Hermes run via `/v1/runs`; the selected model ID goes as `model` into the run body. Specification: [`docs/spec/hermes-runs-pipe.md`](../docs/spec/hermes-runs-pipe.md) (Section 10 takes precedence over 2, 3, and 5), decision: [ADR 0002](../docs/decisions/0002-runs-pipe.md).

### Installation

1. `Admin > Functions`, **+**, paste code from `hermes_runs_pipe.py`, save, enable function.
2. Set valves (at least `HERMES_API_KEY`).
3. `Admin > Settings > Models`: set visibility of each pipe model per group (model permissions). The valve `ALLOWED_ROLES` additionally checks the role.
4. Optional: assign filter `hermes_embeds` to the pipe model.

### Valves (admin only, no UserValves)

| Valve | Type | Default | Description |
|---|---|---|---|
| `HERMES_BASE_URL` | `str` | `http://hermes-gw.hermes:8642/v1` | Base URL of the Hermes API (via `owui-relay` and `hermes-models`) |
| `HERMES_API_KEY` | `str` | `""` | Bearer token; empty = no `Authorization` header |
| `MODEL_DISCOVERY` | `bool` | `True` | Load models from `GET <HERMES_BASE_URL>/models`. `False`: exactly one entry `Hermes (Gateway-Default)` without `model` in run body |
| `MODEL_FILTER` | `str` (Regex) | `""` | Only model IDs matching the regex (`re.search`); empty = all |
| `OWNED_BY_FILTER` | `str` | `""` | Only models with these `owned_by` values, comma-separated; empty = all |
| `MODELS_CACHE_TTL` | `int` | `300` | Seconds cache for the model list. On load error: last list remains; without a list, entry `Hermes (Gateway-Default)` |
| `MODEL_POLICIES` | `str` (JSON) | `[]` | List of `{"match": <Regex on model ID>, ...}`; first match wins and may override `approval_mode`, `allowed_approval_choices`, `auto_choice`, `allowed_roles`. Invalid JSON: no policies and warning in log |
| `MODEL_OPTIONS_KEYS` | `str` | `reasoning_effort` | Comma-separated keys of `body.model_options` that go into the run body as `model_options`; values unchanged, other keys dropped. A top-level `reasoning_effort` fills `model_options.reasoning_effort` if `model_options` has none. `none` or `off` goes as `none` (thinking off). Empty = no `model_options` |
| `IMAGES_ENABLED` | `bool` | `True` | Send the images of the last user message to Hermes. `False`: drop them with a note; the text goes as a string |
| `IMAGE_MAX_COUNT` | `int` | `4` | Maximum images per message; more are dropped with a note |
| `IMAGE_MAX_BYTES` | `int` | `8388608` (8 MB) | Maximum decoded bytes per image (size of a data URL); a larger image is dropped with a note. An `https` URL has no size check |
| `ROUTE_HEADER` | `str` | `X-Hermes-Route` | Response header from `POST /runs` that appears as status line `Route: ...` (for routing proxies); empty = off |
| `REQUEST_TIMEOUT` | `int` | `30` | Seconds for `POST /runs`, `/approval`, `/clarify`, `/stop`, `GET /runs/{id}` |
| `STREAM_IDLE_TIMEOUT` | `int` | `900` | Seconds without event in stream (keepalives do not count), then `stop` and error line |
| `APPROVAL_MODE` | `str` | `ask` | `ask` dialog, `auto` without dialog with `AUTO_CHOICE`, `deny` always `deny` |
| `ALLOWED_APPROVAL_CHOICES` | `str` | `once,deny,session` | Offer = intersection of Hermes `choices` and this list, in this order |
| `AUTO_CHOICE` | `str` | `session` | Choice for `auto`; otherwise `once`, otherwise the first Hermes choice |
| `APPROVAL_TIMEOUT` | `int` | `300` | Seconds for a dialog; afterwards `deny` |
| `SHOW_TOOL_PROGRESS` | `bool` | `True` | Tool events as status and as reasoning block |
| `ALLOWED_ROLES` | `str` | `admin` | Open WebUI roles with access, comma-separated; `*` = all |
| `TEXTS` | `str` (JSON) | `""` | JSON object that overrides user-facing texts by key (keys and English defaults: table `TEXTS` at the top of the module, for example `{"working": "Hermes arbeitet", "denied": "Nicht freigegeben"}`). Unknown keys and non-string values are ignored; invalid JSON keeps the defaults and logs a warning |
| `DEBUG` | `bool` | `False` | Debug logging (also `X-Hermes-Session-Id`) |

### Model Discovery

- Display name from `name`, otherwise the `id`. A `provider` field in the upstream entry goes as `provider` into the run body.
- Open WebUI calls `pipes()` when loading the model list; the pipe caches the result for `MODELS_CACHE_TTL` seconds. `pipes()` is `async`: Open WebUI v0.12.4 (`backend/open_webui/functions.py`, `get_function_models`) checks `asyncio.iscoroutinefunction` and awaits the coroutine; an error there yields an empty list, which is why the pipe always returns at least one entry.
- After an Open WebUI restart, the pipe does not know the list until the next `pipes()` call. A model not in cache is sent unchanged as `model` to Hermes.
- Only built-in entry: `hermes-default` (`Hermes (Gateway-Default)`). It sends no `model`, Hermes then takes its default.

### `MODEL_POLICIES` Example

```json
[
  {"match": "^auto", "approval_mode": "auto", "auto_choice": "session"},
  {"match": ".*", "approval_mode": "ask", "allowed_approval_choices": "once,deny", "allowed_roles": "admin,user"}
]
```

- First rule: all models whose ID starts with `auto` run without approval dialog. Visibility of these models only for the owner group (model permissions in Open WebUI).
- Second rule: all other models; every approval as yes/no dialog, only `once` or `deny`.
- Without a match, global valves apply (`APPROVAL_MODE`, `ALLOWED_APPROVAL_CHOICES`, `AUTO_CHOICE`, `ALLOWED_ROLES`).

### Model Options (`MODEL_OPTIONS_KEYS`)

Hermes reads a per-request reasoning effort only as `model_options.reasoning_effort` in the run body. The pipe builds `model_options` from the Open WebUI request body:

1. Keys of `body.model_options` that are in `MODEL_OPTIONS_KEYS` go through with their value unchanged (the filter `hermes_reasoning` sets `body.model_options.reasoning_effort`). Other keys are dropped.
2. If `model_options` has no `reasoning_effort` and the request has a top-level `reasoning_effort` (the Open WebUI slider of connection models), the pipe uses that value. This applies only if `reasoning_effort` is in `MODEL_OPTIONS_KEYS`.
3. No allowed option: the run body has no `model_options`, and Hermes `config.yaml` decides.
4. Thinking switch: a `reasoning_effort` of `none` or `off` (any case, from step 1 or 2) goes as `none`. Hermes v0.23.1 turns thinking off for `none` (`gateway/platforms/api_server_request_options.py`, `_request_reasoning_config`: `{"enabled": false}`); it ignores `off`, so the pipe maps it. Other values go through unchanged.

Example: `MODEL_OPTIONS_KEYS=reasoning_effort,service_tier` also passes `model_options.service_tier`.

### Images

Open WebUI v0.12.4 puts the images of a user message into the message content as `image_url` parts and changes them to data URLs before the pipe gets the request (`utils/middleware.py`, `convert_url_images_to_base64`). The pipe sends them to Hermes:

1. Sources, in this order: the `image_url` and `input_image` parts of the last user message, then the image files in `__files__` and `body["files"]` (type `image` or a raster image `content_type`, not SVG). The same URL goes only once.
2. Accepted URLs: `data:image/...` and `https://`. An Open WebUI file id or `/api/v1/files/<id>/content` becomes a data URL through the Open WebUI file helper with its access check for the user. Other sources are dropped.
3. Limits: `IMAGES_ENABLED`, `IMAGE_MAX_COUNT`, `IMAGE_MAX_BYTES`. For each reason one note goes into the answer before the run output (`TEXTS` keys `images_disabled`, `images_too_many`, `images_too_big`, `images_unreadable`).
4. Run input with images: `[{"role": "user", "content": [{"type": "text", "text": <text>}, {"type": "image_url", "image_url": {"url": <url>}}, ...]}]`. Without text there is no text part. Without images the input stays the text string.

Prerequisite: Hermes `/v1/runs` must accept content parts in `input` (normalization like the chat route, `_normalize_multimodal_content`). Hermes limits the request body to 10 MB (`MAX_REQUEST_BYTES`); a data URL is about 4/3 of the image size, so keep the sum of the images below about 7 MB.

### Multi-Model Setup

All Open WebUI picker models run through this pipe (`/v1/runs`). One pipe instance lists the Hermes classes and the provider models; the filters select the picker entries:

```text
MODEL_FILTER=^(auto|claude-sub/claude-[a-z0-9-]+|premiumize/.+)$
OWNED_BY_FILTER=hermes,bifrost
MODEL_OPTIONS_KEYS=reasoning_effort
```

- `MODEL_FILTER` is a regex (`re.search`, so use `^...$` for a full match). `OWNED_BY_FILTER` is a comma list; case and spaces do not matter. A model must pass both filters.
- Open WebUI model id: `<function id>.<entry id>`. The pipe splits at the first `.` only, so entry ids with dots and slashes (for example `premiumize/gpt-6.1-sol`) stay whole and go unchanged as `model` to Hermes.
- `ALLOWED_ROLES` applies to the whole pipe; use `MODEL_POLICIES` (`allowed_roles`) and the Open WebUI model permissions for each model.

### Operation Behind a Routing Proxy

If there is a proxy in front of Hermes answering `/v1/models` with its own model IDs (e.g. classes like `auto`, `auto-medium`, `auto-strong` plus passed-through provider models), the pipe lists exactly these IDs. Typical settings:

- `OWNED_BY_FILTER=hermes`: show only proxy classes, not provider models that Open WebUI might already know directly.
- `ROUTE_HEADER`: If the proxy supplies a header with the selected route, the pipe shows it as a status line.
- If the proxy is removed, `/v1/models` from Hermes shows its models; the pipe needs no change.

### Behavior

- Request to Hermes: `POST /runs` with `{"input": <last user message>, "stream": false, "model": <selected ID>}` (input is the text, or a list with one user message for images, see "Images") (plus `provider` if discovery provides one; plus `model_options` if the request has allowed options, see `MODEL_OPTIONS_KEYS`; without `model` for entry `hermes-default`) and `Idempotency-Key: owui:<chat_id>:<message_id>`; then `GET /runs/{id}/events` (SSE).
- Header on all requests: `X-Hermes-Session-Key: owui:<user_id>:<chat_id>` (Hermes loads history itself), `X-Hermes-User-Role`, `X-Hermes-User-Name` (URL encoded as in Open WebUI), `Accept-Encoding: identity`, `Authorization` if key set.
- Text (`message.delta`, `message.interim`, rest of `run.completed.output`) goes as `content` into the stream.
- Tool progress: `__event_emitter__` type `status` and additionally `delta.reasoning_content` chunks (dict). Open WebUI v0.12.4 (`functions.py`, `process_line`) passes a dict as `data: <json>`, the middleware reads `delta.reasoning_content` as a reasoning block. Without `stream`, Open WebUI combines all chunks with `str()`; then the pipe sends no dict chunks, only status events.
- Approval (`approval.request`): Offer only `once,deny` → dialog `confirmation` (Yes = `once`). More choices → dialog `input` ("Answer: once, deny or session", default `once`); invalid input = `deny`. Timeout or dialog error = `deny`.
- Without `__event_call__` (e.g. API call): The pipe shows the command, ends, and remembers the run per chat. The next message `/approve [once|session|always]` or `/deny` sends the choice and reattaches with `Last-Event-ID`.
- Clarification (`clarify.request`): Dialog `input`, answer to `POST /runs/{id}/clarify`.
- Chat command `/reset`: new key with suffix `:r<n>`, i.e. new Hermes session. The counter is in memory only; after an Open WebUI restart, the key without suffix applies again.
- Client abort: `POST /runs/{id}/stop`. Abort of stream without end: up to 3 reconnects, then `GET /runs/{id}`.
- Open WebUI tasks (title, tags, follow-ups) do not go to Hermes; the pipe returns empty text.

### Risks and Prerequisites

- `always` is a permanent approval in Hermes. The pipe offers it only if the admin enters `always` in `ALLOWED_APPROVAL_CHOICES` (or in the model entry). Revocation only in Hermes, not in Open WebUI.
- Approvals exist only if the Hermes profile has `approvals.mode` other than `off`. In stack `ki`, `off` is set today; then Hermes sends no `approval.request`.
- Live untested: Dialog types `confirmation` and `input` in Open WebUI v0.12.4 and the path `/v1/runs/*` through `owui-relay` and `hermes-models`.
