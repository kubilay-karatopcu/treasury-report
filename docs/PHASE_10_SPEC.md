# Phase 10 — Expert Layer Spec

**Status:** Draft · 2026-05-25
**Author:** Treasury Studio Working Group
**Replaces:** Phase 10 (original) — Expert Layer + Block Marketplace
**Note:** Block Marketplace deferred to Phase 11

---

## 1. Overview

Phase 10 introduces the **Expert Layer** — a consumer-facing reading experience
that sits on top of the existing block/snapshot system. Six personas (PRISMA's
six letters: Likidite, Mevduat, Fonlama, NII, Security, Kredi) become the new
information architecture. Snapshots, blocks and (later) processes are surfaced
as **citations** under an expert's daily briefing rather than as top-level nav
entries.

The producer side (Atölye = Keşif → Hazırlık → Sunum) keeps its existing
structure but gets a unified shell and a sidebar that exposes the full
producer IA at all times.

The single most important user-facing change: the Sunum save flow now opens a
**form modal** with a `bound_experts` multi-select. This one field decides
which expert's briefing will reference the published snapshot.

### Companion artefacts

- `examples/phase_10/experts/*.yaml` — fixture YAMLs for the 6 initial experts
- `examples/phase_10/manifest_with_bound_experts.json` — example
- UX prototype: `docs/ux/PRISMA_UX_Prototip_v2.html` (preserve as design source)

---

## 2. Scope

### In scope (Phase 10)

- New global shell: top bar + Atölye sidebar + mode toggle
- New routes: `/` (consumer landing), `/uzmanlar/<code>` (expert detail),
  `/atolye/` (producer overview)
- Expert YAML schema + `ExpertStore` (LocalExpertStore initially)
- Manifest extension: `bound_experts: list[str]` field
- Snapshot save form modal with `bound_experts` selection + LLM suggestion
- Briefing engine: hybrid template + LLM auto-fill + content-hash cache
- Backward compatibility: all existing routes keep working

### Out of scope (defer to later phases)

| Item | Phase |
|---|---|
| Block / Table / Şablon library screens | 11 |
| Süreç İzleme (Process Observatory) | 11 |
| Semantic search across experts | 11 |
| Block Marketplace | 11 |
| Schema drift CI, RBAC, ratings | 12 |
| Process tagging | 13 |
| Bottom-up emergence (auto sub-experts) | 14 |
| Cross-expert briefings, knowledge graph | 15 |

The Atölye sidebar **shows** library/observatory items from Phase 10A as
placeholder links (route returns a "yakında" stub). Real implementations
come in Phase 11.

---

## 3. URL map (before → after)

| Existing URL | Phase 10 behaviour | Notes |
|---|---|---|
| `/` | becomes consumer landing (`prisma_home.landing`) | New |
| `/presentations/` | keeps URL · shell upgrades · primary access via `/atolye/sunum/` alias | List view, new chrome |
| `/presentations/<pid>` | keeps URL · shell upgrades | Editor, unchanged behaviour |
| `/presentations/snapshot/<sid>` | keeps URL · shell upgrades (no sidebar in snapshot view) | Read-only render |
| `/presentations/<pid>/chat` | unchanged | API |
| `/presentations/<pid>/stream/<token>` | unchanged | SSE |
| `/presentations/<pid>/snapshot` (POST) | **request body extended**: now accepts `title`, `description`, `bound_experts` | Phase 10D |
| `/presentations/<pid>/basket` | unchanged | API |
| `/presentations/<pid>/patch` | unchanged | API |
| `/presentations/<pid>/sources` | unchanged | API |
| `/presentations/<pid>/duckdb/*` | unchanged | APIs |
| `/presentations/snapshots` | unchanged | API |
| `/presentations/help.json` | unchanged | API |

### New routes (this phase)

