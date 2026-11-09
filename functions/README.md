# Open WebUI Functions

Dieser Ordner enthält Open-WebUI-Funktionen (Filter und Pipes) zur Unterstützung des Hermes-Backends.

## Installation

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
