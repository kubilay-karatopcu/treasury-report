# Treasury Studio — Presentation Editor Module

This module adds a block-based, LLM-driven presentation editor to the existing **Treasury Report Platform** (Flask + Jinja + Tabler + Oracle EDW + OpenShift/ODH). Users select tables ("basket"), then iteratively build & edit one-pager presentations. An LLM produces JSON Patches (RFC 6902) that mutate a manifest; the frontend renders blocks (KPI, charts, narratives, section headers) and applies patches with no full re-render.

> **You are working inside an existing Flask production app.** Do NOT scaffold a new app. Do NOT introduce new heavy infrastructure. Add this module as a **Flask Blueprint** plugged into the existing app. Reuse existing patterns from `deposit_panel` (the closest analog) and the shared `DataClient`.

> **Read `reference/` BEFORE writing any code.** That folder contains read-only copies of the actual app files. The patterns below are summaries; the real files are authoritative.

> **Güncel durum (Temmuz 2026):** Faz 1–7 üretimde; Faz 8 (scope contract), 9 (Keşif/Hazırlık atölyeleri) ve 10 (Bloklar/marketplace) spec'leri `docs/PHASE_*.md`'de ve büyük ölçüde uygulanmış durumda. Bu dosyanın alt yarısındaki faz planları TARİHSEL sözleşmedir (kilitli kararlar geçerli); anlık yetenek listesi için aşağıdaki **"Shipped capabilities"** bölümüne ve `ROADMAP.md`'ye bak.

---

## Shipped capabilities — bu spec'ten sonra eklenenler (özet)

Aşağıdakiler üretimde; ilgili kod referanslarıyla:

- **Sayfa hiyerarşisi (Page > Başlıklar)** — `manifest.pages: [{id, title}]` canvas
  üstünde sekmeler; `section_header.page` bölümü bir sayfaya bağlar (alansız =
  her sayfada), `filters[].page` filtreyi o sayfaya kısıtlar. Apply-filters
  `block_ids` ile yalnız aktif sayfanın bloklarını çözer. Sayfasız manifest'ler
  birebir eski davranışta. (`manifest.py`, `PageTabs.jsx`, `test_pages.py`)
- **Container blokları** — `carousel` (slaytlı; slide = leaf ya da `canvas`) ve
  `canvas` (12 kolonluk grid, leaf-only). Derinlik sınırı:
  section > carousel > canvas > leaf. (`manifest.py`, `Carousel.jsx`, `Canvas.jsx`)
- **Yeni chart tipleri** — `combo_chart` (çift eksen bar+line, seri başına
  kind/axis), `waterfall_chart` (col0=etiket, col1=delta, col2 ops. toplam
  bayrağı; kümülatif renderer'da), `scatter_chart` (bubble; col0=ad, col1=x,
  col2=y, col3 ops. boyut, col4 ops. yatay referans değeri → `source:"query"`
  çizgisi). (`execute_block_sqls.py`, `blocks/*.jsx`)
- **Referans çizgileri** — `config.ref_lines: [{axis:'x'|'y', value, label?,
  color?, source?}]` tüm kartezyen chart'larda; Properties panelinden ve
  chat'ten yönetilir; `source:'query'` girdiler SQL'den tazelenir.
- **Eksen limitleri** — `config.x_min/x_max/y_min/y_max` (sayı/null) tüm
  kartezyen chart'larda; waterfall'da otomatik kümülatif-aralık kırpması var,
  elle limit onu ezer.
- **Filtre uygulama performansı** — apply-filters blokları 6 worker'lı thread
  havuzunda paralel çözer (DuckDB erişimi oturum kilidiyle serileşir); ayrıca
  **oturum tablo-önbelleği**: `basket[].duck_cache: true` tablolar ilk
  kullanımda bir kez Oracle'dan oturum DuckDB'sine çekilir (TTL 15 dk) ve blok
  SQL'leri `presentations/sql/oracle_duck.py` çevirisiyle (NVL/FROM DUAL/
  ROWNUM/REGEXP_SUBSTR/TO_CHAR/TO_NUMBER/RATIO_TO_REPORT) lokalde koşar; hata
  olursa sessizce Oracle'a düşülür. (`routes.py` apply-filters,
  `test_table_cache_apply.py`)
- **Dış-yazar manifest tazeleme** — importer script'leri manifest.json'ı S3'e
  uygulama dışından yazar; `PresentationSession` ETag değişimini (≥30 sn
  throttle) görüp bellek kopyasını yeniler. (`session.py`)
