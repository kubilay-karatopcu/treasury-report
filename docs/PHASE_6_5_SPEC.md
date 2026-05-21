\# Phase 6.5 — Variable Binding MVP



\*\*Status:\*\* Spec draft

\*\*Owner:\*\* Kubilay Karatopçu

\*\*Depends on:\*\* Phases 1–6 (current production app)

\*\*Forward-compat target:\*\* Phase 7 (Concept Foundation)

\*\*Language note:\*\* Spec is in English for Claude Code consistency. Inline comments and user-facing strings may be Turkish where called out.



\---



\## 1. Context



\### 1.1 Why this phase exists



The team needs to start producing blocks and dashboards \*\*in parallel\*\* with the rest of the roadmap. A full concept-layer implementation (Phase 7) is too heavy to gate the team on. Phase 6.5 introduces a \*\*manual, user-authored variable binding system\*\* that gives the team enough structure to:



\- Save and reuse blocks across dashboards

\- Apply dashboard-level filters that propagate to multiple blocks

\- Cache query results and serve subset-filter requests without re-fetching from Oracle



This is a deliberate \*\*transitional layer\*\*. Variables here are the manual ancestor of concepts in Phase 7. Migration is designed in from day one.



\### 1.2 Forward-compatibility contract with Phase 7



Every variable carries a mandatory `semantic\_tag` field drawn from a fixed allow-list. When Phase 7 lands, this allow-list becomes the concept registry. Migration script will convert `semantic\_tag: currency` variables into concept-aware filters automatically. \*\*No variable may be saved without a semantic\_tag.\*\*



\### 1.3 Out of scope



\- Concept registry, column bindings, scope contract — Phase 7.

\- Stage 1 (Library/Keşif) and Stage 2 (Hazırlık) UI — Phase 8/9.

\- Block marketplace clustering, semantic search, process emergence — Phase 10+.

\- Multi-table joins in the variable layer (one query per block).

\- Free-form text variables (intentionally excluded for safety + UX).

\- Incremental fetch / cache expansion (full refetch on superset miss).

\- Real-time collaboration on blocks.



\### 1.4 Relationship to existing app



The current app (Phases 1–6) has:



\- Manifest engine + JSON Patch

\- Chat + Qwen LLM integration

\- DuckDB session per presentation

\- S3 snapshot persistence

\- Section-mode layout, edit + presentation view modes



Phase 6.5 \*\*extends\*\* these:



\- Existing blocks become a special case: blocks without variables (hard-coded queries) still work.

\- New blocks declare variables.

\- Dashboard manifest gains a top-level `filters` array.

\- DuckDB session becomes block-scoped instead of presentation-scoped (each block manages its own cache keyed by resolved variables).



\---



\## 2. Data Model



\### 2.1 Block YAML schema



A block is the atomic, version-controlled artifact. Stored at `s3://<bucket>/blocks/<team>/<block\_id>/<version>.yaml`.



