# Open WebUI Functions

Dieser Ordner enthält Open-WebUI-Funktionen (Filter und Pipes) zur Unterstützung des Hermes-Backends.

## Installation (Filter)

1. In Open WebUI als Administrator anmelden (`Admin > Functions`).
2. Auf **+ (Import)** klicken oder den Code aus `hermes_embeds_filter.py` direkt hochladen/einfügen.
3. Alternativ über die Open-WebUI Admin API einspielen.
4. Filter global aktivieren und Priorität einstellen.

## Valves (`hermes_embeds_filter.py`)

| Valve | Typ | Standard | Beschreibung |
|---|---|---|---|
| `enabled` | `bool` | `True` | Filter ein- oder ausschalten |
| `priority` | `int` | `5` | Ausführungspriorität des Filters |
| `skip_prefixes` | `str` | `"bifrost."` | Kommagetrennte Modell-IDs, die übersprungen werden sollen |
| `max_embeds` | `int` | `3` | Maximale Anzahl an Embeds pro Nachricht |
| `max_bytes` | `int` | `60000` | Maximale Byte-Größe je Embed-Block |
| `keep_code` | `bool` | `False` | Code-Block im Text neben dem Embed behalten |
| `fence_lang` | `str` | `"viz-html"` | Markdown-Fence-Sprachbezeichner für Embed-Blöcke |
| `replacement_text` | `str` | `"*(Diagramm oben eingebettet)*"` | Text als Ersatz für den eingebetteten Code-Block |
| `debug` | `bool` | `False` | Debug-Logging aktivieren |

## Verhalten ohne Filter

Falls der Filter deaktiviert ist oder kein Event-Emitter vorhanden ist (z. B. Task-Aufrufe), bleiben ```viz-html Blöcke als normale Markdown-Codeblöcke erhalten. Open WebUI bietet diese automatisch als interaktive HTML-Artifacts an (Fail-Open-Verhalten).

## Sicherheitshinweise

- Embeds und Artifacts laufen in isolierten `sandbox`-Iframes ohne `allow-same-origin`.
- Die Umgebungsvariable `IFRAME_CSP` sollte konfiguriert werden (z. B. `connect-src 'none'`), um den Datenabfluss aus Iframes zu verhindern.
- HTML-Inhalte stammen ausschließlich vom eigenen Hermes-Agenten.

## Pipe `hermes_runs_pipe.py`

Manifold-Pipe: die Modelle kommen per Discovery aus `GET <HERMES_BASE_URL>/models` (OpenAI-Format), jedes erscheint als eigenes Modell in Open WebUI. Ein Chat läuft als Hermes-Run über `/v1/runs`; die gewählte Modell-ID geht als `model` in den Run-Body. Spezifikation: [`docs/spec/hermes-runs-pipe.md`](../docs/spec/hermes-runs-pipe.md) (Abschnitt 10 gilt vor 2, 3 und 5), Entscheidung: [ADR 0002](../docs/decisions/0002-runs-pipe.md).

### Installation

1. `Admin > Functions`, **+**, Code aus `hermes_runs_pipe.py` einfügen, speichern, Funktion aktivieren.
2. Valves setzen (mindestens `HERMES_API_KEY`).
3. `Admin > Settings > Models`: Sichtbarkeit jedes Pipe-Modells pro Gruppe setzen (Modellrechte). Das Valve `ALLOWED_ROLES` prüft zusätzlich die Rolle.
4. Optional: Filter `hermes_embeds` dem Pipe-Modell zuordnen.

### Valves (nur Admin, keine UserValves)

| Valve | Typ | Standard | Beschreibung |
|---|---|---|---|
| `HERMES_BASE_URL` | `str` | `http://hermes-gw.hermes:8642/v1` | Basis-URL der Hermes-API (über `owui-relay` und `hermes-models`) |
| `HERMES_API_KEY` | `str` | `""` | Bearer-Token; leer = kein `Authorization`-Header |
| `MODEL_DISCOVERY` | `bool` | `True` | Modelle aus `GET <HERMES_BASE_URL>/models` laden. `False`: genau ein Eintrag `Hermes (Gateway-Default)` ohne `model` im Run-Body |
| `MODEL_FILTER` | `str` (Regex) | `""` | Nur Modell-IDs, auf die der Regex passt (`re.search`); leer = alle |
| `OWNED_BY_FILTER` | `str` | `""` | Nur Modelle mit diesen `owned_by`-Werten, kommagetrennt; leer = alle |
| `MODELS_CACHE_TTL` | `int` | `300` | Sekunden Cache für die Modellliste. Fehler beim Laden: letzte Liste bleibt; ohne Liste der Eintrag `Hermes (Gateway-Default)` |
| `MODEL_POLICIES` | `str` (JSON) | `[]` | Liste von `{"match": <Regex auf Modell-ID>, ...}`; erster Treffer gilt und darf `approval_mode`, `allowed_approval_choices`, `auto_choice`, `allowed_roles` überschreiben. Ungültiges JSON: keine Policies und Warnung im Log |
| `ROUTE_HEADER` | `str` | `X-Hermes-Route` | Antwort-Header von `POST /runs`, der als Statuszeile `Route: ...` erscheint (für Routing-Proxys); leer = aus |
| `REQUEST_TIMEOUT` | `int` | `30` | Sekunden für `POST /runs`, `/approval`, `/clarify`, `/stop`, `GET /runs/{id}` |
| `STREAM_IDLE_TIMEOUT` | `int` | `900` | Sekunden ohne Event im Stream (Keepalives zählen nicht), dann `stop` und Fehlerzeile |
| `APPROVAL_MODE` | `str` | `ask` | `ask` Dialog, `auto` ohne Dialog mit `AUTO_CHOICE`, `deny` immer `deny` |
| `ALLOWED_APPROVAL_CHOICES` | `str` | `once,deny,session` | Angebot = Schnittmenge aus Hermes-`choices` und dieser Liste, in dieser Reihenfolge |
| `AUTO_CHOICE` | `str` | `session` | Wahl bei `auto`; sonst `once`, sonst die erste Hermes-Wahl |
| `APPROVAL_TIMEOUT` | `int` | `300` | Sekunden für einen Dialog; danach `deny` |
| `SHOW_TOOL_PROGRESS` | `bool` | `True` | Tool-Events als Status und als Reasoning-Block |
| `ALLOWED_ROLES` | `str` | `admin` | Open-WebUI-Rollen mit Zugang, kommagetrennt; `*` = alle |
| `DEBUG` | `bool` | `False` | Debug-Logging (auch `X-Hermes-Session-Id`) |

