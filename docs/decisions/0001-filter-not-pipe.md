# ADR 0001: Open-WebUI-Filter statt Pipe für visuelle Embeds

- **Status:** Akzeptiert (2026-11-09)
- **Kontext:** Für interaktive HTML/Chart.js-Visualisierungen aus Hermes-Antworten musste gewählt werden, ob Open WebUI über eine Pipe oder einen Filter angebunden wird.
- **Entscheidung:** Es wird ein Open-WebUI **Filter** (`hermes_embeds_filter.py`) statt einer Pipe verwendet.
- **Begründung:**
  1. Die bestehende Verbindung über den Router `hermes-models` (inklusive SSE-Streaming und `hermes_reasoning_filter.py`) bleibt vollständig erhalten.
  2. Ein Filter greift im `outlet`-Hook auf die fertige Nachricht zu und emittiert `embeds`-Events per `__event_emitter__`, während nicht unterstützte Clients oder Fehlersituationen durch den ```viz-html Codeblock ein automatisches Artifact-Fallback bieten (fail-open).
- **Konsequenzen:** Keine Änderung am Upstream-Modellrouting; nahtlose Integration neben den bestehenden Filtern.