```yaml

block:

&#x20; id: branch\_position\_kpi          # team-unique slug, kebab\_case

&#x20; version: 1                       # integer, immutable per version

&#x20; title: "Şube Net Pozisyon"

&#x20; description: "Şube bazlı net pozisyon karşılaştırma KPI bloğu."

&#x20; team: retail\_banking

&#x20; owner: ahmet.yilmaz

&#x20; created\_at: 2026-05-20T10:00:00Z

&#x20; tags: \[branch, position, kpi]    # free-form, used by library search



&#x20; documentation:

&#x20;   purpose: |

&#x20;     Pazartesi sabah operasyon komitesinde şube karşılaştırma için.

&#x20;   business\_context: |

&#x20;     Bu blok haftalık şube performans toplantısında kullanılır.

&#x20;   decision\_support: |

&#x20;     Şube bazlı kaynak tahsisi ve performans uyarıları.

&#x20;   known\_limitations: |

&#x20;     Yeni açılan şubeler 3 ay geçmeden anlamlı değil.



&#x20; query: |

&#x20;   SELECT

&#x20;     BRANCH\_ID,

&#x20;     BRANCH\_NAME,

&#x20;     SUM(NET\_POSITION) AS TOTAL\_POS

&#x20;   FROM TRD\_BRANCH\_POSITION

&#x20;   WHERE AS\_OF\_DATE BETWEEN :as\_of\_from AND :as\_of\_to

&#x20;     AND CCY IN (:currency\_list)

&#x20;     AND MATURITY\_BUCKET IN (:maturity\_list)

&#x20;   GROUP BY BRANCH\_ID, BRANCH\_NAME

&#x20;   ORDER BY TOTAL\_POS DESC



&#x20; variables:

&#x20;   - name: as\_of\_from

&#x20;     semantic\_tag: as\_of\_time     # REQUIRED

&#x20;     type: date

&#x20;     required: true

&#x20;     default: "today - 30d"       # relative date expression



&#x20;   - name: as\_of\_to

&#x20;     semantic\_tag: as\_of\_time

&#x20;     type: date

&#x20;     required: true

&#x20;     default: "today"



&#x20;   - name: currency\_list

&#x20;     semantic\_tag: currency

&#x20;     type: enum\_multi

&#x20;     required: false

&#x20;     allowed\_values: \[TRY, USD, EUR, GBP, CHF]

&#x20;     default: \[TRY, USD, EUR]



&#x20;   - name: maturity\_list

&#x20;     semantic\_tag: maturity

&#x20;     type: enum\_multi

&#x20;     required: false

&#x20;     allowed\_values: \[ON, 1W, 1M, 3M, 6M, 1Y, 2Y, 5Y]

&#x20;     default: \[1M, 3M, 6M]



&#x20; visualization:

&#x20;   type: bar\_chart                # one of: kpi, kpi\_grid, line, bar, table, pie

&#x20;   config:

&#x20;     x: BRANCH\_NAME

&#x20;     y: TOTAL\_POS

&#x20;     orientation: horizontal

&#x20;     sort: desc

&#x20;     limit: 20

```



\### 2.2 Dashboard YAML schema



A dashboard is the consumer of blocks. Stored at `s3://<bucket>/dashboards/<user>/<dashboard\_id>/<version>.yaml`.



```yaml

dashboard:

&#x20; id: branch\_morning\_review

&#x20; version: 3

&#x20; title: "Pazartesi Şube İnceleme"

&#x20; owner: ahmet.yilmaz

&#x20; created\_at: 2026-05-20T10:00:00Z



&#x20; filters:                         # dashboard-level filter bar

&#x20;   - id: f\_period

&#x20;     semantic\_tag: as\_of\_time

&#x20;     type: date\_range

&#x20;     label: "Tarih Aralığı"

&#x20;     default:

&#x20;       from: "today - 30d"

&#x20;       to: "today"



&#x20;   - id: f\_currency

&#x20;     semantic\_tag: currency

&#x20;     type: enum\_multi

&#x20;     label: "Para Birimi"

&#x20;     allowed\_values: \[TRY, USD, EUR, GBP, CHF]

&#x20;     default: \[TRY, USD, EUR]



&#x20;   - id: f\_maturity

&#x20;     semantic\_tag: maturity

&#x20;     type: enum\_multi

&#x20;     label: "Vade"

&#x20;     allowed\_values: \[ON, 1W, 1M, 3M, 6M, 1Y, 2Y]

&#x20;     default: \[1M, 3M, 6M]



&#x20; layout:

&#x20;   sections:

&#x20;     - id: s\_top

&#x20;       title: "Genel Bakış"

&#x20;       rows:

&#x20;         - blocks:

&#x20;             - block\_ref:

&#x20;                 team: retail\_banking

&#x20;                 id: branch\_position\_kpi

&#x20;                 version: 1

&#x20;               variable\_bindings:

&#x20;                 as\_of\_from:

&#x20;                   from\_filter: f\_period

&#x20;                   accessor: from

&#x20;                 as\_of\_to:

&#x20;                   from\_filter: f\_period

&#x20;                   accessor: to

&#x20;                 currency\_list:

&#x20;                   from\_filter: f\_currency

&#x20;                 maturity\_list:

&#x20;                   from\_filter: f\_maturity



&#x20;             - block\_ref:

&#x20;                 team: treasury

&#x20;                 id: fx\_exposure\_line

&#x20;                 version: 2

&#x20;               variable\_bindings:

&#x20;                 trade\_date\_from:

&#x20;                   from\_filter: f\_period

&#x20;                   accessor: from

&#x20;                 trade\_date\_to:

&#x20;                   from\_filter: f\_period

&#x20;                   accessor: to

&#x20;                 currency\_list:

&#x20;                   from\_filter: f\_currency

&#x20;                 # block has no maturity variable → no binding needed

&#x20;                 # block has a `target\_branch` variable → falls back to block default

```



