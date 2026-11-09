# Hermes OpenWebUI Bridge

Brücke zwischen dem **Hermes Agent** (als API-Backend und Tool-Runner) und **Open WebUI** (als Chat-Oberfläche).

## Zweck & Hintergrund

Hermes führt Tools eigenständig serverseitig aus. Open WebUI erwartet im Standard, dass Tools in der Benutzeroberfläche direkt über WebUI-Tools oder integrierte Pipes/Actions laufen. Da Hermes-Tools und offene Visualisierungen (wie `MEDIA:`-Dateipfade oder interaktive Dashboards) ohne Anpassung nicht im Open-WebUI-Stream ankommen, verbindet dieses Projekt beide Welten, ohne die Vorteile des Routers (`hermes-models`) und bestehender Filter (wie `hermes_reasoning_filter.py`) zu verlieren.

Ausführliche Analysen und Entscheidungen befinden sich im Ordner [`docs/`](docs/README.md).

## Komponenten & Status

| Komponente | Typ | Status | Beschreibung |
|---|---|---|---|
| `skills/openwebui-visuals` | Hermes Skill | **in Arbeit** | Formatanweisungen für Hermes (Mermaid, SVG, HTML-Artifacts, `MEDIA:`-Fallback) |
| `functions/hermes_embeds_filter.py` | Open WebUI Filter | **fertig (ungetestet live)** | Wandelt ```viz-html Blöcke in native Open-WebUI `embeds` (Iframe mit Chart.js / Dashboards) |
| `functions/hermes_runs_pipe.py` | Open WebUI Pipe (Manifold) | **fertig (ungetestet live)** | Anbindung von `/v1/runs`, Freigaben und Session-Header (ADR [0002](docs/decisions/0002-runs-pipe.md), Spezifikation [`docs/spec/hermes-runs-pipe.md`](docs/spec/hermes-runs-pipe.md)) |
| `router/` / Router-Integration | AIOHTTP Relay | **beobachtet** | Pufferung von SSE-Frames, Reasoning-Konvertierung, geplantes MEDIA-Inlining |

## Installation & Hinweise

- **Hermes Skill:** Kopieren nach `$HERMES_HOME/skills/creative/openwebui-visuals/` (siehe [`skills/openwebui-visuals/README.md`](skills/openwebui-visuals/SKILL.md)).
- **Open WebUI Functions:** Import über Admin-Bereich von Open WebUI (siehe [`functions/README.md`](functions/README.md)).

## Lizenz

Dieses Projekt steht unter der **GNU AGPL v3.0** (siehe [LICENSE](LICENSE)).

## Drittanbieter & Muster

Siehe [`THIRD_PARTY.md`](THIRD_PARTY.md) für genutzte Muster und Inspirationsquellen (MIT-Lizenzen).
