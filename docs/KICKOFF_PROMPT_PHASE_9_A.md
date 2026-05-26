# Kickoff prompt — Phase 9.a (Catalog API and detail card)

> Paste this into a fresh Claude Code session. Adapt paths if your repo layout differs from `CLAUDE.md`.

---

## Context

You are continuing work on Treasury Studio. Architecture, conventions, and locked decisions live in `CLAUDE.md`. The full Phase 9 spec is at `docs/PHASE_9_SPEC.md`. Read both before starting.

Phase 9 introduces **Keşif** (Stage 1 / Discover layer) — a new top-level workspace **Atölye** where users browse the catalog, ask the LLM what data exists for their task, view tables as a network graph, and upload their own CSV/XLSX files. Together with Phase 8's Hazırlık, Atölye is the complete pre-composition workflow.

Phases 1–8 are in production. You are now starting **Phase 9.a — the catalog API + detail card + tree-based browsing**. No network graph yet (that's 9.b). No LLM chat yet (9.c). No upload pipeline yet (9.d). Just the foundation: unified catalog read API, Atölye navigation, left-rail tree, right-side detail card, basket panel calling Phase 8's basket API.

Read these spec sections carefully:

- §1 — Context and dependencies on Phase 7 + Phase 8
- §2.1–§2.2, §2.5 — Catalog entry shapes, unified read API, basket API contract with Hazırlık
- §4.4 — Detail card content
- §6.1–§6.3, §6.6 — UI surfaces relevant to this sub-phase (Atölye nav, Keşif layout, basket panel, user-uploads tree section — but empty in this sub-phase)
- §10.a — Acceptance criteria for this sub-phase

---

## Scope for this session: Sub-phase 9.a only

Build the **catalog read API, the Atölye navigation entry, the Keşif tables tab with tree + detail card + basket panel**, calling Phase 8's basket API. Do NOT build the network graph (9.b). Do NOT build the LLM chat (9.c). Do NOT build the upload pipeline (9.d). Those are separate sessions.

Acceptance criteria for this session: spec §10.a (every bullet must pass).

### Deliverables expected by end of session

1. **Unified catalog read API** (`presentations/catalog/api.py`):
   - `GET /catalog` with query params: `scope`, `q`, `dept`, `concept`.
   - `GET /catalog/<schema>/<table>` for single-table detail.
   - `GET /catalog/graph` for the network graph payload (returns the shape from §2.4 but the UI doesn't render it yet; have the endpoint working so 9.b can build on it).
   - Response shape per §2.2 (uniform across corporate and user catalogs).
   - Pagination: not needed at v1 scale (200 tables); return all and let the client filter.

2. **Catalog loader** (`presentations/catalog/loader.py`):
   - Reads corporate tables from `s3://<bucket>/table_docs/<schema>/<table>.yaml` (existing Phase 6.5.b shape).
   - Reads user uploads from `s3://<bucket>/uploads/<sicil>/<upload_id>/doc.yaml` (none expected in fixtures for this sub-phase, but the code path must exist so 9.d builds on it).
   - Merges into a single in-memory catalog with a uniform `TableEntry` model.
   - 30-second TTL cache to avoid hitting S3 on every request (see spec §11).
   - Concept-binding analysis: derive `concepts_bound` and `concepts_unbound` from each table's column bindings.

3. **Edge computation** (`presentations/catalog/edges.py`):
   - For each table, compute outgoing edges per §2.3.
   - Three kinds: `lookup` (from declared lookup columns), `shared_concept`, `manual` (from a `related_tables` array, if present).
   - Used by the graph endpoint (rendered in 9.b).

4. **Atölye navigation entry**:
   - New nav link in the app header: "Atölye".
   - Routes to `/atolye/kesif` (Tables tab as default).
   - Sub-nav with tabs: Tables (active), Blocks (placeholder showing "Yakında"), Processes (placeholder).
   - Existing nav entries (My Presentations, etc.) remain untouched.

5. **Keşif Tables tab UI** (`presentations/templates/atolye/kesif.html`, `presentations/static/js/atolye/kesif/`):
   - Left rail: filters (Department, Concept, Source, Search bar) + tree of tables grouped by department.
   - Center: an empty placeholder where the network graph will go (9.b). Show a friendly "Graph yakında geliyor; sol tarafta arama yaparak başlayın" message.
   - Right rail: detail card (empty initial state).
   - User-uploads tree section above departments (empty in this sub-phase; the section header still renders with "Henüz yükleme yok").

6. **Detail card** (component within the Keşif JS bundle):
   - Renders all fields specified in §4.4 for the currently selected table.
   - "⊕ Add to basket" button calls Phase 8's `POST /presentations/<pid>/basket`.
   - Where `<pid>` comes from: see Draft Presentation handling below.
   - "⌕ Show in chat" button is rendered but disabled (enabled in 9.c).
   - "⚲ Focus graph" button is rendered but disabled (enabled in 9.b).

7. **Basket panel** (right panel, below detail card):
   - Lists basket contents.
   - Per-entry "×" remove button calling `DELETE /presentations/<pid>/basket/<alias>`.
   - Shows union of concepts across basket tables.
   - "Hazırlık'a geç" button: materializes the draft presentation, redirects to Hazırlık (`/atolye/hazirlik/<pid>`).

8. **Draft presentation handling** (`presentations/drafts/manager.py`):
   - On first basket interaction, create a draft presentation with id `draft_<sicil>_<timestamp>`.
   - Store the current draft pid in user prefs / session so subsequent visits find the same draft.
   - "Hazırlık'a geç" promotes draft pid → real presentation id, persists, removes from drafts.
   - Drafts >7 days old garbage-collected by an existing or new cron.

9. **Frontend search and filter logic**:
   - Search bar live-filters the tree (200ms debounce) by name + description.
   - Department filter (multi-select checkbox group): non-matching tree branches collapse / hide.
   - Concept filter: show only tables binding the selected concept.
   - Source filter: corporate / user uploads / favorites. (Favorites = data model stub only; UI mark-as-favorite affordance in 9.b.)
   - Filters combine with AND logic.

10. **Fixtures** in `examples/phase_9/`:
    - `sample_catalog_response.json` — example `/catalog` response with corporate + user uploads.
    - `sample_table_detail_response.json` — example `/catalog/<schema>/<table>` response.
    - `sample_graph_payload.json` — example `/catalog/graph` response (shape only; 9.b consumes).

11. **Tests** (`tests/catalog/`):
    - Catalog API endpoint tests: filtering by scope, dept, concept, search.
    - Catalog loader: corporate-only, user-only, merged.
    - Edge computation: declared lookup edge produced; shared-concept edge produced; manual-edge produced.
    - Draft presentation manager: creation, persistence, promotion, GC.
    - Frontend snapshot or integration test for the tree-render with a representative catalog.
    - Basket panel integration: add/remove invokes Phase 8 endpoints with correct payload.

---

## Working conventions

Follow existing repo conventions verbatim from `CLAUDE.md`:

- DataFrame → JSON via `df.to_json(orient="records")` + `flask.Response`, never `jsonify(df.to_dict(...))`. (Not directly relevant in this sub-phase but mentioned for completeness.)
- Use `current_user.sicil` as the user identifier.
- All Oracle queries through the existing `DataClient`. (Not needed in this sub-phase; catalog reads from S3 only.)
- S3 client pattern from existing snapshot store.
- Module layout: `presentations/catalog/` is the new package, sibling to `presentations/scope/` (Phase 8.a).
- Frontend: bundler + JS framework consistent with the existing app (per CLAUDE.md). Use the same JSX/component patterns as Sunum and Hazırlık.
- Tabler tokens only; no custom CSS in component styles.
- Python: type hints, Pydantic models for the API payloads.
- Tests via pytest; place under `tests/catalog/`.

---

## Out of scope for this session (do NOT touch)

- Network graph rendering (`Cytoscape.js` integration, semantic zoom, hover effects) — that's 9.b. The `GET /catalog/graph` endpoint must exist and return correct data, but the UI doesn't render it yet.
- LLM chat (Stage 1 discovery prompts, proposal cards, graph highlight sync) — that's 9.c.
- Upload pipeline (CSV/XLSX parse, binding inference, review UI, persistence to `uploads/`) — that's 9.d. The user-uploads tree section header renders but is empty.
- Multi-select via shift-click — that's 9.b (only relevant once the graph exists).
- Right-click context menus — 9.b.
- Re-run inference, soft delete, hard delete — 9.d.
- Blocks tab content, Processes tab content — Phase 10+ and 13+ respectively; tabs render as "Yakında" placeholders.

If you find yourself wanting to implement any of these, stop and add to follow-up notes in the PR. Do not expand scope.

---

## Notes on design choices to respect

These are spec'd and should not be re-debated this session:

- **Unified catalog read API.** Consumers (the tree, the graph, the LLM in 9.c) all consume the same shape. Corporate vs user-upload distinction is a field, not a separate endpoint.
- **30-second TTL cache.** Loader is read-mostly; S3 hit on every request hurts at 200-table scale. Cache invalidates on TTL or explicit `?refresh=true` query param.
- **Draft presentation pattern.** Baskets don't create real presentations until the user clicks "Hazırlık'a geç". This avoids polluting the presentations list with abandoned drafts.
- **Edge computation is on-the-fly, not stored.** Edges depend on current bindings; storing them creates a cache invalidation headache.
- **Source filter is a first-class concept.** Corporate vs user-upload UX distinction matters for trust signals; never blur the two.

---

## Definition of done

This session is complete when:

1. All deliverables above are implemented.
2. All §10.a acceptance criteria pass.
3. Tests pass: `python -m pytest tests/catalog/ tests/drafts/ -v`.
4. The app boots; Atölye nav entry is visible; clicking it lands on Keşif Tables tab.
5. Browsing the tree, clicking a table, viewing detail card, adding to basket, removing, and proceeding to Hazırlık all work end-to-end against the existing Phase 8 backend.
6. `GET /catalog/graph` returns a well-formed payload for the existing catalog (the network graph itself is not rendered; this endpoint is plumbing for 9.b).
7. A clean commit per logical unit (catalog API, loader, edges, draft manager, Atölye nav, Keşif UI, basket panel integration, fixtures, tests). Clear commit messages.

Push to a feature branch `feature/phase-9.a-catalog-api-detail-card`. Open a PR summarizing acceptance criteria status. List any items needing data-team coordination (likely: confirming that the corporate `table_docs/` shape after Phase 6.5.b migration matches what the loader expects; check the first 5 migrated tables against the fixture).

Do not start 9.b automatically. Stop after the PR is opened.
