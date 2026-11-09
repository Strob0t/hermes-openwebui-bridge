# Open WebUI Functions

Dieser Ordner enthält Open-WebUI-Funktionen (Filter und Pipes) zur Unterstützung des Hermes-Backends.

## Installation

1. In Open WebUI als Administrator anmelden (`Admin > Functions`).
2. Auf **+ (Import)** klicken oder den Code direkt hochladen/einfügen.
3. Alternativ über die Open-WebUI Admin API.

## Geplante Funktionen

- **`hermes_embeds_filter.py`** (Teil 2): Filter, der ```viz-html Blöcke in interaktive Open-WebUI `embeds` (Iframes mit Chart.js) umwandelt.
- **`hermes_runs_pipe.py`** (Teil 3): Pipe für `/v1/runs`, Freigaben und Session-Mapping.

## Valves (Konfiguration)

Valves und deren Parameter werden ab Teil 2 (Implementierung des Filters) dokumentiert.
