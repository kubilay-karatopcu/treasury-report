# Phase 9 — Keşif (Stage 1 / Discover Layer)

**Status:** Spec draft
**Owner:** Kubilay Karatopçu
**Depends on:** Phases 1–7 (production), Phase 8 (Hazırlık + basket API)
**Followed by:** Phase 10 (Block Marketplace MVP)
**Language note:** Spec is in English for Claude Code consistency. User-facing strings are Turkish where called out.

---

## 1. Context

### 1.1 Why this phase exists

Phase 8 introduced Hazırlık (scope refinement) and a basket primitive — but populating that basket today still requires the user to know the table name and add it via the interim modal. That's a developer-grade UX. Real users — analysts, treasury staff — need a **discovery surface**: a way to browse the catalog, ask "what data exists for X?", visually see how tables relate, and add them to the basket without memorizing schema names.

Phase 9 builds that discovery surface as **Keşif** (Discover), the other half of Atölye alongside Hazırlık. Together they form the complete pre-composition workflow:

```
Atölye
  ├── Keşif    ─ catalog browse, LLM discovery, upload → builds basket
  └── Hazırlık ─ basket refinement, scope contract → builds DuckDB session
Sunum         ─ existing editor → builds the dashboard
```

This phase also covers **user-uploaded catalogs**: CSVs and Excel files the analyst drops in, processed through Phase 7's binding inference, that become first-class tables in the user-scoped catalog.

### 1.2 What it unlocks

- A first-time user can find a relevant table without knowing its name.
- LLM-mediated discovery: "Eurobond pozisyonlarına bakmak istiyorum" → candidate tables surfaced on the graph.
- Visual exploration: the network diagram makes the lookup-join landscape legible.
- Self-service uploads: analysts work with their own data alongside corporate data without IT involvement.
- The basket primitive (Phase 8) becomes population-rich; Hazırlık work becomes meaningful at any scale.

### 1.3 Out of scope

- Blocks tab in Atölye (lists block templates from the marketplace) — Phase 10–11.
- Processes tab in Atölye — Phase 13.
- Embedding-based semantic clustering of tables — Phase 11 (this phase keeps discovery to keyword + LLM-mediated).
- Marketplace-style sharing of user-uploaded tables across teams — Phase 12.
- Multi-user catalog editing — Phase 9 keeps catalog as data-team-owned for corporate tables and user-owned for uploads.
- Realtime collaboration on basket selection — last-write-wins.
- Editing existing binding declarations on corporate catalog tables from this UI — that's a data-team admin tool, separate.

### 1.4 Relationship to existing system

Phase 8 produced:

- Scope contract YAML and the routing decision.
- The basket primitive (server-side per-presentation list of tables targeted for inclusion in scope).
- A basket-write API (per Phase 8 spec §9.1, exposed for use by Keşif).
- A table catalog metadata source (loaded from `s3://<bucket>/table_docs/<schema>/<table>.yaml` per Phase 6.5.b shape).
- The concept registry and column bindings (Phase 7), powering the inference pipeline.

Phase 9 adds:

- Atölye top-level navigation entry (currently the app drops into Sunum).
- Keşif main screen with three sub-surfaces: tree, network, detail card.
- LLM chat panel for cold table discovery.
- Catalog upload pipeline (CSV/XLSX → binding inference → user confirmation → user catalog entry).
- User-scoped catalog store (`uploads/<user>/<table_id>`) alongside the existing corporate catalog.

Existing presentations continue to work; users can enter directly into Sunum as before. Keşif is a new entry point, not a rewrite.

---

## 2. Data Model

### 2.1 Catalog entries

Two storage shapes, unified at the read layer.

**Corporate tables** — extended table doc schema from Phase 6.5.b, stored at `s3://<bucket>/table_docs/<schema>/<table>.yaml`. No change in this phase; Keşif reads them.

**User-uploaded tables** — new shape, stored at `s3://<bucket>/uploads/<user_sicil>/<upload_id>/` with three files:

```
uploads/A16438/u_abc123/
  ├── doc.yaml          # same schema as corporate table_docs (extended Phase 6.5.b shape)
  ├── data.parquet      # the actual data, materialized from the upload
  └── meta.yaml         # upload-specific metadata (filename, upload_at, row count, etc.)
```

`doc.yaml` example for a user upload:

```yaml
table: u_abc123
schema: __user_A16438__         # special schema marker for user catalogs
description: |
  User-uploaded: 2025_q4_branch_targets.xlsx
  Original filename: 2025_q4_branch_targets.xlsx
  Uploaded by: A16438 on 2026-06-22
partition_column: null
estimated_total_rows: 1247
source: user_upload

columns:
  branch_id:
    type: VARCHAR
    description: "Şube kodu"
    filterable: true
    filter_role: dimension
    concept: branch
    binding_inferred_by: regex_match
    binding_confirmed_by: A16438
    binding_confirmed_at: 2026-06-22T14:30:00Z

  target_2025_q4:
    type: NUMBER
    description: "Q4 2025 hedef tutarı (TL)"
    filterable: false
    aggregatable: true
    concept: null
    binding_inferred_by: null

  effective_date:
    type: DATE
    description: "Hedef yürürlük tarihi"
    filterable: true
    filter_role: time_axis
    concept: as_of_time
    binding_inferred_by: dtype_match
    binding_confirmed_by: A16438
    binding_confirmed_at: 2026-06-22T14:30:00Z
```