Variable bindings can also be `constant`-based for block-level overrides:



```yaml

variable\_bindings:

&#x20; as\_of\_from:

&#x20;   constant: "today - 7d"         # this block always shows last 7d regardless of dashboard filter

&#x20; as\_of\_to:

&#x20;   constant: "today"

```



\### 2.3 Table documentation schema (extended)



Stored at `s3://<bucket>/table\_docs/<schema>/<table>.yaml`. Existing docs are migrated by data team; new docs follow this schema.



```yaml

table: TRD\_BRANCH\_POSITION

schema: ODS\_TREASURY

description: "Şube bazlı günlük pozisyon snapshot'ı."

partition\_column: AS\_OF\_DATE       # used by query planner for pushdown hints

estimated\_daily\_rows: 12000



columns:

&#x20; AS\_OF\_DATE:

&#x20;   type: DATE

&#x20;   description: "Snapshot tarihi"

&#x20;   filterable: true

&#x20;   filter\_role: time\_axis         # one of: time\_axis, dimension, measure\_threshold

&#x20;   suggested\_variable: as\_of\_date

&#x20;   suggested\_semantic\_tag: as\_of\_time



&#x20; BRANCH\_ID:

&#x20;   type: VARCHAR2(8)

&#x20;   description: "Şube kodu"

&#x20;   filterable: true

&#x20;   filter\_role: dimension

&#x20;   suggested\_variable: branch\_id

&#x20;   suggested\_semantic\_tag: branch

&#x20;   lookup:

&#x20;     table: DIM\_BRANCH

&#x20;     key: BRANCH\_ID

&#x20;     display: BRANCH\_NAME



&#x20; CCY:

&#x20;   type: CHAR(3)

&#x20;   description: "Para birimi (ISO 4217)"

&#x20;   filterable: true

&#x20;   filter\_role: dimension

&#x20;   suggested\_variable: currency\_list

&#x20;   suggested\_semantic\_tag: currency

&#x20;   distinct\_values\_sample: \[TRY, USD, EUR, GBP, CHF, JPY]

&#x20;   distinct\_values\_sampled\_at: 2026-05-19T03:00:00Z



&#x20; MATURITY\_BUCKET:

&#x20;   type: VARCHAR2(6)

&#x20;   description: "Vade grubu"

&#x20;   filterable: true

&#x20;   filter\_role: dimension

&#x20;   suggested\_variable: maturity\_list

&#x20;   suggested\_semantic\_tag: maturity

&#x20;   distinct\_values\_sample: \[ON, 1W, 1M, 3M, 6M, 1Y, 2Y]



&#x20; NET\_POSITION:

&#x20;   type: NUMBER

&#x20;   description: "Net pozisyon (TL eşdeğeri)"

&#x20;   filterable: false

&#x20;   aggregatable: true



&#x20; CREATED\_AT:

&#x20;   type: TIMESTAMP

&#x20;   description: "Kayıt yaratma zamanı (internal audit)"

&#x20;   filterable: false

&#x20;   visible\_in\_ui: false           # excluded from column pickers and LLM context

```



\### 2.4 Block versioning



\- Block YAML is \*\*immutable per version\*\*. Editing creates a new version (`version: 1 → 2`).

\- Dashboard manifests reference `{team, id, version}` triple. Old dashboards keep pointing to old versions.

\- A `version: latest` reference is allowed but emits a warning at dashboard save time ("you're referencing latest; future block updates may change this dashboard").

\- Block delete is soft (mark as `deprecated: true`); referenced versions remain readable.

\- Version bump from the editor UI requires:

&#x20; - Confirmation modal showing "X dashboards reference v\_N"

&#x20; - Optional changelog field (free text, persisted on the new version)



\### 2.5 Identifier rules



\- `block.id`: kebab\_case, 3–60 chars, `\[a-z0-9\_]+`, team-unique.

\- `variable.name`: snake\_case, 3–40 chars, block-unique. May reuse `suggested\_variable` from table doc.

\- `filter.id`: kebab\_case, 3–40 chars, dashboard-unique, prefixed `f\_` by convention (not enforced).

\- `semantic\_tag`: from fixed allow-list (see §3.2). \*\*Required.\*\*



\---



\## 3. Variable System



\### 3.1 Supported variable types



| Type | SQL bind shape | UI widget | Notes |

|------|---|---|---|

| `date` | single value | date picker | absolute or relative |

| `date\_range` | two values via accessor | range picker | `accessor: from \\| to` in binding |

