# Treasury Studio — Master Roadmap

**Owner:** Kubilay Karatopçu
**Last revision:** 2026-05-21
**Status:** Living document — revise via PR as decisions land.

This is the end-to-end vision and phase plan for Treasury Studio. It supersedes the original 6-phase plan in `CLAUDE.md`'s "What to build first" section and embeds the new direction agreed in May 2026 conversations.

---

## 0. Vision in one paragraph

Treasury Studio is a block-based, LLM-driven dashboard platform that lets QNB Finansbank teams compose and share analytical artifacts without engineering involvement. The platform's evolution is layered: it starts as a single-presentation editor (current production), grows a variable-bound reusable block catalog (Phase 6.5), then introduces a semantic concept layer that unifies disparate data tables under shared filtering vocabulary (Phase 7), exposes a guided two-stage "data preparation → composition" workflow (Phase 8–9), enables crowdsourced block reuse via a marketplace (Phase 10–12), and finally surfaces emergent business-process knowledge from accumulated usage (Phase 13–15). Each phase ships independently and leaves the app fully deployable.

---

## 1. Conceptual model — the three durable abstractions

Three abstractions appear and reappear across all phases. Understanding their relationships up front prevents architectural drift:

```
Concept              "what filter means in business terms"
   ↑ binds to
Column               "where that meaning lives in a table"
   ↑ scoped by
Variable / Filter    "the resolved value at run time"
```

And, layered on top, three composition tiers:

```
Block                atomic visual — one chart, one question
   ↑ composes into
Snapshot             a built dashboard, immutable, shareable
   ↑ recurs as
Process              an archetype of a repeated business activity
```

Concepts give meaning to filters. Filters parameterize blocks. Blocks compose into snapshots. Snapshots, when repeated with cadence, reveal processes. Each phase below introduces or refines exactly one of these abstractions.

---

## 2. Data layers — the physical model

Three storage tiers. Their roles are stable across phases; what changes is which layer the user interacts with directly.

### 2.1 Oracle EDW (source of truth)

- Authoritative, transactional, partitioned (typically by `AS_OF_DATE` or equivalent).
- Read access via existing `DataClient.py` global instance.
- Per-user RBAC enforced at the Oracle level — Treasury Studio never bypasses it.
- Pushdown targets: time partitions, foreign keys on declared partition columns, primary-key dimension lookups.
- Cost model: high latency (seconds), high data integrity, governed.

### 2.2 Per-session DuckDB (working layer)

- Created on demand per `(user, presentation)` pair. Lives in `/tmp/presentations/{user_id}/{presentation_id}/session.duckdb`.
- Loaded via Arrow bridge from Oracle (avoids pandas dtype recasting and NaN issues).
- Block cache lives here (Phase 6.5+): keyed by `(block_id, version, resolved_variables_hash)`.
- Subset routing logic runs here: a narrower filter request can be served by filtering an already-cached wider result.
- 2 GB soft cap per session; LRU eviction.
- Lifecycle: best-effort. Pod restart wipes it. Manifest is the durable contract; session is rebuilt on demand.

### 2.3 S3 durable store (persistence + sharing)

- Manifests: `s3://<bucket>/presentations/<user>/<id>/<version>.json`
- Snapshots (frozen data + manifest as parquet): `s3://<bucket>/snapshots/<sid>/`
- Block templates (Phase 6.5+): `s3://<bucket>/blocks/<team>/<id>/<version>.yaml`
- Dashboard templates: `s3://<bucket>/dashboards/<user>/<id>/<version>.yaml`
- Table documentation: `s3://<bucket>/table_docs/<schema>/<table>.yaml`
- Concept registry (Phase 7+): `s3://<bucket>/concepts/{global,treasury,risk,...}.yaml`
- Vector embeddings index (Phase 11+): for semantic search across block/process documentation.

### 2.4 Why three layers, not two

Oracle is governed and slow. DuckDB is fast but ephemeral. S3 is durable but not query-friendly. Each tier carries exactly what it can carry well: Oracle has the truth, DuckDB has the working set, S3 has the configuration and shareable artifacts. **Routing decisions** between these tiers are the most important architectural choices in every phase.

