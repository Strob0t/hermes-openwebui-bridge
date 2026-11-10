# ADR 0002: Pipe for Hermes `/v1/runs` (Approvals and Session Mapping)

- Status: accepted (2026-11-09)
- Reference: ADR 0001 (Filter instead of Pipe for Embeds), `docs/research/02-current-state-report.md` Measure M8

## Context

Open WebUI currently talks to Hermes `/v1/chat/completions` via `owui-relay` and the `hermes-models` router. The router converts `hermes.tool.progress` into `reasoning_content` and stops runs on client abortion (proven: report, section 1c). It has no backchannel for tool approvals: if Hermes requests an approval, the user cannot answer in Open WebUI.

Hermes v0.23.0 provides the run API for this (proven: `gateway/platforms/api_server_runs.py` l.286-292):
`POST /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (SSE), `POST /v1/runs/{id}/approval`, `/clarify`, `/steer`, `/stop`.

## Decision

An Open WebUI pipe `functions/hermes_runs_pipe.py` addresses `/v1/runs` directly. It appears in Open WebUI as a separate model `hermes-runs`.

A filter is not enough: Filters only see the request (`inlet`) and the completed response (`outlet`/`stream`). An approval needs a query back to the user in the middle of the stream (`__event_call__`) and then a second HTTP call to Hermes. Only a pipe can do this.

## Alternatives

1. Extend the `hermes-models` router. Rejected: The router is an OpenAI-compatible proxy without a UI backchannel. An approval would have to go into the stream as text and back as a new user message, with session state in the router. This mixes proxy and dialog logic.
2. Stick with chat completions and set approvals in Hermes to `auto`. Rejected: Then there are no approvals anymore, or Hermes waits without an answer.
3. Adopt MartianInGreen/openwebui-hermes. Rejected (see report): frozen debug state. Only the patterns are adopted.

## Consequences

- Second model entry in Open WebUI (`hermes-runs`). `hermes-agent` via the router remains for family chats and simple questions.
- The pipe maintains the mapping `chat_id -> session_id` itself. After a restart of Open WebUI, it is lost if file persistence (valve) is off.
- Progress (`tool.started`, `tool.completed`) must be emitted by the pipe itself as `reasoning_content` or status event. The router does not do this for it.
- Filters: Open WebUI filters also apply to pipe models if assigned to the model or globally (Open WebUI doc Filter Function; unverified for v0.12.4). `hermes_embeds` thus remains usable.

## Addendum 2026-11-09

- Path: The pipe goes through `owui-relay` and `hermes-models` (`hermes-gw.hermes:8642`). Proven: the router forwards `/v1/runs/*` frame by frame and only modifies `hermes.tool.progress` frames (`app.py`, `other`/`proxy`/`Relay.frame`). Alternative 1 above remains rejected because no code in the router is needed. No network redesign; Hermes remains in the `internal` network only.
- Session: `X-Hermes-Session-Key` instead of custom mapping (Spec 10.2). The consequence "mapping is lost on restart" no longer applies.
- Approvals: Offer = intersection of Hermes `choices` and admin valve `ALLOWED_APPROVAL_CHOICES` (default `once,deny,session`); `APPROVAL_MODE=auto` for the owner group, manifold with models per group (Spec 10.3, 10.4). Decision Steffen: a lot of freedom for the owner, conservative defaults for other users of the repo.
- Roles: Valve `ALLOWED_ROLES` (default `admin`), because `family_note` of the router does not affect `/v1/runs`.

## Risks

- Approval timeout: Hermes holds the run in `waiting_for_approval`. The pipe sends `deny` after `approval_timeout`.
- Parallel messages in the same chat share a session. Hermes reports a route conflict with 400; the pipe displays the error.
- An approval dialog in Open WebUI shows the planned command. Hermes redacts secrets before sending (`_redact_approval_command`, proven `api_server.py` l.97).
