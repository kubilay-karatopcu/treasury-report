# Kickoff prompt — Phase 8.a (Scope contract data model and persistence)

> Paste this into a fresh Claude Code session. Adapt paths if your repo layout differs from `CLAUDE.md`.

---

## Context

You are continuing work on Treasury Studio. Architecture, conventions, and locked decisions live in `CLAUDE.md`. The full Phase 8 spec is at `docs/PHASE_8_SPEC.md`. Read both before starting.

Phase 8 introduces **Hazırlık** (Stage 2 / Prepare layer) — a new screen between table selection and dashboard composition that produces a durable **scope contract**. This contract gates what the composition editor (Sunum) can mutate and makes the lazy/cache routing decision explicit.

Phases 1–7 are in production. You are now starting **Phase 8.a — the data model and persistence layer**. There is no UI in this sub-phase; everything is testable via fixtures and a temporary endpoint. The UI lands in 8.b.

Read these spec sections carefully:

- §1 — Context and dependencies on Phase 6.5 + Phase 7
- §2 — Data model (scope contract YAML schema, validators, dashboard manifest extension)
- §3.1, §3.6 — Routing decision algorithm and re-entry (note: full fetch path lands in 8.d; 8.a only builds the decision logic, no Oracle calls)
- §4 — Sunum integration (pinned filter enforcement in patch validator and block execution)
- §10.a — Acceptance criteria for this sub-phase

---

## Scope for this session: Sub-phase 8.a only

Build the **data model and persistence layer for scope contracts**, plus the Sunum-side enforcement of pinned filters. Do NOT build any Hazırlık UI. Do NOT implement the Oracle fetch path. Do NOT touch the LLM. Those are 8.b, 8.d, and 8.f respectively.

Acceptance criteria for this session: spec §10.a (every bullet must pass).

### Deliverables expected by end of session

1. **Scope contract Pydantic models** (`presentations/scope/schema.py`):
   - All fields from spec §2.1.
   - Aliased and id-validated per spec §2.4.
   - YAML serialization / deserialization round-trip preserving exact structure.

2. **Validators** (`presentations/scope/validators.py`):
   - All seven rules from spec §2.2.
   - Returns `ValidationResult(ok, errors, warnings)`.
   - Each rule individually testable.

3. **S3 store** (`presentations/scope/store.py`):
   - `save(scope) -> None` — version-bumping write to `s3://<bucket>/presentations/<user>/<id>/scope_v<N>.yaml`.
   - `load(presentation_id, version) -> ScopeContract`.
   - `load_latest(presentation_id) -> ScopeContract | None`.
   - `list_versions(presentation_id) -> list[int]`.
   - Refuses to overwrite existing version (immutable).
   - Uses the existing S3 client pattern from `presentations/store.py` (snapshot storage).

4. **Routing decision algorithm** (`presentations/scope/routing.py`):
   - `decide_routing(table_ref, projection, pinned_filters, threshold_bytes) -> RoutingDecision` per spec §3.1.
   - `estimate_post_scope_size(...)` helper, reading from table catalog metadata (Phase 6.5.b table docs).
   - Hard ceiling for user-forced cached override (`PRESENTATIONS_ROUTING_HARD_CEILING_BYTES` config key, default 10 GB).
   - **No Oracle calls.** Routing decisions are made from catalog metadata only at this stage; the actual fetch path is 8.d.

5. **Patch validator extension** (`presentations/nodes/validate_patch.py`):
   - New rejection rules from spec §4.1.
   - Loads the current scope contract for the presentation; checks every patch path against pinned filter IDs and pinned-bound block variables.
   - Error messages exactly as specified in §4.1.
   - Internal flag mechanism for scope re-entry flow (patches with `_scope_reentry: true` metadata bypass these rules; LLM-generated patches never have this flag).

6. **Block execution layer awareness** (`presentations/nodes/apply_patch.py` and / or `presentations/blocks/run.py` — whichever owns variable resolution):
   - Variable resolver checks if a variable is bound to a pinned scope filter; if so, returns the pinned value, ignoring any dashboard filter widget state.
   - Routing-aware execution stub: if the block's referenced alias is in `status.lazy_tables`, mark the block execution as "would-be-lazy" (raise `NotImplementedError("Lazy execution lands in 8.d")` for now). Cached aliases continue to work via DuckDB views (no change from current behavior beyond the routing check).
   - **Do not implement the actual lazy Oracle path.** That's 8.d.

7. **Dashboard manifest `scope_ref` field**:
   - Update manifest schema to accept optional `scope_ref: {presentation_id, scope_version}`.
   - Backwards-compatible: absent field means current behavior (no scope contract).
   - Documented in the manifest schema's docstring.

