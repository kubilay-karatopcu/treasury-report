# Kickoff — Phase 10A · Shell + Base Template + Sidebar

**Session goal:** Introduce the new PRISMA shell (top bar + Atölye sidebar +
mode toggle) and wrap every existing PRISMA page in it. No new features,
only chrome. After this session, all existing routes still work; they
just render under the new shell.

---

## Before you start

Read these in order:

1. `docs/PHASE_10_SPEC.md` — full Phase 10 spec, sections 1–4 mandatory,
   sections 5–6 useful context
2. `docs/ux/PRISMA_UX_Prototip_v2.html` — the design source of truth.
   Open in browser, click the devkit links to walk through all pages.
   The shell (top bar + sidebar) is what you're implementing in this session.
3. `flask_app/presentations/routes.py` — existing routes; **do not break any**
4. `flask_app/presentations/templates/presentations/editor.html` — example of
   what an existing template looks like; you'll change its `{% extends %}` line

---

## What you're building

A new Flask blueprint `prisma_home` that owns:
- The new shell base template (`_base_prisma.html`)
- Three new routes: `/`, `/atolye/`, plus stub routes under `/atolye/*`
- Two static assets: `prisma.css` (extracted from the prototype) and
  `prisma_shell.js` (mode toggle, sidebar state)

Then you'll switch three existing templates (editor, list, snapshot) to
extend the new base.

This is **chrome only**. The landing and atölye home pages render as
placeholders (full implementations come in 10C). The library screens
under `/atolye/kutuphane/*` return a "yakında" stub.

---

## Files to create

```
flask_app/prisma_home/__init__.py
flask_app/prisma_home/routes.py
flask_app/prisma_home/sidebar.py
flask_app/prisma_home/templates/home/_base_prisma.html
flask_app/prisma_home/templates/home/landing.html       (placeholder)
flask_app/prisma_home/templates/home/atolye_home.html   (placeholder)
flask_app/prisma_home/templates/home/_stub.html
flask_app/prisma_home/templates/partials/topbar.html
flask_app/prisma_home/templates/partials/atolye_sidebar.html
flask_app/prisma_home/static/css/prisma.css
flask_app/prisma_home/static/js/prisma_shell.js
tests/test_prisma_shell.py
```

## Files to modify

```
flask_app/__init__.py
  - register prisma_home_bp at url_prefix=""
  - keep all existing blueprint registrations

flask_app/presentations/templates/presentations/editor.html
  - change first line to: {% extends "home/_base_prisma.html" %}
  - add: {% set mode = "atolye" %}
  - add: {% set crumb = "Atölye · Pipeline · 03 Sunum" %}
  - add: {% set sidebar_active = "sunum" %}
  - {% block content %} body unchanged

flask_app/presentations/templates/presentations/list.html
  - same pattern as editor.html, sidebar_active = "sunum"

flask_app/presentations/templates/presentations/snapshot.html
  - {% extends "home/_base_prisma.html" %}
  - {% set mode = "consumer" %}  (snapshots → no sidebar)
  - {% set crumb = "Snapshot · " ~ manifest.meta.title %}
```

---

## Implementation notes

### 1. Blueprint factory (`prisma_home/__init__.py`)

```python
from flask import Blueprint

prisma_home_bp = Blueprint(
    "prisma_home",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/prisma_home/static",
)

# Defer route import to avoid circular deps
from . import routes  # noqa: E402, F401
```

### 2. Routes (`prisma_home/routes.py`)

Minimal set for 10A. Use `flask_login.login_required` consistently.