| Method | URL | Owner blueprint | Phase | Description |
|---|---|---|---|---|
| GET | `/` | `prisma_home` | 10A | Consumer landing |
| GET | `/uzmanlar/` | `prisma_home` | 10C | Expert list (read-only) |
| GET | `/uzmanlar/<code>` | `prisma_home` | 10C | Expert detail page |
| GET | `/uzmanlar/<code>/briefing` | `prisma_home` | 10E | Briefing JSON (engine output) |
| GET | `/atolye/` | `prisma_home` | 10A | Atölye home (pipeline) |
| GET | `/atolye/sunum/` | `prisma_home` | 10A | Alias → `/presentations/` |
| GET | `/atolye/kesif/` | `prisma_home` | 10A | Alias → existing Keşif route if exists, else stub |
| GET | `/atolye/hazirlik/` | `prisma_home` | 10A | Alias → existing Hazırlık route if exists, else stub |
| GET | `/atolye/kutuphane/blok/` | `prisma_home` | 10A | Stub for Phase 11 |
| GET | `/atolye/kutuphane/tablo/` | `prisma_home` | 10A | Stub for Phase 11 |
| GET | `/atolye/kutuphane/sablon/` | `prisma_home` | 10A | Stub for Phase 11 |
| GET | `/atolye/surec/` | `prisma_home` | 10A | Stub for Phase 11 |
| GET | `/api/experts/` | `prisma_home` | 10B | Expert list JSON |
| GET | `/api/experts/<code>` | `prisma_home` | 10B | Expert detail JSON |
| POST | `/api/experts/suggest` | `prisma_home` | 10D | LLM expert suggestion for a snapshot |

### Backward compatibility contract

1. Every existing URL must return HTTP 200 after Phase 10A is deployed.
2. The pre-existing app's non-presentations routes (rates, deposit_panel,
   competitor, etc.) are **out of scope** — they keep their current chrome,
   nav and base template. PRISMA shell adoption for those modules is a
   future phase.
3. The pre-existing app's home page (whatever served `/`) is **replaced** by
   the new `prisma_home.landing`. If the old home had its own route name,
   it must be redirected or removed in 10A.

---

## 4. Architecture

### 4.1 Blueprint structure

```
flask_app/
  prisma_home/              ← NEW blueprint
    __init__.py
    routes.py               ← landing, expert pages, atölye home
    experts.py              ← ExpertStore + Expert dataclass
    briefing.py             ← Briefing engine (Phase 10E)
    templates/
      home/
        _base_prisma.html   ← NEW base template (top bar + sidebar)
        landing.html
        expert.html
        atolye_home.html
        _stub.html          ← reusable "yakında" placeholder
      partials/
        topbar.html
        atolye_sidebar.html
        save_modal.html     ← used by editor template
    static/
      css/
        prisma.css          ← shell + landing + expert + atölye styles
        prisma_dark.css     ← (lifted from UX prototype)
      js/
        prisma_shell.js     ← mode detection, sidebar state
        save_modal.js       ← Phase 10D

  presentations/            ← MOSTLY UNTOUCHED
    routes.py               ← only snapshot save body changes (10D)
    templates/
      presentations/
        editor.html         ← extends _base_prisma.html (was base.html)
        list.html           ← extends _base_prisma.html
        snapshot.html       ← extends _base_prisma.html (no sidebar)
    static/                 ← unchanged
```

### 4.2 Base template strategy

**Decision:** A single new base template `_base_prisma.html` becomes the shell
for all PRISMA pages (consumer + atölye). Existing presentations templates
switch their `{% extends %}` line.

The base template:
- Renders the top bar always
- Conditionally renders the Atölye sidebar via `{% if mode == 'atolye' %}`
- Reads `mode` from template context (default: `'consumer'`)
- Reads `crumb` from template context (default: empty)
- Reads `user` from `flask_login.current_user`

Pages set `mode` either via route context or by overriding the `mode` block:

```jinja
{# editor.html #}
{% extends "home/_base_prisma.html" %}
{% set mode = "atolye" %}
{% set crumb = "Atölye · Pipeline · " ~ markup_safe("<span class='here'>03 Sunum</span>") %}
{% block content %}
  {# existing React mount + scripts #}
{% endblock %}
```

The pre-existing base template (`flask_app/base.html` or similar in broader
app) is **not removed**. Modules outside PRISMA (rates, deposit_panel) keep
extending it. Only PRISMA-related templates switch.

### 4.3 Mode detection rules

| Route | mode |
|---|---|
| `/` | consumer |
| `/uzmanlar/*` | consumer |
| `/atolye/*` | atolye |
| `/presentations/` | atolye (under "Sunum" pipeline stage) |
| `/presentations/<pid>` | atolye |
| `/presentations/snapshot/<sid>` | consumer (snapshots are reading material) |