8. **Temporary HTTP endpoints** (for testing scope contracts before 8.b builds the UI):
   - `POST /presentations/<pid>/scope` — accepts scope contract JSON, validates, saves, returns version number.
   - `GET /presentations/<pid>/scope` — returns latest scope contract.
   - `GET /presentations/<pid>/scope/<version>` — returns specific version.
   - These are auth'd via existing `@login_required` and use `current_user.sicil`. Marked clearly as "temporary, to be replaced by Hazırlık UI in 8.b".

9. **Fixtures** in `examples/phase_8/`:
   - `sample_scope.yaml` — full example matching spec §2.1.
   - `sample_dashboard_with_scope.yaml` — dashboard manifest referencing the sample scope.
   - `sample_table_catalog_excerpt.yaml` — minimal table catalog data needed for routing decisions (post Phase 6.5.b shape).
   - `expected_validator_outputs.yaml` — for each rule in §2.2, an invalid scope and the expected error message.

10. **Tests** (`tests/scope/`):
    - One test per validator rule (§2.2).
    - Schema round-trip tests (YAML → object → YAML byte-identical).
    - S3 store tests (using moto or in-memory mock): write, read, version bump, immutability.
    - Routing decision tests: small table → cached, large table → lazy, user override valid → applied, user override exceeds ceiling → rejected.
    - Patch validator tests: pinned filter mutation rejected with exact error message; pinned-bound variable mutation rejected; scope_ref tampering rejected; scope re-entry flag bypass works.
    - Block execution variable resolution: pinned-bound variable returns pinned value; interactive-bound variable returns dashboard filter value; unbound variable returns block default.
    - Backwards compat: dashboard without `scope_ref` loads and renders correctly (uses existing test fixtures).

---

## Working conventions

Follow the existing repo conventions verbatim from `CLAUDE.md`:

- DataFrame → JSON via `df.to_json(orient="records")` + `flask.Response`, never `jsonify(df.to_dict(...))`.
- Use `current_user.sicil` for user identifier.
- All Oracle queries through the existing `DataClient`. (None expected in this sub-phase.)
- S3 client pattern from existing snapshot store.
- Module file layout: `presentations/scope/` is the new package, sibling to `presentations/blocks/`, `presentations/nodes/`, etc.
- Python: type hints everywhere, dataclasses or Pydantic for structured data.
- No `print` statements; use `current_app.logger`.
- Tests via pytest; place under `tests/scope/`.

---

## Out of scope for this session (do NOT touch)

- Hazırlık UI (`presentations/templates/hazirlik.html`, any new JSX bundle) — that's 8.b.
- Oracle fetch path for cached tables — that's 8.d.
- Lazy table Oracle query rewrite — that's 8.d.
- Projection picker, lookup join engine — that's 8.c.
- Stage 2 LLM chat — that's 8.f.
- Scope re-entry diff logic — that's 8.e.
- Phase 9 catalog browser — entirely future.

If you find yourself wanting to implement any of these, stop and add to follow-up notes in the PR. Do not expand scope.

---

## Notes on design choices to respect

These are spec'd and should not be re-debated this session:

- **Scope contracts are immutable per version.** Edits create `scope_v<N+1>`. Version bumping is the store's responsibility; callers don't pass version numbers on save.
- **Pinned filter mutation is rejected at the patch validator, not silently ignored.** The error message text is specified in §4.1 — match it exactly so the UI in 8.b can rely on it.
- **`applies_to` semantics:** empty/omitted = applies to all basket tables that bind the concept. Explicit list = applies only to those aliases. Validator rule §2.2.3 enforces concept coverage; missing binding is a warning, not an error.
- **Routing decisions read only from catalog metadata, never from Oracle.** This is what makes 8.a fast and testable without Oracle access.
- **Backwards compatibility is non-negotiable.** Dashboards without `scope_ref` must continue to work unchanged.

---

## Definition of done

This session is complete when:

1. All deliverables above are implemented.
2. All §10.a acceptance criteria pass.
3. Tests pass: `python -m pytest tests/scope/ tests/blocks/ tests/dashboards/ -v`.
4. The temporary `POST /presentations/<pid>/scope` endpoint successfully accepts and validates `examples/phase_8/sample_scope.yaml`.
5. The Sunum patch validator rejects a hand-crafted patch attempting to mutate a pinned filter from the sample scope.
6. A clean commit per logical unit (schema, validators, store, routing, patch validator extension, block execution awareness, endpoints, fixtures, tests). Clear commit messages.

Push to a feature branch `feature/phase-8.a-scope-data-model`. Open a PR summarizing acceptance criteria status and list any items needing data-team coordination (likely none in 8.a, but check the catalog metadata shape against what Phase 6.5.b actually shipped).

Do not start 8.b automatically. Stop after the PR is opened.