---

## 3. Phase ledger

### Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Done, in production |
| 🟢 | Currently in active implementation |
| 🟡 | Spec'd, not yet started |
| ⚪ | Outlined only, spec not written |

### Quick index

| Phase | Title | Status | Spec |
|---|---|---|---|
| 1 | Skeleton | ✅ | CLAUDE.md |
| 2 | Manifest + JSON Patch engine | ✅ | CLAUDE.md |
| 3 | Chat + LLM (no data layer) | ✅ | CLAUDE.md |
| 4 | DuckDB session | ✅ | CLAUDE.md |
| 5 | Persistence + share | ✅ | CLAUDE.md |
| 6 | Polish + layout | ✅ | CLAUDE.md |
| **6.5** | **Variable Binding MVP** | 🟢 | `docs/PHASE_6_5_SPEC.md` |
| 7 | Concept Foundation | 🟡 | TBD |
| 8 | Stage 2 — Hazırlık (Prepare) | 🟡 | TBD |
| 9 | Stage 1 — Keşif Tables (Discover) | 🟡 | TBD |
| 10 | Block Marketplace MVP | 🟡 | TBD |
| 11 | Discovery Layer | ⚪ | TBD |
| 12 | Marketplace Polish + Production | ⚪ | TBD |
| 13 | Process Tagging | ⚪ | TBD |
| 14 | Bottom-up Process Emergence | ⚪ | TBD |
| 15 | Knowledge Graph + Narrative Summarization | ⚪ | TBD |

---

## 4. Phase-by-phase detail

### Phase 1–6 — Foundation (current production state, ✅ shipped)

Already implemented and in production. Documented in detail in `CLAUDE.md`. Summary:

- **Phase 1 — Skeleton.** Flask Blueprint, base.html extension, React mounted from CDN, hardcoded seed manifest renders read-only.
- **Phase 2 — Manifest + JSON Patch.** Manifest schema, Python + JS twin patch engines (RFC 6902 subset: replace/add/remove), validators for immutable fields and chart-length invariants.
- **Phase 3 — Chat + LLM.** Qwen integration via OpenAI-compatible endpoint, JSON-in-content parsing (no tool calling), SSE streaming, validate-then-apply patch flow with 1 retry on validation failure.
- **Phase 4 — DuckDB session.** Per-presentation DuckDB file in `/tmp`, Oracle → Arrow → DuckDB load, `plan_fetch.py` routes patches to render-only / requery / re-fetch tiers.
- **Phase 5 — Persistence + share.** S3 snapshot save (frozen parquet + manifest), shareable presentation-mode page that loads read-only.
- **Phase 6 — Polish + layout.** Loading states, lock UX, basket UI completeness, TOC sidebar, block width / multi-column grid.

What's in the current app: a single-presentation editor where one user creates one ad-hoc dashboard at a time, chats with the LLM to mutate it, and shares an immutable snapshot. Blocks are bespoke per presentation; no reuse, no shared filters across blocks, no catalog of tables with semantic meaning.

What's missing: everything Phase 6.5 onwards addresses.

---

### Phase 6.5 — Variable Binding MVP 🟢

**Spec:** `docs/PHASE_6_5_SPEC.md` (authoritative; this section is summary only).

**Why it exists.** The team needs to start producing reusable blocks in parallel while later phases are being built. Phase 7's concept layer is the right long-term answer but too heavy to gate the team on. Phase 6.5 is a deliberate transitional layer: it gives users a manual but structured way to parameterize blocks and share them across dashboards, with a forward-compatibility contract that makes the Phase 7 migration mechanical.

**What changes.**

- Blocks gain a user-authored SQL query with `:bind_var` placeholders and a `variables` array (name, type, semantic_tag, default, allowed_values).
- Dashboards gain a top-level `filters` array. Filters auto-bind to block variables by matching `semantic_tag`.
- Per-block DuckDB cache with subset routing: a narrower filter request is served from a cached parent without an Oracle round-trip.
- Block library: browse, search, insert into dashboards.
- Table docs gain `filterable`, `filter_role`, `suggested_variable`, `suggested_semantic_tag`, `distinct_values_sample` fields.

