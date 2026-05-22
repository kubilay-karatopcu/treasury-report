# Phase 8 — Hazırlık (Stage 2 / Prepare Layer)

**Status:** Spec draft
**Owner:** Kubilay Karatopçu
**Depends on:** Phases 1–6 (production), Phase 6.5 (variable binding), Phase 7 (concept foundation)
**Followed by:** Phase 9 (Keşif / Stage 1)
**Language note:** Spec is in English for Claude Code consistency. User-facing strings are Turkish where called out.

---

## 1. Context

### 1.1 Why this phase exists

The current app drops the user directly into the composition editor (Sunum). For one-off ad-hoc dashboards this is fine. For structured analytical work — especially with large tables, multi-table baskets, or recurring reports — the user needs a deliberate "what data am I working with and how am I scoping it" step *before* composition.

Phase 8 introduces that step: **Hazırlık** (Prepare). It sits between table selection and dashboard composition. It produces a **scope contract** that gates what the composition layer (Sunum) can mutate, and it makes the **lazy/cache routing decision** explicit and visible.

This phase is the first half of the **Atölye** workflow (Atölye = Hazırlık + Keşif). The user enters via Atölye, builds a basket (Stage 1, Phase 9), refines its scope (Stage 2, this phase), crosses into Sunum (existing editor) with a ready DuckDB session.

### 1.2 What it unlocks

- **Pinned vs interactive filter distinction** — some scope decisions are locked at preparation time (e.g., "this dashboard is only about Q4 2025 USD"); others are exposed as dashboard filter widgets the user can adjust in Sunum.
- **Per-table routing decision** — tables small enough after scope go to DuckDB cache (fast); larger tables stay lazy on Oracle (slower but feasible). The user sees this choice and plans accordingly.
- **Re-entry from Sunum** — adding a table mid-composition is supported via "Edit scope" without losing the dashboard.
- **Re-runnable recipes** — the scope contract is durable and reproducible; a snapshot can be re-run later by re-executing the contract against current Oracle data.

### 1.3 Out of scope

- Stage 1 (Keşif / catalog browse) UI — Phase 9.
- The basket population workflow before Hazırlık (assumed: basket is populated either by Stage 1 in Phase 9, or by a temporary "add table by ID" form in Phase 8 for development).
- Marketplace / template-based scope import — Phase 10+.
- Multi-user editing of scope — last-write-wins, advisory lock per scope contract.
- Cross-scope sharing ("this scope is reusable across multiple dashboards") — backlog. v1: scope is per-presentation.

### 1.4 Relationship to existing system

Phase 7 produced:

- Concept registry (`concepts/global.yaml`, `concepts/<dept>.yaml`).
- Column bindings per table.
- Filter compiler (concept-level filter expression → SQL, dual-target Oracle + DuckDB).
- Binding inference for new tables.

Phase 6.5 produced:

- Block schema with `:bind_var` SQL + `semantic_tag` variables.
- Dashboard schema with `filters` array.
- Per-block DuckDB cache + subset routing.

Phase 8 adds:

- Scope contract as a new top-level artifact alongside the manifest.
- Routing decision (cached vs lazy) per table in the basket.
- Filter compiler invocation at scope-time for pre-fetched cached tables.
- Pinned filter enforcement in Sunum's patch validator.
- Hazırlık UI screen.
- Re-entry flow from Sunum back to Hazırlık.

Existing dashboards without a scope contract continue to work — they implicitly behave as "all filters interactive, all tables cached" (the current behavior).

---

## 2. Data Model

### 2.1 Scope contract

Stored at `s3://<bucket>/presentations/<user>/<id>/scope_v<N>.yaml`. One scope contract per presentation version. Immutable per version; editing creates `scope_v<N+1>`.