Key fields:

- `schema: __user_A16438__` — sentinel value; routing layer recognizes this and reads from parquet, not Oracle.
- `binding_inferred_by` — provenance of each binding. Values: `regex_match`, `dtype_match`, `sample_pattern`, `llm_proposed`, `user_authored`.
- `binding_confirmed_by` + `binding_confirmed_at` — the user has reviewed and accepted (or edited) the inferred binding.

`meta.yaml`:

```yaml
upload:
  id: u_abc123
  user: A16438
  original_filename: 2025_q4_branch_targets.xlsx
  original_size_bytes: 84320
  format: xlsx
  uploaded_at: 2026-06-22T14:28:00Z
  parsed_at: 2026-06-22T14:28:30Z
  row_count: 1247
  column_count: 3
  sheet_used: Sheet1            # for xlsx; null for csv
  inference_status: confirmed   # one of: proposed | confirmed | partial
```

### 2.2 Unified catalog read API

A single endpoint surface exposes both corporate and user catalogs:

```
GET /catalog?scope=corporate                 → corporate tables only
GET /catalog?scope=user                      → current user's uploads only
GET /catalog?scope=all                       → both (default)
GET /catalog?q=<search>                      → text search across descriptions
GET /catalog?dept=<dept>                     → filter by department
GET /catalog?concept=<tag>                   → tables that bind the given concept
GET /catalog/<schema>/<table>                → single table detail
```

Response shape is uniform; consumers don't need to know if a table is corporate or user-uploaded.

```json
{
  "tables": [
    {
      "schema": "ODS_TREASURY",
      "name": "TRD_BRANCH_POSITION",
      "source": "corporate",
      "department": "treasury",
      "description": "Şube bazlı günlük pozisyon snapshot'ı.",
      "concepts_bound": ["as_of_time", "branch", "currency", "maturity"],
      "concepts_unbound": ["counterparty"],
      "row_count_estimate": 12000,
      "row_count_basis": "daily",
      "partition_column": "AS_OF_DATE",
      "doc_url": "/catalog/ODS_TREASURY/TRD_BRANCH_POSITION"
    },
    {
      "schema": "__user_A16438__",
      "name": "u_abc123",
      "source": "user_upload",
      "department": null,
      "description": "User-uploaded: 2025_q4_branch_targets.xlsx",
      "concepts_bound": ["branch", "as_of_time"],
      "concepts_unbound": [],
      "row_count_estimate": 1247,
      "row_count_basis": "total",
      "partition_column": null,
      "doc_url": "/catalog/__user_A16438__/u_abc123",
      "original_filename": "2025_q4_branch_targets.xlsx",
      "uploaded_at": "2026-06-22T14:28:00Z",
      "inference_status": "confirmed"
    }
  ],
  "total": 2,
  "facets": {
    "departments": {"treasury": 1, "risk": 0, ...},
    "concepts": {"as_of_time": 2, "branch": 2, ...}
  }
}
```

Facets enable left-rail filtering without re-querying.

### 2.3 Relationships (edges in the network diagram)

Edges are computed at catalog read time, not stored persistently. Three edge types, ranked by strength:

1. **Declared lookup** — most reliable. A table doc declares a `lookup` field on a column pointing to another table. From Phase 6.5.b shape:
   ```yaml
   columns:
     BRANCH_ID:
       lookup:
         table: DIM_BRANCH
         key: BRANCH_ID
         display: BRANCH_NAME
   ```
   Becomes an edge `TRD_BRANCH_POSITION → DIM_BRANCH` with kind `lookup`.

2. **Shared concept** — weaker. Two tables both bind the same concept on at least one column. This is the "they're related but you have to figure out how" signal. Edges of this kind render thinner and dashed.

3. **Manually declared** — fallback. A `related_tables` array in the table doc (rarely used; exists for cases too odd for the above). Renders as a labeled edge.

Multiple edges between two tables collapse into one in the rendered graph; the strongest type wins for styling.

### 2.4 Network graph payload

The frontend gets a graph payload from `GET /catalog/graph`:

```json
{
  "nodes": [
    {
      "id": "ODS_TREASURY.TRD_BRANCH_POSITION",
      "label": "TRD_BRANCH_POSITION",
      "department": "treasury",
      "source": "corporate",
      "concepts": ["as_of_time", "branch", "currency", "maturity"],
      "usage_score": 0.78
    },
    ...
  ],
  "edges": [
    {
      "source": "ODS_TREASURY.TRD_BRANCH_POSITION",
      "target": "ODS_TREASURY.DIM_BRANCH",
      "kind": "lookup",
      "label": "BRANCH_ID",
      "strength": 1.0
    },
    {
      "source": "ODS_TREASURY.TRD_BRANCH_POSITION",
      "target": "ODS_TREASURY.FX_POSITION_DAILY",
      "kind": "shared_concept",
      "concepts": ["currency", "as_of_time"],
      "strength": 0.4
    }
  ],
  "clusters": [
    {"id": "c_treasury", "label": "Treasury", "node_ids": [...]},
    {"id": "c_risk", "label": "Risk", "node_ids": [...]}
  ]
}
```

Clusters drive semantic zoom: at the most zoomed-out level, individual nodes collapse into department clusters; zooming in expands them.

`usage_score` is a 0–1 normalized score from recent basket additions across all users in the past 30 days. Phase 9.b uses it for node sizing; Phase 11 supersedes with a richer signal.

### 2.5 Basket API contract with Hazırlık

Phase 8's basket API surface (per its spec §9.1) supports:

```
GET    /presentations/<pid>/basket
POST   /presentations/<pid>/basket           # body: {table_refs: [{schema, name}, ...]}
DELETE /presentations/<pid>/basket/<alias>
```

Phase 9 calls these from the Keşif UI. Critically:

- **Adding a table to the basket does NOT create a presentation.** A basket exists transiently for a "draft" presentation that materializes only when the user clicks "Atölye'ye geç" (Hazırlık) and confirms.
- **Until then, the basket lives at a special draft-presentation ID** keyed to the user's session: `pid = draft_<user_sicil>_<timestamp>`. Multiple drafts allowed; current draft tracked in user prefs.

This means a user can browse Keşif, add 3 tables, leave the page, come back later, and the basket is still there. Old drafts (>7 days) garbage-collected.

### 2.6 Identifier rules

- `upload_id`: prefix `u_`, 8 chars hex, user-unique.
- User schema marker: `__user_<sicil>__`. Validator rejects any attempt to write to this schema from the corporate path.
- Draft presentation ID: prefix `draft_`, user-scoped.

---

## 3. Upload Pipeline

### 3.1 Supported formats

- CSV — RFC 4180 compliant. Encoding sniffed via `chardet`; UTF-8 and Windows-1254 (common for Turkish) primary targets.
- XLSX — first sheet by default; user can pick another in the review UI.
- File size cap: 50 MB. Files above are rejected with a clear error suggesting alternative paths (CSV split, contact data team).
- Row cap: 5 million rows post-parse. Above → rejected.
- Column cap: 200 columns. Above → rejected.

XLS (legacy binary) is NOT supported. User instructed to re-save as XLSX.

### 3.2 Upload flow

1. User drops a file in the upload zone.
2. POST `/catalog/upload` with multipart form data.
3. Server: validate format and size, write to staging at `/tmp/uploads/<user>/<upload_id>/raw.<ext>`.
4. Parse to a pandas DataFrame (CSV via `pd.read_csv` with encoding sniffing; XLSX via `openpyxl`).
5. Run binding inference on each column (§3.3).
6. Persist staging artifacts: `staging.parquet`, `inference.yaml`.
7. Return `{upload_id, inference_summary, review_url}` to the client.
8. Client redirects to the review UI at `/atolye/kesif/upload/<upload_id>/review`.
9. User reviews bindings, edits if needed, confirms.
10. On confirm: write `doc.yaml`, `data.parquet`, `meta.yaml` to durable S3 user-catalog location. Staging cleaned up.

If the user navigates away without confirming, staging artifacts are kept for 24 hours then garbage-collected.

### 3.3 Binding inference pipeline

For each column, run inference steps in order; first hit wins:

**Step 1 — Regex on column name.**

Loaded from `presentations/catalog/inference/regex_rules.yaml`:

```yaml
rules:
  - concept: as_of_time
    patterns: ['(?i)as[_-]?of[_-]?(date|time)', '(?i)snapshot[_-]?date', '(?i)tarih$']
  - concept: trade_time
    patterns: ['(?i)trade[_-]?date', '(?i)islem[_-]?tarih']
  - concept: currency
    patterns: ['(?i)^c?cy$', '(?i)currency', '(?i)para[_-]?birim', '(?i)doviz']
  - concept: maturity
    patterns: ['(?i)maturity', '(?i)tenor', '(?i)vade']
  - concept: branch
    patterns: ['(?i)branch[_-]?(id|code)?', '(?i)sube[_-]?(id|kod)?']
  - concept: counterparty
    patterns: ['(?i)counterparty', '(?i)karsi[_-]?taraf', '(?i)bank[_-]?(id|swift|bic)']
  - concept: region
    patterns: ['(?i)region', '(?i)bolge']
```

