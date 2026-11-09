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

- Live prüfen: akzeptiert Hermes ein unbekanntes `session_id` (Abschnitt 3)?
- Live prüfen: `Idempotency-Key` als Header oder Body-Feld.
- Live prüfen: `__event_call__` Typ `confirmation` in Open WebUI v0.12.4.
- Entscheidung Steffen: `HERMES_BASE_URL` direkt auf `hermes:8642` (eigenes Netz nötig) oder über `hermes-models`.