**Locked decisions.**

1. `semantic_tag` is mandatory and drawn from a fixed allow-list (the seed of the future concept registry).
2. Blocks are immutable per version; edits create `version: N+1`.
3. SQL whitelist: `SELECT` and `WITH` only. Parser-based, runs at save and pre-execution.
4. Bind expansion never concatenates; `enum_multi` expands to positional placeholders.
5. Subset routing on the block cache. Full refetch on superset miss.
6. 2 GB session cache soft cap + LRU eviction.
7. No free-form text variables in v0.
8. Forward-compat with Phase 7 is contractual (spec §9).

**Sub-phases (ship independently).**

- **6.5.a — Block save and run.** Block schema, SQL validator, variable resolver, bind expansion, block editor UI, run endpoint. ~1 week.
- **6.5.b — Table documentation enhancement.** Extended schema, LLM prompt integration, nightly distinct-values cron, migration of top 5 treasury tables. ~3–4 days. Can run in parallel with 6.5.c.
- **6.5.c — Dashboard-level filter.** Dashboard schema extension, filter bar UI, auto-binding, "filter eklemek ister misiniz?" prompt, block-level constant override, block cache + subset routing. ~1.5–2 weeks.
- **6.5.d — Library MVP.** Browse / search / preview / insert flow. ~3–5 days.

**Forward-compat to Phase 7.**

When Phase 7 lands, a migration script will:

1. Convert the `SEMANTIC_TAGS_V0` allow-list into `concepts/global.yaml` + `concepts/treasury.yaml`.
2. Cross-check each variable's `allowed_values` against the concept's `canonical_values`; flag mismatches.
3. Reorganize per-table docs into per-concept binding files.
4. Lift the `filters` array on each dashboard into a Phase-7 scope contract's `interactive_filters`.

Block YAMLs and dashboard YAMLs from 6.5 remain valid throughout — no rewriting required.

---

### Phase 7 — Concept Foundation 🟡

**Why it exists.** Phase 6.5 ships a manual variable system where each user names their own variables and picks their own values. This works at small scale but breaks the moment two different tables represent the same business concept with different column names, different value alphabets, or different physical types. Treasury data has many such cases: `MATURITY_DAYS = 45` in one table and `MATURITY_LABEL = "1M"` in another both refer to the same maturity bucket; `CCY = "USD"` and `CURRENCY_NAME = "US Dollar"` both mean the same currency. Without a unifying layer, cross-table filters are impossible.

**What changes.**

- A **Concept Registry** is introduced: a versioned YAML hierarchy.
  - `concepts/global.yaml` — bank-wide concepts: `currency`, `as_of_time`, `counterparty`.
  - `concepts/<dept>.yaml` — departmental concepts: `maturity` (Treasury), `pd_band` (Risk), `mevduat_segment` (Bilanço).
  - User-scoped concepts: stored per-presentation, never overriding global; extension only.
- **Column Bindings** declare per-table how columns map to concepts and how their values transform:
  - Direct (`identity`) — column already matches canonical.
  - Map (`{"US Dollar": "USD", "Euro": "EUR"}`) — inline mapping.
  - Lookup — join to a dimension table (`dim_bank` → SWIFT_BIC).
  - Bucket-from-range (`MATURITY_DAYS` integer → maturity bucket via `day_ranges`).
- **Filter Compiler.** A concept-level filter expression compiles deterministically to table-specific SQL. The LLM never writes SQL; it produces concept-level JSON, the compiler generates raw SQL with parameterized binds.
- **Binding Inference.** Hybrid pipeline for onboarding new tables: regex on column names → dtype check → sample-value pattern detection → LLM fallback for ambiguous cases → human review UI before promotion.
- **Three concept scopes.**
  - Global (system-owned, immutable).
  - Departmental (data team-owned, per-department).
  - User (presentation-scoped; promotable via review).
