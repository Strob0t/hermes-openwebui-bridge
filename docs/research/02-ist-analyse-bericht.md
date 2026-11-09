# Bericht: Visualisierung in Open WebUI mit Hermes als Backend

Stand: 2026-11-09. Analyse-Agent: Hermes (nur lesend). Quellensammlung: `hermes-openwebui-visualisierung.md` (Abschnitte 1 bis 3 gelesen).
Statuskennzeichnung: **belegt** = Datei, Befehlsausgabe oder Quelle in diesem Lauf gelesen; **Annahme** = Schluss aus belegten Fakten; **ungeprüft** = nicht erreichbar oder nicht gelesen.

## 0. Zusammenfassung (5 Zeilen)

1. Hermes v0.23.0 (aktuellstes Release, belegt) führt alle Tools serverseitig aus; Per-Request-Tools (#57431) und `tools: []` (#84652) sind weiter offen. Der Kernbefund der Sammlung bleibt: Open-WebUI-Tools und das automatische Rich-UI-Embedding greifen bei Hermes nicht (belegt).
2. Bilder: Hermes inlined `MEDIA:`-Tags nur in nicht-streamenden Antworten. Open WebUI streamt, deshalb kommt der rohe Pfad an. Fix-PR #103239 ist offen, zuletzt aktualisiert 2026-11-08 (belegt). Sofort-Workaround: Streaming für das Modell `hermes-agent` in Open WebUI ausschalten (Quelle: Issue-Kommentare, lokal nicht getestet).
3. Der eigene Router `hermes-models` (Relay-Klasse, puffert bereits SSE-Frames und hängt eine Marker-Zeile an) ist der natürliche Ort, `MEDIA:`-Tags im Stream selbst in base64 umzusetzen, ohne auf Upstream zu warten (Annahme aus gelesenem Code).
4. Mermaid, SVG und HTML als Codeblock sind heute ohne Umbau nutzbar; kein installierter Hermes-Skill nutzt sie, `architecture-diagram` behauptet sogar, Open WebUI zeige nur PNG (belegt). Ein Format-Regel-Skill ist die günstigste Maßnahme.
5. Für echte Inline-Embeds (Chart.js-Dashboards) braucht es eine kleine Open-WebUI-Filter-Function, die einen Markerblock aus dem Hermes-Text in ein `embeds`-Event wandelt (Open-WebUI-Doku belegt, Umsetzung Entwurf). `IFRAME_CSP` ist nicht gesetzt (belegt) und sollte mit der ersten HTML-Ausgabe kommen.

## 1. IST-Zustand

### a) Open WebUI

| Punkt | Wert | Status / Beleg |
|---|---|---|
| Version | `ghcr.io/open-webui/open-webui:v0.12.4` (Release 2026-10-25) | belegt: `homeserver/ki/compose.yaml` Z.453, Komodo inspect `open-webui` running, GitHub Release v0.12.4 |
| Upstream aktuell | v0.13.3 | belegt: GitHub `releases/latest` |
| Deployment | Stack `ki` (Komodo, `main`), Netze `assistant`, `ki-ui-chat`, `OFFLINE_MODE=true`, Egress nur über `squid-assistant` | belegt: compose Z.452-509 |
| Rendering-Env | `IFRAME_CSP` nicht gesetzt; keine Artifacts-/Sandbox-Variablen in compose | belegt: grep in compose ohne Treffer |
| Same-Origin-Iframe-Nutzereinstellung, Artifacts-Toggle | in der DB (`/srv/...` für mich gesperrt) | ungeprüft (Lücke) |
| Functions im Repo | 4 Filter: `hermes_reasoning_filter.py` (1.0.0), `chat_condense_filter.py` (1.1.1), `context_budget_filter.py` (1.1.0), `claude_usage_filter.py` | belegt: `appdata/open-webui/functions/`; Live-Status (is_active) nur laut README |
| Pipes / Tools / Actions | keine | belegt: kein Pipe/Tool-Code im Repo; Live-DB ungeprüft |
| Angebundene Modelle | Connection `http://hermes-gw.hermes:8642/v1` (hermes-models): `hermes-agent`, `auto`, `auto-medium`, `auto-strong`, `aux-cheap`, `aux-side`, freigegebene Bifrost-Modelle; zweite Connection direkt zu Bifrost (`bifrost.*`, Task-Modell) | belegt: `ki-assistant/README.md` Z.429-449, `ki/README.md` Z.57-77 |
| API-Typ | Chat Completions (Router behandelt `/v1/responses` auch, Open WebUI nutzt laut README Chat Completions) | belegt für Router-Code; Live-Einstellung in DB ungeprüft |

### b) Hermes

| Punkt | Wert | Status / Beleg |
|---|---|---|
| Version | `ki-hermes:v0.23.0-3`, `FROM nousresearch/hermes-agent:v0.23.0` | belegt: `ki-assistant/compose.yaml` Z.307, `ki/build/hermes/Dockerfile` Z.3 |
| Upstream aktuell | v0.23.0 (2026-11-05) → auf Stand | belegt: GitHub `releases/latest` |
| API-Server | Run API `:8642` nur auf `internal`, Key `hermes_api_key`; Kette Open WebUI → `owui-relay` → `hermes-models:8080` → `hermes:8642` | belegt: `ki-assistant/README.md` Z.27-43, 423-427 |
| Profile | nur `default` sichtbar unter `/opt/data` | belegt: `/opt/data/profiles` nicht vorhanden |
| Skills `creative/` | architecture-diagram, ascii-video, baoyu-infographic, claude-design, design-md, humanizer, manim-video, p5js, popular-web-designs, songwriting-and-ai-music | belegt: `ls /opt/data/skills/creative` = `ki-assistant/appdata/hermes/skills/creative` |
| Visualisierungs-Skills aus der Sammlung | `concept-diagrams`, `live-dashboard`, `mermaid-diagrams`: **nicht installiert** | belegt (ls); ob `concept-diagrams` in v0.23.0 upstream liegt: ungeprüft (API-Limit) |
| Plugins | `/opt/data/plugins` nicht vorhanden | belegt |
| MEDIA-Umgang | `architecture-diagram/SKILL.md` Z.55-57 und `references/png-export-without-libs.md` Z.3: "Open WebUI zeigt nur PNG per `MEDIA:`"; `ki-assistant/README.md` Z.390 gleich. `hermes-models/app.py` enthält kein MEDIA-Handling | belegt |
| Bild-Cache | `/opt/data/cache/images`: 0 Dateien | belegt |
| Logs | `open-webui` und `hermes-models`: 0 Treffer für `MEDIA` in den letzten 5000 Zeilen | belegt (Komodo search_logs) |

### c) Verbindung und Verhalten pro Format

- Aufruf: Open WebUI → `hermes-models` (aiohttp-Router, Image `ki-hermes-models:1.2.0-1`) → Hermes `/v1/chat/completions` bzw. `/v1/runs`. Der Router wandelt `hermes.tool.progress`/`hermes.status` in `reasoning_content` um, hängt den Marker `<!-- served-model: ... -->` an und stoppt Runs bei Client-Abbruch (belegt: `app.py` Z.594-660, 704-745; `ki/README.md` Z.62-66).
- Keine Pipe, keine Pipeline. Nur Filter (belegt).
- Mermaid-Block: Open WebUI rendert Mermaid-Codeblöcke nativ (belegt: Open-WebUI-Doku Artifacts/Chat; Live-Test nicht gemacht).
- SVG/HTML-Block: Artifacts-Panel (srcdoc-Iframe, sandboxed) (belegt: Doku; Live-Test nicht gemacht).
- PNG über `MEDIA:`: im Stream kommt der rohe Pfad an. Ursache laut Code-Analyse im Issue #94177: `_resolve_media_to_data_urls()` läuft nur in `_compute_completion`, `_compute_response`, Session-Chat; die SSE-Writer nicht (belegt: Issue-Kommentar vom 2026-08-24; PR #103239 "Fix api media streaming" offen, updated 2026-11-08). Lokaler Code steht auf v0.23.0; ob dort schon etwas gefixt ist, ungeprüft (Quelle nicht lesbar wegen API-Limit). Die lokale Skill-Doku (`architecture-diagram`) sagt faktisch, dass PNG inline ankommt → Widerspruch zum Issue; **Live-Test nötig** (ändert keinen Zustand, war aber hier nicht möglich: Terminal erreicht Hermes-API nicht).

### d) Netz

- Hermes-Egress (`ki-assistant/config/squid-hermes.conf`): `ntfy.home.lan`, `api.github.com`, `export.arxiv.org`, `pypi.org`, `hub.docker.com`, `lesezeichen.home.lan` (belegt). `files.pythonhosted.org` fehlt → kein `pip install` von Wheels (belegt durch Allowlist; Memory bestätigt 403).
- Open WebUI: `OFFLINE_MODE=true`, Proxy `squid-assistant`, `NO_PROXY` nur interne Dienste (belegt). **Folge für Visualisierung:** HTML, das Chart.js oder D3 per CDN lädt, läuft im Browser des Nutzers, nicht im Container. Ob der Browser (Steffens Netz) CDNs erreicht: Annahme ja; wenn `IFRAME_CSP` mit `connect-src 'none'` gesetzt wird, sind CDN-Skripte dennoch zu erlauben (`script-src`), sonst bricht Chart.js.
- GitHub-API anonym: 60 Anfragen/h; in diesem Lauf erschöpft (belegt: `rate_limit` remaining 0).

## 2. Quellenprüfung

| Quelle | Stand 2026-11-09 | Änderung am Kernbefund | Lizenz / Pflege / Risiko |
|---|---|---|---|
| Hermes #57431 Per-Request-Tools | offen, updated 2026-10-20 | nein | – |
| Hermes #84652 `tools: []` ignoriert | offen, P3 | nein | – |
| Hermes #94177 MEDIA roh in Open WebUI | geschlossen 2026-08-24 (`needs-repro`), **nicht gefixt**; Kommentar belegt Ursache (Streaming-Writer); 2. Kommentar: Workaround Connection "external", Chat Completions, Stream aus → Bilder inline | Option 4 wird konkreter | – |
| Hermes PR #103239 "fix(api-server): Fix api media streaming" | offen, updated 2026-11-08; fügt streaming-fähigen Media-Prozessor und Provider `base64`/`none` hinzu | Option 4 kann durch Upstream-Update gelöst werden, Zeitpunkt offen | – |
| Hermes v0.23.0 | aktuelles Release (2026-11-05) | – | – |
| Open WebUI Rich-UI-Doku (`/features/extensibility/plugin/development/rich-ui/`) | gelesen; Pipe muss Embeds selbst per `__event_emitter__` emittieren | bestätigt Option 2 | Skripte in Iframe: sandbox + `IFRAME_CSP` |
| Open WebUI Events-Doku (`embeds`) | gelesen; Strings = URL oder Roh-HTML; `replace: True` für Live-Widgets | bestätigt Option 2 | – |
| Open WebUI `IFRAME_CSP` | in `docs/reference/env-configuration.mdx` dokumentiert: CSP als `<meta>` in alle srcdoc-Iframes (Artifacts, HTML-Preview, MCP Apps); Beispiel `default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; connect-src 'none'` | – | Ob in v0.12.4 vorhanden: ungeprüft (env.py nicht lesbar, API-Limit) |
| Open WebUI v0.13.3 | neuestes Release | Update-Lücke v0.12.4 → v0.13.3 | Changelog ungeprüft |
| MartianInGreen/openwebui-hermes | MIT, 1 Stern, 1 Fork, 0 Issues | – | geringe Pflege; Code nur teilweise gelesen |
| nicolasramos/hermes-openwebui-pipeline | MIT, 0 Sterne, letzter Push 2026-08-08 | – | inaktiv |
| Skills concept-diagrams / live-dashboard / mermaid-diagrams | Inhalt in diesem Lauf nicht erneut lesbar (404/API-Limit) | – | ungeprüft |
| Neue Quellen seit 2026-11-09 | Suche (SearXNG, Brave teils gesperrt): keine neue Hermes→Open-WebUI-Embed-Pipe; neu gesehen: `uhub/awesome-agent-skills`, `0xNyk/awesome-hermes-agent`, `Gabebac/Pipe-Plugin` (Hermes-Desktop-Plugin, nicht Open WebUI), `nesquena/hermes-webui` aktiv mit Roadmap | nein | nicht geöffnet |

Sicherheitsrahmen (belegt aus Doku): Artifacts und Embeds laufen in `sandbox`-Iframes; `IFRAME_CSP` ist Zusatzschicht. "Allow Same Origin" ist Nutzereinstellung und öffnet den Iframe zum Open-WebUI-Origin → nur setzen, wenn ein Embed `sendPrompt()` o. ä. braucht.

## 3. Lücken-Analyse

| Option | IST | Lücke | Aufwand | Nutzen | Risiko | Beleg |
|---|---|---|---|---|---|---|
| 1 Mermaid/SVG/HTML im Text | Open WebUI kann es; kein Skill/SOUL-Regel nutzt es; `architecture-diagram` rät zu PNG | Format-Regeln fehlen; Skill-Doku falsch | gering | mittel-hoch | gering (sandbox) | compose, Skill Z.55-57, Doku |
| 2 Filter/Pipe → `embeds` | nichts vorhanden; `__event_emitter__` steht Filtern im `outlet`/`stream` zur Verfügung | Eigenbau nötig | mittel | hoch | mittel (HTML aus LLM im Iframe; CSP setzen) | Rich-UI-Doku, Events-Doku |
| 3 hermes-rich-ui | kein Plugin installiert; nur Desktop | Brücke wäre Eigenkonstruktion | hoch | mittel | mittel | `/opt/data/plugins` fehlt |
| 4 PNG via MEDIA | Stream-Pfad gibt Rohtext; `hermes-models` fasst Stream ohnehin an | Inlining fehlt im Stream | gering (Stream aus) / mittel (Router) | mittel | gering | #94177, PR #103239, app.py Relay |
| 5 Zweites "Darsteller"-Modell mit Inline Visualizer | Bifrost-Modelle direkt verfügbar (`bifrost.*`) | Tool-Plugin nicht installiert; Kontext nur über Chat | gering | gering-mittel | mittel (Same-Origin nötig) | README ki Z.72-77 |
| 6 Responses-Modus | Router unterstützt `/v1/responses`; OWUI nutzt Chat Completions | nur Darstellung der Tool-Calls | gering | gering | gering | app.py Z.56 |
| 7 hermes-webui | nicht installiert | Parallelbetrieb, Auth (Authelia), Familienchat-Regeln fehlen dort | hoch | hoch (Artifacts eingebaut) | hoch (zweite Oberfläche, Rechte) | Suchergebnisse |

Nebenlücken, die beim Vergleich auffielen:
- Open WebUI v0.12.4 liegt 1 Minor hinter v0.13.3 (belegt). Changelog ungeprüft.
- `architecture-diagram/SKILL.md` und `references/png-export-without-libs.md` enthalten die Aussage "Open WebUI zeigt nur PNG"; das ist für HTML/SVG-Artifacts falsch und für PNG im Stream genau umgekehrt (belegt vs. Issue). Die Skill-Doku steuert Hermes aktiv in die schlechteste Variante.
- Kein `pip`-Zugang: Skills, die matplotlib/plotly nachinstallieren wollen, scheitern (belegt Allowlist). Prüfen, was im Image schon liegt (ungeprüft).

## 4. Maßnahmen (nach Nutzen pro Aufwand)

### M1 Hermes-Skill "Format-Regeln für Open WebUI" (Entwurf unten)
- Ändern: neuer Skill unter `/opt/data/skills/creative/openwebui-visuals/SKILL.md` (Repo-Pfad `homeserver/ki-assistant/appdata/hermes/skills/creative/...`); `architecture-diagram/SKILL.md` Z.55-57 und `references/png-export-without-libs.md` Z.3 korrigieren.
- Ergebnis: Mermaid inline, SVG/HTML im Artifacts-Panel, PNG nur als Fallback.
- Voraussetzungen: keine. Abhängig von M3 für PNG.
- Abnahmetest: Chat "Zeichne den Chat-Pfad Open WebUI → hermes-models → Hermes als Diagramm": Antwort enthält einen ```mermaid-Block, Open WebUI rendert ihn (Screenshot). Zweiter Test "Balkendiagramm der Containeranzahl je Host als HTML": Artifacts-Panel öffnet sich.
- Rollback: Skill-Ordner entfernen, Skill-Doku per `git revert`.
- Entscheidung: keine; Skill-Änderung über `skill_manage` ist mein Standardweg. Der Weg ins Image/Repo läuft über den Workbench.

### M2 `IFRAME_CSP` setzen
- Ändern: `homeserver/ki/compose.yaml`, Service `open-webui`, `IFRAME_CSP: "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; img-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; connect-src 'none'"`. Deploy über Komodo (Recreate).
- Ergebnis: Artifacts/Embeds dürfen Chart.js von jsDelivr laden, aber keine Daten nach außen senden.
- Voraussetzung: Variable in v0.12.4 vorhanden (ungeprüft; sonst mit M5 kombinieren). Browser erreicht jsDelivr (Annahme).
- Abnahmetest: Artifact mit `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>` rendert ein Diagramm; Artifact mit `fetch('https://example.com')` zeigt CSP-Fehler in der Browserkonsole.
- Rollback: Zeile entfernen, Deploy.
- Entscheidung: keine (Sicherheitsschicht, kein Funktionsverlust). Hinweis: wenn Steffen CDN-frei will, muss Chart.js als Datei unter Open WebUI `static/` liegen → das wäre eine Entscheidung.

### M3 PNG im Stream: Inlining im Router `hermes-models`
- Ändern: `homeserver/ki/build/hermes-models/app.py`, Klasse `Relay`: Content-Deltas puffern, Muster `MEDIA:/<pfad>.(png|jpg|jpeg|gif|webp)` erkennen (auch über Frame-Grenzen, analog zum vorhandenen `held`), Datei aus dem geteilten Volume lesen und als `![](data:image/png;base64,...)` ausgeben. Nicht-Stream-Pfad ist durch Hermes bereits abgedeckt. Neuer Image-Tag `ki-hermes-models:1.3.0-1`, Tests in `test_app.py`.
- Voraussetzung: `hermes-models` muss `/opt/data/cache/images` (Hermes-Datenvolume) lesend sehen — heute nicht gemountet (ungeprüft, compose ki-assistant Z.237 ff. nicht vollständig gelesen) → Volume read-only hinzufügen. Erst mit **Live-Test** klären, ob v0.23.0 das Problem überhaupt noch hat: `curl` mit `stream:true` gegen `hermes-models` und Prompt "erzeuge ein 10x10 PNG und zeig es" (Workbench, read-only außer Cache-Datei).
- Alternative ohne Code: in Open WebUI, Modell `hermes-agent`, Advanced Params "Stream Chat Response" aus (Quelle: #94177-Kommentar). Nebenwirkung: Denkblöcke (`reasoning_content` aus Tool-Fortschritt) und Stop-Verhalten entfallen → nicht empfohlen als Dauerlösung.
- Ergebnis: Hermes-erzeugte PNG (matplotlib, ffmpeg-Renderer, Screenshots) erscheinen inline.
- Abnahmetest: Chat "Erzeuge ein PNG mit einem roten Quadrat und zeig es": Bild sichtbar; `docker logs hermes-models` enthält eine Zeile `media inlined ... bytes`.
- Rollback: alter Image-Tag in compose, Deploy.
- Entscheidung: keine, sofern Live-Test den Fehler bestätigt. Wenn PR #103239 vorher gemergt und Hermes aktualisiert wird, entfällt M3.

### M4 Open-WebUI-Filter "hermes_embeds" (Skizze unten)
- Ändern: neue Function `homeserver/ki/appdata/open-webui/functions/hermes_embeds_filter.py`, global aktiv, Installation wie `hermes_reasoning` (Admin-API, README ki Z.181-199).
- Ergebnis: ein Block ```viz-html ... ``` in der Hermes-Antwort wird als interaktiver Iframe inline im Chat gezeigt (Chart.js, Tabellen, Karten), der Codeblock selbst wird aus dem Text entfernt.
- Voraussetzungen: M1 (Skill lehrt Hermes den Block), M2 (CSP). Open WebUI v0.12.4 unterstützt `embeds`-Event (Doku-Stand aktuell; Version ungeprüft → im Test klären).
- Abnahmetest: Prompt "Dashboard der 3 Hosts als Chart" → Iframe sichtbar, kein Codeblock im Text, nach Reload noch sichtbar (persistiert).
- Rollback: Filter deaktivieren oder löschen (Admin, Functions).
- Entscheidung: keine. Nur HTML aus Hermes (eigener Agent) wird eingebettet; Fremdmodelle (`bifrost.*`) per `skip_prefixes` ausgenommen.

### M5 Open WebUI auf v0.13.x heben
- Ändern: `compose.yaml` Z.453 Image-Tag; Changelog 0.12.5 bis 0.13.3 lesen (ungeprüft); Filter-Tests (`tests/owui_context`, 112 Tests) vorher laufen lassen.
- Ergebnis: aktueller Artifacts/Embeds-Stand; ggf. `IFRAME_CSP`.
- Voraussetzung: DB-Backup (`webui.db`), ruhiges Fenster.
- Abnahmetest: Login über Authelia, Chat mit `hermes-agent`, Filter aktiv (Log-Zeilen `chat condense`/`context budget`), Mermaid rendert.
- Rollback: alter Tag + DB-Backup zurück.
- Entscheidung: **ja, Steffen**: Minor-Update mit DB-Migration an der zentralen Oberfläche, Familienchat betroffen. Vorschlag: nach M1-M4, nicht davor.

### M6 Fehlende offizielle Skills prüfen (`concept-diagrams`, `live-dashboard`)
- Ändern: Workbench-Checkout `NousResearch/hermes-agent` Tag v0.23.0, Pfade `skills/creative/concept-diagrams`, `skills/productivity/live-dashboard` prüfen (im Image unter `/opt/hermes/skills`?); wenn vorhanden, als optionale Skills in `/opt/data/skills` aufnehmen und mit M1 verzahnen (SVG-Ausgabe → Artifact).
- Ergebnis: fertige Diagramm-Vorlagen.
- Abnahmetest: `skills_list` zeigt sie; Testprompt liefert SVG-Artifact.
- Rollback: Ordner entfernen.
- Entscheidung: keine. Status heute: ungeprüft (API-Limit).

### M7 hermes-webui als Vergleich (nur Test-Instanz)
- Ändern: eigener Stack-Entwurf, nur hinter Authelia, nur Admin-Gruppe, read-only Zugriff auf Hermes-Daten prüfen (das Tool liest Hermes-State direkt, "coupled to Agent internals").
- Ergebnis: Vergleich Artifacts/Mermaid out of the box.
- Entscheidung: **ja, Steffen** (neue Oberfläche, Rechte, Pflege einer zweiten UI). Empfehlung unten: nicht jetzt.

## 4a. Entwurf Skill "Format-Regeln für Open WebUI" (nicht angewendet)

```markdown
---
name: openwebui-visuals
description: Use when the reader is in Open WebUI and asks for a diagram, chart, table or image. Pick the output format.
---

# Visual output for Open WebUI

Open WebUI renders three things from the answer text. Use them in this order.

## Rules

1. Flow, sequence, state, Gantt, class, ER, pie: write one ```mermaid block. Keep the syntax simple. Do not add text in the block. Test the syntax in your head: a bad block shows as code.
2. Numbers over categories or time (bar, line, scatter), dashboards, tables with sort: write one ```viz-html block (self-contained HTML, inline CSS, Chart.js from https://cdn.jsdelivr.net/npm/chart.js, no external data calls, no forms that send data). Max 60 KB. The filter in Open WebUI shows the block as an inline card. Without the filter, Open WebUI shows it as an HTML artifact.
3. Static drawings, icons, architecture pictures: write one ```svg block or one ```html block. Open WebUI opens it in the Artifacts panel.
4. Raster images (screenshots, plots from a file, photos): give the file as MEDIA:/absolute/path.png on its own line. Use this only when the image exists as a file. Do not write MEDIA: for diagrams that you can write as Mermaid or SVG.
5. One visual per answer unless the user asks for more. Put one sentence before it: what it shows. Put the data source after it.
6. Family chat: same rules; no links to internal hosts in the HTML.

## Do not

- Do not render a diagram as ASCII when Mermaid fits.
- Do not install packages (pip is blocked). Use ffmpeg or the HTML path.
- Do not put secrets, tokens or internal URLs into HTML.
```

Zusätzlich in `architecture-diagram/SKILL.md` Z.55-57 ersetzen: "Open WebUI zeigt HTML und SVG im Artifacts-Panel; PNG nur über MEDIA: und nur, wenn der Stream-Inliner aktiv ist (M3)."

## 4b. Skizze Filter "hermes_embeds" (Option 2, nicht angewendet)

- Typ: Open-WebUI **Filter** (nicht Pipe): die Connection zu `hermes-models` bleibt, Streaming und `hermes_reasoning`-Filter bleiben. Hook: `stream()` sammelt Content-Deltas; `outlet(body, __event_emitter__, __model__)` arbeitet auf der fertigen Nachricht.
- Marker: ```viz-html\n...\n``` (Fence-Sprache `viz-html`). Vorteil gegenüber `@@@VIZ-START`: ohne Filter bleibt es ein normaler Codeblock, den Open WebUI als HTML-Artifact anbietet (graceful degradation).
- Ablauf `outlet`:
  1. Modell-ID gegen `skip_prefixes` (`bifrost.`) prüfen; nur Hermes-Modelle.
  2. Letzte Assistant-Nachricht nach allen `viz-html`-Blöcken durchsuchen (Regex über Fences, max `max_embeds` = 3, max `max_bytes` = 60000 je Block).
  3. Je Block: HTML in ein Grundgerüst setzen (`<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{margin:0;font-family:system-ui}</style></head><body>…<script>new ResizeObserver(()=>parent.postMessage({type:'iframe:resize',height:document.body.scrollHeight},'*')).observe(document.body)</script></body></html>`). Kein `allow-same-origin` nötig.
  4. `await __event_emitter__({"type":"embeds","data":{"embeds":[html,...],"replace":True}})`.
  5. Block im Nachrichtentext durch eine Zeile `*(Diagramm oben eingebettet)*` ersetzen, damit der Code nicht doppelt steht. Valve `keep_code` = false.
- Fehlerfall: jede Exception → Log-Warnung, Nachricht unverändert (fail-open, wie die anderen Filter). Block zu groß → nicht einbetten, Codeblock bleibt (Artifacts-Fallback). Kein `__event_emitter__` (Task-Aufruf) → nichts tun. `metadata.task` gesetzt → nichts tun.
- Streaming-Variante (`replace: True` für Live-Dashboards) erst in Version 2.
- Tests: wie `tests/owui_context`: Fence-Parsing, Größenlimit, skip_prefixes, Idempotenz bei Re-Run des Outlets.
- Sicherheit: Iframe bleibt `sandbox` ohne same-origin; `IFRAME_CSP` (M2) beschränkt Netz; HTML stammt nur vom eigenen Agenten. Restrisiko: Prompt-Injection über gelesene Webseiten könnte HTML einschleusen → `connect-src 'none'` verhindert Datenabfluss, Formulare ohne Ziel.

## 5. Offene Lücken

- Live-Test aller vier Formate gegen die Instanz (Mermaid, SVG, HTML, PNG im Stream) fehlt. Mein Terminal erreicht die Hermes-API nicht; braucht Workbench-Task (read-only, erzeugt nur eine Cache-Datei).
- Open-WebUI-DB: aktive Functions, API-Typ der Connection, Nutzereinstellungen (Same-Origin, Artifacts) nicht gelesen.
- `IFRAME_CSP` und `embeds`-Event in **v0.12.4** nicht gegen den Quellcode geprüft (GitHub-API-Limit erschöpft).
- Upstream-Inhalt der Skills `concept-diagrams`, `live-dashboard` zum Tag v0.23.0 nicht gelesen.
- Ob `hermes-models` das Bild-Volume sieht: compose `ki-assistant` Z.237 ff. nicht vollständig gelesen.
- Changelog Open WebUI 0.12.5 bis 0.13.3 nicht gelesen.
- Keine Zugangsdaten eingesehen oder ausgegeben.

## 6. Empfehlung

Open WebUI behalten. Begründung: Auth (Authelia, Rollen `admins`/`familie`), Familienchat-Kennzeichnung im Router, vier eingespielte Filter, Cron-Delivery in Chats und der Modell-Router sind an Open WebUI gebunden (belegt). hermes-webui bringt Artifacts von Haus aus, ist aber eng an Hermes-Interna gekoppelt und bräuchte Auth, Familienregeln und Pflege einer zweiten Oberfläche (Suchergebnisse, nicht geöffnet). Der Nutzen der Visualisierung ist mit M1 bis M4 im bestehenden Stack erreichbar; M7 nur, wenn M4 scheitert.

Reihenfolge: M1 (sofort, Skill) → Live-Test der 4 Formate (Workbench, read-only) → M2 + M4 (ein Deploy) → M3 nur, wenn der Live-Test das PNG-Problem bestätigt und PR #103239 nicht gemergt ist → M6 → M5 nach Entscheidung.
Echte Entscheidungen für Steffen: M5 (Open-WebUI-Update) und M7 (zweite Oberfläche). Alles andere ist Routine.
