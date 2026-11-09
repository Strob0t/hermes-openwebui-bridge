# ADR 0002: Pipe für Hermes `/v1/runs` (Freigaben und Session-Mapping)

- Status: akzeptiert (2026-11-09)
- Bezug: ADR 0001 (Filter statt Pipe für Embeds), `docs/research/02-ist-analyse-bericht.md` Maßnahme M8

## Kontext

Open WebUI spricht heute über `owui-relay` und den Router `hermes-models` mit Hermes `/v1/chat/completions`. Der Router wandelt `hermes.tool.progress` in `reasoning_content` um und stoppt Runs bei Client-Abbruch (belegt: Bericht, Abschnitt 1c). Er hat keinen Rückkanal für Tool-Freigaben: Fordert Hermes eine Freigabe an, kann der Nutzer in Open WebUI nicht antworten.

Hermes v0.23.0 bietet dafür die Run-API (belegt: `gateway/platforms/api_server_runs.py` Z.286-292):
`POST /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (SSE), `POST /v1/runs/{id}/approval`, `/clarify`, `/steer`, `/stop`.

## Entscheidung

Eine Open-WebUI-Pipe `functions/hermes_runs_pipe.py` spricht `/v1/runs` direkt an. Sie erscheint in Open WebUI als eigenes Modell `hermes-runs`.

Ein Filter reicht nicht: Filter sehen nur den Request (`inlet`) und die fertige Antwort (`outlet`/`stream`). Eine Freigabe braucht eine Rückfrage an den Nutzer mitten im Stream (`__event_call__`) und danach einen zweiten HTTP-Aufruf an Hermes. Das kann nur eine Pipe.

## Alternativen

1. Router `hermes-models` erweitern. Abgelehnt: Der Router ist ein OpenAI-kompatibler Proxy ohne UI-Rückkanal. Eine Freigabe müsste als Text in den Stream und als neue Nutzernachricht zurück, mit Sitzungszustand im Router. Das vermischt Proxy und Dialoglogik.
2. Bei Chat Completions bleiben und Freigaben in Hermes auf `auto` stellen. Abgelehnt: Dann gibt es keine Freigaben mehr, oder Hermes wartet ohne Antwort.
3. MartianInGreen/openwebui-hermes übernehmen. Abgelehnt (siehe Bericht): eingefrorener Debug-Stand. Übernommen werden nur die Muster.

## Konsequenzen

- Zweiter Modelleintrag in Open WebUI (`hermes-runs`). `hermes-agent` über den Router bleibt für Familienchats und einfache Fragen.
- Die Pipe hält das Mapping `chat_id -> session_id` selbst. Nach einem Neustart von Open WebUI geht es verloren, wenn die Datei-Persistenz (Valve) aus ist.
- Fortschritt (`tool.started`, `tool.completed`) muss die Pipe selbst als `reasoning_content` oder Status-Event ausgeben. Der Router macht das für sie nicht.
- Filter: Open-WebUI-Filter gelten auch für Pipe-Modelle, wenn sie dem Modell oder global zugeordnet sind (Open-WebUI-Doku Filter Function; für v0.12.4 ungeprüft). `hermes_embeds` bleibt damit nutzbar.

## Risiken

- Freigabe-Timeout: Hermes hält den Run in `waiting_for_approval`. Die Pipe sendet nach `approval_timeout` `deny`.
- Parallele Nachrichten im selben Chat teilen eine Session. Hermes meldet einen Routenkonflikt mit 400; die Pipe zeigt den Fehler an.
- Ein Freigabe-Dialog in Open WebUI zeigt den geplanten Befehl. Hermes redigiert Secrets vor dem Senden (`_redact_approval_command`, belegt `api_server.py` Z.97).