- **Date concept disambiguation.** Each table declares a `primary_time_concept`. A `FX_SWAP_DEALS` table might have both `TRADE_DATE` (bound to `as_of_time`) and `VALUE_DATE` (bound to `settle_time`). "Last 30 days" filters use the table's primary time concept.

**Locked decisions (preliminary; finalize in spec).**

- System + departmental concepts in YAML (git-versioned). User concepts in DB (runtime-editable).
- Promote = DB → YAML PR. Review required.
- User concept extension only; cannot override or redefine global concepts.
- Maturity bucket transformations are lossy by design (`45 days → 1M`); both original and canonical bucket columns are preserved in DuckDB views.
- Concept blind chart (a chart whose underlying table doesn't bind to a filter's concept) renders fine but is flagged in the UI with a "filter not applied here" badge.

**Migration from Phase 6.5.**

- `semantic_tag` field on every variable becomes the concept reference key. Direct lookup.
- `allowed_values` per variable is cross-checked against the concept's `canonical_values`. Mismatches are reported, not silently overwritten.
- Block queries with raw SQL are NOT rewritten. The concept-aware filter compiler is additive: it adds extra `WHERE ... AND ...` clauses derived from concept-level filters, on top of the block's own query.

**Dependencies.** Phase 6.5 must be in production; without `semantic_tag` field on variables and the extended table doc schema, the migration is impractical.

**Effort estimate.** ~6–8 weeks. Largest chunk: binding inference + human review tooling.

**Open questions.**

- Concept registry write path: YAML PR + redeploy, or hot-reload from S3? Probably YAML in code for system/dept, with DB for user-level.
- LLM's role: writes concept-level JSON only (never SQL), constrained-decoded via grammar (llguidance / GBNF)? Decided yes in principle; implementation detail.
- How "lossy" the bucket transformation is allowed to be — visible to user or transparent?

---

### Phase 8 — Stage 2 (Hazırlık / Prepare) 🟡

**Why this comes before Stage 1 in the build order.** Stage 1 (the Library/Discovery view) makes no sense without a "basket" of selected tables that the user is preparing for use. Stage 2 (Hazırlık) is the screen where that preparation happens. Building Stage 2 first lets us validate the prepared-scope concept against the dashboard editor that already exists. Stage 1 is a discovery surface on top of Stage 2; it cannot be built standalone.

**The product flow we're building toward.**

```
Atölye                              Sunum
  ├── Keşif (Stage 1, Phase 9)        └── existing editor (Phases 1–6.5)
  └── Hazırlık (Stage 2, Phase 8)
```

The user enters via Atölye, builds a basket, refines its scope, then crosses into Sunum to compose.

**What changes.**

- **Basket state.** Server-side per-user-per-session collection of selected tables. Backed by S3 manifest fragment (so it survives reloads but not pod restarts).
- **Scope contract** (`scope.yaml`). Phase 7 lays the conceptual groundwork; Phase 8 materializes it as a UI-driven artifact.
  - **Pinned filters.** Locked at scope time; Stage 3 cannot mutate them. Example: `currency in [TRY, USD, EUR]`.
  - **Interactive filters.** Defaults + allowed-value ranges; Stage 3 user can adjust them inside dashboard filter widgets.
  - **Projections.** Per-table column selection (what to pull from Oracle).
  - **Joins.** Auto-proposed lookup joins from declared dimension tables, user-confirmed.
- **Hazırlık UI.** Single screen.
  - Top: basket contents (with row-count and projected-size estimates).
  - Middle: per-concept distribution histograms (e.g., `currency: TRY 62%, USD 24%, EUR 11%, other 3%`) so the user can see what they're filtering against.
  - Filter editor: pin or make interactive, set defaults.
  - Column projection picker: default LLM-proposed subset + "include all columns" override.
  - Lookup join suggestions: confirm/decline per join.
