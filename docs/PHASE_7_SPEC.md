# Phase 7 — Concept Foundation

**Status:** Spec draft

**Owner:** Kubilay Karatopçu

**Depends on:** Phases 1–6.5 (variables + semantic_tag + extended table docs in production)

**Forward-compat target:** Phases 8 (Hazırlık scope contract), 9 (Stage 1 catalog), 10 (block marketplace), 13+ (process layer).

**Language note:** Spec is in English for Claude Code consistency. Inline comments and user-facing strings may be Turkish where called out.

---

## 1. Context

### 1.1 Why this phase exists

Phase 6.5 ships a manual variable system: each user names their own variables, picks their own values, and tags them with a flat `semantic_tag` drawn from a fixed allow-list. This works at small scale but breaks the moment two different tables represent the same business concept with different column names, different value alphabets, or different physical types. Treasury data has many such cases:

- `MATURITY_DAYS = 45` (integer days) in one table and `MATURITY_LABEL = "1M"` (bucket string) in another both refer to the same maturity bucket.
- `CCY = "USD"` in trading tables and `CURRENCY_NAME = "US Dollar"` in customer-facing tables both mean the same currency.
- `AS_OF_DATE` in position tables, `TRADE_DATE` in deal tables, `VALUE_DATE` in settlement tables — three distinct *time* columns whose business meaning is also distinct.

Without a unifying layer, **cross-table filters are impossible**: a dashboard with blocks pulling from three tables cannot honour a single "currency = USD" filter if the three tables encode currency differently. Phase 6.5's `semantic_tag` was a placeholder for this — it lets the user *say* a variable means `currency`, but the platform has no machinery to translate that tag into per-table SQL.

Phase 7 introduces that machinery.

### 1.2 What this phase delivers

A **concept-aware filter layer** sitting between the user (or LLM) and the underlying tables. The user expresses filters in business terms (`currency in [USD, EUR]`); the platform compiles those filter expressions into per-table SQL that respects each table's actual columns and value encodings.

Four moving parts:

1. **Concept Registry** — YAML-hierarchical vocabulary of business concepts (currency, maturity, branch, etc.) with canonical value alphabets, transformation rules, and ownership scopes.
2. **Column Bindings** — per-table declarations of how columns map to concepts (identity / map / lookup / bucket-from-range).
3. **Filter Compiler** — deterministic compiler from concept-level filter expressions to per-table SQL with parameterized binds.
4. **Binding Inference** — semi-automated pipeline that proposes column→concept bindings for new tables (regex + dtype + sample-value pattern + LLM fallback), with mandatory human review before promotion.

### 1.3 What changes for users

- **Dashboard filters become concept-aware.** A "currency" filter widget understands what currency means and which tables can answer it. Tables that don't bind to currency render their blocks with a "filter not applied here" badge — the chart still renders, just on un-filtered data.
- **Block SQL stays raw.** Block queries written in Phase 6.5 are not rewritten. The compiler is *additive* — it adds extra `WHERE ... AND ...` clauses derived from concept-level filters on top of the block's own query.
- **LLM stops writing concept SQL.** When the LLM creates filters or proposes value sets, it emits concept-level JSON. The compiler generates the SQL. The LLM is no longer in the position of getting column names wrong.
- **A "concept blind" badge** appears on charts whose source table doesn't bind to one of the active filters. Today these would silently ignore the filter; tomorrow they're visibly flagged.

### 1.4 What does NOT change

- **Phase 6.5 blocks and dashboards remain valid.** Migration is mechanical: `semantic_tag` field on every variable maps directly to a concept reference.
- **Existing raw SQL queries are not rewritten.** A block's `query` field is preserved untouched. The compiler appends predicates; it does not parse the user-authored SQL.
- **Pre-Phase-6.5 blocks (no variables) keep working.** They're just concept-blind by definition.
- **DuckDB session and block cache.** Unchanged. The cache key still uses resolved variables; concept compilation happens before resolution.

### 1.5 Out of scope (deferred)