Match → propose concept with provenance `regex_match`. Confidence: 0.9.

**Step 2 — Dtype check + sample values.**

If regex misses, look at dtype and sample 50 unique values:

- Dtype is date/datetime → propose `as_of_time` (most common; user can adjust to `trade_time` etc.). Provenance `dtype_match`. Confidence: 0.6.
- Dtype is string + sample matches `^[A-Z]{3}$` and all values in known currency ISO codes → `currency`. Provenance `sample_pattern`. Confidence: 0.85.
- Dtype is string + sample matches maturity tokens like `1M, 3M, 6M, 1Y` → `maturity`. Provenance `sample_pattern`. Confidence: 0.85.
- Dtype is int and column name contains `id` → `branch` or `counterparty` candidate (low confidence, will defer to step 3 or leave unbound).

**Step 3 — LLM fallback.**

Triggered only if steps 1 and 2 don't yield a binding above 0.5 confidence. Bundle: column name + 20 sample values + the list of valid concepts. Ask the LLM (Qwen, structured JSON output enforced) to pick one or `null`. Provenance `llm_proposed`. Confidence as returned by LLM (typically 0.4–0.7).

**Step 4 — Default to unbound.**

If LLM also returns null, column is left without a concept binding. The user can manually assign in the review UI.

The inference result for one column:

```yaml
column: branch_id
proposed_concept: branch
provenance: regex_match
confidence: 0.9
candidates_considered:
  - {concept: branch, confidence: 0.9, by: regex_match}
  - {concept: counterparty, confidence: 0.3, by: sample_pattern}
sample_values: [B0001, B0002, B0011, B0042, B0103]
```

Candidates are surfaced in the review UI so the user can switch among them.

### 3.4 Review UI flow

Three-pane layout:

- **Left:** column list. Each column shows its proposed concept (or "unbound") with a colored chip indicating provenance and confidence.
- **Center:** for the selected column — name, dtype, sample values, proposed concept, alternative candidates, "Unbound" option, "Other concept..." dropdown.
- **Right:** summary panel — total columns, bound count, unbound count, "Confirm" button.

User actions:

- Switch proposed concept by clicking an alternative candidate.
- Pick a concept from the full registry via the "Other concept..." dropdown.
- Mark a column as `filterable: false` (e.g., audit timestamps).
- Override `filter_role` (`time_axis | dimension | measure_threshold`).
- Edit the table description.
- Pick a different XLSX sheet (re-runs parsing if changed).
- Click "Confirm" → §3.2 step 10.

Confirmation refuses if:

- Any column proposed `as_of_time` is marked unbound and no other time concept is bound (a table with no time concept binding will be hard to filter — warn aggressively but allow override).
- The table has zero filterable columns (warn but allow).

### 3.5 Re-running inference

A confirmed upload can be re-inferred:

- Useful if the concept registry changed (new concepts added).
- Useful if the user originally accepted poor inference and now wants to re-do.

UI affordance: "Re-run inference" button on the table detail card. Spins up a new staging round; user reviews the new proposals; on confirm, writes a new version of `doc.yaml` (file `doc_v<N>.yaml`; latest pointed to by `doc.yaml` symlink-style or via a `latest_version` field in `meta.yaml`).

### 3.6 Reverting / deleting an upload

User can delete their own uploads from the detail card. Soft delete first (`deleted: true` in `meta.yaml`); hard delete after 30 days via a cleanup cron.

Soft-deleted uploads:

- Don't appear in catalog reads.
- Don't appear in network graph.
- Can be restored from a "Trash" view in user prefs.

If a soft-deleted upload is referenced by a draft basket or a saved scope contract, the reference is preserved but the table card shows a "deleted source" warning. Hard delete blocked if referenced; the user is asked to confirm or first clean up the referencing scope.

---

## 4. Network Graph Rendering

### 4.1 Library choice

Two front-runners; decision in the 9.b spike (open question):

- **Cytoscape.js** — battle-tested, declarative, good performance with 200+ nodes. Layouts: cola, dagre, klay. Likely choice.
- **D3-force** — more flexible but more code; force-directed layout free.

Either way: the graph payload (§2.4) is library-agnostic, so swap cost is low if we change later.

### 4.2 Semantic zoom

Three zoom levels:

- **Macro (zoomed out):** each cluster shown as a single big node, labeled with department name. Cluster size = node count. Inter-cluster edges aggregated.
- **Meso (mid-zoom):** clusters expanded; individual tables visible. Edges within a cluster shown; cross-cluster edges aggregated to show "N connections" labels.
- **Micro (zoomed in):** all edges expanded; labels readable; concept tags visible on each node.

Zoom transitions are smooth (animated layout interpolation). User can also force a level via toolbar buttons.

### 4.3 Interactivity