- **Cache / lazy routing.** From this phase forward, scope outcomes carry a per-table routing decision:
  - `cached`: table small enough (under size threshold post-scope) to load fully into DuckDB. Dashboard interactions are local.
  - `lazy`: table still large after scope; query Oracle on demand for each dashboard interaction. Slower but feasible for huge tables.
  - Threshold is byte-based (column count and dtype matter, not just row count). Default soft cap: ~500 MB.
- **Re-entry from Sunum.** A user in Sunum can return to Hazırlık to add a table or expand scope. Doing so re-runs the Oracle fetch for affected tables; the manifest is preserved.

**Stage 2 LLM role.** Refinement only. The LLM may explain concepts, suggest pinned-vs-interactive choices, recommend column subsets, and propose scope adjustments. **It does NOT propose adding tables** — that's Stage 1's job.

**Locked decisions.**

- Pinned vs interactive is a hard distinction; pinned filters are immutable in Stage 3.
- Lookup joins are user-confirmed, never silent.
- Routing decision (cached vs lazy) is per-table and visible to the user via a badge (`cached (320MB)` / `lazy (Oracle, est. 4.2GB)`).
- Column projection default is LLM-suggested narrow + "include all" checkbox for power users.
- Re-entering Hazırlık from Sunum is supported but warns the user that affected blocks may need re-resolution.

**Effort estimate.** ~4–6 weeks.

---

### Phase 9 — Stage 1 (Keşif / Tables Discover) 🟡

**What changes.**

- **Library (Keşif) — Tables tab.**
  - Left rail: category tree (department → topic → table) + upload area (CSV/XLSX).
  - Center: network diagram of tables. Nodes = tables, edges = declared joinable lookups. Colors = department. Edge thickness = relationship cardinality.
  - Right: LLM chat panel + selected-node detail view (documentation, concept bindings, sample rows).
- **Hover/click card on a table:**
  ```
  TRD_BRANCH_POSITION
  ────────────────────
  12,000 rows/day · partitioned: AS_OF_DATE
  Concepts:
    ✓ as_of_time (AS_OF_DATE, partitioned)
    ✓ currency  (CCY)
    ✓ maturity  (MATURITY_BUCKET)
    ✗ counterparty (not bound)
  Docs: 2 references
  ```
- **Catalog upload.** User uploads a CSV/XLSX. The system runs Phase 7's binding inference, presents a review UI: "we think `MAT_DAYS` is `maturity` via bucket-from-range; confirm?" User confirms or corrects, file enters catalog as a user-owned table.
- **Chat surface.** "I want to look at Eurobond positions" → LLM highlights 3-4 candidate tables on the graph; user clicks one to inspect.
- **"Add to basket" flow.** From any table card or graph node, push to the current Hazırlık basket. Then user clicks "Atölye'ye geç" to transition to Stage 2.

**Stage 1 LLM role.** Cold table discovery only — propose tables, explain what's in them, link documentation. Does NOT touch scope or binding (those are Stage 2 and Phase 7 territory).

**Effort estimate.** ~4–5 weeks.

---

### Phase 10 — Block Marketplace MVP 🟡

**Why this matters.** Phase 6.5 lets a user save a block for their own reuse. Phase 10 lets a user save a block for the team's reuse. This single change is the inflection point at which Treasury Studio becomes a knowledge platform rather than a dashboard tool — the team's collective analytical work starts to accumulate.

**What changes.**

- **"Save as template" action** in the Sunum editor. Promotes an in-presentation block to a shareable template. Required fields:
  - `documentation.purpose`
  - `documentation.business_context`
  - `documentation.decision_support`
  - `documentation.known_limitations` (recommended, not required)
  - `tags`
- **Template store.** `s3://<bucket>/templates/<team>/<id>/<version>.yaml`. Immutable per version, version bumping required for changes (Phase 6.5 versioning rules apply).
- **Soft binding to tables.** Template declares `required_concepts` (not tables). A template binds to a table at import time, with `preferred` + `alternatives` so teams using different physical tables for the same concept can still import.
  ```yaml
  requires:
    tables:
      - role: positions
        preferred: trd_branch_position
        alternatives: [trd_branch_position_v2]
        required_concepts: [as_of_time, branch, currency, position_amt]
  ```