```python
from flask import render_template, redirect, url_for
from flask_login import login_required, current_user
from . import prisma_home_bp
from .sidebar import get_sidebar


@prisma_home_bp.route("/")
@login_required
def landing():
    return render_template(
        "home/landing.html",
        mode="consumer",
        crumb="",
        sidebar=get_sidebar(active_key=None),
    )


@prisma_home_bp.route("/atolye/")
@login_required
def atolye_home():
    return render_template(
        "home/atolye_home.html",
        mode="atolye",
        crumb="Atölye · Ana",
        sidebar=get_sidebar(active_key="atolye"),
    )


# Pipeline aliases — Sunum is the existing presentations list

@prisma_home_bp.route("/atolye/sunum/")
@login_required
def atolye_sunum():
    return redirect(url_for("presentations.list_presentations"))


# Keşif / Hazırlık — alias to existing modules if those routes exist;
# otherwise render the stub. Check the broader app's blueprint names
# before deciding. If unsure, use the stub for now.

@prisma_home_bp.route("/atolye/kesif/")
@login_required
def atolye_kesif():
    return render_template(
        "home/_stub.html",
        mode="atolye",
        crumb="Atölye · Pipeline · 01 Keşif",
        sidebar=get_sidebar(active_key="kesif"),
        page_title="Keşif",
        message="Phase 9 modülü bu route'a bağlanacak.",
    )


@prisma_home_bp.route("/atolye/hazirlik/")
@login_required
def atolye_hazirlik():
    return render_template(
        "home/_stub.html",
        mode="atolye",
        crumb="Atölye · Pipeline · 02 Hazırlık",
        sidebar=get_sidebar(active_key="hazirlik"),
        page_title="Hazırlık",
        message="Phase 8 modülü bu route'a bağlanacak.",
    )


# Library stubs

@prisma_home_bp.route("/atolye/kutuphane/blok/")
@login_required
def atolye_lib_blocks():
    return render_template(
        "home/_stub.html",
        mode="atolye",
        crumb="Atölye · Kütüphane · Bloklar",
        sidebar=get_sidebar(active_key="bloklar"),
        page_title="Blok Kütüphanesi",
        message="Phase 11'de geliyor.",
    )


@prisma_home_bp.route("/atolye/kutuphane/tablo/")
@login_required
def atolye_lib_tables():
    return render_template(
        "home/_stub.html",
        mode="atolye",
        crumb="Atölye · Kütüphane · Tablolar",
        sidebar=get_sidebar(active_key="tablolar"),
        page_title="Tablo Kütüphanesi",
        message="Phase 11'de geliyor.",
    )


@prisma_home_bp.route("/atolye/kutuphane/sablon/")
@login_required
def atolye_lib_templates():
    return render_template(
        "home/_stub.html",
        mode="atolye",
        crumb="Atölye · Kütüphane · Şablonlar",
        sidebar=get_sidebar(active_key="sablonlar"),
        page_title="Şablonlar",
        message="Phase 11'de geliyor.",
    )


@prisma_home_bp.route("/atolye/surec/")
@login_required
def atolye_observatory():
    return render_template(
        "home/_stub.html",
        mode="atolye",
        crumb="Atölye · Meta · Süreç İzleme",
        sidebar=get_sidebar(active_key="surec"),
        page_title="Süreç İzleme",
        message="Phase 11'de geliyor.",
    )
```

### 3. Sidebar registry (`prisma_home/sidebar.py`)

Copy the `SIDEBAR_GROUPS` and `get_sidebar()` definitions from
`docs/PHASE_10_SPEC.md` section 4.4 verbatim. Badge counts: pass `None`
for now; Phase 10B/11 fill them.

### 4. Base template (`templates/home/_base_prisma.html`)

```jinja
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <title>{% block title %}{{ page_title or "PRISMA" }} — Hazine{% endblock %}</title>
  <meta name="viewport" content="width=1280">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@200;300;400;500;600&family=JetBrains+Mono:wght@300;400;500&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{{ url_for('prisma_home.static', filename='css/prisma.css') }}">
  {% block head %}{% endblock %}
</head>
<body class="prisma {{ mode|default('consumer') }}">
  <div class="app {% if mode == 'atolye' %}in-atolye{% endif %}" id="appShell">
    {% include "partials/topbar.html" %}
    {% if mode == 'atolye' %}
      {% include "partials/atolye_sidebar.html" %}
    {% endif %}
    <main class="canvas">
      {% block content %}{% endblock %}
    </main>
  </div>
  <script src="{{ url_for('prisma_home.static', filename='js/prisma_shell.js') }}"></script>
  {% block scripts %}{% endblock %}
</body>
</html>
```

### 5. Top bar partial (`partials/topbar.html`)

