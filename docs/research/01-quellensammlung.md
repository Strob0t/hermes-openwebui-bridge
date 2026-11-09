# Hermes + Open WebUI: Visualisierung – Quellensammlung und Agent-Prompt

Stand: 2026-11-09. Quelle: Web-Recherche (SearXNG), keine Tests gegen die Live-Instanz.
Status der Aussagen: "belegt" = in Doku/Issue/Code gelesen. "Annahme" = Schluss von mir, nicht getestet.

## 1. Kernbefund

- Open WebUI ruft Hermes über POST /v1/chat/completions auf (belegt, Hermes-Doku).
- Hermes baut pro Request einen eigenen Agenten und führt seine Tools selbst aus. Zurück kommen nur Fortschrittszeilen und Endtext (belegt).
- Open-WebUI-Tools (Inline Visualizer, OpenUI, MCP App Bridge) werden von Hermes nicht aufgerufen. Hermes-Issue #57431 (offen) fordert Per-Request-Tools. Issue #84652: der API-Server ignoriert sogar `tools: []` (belegt).
- Rich-UI-Embeds in Open WebUI brauchen einen HTMLResponse aus einem Open-WebUI-Tool oder ein "embeds"-Event per `__event_emitter__` aus einer Pipe (belegt, Open-WebUI-Doku).
- Folge: Embeds für Hermes gehen nur über eine Pipe/Filter in Open WebUI, die Hermes-Text umwandelt (Annahme, noch nicht gebaut).

## 2. Optionen (nach Aufwand)