| `enum\_single` | single value | dropdown | from `allowed\_values` |

| `enum\_multi` | list expanded to `IN (...)` | multi-select | from `allowed\_values` |

| `number\_range` | two values | numeric range slider | for future use; implement basic version |



`text` (free-form string) is \*\*not supported\*\* in v0. Add to backlog only if user demand justifies the risk.



\### 3.2 Semantic tag allow-list (v0)



This list is the seed of the future concept registry. Hard-coded in `app/variables/semantic\_tags.py`:



```python

SEMANTIC\_TAGS\_V0 = {

&#x20;   "as\_of\_time",

&#x20;   "trade\_time",

&#x20;   "value\_time",

&#x20;   "settle\_time",

&#x20;   "currency",

&#x20;   "maturity",

&#x20;   "tenor\_bucket",

&#x20;   "counterparty",

&#x20;   "branch",

&#x20;   "region",

&#x20;   "product\_group",

&#x20;   "segment",

&#x20;   "rating\_bucket",

&#x20;   "user\_id",

&#x20;   "deal\_id",

&#x20;   "instrument\_type",

&#x20;   "other",                       # explicit escape hatch; flagged in UI

}

```



Rules:

\- New tags require a code change + PR. Not user-editable in v0.

\- `other` is the escape hatch when nothing fits. Block editor surfaces a yellow warning when used.

\- A short human-readable label and description exists per tag (Turkish), shown in the editor.



\### 3.3 Relative date expression grammar



Supported expressions in `default` and `constant`:



```

today

today - <N>d

today - <N>w

today - <N>m

today - <N>y

start\_of\_month

start\_of\_year

start\_of\_quarter

<ISO date literal>           # e.g., 2026-01-01

```



Parsed by a small regex-based resolver. Reject anything else with a clear error at block save time, not at run time.



\### 3.4 Variable resolution flow



Per block, per render:



```

1\. Initialize resolved = {}

2\. For each variable v in block.variables:

&#x20;  a. If dashboard has a variable\_binding for v.name:

&#x20;       - If binding.constant is set: resolved\[v.name] = parse(binding.constant)

&#x20;       - If binding.from\_filter is set:

&#x20;           filter = dashboard.filters\[binding.from\_filter]

&#x20;           value = current filter value (from UI state)

&#x20;           if binding.accessor: value = value\[binding.accessor]

&#x20;           resolved\[v.name] = value

&#x20;  b. Else: resolved\[v.name] = parse(v.default)

3\. Validate:

&#x20;  - All required vars resolved

&#x20;  - enum\_multi values ⊆ allowed\_values

&#x20;  - date types parseable

4\. Return resolved dict.

```



Validation failures abort render with a clear error displayed on the block tile.



\### 3.5 Auto-binding by semantic tag



When a block is added to a dashboard, the editor proposes default variable\_bindings:



\- For each variable `v` in the block:

&#x20; - Find dashboard filters where `filter.semantic\_tag == v.semantic\_tag`.

&#x20; - If exactly one match: propose `from\_filter: <id>`. If type is `date\_range` and `v.type` is `date`, propose `accessor: from` or `to` based on variable name heuristic (`\*\_from` → `from`, `\*\_to` → `to`, otherwise prompt user).

&#x20; - If multiple matches: surface a choice in the editor, no auto-bind.

&#x20; - If no matches: leave unbound; trigger the prompt described in §5.3.



Auto-binding is a \*\*suggestion\*\*, always shown to the user for confirmation before save. Never silent.



\---



\## 4. Query Engine



\### 4.1 SQL validator (parser-based whitelist)



Library: `sqlparse` (already in stack; verify version supports CTE detection).



Validation rules (block save time + before each execution):



1\. Parse must succeed.

2\. Top-level statement must be one of: `SELECT`, `WITH`.

3\. No `DDL` keywords anywhere: `CREATE`, `DROP`, `ALTER`, `TRUNCATE`, `RENAME`, `GRANT`, `REVOKE`, `COMMENT`.

4\. No `DML` write keywords: `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `UPSERT`.

5\. No procedural blocks: `BEGIN`, `DECLARE`, `EXECUTE IMMEDIATE`, `CALL`.

6\. No multiple statements (no `;` terminating a non-terminal statement).

7\. All bind variables (`:name`) must be declared in `block.variables`.

8\. All declared variables must be referenced in the query (warning, not error — block save allowed).



Implementation: `app/sql/validator.py`. Returns a `ValidationResult` with `ok: bool`, `errors: list\[str]`, `warnings: list\[str]`.



\### 4.2 Bind variable execution



\- \*\*Never concatenate values into SQL.\*\* Use `oracledb` bind variables.

\- For `enum\_multi`, expand to dynamic IN clause with positional placeholders:

&#x20; ```python