Snapshot view in consumer mode means **no sidebar visible** — reader gets a
clean top bar only. Save modal not available (snapshots are read-only).

### 4.4 Sidebar item registry

The Atölye sidebar reads its items from a Python list of dicts (not hardcoded
in template) so badge counts can be injected dynamically:

```python
# prisma_home/sidebar.py
SIDEBAR_GROUPS = [
    {
        "label": "Pipeline",
        "items": [
            {"key": "atolye",   "num": "A·0", "label": "Atölye Ana", "route": "prisma_home.atolye_home"},
            {"key": "kesif",    "num": "A·1", "label": "Keşif",     "route": "prisma_home.atolye_kesif"},
            {"key": "hazirlik", "num": "A·2", "label": "Hazırlık",  "route": "prisma_home.atolye_hazirlik"},
            {"key": "sunum",    "num": "A·3", "label": "Sunum",     "route": "prisma_home.atolye_sunum"},
        ],
    },
    {
        "label": "Kütüphane",
        "items": [
            {"key": "tablolar",  "num": "⊟", "label": "Tablolar",  "route": "prisma_home.atolye_lib_tables"},
            {"key": "bloklar",   "num": "▦", "label": "Bloklar",   "route": "prisma_home.atolye_lib_blocks"},
            {"key": "sablonlar", "num": "◇", "label": "Şablonlar", "route": "prisma_home.atolye_lib_templates"},
        ],
    },
    {
        "label": "Meta",
        "items": [
            {"key": "surec", "num": "∿", "label": "Süreç İzleme", "route": "prisma_home.atolye_observatory"},
        ],
    },
]


def get_sidebar(active_key: str, counts: dict | None = None) -> list:
    """Return sidebar groups with active flag + injected badge counts."""
    counts = counts or {}
    result = []
    for group in SIDEBAR_GROUPS:
        items = [
            {**item, "active": item["key"] == active_key, "badge": counts.get(item["key"])}
            for item in group["items"]
        ]
        result.append({"label": group["label"], "items": items})
    return result
```

Each PRISMA route, before rendering, calls `get_sidebar(active_key=...)` and
passes it into the template context.

---

## 5. Data model

### 5.1 Expert YAML schema

Stored as one YAML file per expert. Location: `examples/phase_10/experts/`
for fixtures; `s3://.../experts/<id>.yaml` for production.

```yaml
# expert id — used in URLs and as bound_experts reference
id: liq
version: 1                                # immutable per version
code: LIQ                                 # 2-3 char glyph
name: Likidite Uzmanı
domain_label: Likidite
short_description: "Likidite, repo, swap, LCR/NSFR."
status: active                            # active | archived

persona:
  system_prompt: |
    Sen QNB Hazine'nin likidite uzmanısın. Veriye dayalı,
    açık ve teknik konuşursun. Aşırı dramatize etmezsin.
    Her cevabın sonunda ilgili kaynak block/snapshot id'lerini referans verirsin.
  voice_examples:
    - "Gece boyunca LCR oranı %118'e geriledi — dün %125'ti."
    - "Repo kanalı açık ama kısa vadeli fiyatlama 50 baz puan yukarı kaydı."

bound_content:
  blocks: []                              # list of block IDs (Phase 11 will populate)
  snapshots: []                           # list of snapshot IDs
  processes: []                           # reserved for Phase 13

briefing_recipe:
  cache_ttl_seconds: 1800                 # 30 min, briefing engine respects
  sections:
    - id: pulse
      title: "Bu Sabah"
      fill_from:
        kind: snapshot                    # snapshot | block | metric
        role: daily_pulse                 # selector (semantic_tag or named role)
        limit: 1
      llm_paraphrase: true
    - id: key_metrics
      title: "Anahtar Göstergeler"
      fill_from:
        kind: block
        semantic_tag: kpi_liquidity
        limit: 5
      llm_paraphrase: false
    - id: citations
      title: "Kaynakça"
      fill_from:
        kind: snapshot
        limit: 6
      llm_paraphrase: false

access_scope:
  read: ["*"]                             # all authenticated users
  edit: ["FINANSAL YAPAY ZEKA UYGULAMALARI"]  # department names from app.py SIDEBAR_RULES

ui:
  accent_color: "#6B8AFD"                 # CSS color
  glyph: "LIQ"                            # display string (usually == code)
```