| Nr | Option | Aufwand | Gewinn | Status |
|----|--------|---------|--------|--------|
| 1 | Mermaid / SVG / HTML-Codeblöcke im Antworttext, Open WebUI rendert (Mermaid inline, HTML/SVG/JS im Artifacts-Panel). Hermes-Skill legt Format-Regeln fest. | sehr gering | mittel | belegt für Open WebUI, nicht live getestet |
| 2 | Pipe/Filter in Open WebUI: Marker im Hermes-Text (z. B. Block `viz-html`) -> `__event_emitter__` Typ "embeds" -> Inline-Iframe. Vorlage: Inline Visualizer v2 (@@@VIZ-START/@@@VIZ-END). | mittel (Eigenbau) | hoch | Annahme |
| 3 | hermes-rich-ui Plugin (rich_present, 26 Komponenten, Direktive ::richui). Rendert nur in Hermes Desktop. Brücke über `$HERMES_HOME/rich-ui/cards/` möglich. | mittel-hoch | mittel | Brücke = Konstruktion |
| 4 | Bilder: PNG per MEDIA-Tag (Issue #94177: Open WebUI zeigt Rohtext, Issue geschlossen, Fix ungeprüft). Workaround nicolasramos: statischer Server für ~/.hermes/cache/images + Markdown-Bild. | gering | mittel | belegt |
| 5 | Zweites Modell/Profil in Open WebUI mit aktivem Inline Visualizer als "Darsteller" für Hermes-Ergebnisse. | gering | gering-mittel | Annahme; Kontext nur über Chatverlauf |
| 6 | Responses-Modus ("Responses (Experimental)"): strukturierte function_call-Items, reasoning_content als Denkblock. Nur Tool-Darstellung. | gering | gering | belegt |
| 7 | Alternative Frontends: hermes-webui (nesquena, Artifacts/Mermaid/SVG/HTML eingebaut), hermes-web-ui, Hermes-Studio, hermes-ui. | Umstieg | hoch | als Vergleichskandidat |

## 3. Quellen

Anbindung Hermes <-> Open WebUI
- MartianInGreen/openwebui-hermes (MIT, Mai 2026): Gateway-Plugin (Channel-Polling alle 3 s) + Pipe Function über /v1/runs mit SSE, Slash-Befehlen, Freigabe-Dialogen. README nennt "HTML tool rendering issues". Pipe liefert Markdown-Statuszeilen. Embeds im Code nicht gesehen (Code nur zur Hälfte gelesen). Beste Basis für eine Eigenbau-Pipe, weil Function = Zugriff auf `__event_emitter__`.
- nicolasramos/hermes-openwebui-pipeline (MIT, Aug 2026): läuft im Pipelines-Server. hermes.tool.progress -> reasoning_content; Bilder aus ~/.hermes/cache/images als Markdown. Kein HTML/Embed.
- Hermes-Doku: Open-WebUI-Integration, API-Server, Responses-Modus.

Hermes-Issues
- #57431 Per-Request-Tools für API-Clients (offen)
- #84652 API-Server ignoriert `tools: []`
- #94177 MEDIA-Tag in Open WebUI als Rohtext (geschlossen)

Open WebUI
- Doku Rich UI Embedding (HTMLResponse, Content-Disposition inline, postMessage-Höhe, window.args, Tuple mit Kontext, "embeds"-Event für Pipes).
- Artifacts (HTML/SVG/JS, Versionen, Vollbild), Mermaid-Rendering.
- Env-Variable IFRAME_CSP (CSP des Artifact-Iframes); Nutzereinstellung "iframe Sandbox Allow Same Origin" (Standard aus).
- Community-Plugins: Inline Visualizer v2 (Streaming, sendPrompt(), Chart.js, Skill), OpenUI-Plugin (render_openui), MCP App Bridge.

Hermes-Skills (Bausteine)
- concept-diagrams (offiziell, optional, creative): flache SVG-Diagramme mit CSS-Klassen. Nur als Snippet gesehen.
- live-dashboard (offiziell, optional): selbstaktualisierende HTML-Dashboards per Cron; Ausgabe = Datei. SKILL.md gelesen.
- mermaid-diagrams (awknode/hermes-agent-skills, MIT; Kopie bei hosseinmahdevar): Mermaid-Syntax, Fokus PDF/CLI. SKILL.md gelesen.
- excalidraw, archify, system-atlas, tldraw-offline: nur in Liste gesehen, nicht geöffnet.
- Kataloge, nicht durchsucht: awesome-hermes-agent (Anil-matcha, 0xNyk, mergisi), ZeroPointRepo/awesome-hermes-skills, hermesatlas.com/lists/top-skills, hermes-agent-lab.com, codesstar/hermes-skill-atlas (Skills-Hub lieferte JSON-Fehler), dazfx/hermes-skills (nur Installation).

Hermes-Plugins/Dashboards
- hermes-rich-ui (rich_present, ::richui-Direktive, nur Desktop).
- hermes-web-ui (7,9k Sterne), mojomast/hermesdashboard, perejaslav/hermes-agent-dashboard (Auswertung von Hermes selbst).

Lücken der Recherche
- Kein fertiger Skill/Pipe-Code Hermes -> Open-WebUI-Embeds gefunden.
- Reddit gesperrt; Brave/DDG zeitweise offline; skillsmp und Teile von GitHub nicht lesbar.
- Nichts gegen die Live-Instanz getestet.

## 4. Agent-Prompt (zum Kopieren)

```
ROLLE
Du bist ein Analyse-Agent für meinen AI-Stack (Hermes + Open WebUI + zugehörige Dienste). Du vergleichst den IST-Zustand mit der Quellensammlung unten und leitest konkrete, priorisierte Verbesserungen ab. Du änderst nichts am System.

ZIEL
Bessere Visualisierung (Diagramme, Charts, Dashboards, Bilder) in Open WebUI bei Hermes als Backend. Nebenziel: andere Lücken im Stack, die dir beim Vergleich auffallen, kurz nennen.

REGELN
- Nur lesen. Keine Schreibaktionen, keine Neustarts, keine Deployments, keine Config-Änderungen.
- Aussagen immer mit Status: belegt (Datei/Befehlsausgabe/Quelle gelesen), Annahme, ungeprüft.
- Keine erfundenen Versionen, Pfade, Env-Variablen oder Funde. Fehlt eine Info: als Lücke melden.
- Zugangsdaten nicht ausgeben.
- Antwort auf Deutsch, direkt, ohne Kostenhinweise und ohne Rückfragen, die du selbst klären kannst.

SCHRITT 1 – IST-ZUSTAND ERHEBEN (live, nicht aus Gedächtnis)
Prüfe und notiere, jeweils mit Beleg:
a) Open WebUI: Version, Deployment (Compose/Komodo-Stack in komodo/resources.toml, Repo Strob0t/homelab), Env-Variablen zu Rendering (IFRAME_CSP, Artifacts, Same-Origin-Setting), installierte Functions/Pipes/Filter/Tools, angebundene Modelle und API-Typ (Chat Completions oder Responses).
b) Hermes: Version, API-Server-Konfiguration, Profile, installierte Skills (Visualisierungs-Skills: concept-diagrams, live-dashboard, mermaid-diagrams usw.), Plugins, Umgang mit MEDIA:-Tags und Bild-Cache.
c) Verbindung: Wie ruft Open WebUI Hermes auf? Gibt es schon eine Pipe/Pipeline? Was passiert in einem Test mit Mermaid-Block, SVG-Block, HTML-Block und PNG? (Nur Lesen: Logs und Konfiguration prüfen; Live-Test nur, wenn er keinen Zustand ändert.)
d) Netz: Welche Hosts darf der Agent erreichen (Squid-Allowlist)? Was blockiert Downloads (z. B. files.pythonhosted.org)?

SCHRITT 2 – QUELLEN PRÜFEN
Nimm die Quellenliste aus Abschnitt 3 der Sammlung. Für jede Option 1–7:
- Prüfe die Quelle erneut auf Aktualität (neue Commits, Releases, Issue-Status, z. B. #94177, #57431, #84652). Ändert sich der Kernbefund, etwa wenn Hermes inzwischen Per-Request-Tools unterstützt?
- Suche zusätzlich nach neuen Quellen seit 2026-11-09 (Hermes-Skills für Charts/Plotly/HTML, Open-WebUI-Pipes für Hermes mit Embeds, awesome-Listen).
- Prüfe Lizenz, Pflegezustand, Sicherheitsrisiko (Skripte in Iframes, CSP, Same-Origin).

SCHRITT 3 – LÜCKEN-ANALYSE
Tabelle: Option | IST-Zustand | Lücke | Aufwand (gering/mittel/hoch) | Nutzen | Risiko | Beleg.

SCHRITT 4 – VERBESSERUNGEN KRISTALLISIEREN
Liefere maximal 7 Maßnahmen, sortiert nach Nutzen pro Aufwand. Für jede:
- Was genau ändern (Datei/Dienst/Setting, soweit belegt).
- Erwartetes Ergebnis in Open WebUI (z. B. "Mermaid inline", "Chart als Embed").
- Voraussetzungen und Abhängigkeiten.
- Wie man es prüft (Abnahmetest, konkret).
- Rollback.
- Welche Maßnahme ohne Entscheidung von mir umgesetzt werden kann und welche Entscheidung brauche ich von mir (nur echte Entscheidungen).
Gib zusätzlich einen Entwurf für einen Hermes-Skill "Format-Regeln für Open WebUI" (wann Mermaid, SVG, HTML/Chart.js, PNG) und eine Skizze der Pipe aus Option 2 (Marker, Event, Fehlerfall), jeweils als Entwurf, nicht angewendet.

SCHRITT 5 – ABSCHLUSSBERICHT
- 5 Zeilen Zusammenfassung.
- Die Maßnahmenliste aus Schritt 4.
- Offene Lücken und was nicht geprüft werden konnte.
- Empfehlung: Open WebUI behalten oder hermes-webui als Vergleich testen – mit Begründung aus Schritt 1–3.
```

## 5. Hinweise zur Nutzung des Prompts
- Der Agent soll auf dem Workbench laufen (Repo und Hosts sind dort erreichbar), nicht im Terminal von Hermes (/opt/data only).
- Ergebnis in `/opt/data/home/work/research-additions/` ablegen und in PROGRESS.md eintragen.
