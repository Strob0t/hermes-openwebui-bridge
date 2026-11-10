# Documentation & Research

This directory contains the research, current-state analyses, and architectural decision records (ADRs) for the bridge between Hermes and Open WebUI.

## Contents

- **Research (`docs/research/`)**:
  - `01-source-collection.md`: Initial source collection and issue references.
  - `02-current-state-report.md`: Comprehensive current state, option comparison, and action plan (M1–M7).
  - `03-working-notes.md`: Raw notes and handoff information.

- **Architectural Decisions (`docs/decisions/`)**:
  - `0001-filter-not-pipe.md`: Decision for an Open WebUI filter instead of a pipe for embeds.
  - `0002-runs-pipe.md`: Pipe for Hermes `/v1/runs` (approvals, session headers, manifold).

- **Specifications (`docs/spec/`)**:
  - `hermes-runs-pipe.md`: Specification of the Runs-Pipe (Section 10 takes precedence over 2, 3, and 5).
