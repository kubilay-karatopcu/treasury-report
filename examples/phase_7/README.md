# Phase 7 fixtures

Concrete YAML shapes for the artifacts introduced in
[`docs/PHASE_7_SPEC.md`](../../docs/PHASE_7_SPEC.md). When the
implementation phases (11.a–11.d in spec terminology) start, these
files are the authoritative shape references — both for the loader
Pydantic models and for documentation in the binding review UI.

## Layout

```
phase_7/
├── concepts/
│   ├── global.yaml                  — bank-wide concepts (currency, time, ...)
│   └── treasury.yaml                — Treasury department concepts (maturity, branch, ...)
├── table_docs/
│   └── ODS_TREASURY/
│       ├── TRD_BRANCH_POSITION.yaml — extended doc with concept_bindings
│       └── FX_SWAP_DEALS.yaml       — two time concepts + bucket_from_range
└── compiler_golden/
    ├── currency_in_two_tables.yaml  — uniform identity transform
    ├── maturity_mixed_transforms.yaml — identity + bucket_from_range
    └── concept_blind_block.yaml     — table without a binding → blind: true
```

## How these are used per sub-phase

### 11.a — Concept Registry

- `concepts/global.yaml` + `concepts/treasury.yaml` load via the new
  `concepts/registry.py` loader.
- Migration script `concepts/migrations/0001_v0_to_v1.py` generates
  these files from `semantic_tags.SEMANTIC_TAGS_V0`. Round-trip test:
  re-emit the allow-list from the registry, assert equality with v0.

### 11.b — Column Bindings + Filter Compiler

- `table_docs/ODS_TREASURY/*.yaml` exercise every transform kind:
  - `identity` (CCY, AS_OF_DATE, MATURITY_BUCKET, VALUE_DATE, ...)
  - `lookup` (BRANCH_ID via DIM_BRANCH)
  - `bucket_from_range` (MATURITY_DAYS → maturity bucket)
  - `time_truncation` (TRADE_DATE timestamp → date)
- `compiler_golden/*.yaml` are byte-exact expected outputs. Each
  golden file declares `filter_state`, `tables_in_play`, and the
  expected `per_table_predicates`. The compiler test loads the
  registry + table_docs + golden, runs the compiler, and asserts
  the emitted SQL/params match exactly.
- Determinism property: every golden file runs N=100 times and the
  output must be byte-identical across runs.

### 11.c — Binding Inference

- Inference pipeline operates on a (column_name, dtype, sample_values)
  tuple. Fixtures for this live alongside the inference tests, not
  here. This directory only holds artifacts the compiler reads.

### 11.d — User-scoped concepts

- Out of band: user concepts live in a per-presentation JSON file, not
  in `concepts/*.yaml`. The shape mirrors a single concept entry from
  these YAMLs.

## Backward compatibility check

Loading these files with a Phase 6.5 reader (no `concept_bindings`
awareness) must NOT error. The reader is expected to ignore unknown
top-level fields. Test: load `TRD_BRANCH_POSITION.yaml` through
Phase 6.5's table-doc parser and assert no exception.