Lift the `<header class="topbar">` markup from the UX prototype. Replace
hard-coded "Kubilay Karatopçu / Bilanço Yönetimi / A16438" with
`{{ current_user.name }} / {{ current_user.department }} /
{{ current_user.sicil }}`.

The mode toggle buttons are plain `<a>` tags pointing to `/` and `/atolye/`
respectively; no client-side state needed.

The brand mark stays as the literal `PR<span class="i">I</span>SM<span class="a">A</span>`.

### 6. Sidebar partial (`partials/atolye_sidebar.html`)

Iterate `sidebar` (passed in context):

```jinja
<aside class="atolye-sidebar">
  {% for group in sidebar %}
    <div class="sidebar-group">
      <div class="sidebar-label">{{ group.label }}</div>
      {% for item in group['items'] %}
        <a class="sidebar-item {% if item.active %}on{% endif %}"
           href="{{ url_for(item.route) }}">
          <span class="num">{{ item.num }}</span>
          <span class="label-text">{{ item.label }}</span>
          {% if item.badge is not none %}
            <span class="badge">{{ item.badge }}</span>
          {% endif %}
        </a>
      {% endfor %}
    </div>
  {% endfor %}
  <a class="sidebar-back" href="{{ url_for('prisma_home.landing') }}">← Ana Ekran</a>
</aside>
```

### 7. CSS extraction (`static/css/prisma.css`)

Copy the entire `<style>` block from `docs/ux/PRISMA_UX_Prototip_v2.html`
into `prisma.css`. **Important:** strip out the page-specific selectors
that aren't shell-related (e.g. landing/expert/library page bodies) if
the file gets too large. For 10A you actually need the full file —
later phases will use the same CSS.

If `prisma.css` ends up >100KB, split into:
- `prisma_shell.css` — top bar, sidebar, layout, colors, fonts
- `prisma_pages.css` — landing, expert detail, library pages

For now: one file is fine.

### 8. JavaScript (`static/js/prisma_shell.js`)

Minimal — the prototype uses JS only for view switching, but in production
each view is a real route. So this file is small:

```javascript
// Save modal open/close (used in Phase 10D); keep stub here.
window.openSaveModal = function() {
  document.getElementById('saveModal')?.classList.add('active');
};
window.closeSaveModal = function() {
  document.getElementById('saveModal')?.classList.remove('active');
};

// Toggle pill states based on current location
(function(){
  const path = window.location.pathname;
  const isAtolye = path === '/atolye/' || path.startsWith('/atolye/') || path.startsWith('/presentations/');
  const consumer = document.getElementById('modeConsumer');
  const producer = document.getElementById('modeProducer');
  if (consumer && producer) {
    consumer.classList.toggle('on', !isAtolye);
    producer.classList.toggle('on', isAtolye);
  }
})();
```

### 9. Placeholder pages

`landing.html` and `atolye_home.html` are placeholders for 10A. Use a
single content block with the page name and "Phase 10C / 10A geliyor"
message. Full versions in 10C.

### 10. Stub template (`_stub.html`)

```jinja
{% extends "home/_base_prisma.html" %}
{% block content %}
  <div style="padding: 96px 0; text-align: center;">
    <div style="font-size: 11px; letter-spacing: 0.32em; color: var(--gold); text-transform: uppercase; margin-bottom: 24px;">
      Yakında
    </div>
    <h2 style="font-size: 36px; font-weight: 200; letter-spacing: -0.015em; margin-bottom: 14px;">
      {{ page_title }}
    </h2>
    <p style="font-family: 'Inter', sans-serif; font-style: italic; font-size: 15px; color: var(--ink-mute);">
      {{ message }}
    </p>
  </div>
{% endblock %}
```

### 11. Tests

