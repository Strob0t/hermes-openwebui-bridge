# Spezifikation: `functions/hermes_runs_pipe.py`

Stand 2026-11-09. Quelle für die Hermes-Fakten: Quellcode v0.23.0, `gateway/platforms/api_server_runs.py` (R) und `gateway/platforms/api_server.py` (A). Zeilenangaben beziehen sich auf den Tag v0.23.0. Punkte mit "ungeprüft" braucht Teil 3b vor dem Code noch gegen die Live-API.

## 1. Zweck

Open-WebUI-Pipe, die einen Chat als Hermes-Run über `/v1/runs` ausführt, Tool-Fortschritt streamt, Freigaben als Dialog an den Nutzer gibt und eine Hermes-Session pro Open-WebUI-Chat hält.

## 2. Valves

| Valve | Typ | Default | Zweck |
|---|---|---|---|
| `HERMES_BASE_URL` | str | `http://hermes-gw.hermes:8642/v1` | Basis-URL der Hermes-API (im Stack `ki`: direkt oder über `hermes-models`, Teil 3b entscheidet) |
| `HERMES_API_KEY` | str | `""` | Bearer-Token; leer = kein Header |
| `MODEL_NAME` | str | `hermes-runs` | Anzeigename in Open WebUI |
| `REQUEST_TIMEOUT` | int | `30` | Sekunden für `POST /runs`, `/approval`, `/stop` |
| `STREAM_IDLE_TIMEOUT` | int | `900` | Sekunden ohne SSE-Frame, dann Abbruch mit Fehlermeldung |
| `APPROVAL_MODE` | `ask\|auto\|deny` | `ask` | `ask` = Dialog, `auto` = immer `once`, `deny` = immer `deny` |
| `APPROVAL_TIMEOUT` | int | `300` | Sekunden bis automatisch `deny` |
| `SHOW_TOOL_PROGRESS` | bool | `true` | Tool-Events als `reasoning_content`-Block ausgeben |
| `SESSION_TTL_HOURS` | int | `72` | Mapping-Einträge älter als TTL verwerfen |
| `SESSION_FILE` | str | `""` | leer = nur im Speicher; sonst JSON-Datei unter dem Open-WebUI-Datenverzeichnis |

## 3. Session-Mapping

- Schlüssel: Open-WebUI `chat_id` aus `__metadata__["chat_id"]`. Fallback, wenn leer: `user_id` plus SHA-256 der ersten Nutzernachricht (Muster aus MartianInGreen, keine Code-Übernahme).
- Wert: `{"session_id": "owui-<chat_id>", "updated_at": <epoch>}`.
- Erster Aufruf: `session_id = "owui-" + chat_id` wird selbst gewählt und an Hermes gesendet. Hermes akzeptiert ein Client-`session_id` (R Z.801: `body.get("session_id") or stored_session_id`). Ungeprüft: ob Hermes ein unbekanntes `session_id` neu anlegt oder 404 gibt.
- Danach: dasselbe `session_id`. Verlauf aus Open WebUI wird nicht noch einmal gesendet, nur die letzte Nutzernachricht (`input`).
- Alternative, falls eigenes `session_id` nicht geht: `previous_response_id` = letzte `run_id` (R Z.800).
- Persistenz: in-memory `dict`; wenn `SESSION_FILE` gesetzt, atomar schreiben (`tmp` + `os.replace`). Beim Laden Einträge älter als `SESSION_TTL_HOURS` verwerfen.
- Slash-Befehl `/reset` löscht den Eintrag für den Chat und antwortet "Session zurückgesetzt".

## 4. Ablauf eines Runs

1. `POST {base}/runs` mit `{"input": <text>, "session_id": <sid>, "model": <optional>, "stream": false}`. Antwort `202` mit `{"run_id", "status"}` (R Z.550). Idempotenz: Header `Idempotency-Key = chat_id + ":" + message_id` (R Z.786, Feldname ungeprüft: Header oder Body).
2. `GET {base}/runs/{run_id}/events` als SSE. Frames: `id: <seq>` und `data: <json>` mit Feldern `object`, `run_id`, `event`, `seq` (R Z.1297-1300). Bei Reconnect Header `Last-Event-ID = <seq>` (R Z.1273).
3. Events und Reaktion der Pipe:

| Event (R) | Felder | Pipe |
|---|---|---|
| `run.queued`, `run.running` | `status` | Status-Event "Hermes arbeitet" |
| `message.delta` | `delta` | als `content`-Chunk streamen (R Z.1072) |
| `message.interim` | `text`, `already_streamed` | nur ausgeben, wenn `already_streamed` falsch (R Z.1081) |
| `tool.started` | `tool`, `preview`, `tool_call_id` | `reasoning_content`: `> tool: preview` (R Z.141) |
| `tool.completed` | `tool`, `duration`, `error`, `preview` | `reasoning_content`: Dauer und Fehlerflag (R Z.142) |
| `reasoning.available` | `text` | `reasoning_content` (R Z.145) |
| `subagent.start`, `subagent.complete` | `preview` | Status-Event |
| `approval.request` | `command`, `description`, `choices`, `request_id` | Abschnitt 5 (A Z.90-102) |
| `approval.responded` | `choice`, `resolved` | Hinweiszeile `(choice)` |
| `clarify.request` | Prompt-Felder (R Z.1115) | wie Freigabe, aber `__event_call__` Typ `input`; Antwort an `POST /runs/{id}/clarify`. Felder ungeprüft |
| `run.steered` | `accepted` | ignorieren |
| `replay.truncated` | `oldest_retained_seq` | Hinweis "Teile des Fortschritts fehlen" |
| `run.completed` | `output`, `usage` | Rest von `output` ausgeben, der noch nicht gestreamt wurde; `done` (R Z.1163) |
| `run.failed` | `error` | Fehlertext; `done` |
| `run.cancelled`, `run.interrupted` | `error` optional | Hinweis; `done` (R Z.1087-1092) |

4. Open-WebUI-Ausgabe: OpenAI-kompatible SSE-Chunks (`choices[0].delta.content` beziehungsweise `delta.reasoning_content`), am Ende `finish_reason: "stop"` und `data: [DONE]`. Zusätzlich `__event_emitter__` Typ `status` für Zustandswechsel.

## 5. Freigaben

- `APPROVAL_MODE=ask`: `__event_call__({"type": "confirmation", "data": {"title": "Hermes braucht eine Freigabe", "message": "<description>\n\n```\n<command>\n```"}})`. Ergebnis wahr = `once`, falsch = `deny`. Typ `confirmation` ist in der Open-WebUI-Doku (Events, `__event_call__`) genannt; Verhalten in v0.12.4 ungeprüft, Live-Test in Teil 3b.
- Kein `__event_call__` verfügbar (zum Beispiel API-Aufruf ohne UI): Pipe schreibt den Befehl in den Stream und beendet ihn mit dem Hinweis, mit `/approve` oder `/deny` zu antworten. Die nächste Nutzernachricht mit diesem Befehl geht an `POST /runs/{run_id}/approval`; danach hängt sich die Pipe wieder an `/events` mit `Last-Event-ID`.
- Antwort an Hermes: `POST {base}/runs/{run_id}/approval` mit `{"choice": "once"|"session"|"always"|"deny", "request_id": <aus Event>}` (R Z.1372-1405). `409` = keine offene Freigabe, `400` = ungültige Wahl. Die Pipe sendet nur `once` oder `deny`; `session` und `always` nur, wenn Steffen das später freigibt.
- `APPROVAL_TIMEOUT` abgelaufen: `deny` senden, Hinweis im Stream.
- `APPROVAL_MODE=auto` nur für Testzwecke; Default bleibt `ask`.

## 6. Abbruch und Fehler