Validation rules (Pydantic or manual):
- `id` is unique across the store
- `version` is `int`, `1` for the first version; increasing means a new file (immutable per version is forward-compat with Phase 6.5 block contract)
- `code` is 2-3 uppercase chars
- `briefing_recipe.sections[].id` is unique within an expert
- `access_scope.edit` department names must exist in `SIDEBAR_RULES` keys
- `ui.accent_color` is a valid hex color

### 5.2 ExpertStore

```python
# prisma_home/experts.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol
import yaml


@dataclass
class Expert:
    id: str
    version: int
    code: str
    name: str
    domain_label: str
    short_description: str
    persona: dict
    bound_content: dict
    briefing_recipe: dict
    access_scope: dict
    ui: dict
    status: str = "active"

    @classmethod
    def from_dict(cls, d: dict) -> "Expert":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class ExpertStore(Protocol):
    def list_all(self) -> list[Expert]: ...
    def load(self, expert_id: str) -> Optional[Expert]: ...
    def list_for_user(self, user) -> list[Expert]: ...


class LocalExpertStore:
    """Filesystem-backed expert store. Reads YAML files from a directory."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self._cache: dict[str, Expert] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        for yaml_path in sorted(self.base_dir.glob("*.yaml")):
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            expert = Expert.from_dict(data)
            self._cache[expert.id] = expert
        self._loaded = True

    def list_all(self) -> list[Expert]:
        self._ensure_loaded()
        return list(self._cache.values())

    def load(self, expert_id: str) -> Optional[Expert]:
        self._ensure_loaded()
        return self._cache.get(expert_id)

    def list_for_user(self, user) -> list[Expert]:
        self._ensure_loaded()
        dept = getattr(user, "department", None)
        return [
            e for e in self._cache.values()
            if "*" in e.access_scope.get("read", [])
            or dept in e.access_scope.get("read", [])
        ]
```

App config wires it in `__init__.py`:

```python
app.config["EXPERT_STORE"] = LocalExpertStore(
    base_dir=Path(app.root_path).parent / "examples" / "phase_10" / "experts"
)
```

### 5.3 Manifest extension

```python
# presentations/manifest.py — additive change

class Manifest(TypedDict):
    id: str
    version: int
    owner_id: str
    meta: dict
    basket: list
    blocks: list
    bound_experts: list[str]      # NEW — empty list allowed, default []
```

Migration: `presentations/migration.py::ensure_nested(manifest)` is extended
to set `bound_experts: []` if missing. No DB migration needed (manifests are
JSON files in session dirs).

Validation in `validate_manifest()`:
- `bound_experts` must be a list of strings
- Each string must be an existing expert ID (look up via current_app's
  `EXPERT_STORE`)
- Snapshot copies preserve `bound_experts`

### 5.4 Snapshot meta extension

```python
# presentations/store.py — meta dict gets extra fields
meta = {
    "snapshot_id": sid,
    "created_at": ...,
    "owner_id": owner_id,
    "presentation_id": manifest.get("id"),
    "manifest_version": manifest.get("version"),
    "title": ...,                          # NEW: user-overridable, falls back to manifest meta.title
    "description": ...,                    # NEW: from save form
    "bound_experts": [...],                # NEW: from save form
}
```

The frozen manifest also carries `bound_experts` so reading a snapshot still
shows the connection.

### 5.5 LLM suggestion endpoint

```
POST /api/experts/suggest
Body:
  {
    "manifest": { ... full manifest ... },
    "title": "...",
    "description": "..."
  }
Response:
  {
    "suggestions": [
      { "id": "liq", "code": "LIQ", "confidence": 0.92, "reason": "..." },
      { "id": "dep", "code": "DEP", "confidence": 0.68, "reason": "..." }
    ]
  }
```

Implementation: build a compact prompt from manifest (block titles, semantic
tags, data sources) + the full expert list (id, name, short_description),
ask Qwen to return JSON with top 1-3 candidates and reasons. Use the same
`QwenClient` infrastructure that already handles JSON-in-content parsing.

System prompt sketch (Turkish, in `prisma_home/prompts/suggest_experts.txt`):

