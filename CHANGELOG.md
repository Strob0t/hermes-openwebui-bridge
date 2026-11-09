# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in dieser Datei dokumentiert.

## [Unreleased]

### Added
- Runs-Pipe 0.2.0: Modell-Discovery aus `GET /models` (`MODEL_DISCOVERY`, `MODEL_FILTER`, `OWNED_BY_FILTER`, `MODELS_CACHE_TTL`), Freigabe-Regeln per Regex (`MODEL_POLICIES`), gewählte ID als `model` im Run-Body, Statuszeile aus `ROUTE_HEADER`. Ersetzt das statische Valve `MODELS`.
- Runs-Pipe 0.1.0 (Manifold, Freigaben, Session-Header) mit Tests (`functions/hermes_runs_pipe.py`, `tests/test_hermes_runs_pipe.py`).
- ADR 0002 und Spezifikation der Runs-Pipe (`/v1/runs`, Freigaben, Session-Mapping).
- Projektgerüst, Dokumentation und Research-Dokumente (`docs/research/`, `docs/decisions/0001-filter-not-pipe.md`).
- Hermes Skill für Visualisierungs-Formatregeln (`skills/openwebui-visuals/SKILL.md`).
- Open WebUI Filter `functions/hermes_embeds_filter.py` (v0.1.0) für viz-html Embeds und vollständige Unit-Tests (`tests/test_hermes_embeds_filter.py`).
- Dokumentation für Functions und Valves (`functions/README.md`).
- Basis-Dokumente (`README.md`, `THIRD_PARTY.md`, `.gitignore`, `.editorconfig`).