&#x20; # query has: WHERE CCY IN (:currency\_list)

&#x20; # resolved: currency\_list = \["TRY", "USD"]

&#x20; # rewritten: WHERE CCY IN (:ccy\_0, :ccy\_1)

&#x20; # binds: {ccy\_0: "TRY", ccy\_1: "USD"}

&#x20; ```

&#x20; Implementation: pre-execution rewrite step in `app/sql/binder.py`.

\- For `date` and `date\_range`, bind as `datetime.date` objects, not strings.



\### 4.3 Cache key + subset routing



Each block maintains a DuckDB-backed cache. Cache layer is in `app/cache/block\_cache.py`.



\*\*Cache key\*\* = SHA256 of `(block.id, block.version, resolved\_variables\_normalized)`.

Normalization: sorted keys, sorted enum\_multi values, ISO date strings.



\*\*Subset detection\*\* runs when key misses:



```python

def find\_subset\_parent(current\_resolved, candidates):

&#x20;   """

&#x20;   candidates = list of cached (key, resolved) tuples for same block\_id+version.

&#x20;   Returns parent key if current ⊆ parent, else None.

&#x20;   """

&#x20;   for key, parent\_resolved in candidates:

&#x20;       if is\_subset(current\_resolved, parent\_resolved):

&#x20;           return key

&#x20;   return None



def is\_subset(current, parent):

&#x20;   for var\_name, var\_def in block.variables.items():

&#x20;       c = current.get(var\_name)

&#x20;       p = parent.get(var\_name)

&#x20;       if var\_def.type == "date":

&#x20;           if c != p: return False

&#x20;       elif var\_def.type == "date\_range":

&#x20;           if not (p\["from"] <= c\["from"] and c\["to"] <= p\["to"]):

&#x20;               return False

&#x20;       elif var\_def.type == "enum\_multi":

&#x20;           if not set(c).issubset(set(p)):

&#x20;               return False

&#x20;       elif var\_def.type == "enum\_single":

&#x20;           if c != p: return False

&#x20;       elif var\_def.type == "number\_range":

&#x20;           if not (p\["min"] <= c\["min"] and c\["max"] <= p\["max"]):

&#x20;               return False

&#x20;   return True

```



When a subset parent is found:

1\. Do not re-execute against Oracle.

2\. Run a DuckDB filter query against parent's cached result.

3\. Store derived result under new key.



When no subset parent:

1\. Validate and execute against Oracle.

2\. Write result to DuckDB under new key.



\### 4.4 Cache eviction policy



\- Per-session DuckDB file. Soft cap: \*\*2 GB on disk\*\*.

\- LRU eviction when cap exceeded: drop least-recently-accessed cache entries (across all blocks in the session).

\- Each cache write records `last\_accessed\_at`. Reads update this timestamp.

\- Eviction runs lazily before each new write, not on a timer.



\### 4.5 Refetch UX states



Each block tile displays a small status indicator during query lifecycle:



\- `cache\_hit`: green dot, "ön belleğe alındı" tooltip

\- `subset\_filter`: blue dot, "filtre uygulandı" tooltip

\- `refetching`: spinner, "Oracle'dan veri çekiliyor..."

\- `error`: red dot, error message in tooltip



Status updates are pushed via the existing SSE channel.



\---



\## 5. Dashboard Filter Bar



\### 5.1 Layout and placement



Filter bar sits at the top of the dashboard in edit and presentation modes. Sticky on scroll. Below the title, above the first section.



For mobile/narrow viewports, collapses into a "Filtreler (3)" expandable panel.



\### 5.2 Widget mapping



| Filter type | Widget |

|---|---|

| `date\_range` | Range picker with presets (Son 7 gün, Son 30 gün, Son 3 ay, Bu ay, Bu yıl, Özel) |

| `enum\_multi` | Multi-select with search + "Tümü" / "Hiçbiri" |

| `enum\_single` | Dropdown |

| `number\_range` | Dual-handle slider |



All widgets show current selection inline. Changing a widget does \*\*not\*\* auto-trigger refetch — there is a single "Güncelle" button (§5.5).