```
Sen bir hazine sunum bağlantı önericisin. Sana bir sunumun başlığı,
açıklaması ve içindeki blok başlıklarını veriyorum. Bu sunumun hangi
uzman(lar)ın kaynakçası altında görünmesi gerektiğini öneriyorsun.

Çıktın sadece şu JSON: {
  "suggestions": [
    {"id": "<expert_id>", "confidence": 0-1, "reason": "kısa Türkçe gerekçe"}
  ]
}

Mevcut uzmanlar: ...
```

Confidence > 0.7 → checked-by-default in the modal. Top suggestion gets a
star (★).

### 5.6 Briefing engine (Phase 10E)

Cache key: `sha256(expert_id || expert_version || sorted_bound_snapshot_ids || date_ymd)`

Storage: per-process dict initially (will move to Redis in Phase 12).

Algorithm:

```python
def render_briefing(expert: Expert, snapshots: list[dict]) -> dict:
    sections = []
    for sec in expert.briefing_recipe["sections"]:
        items = _resolve_fill_from(sec["fill_from"], snapshots, store)
        if sec.get("llm_paraphrase"):
            content = _llm_paraphrase(expert.persona, items, sec)
        else:
            content = _render_raw(items, sec)
        sections.append({
            "id": sec["id"],
            "title": sec["title"],
            "content_html": content,
            "citations": [{"ref": x["id"], "title": x["title"], ...} for x in items],
        })
    return {"sections": sections, "rendered_at": datetime.utcnow().isoformat()}
```

Initially (Phase 10C), the expert detail page renders a **static briefing**
(checked-in markdown per expert in the YAML or a sibling file). Phase 10E
swaps that for the engine output.

---

## 6. Sub-phases

Each sub-phase is an independent PR. Acceptance criteria are explicit; tests
must pass before merge.

### Phase 10A — Shell + base template + sidebar

**Goal:** Every existing PRISMA page renders inside the new shell. No new
features, only chrome. Sidebar visible only in atolye mode.

**Files to create:**

- `flask_app/prisma_home/__init__.py`
- `flask_app/prisma_home/routes.py`
- `flask_app/prisma_home/sidebar.py`
- `flask_app/prisma_home/templates/home/_base_prisma.html`
- `flask_app/prisma_home/templates/home/landing.html` *(placeholder)*
- `flask_app/prisma_home/templates/home/atolye_home.html` *(placeholder)*
- `flask_app/prisma_home/templates/home/_stub.html`
- `flask_app/prisma_home/templates/partials/topbar.html`
- `flask_app/prisma_home/templates/partials/atolye_sidebar.html`
- `flask_app/prisma_home/static/css/prisma.css`
  *(extract from `docs/ux/PRISMA_UX_Prototip_v2.html`)*
- `flask_app/prisma_home/static/js/prisma_shell.js`
- `tests/test_prisma_shell.py`

**Files to modify:**

- `flask_app/__init__.py` — register `prisma_home_bp`
- `flask_app/presentations/templates/presentations/editor.html`
  — change `{% extends %}` to `_base_prisma.html`, set `mode = "atolye"`
- `flask_app/presentations/templates/presentations/list.html` — same
- `flask_app/presentations/templates/presentations/snapshot.html`
  — set `mode = "consumer"` (no sidebar)

**Routes added (10A):**

```python
@prisma_home_bp.route("/")
@login_required
def landing():
    return render_template("home/landing.html",
                           mode="consumer", crumb="",
                           sidebar=get_sidebar(active_key=None))

@prisma_home_bp.route("/atolye/")
@login_required
def atolye_home():
    return render_template("home/atolye_home.html",
                           mode="atolye", crumb="Atölye · Ana",
                           sidebar=get_sidebar(active_key="atolye"))

@prisma_home_bp.route("/atolye/sunum/")
@login_required
def atolye_sunum():
    return redirect(url_for("presentations.list_presentations"))

# … same redirect pattern for /atolye/kesif/, /atolye/hazirlik/
# … and stub pages for /atolye/kutuphane/* and /atolye/surec/

@prisma_home_bp.route("/atolye/kutuphane/blok/")
@login_required
def atolye_lib_blocks():
    return render_template("home/_stub.html",
                           mode="atolye", crumb="Atölye · Kütüphane · Bloklar",
                           sidebar=get_sidebar(active_key="bloklar"),
                           page_title="Blok Kütüphanesi",
                           message="Phase 11'de geliyor.")
```