- **Hover a node:** highlight directly connected nodes, dim the rest, show a tooltip with name + concept summary.
- **Click a node:** open the detail card in the right panel (§4.4). Optionally select for basket (single-click selects + opens card; double-click adds to basket).
- **Shift-click multiple nodes:** select multi-tables for basket.
- **Click an edge:** show edge info — kind, shared concepts, lookup column. No basket action.
- **Right-click a node:** context menu with "Add to basket", "Open detail", "Hide from view", "Mark as favorite".

### 4.4 Detail card

Right side panel. Renders for the currently selected node:

```
TRD_BRANCH_POSITION                              [⊕ Add to basket]
─────────────────────────────────────────────────────────────────
ODS_TREASURY · Treasury · 12,000 rows/day
Partition: AS_OF_DATE

Description
─────────────
Şube bazlı günlük pozisyon snapshot'ı. ETL gün sonunda doluyor;
yeni gün verisi T+1 09:00 itibarıyla mevcut olur.

Concepts
─────────
✓ as_of_time     (AS_OF_DATE, partitioned)
✓ currency       (CCY, identity binding)
✓ maturity       (MATURITY_BUCKET, identity binding)
✓ branch         (BRANCH_ID, identity binding)
✗ counterparty   not bound

Lookups
─────────
→ DIM_BRANCH (BRANCH_ID → BRANCH_NAME)

Columns (8)
─────────
AS_OF_DATE, BRANCH_ID, CCY, MATURITY_BUCKET,
NET_POSITION, GROSS_INFLOW, GROSS_OUTFLOW, CREATED_AT

Documentation
─────────
[Link to doc.md if available]

Related processes (Phase 13+)
─────────
- (empty in Phase 9)

[⊕ Add to basket]    [⌕ Show in chat]    [⚲ Focus graph]
```

### 4.5 Filters and search (left rail)

- **Department:** multi-select. Affects both graph (non-matching nodes dimmed) and tree.
- **Concept:** multi-select. Show only tables binding selected concepts.
- **Source:** corporate / user uploads / favorites.
- **Search bar:** full-text over names + descriptions. Live-filters the tree; pulses matching graph nodes.

### 4.6 Performance targets

- Initial load (200 tables): graph render < 2s; tree render < 500ms.
- Pan/zoom: 60fps.
- Search debounced at 200ms.
- Node hover highlight latency < 100ms.

200+ tables is the v1 scale assumption. At 500+, semantic zoom becomes essential; at 1000+, we'd need additional virtualization (backlog).

---

## 5. Keşif LLM Chat

### 5.1 Role

Stage 1 LLM is a **discovery assistant**. It proposes tables — nothing else.

### 5.2 Context provided to the LLM

System prompt contains:

- Full catalog summary as a structured list (table name + description + concepts bound). Limited to ~150 most-recently-touched + ~50 most-used tables to keep token budget in check.
- The current user's department.
- The current draft basket (tables already added).
- Recent chat history (last 10 turns).

User uploads ARE included (filtered to the current user). Other users' uploads are NOT visible.

### 5.3 Output contract

```json
{
  "explanation": "Şube performansına bakmak için bu tabloları öneriyorum...",
  "proposals": [
    {
      "schema": "ODS_TREASURY",
      "name": "TRD_BRANCH_POSITION",
      "rationale": "Şube bazlı net pozisyon ve günlük akışlar.",
      "match_score": 0.92,
      "suggested_companion": "DIM_BRANCH"
    },
    {
      "schema": "ODS_TREASURY",
      "name": "TRD_BRANCH_PROFITABILITY",
      "rationale": "Şube bazlı karlılık metrikleri.",
      "match_score": 0.78,
      "suggested_companion": null
    }
  ],
  "highlight_graph_node_ids": [
    "ODS_TREASURY.TRD_BRANCH_POSITION",
    "ODS_TREASURY.TRD_BRANCH_PROFITABILITY",
    "ODS_TREASURY.DIM_BRANCH"
  ]
}
```

The chat panel renders the explanation as text. Proposals render as cards below it, each with "Add to basket" and "Show details" buttons. `highlight_graph_node_ids` causes those nodes to pulse on the graph; the rest dim briefly.

### 5.4 What Stage 1 LLM does NOT do

- Does not modify the basket directly. Every change goes through user click.
- Does not write SQL.
- Does not touch scope, filters, projections — those are Stage 2 (Phase 8) territory.
- Does not propose blocks or templates — those are Phase 11.
- Does not propose joins — those are Hazırlık (Phase 8.c).

### 5.5 Failure modes

- LLM returns invalid JSON → existing Phase 3 retry-with-feedback (one retry). Second failure → graceful message "Bir sorun oldu, tekrar dener misiniz?".
- LLM proposes a table that doesn't exist in the catalog → silently dropped from the proposals list; logged for prompt tuning.
- LLM proposes a table the user lacks Oracle access to → still surfaced with a "permission needed" badge; user can request access via a separate workflow (out of scope here).

---

## 6. UI Surfaces

### 6.1 Atölye top-level navigation