### Modell-Discovery

- Anzeigename aus `name`, sonst die `id`. Ein Feld `provider` im Upstream-Eintrag geht als `provider` in den Run-Body.
- Open WebUI ruft `pipes()` beim Laden der Modellliste auf; die Pipe hält das Ergebnis `MODELS_CACHE_TTL` Sekunden. `pipes()` ist `async`: Open WebUI v0.12.4 (`backend/open_webui/functions.py`, `get_function_models`) prüft `asyncio.iscoroutinefunction` und wartet auf die Coroutine; ein Fehler dort ergibt eine leere Liste, deshalb liefert die Pipe immer mindestens einen Eintrag.
- Nach einem Neustart von Open WebUI kennt die Pipe die Liste erst nach dem nächsten `pipes()`-Aufruf. Ein Modell, das nicht im Cache ist, wird unverändert als `model` an Hermes gesendet.
- Einziger eingebauter Eintrag: `hermes-default` (`Hermes (Gateway-Default)`). Er sendet kein `model`, Hermes nimmt dann seinen Standard.

### `MODEL_POLICIES`-Beispiel

```json
[
  {"match": "^auto", "approval_mode": "auto", "auto_choice": "session"},
  {"match": ".*", "approval_mode": "ask", "allowed_approval_choices": "once,deny", "allowed_roles": "admin,user"}
]
```

- Erste Regel: alle Modelle, deren ID mit `auto` beginnt, laufen ohne Freigabe-Dialog. Sichtbarkeit dieser Modelle nur für die Owner-Gruppe setzen (Modellrechte in Open WebUI).
- Zweite Regel: alle anderen Modelle; jede Freigabe als Ja/Nein-Dialog, nur `once` oder `deny`.
- Ohne Treffer gelten die globalen Valves (`APPROVAL_MODE`, `ALLOWED_APPROVAL_CHOICES`, `AUTO_CHOICE`, `ALLOWED_ROLES`).