- **Library — Blocks tab.** Phase 6.5.d's Library is extended to include templates from other teams (visibility permitting). List + filter (team, tag, viz type) + search across documentation. No clustering yet — that's Phase 11.
- **Import flow.** Choose template → reconciliation modal (concept bindings auto-matched against current dashboard's scope; conflicts surfaced) → confirm → block dropped into dashboard with appropriate variable_bindings.
- **Fork-at-import** semantics. Imported template becomes a regular block in the importer's dashboard; no upstream link. Author updates to the template do NOT propagate to importing dashboards.

**Locked decisions.**

- Template granularity = block-level (not multi-block; not dashboard-level). Phase 13+'s Process layer handles composition.
- Templates reference concepts, not raw column names. Without Phase 7, this constraint is enforced via `semantic_tag` from Phase 6.5.
- Lookups auto-add is opt-in (user confirms before scope changes).

**Dependencies.** Phase 6.5 (variable system + library) must be in production. Phase 7 (concept layer) makes template-to-table binding far more robust; if Phase 7 is delayed, templates work via `semantic_tag` matching but with weaker guarantees.

**Effort estimate.** ~4–6 weeks.

---

### Phase 11 — Discovery Layer ⚪

**Why.** Phase 10's library is a list. At 200+ templates, list browsing breaks down. Users need cluster-based navigation and LLM-mediated discovery.

**What changes.**

- **Embedding index.** Block documentation (purpose, business_context, decision_support, tags) embedded via Qwen embedding model. Index stored in vector DB (pgvector / Milvus / Qdrant — choice deferred until existing stack is reviewed).
- **Blocks tab cluster view.** 2D embedding scatter (UMAP/t-SNE). Colors = team, size = `usage_count`, hover = preview card. Spatial proximity = semantic similarity.
- **Cross-dimension LLM discovery.** Stage 1 chat learns to search across tables AND templates. "Show me branch performance" → LLM first checks templates (`branch_performance_v2` 87% match), proposes it; if user declines, falls back to tables.
- **Snapshot → template promotion.** One-click promotion from a finished snapshot into a candidate template (still requires user to fill documentation).
- **Usage signals.** `usage_count_30d`, `unique_users`, `last_validated_at`, `data_team_verified`, `user_rating`, `clone_then_modify_ratio`. Surfaced in template cards and used in LLM ranking.

**Effort estimate.** ~5–7 weeks.

---

### Phase 12 — Marketplace Polish + Production ⚪

- **Schema drift CI.** Nightly job dry-runs every template against the current schema; templates whose source tables changed get a `degraded` badge and notify the author.
- **RBAC.** Template visibility (`team | department | bank | public`). Public templates are discoverable but data access still gates execution.
- **Rating + comment.** Lightweight social signals on templates.
- **Performance.** Cluster diagram virtualization for 500+ template scale; lazy-loaded embeddings; cached search results.

**Effort estimate.** ~3–4 weeks.

---

### Phase 13 — Process Tagging ⚪

**Why.** With Phase 10–12 the team has accumulated templates and snapshots. Snapshots are individual instances; many of them follow recurring patterns. Phase 13 introduces explicit Process entities — named, owned, scheduled archetypes of analytical work. These are the bridge from "we have a lot of dashboards" to "we have a structured representation of what our department does."

**What changes.**

- **Process entity** (durable, in S3).
  ```yaml
  process:
    id: branch_morning_review
    name: "Pazartesi Şube İnceleme"
    department: retail_banking
    owner: ahmet.yilmaz
    cadence:
      type: weekly
      day_of_week: monday
      time: "09:00"
    consumed_by: [ops_committee]
    criticality: medium
    description: |
      Şube karşılaştırma + bölgesel agregat. Operasyon komitesinde sunulur.
    references:
      block_templates: [branch_position_kpi, branch_trend_line, region_heatmap]
      data_tables: [TRD_BRANCH_POSITION, DIM_BRANCH]
      concepts: [as_of_time, currency, maturity, branch, region]
  ```
- **Manual process creation.** Users tag snapshots: "this snapshot is an instance of `branch_morning_review`". Snapshot manifest gains `process_id` reference.
- **Library — Processes tab.** Departmental grouping; grid of process cards. Click a process to see: recent instances, used templates, recent snapshots.
- **Process card on Stage 1.** Discoverable from the chat: "what process tracks branch performance?" → LLM finds the process node.

**Effort estimate.** ~3–4 weeks.

---

### Phase 14 — Bottom-up Process Emergence ⚪

**Why.** Manual process creation is friction. Phase 14 makes process structure emerge from existing usage data — block documentation + snapshot history.

**What changes.**

- **Clustering job.** Background process clusters snapshots by `(blocks_used_set, cadence_pattern, department, tags)`. Output: candidate processes.
- **Documentation aggregation.** For each candidate cluster, LLM synthesizes a draft process description from the constituent blocks' documentation.
- **Review queue.** Data team / power users see candidate processes, accept / edit / reject. Accepted ones become real process entities.
- **Notification flow.** "Your department appears to run 3 weekly processes we hadn't formally documented. Review them?"

**Effort estimate.** ~4–6 weeks.

---

### Phase 15 — Knowledge Graph + Narrative Summarization ⚪

**The endgame vision.** With concepts, blocks, templates, snapshots, and processes all in a single graph, the LLM can answer queries that span the whole knowledge base.

**What changes.**

- **Unified graph queries.** "What's the state of branch performance this week?" → LLM identifies the process, finds its latest instance (snapshot), reads the underlying data, generates a 3-sentence summary with key numbers and deltas.
- **Cross-process narratives.** "Summarize this department's week" → LLM walks all processes for the department, identifies anomalies in their latest instances, produces a briefing.
- **Process-to-process dependencies.** Declared in process YAML (`consumed_by`, `feeds_into`); enables impact analysis ("if this block's source table is delayed, which processes are affected?").
- **Voice/text query interface.** The chat surface from Stage 1 becomes a department-wide query interface, not just a discovery aid.

**Effort estimate.** ~6–8 weeks; partially research-flavored.

---

## 5. Cross-cutting concerns

### 5.1 Forward compatibility

Every phase from 6.5 onwards is designed so artifacts produced in earlier phases remain valid. Migrations are scripted and idempotent. Specifically:

- Phase 6.5 blocks remain valid in Phase 7 (variables become concept-aware filter inputs).
- Phase 6.5 dashboards remain valid in Phase 8 (filters become interactive_filters in scope contract).
- Phase 10 templates remain valid in Phase 11 (they gain embeddings + usage signals; their core YAML doesn't change).
- Phase 13 processes are additive — existing snapshots without `process_id` simply lack the tag.

### 5.2 Performance budget

By Phase 12, a single user session may simultaneously involve:
- Multiple Oracle fetches (during scope changes)
- A 2 GB DuckDB session cache
- A few hundred KB of manifest state
- An open SSE channel for streaming updates
- Embedding queries against a vector DB

Performance targets:
- Stage 1 LLM response < 3s (chat in catalog).
- Stage 2 Hazırlık preview (per-concept histogram) < 2s.
- Sunum filter apply: cache hit < 200ms, subset filter < 1s, Oracle refetch ≤ data load time + 500ms overhead.
- Cluster diagram render (500 templates): < 2s initial, smooth pan/zoom.

### 5.3 RBAC and privacy

- Oracle access permissions are the source of truth for raw data. Treasury Studio never elevates.
- Templates: `visibility: team | department | bank | public`. Public visibility lets the template be discovered but does NOT grant data access; that's a separate gate.
- User concepts in user-scope; not visible to others unless promoted.
- Snapshots: by default visible to creator only; explicit share generates a unique link with optional team/department scope.

### 5.4 Observability

By Phase 12, we need:
- Per-block execution telemetry (resolved variables, query duration, cache state).
- Template usage metrics (imports per week, modifications post-import).
- Process instance tracking (which processes ran on schedule, which were skipped).
- LLM call audit (prompts, responses, cost). Especially important for Phase 11+ where LLM choices affect product behavior.

### 5.5 The "single source of truth" for filter semantics

Across all phases, exactly one entity owns "what does this filter mean":

- Phase 1–6: implicit (filter logic is per-block, ad hoc).
- Phase 6.5: `semantic_tag` (flat list, manual).
- Phase 7 onward: Concept Registry (hierarchical, versioned).

This is the most important architectural invariant. Every new feature must be expressible in concept terms; if not, the concept registry needs an addition first.

---

## 6. What's deliberately NOT on the roadmap

Decisions made about non-features, to prevent scope creep:

- **No collaborative editing.** Advisory locks per presentation. Real-time collaboration is out of scope indefinitely (Google Docs-style coediting is a separate product).
- **No custom CSS / theme overrides.** Tabler tokens. Period.
- **No PDF/Excel export.** Snapshots are read-only HTML; if users need PDF, they screenshot. Revisit if user demand justifies.
- **No mobile-first UX.** Desktop primary; mobile gracefully degrades to read-only via responsive collapse.
- **No multi-model LLM routing.** Single internal Qwen endpoint. No fallback to external APIs (corporate policy).
- **No "AutoML" features.** Treasury Studio is a composition platform, not a modeling platform.
- **No write-back to Oracle.** Read-only forever. Any write needs a separate ETL pipeline outside this platform.

---

## 7. Sequencing and parallelism

Most phases can overlap. Here's the recommended parallelization:

```
Time →

6.5.a ─┬─ 6.5.b ─┐
       │         ├─ 6.5.c ─┬─ 6.5.d ─┐
       └─────────┘         │         │
                           │         ├─ 7 ─┬─ 8 ─┬─ 9 ─┬─ 10 ─┬─ 11 ─┬─ 12
                           └─────────┘     │     │     │      │      │
                                           └─────┘     │      │      │
                                                       └──────┘      │
                                                                     └─ 13 ─ 14 ─ 15
```

- 6.5.a is the single blocker; nothing else in 6.5 starts without it.
- 6.5.b (table doc) and 6.5.c (dashboard filter) are parallelizable.
- Phase 7 (concept) starts as soon as 6.5 is in production. It's the longest single phase; many later phases benefit from it landing.
- Phases 8 and 9 are sequential within themselves (Hazırlık before Keşif) but can start before 7 fully lands if the scope contract is provisional.
- Phases 10–12 form a single marketplace track.
- Phases 13–15 form a single process emergence track and shouldn't start until the marketplace has accumulated meaningful data (~3 months of usage post-Phase 11).

---

## 8. Decision log (pending the right phase to land)

Open questions tracked here until they need to be resolved:

| # | Question | Earliest phase | Notes |
|---|---|---|---|
| 1 | Concept registry write path: YAML PR + redeploy, or hot reload from S3? | Phase 7 | Lean toward YAML for system/dept, DB for user |
| 2 | LLM constrained decoding: GBNF, llguidance, or outlines? | Phase 7 | Driven by Qwen llama.cpp stack |
| 3 | Vector DB choice: pgvector, Milvus, or Qdrant? | Phase 11 | Existing stack review needed |
| 4 | Process review queue UI: in-app or separate admin tool? | Phase 14 | Probably in-app once Library is mature |
| 5 | Schema drift CI: nightly batch or per-table-change webhook? | Phase 12 | Nightly is simpler, may be enough |
| 6 | Block template granularity expansion: ever allow multi-block templates? | Phase 10+ | Currently no — process layer handles composition |
| 7 | Free-form text variables: revisit demand? | Phase 7+ | Defer until users explicitly ask |

---

## 9. Revision notes

- **2026-05-21:** Initial roadmap consolidating Phase 6.5 spec, concept layer plan, two-stage workflow, marketplace, and process emergence direction agreed in May 2026 discussions.

Future revisions should append, not rewrite. When a phase ships, mark it ✅ and link to its retrospective doc.