```yaml
scope:
  presentation_id: p_abc123
  version: 4
  created_by: A16438
  created_at: 2026-06-15T10:00:00Z
  parent_version: 3                    # null on first version

  basket:
    - table_ref:
        schema: ODS_TREASURY
        name: TRD_BRANCH_POSITION
      alias: positions                 # local alias used by joins and projections
      projection:
        columns: [AS_OF_DATE, BRANCH_ID, CCY, MATURITY_BUCKET, NET_POSITION]
        include_all: false             # if true, columns list is ignored at fetch time
      routing:
        decision: cached               # one of: cached | lazy
        decided_by: system             # one of: system | user
        estimated_bytes: 320_000_000   # post-scope estimate at decision time
        threshold_bytes: 500_000_000   # the cap that was applied

    - table_ref:
        schema: ODS_TREASURY
        name: DIM_BRANCH
      alias: branch_dim
      projection:
        columns: [BRANCH_ID, BRANCH_NAME, REGION]
        include_all: false
      routing:
        decision: cached
        decided_by: system
        estimated_bytes: 2_400_000

  joins:
    - id: j_positions_to_branch
      kind: lookup                     # one of: lookup | inner | left
      left:
        alias: positions
        column: BRANCH_ID
      right:
        alias: branch_dim
        column: BRANCH_ID
      confirmed_at: 2026-06-15T10:02:00Z

  filters:
    pinned:
      - id: pf_q4_2025
        concept: as_of_time
        op: between
        from: 2025-10-01
        to: 2025-12-31
        applies_to: [positions]        # which basket aliases this filter targets

    interactive:
      - id: if_currency
        concept: currency
        op: in
        default_values: [TRY, USD, EUR]
        allowed_values: [TRY, USD, EUR, GBP, CHF, JPY]
        label: "Para Birimi"
        applies_to: [positions]

      - id: if_maturity
        concept: maturity
        op: in
        default_values: [1M, 3M, 6M]
        allowed_values: [ON, 1W, 1M, 3M, 6M, 1Y, 2Y]
        label: "Vade"
        applies_to: [positions]

  status:
    state: ready                       # one of: drafting | fetching | ready | failed
    fetched_at: 2026-06-15T10:05:30Z
    fetch_duration_ms: 12_340
    cached_tables: [positions, branch_dim]
    lazy_tables: []
    errors: []
```

Key rules:

- `presentation_id` and `version` together uniquely identify a contract.
- `parent_version` enables auditing the evolution of a scope across re-entries.
- `basket[].alias` is the local name used everywhere downstream (joins, filters, block queries via `FROM :positions`-style references). Aliases are scope-unique.
- `routing.decision` is `cached` if `estimated_bytes <= threshold_bytes`, otherwise `lazy`. User can override (then `decided_by: user`).
- `filters.pinned[]` apply at fetch time (pushed to Oracle WHERE or applied during DuckDB materialization) and are immutable in Sunum.
- `filters.interactive[]` become dashboard filter widgets; they are not applied at fetch time but at block execution time.
- `applies_to` makes the filter target explicit. Empty or omitted = applies to all basket tables that bind the concept. Listed = applies only to those aliases.
- `status` is mutated by the system as the scope is materialized; the user-authored fields above never change once `status.state == ready`.

### 2.2 Scope contract validators

All run at scope save and before fetch:

1. **Alias uniqueness.** `basket[].alias` values must be unique within the scope.
2. **Concept validity.** Every filter's `concept` must exist in the concept registry (global or current user's dept).
3. **Concept coverage.** For every filter with `applies_to` populated, each listed alias must have a column binding for that concept. Otherwise, surfaced as warning ("filter has no effect on these tables") not error.
4. **Pinned filter consistency.** Pinned filter values must satisfy the concept's type and value constraints. `between` requires `from <= to`. `in` requires values from `canonical_values` or registered extension.
5. **Join consistency.** Each join's `left.alias` and `right.alias` must exist in basket; columns must exist in the respective tables' projections (or in `include_all` projections — verify against table schema).
6. **Projection sanity.** `columns` must all exist on the referenced table per its catalog metadata. Warn if `partition_column` is omitted (perf cost; not error).
7. **Routing threshold sanity.** `estimated_bytes` must be a positive integer or zero. If `threshold_bytes` is below a configured floor (e.g., 1 MB), warn (likely misconfiguration).

Validator returns `ValidationResult(ok: bool, errors: list[str], warnings: list[str])`.

### 2.3 Dashboard manifest extension

The dashboard manifest gains a single new top-level field:

```yaml
dashboard:
  id: branch_morning_review
  version: 7
  ...

  scope_ref:
    presentation_id: p_abc123
    scope_version: 4

  filters:
    # existing Phase 6.5 dashboard filters — these continue to work, but
    # interactive filters from scope are now also surfaced here automatically.
    ...

  blocks:
    ...
```

