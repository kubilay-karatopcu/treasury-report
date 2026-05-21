\# Kickoff prompt — Phase 6.5.a (Block save and run)



> This is the prompt you paste into a fresh Claude Code session.

> Adapt the paths if your repo layout differs from what's in `CLAUDE.md`.



\---



\## Context



You are continuing work on the Treasury Studio app. The architecture, conventions, and locked design decisions live in `CLAUDE.md` (top of repo). The roadmap has reached Phase 6 (current production state); we are now implementing \*\*Phase 6.5 — Variable Binding MVP\*\*, a transitional layer between the current hardcoded-block app and the future concept layer (Phase 7).



The full spec for Phase 6.5 is at `docs/PHASE\_6\_5\_SPEC.md`. Read it before starting. Pay particular attention to:



\- §1.2 — Forward-compatibility contract with Phase 7

\- §2 — Data model (block, dashboard, table doc schemas)

\- §3 — Variable system (types, semantic\_tag allow-list, resolution flow)

\- §4 — Query engine (SQL whitelist, bind binding, cache + subset routing)

\- §10 — Acceptance criteria



Reference fixtures are in `examples/phase\_6\_5/`:



\- `sample\_block.yaml` — a representative Treasury block

\- `sample\_block\_2.yaml` — a second block using a different table

\- `sample\_dashboard.yaml` — a dashboard composing both blocks with filters

\- `sample\_table\_doc.yaml` — extended table documentation

\- `expected\_resolved\_query.sql` — the SQL after variable resolution + bind expansion, used in tests



\---



\## Scope for this session: Sub-phase 6.5.a only



Implement \*\*Block save and run\*\* as defined in spec §8.a. Do \*\*not\*\* start sub-phases 6.5.b, 6.5.c, or 6.5.d. Those will be separate sessions.



Acceptance criteria for this session: spec §10.a (every bullet must pass).



\### Deliverables expected by end of session



1\. \*\*Pydantic models\*\* for the block schema (`app/blocks/schema.py`). Match the YAML structure in `examples/phase\_6\_5/sample\_block.yaml`. All fields validated.



2\. \*\*SQL validator\*\* (`app/sql/validator.py`):

&#x20;  - Parses with `sqlparse`

&#x20;  - Whitelist: `SELECT` and `WITH` only

&#x20;  - Rejects DDL (`CREATE`, `DROP`, `ALTER`, `TRUNCATE`, `RENAME`, `GRANT`, `REVOKE`)

&#x20;  - Rejects DML writes (`INSERT`, `UPDATE`, `DELETE`, `MERGE`, `UPSERT`)

&#x20;  - Rejects procedural blocks (`BEGIN`, `DECLARE`, `EXECUTE IMMEDIATE`, `CALL`)

&#x20;  - Rejects multi-statement queries

&#x20;  - Verifies all `:bind\_var` references match declared `block.variables`

&#x20;  - Warns (does not reject) when declared variables are unused

&#x20;  - Returns a `ValidationResult(ok, errors, warnings)`



3\. \*\*Variable resolver\*\* (`app/variables/resolver.py`):

&#x20;  - Parses relative date expressions: `today`, `today - <N>d/w/m/y`, `start\_of\_month/year/quarter`, ISO literals

&#x20;  - Resolves a block's variables to concrete values using defaults (no dashboard filters in this sub-phase)

&#x20;  - Validates required vars, enum\_multi subset of allowed\_values, type correctness



4\. \*\*Bind expansion\*\* (`app/sql/binder.py`):

&#x20;  - Rewrites `IN (:list\_var)` to `IN (:list\_var\_0, :list\_var\_1, ...)` for `enum\_multi`

&#x20;  - Returns `(rewritten\_sql, bind\_dict)`

&#x20;  - Uses `datetime.date` objects for date binds (never strings)

&#x20;  - Verified against `examples/phase\_6\_5/expected\_resolved\_query.sql`