**Acceptance criteria:**

1. `curl http://localhost:8080/` returns HTML containing the brand mark
   `PRISMA` and the user's name from `current_user`.
2. `curl http://localhost:8080/presentations/` returns the existing
   presentations list, but with the new top bar and sidebar in markup.
3. `curl http://localhost:8080/presentations/<pid>` editor still works
   end-to-end (chat, basket, etc. — no regressions).
4. `curl http://localhost:8080/atolye/kutuphane/blok/` returns 200 with
   the "yakında" stub.
5. The mode toggle in top bar visually switches between Tüketici and
   Atölye states.
6. Visiting `/presentations/snapshot/<sid>` shows top bar but **no
   sidebar** (snapshots are consumer-mode read-only).
7. `pytest tests/test_prisma_shell.py` passes.

**Tests (`tests/test_prisma_shell.py`):**

- `test_landing_returns_200_with_brand_mark`
- `test_atolye_home_includes_sidebar`
- `test_landing_excludes_sidebar`
- `test_presentations_list_renders_under_prisma_shell`
- `test_editor_renders_under_prisma_shell`
- `test_snapshot_view_has_no_sidebar`
- `test_sidebar_active_key_marks_correct_item`
- `test_stub_pages_return_200`

### Phase 10B — Expert backend + manifest extension

**Goal:** `ExpertStore` is wired, `bound_experts` field exists on manifest
and snapshot meta, validation works. No UI changes.

**Files to create:**

- `flask_app/prisma_home/experts.py` — Expert dataclass, LocalExpertStore
- `examples/phase_10/experts/liq.yaml`
- `examples/phase_10/experts/dep.yaml`
- `examples/phase_10/experts/fnd.yaml`
- `examples/phase_10/experts/nii.yaml`
- `examples/phase_10/experts/sec.yaml`
- `examples/phase_10/experts/krd.yaml`
- `examples/phase_10/manifest_with_bound_experts.json` — fixture
- `tests/test_experts.py`
- `tests/test_manifest_bound_experts.py`

**Files to modify:**

- `flask_app/__init__.py` — `app.config["EXPERT_STORE"] = LocalExpertStore(...)`
- `flask_app/presentations/manifest.py`:
  - Add `bound_experts` to TypedDict
  - Add validator: each ID must exist in EXPERT_STORE
- `flask_app/presentations/migration.py::ensure_nested()`:
  - Default `bound_experts: []` if missing
- `flask_app/prisma_home/routes.py`:
  - `GET /api/experts/` → list as JSON
  - `GET /api/experts/<code>` → detail as JSON

**Acceptance criteria:**

1. `curl http://localhost:8080/api/experts/` returns the 6 experts as JSON.
2. `curl http://localhost:8080/api/experts/liq` returns the LIQ expert dict.
3. Loading an old manifest without `bound_experts` succeeds, returns
   `bound_experts: []` after migration.
4. Manifest validation rejects `bound_experts: ["nonexistent"]`.
5. `pytest tests/test_experts.py tests/test_manifest_bound_experts.py` passes.

### Phase 10C — Consumer landing + expert detail page

**Goal:** New `/` and `/uzmanlar/<code>` routes render real content. Briefings
are static placeholder (markdown checked in or hardcoded per expert).
Bound snapshots show as citations.

**Files to create:**

- `flask_app/prisma_home/templates/home/landing.html` *(full)*
- `flask_app/prisma_home/templates/home/expert.html`
- `flask_app/prisma_home/templates/home/partials/expert_card.html`
- `flask_app/prisma_home/templates/home/partials/citation_grid.html`
- `examples/phase_10/briefings/liq_static.md` *(and 5 others)*

**Files to modify:**

- `flask_app/prisma_home/routes.py`:
  - `GET /uzmanlar/` → expert list (same as landing, alt URL)
  - `GET /uzmanlar/<code>` → expert detail
- `flask_app/prisma_home/templates/home/atolye_home.html` *(full version with pipeline)*

**Routes (10C):**