- **Deposits taşıma hattı** — `jobs/deposits_pipeline.py` (ofiste tek koşu:
  NIM_calculation motorlarının portuyla plot-hazır `PRISMA_DEP_*`/`PRISMA_NP_*`
  tabloları + GRANT + S3 tablo dokümanları) ve `jobs/deposits_dashboards.py`
  (5 deposits dashboard'unu kaynak siteyle blok-blok birebir üretip S3'e
  yazar: Bennet waterfall carousel'leri, bubble/heatmap'ler, sayfalar,
  filtreler). DuckDB tabanlı semantik regresyon testleri
  (`test_deposits_dashboards_*.py`) SQL'leri kaynak formüllerle sayı sayı
  karşılaştırır ve üretim lehçe çeviricisini kullanır.
- **Bant sıralaması** — AUM/vade bandı etiketleri her yerde sayısal alt sınıra
  göre sıralanır (ilk sayı × K/M/B çarpanı): SQL'de `band_order_expr`,
  Python'da `_band_sort_key`, filtre `allowed_values` listelerinde.
- **KVKK maskesi** — müşteri adları (`FULL_NM`) PRISMA tablolarına ve
  manifest'lere daima maskeli yazılır (`X*** Y***`); düz PII asla depolanmaz.

---

## Repository context (existing patterns to follow)

The Treasury Platform follows these conventions — **mirror them**. See `reference/` for actual source files.

- **App structure**: single `app.py` at the root contains the main Flask app, the `User` class, `load_user`, and routes for legacy pages (`/oranlar`, `/competitor`, `/historic`). Modules are added as Blueprints registered via `app.register_blueprint(...)`.
- **Blueprint pattern**: see `reference/modules/deposit_panel/app_deposit.py`. Pattern: `xxx_bp = Blueprint("xxx", __name__, template_folder="templates", static_folder="static")`, routes decorated with `@xxx_bp.route(...)`, registered in `app.py` with `app.register_blueprint(xxx_bp, url_prefix="/xxx")`.
- **Auth — Flask-Login with `current_user`**:
  - `from flask_login import current_user, login_required`
  - `User` class extends `UserMixin`; attributes: `name`, `sicil`, `ip`, `department`, `password`
  - **Use `current_user.sicil` as the user identifier** (string like "A16438"). NOT `g.user.id`, NOT `current_user.id`.
  - All authenticated routes need `@login_required`.
- **DataClient**: `DataClient.py` at root, instantiated as global `dc` in `app.py`. Main method: `dc.get_data(base_prefix=..., dataset=..., query="./queries/X.sql", query_params={...})` returns DataFrame. SQL files live under `./queries/`. NEVER instantiate raw `oracledb` connections.
- **DataFrame → JSON**: `flask.Response(df.to_json(orient="records"), mimetype="application/json")`. NEVER `jsonify(df.to_dict(...))` — it emits `NaN` which breaks `JSON.parse` on the client. Confirmed production bug fix.
- **Templates**: `base.html` is the shared layout. Block slots available: `title`, `head`, `filter_panel`, `content`, `scripts`. `editor.html` will override `content` and `scripts`; do NOT use `filter_panel` (the editor has its own sidebar). See `reference/templates/rates.html` and `competitor.html` for extension examples.
- **CSS — Tabler-based with custom tokens**: see `reference/static/css/styles.css`. Reuse existing tokens (`--bs-primary` etc.) and Tabler component classes (`.card`, `.btn`, `.btn-primary`, `.page-header`, `.navbar-vertical`). Do NOT define new color tokens.
- **CDN URLs — no `@` allowed**: corporate proxy mangles URLs containing `@`. Use cdnjs.cloudflare.com paths. See `base.html` for examples of how libraries are loaded.
- **Frontend bundling**: esbuild üç bundle üretir (`bash presentations/build.sh`). Editör bundle'ı (`static/js/bundle.js`) git'te taşınır — ofis makinesi build almaz; JS değişikliği yapan, bundle'ı da build edip commit'ler.
- **OpenShift reverse proxy**: SSE/streaming endpoints must set `X-Accel-Buffering: no` header. The existing WSGI middleware injects `SCRIPT_NAME = '/proxy/8080'`; respect this. (The deposit module deals with this; mirror its pattern.)
- **LLM endpoint**: Qwen3.5-27B-GGUF, OpenAI-compatible. Token and URL in app config. Tool calling is broken in the GGUF wrapper — use system prompt + JSON parsing in message content. See `reference/modules/deposit_panel/api_deposit.py` for the existing client pattern.
- **Threading**: heavy work outside the lock; only the atomic reference swap inside. Snapshot pattern: `df_snap = current_df` inside lock, work on snapshot outside. See deposit_panel for examples.
- **`load_user()` is cached**: see `app.py` lines ~280-305. 5-minute LDAP cache. Don't add another auth round-trip per request.
- **No `current_app.config` for DataClient access**: existing modules import `dc` directly: `from app import dc` or use the global. For the new module, prefer dependency injection — `presentations` blueprint reads `current_app.config["DATA_CLIENT"]` so `run_local.py` can swap it for a stub. If this conflicts with existing pattern, defer to existing.

---

## Module: `presentations`

### Goals