\### 5.3 "Filter eklemek ister misiniz?" prompt



Triggered when a block is added to a dashboard and at least one of its variables has a `semantic\_tag` not represented in any dashboard filter.



UI:

\- Non-blocking inline banner under the new block.

\- "Bu bloktaki `<variable\_name>` (`<semantic\_tag>`) bir dashboard filtresine bağlı değil. Eklemek ister misiniz?"

\- Buttons: `Filtre ekle` (opens filter creation form pre-filled with sensible defaults from block variable), `Hayır, varsayılan kullansın`, `Daha sonra`.



If user chooses `Filtre ekle`:

\- A new filter is created on the dashboard.

\- The block's variable is auto-bound to the new filter.

\- Banner dismisses.



If user chooses `Hayır`:

\- Block falls back to its own variable defaults.

\- Banner dismisses, no filter created.



`Daha sonra`:

\- Banner dismisses for this session.

\- A small badge remains on the block ("1 unbound variable") clickable to re-open the prompt.



\### 5.4 Block-level constant override



Variable bindings may carry `constant: "<expr>"` instead of `from\_filter`. Use case: a block that should always show "last 7 days" regardless of dashboard filter.



UI: in the block-config side panel, each variable shows three radio options:

\- `Dashboard filtresi: <filter\_label>` (if auto-bind / manual bind exists)

\- `Sabit: \[\_\_\_\_]` (text input for relative date or literal value)

\- `Blok varsayılanı: <default>`



\### 5.5 Apply (Güncelle) flow



A single "Güncelle" button in the filter bar triggers the apply pass.



Behavior:

1\. Disable button, show spinner.

2\. For each block on the dashboard:

&#x20;  a. Resolve variables with new filter state.

&#x20;  b. Check cache: hit → render; subset → DuckDB filter; miss → Oracle refetch.

&#x20;  c. Update block tile status indicator (§4.5).

3\. Re-enable button when all blocks settle.



Per-block updates stream independently via SSE — slow Oracle refetches do not block fast cache hits.



\### 5.6 Filter persistence



Filter state (current values) is persisted to dashboard manifest \*\*only on explicit user save\*\*. Mid-session changes are kept in client state. This preserves dashboard reproducibility (defaults vs. ad-hoc exploration).



\---



\## 6. UI Surfaces



\### 6.1 Block editor



Route: `/blocks/edit/<team>/<block\_id>` (new version on save) or `/blocks/new`.



Layout:

\- \*\*Left panel:\*\* metadata form (id, title, description, tags, team, documentation fields).

\- \*\*Center top:\*\* SQL editor (Monaco or CodeMirror, syntax-highlighted, bind variable autocomplete).

\- \*\*Center middle:\*\* variables form (per-variable: name, semantic\_tag dropdown, type, required, default, allowed\_values).

\- \*\*Center bottom:\*\* visualization config (chart type + per-type fields).

\- \*\*Right panel:\*\* preview pane.



Preview pane:

\- "Çalıştır" button executes with current variable defaults.

\- Shows resolved query (after bind expansion) in read-only collapsed section.

\- Renders the chart below.

\- Performance metrics: row count, query duration, cache state.



Save flow:

\- Validate (SQL whitelist, variable refs, semantic\_tag presence).

\- Compute new version.

\- Confirm "X dashboards reference v\_<previous>" if editing existing block.

\- Optional changelog field.

\- Write to S3.



\### 6.2 Block library



Route: `/library` (new top-level nav item).



Layout:

\- \*\*Left:\*\* filter rail

&#x20; - Team (multi-select)

&#x20; - Tag (multi-select)

&#x20; - Visualization type

&#x20; - Created in last N days

\- \*\*Center:\*\* result grid

&#x20; - Card per block: title, team, owner, viz type icon, tags, "v3 · 47 dashboards" footer

&#x20; - Click → block detail page

\- \*\*Right:\*\* preview panel

&#x20; - Selected block's documentation, variable summary, sample render with defaults

&#x20; - "Dashboard'a ekle" CTA → opens dashboard picker



Search: top bar full-text across `title`, `description`, `documentation.purpose`, `tags`.



\### 6.3 Dashboard filter bar (in editor)



When in edit mode, filter bar has an additional "+" button to add a filter:



\- Opens form: id, semantic\_tag, type, label, allowed\_values (for enum types), default.

\- Save → filter appears immediately in the bar.