- Client bricht ab (Generator wird geschlossen, `GeneratorExit`/`CancelledError`): `POST {base}/runs/{run_id}/stop` (R Z.1523, Antwort `{"status": "stopping"}`).
- Hermes nicht erreichbar (Verbindungsfehler, Timeout bei `POST /runs`): eine Zeile "Hermes ist nicht erreichbar (<base>)", `done`, kein Retry.
- SSE bricht ab ohne Terminal-Event: bis zu 3 Reconnects mit `Last-Event-ID`, dann `GET /runs/{run_id}` lesen und `output` aus dem Status nehmen (R Z.330 enthält `output`).
- `STREAM_IDLE_TIMEOUT` erreicht: `stop` senden, Fehlermeldung.
- Hermes `400` Routenkonflikt (R Z.806-811): Fehlertext anzeigen, Mapping nicht löschen.

## 7. Abgrenzung zum Router `hermes-models`

| Aufgabe | Router | Pipe |
|---|---|---|
| Chat Completions, Modellrouting, `served-model`-Marker | ja | nein |
| `hermes.tool.progress` zu `reasoning_content` | ja (Chat Completions) | selbst, aus `tool.*`-Events |
| Stop bei Client-Abbruch | ja | ja, über `/stop` |
| Freigaben | nein | ja |
| Session pro Chat | nein (Header-basiert, laut Bericht ungeprüft) | ja, Mapping |
| Embeds | Filter `hermes_embeds` | derselbe Filter, wenn dem Modell zugeordnet |

## 8. Tests (unittest, kein Netz)

HTTP wird über eine injizierbare Client-Fabrik gemockt (Standard `aiohttp`, Open WebUI liefert es mit).

1. `test_session_mapping_new_chat`: neuer `chat_id` ergibt `owui-<chat_id>`, Eintrag gespeichert.
2. `test_session_mapping_ttl`: Eintrag älter als TTL wird verworfen.
3. `test_session_file_roundtrip`: Schreiben und Laden der JSON-Datei, atomar.
4. `test_message_delta_stream`: `message.delta`-Frames werden zu `content`-Chunks in Reihenfolge.
5. `test_tool_progress_as_reasoning`: `tool.started`/`tool.completed` ergeben `reasoning_content`; mit `SHOW_TOOL_PROGRESS=false` nichts.
6. `test_run_completed_remaining_output`: `output` wird nur um den schon gestreamten Teil ergänzt.
7. `test_approval_confirmation_once`: `approval.request` ruft `__event_call__` auf; `True` sendet `{"choice":"once","request_id":...}`.
8. `test_approval_timeout_deny`: kein Ergebnis bis Timeout sendet `deny`.
9. `test_approval_mode_deny`: kein Dialog, direkt `deny`.
10. `test_approval_fallback_without_event_call`: Stream endet mit Hinweis; `/approve` als nächste Nachricht sendet die Wahl und hängt sich wieder an.
11. `test_client_cancel_sends_stop`: Schließen des Generators ruft `POST /stop`.
12. `test_reconnect_with_last_event_id`: Abbruch nach `seq=4` setzt Header `Last-Event-ID: 4`.
13. `test_hermes_unreachable`: Verbindungsfehler ergibt eine Fehlerzeile und `done`.
14. `test_reset_command`: `/reset` löscht den Eintrag.

## 9. Offen für Teil 3b

- Live prüfen: `__event_call__` Typ `confirmation` in Open WebUI v0.12.4.
- Live prüfen: Durchreichen von `/v1/runs/*` durch `owui-relay` und `hermes-models` (Abschnitt 10.1).
- Freigaben live: nur mit einem Hermes-Profil, dessen `approvals.mode` nicht `off` ist (Abschnitt 10.3).

## 10. Nachtrag 2026-11-09 (Entscheidungen Steffen, Code-Fakten)

Die Punkte unten ergänzen oder ersetzen die Abschnitte 2, 3 und 5.

### 10.1 Pfad zu Hermes (ersetzt die offene Frage in Abschnitt 9)