1. Users build "Presentations" — ordered lists of typed blocks rendered as a one-pager.
2. The frontend is a single React bundle. The Flask backend serves the shell template and the JSON/SSE APIs.
3. LLM (Qwen) produces JSON Patches against a Manifest schema. Patches apply atomically; only affected blocks re-render.
4. Per-presentation **DuckDB session** holds fetched data. Edits route to DuckDB (transform) or back to Oracle (re-fetch) based on which paths the patch touches.
5. Snapshots persist to S3 (parquet + frozen manifest). Recipes (manifest only) re-run from scratch.

### Non-goals (out of scope for v1)

- No multi-user collaborative editing (last-write-wins is fine; advisory lock per presentation).
- No PDF/Excel upload to basket yet (data sources = EDW tables only).
- No custom CSS / theme overrides. One theme: Tabler tokens.
- No Schema Explorer integration yet (will hook in later via "Add to basket" deeplink).
- No undo/redo in v1 (manifest is versioned in DB; rollback is "load previous version").

---

## File layout

Gerçek repo düzeni (özet — tam liste için ağaca bak):

```
repo/
├── app.py                        # ana Flask app (blueprint kayıtları burada)
├── DataClient.py                 # Oracle/S3 erişimi (havuz + get_data)
├── queries/                      # legacy sayfa SQL'leri + queries/deposits/
├── jobs/                         # OFİSTE koşulan tek-atımlık script'ler
│   ├── deposits_pipeline.py      #   NIM verisi → PRISMA_* tabloları + dokümanlar
│   ├── deposits_dashboards.py    #   PRISMA_* → 5 deposits dashboard'u (S3 manifest)
│   ├── generate_table_docs.py
│   └── sample_distinct_values.py
├── prisma_home/                  # PRISMA kabuk sayfaları + editor_dark.css
├── docs/                         # ROADMAP, PHASE_6_5/7/8/9/10 spec'leri, backend notları
└── presentations/                # ana modül
    ├── routes.py                 # Sunum endpoint'leri (apply-filters dahil)
    ├── routes_blocks.py / _concepts.py / _kesif.py / _library.py / _scope.py
    ├── manifest.py               # şema + doğrulayıcılar (pages/container/ref_lines)
    ├── patch.py, session.py, duck.py, graph.py, llm.py, store.py
    ├── blocks/                   # Block/Variable pydantic şemaları (Phase 6.5)
    ├── variables/                # resolver + semantic_tags
    ├── sql/                      # validator, binder, oracle_duck (lehçe çevirisi)
    ├── dashboards/               # DashboardFilter şeması + binding resolver
    ├── cache/                    # block_cache (DuckDB, subset routing) + library cache
    ├── concepts/                 # Phase 7: registry, compiler, review, user-scope
    ├── catalog/                  # tablo dokümanları + concept YAML'ları + loader
    ├── scope/                    # Phase 8: scope contract, materialize, store
    ├── discovery/, drafts/, table_docs/, python_runtime/, uploads.py
    ├── nodes/                    # LLM graph düğümleri + execute_block_sqls
    ├── prompts/                  # tüm LLM metinleri (asla inline yazma)
    ├── templates/presentations/  # editor/list/kesif/hazirlik/bloklar sayfaları
    ├── static/js/editor/         # React kaynak (App, components/, blocks/, lib/)
    ├── static/js/bundle.js       # editör bundle'ı — GİT'TE (aşağıya bak)
    ├── static/css/editor.css     # açık tema token'ları (editor_dark.css remap eder)
    ├── build.sh                  # üç bundle'ı üretir
    └── tests/                    # pytest — 1000+ test
```

**Registration** in existing `app.py`:

```python
from presentations import presentations_bp
app.register_blueprint(presentations_bp, url_prefix="/presentations")
```

---

## Manifest schema

A Presentation is:

```json
{
  "id": "p_abc123",
  "version": 7,
  "owner_id": "A16438",
  "created_at": "2026-05-07T10:00:00Z",
  "updated_at": "2026-05-07T11:30:00Z",
  "meta": {
    "title": "Q4 2025 Treasury Performance",
    "eyebrow": "Treasury Report",
    "date": "December 2025",
    "author_label": "kubilay (A16438)"
  },
  "basket": [
    {
      "table": "EDW.DEPOSITS_DAILY",
      "columns": ["BRANCH_CODE", "SEGMENT", "DATE", "BALANCE_TRY"],
      "row_filter": "SEGMENT IN ('RETAIL', 'SME')"
    }
  ],
  "blocks": [
    {
      "id": "h_overview",
      "type": "section_header",
      "title": "Overview",
      "config": {}
    },
    {
      "id": "b_kpi_deposits",
      "type": "kpi",
      "title": "Total Deposits",
      "locked": false,
      "source": "EDW.DEPOSITS_DAILY",
      "config": {
        "value": 487.2,
        "unit": "B TRY",
        "delta": 4.8,
        "delta_label": "vs Q3 2025",
        "period": "Q4 2025"
      }
    }
  ]
}
```