```python
@prisma_home_bp.route("/uzmanlar/<code>")
@login_required
def expert_detail(code):
    store = current_app.config["EXPERT_STORE"]
    expert = store.load(code.lower())
    if not expert:
        abort(404)
    # Load snapshots bound to this expert
    snapshot_store = current_app.config["SNAPSHOT_STORE"]
    bound_snapshots = _find_snapshots_bound_to(snapshot_store, expert.id)
    # Static briefing for now (10C); Phase 10E replaces with engine
    briefing_md = _load_static_briefing(expert.id)
    return render_template("home/expert.html",
                           mode="consumer",
                           crumb=f"Uzmanlar · {expert.domain_label}",
                           sidebar=get_sidebar(active_key=None),
                           expert=expert,
                           briefing_md=briefing_md,
                           bound_snapshots=bound_snapshots)
```

`_find_snapshots_bound_to(store, expert_id)` iterates store and filters
where `meta["bound_experts"]` contains `expert_id`.

**Acceptance criteria:**

1. Landing page shows 6 expert cards. Featured expert is the first one
   in user's department mapping (see decision matrix below) or LIQ as fallback.
2. Clicking an expert card navigates to `/uzmanlar/<code>` and renders
   detail page with static briefing.
3. Bound snapshots appear as citation cards on the expert detail page.
4. Snapshots not bound to this expert do not appear.
5. Visual fidelity matches `docs/ux/PRISMA_UX_Prototip_v2.html` within
   reasonable tolerance.

**Department → featured expert mapping (initial):**

```python
DEPT_TO_FEATURED_EXPERT = {
    "BİLANÇO YÖNETİMİ": "liq",
    "BİLANÇO ANALİZİ VE MEVDUAT YÖNETİMİ": "dep",
    "AKTİF PASİF YÖNETİMİ İŞTİRAKLER KOORDİNASYON": "nii",
    "AKTİF PASİF YÖNETİMİ VE FON TRANSFER FİYATLAMASI": "nii",
    "HAZİNE SATIŞ": "fnd",
    "FİNANSAL YAPAY ZEKA UYGULAMALARI": "liq",  # data team default
    "MYU": "krd",
    "IBTECH-INF OPEN SOLUTIONS": "liq",
}
```

Fallback if department not mapped: `liq`.

### Phase 10D — Save modal + bound_experts UI + LLM suggestions

**Goal:** Header's "Snapshot Al" button opens a pre-save form modal. User
fills title/description, selects bound_experts (LLM-suggested), submits,
existing ShareModal opens with the URL.

**Files to create:**

- `flask_app/prisma_home/templates/partials/save_modal.html`
- `flask_app/prisma_home/static/js/save_modal.js`
- `flask_app/prisma_home/prompts/suggest_experts.txt`
- `tests/test_suggest_experts.py`
- `tests/test_snapshot_save_with_experts.py`

**Files to modify:**

- `flask_app/presentations/static/js/components/Header.jsx`
  — clicking "Snapshot Al" opens save modal instead of POST directly
- `flask_app/presentations/static/js/components/SaveModal.jsx` *(new)*
- `flask_app/presentations/routes.py::create_snapshot`
  — accept JSON body `{title, description, bound_experts}`, validate, persist
- `flask_app/presentations/store.py::LocalSnapshotStore.save`
  — accept and persist new meta fields
- `flask_app/prisma_home/routes.py`
  — add `POST /api/experts/suggest`

**Endpoints (10D):**

```python
@prisma_home_bp.route("/api/experts/suggest", methods=["POST"])
@login_required
def suggest_experts():
    body = request.get_json(silent=True) or {}
    manifest = body.get("manifest", {})
    title = body.get("title", "")
    description = body.get("description", "")

    store = current_app.config["EXPERT_STORE"]
    experts = store.list_for_user(current_user)

    llm = current_app.config["LLM_CLIENT"]
    suggestions = _llm_suggest_experts(llm, manifest, title, description, experts)
    return jsonify({"suggestions": suggestions})
```

**Acceptance criteria:**

1. Clicking "Snapshot Al" opens the save modal with title pre-filled from
   manifest meta.
2. LLM suggestion endpoint returns within 5s and pre-checks ≥1 expert.
3. User can toggle experts on/off, must select at least 1 to publish (or
   "Taslak Kaydet" path skips this).
4. Submit creates snapshot with persisted `bound_experts` on meta.
5. After save, existing ShareModal opens with URL (no regression).
6. Visiting that snapshot's URL shows it under the bound experts' pages.

### Phase 10E — Briefing engine