5\. \*\*Semantic tag allow-list\*\* (`app/variables/semantic\_tags.py`):

&#x20;  - The constant set from spec §3.2

&#x20;  - Helper functions: `is\_valid\_tag(s)`, `describe\_tag(s)` (Turkish label)



6\. \*\*Block store\*\* (`app/blocks/store.py`):

&#x20;  - S3 read/write at `blocks/<team>/<block\_id>/<version>.yaml`

&#x20;  - List blocks (by team, by tag, by viz type, with search)

&#x20;  - Version bumping logic (immutable; cannot overwrite existing version)

&#x20;  - Soft delete via `deprecated: true` flag in YAML



7\. \*\*Block editor UI\*\* (Flask routes + templates + JS bundle):

&#x20;  - Route: `/blocks/new` and `/blocks/edit/<team>/<block\_id>`

&#x20;  - Layout per spec §6.1 (metadata form, SQL editor, variables form, viz config, preview)

&#x20;  - Monaco or CodeMirror for the SQL editor (consistent with the rest of the app — pick what's already in the stack)

&#x20;  - Preview pane: "Çalıştır" button executes the block with default-resolved variables, renders the chart



8\. \*\*Standalone "run block" endpoint\*\* (`POST /blocks/<team>/<id>/<version>/run`):

&#x20;  - Accepts an optional `variable\_overrides` dict

&#x20;  - Resolves variables, validates, expands binds, executes against Oracle (using the existing `DataClient`), returns DataFrame as `df.to\_json(orient="records")` (per `CLAUDE.md` rule)

&#x20;  - Returns row count + query duration in headers



9\. \*\*Tests\*\* (`tests/blocks/`, `tests/sql/`, `tests/variables/`):

&#x20;  - One test per validation rule in §4.1

&#x20;  - Resolver tests for every supported date expression and edge cases

&#x20;  - Binder test verifying `expected\_resolved\_query.sql`

&#x20;  - E2E test: load `sample\_block.yaml` → save → fetch → run → verify result shape



\---



\## Working conventions



Follow the existing repo conventions verbatim. From `CLAUDE.md`:



\- `DataFrame → JSON` always via `df.to\_json(orient="records")` + `flask.Response`, never `jsonify(df.to\_dict(...))`.

\- DuckDB transfer from Oracle via Arrow bridge, no pandas dtype recasting.

\- `SCRIPT\_NAME` middleware behavior must remain intact.

\- CDN URLs without `@` characters (cdnjs.cloudflare.com paths).

\- Minimize per-request work in `load\_user()`.



Code style: match what's already in the codebase. If there's a `pyproject.toml` with `ruff`/`black` config, conform to it. Tests use the existing pytest setup.



\---



\## Out of scope for this session (do not touch)



\- Dashboard-level filter bar — that's 6.5.c.

\- Table doc schema extension — that's 6.5.b. (You can read existing table docs but do not change their schema.)

\- Library page — that's 6.5.d.

\- DuckDB cache + subset routing — that's 6.5.c. (The "run block" endpoint goes straight to Oracle this session.)

\- Phase 7 concept layer — out of scope entirely.



If you find yourself wanting to implement any of these, stop and note it as a follow-up. Do not expand scope.



\---



\## Definition of done



This session is complete when:



1\. All deliverables above are implemented.

2\. All acceptance criteria in spec §10.a pass.

3\. Tests pass: `python -m pytest tests/blocks/ tests/sql/ tests/variables/ -v`.

4\. The offline dev runner in `examples/` can save and execute the `sample\_block.yaml` against the SQLite fixture.

5\. A clean commit per logical unit (schema, validator, resolver, binder, store, UI, tests), each with a clear message.



Push to a feature branch `feature/phase-6.5.a-block-save-run`. Open a PR with a summary of acceptance criteria status.



Do not start 6.5.b automatically. Stop after the PR is opened.