### Block types (güncel)

Leaf tipler (SQL sözleşmesi: `execute_block_sqls.py` başındaki yorumlar
otoritedir — col0 = kategori/etiket, sonraki kolonlar veri):

| type              | config schema (veri alanları)                                              |
| ----------------- | --------------------------------------------------------------------------- |
| `kpi`             | `{ value, unit, delta, delta_label, period }`                               |
| `narrative`       | `{ text }`                                                                  |
| `bar_chart`       | `{ categories: [str], series: [{name, values}] , stacked?, horizontal?, distributed?, colors? }` |
| `line_chart`      | `{ x_axis: [str], series: [{name, values}], curve?, stroke_width?, show_markers? }` |
| `area_chart`      | line_chart + `fill_opacity?`                                                 |
| `pie_chart`       | `{ labels: [str], values: [num], donut?, legend_position? }`                 |
| `heatmap`         | `{ x_axis: [str], series: [{name, values}] }` — SQL long format (satır, kolon, değer) pivotlanır; Δ verisi ıraksak, tek işaret sıralı renk skalası |
| `radial_bar`      | `{ value, max?, label? }`                                                    |
| `combo_chart`     | `{ categories, series: [{name, values, kind: bar\|line, axis: left\|right}], left_axis_title?, right_axis_title? }` |
| `data_table`      | `{ columns: [{field, header?, type?}], rows: [obj] }` (AG Grid)              |
| `waterfall_chart` | `{ categories, values, totals?: [bool], unit? }` — kümülatif renderer'da; `unit:"%"` etiketleri 2 ondalık |
| `scatter_chart`   | `{ points: [{name, x, y, size?}], x_title?, y_title? }` (Apex bubble)        |

Container tipler: `section_header` (yalnız top-level; `children` + ops. `page`),
`carousel` (section içinde; slide = leaf ya da canvas), `canvas` (12 kolon grid,
leaf-only). Kartezyen chart'larda ortak opsiyoneller: `width` (`full|2/3|1/2|1/3`),
`ref_lines`, `x_min/x_max/y_min/y_max`, `show_data_labels`.

### Manifest — güncel üst-düzey alanlar

Yukarıdaki örnek çekirdek şemadır; üretimdeki manifest ek olarak şunları taşır
(hepsi opsiyonel, yokluğu eski davranışı korur):

- `blocks` artık İÇ İÇEDİR: top-level yalnız `section_header`, leaf'ler
  `section.children` içinde (carousel/canvas da orada). Düz `blocks[]`
  taraması leaf'leri kaçırır — `manifest.iter_all_blocks()` kullan.
- `pages: [{id, title}]` + `section.page` + `filters[].page` — sayfa
  hiyerarşisi (yukarıdaki Shipped capabilities bölümüne bak).
- `filters: [DashboardFilter]` (Phase 6.5.c) + `filter_state` — dashboard
  filtre barı; bloklara `variable_bindings {var: {from_filter}}` ile bağlanır.
- `basket[]` girdileri `alias`, `column_concepts`, `duck_cache` taşır.
- `scope_ref` (Phase 8), `uploads`, `bound_experts`, `user_concepts` (7.d).

### Immutable fields

`id`, `type`, `locked`, and the schema shape itself. The patch validator MUST reject any patch that touches these. İzinli patch kökleri: `/blocks/`, `/meta/`, `/filters`, `/filter_state`, `/user_concepts`, `/pages` (`ALLOWED_PATCH_PREFIXES`).

### Invariants (validated server-side)

- Chart length consistency: `len(series[i].values) == len(categories | x_axis)` for all `i`.
- All paths in a patch start with `/blocks/{N}/`, `/meta/`, or are `/blocks/-` (append) / `/blocks/{N}` (insert/remove).
- `value` numeric where schema requires.

---

## JSON Patch — what we support

Subset of RFC 6902:
- `replace` — common case
- `add` — including `/blocks/-` (append) and `/blocks/N` (insert)
- `remove`

We do NOT implement `move`, `copy`, `test`. If LLM generates one, validator rejects.

`patch.py` exports:
- `apply_patches(state, patches) -> new_state` (deep-copy, atomic per call)
- `compute_inverse(state, patches) -> inverse_patches` (for future undo)
- `classify_paths(patches) -> {"meta": [...], "blocks": {N: [...]}, "structural": [...]}`
  - structural = blocks add/remove/reorder

The frontend has its own `patch.js` mirroring this; both must be kept in sync. Add a Python test that round-trips known fixtures and a JS test that asserts the same output for the same input.

---

## Edit routing — three layers

A patch can require:

1. **Render only** — frontend re-renders from new manifest. Examples: title rename, narrative rewrite, chart type swap, sort order.
2. **DuckDB requery** — frontend asks server for new data because aggregation/filter/window changed but tables in basket are sufficient. Server runs DuckDB SQL, returns new chart `config.values`. Patch is applied as a follow-up.
3. **Oracle re-fetch** — basket changed (new table, new column projection, new row filter). Server fetches fresh data into DuckDB session, then path 2.

`plan_fetch.py` decides which layer based on the user's intent + diff between current and proposed state.

---

## LangGraph flow

```
user_message
    │
    ▼
[route_intent]  ──► block-scoped or global-scoped?
    │
    ▼
[plan_fetch]    ──► render-only / requery / re-fetch?
    │
    ├─(re-fetch)──► [fetch_data] ──► [generate_patch]
    │
    ├─(requery)───► [generate_patch]  (with new DuckDB result available as context)
    │
    └─(render)────► [generate_patch]
                          │
                          ▼
                   [validate_patch]
                          │
              ┌───────────┴───────────┐
              │ valid                 │ invalid (1 retry with error feedback)
              ▼                       │
         [apply_patch]            [generate_patch]
              │
              ▼
       SSE events to client
```

State carried through the graph: `manifest`, `selected_block_id`, `user_message`, `duck_views_available`, `pending_patches`, `validation_errors`, `retries_left`.

---

## API surface

All endpoints are JSON unless noted. All require auth via existing `load_user()`.

| Method | Path                                          | Purpose                                              |
| ------ | --------------------------------------------- | ---------------------------------------------------- |
| GET    | `/presentations/`                             | List user's presentations (HTML page)                |
| GET    | `/presentations/<pid>`                        | Editor shell page (HTML, mounts React)               |
| POST   | `/presentations/`                             | Create new presentation, returns `{id}`              |
| GET    | `/presentations/<pid>/manifest`               | Current manifest JSON                                |
| POST   | `/presentations/<pid>/chat`                   | Submit chat message, returns SSE stream URL token    |
| GET    | `/presentations/<pid>/stream/<token>`         | SSE stream of patch events                           |
| POST   | `/presentations/<pid>/basket`                 | Add/remove table from basket (triggers re-fetch)     |
| GET    | `/presentations/<pid>/sources`                | List available tables grouped by domain (catalog)    |
| POST   | `/presentations/<pid>/snapshot`               | Save snapshot to S3, returns shareable link         |
| GET    | `/presentations/snapshot/<sid>`               | View read-only snapshot (presentation mode)          |

### SSE event types

```
event: patch
data: {"patches": [...], "explanation": "..."}

event: status
data: {"phase": "fetching" | "thinking" | "applying"}

event: error
data: {"message": "..."}

event: done
data: {"manifest_version": 8}
```

Frontend applies each `patch` event immediately via local `applyPatches`.

---

## Session lifecycle

`session.py` exposes:

```python
class PresentationSession:
    def __init__(self, user_id: str, presentation_id: str): ...
    def get_manifest(self) -> dict: ...
    def update_manifest(self, patches: list[dict]) -> dict: ...
    def get_duck_conn(self) -> duckdb.DuckDBPyConnection: ...
    def fetch_basket(self, basket: list[dict]) -> None: ...   # Oracle → DuckDB
    def close(self) -> None: ...

class SessionRegistry:
    def get_or_create(self, user_id: str, presentation_id: str) -> PresentationSession: ...
    def cleanup_idle(self, idle_seconds: int = 1800) -> None: ...
```

Storage: `/tmp/presentations/{user_id}/{presentation_id}/`
- `session.duckdb`
- `manifest.json` (last persisted snapshot)
- `lock.pid` (advisory)

Sessions are best-effort — pod restart wipes them. Manifest is also persisted to a small Oracle table (or S3 JSON, decide at impl time) for durability across restarts.

---

## Frontend

Single React app, mounted at `<div id="presentation-root" data-presentation-id="...">`. Initial manifest passed via `<script id="initial-manifest" type="application/json">{...}</script>`. esbuild bundles to `static/js/bundle.js`. No Tailwind compiler — use plain CSS + Tabler classes; for component-internal needs use inline style objects with imported `theme.js` tokens.

State: **Zustand** store holds `manifest`, `selectedBlockId`, `chatHistory`, `loading`, `flashIds`. Connection to backend is `EventSource` for SSE. On each `patch` event, store calls `applyPatches` from `lib/patch.js` and triggers React re-render.

Two view modes: `edit` and `presentation`. Stored in store as `viewMode`. Sidebar content swaps based on mode. Toggle via header button.

