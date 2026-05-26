"""Phase 6.5.c — dashboard-level filter system.

A dashboard is the Phase 6.5 evolution of a Presentation: it adds a top-level
``filters: []`` array. Each filter carries a semantic_tag, type, label, and
defaults; values from these filters propagate to per-block variables via
``variable_bindings`` (auto-bound by matching semantic_tag, or hand-tuned).

This module owns:

- :mod:`presentations.dashboards.schema` — Pydantic models for Filter,
  VariableBinding, and the dashboard's ``filters`` + ``layout`` structure.
- :mod:`presentations.dashboards.binding` — resolves filter values → block
  variables, including ``constant`` overrides and date/number range
  accessors.

The on-disk dashboard format remains JSON (manifest.json) for back-compat
with Phase 5 snapshots; Phase 6.5 adds ``filters`` as an optional top-level
key. Existing dashboards without the array render exactly as before.
"""