\- Existing filters editable via pencil icon (changes filter ID requires re-binding warning).

\- Reorder via drag.



\### 6.4 Dashboard editor block insertion



When adding a block from the library:



1\. Block appears in placeholder.

2\. Auto-bind suggestions computed (§3.5).

3\. Bind-confirmation modal:

&#x20;  - For each variable, show: name, semantic\_tag, suggested binding (or "unbound").

&#x20;  - User can adjust before confirming.

4\. On confirm: bindings written, "Filter eklemek ister misiniz?" prompt fires for unbound vars (§5.3).



\---



\## 7. Migration Strategy



\### 7.1 Existing blocks (hardcoded queries)



Existing pre-6.5 blocks remain functional with no changes — they are treated as blocks with zero variables. They do not auto-bind to dashboard filters.



Optional upgrade path: a "convert to variable-aware" action in block editor. Manual; not automated. Documented in user guide.



\### 7.2 Existing table documentation



Data team migrates existing docs to extended schema (§2.3) \*\*table-by-table, not as a big bang\*\*.



Priority order (from current usage stats):

1\. `TRD\_BRANCH\_POSITION`

2\. `FX\_SWAP\_DEALS`

3\. `TRD\_POSITION\_DAILY`

4\. `DIM\_BANK`, `DIM\_BRANCH`, `DIM\_CURRENCY` (lookups)

5\. Remaining \~10 treasury tables



Each migration is a small PR with the YAML; reviewed and merged.



Until a table is migrated, its blocks can still be authored — they just lack the LLM autocompletion benefits.



\### 7.3 Existing dashboards



Existing dashboards remain functional. No top-level `filters` array → no dashboard filter bar shown.



Users can add filters incrementally to existing dashboards through the editor. Adding a filter does not auto-bind to existing blocks (which have no variables).



\---



\## 8. Implementation Sub-Phases



Each sub-phase has its own acceptance criteria (§10) and can be implemented and shipped independently.



\### 8.a — Block save and run



Build the block authoring loop end-to-end without dashboard filters.



Deliverables:

\- Block YAML schema + Pydantic models (`app/blocks/schema.py`)

\- SQL validator (`app/sql/validator.py`)

\- Variable resolver (`app/variables/resolver.py`)

\- Bind expansion (`app/sql/binder.py`)

\- Block CRUD API + S3 persistence (`app/blocks/store.py`)

\- Block editor UI (§6.1)

\- Standalone "run block" endpoint that executes with default variables



\### 8.b — Table documentation enhancement



Extend table doc schema and tooling.



Deliverables:

\- Extended table doc schema + parser

\- LLM prompt context includes new fields (filterable, suggested\_variable, suggested\_semantic\_tag, distinct\_values\_sample)

\- `distinct\_values\_sample` cron job (`jobs/sample\_distinct\_values.py`)

\- Data team migration: top 5 tables (§7.2)



\### 8.c — Dashboard-level filter



Wire dashboard filters to blocks.



Deliverables:

\- Dashboard YAML schema extension

\- Filter bar UI (§6.3, §5.1–5.2)

\- Auto-binding logic (§3.5)

\- "Filter eklemek ister misiniz?" prompt (§5.3)

\- Block-level constant override UI (§5.4)

\- Apply flow + SSE block status updates (§5.5)

\- Block cache + subset routing (§4.3, §4.4)



\### 8.d — Library MVP



Block library browsing.



Deliverables:

\- Library page (§6.2)

\- Search + filter (team, tag, viz type)

\- Block detail / preview

\- "Dashboard'a ekle" → dashboard picker → insert flow (§6.4)



\---



\## 9. Forward-Compat with Phase 7



When Phase 7 (Concept Foundation) lands:



1\. \*\*Semantic tag allow-list becomes concept registry.\*\* YAML in `concepts/global.yaml` and `concepts/treasury.yaml`. The Python allow-list is removed; loaded from YAML.



2\. \*\*Variable migration.\*\* A one-time script converts variable definitions to concept-aware filters:

&#x20;  - `semantic\_tag` → `concept` reference

&#x20;  - `allowed\_values` → if not consistent with concept's `canonical\_values`, flagged for review

&#x20;  - Type alignment: `date` + `as\_of\_time` → concept handles type uniformly



3\. \*\*Block query rewriting (optional).\*\* When concept-aware compilation arrives, block queries can be left as-is (raw SQL with bind vars). The filter compiler is additive — applies extra `AND` clauses derived from concept-level filters. No block need be rewritten.