If `scope_ref` is absent, the dashboard behaves as in Phase 6.5/7 (no scope contract, everything interactive, all tables cached if they fit). This preserves backwards compatibility.

If `scope_ref` is present:

- Pinned filters from scope are surfaced to blocks at execution time but never as user-adjustable widgets.
- Interactive filters from scope are merged into the dashboard filter bar. Dashboard-local `filters[]` array can add additional filters but cannot override the scope's interactive set.
- Block queries that reference `:bind_var`s mapped to pinned filters get values from scope; cannot be rebound to dashboard filters.

### 2.4 Identifier rules

- `scope.version`: integer, monotonically increasing per `presentation_id`.
- `basket[].alias`: snake_case, 3–40 chars, scope-unique.
- `filters.pinned[].id`: prefix `pf_`, kebab_case, scope-unique.
- `filters.interactive[].id`: prefix `if_`, kebab_case, scope-unique.
- `joins[].id`: prefix `j_`, kebab_case, scope-unique.

---

## 3. Routing Logic

### 3.1 Routing decision algorithm

Per table in the basket, at scope-build time (before fetch):

```python
def decide_routing(table_ref, projection, pinned_filters_for_table, threshold_bytes):
    """
    Returns RoutingDecision(decision='cached'|'lazy', estimated_bytes, decided_by='system'|'user')
    """
    estimated_bytes = estimate_post_scope_size(
        table_ref=table_ref,
        projection=projection,
        pinned_filters=pinned_filters_for_table,
    )

    if estimated_bytes <= threshold_bytes:
        return RoutingDecision(
            decision='cached',
            estimated_bytes=estimated_bytes,
            decided_by='system',
        )
    return RoutingDecision(
        decision='lazy',
        estimated_bytes=estimated_bytes,
        decided_by='system',
    )
```

`estimate_post_scope_size` consults:

- Table catalog metadata: `estimated_daily_rows`, `partition_column`.
- Pinned filter date range (if `as_of_time` filter present and table partitioned on `AS_OF_DATE`): rows = `daily_rows * days_in_range`.
- Pinned filter selectivity for other concepts: from `distinct_values_sample` cardinality and assumed uniform distribution.
- Projection: bytes per row computed from column types and projected column list. Strings estimated at 2x average sampled length; numbers at fixed width.

The estimate is conservative — overestimate is fine (table goes lazy, slightly worse UX); underestimate is worse (DuckDB fills up, eviction churns).

User can override `system` decision via the routing badge UI. This sets `decided_by: user` and the system respects the choice unless `estimated_bytes` exceeds a hard ceiling (e.g., 10 GB), at which point the override is refused with a clear error.

### 3.2 Cached table fetch

For each `cached` table:

1. Compile pinned filters for that table via Phase 7's filter compiler → SQL WHERE clause (pushdown).
2. Compose the Oracle SELECT: `SELECT {projection_columns} FROM {schema}.{name} WHERE {pinned_where}`.
3. Execute via `DataClient.get_data(query=composed_sql, query_params={...})`.
4. Convert result via Arrow bridge into a DuckDB view named after the alias (`positions`, `branch_dim`).
5. Update `status.fetched_at`, `status.cached_tables`.

Block queries in Sunum then reference these aliases as DuckDB views. Block-level interactive filters are applied at block execution time on top of these views (existing Phase 6.5 path).

### 3.3 Lazy table query path

For each `lazy` table, no fetch happens at scope time. Instead, the dashboard registers a deferred resolution:

- When a block in Sunum queries against this alias, the block's SQL is rewritten to target Oracle directly (not the DuckDB view).
- Pinned filters AND any interactive filters resolved for that block execution are merged into the Oracle WHERE clause.
- Result is streamed back through the Arrow bridge, optionally cached per block execution (existing Phase 6.5 block cache applies normally).

The block author doesn't write different SQL for cached vs lazy — the rewrite happens transparently in the execution layer. From the block's perspective, `FROM positions WHERE ...` always works; the layer below decides whether `positions` is a DuckDB view or a deferred Oracle reference.

### 3.4 Routing decision UI

In Hazırlık, every basket table shows a routing badge:

- `cached (320 MB)` — green/blue dot, hover shows estimated_bytes details and "Switch to lazy" override link.
- `lazy (Oracle, ~4.2 GB)` — yellow dot, hover shows reasoning and "Force cached" override link (refused if exceeds hard ceiling).

Override sets `decided_by: user`. Saving the scope with overrides locks them in.

### 3.5 What "Build dashboard" does

User clicks "Build dashboard" in Hazırlık. The flow:

1. Run validators on scope contract. Abort with errors if any.
2. Set `status.state = fetching`.
3. For each cached table: §3.2 fetch + DuckDB materialize.
4. Set `status.state = ready`, populate `status.cached_tables`, `status.lazy_tables`, `status.fetched_at`.
5. Persist scope contract.
6. Create / update dashboard manifest with `scope_ref`.
7. Redirect to Sunum.

If any fetch fails: `status.state = failed`, error in `status.errors`, user remains in Hazırlık to fix the problem (often: projection includes a non-existent column, pinned filter pushes selectivity to zero, network issue).

### 3.6 Re-entry from Sunum

When the user clicks "Edit scope" in Sunum:

1. Load current scope contract (`scope_v<N>`).
2. Render Hazırlık with current state pre-populated.
3. User makes changes (add table, modify projection, change filter).
4. On "Apply changes":
   a. Compute diff between `scope_v<N>` and proposed new scope.
   b. Determine affected tables: any with changed projection, filter, or routing.
   c. Re-fetch only affected tables (other tables' DuckDB views remain).
   d. Persist as `scope_v<N+1>` with `parent_version: N`.
   e. Update dashboard manifest's `scope_ref` to point to `scope_v<N+1>`.
   f. Identify blocks affected by the change (reference removed tables, reference renamed aliases, use filters that changed pinned-vs-interactive state).
   g. Warn the user with a list of affected blocks before committing the dashboard manifest update. User can choose to proceed (affected blocks may render with errors until manually fixed) or cancel.

If user removes a table that blocks reference, those blocks enter an error state in Sunum (similar to deleted-source error from Phase 6); they aren't auto-deleted.

---

## 4. Sunum Integration — Pinned Filter Enforcement

### 4.1 Patch validator extension

Sunum's existing patch validator (Phase 2's `validate_patch.py`) must be extended to reject patches that violate the scope contract.

New rejection rules:

1. **Pinned filter mutation.** A patch targeting `/filters/pinned/<id>/` paths or `/blocks/<n>/variable_bindings/<var>/...` where the variable's resolved binding is a pinned filter is rejected. Error: `"Cannot mutate pinned filter '<id>' — set in scope contract scope_v<N>"`.
2. **Scope-ref tampering.** Patches modifying `/scope_ref` are rejected unless emitted by the scope re-entry flow (internal flag set on the patch metadata; LLM-generated patches never have this flag).
3. **Lazy table caching.** Patches that would force a lazy table into cached mode mid-session are rejected. User must return to Hazırlık.

These checks run before existing immutable-field checks. Test coverage required (see §10).

### 4.2 Block execution against scope

Block execution flow (existing Phase 6.5 path) gains scope awareness:

1. Resolve variables (existing).
2. **NEW:** For each variable bound to a pinned filter, override with the pinned value (ignore any dashboard filter widget state, which shouldn't exist anyway).
3. **NEW:** Determine routing: if the block queries an alias in `status.lazy_tables`, rewrite to Oracle path; else use DuckDB view.
4. Apply concept-level filter compilation (existing Phase 7).
5. Execute against the determined target.
6. Cache result keyed by `(block_id, version, resolved_variables_hash)` (existing).

### 4.3 Dashboard filter bar with scope

The filter bar in Sunum shows:

- Interactive filters from the scope contract (read from `scope_ref`).
- Dashboard-local filters from the manifest's `filters[]` array.

Pinned filters are *not* shown in the filter bar. They may optionally be surfaced as a read-only "scope" indicator chip (UI decision; see §6.3).

If a dashboard-local filter has the same `semantic_tag` and target as a scope interactive filter, the scope filter wins and the local filter is rejected at save with a clear error.

---

## 5. Hazırlık LLM Role

### 5.1 What Stage 2 LLM does

The Hazırlık chat panel runs the **same Qwen endpoint** but with a scope-refinement system prompt. It can:

- Explain concepts and their binding in the basket tables.
- Suggest pinning vs interactive for a filter based on conversational signal.
- Recommend column projections based on what the user describes wanting to analyze.
- Propose lookup joins when the user mentions joining dimensions.
- Summarize the current scope and call out potential issues ("this projection drops the partition column; queries will be slow").

### 5.2 What Stage 2 LLM does NOT do

- Does not propose new tables for the basket. That's Stage 1 (Phase 9).
- Does not write SQL.
- Does not modify the scope contract directly — every change goes through the user's confirmation in the UI.
- Does not touch block-level configuration.

### 5.3 LLM output contract

The Hazırlık LLM outputs **scope refinement suggestions** as structured JSON:

```json
{
  "explanation": "Tarih filtresini pin etmeni öneririm çünkü ...",
  "suggestions": [
    {
      "kind": "pin_filter",
      "filter_id": "if_period",
      "rationale": "Bu dashboard sadece Q4 2025 odaklı görünüyor."
    },
    {
      "kind": "add_projection_column",
      "alias": "positions",
      "column": "CCY",
      "rationale": "Para birimi filtresi için gerekli."
    },
    {
      "kind": "confirm_join",
      "join_id": "j_positions_to_branch",
      "rationale": "Şube adlarını görüntülemek için."
    }
  ]
}
```

Each suggestion has a one-click "Apply" affordance in the UI. The user can also dismiss.

### 5.4 Forward-compat

Stage 2 LLM's prompt and output contract are designed to extend naturally in Phase 11 when LLM gains cross-dimension awareness (process suggestions, template recommendations). Adding a `kind: "suggest_template"` later is additive.

---

## 6. UI Surfaces

### 6.1 Hazırlık main screen layout

Route: `/atolye/hazirlik/<presentation_id>`. Standalone screen, not nested in Sunum.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Hazırlık                                            [Atölye'ye dön] │
│  Sunum: "Pazartesi Şube İnceleme"                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Sepet (3 tablo)                                          [+ Tablo]   │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ positions                                       [cached, 320MB] │ │
│  │ ODS_TREASURY.TRD_BRANCH_POSITION                                │ │
│  │ Kolonlar: AS_OF_DATE, BRANCH_ID, CCY, MATURITY_BUCKET, +1  [✎] │ │
│  │ Concepts: as_of_time, branch, currency, maturity                │ │
│  └────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ branch_dim                                       [cached, 2MB] │ │
│  │ ODS_TREASURY.DIM_BRANCH                                         │ │
│  │ Lookup join: positions.BRANCH_ID → branch_dim.BRANCH_ID  [✓]   │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  Filtreler                                              [+ Filtre]    │
│  ┌─ Pinned ──────────────────────────────────────────────────────┐  │
│  │ pf_q4_2025: as_of_time between 2025-10-01 and 2025-12-31  [✎] │  │
│  └────────────────────────────────────────────────────────────────┘  │
│  ┌─ Interactive ─────────────────────────────────────────────────┐  │
│  │ if_currency: currency in [TRY, USD, EUR] (default)         [✎] │  │
│  │ if_maturity: maturity in [1M, 3M, 6M] (default)            [✎] │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  Concept dağılımları                                                  │
│  [histogram chips: currency, maturity, branch — clickable]            │
│                                                                       │
│                                              [Sunum'a geç →]          │
└─────────────────────────────────────────────────────────────────────┘
```

Right side panel (collapsible): Stage 2 LLM chat.

### 6.2 Routing badge

Inline on each basket table card:

- Green/blue chip: `cached (320 MB)` — hover tooltip explains threshold + estimated_bytes math; right-click or click reveals "Switch to lazy" link.
- Yellow chip: `lazy (Oracle, ~4.2 GB)` — hover tooltip explains why; "Force cached" link (rejected with explanation if exceeds hard ceiling).

### 6.3 Pinned filter visibility in Sunum

Pinned filters are not in the editable filter bar. They surface as a small read-only banner at the top of Sunum:

```
🔒 Scope: Q4 2025 · TRY/USD/EUR    [Edit scope]
```

Clicking "Edit scope" returns to Hazırlık.

### 6.4 Filter editor modal

When the user clicks `[✎]` on a filter, a modal opens:

- Concept (read-only after creation).
- Operation (`in`, `between`, `eq`, `last_n_days`, etc. — based on concept type).
- Value(s) — input widget chosen by op + concept type (date range picker, multi-select, etc.).
- "Pinned" vs "Interactive" toggle. Defaults to user's last choice for this concept (memoized in user prefs).
- `applies_to` table multi-select (defaults to all basket tables that bind this concept).
- Allowed values (for interactive only) — restrict what dashboard widget can submit.

### 6.5 Concept distribution preview

For each concept bound by ≥1 basket table, render a chip showing top-N value distribution:

```
currency  [TRY 62%][USD 24%][EUR 11%][...]
maturity  [1M 35%][3M 28%][6M 19%][...]
branch    [402 41%][501 33%][...]
```

Clicking a chip opens a panel with full distribution and a one-click "Filter by these values" affordance.

Data source: distinct_values_sample from table docs (Phase 6.5.b) joined across basket tables for shared concepts. Pre-fetched at Hazırlık load.

### 6.6 "Add table" flow (interim)

Phase 9 will provide the proper Stage 1 catalog browser. For Phase 8, "Add table" opens a simple modal:

- Search bar against table catalog (server-side endpoint, returns matching table names + descriptions).
- Select a table → form pre-populated with default projection + suggested bindings.
- Confirm → table added to basket with `cached` decision (system-decided).

This is intentionally minimal; the polished UX lives in Phase 9.

---

## 7. Migration Strategy

### 7.1 Existing dashboards without scope

Continue to work exactly as in Phase 6.5/7. Sunum recognizes the absence of `scope_ref` and treats everything as interactive + cached.

A "Convert to scoped dashboard" action in Sunum settings lets a user opt in:

1. Build a default scope contract from current dashboard state: all tables referenced by blocks → basket; all dashboard filters → interactive; no pinned filters initially.
2. Open Hazırlık for refinement.
3. On save, dashboard gets `scope_ref` pointing to the new contract.

Existing snapshots: unaffected. They are frozen; scope contract is for live presentations only.

### 7.2 Snapshots and scope

When a snapshot is taken (Phase 5), the scope contract is bundled alongside the manifest + frozen data. Re-running a recipe (re-fetching from Oracle into a new session) replays the scope contract first, then the manifest.

### 7.3 Concept registry changes

If a concept in `concepts/global.yaml` or `concepts/<dept>.yaml` changes (canonical values added/removed):

- Pinned filters referencing that concept may fail validation. They get a `degraded` badge in Hazırlık.
- The user must re-open Hazırlık and adjust before the dashboard can run.
- A scope re-validation job (nightly) flags affected scopes.

---

## 8. Implementation Sub-Phases

Each sub-phase has its own acceptance criteria (§10) and ships independently.

### 8.a — Scope contract data model and persistence

**Goal:** the contract exists as a durable artifact. Sunum enforces pinned filters. No Hazırlık UI yet; testing via fixtures and a temporary `POST /scope` endpoint.

Deliverables:
- Scope contract YAML schema + Pydantic models (`presentations/scope/schema.py`).
- Validators (`presentations/scope/validators.py`).
- S3 store with version bumping (`presentations/scope/store.py`).
- Routing decision algorithm (`presentations/scope/routing.py`), without fetch path (8.d adds fetch).
- Sunum patch validator extension to reject pinned filter mutations (`presentations/nodes/validate_patch.py` update).
- Dashboard manifest `scope_ref` field, optional, backwards-compatible.
- Block execution layer awareness: variable resolution prefers pinned scope filter values.
- Tests: schema, validators, patch rejection, routing decisions for varied table sizes.
- Fixtures: a sample scope contract + dashboard manifest that uses it.

### 8.b — Hazırlık UI: basket and scope filters

**Goal:** the screen exists and works for cached tables. Lazy path still pending.

Deliverables:
- Hazırlık page route + template + JS bundle entry (`presentations/templates/hazirlik.html`, `presentations/static/js/hazirlik/`).
- Basket table card UI.
- Filter editor modal with pinned/interactive toggle.
- Concept distribution chips (uses Phase 6.5.b `distinct_values_sample`).
- "Add table" interim modal (§6.6).
- "Sunum'a geç" button: validates scope → fetches cached tables → redirects to Sunum.
- Sunum read-only scope banner (§6.3).
- Sunum "Edit scope" link returning to Hazırlık.

### 8.c — Projection and lookup joins

**Goal:** users can refine projections and confirm joins.

Deliverables:
- Column projection picker UI (per-table).
- LLM endpoint for suggested column subset given user task description.
- Lookup join suggestion engine (reads table docs `lookup` field, suggests joins).
- Join confirmation UI.
- Validator updates: joins must reference existing aliases and projected columns.

### 8.d — Lazy/cache routing

**Goal:** large tables can be in the basket without overwhelming DuckDB.

Deliverables:
- Routing badge UI (§3.4) with override link.
- `estimate_post_scope_size` enhancements (handle missing partition info, fallback heuristics).
- Lazy table query path (block execution layer rewrite to Oracle for lazy aliases).
- Pinned filter pushdown for cached tables (§3.2).
- Hard ceiling for forced-cached override.
- Tests: routing decisions, lazy query rewrite, block result correctness against lazy vs cached.

### 8.e — Re-entry from Sunum

**Goal:** users can modify scope mid-composition without losing the dashboard.

Deliverables:
- Scope diff computation between two versions.
- Affected-tables identification.
- Affected-blocks identification.
- Re-fetch logic that updates only changed tables.
- Warning UI before applying scope changes that affect existing blocks.
- Tests: diff correctness, partial re-fetch correctness, block error state for removed tables.

### 8.f — Hazırlık LLM (refinement chat)

**Goal:** the LLM helps users refine scope.

Deliverables:
- New system prompt for Stage 2 (`presentations/prompts/scope_refine.txt`).
- LLM client wrapper that constrains output to the JSON contract (§5.3).
- Chat panel UI (right side of Hazırlık).
- Suggestion cards with one-click "Apply" mapping each `kind` to the appropriate scope mutation.
- "Dismiss" affordance.
- Tests: prompt produces valid JSON for representative scenarios.

### Sub-phase ordering

```
8.a (data model + Sunum enforcement)
  ↓
8.b (basic Hazırlık UI with cached tables only)
  ↓
8.c (projection + joins)    8.d (lazy routing)    8.f (LLM chat)
  └──────────────────────────┴────────────────────┘
                             ↓
                           8.e (re-entry)
```

8.c, 8.d, 8.f can be parallel after 8.b. 8.e last because re-entry depends on all other surfaces being stable.

---

## 9. Forward-Compat with Future Phases

### 9.1 Phase 9 (Keşif / Stage 1)

The interim "Add table" modal in Phase 8 (§6.6) is replaced by the Stage 1 catalog browser. The basket-write API from 8.b must be callable from Stage 1 — design it to accept multiple tables at once and to not require the user to be currently in Hazırlık.

### 9.2 Phase 10 (Marketplace MVP)

Templates declare `required_concepts`. When importing a template into a dashboard that has a scope contract, the importer's reconciliation flow checks the scope's basket for tables binding those concepts. The scope contract format must remain stable.

### 9.3 Phase 11 (Discovery)

Stage 2 LLM suggestion contract (§5.3) is forward-compatible with template suggestions (`kind: "suggest_import_template"`). No schema change needed.

### 9.4 Phase 13–15 (Process layer)

Process entities reference `data_tables` and `concepts`. A snapshot tagged with a process_id will have its scope contract contribute to the process's accumulated knowledge (what tables, filters, concepts a process typically uses). No Phase-8 change needed; just keep scope contracts well-typed.

---

## 10. Acceptance Criteria

### 10.a — Scope contract data model and persistence

- A scope contract YAML can be saved to S3 and loaded back identically.
- All §2.2 validators run; each rejection rule has a test.
- A dashboard manifest can reference a scope contract via `scope_ref`.
- A Sunum patch attempting to mutate a pinned filter is rejected with the exact error specified in §4.1.
- A block variable bound to a pinned filter resolves to the pinned value regardless of dashboard filter widget state.
- Routing decision: given a 30-day projection of a table with `estimated_daily_rows: 12000` and 5 columns totaling ~200 bytes/row, decision is `cached`. Given 5 years with same parameters, decision is `lazy`.
- Routing override: user setting `decided_by: user, decision: cached` on a table estimated at 3 GB succeeds. Setting same on a table estimated at 15 GB is rejected with hard-ceiling error.
- Backwards compat: a dashboard manifest with no `scope_ref` field loads and renders correctly.
- All scope contract field defaults documented and respected.

### 10.b — Hazırlık UI

- Hazırlık route renders for an existing presentation with a scope contract.
- User can add a table via the interim modal.
- User can edit projection (column selection).
- User can add/edit/remove filters; toggle pinned/interactive.
- "Sunum'a geç" validates scope, fetches cached tables, redirects to Sunum without errors for the fixture scope.
- Sunum shows the read-only scope banner.
- Concept distribution chips render with real distinct_values data.

### 10.c — Projection and joins

- User can select/deselect columns per basket table.
- Removing a column referenced by a join is rejected with explanation.
- LLM suggests reasonable column subsets when given a task description (smoke test, not exact match).
- Join suggestion engine proposes joins for tables with declared lookups in their docs.
- User can confirm or decline joins; declined joins do not enter the scope contract.

### 10.d — Lazy/cache routing

- Lazy table query rewrite produces correct SQL targeting Oracle for a block referencing a lazy alias.
- Pinned filter values are pushed into Oracle WHERE for cached fetches and into block query WHERE for lazy executions.
- A block referencing both a cached and a lazy alias works (join executed in the layer that has both — currently DuckDB by pulling the lazy result on the fly).
- Routing badge UI reflects current decision and decided_by.
- Override link works for valid override; refuses for hard-ceiling violations.

### 10.e — Re-entry

- User in Sunum clicks "Edit scope", lands in Hazırlık with current state.
- Adding a new table and saving produces `scope_v<N+1>` with correct `parent_version`.
- Only the new table is fetched; existing cached tables remain in DuckDB.
- Removing a table used by blocks shows the warning UI; user can proceed (blocks enter error state) or cancel.
- Changing a filter from pinned to interactive does not require re-fetch.
- Changing a pinned filter value triggers re-fetch for tables it `applies_to`.

### 10.f — LLM refinement chat

- Stage 2 chat returns JSON matching the contract in §5.3 for representative user messages.
- Invalid LLM output is caught and retried once with error feedback (existing Phase 3 pattern).
- "Apply" on each suggestion kind correctly mutates the scope contract draft state.
- Chat panel UI does not block other Hazırlık interactions.

---

## 11. Open Questions / Backlog

- **Shared scope across multiple dashboards.** A team running the same weekly report wants to share one scope across dashboards. Backlog. Likely Phase 12+.
- **Scope templates.** A user starting a new presentation might want to pick a starter scope ("Treasury Q4 default"). Backlog. Likely co-evolves with Phase 13's process layer.
- **Cross-table interactive filters with conflicting domain.** If `if_currency` applies to two tables and one has `JPY` in its data while the other doesn't, allowed_values should reflect the intersection or union? v1: intersection (only values valid in all targeted tables). Revisit.
- **Predictive routing.** Use historical block execution patterns to bias routing decisions toward likely access patterns. Backlog. Requires Phase 12+ telemetry.
- **Incremental fetch on scope expansion.** Currently a date-range expansion in a pinned filter triggers full re-fetch. Incremental fetch (delta only) is backlog (also called out in Phase 6.5 backlog).

---

## 12. Glossary

- **Hazırlık** — the Stage 2 Prepare screen. Builds the scope contract.
- **Keşif** — the Stage 1 Discover screen (Phase 9, not in this spec). Builds the basket.
- **Atölye** — the combined workspace (Hazırlık + Keşif). User-facing umbrella name.
- **Sunum** — the existing composition editor (Phases 1–6.5).
- **Scope contract** — durable YAML artifact gating dashboard behavior.
- **Pinned filter** — locked at scope time, immutable in Sunum.
- **Interactive filter** — exposed as a dashboard widget in Sunum.
- **Cached table** — fully loaded into DuckDB at scope-build time; fast block execution.
- **Lazy table** — queried against Oracle on demand at block execution time.
- **Routing decision** — per-table choice of cached vs lazy.
- **Basket** — the set of tables in scope for a presentation.

---

*End of spec. Revise via PR. Sub-phase 8.a kickoff prompt is at `docs/KICKOFF_PROMPT_PHASE_8_A.md`.*