A new top-level nav entry **Atölye** appears in the app header. Clicking it opens Keşif (Tables tab) by default. Atölye has a sub-nav with three tabs:

- **Tables** (this phase)
- **Blocks** (Phase 10, currently a placeholder "Yakında" tab)
- **Processes** (Phase 13, placeholder)

Existing top-level entries (My Presentations, etc.) remain.

### 6.2 Keşif main screen layout

Route: `/atolye/kesif`.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Atölye   [Tables*] [Blocks] [Processes]      [⊕ Yeni Sunum]         │
├──────────────────────────────────────────────────────────────────────┤
│ Filtreler      │                                       │  Detay      │
│ ───────────    │                                       │  ─────      │
│ Departman      │                                       │  [empty     │
│ ☐ Treasury     │                                       │   until a   │
│ ☐ Risk         │              Network graph            │   table is  │
│ ☐ Bilanço      │              with semantic            │   selected] │
│                │                  zoom                  │              │
│ Concept        │                                       │              │
│ ☐ currency     │                                       │              │
│ ☐ as_of_time   │                                       │  Sepet (0)  │
│ ☐ maturity     │                                       │  [empty]    │
│                │                                       │              │
│ Source         │                                       │  [Atölye'ye │
│ ☐ Corporate    │                                       │   geç →]    │
│ ☐ User uploads │                                       │              │
│ ☐ Favorites    │                                       │              │
│                │                                       │              │
│ [Search bar]   │                                       │              │
│                │                                       │              │
│ Tablo ağacı    │                                       │              │
│ - Treasury     │                                       │              │
│   - TRD_...    │                                       │              │
│   - FX_...     │                                       │              │
│ - Risk         │                                       │              │
│   - ...        │                                       │              │
│                │                                       │              │
│ [⊕ Upload]     │                                       │              │
└──────────────────────────────────────────────────────────────────────┘

Bottom drawer (collapsible):
[ Keşif Chat ]
```

The bottom drawer is the LLM chat. User clicks to expand; can be docked left or bottom.

### 6.3 Basket panel (right side)

When tables are added to the basket, the right panel switches mode:

```
Detay
─────
TRD_BRANCH_POSITION
[... detail card content ...]

Sepet (3)
─────
1. TRD_BRANCH_POSITION   [×]
2. DIM_BRANCH            [×]
3. FX_POSITION_DAILY     [×]

Concepts in basket:
  as_of_time, branch, currency, maturity, trade_time