**Goal:** Replace static markdown briefings with engine output.

**Files to create:**

- `flask_app/prisma_home/briefing.py` — engine
- `flask_app/prisma_home/prompts/briefing_section.txt` — LLM prompt
- `tests/test_briefing_engine.py`

**Files to modify:**

- `flask_app/prisma_home/routes.py`:
  - Add `GET /uzmanlar/<code>/briefing` (JSON)
  - Update `expert_detail` to use engine output instead of static MD

**Acceptance criteria:**

1. `GET /uzmanlar/liq/briefing` returns JSON with section list, each
   containing `content_html` and `citations`.
2. Second request within 30 min (cache_ttl) returns from cache (verify
   via timing or log).
3. Modifying a bound snapshot invalidates cache for that expert.
4. LLM section content quotes citation IDs.
5. Page render time < 2s on warm cache.

---

## 7. Data flow diagrams

### Save flow (Phase 10D)

```
User clicks "Snapshot Al"
        ↓
Header.jsx opens SaveModal (new)
        ↓
SaveModal mounts, calls POST /api/experts/suggest with manifest
        ↓
LLM returns top 1-3 expert suggestions
        ↓
User adjusts selections, clicks "Yayınla & Paylaş"
        ↓
SaveModal POSTs to /presentations/<pid>/snapshot
        body: { title, description, bound_experts }
        ↓
LocalSnapshotStore.save() persists manifest + meta (with new fields)
        ↓
SaveModal closes, ShareModal opens with URL (existing flow)
        ↓
The new snapshot now appears under bound experts' citation grids
```

### Briefing engine flow (Phase 10E)

```
User visits /uzmanlar/liq
        ↓
expert_detail() loads Expert + bound_snapshots
        ↓
Briefing engine checks cache for (expert_id, snapshot_sig, ymd)
        ↓
Miss → iterate recipe.sections:
         · resolve fill_from (snapshot/block/metric)
         · if llm_paraphrase: send to LLM with persona + items
         · else: render raw with template
        ↓
Cache result, return sections + citations
        ↓
Template renders briefing prose with sup references → citation grid below
```

---

## 8. Conventions (reminder)

- Spec text: English. User-facing strings: Turkish.
- Sub-phase = one independent PR.
- Blocks/snapshots/experts immutable per version (Phase 6.5 forward-compat).
- SQL whitelist parser-based (no regex).
- Backward compat: existing routes keep working.
- Tests required for each sub-phase; CI must pass.

---

## 9. Open questions

1. **Pre-existing app home page replacement.** The broader treasury app
   currently serves something at `/`. Does Phase 10A replace it directly,
   or do we add a feature flag and let the old home coexist for a transition?
   *Default proposal:* replace directly; pre-existing app shows old home at
   a different URL temporarily if needed.

2. **Expert YAML location in production.** Fixtures for Phase 10. For
   production, do we (a) keep them in repo and ship via deploy, or
   (b) store in S3 and admin-edit them through a UI?
   *Default proposal:* (a) for now; (b) is a Phase 14 emergence concern.

3. **Briefing cache backend.** In-process dict for Phase 10E vs Redis
   from day one.
   *Default proposal:* in-process dict; document migration path to Redis.

4. **Snapshot auth on consumer pages.** Currently snapshot view requires
   login (corporate intranet). Do we maintain that or open to read-only
   anonymous?
   *Default proposal:* maintain login requirement.

5. **Expert visibility for first launch.** All 6 experts shown to everyone
   on landing, or filter by `access_scope.read`?
   *Default proposal:* show all 6 to all users (read access wide-open initially).

---

## 10. Done state

After all five sub-phases ship:

- Every user logs in and lands on a consumer home with 6 expert cards.
- Clicking an expert shows a daily briefing with real citations.
- Users producing in Atölye see a sidebar with full producer IA.
- The Sunum save modal is the funnel through which content enters the
  consumer experience.
- All pre-existing routes continue to work.
- The 6 PRISMA experts (LIQ, DEP, FND, NII, SEC, KRD) are seeded.
- Snapshots produced by Atölye users with `bound_experts` set start
  appearing under expert citation grids.
- The data team can edit expert YAMLs to refine personas and recipes.

Phase 11 then adds the library/observatory screens, semantic search, and
Block Marketplace.