- Entscheidung: direkter Pfad ohne eigenen Code im Router. Belegt (`homeserver/ki/build/hermes-models/app.py`): der Router leitet jeden Pfad außer `/v1/chat/completions` und `/v1/responses` über `proxy()` durch (`other`, Z.747-750). Bei `text/event-stream` puffert `Relay` nur bis zum Frame-Ende (`FRAME_END`), schreibt nur Frames mit `event: hermes.tool.progress|hermes.status` um und gibt alle anderen Frames unverändert zurück (`Relay.frame`, Z.270-271). Request-Header außer Hop-by-Hop gehen durch (`HOP`, Z.57), also auch `Idempotency-Key` und `Last-Event-ID`. `owui-relay` ist TCP-HAProxy mit 15 min Timeout.
- Default `HERMES_BASE_URL = http://hermes-gw.hermes:8642/v1` (Alias liegt in `NO_PROXY` von Open WebUI). Kein Netzumbau. Hermes bleibt allein im Netz `internal`.
- `Idempotency-Key` ist ein Request-Header (belegt R Z.781-785: 1-255 sichtbare ASCII-Zeichen). Wert: `owui:<chat_id>:<message_id>`.

### 10.2 Session (ersetzt Abschnitt 3)

- Kein eigenes `session_id` und keine Mapping-Datei. Die Pipe sendet den Header `X-Hermes-Session-Key: owui:<user_id>:<chat_id>`, wie der bestehende Chat-Pfad (Referenz `owui-hermes-api.md`). Hermes löst den Key selbst zur Session auf (`_declared_conversation_session`, A Z.1603; R Z.843-847: `selected_session_id = session_id or declared(gateway_session_key)`) und lädt die Historie aus `state.db`. Body ohne `session_id`, `input` = nur die letzte Nutzernachricht.
- `/reset`: die Pipe hängt an den Key ein Suffix `:r<n>` (in-memory Zähler pro `chat_id`). Hermes beginnt eine neue Session. Valves `SESSION_TTL_HOURS` und `SESSION_FILE` entfallen.
- Antwort-Header `X-Hermes-Session-Id` nur loggen (`debug`).

### 10.3 Freigaben (ersetzt Abschnitt 5, Teil "nur once oder deny")

Belegt (A Z.84-101): Hermes sendet im Event `approval.request` das Feld `choices`, Teilmenge von `once, session, always, deny`; `always` fehlt bei `allow_permanent=false`, `session` und `always` fehlen bei `smart_denied`. `POST /approval` nimmt alle vier (R Z.1377), Aliase `approve|approved|allow` = `once` (R Z.1353).

Valves (Admin-Valves, keine UserValves):

| Valve | Default | Zweck |
|---|---|---|
| `APPROVAL_MODE` | `ask` | `ask` Dialog; `auto` ohne Dialog mit `AUTO_CHOICE`; `deny` immer `deny` |
| `ALLOWED_APPROVAL_CHOICES` | `once,deny,session` | Angebot = Schnittmenge aus Event-`choices` und dieser Liste, in dieser Reihenfolge |
| `AUTO_CHOICE` | `session` | Wahl bei `APPROVAL_MODE=auto`; fällt auf `once` zurück, wenn nicht in Event-`choices` |
| `APPROVAL_TIMEOUT` | `300` | danach `deny` |

Dialog: Schnittmenge hat nur `once,deny` → `__event_call__` Typ `confirmation` (wahr = `once`). Schnittmenge hat mehr → `__event_call__` Typ `input` mit Text "Antwort: once, session, always oder deny" (Default-Wert `once`); ungültige Eingabe = `deny`. Ohne `__event_call__`: Fallback per Chatbefehl `/approve [once|session|always]` oder `/deny` (Abschnitt 5 bleibt). `always` ist nur erreichbar, wenn der Admin es in `ALLOWED_APPROVAL_CHOICES` einträgt; README dokumentiert das Risiko (dauerhafte Freigabe, Widerruf nur in Hermes).

Stack `ki` heute: `config.yaml` hat `approvals.mode: "off"` (belegt). Hermes sendet dort keine `approval.request`. Der Freigabe-Pfad ist für Profile mit `smart` oder `manual` und wird nur per Mock getestet.

### 10.4 Manifold und Rollen