[Hazırlık'a geç →]
```

"Hazırlık'a geç" materializes the draft basket as a new presentation and redirects to Hazırlık (Phase 8) for scope refinement.

### 6.4 Upload zone

Left rail bottom: a drop zone with "Dosya yükle" button. Accepts drag-and-drop or click-to-pick. On drop:

- File validation runs (§3.1).
- Upload + parse + inference (§3.2 steps 1–7).
- Redirect to review UI (§3.4) full-screen.
- On confirm: redirect back to Keşif with the new upload visible in the tree under a "My uploads" section.

### 6.5 Upload review UI

Full-screen modal at `/atolye/kesif/upload/<upload_id>/review`. Layout per §3.4.

### 6.6 User uploads section in tree

In the left rail tree, a new section "Yüklemelerim" (My uploads) appears above the department list. Shows the user's confirmed uploads. Each entry has:

- Filename + upload date.
- Concept binding summary (X bound, Y unbound).
- Click → node selected in graph + detail card opens.

User uploads also appear as nodes in the graph, in a "My uploads" cluster that floats near the user's department cluster.

### 6.7 Chat panel

Drawer at the bottom of Keşif. Collapsed by default; expand reveals a standard chat interface with:

- Message thread.
- Input box.
- "Send" button.
- "Clear chat" link.

When the LLM proposes tables, the proposal cards appear inline in the chat thread (below the LLM's explanation text), and the graph nodes pulse simultaneously.

---

## 7. Migration Strategy

### 7.1 Existing presentations

Users continue to enter Sunum directly via the existing "New presentation" flow. Keşif is an additional entry point, not a replacement.

A new presentation can be started in two ways:

- **From Atölye:** Keşif → Hazırlık → Sunum (the new guided flow).
- **From the existing "New presentation" button:** drops into Sunum without a scope contract (Phase 8 backwards-compat behavior).

Over time, the team is encouraged to use Atölye; the legacy flow stays available indefinitely.

### 7.2 Existing user data

No migration needed. No prior user-upload feature existed. The schema marker `__user_<sicil>__` is new; corporate tables continue using their real schemas.

### 7.3 Existing table docs

No changes to corporate table docs in this phase. The extended Phase 6.5.b shape is what Keşif reads. If a corporate doc is missing the extended fields, the catalog API surfaces the minimal info available (description, columns); concepts column shows "no bindings declared".

### 7.4 Concept registry growth from uploads

User uploads may push for new concepts (e.g., an analyst uploads data with a `cohort` column that doesn't map to any existing concept). Phase 9 does NOT allow user-defined concepts in v1; the column is left unbound. A backlog item ("user-scoped concept extensions") covers the eventual mechanism — likely in Phase 11–12 timeframe.

For now, an unbound column means: the column exists, can be displayed, can be aggregated, but can't participate in cross-table filters.

---

## 8. Implementation Sub-Phases

Each sub-phase ships independently. Acceptance criteria in §10.

### 8.a — Catalog API and detail card

**Goal:** unified catalog read API + the detail-card UI. No network graph yet. Tree-based browsing only.

Deliverables:

- Unified catalog read endpoints (`GET /catalog`, `GET /catalog/<schema>/<table>`, `GET /catalog/graph`).
- Server-side catalog loader merging corporate `table_docs/` + user uploads.
- Atölye nav entry + Tables tab.
- Left-rail tree (department-grouped, with search).
- Right-side detail card.
- Basket panel UI calling Phase 8's basket API.
- "Hazırlık'a geç" transition.

### 8.b — Network graph

**Goal:** the network diagram is the primary discovery surface.

Deliverables:

- Graph library integration (Cytoscape.js spike + decision).
- Edge computation logic (declared lookups + shared concepts + manual).
- Semantic zoom (macro/meso/micro layers).
- Hover / click / right-click interactions.
- Multi-select via shift-click.
- Graph layout caching (computed once per catalog snapshot, served from cache; invalidated on catalog change).
- Performance targets met for 200-table scale.

### 8.c — LLM chat (Keşif)

**Goal:** the chat panel proposes tables based on user requests.

Deliverables:

- Chat drawer UI.
- Stage 1 LLM system prompt (`presentations/prompts/discover.txt`).
- Catalog summary builder for prompt context (token-budgeted).
- LLM client wrapper enforcing the §5.3 JSON contract (one retry on invalid JSON).
- Proposal cards inline in chat thread.
- Graph node highlighting synced with LLM proposals.
- Clear chat affordance.

### 8.d — Catalog upload pipeline

**Goal:** users can upload CSV/XLSX, review bindings, persist to their user catalog.

Deliverables:

- Upload endpoint with format/size validation.
- Parser (CSV + XLSX with encoding sniffing).
- Binding inference pipeline (regex + dtype + sample + LLM fallback).
- Staging artifact persistence.
- Review UI (full-screen modal).
- Confirmation → durable persistence to `uploads/<user>/<upload_id>/`.
- "Re-run inference" affordance.
- Soft delete + restore + hard delete cleanup cron.
- User uploads visible in catalog, tree, graph.

### Sub-phase ordering

```
8.a (catalog API + detail card + tree)
  ↓
8.b (network graph)
  ↓                              8.d (upload pipeline) — parallel with 8.b or after
8.c (LLM chat)
```

8.a is the foundation. 8.b and 8.d can parallelize after 8.a. 8.c after at least 8.a (needs catalog data) but can overlap with 8.b or 8.d.

---

## 9. Forward-Compat with Future Phases

### 9.1 Phase 10 (Marketplace MVP)

Atölye's Tables tab is joined by a Blocks tab. The unified catalog read API can extend to a block-template catalog with the same shape (`source: marketplace`). The current `placeholder` Blocks tab is replaced.

### 9.2 Phase 11 (Discovery layer)

The Keşif chat learns to query templates AND tables. The §5.3 output contract is extended with a `template_proposals` field (additive; current consumers ignore unknown fields). The 2D embedding cluster view becomes another rendering of the network graph (different layout backend, same node/edge data).

### 9.3 Phase 13 (Process tagging)

The detail card's "Related processes" section (currently empty) is populated. Hover-over on a node reveals process membership.

### 9.4 User-scoped concept extensions

Backlog. When implemented, the upload review UI gains a "Define new concept" option that creates a user-scoped concept (stored in `concepts/users/<sicil>.yaml`). Validators allow user-scoped concepts in user-uploaded table bindings only, never in corporate table bindings.

---

## 10. Acceptance Criteria

### 10.a — Catalog API and detail card

- `GET /catalog` returns a unified list including both corporate tables (from `table_docs/`) and the current user's uploads (none in this sub-phase's fixtures).
- `GET /catalog?dept=treasury` filters correctly.
- `GET /catalog?concept=currency` returns only tables binding the currency concept.
- `GET /catalog?q=branch` matches description + name text.
- `GET /catalog/<schema>/<table>` returns full detail for an existing table; 404 for missing.
- Atölye nav entry exists; clicking it lands on Keşif Tables tab.
- Left-rail tree groups corporate tables by department; user uploads section visible (empty in this sub-phase).
- Selecting a table in the tree opens the detail card with all fields from §4.4.
- Basket panel reflects Phase 8's basket API state; "Add to basket" / "Remove" buttons work.
- "Hazırlık'a geç" materializes a draft into a real presentation and redirects to Hazırlık.

### 10.b — Network graph

- `GET /catalog/graph` returns a payload matching the §2.4 shape.
- Cytoscape.js (or chosen library) renders 200 nodes within 2s on a representative laptop.
- Edges of kind `lookup` render solid; kind `shared_concept` renders dashed and thinner.
- Hover highlights neighbors and dims non-neighbors.
- Click selects a node and opens its detail card.
- Shift-click multi-selects; multi-select drives a "Add N to basket" affordance.
- Semantic zoom: at macro level, clusters are visible; at micro level, all individual nodes and edges.
- Smooth pan/zoom maintained at 60fps on a graph of 200 nodes / 500 edges.
- Graph layout cached and invalidated when catalog changes.

### 10.c — LLM chat

- Stage 1 chat returns valid JSON matching §5.3 for representative requests (smoke test, not exact text match).
- Invalid JSON is retried once with feedback; second failure shows graceful error.
- Proposal cards appear inline in the chat thread.
- Clicking "Add to basket" on a proposal correctly invokes Phase 8's basket API.
- Graph nodes referenced in `highlight_graph_node_ids` pulse for ~3 seconds and then return to normal.
- Catalog summary in the prompt respects token budget (configurable; default 8k tokens).
- Proposed tables that don't exist in the catalog are silently dropped from the rendered list; logged.

### 10.d — Catalog upload pipeline

- CSV upload (UTF-8) of 1k rows × 5 columns parses and runs inference in < 10s.
- XLSX upload of similar size parses correctly; default sheet picked.
- Size cap (50 MB) enforced; oversize rejected with clear error.
- Row cap (5M) and column cap (200) enforced.
- Inference results match expected concepts for the fixture upload (see fixtures).
- Review UI lets the user switch proposed concept, mark unbound, edit description.
- Confirmation writes `doc.yaml`, `data.parquet`, `meta.yaml` to `uploads/<user>/<upload_id>/`.
- Upload appears in Keşif tree under "Yüklemelerim" and in the graph in a "My uploads" cluster.
- Detail card for an uploaded table renders correctly with provenance info on bindings.
- Re-run inference produces a new `doc_v<N>.yaml`; the latest version is what catalog reads return.
- Soft delete hides the upload from catalog; restoring brings it back.
- Hard delete blocked if upload is referenced by a draft basket; the warning UI lists the references.

---

## 11. Open Questions / Backlog

- **Graph library choice — Cytoscape.js vs alternatives.** Decide in 8.b spike. Document the decision.
- **Cross-team upload visibility.** Currently uploads are user-scoped only. Backlog: team-scoped uploads ("anyone in Treasury can see my upload"). Likely Phase 12.
- **Upload size cap.** 50 MB is the v1 cap. If users routinely hit this, raise via S3 multipart upload (currently single PUT).
- **PDF / DOCX uploads.** Not for table data, but for tables-extracted-from-PDF use cases. Far backlog; LLM-mediated extraction unproven for our domain.
- **Upload-driven concept proposals.** When several users repeatedly create unbound columns with similar names/patterns, propose a new concept to the data team. Cross-phase backlog.
- **Catalog refresh cadence.** Currently the catalog reads from `table_docs/` on every request. Performance-acceptable at 200 tables; add a 30-second TTL cache if 500+ scale is reached.
- **Favorites.** Per-user "favorite" tables that show in a fast-access list. Stub in 8.a (data model), full UI in 8.b.
- **Permissions enforcement on catalog read.** Currently we surface all corporate tables to all authenticated users. Filtering by Oracle access permissions is a backlog item; for now, tables a user can't access still appear in the catalog but block execution will fail (existing Phase 4/6.5 behavior).

---

## 12. Glossary

- **Atölye** — the combined workspace umbrella covering Keşif + Hazırlık (+ future tabs).
- **Keşif** — Stage 1 Discover screen. The subject of this spec.
- **Hazırlık** — Stage 2 Prepare screen (Phase 8).
- **Sunum** — composition editor (Phases 1–7).
- **Basket** — server-side per-presentation set of tables targeted for inclusion in scope.
- **Draft presentation** — a placeholder presentation that exists only to hold a basket before the user materializes it via Hazırlık.
- **Corporate catalog** — tables sourced from Oracle EDW, documented in `table_docs/`.
- **User catalog** — tables uploaded by an individual user, stored at `uploads/<user>/`.
- **Binding inference** — the pipeline that proposes concept bindings for uploaded columns.
- **Provenance** — the inference step that produced a binding (regex, dtype, sample, LLM, user-authored).
- **Semantic zoom** — graph rendering that aggregates / expands nodes based on zoom level.
- **Highlight pulse** — short-duration visual emphasis on graph nodes (used by LLM proposals).

---

*End of spec. Revise via PR. Sub-phase 9.a kickoff prompt is at `docs/KICKOFF_PROMPT_PHASE_9_A.md`.*