```python
# tests/test_prisma_shell.py
import pytest
from flask import url_for


@pytest.fixture
def auth_client(client, monkeypatch):
    """Login a mock user."""
    # Adapt to the project's existing test auth fixture; if none exists,
    # create one that sets current_user manually.
    ...
    return client


def test_landing_returns_200_with_brand_mark(auth_client):
    rv = auth_client.get("/")
    assert rv.status_code == 200
    assert b"PRISMA" in rv.data or b'class="brand-mark"' in rv.data


def test_atolye_home_includes_sidebar(auth_client):
    rv = auth_client.get("/atolye/")
    assert rv.status_code == 200
    assert b'atolye-sidebar' in rv.data
    assert b'Kütüphane' in rv.data


def test_landing_excludes_sidebar(auth_client):
    rv = auth_client.get("/")
    assert rv.status_code == 200
    assert b'atolye-sidebar' not in rv.data


def test_presentations_list_renders_under_prisma_shell(auth_client):
    rv = auth_client.get("/presentations/")
    assert rv.status_code == 200
    # Top bar + sidebar both present
    assert b'class="brand-mark"' in rv.data
    assert b'atolye-sidebar' in rv.data


def test_editor_still_loads(auth_client):
    # Existing demo presentation
    rv = auth_client.get("/presentations/p_demo")
    assert rv.status_code == 200


def test_snapshot_view_has_no_sidebar(auth_client, snapshot_id):
    rv = auth_client.get(f"/presentations/snapshot/{snapshot_id}")
    assert rv.status_code == 200
    assert b'class="brand-mark"' in rv.data
    assert b'atolye-sidebar' not in rv.data


def test_stub_pages_return_200(auth_client):
    for path in [
        "/atolye/kutuphane/blok/",
        "/atolye/kutuphane/tablo/",
        "/atolye/kutuphane/sablon/",
        "/atolye/surec/",
    ]:
        rv = auth_client.get(path)
        assert rv.status_code == 200, f"{path} returned {rv.status_code}"
        assert b'Yakında' in rv.data


def test_sidebar_active_key_marks_correct_item(auth_client):
    rv = auth_client.get("/atolye/kutuphane/blok/")
    assert b'class="sidebar-item on"' in rv.data or b'sidebar-item on' in rv.data
```

If the project doesn't have a test auth fixture yet, create a minimal one
in `tests/conftest.py`.

---

## Acceptance checklist

Run these checks. All must pass before opening the PR.

- [ ] `pytest tests/test_prisma_shell.py` — all green
- [ ] `pytest` — no other regression (existing tests still pass)
- [ ] `curl http://localhost:8080/` → 200, brand mark in HTML
- [ ] `curl http://localhost:8080/atolye/` → 200, sidebar in HTML
- [ ] `curl http://localhost:8080/presentations/` → 200, renders with new
       shell, list contents intact
- [ ] `curl http://localhost:8080/presentations/p_demo` → 200, editor JS
       bundle still loads, React mount root present
- [ ] Manually open `/presentations/p_demo` in browser → editor works
       end-to-end (chat, basket, snapshot creation all functional)
- [ ] Visit `/presentations/snapshot/<any-existing-sid>` in browser →
       page shows top bar, no sidebar
- [ ] Mode toggle in top bar visually reflects current page
- [ ] All `/atolye/kutuphane/*` and `/atolye/surec/` return "yakında" stub
- [ ] No CSS files outside `prisma_home/static/` were modified

---

## Out of scope this session

These are **explicitly not** part of 10A:

- Expert YAML loading (Phase 10B)
- `bound_experts` manifest field (Phase 10B)
- Real landing page content (Phase 10C)
- Real expert detail page (Phase 10C)
- Save modal (Phase 10D)
- Briefing engine (Phase 10E)
- Block / Table / Şablon library pages (Phase 11)
- Süreç İzleme (Phase 11)
- Migrating pre-existing app modules (rates, deposit_panel, competitor) to
  the PRISMA shell — those keep their current chrome for now

If you find yourself implementing any of these, stop and check the spec.

---

## When you're done

1. Commit message: `feat(phase-10a): introduce PRISMA shell + atölye sidebar`
2. PR description references: `docs/PHASE_10_SPEC.md` section 6 (Phase 10A
   sub-phase)
3. Include before/after screenshots: `/`, `/atolye/`, `/presentations/`,
   `/presentations/p_demo`, `/presentations/snapshot/<sid>`
4. Tag the PR `phase-10a` for tracking