- Die Pipe ist ein Manifold (`type = "manifold"`, async Methode `pipes()`). Modelle per Discovery aus `GET <HERMES_BASE_URL>/models` (OpenAI-Format; `name` als Anzeigename, sonst `id`; `provider` wird übernommen). Valves: `MODEL_DISCOVERY` (Default an), `MODEL_FILTER` (Regex auf die ID), `OWNED_BY_FILTER` (Liste), `MODELS_CACHE_TTL` (Default 300 s). Fehler beim Laden: letzte Liste bleibt, sonst der einzige eingebaute Eintrag `hermes-default` (`Hermes (Gateway-Default)`), der kein `model` sendet. Die gewählte ID geht als `model` in den Run-Body. Freigabe-Regeln pro Modell über `MODEL_POLICIES` (JSON-Liste, `match` = Regex auf die ID, erster Treffer gilt; Overrides `approval_mode`, `allowed_approval_choices`, `auto_choice`, `allowed_roles`). Sichtbarkeit pro Gruppe regelt Open WebUI (Modellrechte). Der Antwort-Header aus `ROUTE_HEADER` (Default `X-Hermes-Route`) von `POST /runs` erscheint als Statuszeile.
- Valve `ALLOWED_ROLES` Default `admin`. Rolle aus `__user__["role"]`. Andere Rolle: eine Zeile "Dieses Modell ist für dein Konto nicht freigegeben", kein Hermes-Aufruf. Grund: der Familien-Hinweis des Routers (`family_note`) wirkt nur auf Chat-Pfade, nicht auf `/v1/runs`.
- Die Pipe sendet `X-Hermes-User-Role` und `X-Hermes-User-Name` aus `__user__` mit (Format wie der Router erwartet; heute ohne Wirkung auf `/v1/runs`, für spätere Router-Logik).

### 10.5 Tests (ergänzt Abschnitt 8)

Entfallen: 1-3 (Mapping, TTL, Datei). Neu:

15. `test_session_key_header`: Request trägt `X-Hermes-Session-Key: owui:<user>:<chat>`, Body ohne `session_id`.
16. `test_reset_suffix`: nach `/reset` endet der Key auf `:r1`, danach `:r2`.
17. `test_idempotency_header`: `Idempotency-Key` = `owui:<chat>:<message_id>`, nur sichtbare ASCII.
18. `test_choices_intersection`: Event `["once","session","always","deny"]` mit Valve `once,deny,session` ergibt `["once","deny","session"]`; Event `["once","deny"]` ergibt `["once","deny"]`.
19. `test_input_dialog_multi_choice`: Schnittmenge > 2 ruft `__event_call__` Typ `input`; Antwort `session` sendet `{"choice":"session"}`; Antwort `foo` sendet `deny`.
20. `test_auto_mode_choice_fallback`: `auto` mit `AUTO_CHOICE=session`, Event ohne `session` sendet `once`.
21. `test_role_denied`: `__user__["role"]="user"` mit Default `ALLOWED_ROLES` ergibt die Hinweiszeile, kein HTTP-Aufruf.
22. `test_manifold_policies_per_model`: `pipes()` liefert die Discovery-Einträge; `MODEL_POLICIES` greift pro Modell (erster Regex-Treffer).
23. `test_pipes_from_discovery_with_filters`: `GET /models` wird gelesen; `MODEL_FILTER` und `OWNED_BY_FILTER` filtern.
24. `test_cache_ttl`: innerhalb `MODELS_CACHE_TTL` kein zweiter Abruf, danach einer.
25. `test_fetch_error_keeps_last_list_or_default`: Netzfehler oder HTTP-Fehler hält die letzte Liste; ohne Liste der Eintrag `hermes-default`.
26. `test_discovery_off`: `MODEL_DISCOVERY=False` ergibt nur `hermes-default`, kein HTTP-Aufruf.
27. `test_run_body_model_and_provider`: gewählte ID als `model` im Body, `provider` falls vorhanden, kein `model` bei `hermes-default`; unbekannte ID geht unverändert durch.
28. `test_policy_first_match`: erste passende Policy gewinnt; ungültiges JSON ergibt keine Policies und eine Warnung.
29. `test_route_header_status`: Header aus `ROUTE_HEADER` erscheint als Statuszeile `Route: ...` vor `Hermes arbeitet`; leeres Valve schaltet es ab.
