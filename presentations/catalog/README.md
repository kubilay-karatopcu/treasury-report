# Catalog — hand-authored knowledge documents

Single, git-versioned home for every document the **data team fills in by
hand**. Classified by type. This is the deployable source of truth (same in
DEV and prod — no "rich in dev, empty in prod" drift).

```
presentations/catalog/
├── concepts/              ← Concept Registry (Phase 7.a)
│   ├── global.yaml          bank-wide concepts (currency, time, ...)
│   └── <dept>.yaml          departmental concepts (treasury, risk, ...)
└── tables/                ← Table documentation + concept bindings (Phase 7.b)
    └── <SCHEMA>/
        └── <TABLE>.yaml     per-table columns, filter hints, concept_bindings
```

## Two document types, one directory

| Type | Lives in | Owns | Loaded by |
|---|---|---|---|
| **Concept** | `concepts/<scope>.yaml` | what a business term means + its canonical values/aliases | `concepts/registry.py` → `CONCEPT_REGISTRY` |
| **Table doc** | `tables/<SCHEMA>/<TABLE>.yaml` | which column realizes which concept, via which transform | `concepts/bindings.py` (7.b) → table-doc store |

The two halves combine in the filter compiler (7.b): the concept supplies the
canonical value alphabet, the table doc's `concept_bindings` says how that
value becomes a SQL predicate on each specific table.

## Ownership & editing

- **Concepts** — `concepts/global.yaml` is system-owned (data platform);
  `concepts/<dept>.yaml` is owned by that department's data team. Edits go
  through PR review. User-scoped concepts do NOT live here (they're stored
  per-presentation; see spec §3.4).
- **Table docs** — owned by the data team. `concept_bindings` reach the
  filter compiler ONLY when `confidence: human_verified`. Inferred bindings
  (regex/sample/llm_proposed) may sit in the YAML but are gated until an
  operator approves them in the 7.c review UI.

## Invariants

- The concept registry is always a **superset** of the Phase 6.5
  `SEMANTIC_TAGS_V0` allow-list — every legacy `semantic_tag` exists here as a
  concept, so pre-Phase-7 blocks keep validating.
- YAML 1.1 boolean tokens (`ON`/`OFF`/`YES`/`NO`) are **not** auto-coerced to
  bool by the concept loader, so codes like the maturity bucket `ON`
  (overnight) load as the string `"ON"`. Still, prefer quoting them.
- Filenames under `tables/` mirror the Oracle identifier exactly
  (`<SCHEMA>/<TABLE>.yaml`, ALL_CAPS).

## Migration note

Phase 6.5.b table docs previously lived under `examples/table_docs/` (DEV
fixtures) and S3 (prod). As Phase 7.b extends the table-doc schema with
`concept_bindings`, the canonical hand-authored docs converge here under
`tables/`. The reference fixtures in `examples/phase_7/` remain as shape
documentation only.