4\. \*\*Dashboard filter → scope contract.\*\* Phase 7's scope contract subsumes dashboard filters. Migration: existing `filters` array becomes the `interactive\_filters` list in scope contract. Add `pinned\_filters: \[]`.



5\. \*\*Column binding inference.\*\* Table doc's `suggested\_semantic\_tag` becomes the seed for binding inference. Migration is a transpose: per-table doc reorganized by concept.



These migrations are scripted and idempotent. Phase 6.5 artifacts (blocks, dashboards, table docs) remain valid throughout.



\---



\## 10. Acceptance Criteria



\### 10.a — Block save and run



\- A user can create a block via the editor UI and persist it to S3.

\- SQL with `WHERE x = :foo` and matching `variables` saves successfully.

\- SQL with `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, `TRUNCATE` is rejected at save with a clear error.

\- SQL with an undeclared bind variable is rejected.

\- Variable without `semantic\_tag` is rejected.

\- Variable with `semantic\_tag` not in allow-list is rejected.

\- Block can be executed with defaults via the preview pane and renders a chart.

\- Block versioning: saving an edited block creates `version: N+1`; v\_N remains readable.

\- Test coverage: at least one test per validation rule, one e2e test (create → save → run → render).



\### 10.b — Table documentation



\- Extended table doc YAML loads cleanly for top 5 treasury tables.

\- LLM block-authoring chat includes `suggested\_variable`, `suggested\_semantic\_tag`, `distinct\_values\_sample` in its context.

\- A new block authored by LLM for `TRD\_BRANCH\_POSITION` uses suggested variable names and semantic tags.

\- `distinct\_values\_sample` cron updates samples nightly and writes back to YAML.



\### 10.c — Dashboard-level filter



\- A dashboard can declare filters; they appear in the filter bar.

\- Adding a block proposes auto-bindings by semantic\_tag.

\- "Filter eklemek ister misiniz?" prompt fires for unbound variables.

\- Block-level constant override works (verified: dashboard filter change does not affect block with constant binding).

\- Single "Güncelle" button triggers per-block resolution; cache states are correct.

\- Subset routing verified: widening a date range refetches; narrowing it does not (DuckDB-filter on cached result).

\- LRU eviction kicks in at 2 GB session cap.



\### 10.d — Library MVP



\- Library page lists all blocks the current user has access to.

\- Search across title, description, documentation finds expected blocks.

\- Team / tag / viz-type filters narrow results.

\- "Dashboard'a ekle" inserts a block into the chosen dashboard with auto-binding applied.



\---



\## 11. Open Questions / Backlog (not blocking implementation)



\- \*\*Block-level result sharing across dashboards in the same session.\*\* If two dashboards in the same session use the same block with the same resolved vars, can they share cache? Defer to v0.

\- \*\*Cross-block cache hits.\*\* Two different blocks with identical resolved SQL could share a result. Out of scope; treat each block independently.

\- \*\*Incremental fetch on superset miss.\*\* Cache widens by fetching only the delta. Backlog.

\- \*\*Free-form text variables.\*\* Re-evaluate if user demand justifies the security review work.

\- \*\*Multi-block templates\*\* (one YAML, multiple blocks). Defer to Phase 10 (marketplace).

\- \*\*Block visibility / RBAC.\*\* All blocks team-visible in v0. Public/private and cross-team visibility in Phase 12.



\---



\## 12. Glossary



\- \*\*Block\*\* — atomic visualization artifact: SQL + variables + viz config. Versioned, immutable per version.

\- \*\*Dashboard\*\* — composition of blocks with shared filters.

\- \*\*Variable\*\* — typed parameter in a block's SQL, declared with a semantic\_tag.

\- \*\*Semantic tag\*\* — predefined category that gives meaning to a variable (e.g., `currency`). Forward-compatible with Phase 7 concepts.

\- \*\*Filter\*\* — dashboard-level UI widget that feeds variables across blocks via bindings.

\- \*\*Variable binding\*\* — explicit link from a dashboard filter (or constant) to a block variable.

\- \*\*Subset routing\*\* — serving a tighter filter request from a previously cached wider result, without hitting Oracle.

\- \*\*Cache key\*\* — hash of (block\_id, version, resolved\_variables) identifying a cached result.



\---



\*End of spec. Revise via PR. When implementation begins, link this file from CLAUDE.md and reference the relevant sub-phase from the kickoff prompt.\*