- Concept-aware *aggregation* (e.g. "group by maturity bucket" using the canonical concept bucket even on a table that only has `MATURITY_DAYS`). Phase 8's scope contract may revisit this.
- Cross-concept derived values (e.g. "amount in EUR" computed from `AMOUNT_TRY` × FX rate). Out of scope; users join an FX table manually.
- Concept versioning at runtime (concepts are versioned but the runtime always uses the latest accepted version). Backward-compat for old concept versions is backlog.
- User-defined concepts that override global concepts. Extension only — see §3.4.
- Full SQL parser. The compiler walks a structured filter expression, not user SQL. (Phase 6.5's `sqlparse`-based whitelist stays for block-save validation.)

### 1.6 Relationship to existing modules

The current app has:

- `presentations/variables/semantic_tags.py` — the flat allow-list (frozen v0 set).
- `presentations/blocks/schema.py` — Pydantic schemas for blocks + variables.
- `presentations/sql/validator.py` + `sql/binder.py` — SQL whitelist + bind expansion.
- `presentations/variables/resolver.py` — variable resolution with overrides.
- `presentations/cache/block_cache.py` — DuckDB block cache + LRU eviction.
- `presentations/dashboards/binding.py` — Phase 6.5.c filter→variable binding resolver.

Phase 7 **extends** these:

- A new `presentations/concepts/` module: registry loader, value transformer, filter compiler.
- `semantic_tags.py` becomes a thin wrapper around the registry's tag set (allow-list emits from concepts).
- `dashboards/binding.py` learns to delegate per-table SQL generation to the filter compiler.
- A new `presentations/catalog/bindings/` module: per-table column-binding YAML loaders + inference pipeline.

---

## 2. Three durable abstractions revisited

The roadmap (§1.1) introduces three durable abstractions: Concept, Column, Variable. Phase 7 finally separates them in code:

```
Concept              YAML — "what filter means in business terms"
   ↑ binds to
ColumnBinding        YAML — "where that meaning lives in table T"
   ↑ resolved by
ResolvedFilter       runtime — "the actual SQL predicate for table T"
```

Variables (Phase 6.5) and Filters (Phase 6.5.c) sit *above* concepts:

```
Dashboard Filter     "currency in [USD, EUR]"
   ↓ binds to block variable via semantic_tag → concept ref
Block Variable       :currency_list
   ↓ compiles via column binding
Per-table SQL        "AND CCY IN ('USD', 'EUR')"   in table A
                     "AND CURRENCY_NAME IN ('US Dollar', 'Euro')"   in table B
```

Every Phase 6.5 variable becomes the input to a concept-aware compilation step.

---

## 3. Data Model

### 3.1 Concept Registry

Concepts live in versioned YAML files. Path: `s3://<bucket>/concepts/<scope>.yaml`.

Three scopes:

- **Global** — `s3://<bucket>/concepts/global.yaml`. Bank-wide concepts: `currency`, `as_of_time`, `counterparty`. **System-owned**, never user-editable.
- **Departmental** — `s3://<bucket>/concepts/<dept>.yaml`. Per-department concepts: `maturity` (Treasury), `pd_band` (Risk), `mevduat_segment` (Bilanço). **Data team-owned**, edited via PR.
- **User-scoped** — stored in DB per-presentation as JSON. **User-owned**, extension-only (cannot redefine global/departmental concepts; can only add new ones for one-off analyses). Promotion to departmental is via review.

Concept YAML schema:

```yaml
concept:
  id: currency                    # globally unique slug
  name: "Para Birimi"             # human label, Turkish for UI
  scope: global                   # global | dept:<name> | user (this file's scope)
  version: 3                      # bumped on every accepted change
  description: |
    Para birimi (ISO 4217 canonical). FX işlemlerinde işlemin
    para birimi; pozisyon raporlarında pozisyonun para birimi.
  type: enum                      # enum | time | bucket | scalar
  canonical_values:               # for enum/bucket types
    - code: TRY
      label: "Türk Lirası"
      aliases: [TL, TRL]          # synonyms accepted on import; canonical on output
    - code: USD
      label: "US Doları"
      aliases: ["US Dollar", "American Dollar"]
    - code: EUR
      label: "Euro"
      aliases: ["EUR Currency"]
  owners:
    department: treasury_data
    contact: data.team@qnbfb.com
  related_concepts: [counterparty]  # informational, used by Stage 1 LLM
```

For `type: time`:

```yaml
concept:
  id: as_of_time
  type: time
  granularity_default: day        # day | hour | minute
  reference_anchor: as_of_date    # which Oracle column convention this represents
  description: "Snapshot zamanı — pozisyon raporlama günü sonu."
```

For `type: bucket`:

```yaml
concept:
  id: maturity
  type: bucket
  canonical_values:
    - code: ON   # overnight
      label: "Overnight"
      day_range: [0, 1]           # inclusive low, exclusive high; used by binding inference
    - code: "1W"
      label: "1 Hafta"
      day_range: [1, 8]
    - code: "1M"
      label: "1 Ay"
      day_range: [8, 32]
    # ...
```

### 3.2 Column Bindings

A column binding declares **how a column in table T realizes concept C**. Stored per-table at `s3://<bucket>/table_docs/<schema>/<table>.yaml` (same path as Phase 6.5.b's table docs — extended schema, additive).

Example for `TRD_BRANCH_POSITION`:

```yaml
table: TRD_BRANCH_POSITION
schema: ODS_TREASURY
# … Phase 6.5 fields (description, partition_column, etc.) preserved …

primary_time_concept: as_of_time   # Phase 7 NEW — see §3.5

concept_bindings:                  # Phase 7 NEW — array of bindings
  - concept: as_of_time
    column: AS_OF_DATE
    transform:
      kind: identity               # column already canonical
    confidence: human_verified

  - concept: currency
    column: CCY
    transform:
      kind: identity
    confidence: human_verified

  - concept: maturity
    column: MATURITY_BUCKET
    transform:
      kind: map                    # inline mapping, value-by-value
      pairs:
        "OVERNIGHT": ON
        "1WEEK":      "1W"
        "1MONTH":     "1M"
        # ...
    confidence: human_verified

  - concept: branch
    column: BRANCH_ID
    transform:
      kind: lookup                 # value via join to dim table
      dim_table: DIM_BRANCH
      dim_key: BRANCH_ID           # column in dim_table that matches our column's value
      dim_canonical: BRANCH_CODE   # column in dim_table whose value is the canonical concept value
    confidence: human_verified
```

A `bucket-from-range` example (column is integer days, concept is maturity bucket):

```yaml
  - concept: maturity
    column: MATURITY_DAYS
    transform:
      kind: bucket_from_range
      ranges_concept: maturity     # references the concept's day_range arrays
    confidence: human_verified
```

**Transform kinds** (the only ones supported in v0):

| kind                 | semantics |
|---|---|
| `identity`           | Column value is already canonical. Filter compiles to `<col> = <val>` or `<col> IN (...)`. |
| `map`                | Column value goes through a per-binding inline lookup table. Compiles to `<col> IN (<mapped_values>)`. |
| `lookup`             | Column value is a foreign key to a dimension table; canonical concept value lives in another column of the dim table. Compiles to a sub-query: `<col> IN (SELECT dim_key FROM dim_table WHERE dim_canonical IN (<vals>))`. |
| `bucket_from_range`  | Column is numeric, concept is a bucket. Compiles to a chained `BETWEEN` per selected bucket. |
| `time_truncation`    | Column is `TIMESTAMP`, concept is `time` with `granularity: day`. Compiles to `TRUNC(<col>) BETWEEN :from AND :to`. |

**Confidence field** (`human_verified` | `llm_proposed` | `inferred_regex` | `inferred_dtype`) controls whether the binding is usable in production. Only `human_verified` bindings are used by the filter compiler in v0 — others are flagged in the review UI but never compiled to SQL.

### 3.3 ResolvedFilter (runtime, not persisted)

When the user clicks "Güncelle" on the dashboard filter bar:

```python
@dataclass
class ResolvedFilter:
    concept: str                  # "currency"
    operator: Literal["in", "between", "eq"]
    values: list[Any]             # canonical concept values: ["TRY", "USD"]
    granularity: str | None       # for time concepts only
```

The filter compiler walks all blocks in the dashboard, finds each block's underlying tables, and for each table either:

- Emits a SQL predicate via the column binding, OR
- Marks the block "concept-blind for this filter" (table has no binding for this concept) and emits nothing.

Output: a per-block list of additional `AND <predicate>` clauses with their own bind parameters, appended to the block's original SQL at execution time.

### 3.4 User-scoped concepts

User concepts live in `s3://<bucket>/presentations/<user>/<pid>/concepts.json` (or in the per-presentation Oracle row if we go DB). They are **strictly additive**:

- A user concept's `id` must not collide with any global or departmental concept.
- A user concept cannot redefine `canonical_values` of an existing concept.
- A user concept inherits the scope rules of departmental: only the presentation it lives in sees it.

**Promotion path:** `Promote to departmental` button → opens a PR-like review queue (Phase 11 builds the queue UI; Phase 7 records the promotion intent and stores it in a Pending Promotions list).

### 3.5 Primary time concept

Many tables have multiple time-typed columns. The `primary_time_concept` field on each table doc declares which one a generic "last 30 days" filter targets.

Example: `FX_SWAP_DEALS` has both `TRADE_DATE` (bound to `trade_time`) and `VALUE_DATE` (bound to `value_time`). With `primary_time_concept: trade_time`, a dashboard filter "last 30 days" sent without specifying a concept resolves to `TRADE_DATE BETWEEN ... AND ...`.

Explicit concept filters always win: a filter explicitly typed as `value_time in [last 30 days]` resolves to `VALUE_DATE` regardless of the primary.

---

## 4. Filter Compiler

The compiler is **pure**, **deterministic**, and **side-effect-free**. Given:

```
inputs:
  - dashboard_filters: list[ResolvedFilter]
  - tables_in_play: list[(schema, table)]
  - concept_registry (loaded at request time)
  - column_bindings (loaded at request time)

outputs:
  - per_table_predicates: dict[(schema, table), list[CompiledPredicate]]
    where CompiledPredicate = {
      sql: str,                # e.g. "CCY IN (:f0_currency_0, :f0_currency_1)"
      params: dict[str, Any],
      filter_id: str,          # back-reference for telemetry
      blind: bool,             # True if no binding exists → no predicate
    }
```

The compiler **never concatenates values into SQL**. All values become positional bind params (mirroring Phase 6.5's `enum_multi` expansion). The bind name prefix carries the filter id so multiple filters of the same concept don't collide.

### 4.1 Compilation rules per transform kind

```
identity:           CCY IN (:f0_currency_0, :f0_currency_1, ...)
map:                CCY IN (:f0_currency_0, ...)   ← canonical → table value resolved first
lookup:             BRANCH_ID IN (
                       SELECT BRANCH_ID
                       FROM DIM_BRANCH
                       WHERE BRANCH_CODE IN (:f0_branch_0, ...)
                    )
bucket_from_range:  ( (MATURITY_DAYS >= :f0_maturity_0_lo AND MATURITY_DAYS < :f0_maturity_0_hi)
                       OR (MATURITY_DAYS >= :f0_maturity_1_lo AND ...) )
time_truncation:    TRUNC(VALUE_DATE) BETWEEN :f0_value_time_from AND :f0_value_time_to
```

### 4.2 Determinism guarantees

For a fixed `(concept_registry, column_bindings, filter_state)` triple, the compiler emits byte-identical SQL and bind-param dicts. This is critical for:

- **Block cache keys.** The compiled SQL becomes part of the cache lookup; non-determinism would break the cache.
- **Testability.** Snapshot tests compare emitted SQL strings.
- **LLM debuggability.** When the LLM produces a filter and the user asks "why this result?", we can trace exactly which predicate ran on which table.

### 4.3 Empty / null handling

- A filter resolving to an empty `values` list short-circuits the block (same behaviour as Phase 6.5.c's `EmptySelectionError`).
- A `NULL` in the column never matches an `IN` predicate — that's standard SQL semantics, and we don't mask it. If a user wants nulls included, they need an explicit `or is_null: true` flag (deferred to backlog).

### 4.4 Concept-blind blocks

When a block's underlying table has no binding for a filter's concept, the compiler emits no predicate for that (block, filter) pair. The block's `status` in the response gains a `blind_filters: ["currency", "maturity"]` field, surfaced in the UI as a small badge on the block card: "Filtre uygulanmadı — bu tablo `currency`'i bilmiyor." The chart still renders, on un-filtered data.

---

## 5. Binding Inference Pipeline

Onboarding a new table to the concept system is the biggest single workflow Phase 7 introduces. The pipeline is **hybrid**: cheap deterministic checks first, LLM fallback only when needed, and **always human review before promotion**.

### 5.1 Pipeline stages

```
1. Column ingest        → list of (column_name, dtype, sample_values[])
2. Regex matcher        → exact name matches against known concept aliases
3. Dtype filter         → eliminate impossible concept matches (e.g. NUMBER → not currency)
4. Sample-value pattern → distinct sample matches against concept canonical_values + aliases
5. LLM fallback         → only for columns with no high-confidence match after stages 1–4
6. Review UI            → data team operator approves / corrects / rejects per column
7. Promotion            → approved bindings written to table_docs YAML with confidence: human_verified
```

Each stage outputs a `BindingProposal` with `confidence`:

| stage                   | confidence emitted    | usable by compiler? |
|---|---|---|
| regex match             | `inferred_regex`      | No                  |
| dtype-only              | `inferred_dtype`      | No                  |
| sample-value match      | `inferred_sample`     | No                  |
| LLM proposal            | `llm_proposed`        | No                  |
| operator-approved       | `human_verified`      | **Yes**             |

The compiler **only consumes `human_verified`** bindings. Inferred bindings live in the YAML alongside but are ignored at runtime. This guarantees no auto-generated mapping ever silently goes to production.

### 5.2 Regex matcher

Hard-coded patterns per concept, drawn from observed column naming in Treasury tables. Examples:

```python
REGEX_HINTS = {
    "currency":   [r"^CCY$", r"^CURRENCY$", r"^CUR_CODE$", r".*_CCY$"],
    "branch":     [r"^BRANCH_ID$", r"^BRANCH_CODE$", r"^BR_ID$", r"^SUBE_KODU$"],
    "as_of_time": [r"^AS_OF_DATE$", r"^SNAPSHOT_DATE$", r"^REPORT_DATE$"],
    "trade_time": [r"^TRADE_DATE$", r"^DEAL_DATE$"],
    "settle_time":[r"^SETTLE_DATE$", r"^SETTLEMENT_DATE$"],
    "value_time": [r"^VALUE_DATE$", r"^VALOR$"],
    "maturity":   [r"^MATURITY_BUCKET$", r"^MATURITY_DAYS$", r"^VADE_GRUBU$"],
    "counterparty":[r"^COUNTERPARTY_ID$", r"^CP_ID$", r"^KARSI_TARAF_ID$"],
}
```

A regex match alone is insufficient to bind in production — it's just a *hint* surfaced to the operator with high confidence.

### 5.3 Dtype filter

Eliminates impossible matches:

- `currency` → only `CHAR` / `VARCHAR` columns ≤ 8 chars.
- `as_of_time`, `trade_time`, etc. → only `DATE` / `TIMESTAMP`.
- `maturity` with transform `bucket_from_range` → only `NUMBER`.
- `maturity` with transform `identity`/`map` → only `VARCHAR` / `CHAR`.

### 5.4 Sample-value pattern

The pipeline samples ~50 distinct values per column (reusing Phase 6.5.b's `distinct_values_sample` cron). For each candidate concept:

- Compute `Σ(samples that appear in concept.canonical_values + their aliases) / total_samples`.
- If ratio ≥ 0.8: emit `inferred_sample` with auto-detected transform kind (`identity` if no aliases needed, `map` if aliases present).
- If 0.4 ≤ ratio < 0.8: emit `inferred_sample` with lower confidence + LLM is asked to reconcile.
- If ratio < 0.4: no proposal.

### 5.5 LLM fallback

Triggered for columns where regex+dtype+sample produced no proposal AND the operator hasn't already verified. Prompt template lives at `presentations/concepts/prompts/binding_proposal.txt`. The LLM gets:

- Column name, dtype, 50 sample values.
- All concepts in scope (global + relevant departmental).
- For each candidate concept: its `canonical_values` + `aliases` + `description`.

The LLM emits structured JSON:

```json
{
  "proposals": [
    {
      "concept": "currency",
      "transform": {"kind": "map", "pairs": {"US Dollar": "USD", ...}},
      "confidence": 0.7,
      "rationale": "Sample values are full currency names; mapping to ISO 4217 covers all observed."
    }
  ]
}
```

LLM output is **never** auto-promoted. It enters the review queue with `confidence: llm_proposed`.

### 5.6 Review UI

A simple page at `/concepts/review`. For each unverified column:

- Show column meta (name, dtype, sample values).
- Show every proposal (regex / sample / LLM) ranked by confidence.
- Operator clicks: Approve (writes to YAML with `human_verified`), Reject (no-op), Edit (opens a binding editor for manual transform definition).
- Bulk approve "all identity-confident currency columns across schema X" supported via row multi-select.

PR-style approval is intentionally **avoided** in v0: writes go directly to the YAML file (via a generated PR or direct S3 put, decided per deployment). Operator audit is via git history of the YAML files.

---

## 6. Migration from Phase 6.5

### 6.1 Migration script outline

A one-time script `presentations/concepts/migrations/0001_v0_to_v1.py`:

1. Read `SEMANTIC_TAGS_V0` allow-list from `semantic_tags.py`.
2. Emit `concepts/global.yaml` + `concepts/treasury.yaml` skeletons.
   - Each tag becomes a concept with `type: enum`, empty `canonical_values` (to be filled by data team).
   - Description copied from `_DESCRIPTIONS_TR`.
3. For every saved block (S3 walk over `blocks/<team>/<id>/<version>.yaml`):
   - For each variable: add a `concept_ref: <semantic_tag>` field (alongside the existing `semantic_tag`, kept for backward compat).
   - `allowed_values` cross-checked against the concept's canonical_values (only meaningful after the data team fills them in — this becomes a follow-up batch job).
4. For every saved dashboard:
   - For each `filter` in `filters[]`: ensure the filter's `semantic_tag` references a concept; emit a deprecation warning if the tag has no concept yet.
5. For each table doc in `table_docs/`:
   - For each column with `suggested_semantic_tag`: emit a candidate `concept_binding` with `transform: {kind: identity}` and `confidence: llm_proposed`.
   - These enter the review queue, not production.

The script is **idempotent**: running it twice produces the same output. It is **non-destructive**: existing fields are preserved; only new fields are added.

### 6.2 Backward compatibility

- Blocks without `concept_ref` on variables (i.e. all pre-Phase-7 blocks) continue to work. Their `semantic_tag` is read as the concept ref if no explicit `concept_ref` is present.
- Dashboards without concept-aware filters render filters using Phase 6.5.c's existing path (auto-binding by semantic_tag).
- Tables without `concept_bindings` are concept-blind to all filters — their blocks render with the "filter not applied" badge.

### 6.3 Phase 7 does NOT require

- The data team to have filled in `canonical_values` for every concept on day 1. The compiler degrades gracefully: a concept with no canonical_values accepts whatever the user types (Phase 6.5 behaviour).
- Every table to have `concept_bindings`. Unbound tables are concept-blind, which is no worse than Phase 6.5.
- Rewriting any existing SQL queries.

---

## 7. New module layout

**Code** lives in the `concepts/` package; **hand-authored data** (concept
registry + table docs) lives together under `catalog/`. This split keeps the
data team's editable YAMLs in one classified directory, separate from Python.

```
presentations/
├── concepts/                         (CODE — Phase 7 NEW)
│   ├── __init__.py
│   ├── registry.py                   # load + cache concept YAMLs (7.a ✅)
│   ├── schema.py                     # Pydantic: Concept, CanonicalValue (7.a ✅)
│   │                                 #   + ColumnBinding, Transform (7.b)
│   ├── bindings.py                   # load + cache per-table concept_bindings (7.b)
│   ├── compiler.py                   # filter compiler — pure, deterministic (7.b)
│   ├── inference/                    # (7.c)
│   │   ├── pipeline.py
│   │   ├── regex_matcher.py
│   │   ├── dtype_filter.py
│   │   ├── sample_matcher.py
│   │   └── llm_proposer.py
│   ├── prompts/
│   │   └── binding_proposal.txt      # (7.c)
│   ├── migrations/
│   │   └── 0001_v0_to_v1.py          # (7.a ✅)
│   └── tests/
│       ├── test_schema.py            # (7.a ✅)
│       ├── test_registry.py          # (7.a ✅)
│       ├── test_migration.py         # (7.a ✅)
│       ├── test_api.py               # (7.a ✅)
│       ├── test_bindings.py          # (7.b)
│       ├── test_compiler.py          # (7.b)
│       └── test_inference.py         # (7.c)
├── catalog/                          (DATA — Phase 7 NEW; hand-authored YAML)
│   ├── README.md
│   ├── concepts/                     # the concept registry (7.a ✅)
│   │   ├── global.yaml
│   │   └── <dept>.yaml               # treasury.yaml, risk.yaml, ...
│   └── tables/                       # table docs + concept_bindings (7.b)
│       └── <SCHEMA>/<TABLE>.yaml
└── routes_concepts.py                (CODE — Phase 7 NEW)
    # /concepts/api/list              (7.a ✅)
    # /concepts/api/<id>              (7.a ✅)
    # /concepts/review              (HTML page)        (7.c)
    # /concepts/review/api/queue                       (7.c)
    # /concepts/review/api/approve  (POST)             (7.c)
```

> **Layout decision (locked):** the binding-loader code lives in
> `concepts/bindings.py` (not a separate `catalog/` *code* package — that name
> is reserved for the **data** directory above). All hand-authored docs
> (concepts + table docs) sit together under `presentations/catalog/`, loaded
> in both DEV and prod from the same git-tracked path.

`semantic_tags.py` shrinks to a thin shim:

```python
def all_tags() -> list[dict[str, str]]:
    return [{"tag": c.id, "label": c.name, "description": c.description}
            for c in registry.all_concepts()]
```

---

## 8. API surface additions

| Method | Path                                       | Purpose                                              |
|---|---|---|
| GET    | `/concepts/api/list`                       | All concepts in scope (global + dept + user) (JSON)  |
| GET    | `/concepts/api/<id>`                       | One concept's full definition (JSON)                 |
| GET    | `/concepts/review`                         | Binding review UI (HTML)                             |
| GET    | `/concepts/review/api/queue`               | Pending bindings, all confidence < `human_verified`  |
| POST   | `/concepts/review/api/approve`             | Approve N proposals → write to YAML                  |
| POST   | `/concepts/review/api/reject`              | Mark proposal rejected                               |
| POST   | `/concepts/inference/run`                  | Trigger inference for one or more tables             |
| POST   | `/<pid>/apply-filters`                     | **EXTENDED** — filter_state now flows through compiler before per-block resolution |

The `/<pid>/apply-filters` change is the only behavioural change to existing endpoints. The response shape gains:

```json
{
  "ok": true,
  "blocks": [
    {
      "id": "...",
      "status": "refetched",
      "blind_filters": ["currency"],         // ← NEW
      "applied_predicates": [                 // ← NEW (for debugging)
        {"filter_id": "f0", "concept": "as_of_time", "sql": "TRUNC(VALUE_DATE) BETWEEN ..."}
      ]
    }
  ]
}
```

---

## 9. Forward-compat with later phases

### 9.1 Phase 8 — Hazırlık scope contract

The scope contract (`scope.yaml`) introduced in Phase 8 wraps the same filter expression syntax but separates "pinned" from "interactive" filters. The compiler is the same — it just receives pinned + interactive in one expression set.

Migration: a Phase 6.5.c `filters[]` array becomes `interactive_filters: [...]` in the scope contract; `pinned_filters: []` starts empty.

### 9.2 Phase 9 — Stage 1 Keşif

The Library/Catalog page consumes the concept registry directly:

- Table cards show "binds to: as_of_time, currency, maturity" — read from `concept_bindings`.
- Search "I want eurobond positions" → LLM reasons over concepts (instrument_type, maturity, currency) and proposes tables.
- The chat's vocabulary is bounded to concepts; this prevents the LLM from inventing column names.

### 9.3 Phase 10 — Block Marketplace

Block templates declare `requires.concepts: [...]` instead of `requires.columns: [...]`. At import time, the target dashboard's scope contract is checked against the template's required concepts: if the dashboard binds to all of them, import succeeds with auto-binding; otherwise, the import flow surfaces what's missing.

### 9.4 Phase 13+ — Process layer

A process YAML declares `concepts: [as_of_time, currency, branch]` — the concepts the process operates on. This becomes the canonical signature for clustering similar processes in Phase 14 (snapshots that touch the same concept set are grouped).

---

## 10. Locked decisions (do NOT reopen mid-implementation)

1. **Concept storage scope split:** Global + departmental in YAML (git-versioned). User-scoped in DB. **No exceptions.** YAML edits go through PR; DB edits are per-presentation.
2. **Transform kinds frozen:** `identity`, `map`, `lookup`, `bucket_from_range`, `time_truncation`. New kinds require spec amendment.
3. **Compiler determinism:** byte-identical output for identical inputs. Any non-determinism (uuid, timestamp, dict iteration order) is a bug.
4. **Confidence gating:** only `human_verified` bindings reach the compiler. No exceptions, no override flags.
5. **User concepts are extension-only:** cannot redefine global or departmental. Cannot mask. Cannot version. Promotion is the only path to scope expansion.
6. **No SQL rewriting:** the compiler appends predicates. It does not parse or rewrite the block's user-authored SQL.
7. **Concept-blind charts render normally:** they do not error, do not hide. They just get a badge.
8. **LLM produces concept JSON, never SQL.** The compiler owns SQL emission. No LLM-direct-to-SQL path is acceptable in this phase.
9. **Filter bind names carry filter id:** prevents collision across multiple filters of the same concept (e.g. two date ranges).
10. **Primary time concept is mandatory on tables that bind ≥ 2 time concepts.** Tables with one or zero time bindings can omit it.

---

## 11. Sub-phases — ship independently, in order

Each sub-phase ends with a runnable, deployable state. No half-merged middle states.

### 11.a — Concept Registry + schema infrastructure

Goal: load and validate concept YAMLs; surface concepts via API; `semantic_tags.py` reads from registry.

Scope:

- `concepts/schema.py` — Pydantic for Concept, CanonicalValue, alias resolution.
- `concepts/registry.py` — YAML loader with caching (5-minute TTL or hot-reload via mtime).
- Migration script `0001_v0_to_v1.py` (concept skeletons only — no block/dashboard touching yet).
- `concepts/api/list`, `concepts/api/<id>` endpoints.
- `semantic_tags.py` rewired to read from registry.
- Tests: registry load roundtrip, alias resolution, scope precedence (global > dept > user).

Acceptance:

- `concepts/global.yaml` + `concepts/treasury.yaml` skeletons load.
- Every Phase 6.5 `semantic_tag` exists as a concept (even if `canonical_values` is empty).
- `concepts/api/list` returns all concepts with scope tags.
- Phase 6.5 blocks still load, run, render. Zero regression.

**Effort:** ~5–7 days.

### 11.b — Column Bindings + filter compiler

Goal: compile concept-level filters to per-table SQL via human-verified bindings; serve dashboard filters through the compiler.

Scope:

- `concepts/schema.py` — add ColumnBinding, Transform, ResolvedFilter Pydantic models.
- `catalog/bindings.py` — extend table_doc loader to parse `concept_bindings`.
- `concepts/compiler.py` — implement all 5 transform kinds.
- `dashboards/binding.py` — route filter resolution through compiler.
- `routes.py::apply_dashboard_filters` — extended response shape (`blind_filters`, `applied_predicates`).
- Data team migration: hand-write `concept_bindings` for top 10 tables (treasury + risk + bilanço).
- Tests: per-transform-kind golden SQL snapshots, determinism property test, empty-list short-circuit, blind-block handling.

Acceptance:

- A dashboard with 3 blocks pulling from 3 different tables responds correctly to a single `currency` filter — each block's SQL has its own correctly compiled predicate.
- A block whose table has no `currency` binding renders without the filter and shows the "blind" badge.
- Compiler determinism: same inputs → same SQL byte string, every time.
- LRU cache invalidation: editing a column binding's transform invalidates cached results for that table.

**Effort:** ~2–3 weeks. Largest single piece in Phase 7.

### 11.c — Binding Inference pipeline + review UI

Goal: data team can onboard a new table in < 30 minutes via inference + review.

Scope:

- `concepts/inference/pipeline.py` + four matchers.
- `concepts/prompts/binding_proposal.txt` — LLM prompt for ambiguous columns.
- `routes_concepts.py` — review queue + approve/reject endpoints.
- `templates/concepts/review.html` — operator UI.
- Tests: per-matcher unit tests, full-pipeline integration test with synthetic table.

Acceptance:

- Pointing the inference pipeline at a new EDW table proposes bindings for ≥ 70% of "obviously bindable" columns (currency, branch, time columns) at `inferred_sample` confidence or better.
- Operator approves a batch of 5 proposals; YAML diff is correct, compiler picks up changes on next request (no restart).
- LLM fallback fires only on columns where stages 1–4 produced nothing — verified via log telemetry on a real onboarding session.

**Effort:** ~2 weeks.

### 11.d — User-scoped concepts + promotion flow

Goal: a power user can define a one-off concept inside a presentation, use it, and request promotion to departmental.

Scope:

- Per-presentation concept storage (JSON on S3 alongside the manifest).
- Editor UI: "Add user concept" → minimal form (id, name, canonical_values, scope).
- `concepts/promote` endpoint: writes promotion intent to a review queue.
- Documentation page explaining when user concepts are appropriate vs. requesting departmental.

Acceptance:

- A user concept defined in presentation P is invisible to presentation Q (even same user).
- A user concept cannot collide with a global concept id (rejected at save).
- "Promote" generates a queue entry visible to the data team.

**Effort:** ~1 week. Can ship after 11.b independently; not blocking.

---

## 12. Test strategy

- **Registry & schema** — unit tests with fixture YAMLs in `concepts/tests/fixtures/`.
- **Compiler** — snapshot tests: golden SQL strings per (concept, transform_kind, value_set). Plus property tests: determinism, empty-list short-circuit, blind-block.
- **Inference matchers** — unit tests with synthetic column profiles.
- **End-to-end** — real Treasury table fixture (`TRD_BRANCH_POSITION` + `FX_SWAP_DEALS` + `DEPOSITS_DAILY`) → multi-filter request → compare against hand-written expected SQL.
- **Migration** — round-trip: SEMANTIC_TAGS_V0 → registry → re-emit allow-list → assert equality.
- **Regression** — every Phase 6.5 integration test still passes unchanged.

Coverage targets:

- Compiler: ≥ 95% line coverage (it's pure, tests are cheap).
- Inference matchers: ≥ 85%.
- Registry: ≥ 90%.

---

## 13. Performance budget

- **Concept registry load:** < 100ms cold, served from in-memory cache on warm hits. YAML count ≤ 20 in v0.
- **Compiler:** < 5ms per (block, filter) pair on a 50-block dashboard with 10 active filters. The compiler is a pure function over small structured inputs; no DB calls, no LLM calls.
- **Inference run per table:** ≤ 30s including 50-sample-value `SELECT DISTINCT` (which dominates the budget).
- **Review queue API:** < 200ms for a queue of ≤ 500 pending proposals.

The compiler must not become a bottleneck. If profiling shows > 5ms per (block, filter), the cause is wrong (likely YAML re-parse, dictionary copy in a loop, or something equally fixable).

---

## 14. Open questions / backlog (not blocking 11.a–11.d)

- **Concept versioning at runtime.** Today's compiler uses the latest accepted concept version. Old dashboards saved against v2 will silently get v3's canonical_values. Acceptable in v0; revisit if it bites.
- **Cross-concept derivations.** "Amount in EUR" from `AMOUNT_TRY × FX_RATE`. Probably belongs to a future "calculated field" feature, not Phase 7.
- **Constrained LLM decoding for binding proposals.** Grammar-guided JSON (llguidance / GBNF) would lock the LLM's output shape. Decided yes in principle; implementation is a follow-up if vanilla JSON parsing of LLM output proves unreliable.
- **NULL-in-filter semantics.** A filter `currency in [USD]` excludes rows where `CCY IS NULL`. Adding `include_nulls: true` is a UX question, not architectural. Defer.
- **Bucket re-bucketing.** What if `maturity` concept gains a new bucket `2W` after blocks are cached? Cache invalidation on concept version bump. Mechanism is straightforward; build when needed.
- **Concept-aware aggregation.** `GROUP BY maturity_bucket` even on tables that only have `MATURITY_DAYS`. Out of scope for Phase 7; possibly Phase 8 (scope contract semantics).
- **Multilingual aliases.** Currently aliases are flat strings. Could grow into `{en: [...], tr: [...]}`. Defer.

---

## 15. Glossary

- **Concept** — versioned business term defined in YAML. Has a canonical id and (for enum/bucket types) a canonical value alphabet.
- **Column binding** — per-table declaration that says "column X realizes concept Y via transform Z."
- **Transform** — function that maps a canonical concept value to a table-specific SQL predicate.
- **Filter compiler** — pure deterministic engine that converts (filters, table-set) into per-table SQL predicates.
- **Concept-blind block** — a block whose underlying table doesn't bind to one of the active filters. Renders without that filter, with a UI badge.
- **Binding inference** — semi-automated pipeline that proposes column→concept bindings for new tables.
- **Confidence** — provenance tag on each binding (`human_verified` / `inferred_*` / `llm_proposed`). Only `human_verified` reaches the compiler.
- **Primary time concept** — the time concept used when a generic "last 30 days" filter has no explicit time-concept type. Per-table.
- **User concept** — extension-only, per-presentation concept; cannot redefine global/departmental.
- **Promotion** — user concept → departmental, via the review queue.