Gerçek bağımlılıklar (`presentations/package.json` otoritedir): `react` /
`react-dom` 18, **`apexcharts` + `react-apexcharts`** (chart motoru — Recharts
DEĞİL), `ag-grid-community` + `ag-grid-react` (data_table), `zustand`,
`lucide-react`, `marked`, `@uiw/react-codemirror` (+lang-sql/python),
`@xyflow/react` (Hazırlık graf editörü), `@cosmograph/react` (Keşif grafı).
Ortak Apex ayarları `blocks/chartHelpers.js`'te — yeni chart eklerken oradan
başla (tema, referans çizgileri, eksen limitleri, formatlayıcılar orada).

Build (`presentations/build.sh` → editor + hazirlik + kesif bundle'ları):
```bash
cd presentations && bash build.sh
```

**Bundle politikası:** `static/js/bundle.js` (editör) **git'te taşınır** —
ofiste npm/build gerekmez; editör JS'ine dokunan her değişiklikte bundle
yeniden build edilip birlikte commit'lenir. `hazirlik`/`kesif` bundle'ları
(duckdb-wasm asset'leri büyük) gitignore'da kalır ve prod'da build.sh ile
üretilir.

---

## Configuration

`flask_app/config.py` (or wherever the app reads config) gets these keys; module reads via `current_app.config`:

```python
PRESENTATIONS_LLM_ENDPOINT = "https://smg-llm-api.../v1/chat/completions"
PRESENTATIONS_LLM_MODEL = "qwen3.5-27b"
PRESENTATIONS_LLM_TOKEN = "<from env>"
PRESENTATIONS_S3_BUCKET = "treasury-snapshots"
PRESENTATIONS_S3_PREFIX = "presentations/"
PRESENTATIONS_SESSION_DIR = "/tmp/presentations"
PRESENTATIONS_SESSION_IDLE_TIMEOUT = 1800
```

No new infra dependencies. Reuse the existing S3 client and Oracle DataClient.

---

## Testing strategy

- **`test_patch.py`** — fixtures of (state, patches) → expected_new_state. Round-trip with `compute_inverse`. Reject invalid ops.
- **`test_manifest.py`** — schema validators: chart length consistency, immutable fields, type-specific configs.
- **`test_session.py`** — mock Oracle, real DuckDB; verify Arrow bridge handles NaN, dtype edge cases.
- **`test_llm_smoke.py`** — hits real Qwen with `pytest -m integration`. The 12 cases from `qwen_patch_test.py` (the standalone test we already ran). Track pass rate over time.
- **`test_deposits_dashboards_cost.py` / `_pages.py`** — importer SQL'lerini
  DuckDB'de (üretim `oracle_duck` çevirisiyle) koşup kaynak NIM motorlarının
  formülleriyle sayı sayı karşılaştırır (Bennet ayrıştırması, ₺M/bps birimleri,
  bant sıralaması, bileşik→basit geri çevrim).
- **`test_table_cache_apply.py`** — apply-filters'ın oturum tablo-önbelleği:
  ilk apply 1 tablo yüklemesi, farklı filtreyle ikinci apply 0 Oracle çağrısı;
  ayrıca `_process_block` içinde yerel-import gölgeleme regresyon taraması.
- **`test_pages.py`** — sayfa hiyerarşisi doğrulaması + importer sayfa ataması.
- Bilinen kırıklar: `examples/` fixture'ları repoda olmadığından ~9 test + 95
  collection hatası baştan beri kırmızıdır — regresyon değildir, yeşile
  çevirmek için fixture'ların eklenmesi gerekir.

---

## Migration path

1. Develop locally with Claude Code; use `examples/` fixtures + a small SQLite or in-memory DataClient stub for offline iteration.
2. Push to GitHub.
3. Pull on office machine. Add to `flask_app/__init__.py`:
   ```python
   from flask_app.presentations import presentations_bp
   app.register_blueprint(presentations_bp, url_prefix="/presentations")
   ```
4. Editör bundle'ı git'te gelir (build gerekmez). `hazirlik`/`kesif`
   bundle'larına dokunulduysa `cd presentations && bash build.sh`.
5. Deploy via existing OpenShift pipeline.

---

## Coding standards

- Python: type hints everywhere, dataclasses for structured data, no globals beyond config.
- JS: function components only, no class components. Hooks. Zustand for global state, `useState` for purely local.
- All LLM-facing strings live in `prompts/*.txt` — never inline. Loaded once at module import.
- All Oracle queries live in `duck.py::fetch_*` functions. Routes never call Oracle directly.
- Logging: use the app's existing logger (`current_app.logger`), level INFO for state transitions, DEBUG for patch contents, ERROR for validation failures.
- No `print` statements in committed code.
- Comments only when the *why* is non-obvious. Code should read top-to-bottom.

---

## What to build first

Phase the implementation as separate PRs / commits. Each phase ends with something runnable.

**Phase 1 — Skeleton (no LLM, no DuckDB)**

Goal: blueprint registers correctly, navigating to `/presentations/p_demo` shows the seed report rendered read-only.

Concrete scope:
- Blueprint factory + two routes (list, editor)
- Editor template extends `base.html`, embeds the seed manifest as `<script id="initial-manifest" type="application/json">`
- React mounted from CDN (no build step in Phase 1) — use UMD builds of React 18, Recharts, lucide-react. All from cdnjs.cloudflare.com, no `@` in URLs
- Sidebar shows hardcoded data sources (read from `examples/sample_catalog.json`)
- Blocks render read-only (KPI, bar, line, narrative, section_header)
- TOC sidebar in presentation mode (just for completeness, even though there's no toggle yet)
- NO chat, NO editing, NO lock toggle, NO basket persistence, NO save button (or button exists but disabled with tooltip "Phase 5'te geliyor")
- NO Oracle access, NO DuckDB
- The list page returns 2 hardcoded entries (`p_demo`, `p_example2`); only `p_demo` actually loads
- For local dev: `examples/templates/base.html` is a minimal Tabler-CDN-loading stub mirroring the real `base.html`'s block names. `run_local.py` uses `ChoiceLoader` so real templates take precedence; stub is fallback
- For local dev: fake `current_user` injected via `LOGIN_DISABLED=True` config and a custom `before_request` that sets a fake user with `.sicil = "A16438"`. NO `g.user` — real app uses `current_user`, mirror that.

Phase 1 ends with: `cd examples && python run_local.py` → browser shows the report.

**Phase 2 — Manifest + patches**
`manifest.py`, `patch.py` (Python + JS mirror), tests passing. Frontend lets user click blocks, lock/unlock, scroll. No backend mutation yet.

**Phase 3 — Chat + LLM (no data layer)**
`llm.py`, `prompts/`, `nodes/generate_patch.py`, `nodes/validate_patch.py`, `nodes/apply_patch.py`. Wire up SSE. User can type "switch to YTD" and see the KPI block change. Data is still hardcoded; LLM only edits config values.

**Phase 4 — DuckDB session**
`session.py`, `duck.py`. Basket → Oracle fetch → DuckDB views → blocks query DuckDB. The "render-only vs requery vs re-fetch" routing in `plan_fetch.py`.

**Phase 5 — Persistence + share**
`store.py` (S3 snapshots), `/snapshot` endpoint, presentation-mode page that loads frozen manifest read-only.

**Phase 6 — Polish + layout**
Loading states, error UX, lock semantics, basket UI completeness, presentation-mode TOC, share button.

Plus **block width / multi-column layout** (deferred from Phase 4):
- Add an optional `width` field to non-section blocks: `"full" | "1/2" | "1/3" | "2/3"`. Default `"full"` (current behavior).
- Frontend canvas wraps blocks in a CSS grid; consecutive non-`full` blocks share a row.
- Update LLM prompts so the model can pick a width when the user asks "yan yana" / "side by side".
- Sliders, tabs, and conditional rendering stay out of scope (one-pager constraint).

**Phase 6.5 — Variable Binding MVP** *(transitional, ships in parallel with team block production)*

Full spec: **[`docs/PHASE_6_5_SPEC.md`](docs/PHASE_6_5_SPEC.md)** — read this before starting any work on Phase 6.5.

This phase introduces a user-authored variable layer on top of the existing block-render engine, so the team can start producing and sharing reusable blocks while later phases (concept layer, scope contract, marketplace) are being built. It is **forward-compatible with Phase 7** by design — every variable carries a mandatory `semantic_tag` that becomes the migration key into the concept registry.

Scope (high level):

- Blocks carry a user-written SQL query with `:bind_var` placeholders and a `variables` array (name, type, semantic_tag, default, allowed_values).
- Dashboards gain a top-level `filters` array; filters auto-bind to block variables by matching `semantic_tag`.
- DuckDB gains a per-block cache keyed by resolved variables, with subset routing (narrower filter served from cached parent without an Oracle round-trip).
- A Library MVP page lets the team browse, search, and insert saved blocks into dashboards.

Sub-phases (each ships independently, in this order):

- **6.5.a** — Block save and run (block schema, SQL validator, variable resolver, bind expansion, block editor UI, run endpoint). Acceptance: spec §10.a.
- **6.5.b** — Table documentation enhancement (extended schema with `filterable`, `filter_role`, `suggested_variable`, `suggested_semantic_tag`, `distinct_values_sample`; LLM prompt integration; nightly distinct-values cron; data team migration of top 5 tables). Acceptance: spec §10.b.
- **6.5.c** — Dashboard-level filter (dashboard schema extension, filter bar UI, auto-binding by semantic_tag, "filter eklemek ister misiniz?" prompt, block-level constant override, block cache + subset routing). Acceptance: spec §10.c.
- **6.5.d** — Library MVP (browse, search, filter, preview, insert into dashboard with auto-binding). Acceptance: spec §10.d.

Reference fixtures (representative blocks + dashboard + extended table doc + expected resolved SQL) live in `examples/phase_6_5/`. Use these as authoritative shape references when implementing.

**Locked design decisions for Phase 6.5** (do NOT reopen — see spec §1–§4 for the full reasoning):

1. **Variable bindings use a mandatory `semantic_tag` from a fixed allow-list** (spec §3.2). This tag is the forward-compatibility hook for Phase 7's concept registry. `semantic_tag: other` is the escape hatch and is flagged in the UI. Variable names themselves are user-chosen and not enforced.
2. **Blocks are immutable per version.** Editing creates `version: N+1`; dashboards reference `{team, id, version}` triples. Deletion is soft (`deprecated: true`).
3. **SQL whitelist is parser-based: only `SELECT` and `WITH` statements allowed.** All `:bind_var` references must match declared block variables. No DDL, no DML writes, no procedural blocks, no multi-statement queries. Validation runs at block save and before each execution. Implementation uses `sqlparse`.
4. **Bind variable execution never concatenates values into SQL.** `enum_multi` is expanded to positional placeholders at execution time (`IN (:list_0, :list_1, ...)`).
5. **Subset routing on block cache.** When a new resolved-variable set is a subset of a cached parent set, serve from DuckDB filter without re-fetching Oracle. Full refetch on superset miss. Incremental fetch (delta-only Oracle pull) is backlog.
6. **Per-session DuckDB block cache with a 2 GB soft cap + LRU eviction.** Eviction runs lazily before each cache write.
7. **Free-form text variables are not supported in v0.** Only `date`, `date_range`, `enum_single`, `enum_multi`, `number_range`.
8. **Forward-compat with Phase 7 is contractual, not aspirational.** Spec §9 defines the migration from variables → concept-aware filters. Implementations in 6.5.a–6.5.d MUST preserve this contract; if a deviation is needed, update spec §9 in the same PR.

Phase 6.5 does NOT change Phase 1–6 artifacts. Existing pre-6.5 blocks (with hardcoded queries and no variables) remain functional and unmodified. Existing dashboards without a `filters` array render exactly as before. Migration of existing artifacts to the variable layer is opt-in, never forced.

**Phase 7 — Concept Foundation** *(backend complete — 7.a–7.d implemented + tested; full editor UI wiring is a follow-up)*

Full spec: **[`docs/PHASE_7_SPEC.md`](docs/PHASE_7_SPEC.md)** — authoritative for all 7.a–7.d work. Read before starting any Phase 7 task.

Phase 7 introduces a concept-aware filter layer between the user and the underlying tables: a versioned YAML concept registry (global / departmental / user scopes), per-table column bindings with five transform kinds (`identity`, `map`, `lookup`, `bucket_from_range`, `time_truncation`), and a pure deterministic filter compiler that converts concept-level filter expressions into per-table SQL predicates with parameterized binds.

Sub-phases (ship independently, in order):

- **7.a** — Concept Registry + schema infrastructure. ~5–7 days. Acceptance: spec §11.a.
- **7.b** — Column Bindings + filter compiler. ~2–3 weeks. The heart of Phase 7. Acceptance: spec §11.b.
- **7.c** — Binding Inference pipeline + review UI. ~2 weeks. Acceptance: spec §11.c.
- **7.d** — User-scoped concepts + promotion flow. ~1 week. Ships independently after 7.b. Acceptance: spec §11.d.

Reference fixtures (concept YAMLs, extended table docs, compiler golden snapshots) live in `examples/phase_7/`. Use these as authoritative shape references when implementing.

**Locked design decisions for Phase 7** (do NOT reopen — see spec §10 for full list):

1. **Concept storage scope split:** global/dept in YAML (git-versioned), user-scope in DB (per-presentation JSON). No exceptions.
2. **Transform kinds frozen:** `identity`, `map`, `lookup`, `bucket_from_range`, `time_truncation`. New kinds require spec amendment.
3. **Compiler determinism:** byte-identical output for identical inputs. Critical for cache keys + testability.
4. **Confidence gating:** only `human_verified` bindings reach the compiler. No override flags. `llm_proposed` / `inferred_*` live in YAML but are gated until operator approval.
5. **User concepts are extension-only:** cannot redefine global/departmental. Cannot mask. Promotion is the only path to scope expansion.
6. **No SQL rewriting:** the compiler appends `AND` predicates. It does not parse the block's user-authored SQL.
7. **Concept-blind charts render normally** with a "filter not applied here" badge. They do not error, do not hide.
8. **LLM produces concept JSON, never SQL.** The compiler owns SQL emission. No LLM-direct-to-SQL path is acceptable in this phase.

Phase 7 does NOT change Phase 6.5 artifacts. Blocks and dashboards remain valid; the compiler is purely additive (extra predicates appended). Backward compat: blocks without `concept_ref` fall back to `semantic_tag`, tables without `concept_bindings` are concept-blind, both render correctly.

Each phase should leave the app deployable. No half-merged states.