### Betrieb hinter einem Routing-Proxy

Steht vor Hermes ein Proxy, der `/v1/models` mit eigenen Modell-IDs beantwortet (z. B. Klassen wie `auto`, `auto-medium`, `auto-strong` plus durchgereichte Provider-Modelle), listet die Pipe genau diese IDs. Typische Einstellung:

- `OWNED_BY_FILTER=hermes`: nur die Proxy-Klassen anzeigen, nicht die Provider-Modelle, die Open WebUI vielleicht schon direkt kennt.
- `ROUTE_HEADER`: Liefert der Proxy einen Header mit der gewählten Route, zeigt die Pipe ihn als Statuszeile.
- Fällt der Proxy weg, zeigt `/v1/models` von Hermes dessen Modelle; die Pipe braucht keine Änderung.

### Verhalten

- Request an Hermes: `POST /runs` mit `{"input": <letzte Nutzernachricht>, "stream": false, "model": <gewählte ID>}` (plus `provider`, falls die Discovery eines liefert; ohne `model` beim Eintrag `hermes-default`) und `Idempotency-Key: owui:<chat_id>:<message_id>`; danach `GET /runs/{id}/events` (SSE).
- Header an alle Requests: `X-Hermes-Session-Key: owui:<user_id>:<chat_id>` (Hermes lädt die Historie selbst), `X-Hermes-User-Role`, `X-Hermes-User-Name` (URL-kodiert wie bei Open WebUI), `Accept-Encoding: identity`, `Authorization` falls Key gesetzt.
- Text (`message.delta`, `message.interim`, Rest von `run.completed.output`) geht als `content` in den Stream.
- Tool-Fortschritt: `__event_emitter__` Typ `status` und zusätzlich `delta.reasoning_content`-Chunks (Dict). Open WebUI v0.12.4 (`functions.py`, `process_line`) gibt ein Dict als `data: <json>` weiter, die Middleware liest `delta.reasoning_content` als Reasoning-Block. Ohne `stream` verbindet Open WebUI alle Chunks mit `str()`; dann sendet die Pipe keine Dict-Chunks, nur Status-Events.
- Freigabe (`approval.request`): Angebot nur `once,deny` → Dialog `confirmation` (Ja = `once`). Mehr Wahlen → Dialog `input` ("Antwort: once, deny oder session", Vorgabe `once`); ungültige Eingabe = `deny`. Timeout oder Dialogfehler = `deny`.
- Ohne `__event_call__` (z. B. API-Aufruf): Die Pipe zeigt den Befehl, endet und merkt sich den Run pro Chat. Die nächste Nachricht `/approve [once|session|always]` oder `/deny` sendet die Wahl und hängt sich mit `Last-Event-ID` wieder an.
- Rückfrage (`clarify.request`): Dialog `input`, Antwort an `POST /runs/{id}/clarify`.
- Chat-Befehl `/reset`: neuer Key mit Suffix `:r<n>`, also neue Hermes-Session. Der Zähler liegt nur im Speicher; nach einem Neustart von Open WebUI gilt wieder der Key ohne Suffix.
- Client-Abbruch: `POST /runs/{id}/stop`. Abbruch des Streams ohne Ende: bis zu 3 Reconnects, danach `GET /runs/{id}`.
- Open-WebUI-Tasks (Titel, Tags, Follow-ups) gehen nicht an Hermes; die Pipe gibt leeren Text zurück.

### Risiken und Voraussetzungen

- `always` ist eine dauerhafte Freigabe in Hermes. Die Pipe bietet sie nur an, wenn der Admin `always` in `ALLOWED_APPROVAL_CHOICES` (oder im Modell-Eintrag) einträgt. Widerruf nur in Hermes, nicht in Open WebUI.
- Freigaben gibt es nur, wenn das Hermes-Profil `approvals.mode` ungleich `off` hat. Im Stack `ki` ist heute `off` gesetzt; dann sendet Hermes kein `approval.request`.
- Live ungetestet: Dialogtypen `confirmation` und `input` in Open WebUI v0.12.4 und der Pfad `/v1/runs/*` durch `owui-relay` und `hermes-models`.
