"""PRISMA shell routes — Phase 10A.

Landing page, atölye home, pipeline aliases (Sunum → existing
presentations list), library + observatory stubs. Real implementations
of expert pages (10C), save modal (10D) and briefing engine (10E) land
in later sub-phases.
"""
from __future__ import annotations

import json

from flask import render_template, redirect, url_for, Response, current_app, abort, request, jsonify
from flask_login import login_required, current_user

from . import prisma_home_bp
from .processes import resolve_processes
from .sidebar import get_sidebar
from .briefings import (
    featured_expert_for,
    load_static_briefing,
    find_snapshots_bound_to,
)
from .suggest import suggest_experts


# ── Consumer landing ─────────────────────────────────────────────────────────

def _landing_context():
    """Build the context dict for landing.html.

    Shared between `/` and `/uzmanlar/` so the latter renders the same
    six-card layout under a different URL (per spec §3 URL map).
    """
    store = current_app.config.get("EXPERT_STORE")
    snapshot_store = current_app.config.get("SNAPSHOT_STORE")

    experts = store.list_for_user(current_user) if store else []
    featured_id = featured_expert_for(current_user)
    featured = next((e for e in experts if e.id == featured_id), None) or (experts[0] if experts else None)
    others = [e for e in experts if e.id != (featured.id if featured else None)]

    # Featured expert's static briefing + bound snapshots (cross-owner) for
    # the hero's voice quote + citations strip. Snapshot store may be absent
    # in some test contexts — fall back to empty list cleanly.
    if featured is not None:
        briefing = load_static_briefing(featured.id)
        bound = find_snapshots_bound_to(snapshot_store, featured.id) if snapshot_store else []
    else:
        briefing = None
        bound = []

    return {
        "mode": "consumer",
        "crumb": "",
        "sidebar": get_sidebar(active_key=None),
        "featured_expert": featured,
        "featured_briefing": briefing,
        "featured_snapshots": bound,
        "other_experts": others,
    }


@prisma_home_bp.route("/")
@login_required
def landing():
    return render_template("home/landing.html", **_landing_context())


@prisma_home_bp.route("/uzmanlar/")
@login_required
def expert_list():
    # Alternate URL for the same 6-card layout; useful as a "go back to
    # expert browse" target from the detail page's breadcrumb.
    ctx = _landing_context()
    ctx["crumb"] = "Uzmanlar"
    return render_template("home/landing.html", **ctx)


@prisma_home_bp.route("/uzmanlar/<code>")
@login_required
def expert_detail(code: str):
    store = current_app.config.get("EXPERT_STORE")
    if store is None:
        abort(500)
    expert = store.load(code.lower())
    if expert is None:
        abort(404)

    # W8 — departman bakışı: erişim + süreç seti + topic gruplama tek yerden.
    from prisma_home.expert_views import legacy_view, resolve_view

    dept = getattr(current_user, "department", None) or ""
    r = resolve_view(expert, dept)
    if not r["granted"]:
        abort(403)          # SIKI: bu departmana açık bakış yok.
    view = r["view"] if not r["legacy"] else legacy_view(expert)

    # Phase 10E — engine drives the briefing. Falls back to the static
    # markdown internally when the LLM is unavailable, so 10C behaviour
    # is preserved when the model can't be reached.
    engine = current_app.config.get("BRIEFING_ENGINE")
    if engine is not None:
        briefing = engine.render_briefing(expert)
    else:
        briefing = load_static_briefing(expert.id)

    snapshot_store = current_app.config.get("SNAPSHOT_STORE")
    bound = find_snapshots_bound_to(snapshot_store, expert.id) if snapshot_store else []

    # Bakışın süreçleri (flat) + topic gruplaması (render için).
    processes = resolve_processes(view["process_ids"])
    card_by_id = {c["id"]: c for c in processes}
    topics = []
    for t in view["topics"]:
        cards = [card_by_id[pid] for pid in t["process_ids"] if pid in card_by_id]
        if cards:
            topics.append({"title": t["title"], "processes": cards})

    # W4a→W8 — uzman brifingi: (uzman, bakış) Aşama-C kaydı. İSTEK YOLU
    # BLOKLANMAZ: get_commentary sıcak cache'i/fallback'i anında döner.
    try:
        from prisma_home.commentary import get_commentary, get_commentary_record
        commentary = get_commentary(expert, view) if processes else None
        commentary_rec = (get_commentary_record(expert.id, view["key"])
                          if processes else None)
    except Exception:
        current_app.logger.exception("uzman yorumu üretilemedi: %s", expert.id)
        commentary, commentary_rec = None, None

    # W5c — atıf çipleri + kaynakça + süreç kartlarına Aşama-B metni.
    cite_meta: dict = {}
    citations: list = []
    proc_evals: dict = {}
    try:
        from prisma_home.evaluation import get_process_evaluation

        for p in processes:
            rec = get_process_evaluation(p["id"])
            if rec:
                proc_evals[p["id"]] = rec["text"]
        if commentary_rec and commentary_rec.get("cites"):
            citations = _citation_entries(
                commentary_rec, [p["id"] for p in processes])
            cite_meta = {c["id"]: c for c in citations}
    except Exception:
        current_app.logger.exception("atıf verisi hazırlanamadı: %s", expert.id)

    brief_slides = _brief_slides(commentary_rec, cite_meta)

    return render_template(
        "home/expert.html",
        mode="consumer",
        crumb=f"Uzmanlar · {expert.domain_label}",
        sidebar=get_sidebar(active_key=None),
        expert=expert,
        briefing=briefing,
        snapshots=bound,
        processes=processes,
        topics=topics,
        view_label=view.get("label") or "",
        commentary=commentary,
        commentary_rec=commentary_rec,
        cite_meta=cite_meta,
        citations=citations,
        proc_evals=proc_evals,
        brief_slides=brief_slides,
    )


def _frame_url(render_url: str, anchors: list[str], controls: list[dict]) -> str:
    """FB6 — bir sayfa-grubu için birleşik embed URL'i.

    Aynı SPA sayfasındaki birden çok blok tek iframe'de gösterilir: anchor'lar
    virgülle birleşir (embed hepsini izole eder), controls birliği tek state
    paketinde taşınır (aynı süreçteki bloklar zaten aynı görünümü kullanır)."""
    sep = "&" if "?" in render_url else "?"
    embed = f"{render_url}{sep}embed=1"
    anchors = [a for a in anchors if a]
    if anchors:
        embed += "&anchor=" + ",".join(anchors)
    state = _view_state_param({"controls": controls})
    if state:
        embed += f"&state={state}"
    return embed


def _slide_frames(blocks: list[dict]) -> list[dict]:
    """FB6 — slaytın atıf bloklarını SPA sayfasına göre iframe gruplarına böler.

    Aynı sayfadaki bloklar (ör. cost-analysis'teki waterfall + heatmap) tek
    frame'de birleşir → iki plot birlikte, kullandıkları filtreyle. Farklı
    sayfadakiler ayrı frame olur (slaytta chip'le geçilir). Frame sırası
    blokların slayttaki sırasını korur."""
    groups: dict[str, dict] = {}
    order: list[str] = []
    for b in blocks:
        key = b.get("render_url") or ""
        if not key:
            continue
        g = groups.get(key)
        if g is None:
            g = {"render_url": key, "anchors": [], "controls": [],
                 "titles": [], "labels": [], "block_ids": []}
            groups[key] = g
            order.append(key)
        if b.get("anchor"):
            g["anchors"].append(b["anchor"])
        for c in b.get("controls") or []:
            if c not in g["controls"]:
                g["controls"].append(c)
        g["titles"].append(b.get("title") or b.get("id"))
        lbl = b.get("state_label")
        if lbl and lbl not in g["labels"]:
            g["labels"].append(lbl)
        g["block_ids"].append(b.get("id"))
    frames = []
    for key in order:
        g = groups[key]
        frames.append({
            "url": _frame_url(g["render_url"], g["anchors"], g["controls"]),
            "title": " · ".join(t for t in g["titles"] if t),
            "label": " · ".join(g["labels"]),
            "block_ids": g["block_ids"],
        })
    return frames


def _brief_slides(record: dict | None, cite_meta: dict) -> list[dict]:
    """W6c/FB6 — headline kaydını sunum slide'larına çevirir.

    Slide = {"text": madde, "blocks": [cite_meta girdisi], "frames": [...]}.
    `blocks` atıf sırasını korur (chat bağlamı + geriye uyum). `frames` ise
    blokları SPA sayfasına göre gruplar: aynı sayfadaki bloklar tek iframe'de
    (çoklu-anchor) birlikte gösterilir. Atıfsız madde blocks=frames=[] ile
    gelir (yalnız-metin slide'ı). Headline yoksa boş liste."""
    if not record or not record.get("headlines"):
        return []
    slides = []
    for h in record["headlines"]:
        blocks = [cite_meta[c] for c in h.get("cites") or [] if c in cite_meta]
        slides.append({"text": h.get("text") or "", "blocks": blocks,
                       "frames": _slide_frames(blocks)})
    return [s for s in slides if s["text"]]


def _view_state_param(view: dict | None) -> str:
    """W6b — digest view'ini embed URL'ine taşınacak b64url paketine çevirir.

    Yalnız controls taşınır (SPA'nın uygulayacağı kısım); label UI'da metin
    olarak gösterilir. Kontrol yoksa boş string → parametre eklenmez."""
    controls = (view or {}).get("controls") or []
    if not controls:
        return ""
    import base64

    payload = json.dumps({"controls": controls}, ensure_ascii=False)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _citation_entries(record: dict, process_ids: list[str]) -> list[dict]:
    """W5c/W6b — brifing atıflarını render girdisine çözer.

    record["cites"] sıralı blok id listesi; her id bağlı süreçlerin dökümante
    bloklarında aranır. Bulunanlar numaralanır (1'den; bulunamayan — ör. blok
    registry'den kalkmış — sessizce düşer, numara yeniden dizilir). URL, bloğun
    canlı görünümü + embed modu + W6b view-state'i:
    render_url&embed=1&anchor=<id>&state=<b64url> — böylece açılan blok
    DEĞERLENDİRMENİN YAPILDIĞI tarih/boyutu gösterir. state_label kaynakça/
    slide'da okunur chip metnidir."""
    from prisma_home.evaluation import get_block_evaluation
    from prisma_home.processes import get_process

    index: dict[str, dict] = {}
    for pid in process_ids:
        p = get_process(pid)
        if not p:
            continue
        for b in p.get("blocks") or []:
            bid = b.get("id")
            url = b.get("render_url")
            if not bid or not url:
                continue
            anchor = (b.get("custom_render") or {}).get("anchor") or ""
            sep = "&" if "?" in url else "?"
            embed = f"{url}{sep}embed=1" + (f"&anchor={anchor}" if anchor else "")
            # W6b — Aşama-A kaydındaki digest view'i: state + okunur label.
            view = (get_block_evaluation(bid) or {}).get("view")
            state = _view_state_param(view)
            if state:
                embed += f"&state={state}"
            index[bid] = {"id": bid, "title": b.get("title") or bid,
                          "process": p.get("label", ""), "url": embed,
                          "state_label": (view or {}).get("label") or "",
                          # FB6 — çoklu-blok slayt için yapısal alanlar: aynı
                          # sayfadaki bloklar tek iframe'de birleştirilir.
                          "render_url": url, "anchor": anchor,
                          "controls": (view or {}).get("controls") or []}
    out = []
    for bid in record.get("cites") or []:
        entry = index.get(bid)
        if entry:
            out.append({**entry, "num": len(out) + 1})
    return out


@prisma_home_bp.route("/uzmanlar/<code>/sor", methods=["POST"])
@login_required
def expert_ask(code: str):
    """W4b — "…'ye sor": senkron JSON cevap (persona + doküman + metrik).

    Erişim kuralları expert_detail ile aynı."""
    store = current_app.config.get("EXPERT_STORE")
    expert = store.load(code.lower()) if store else None
    if expert is None:
        abort(404)
    # W8 — bakış erişimi + kapsam: cevap yalnız bu departmanın süreçlerine dayanır.
    from prisma_home.expert_views import legacy_view, resolve_view

    dept = getattr(current_user, "department", None) or ""
    r = resolve_view(expert, dept)
    if not r["granted"]:
        abort(403)
    view = r["view"] if not r["legacy"] else legacy_view(expert)
    from prisma_home.commentary import answer_question

    payload = request.get_json(silent=True) or {}
    context = payload.get("context")
    answer = answer_question(
        expert, payload.get("question", ""),
        context=context if isinstance(context, dict) else None,
        process_ids=view["process_ids"])
    return jsonify({"answer": answer})


@prisma_home_bp.route("/uzmanlar/<code>/briefing")
@login_required
def expert_briefing_json(code: str):
    """Phase 10E — engine output as JSON for programmatic access.

    Same access rules as the HTML detail route. The response includes a
    `from_cache` flag so callers (or curious humans) can tell whether the
    engine had to re-render this turn.
    """
    store = current_app.config.get("EXPERT_STORE")
    if store is None:
        return _json({"error": "EXPERT_STORE yapılandırılmamış."}, status=500)
    expert = store.load(code.lower())
    if expert is None:
        return _json({"error": f"Uzman bulunamadı: {code!r}"}, status=404)

    read = expert.access_scope.get("read") or []
    dept = getattr(current_user, "department", None) or ""
    if "*" not in read and dept not in read:
        return _json({"error": "Bu uzmana erişim yetkin yok."}, status=403)

    engine = current_app.config.get("BRIEFING_ENGINE")
    if engine is None:
        return _json({"error": "BRIEFING_ENGINE yapılandırılmamış."}, status=500)

    result = engine.render_briefing(expert)
    return _json(result.to_dict())


# ── Atölye home + pipeline aliases ───────────────────────────────────────────

@prisma_home_bp.route("/atolye/")
@login_required
def atolye_home():
    # Phase 12.workshops: surface the user's recent workshops on the
    # home page so the producer can resume work without going through
    # the sidebar. The helper lives in presentations.routes_kesif so
    # we lazily import to avoid a circular blueprint import at module
    # load time.
    from presentations.routes_kesif import list_workshops_for
    sicil = getattr(current_user, "sicil", None) or ""
    workshops = list_workshops_for(sicil)
    return render_template(
        "home/atolye_home.html",
        mode="atolye",
        crumb="Atölye · Ana",
        sidebar=get_sidebar(active_key="atolye"),
        workshops=workshops[:6],          # top-5 + 1 spillover
        workshop_total=len(workshops),
    )


# Phase 11.wire + 11.lib removed all atolye stub routes — every sidebar
# item now points at a real presentations.* page. If a future sidebar item
# lacks a producer module, re-add a stub here with the same shape as the
# original _stub.html render — it's intentionally cheap.


# ════════════════════════════════════════════════════════════════════════════
# Expert backend — Phase 10B JSON endpoints
# ════════════════════════════════════════════════════════════════════════════

def _json(payload, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False),
        status=status,
        mimetype="application/json",
    )


@prisma_home_bp.route("/api/experts/")
@login_required
def api_list_experts():
    """List experts visible to the current user.

    Filtering is delegated to `ExpertStore.list_for_user` which honours
    each expert's `access_scope.read` (default `["*"]` → visible to all).
    Returns a slim dict per expert — the full `persona.system_prompt`
    isn't shipped to the client because it's only consumed server-side
    by the briefing engine (Phase 10E).
    """
    store = current_app.config.get("EXPERT_STORE")
    if store is None:
        return _json({"error": "EXPERT_STORE yapılandırılmamış."}, status=500)

    experts = store.list_for_user(current_user)
    summary = [{
        "id":                e.id,
        "version":           e.version,
        "code":              e.code,
        "name":              e.name,
        "domain_label":      e.domain_label,
        "short_description": e.short_description,
        "status":            e.status,
        "ui":                e.ui,
    } for e in experts]
    return _json({"experts": summary})


@prisma_home_bp.route("/api/experts/<expert_id>")
@login_required
def api_get_expert(expert_id: str):
    """Full expert detail (persona + briefing_recipe + bound_content).

    Auth check: the user must have read access per the expert's
    `access_scope.read`. Loading-then-filtering keeps the response 404
    for nonexistent IDs and 403 for forbidden ones — important so the
    Phase 10D save-modal suggestion path can distinguish the two.
    """
    store = current_app.config.get("EXPERT_STORE")
    if store is None:
        return _json({"error": "EXPERT_STORE yapılandırılmamış."}, status=500)

    expert = store.load(expert_id.lower())
    if expert is None:
        return _json({"error": f"Uzman bulunamadı: {expert_id!r}"}, status=404)

    # W8 — bakış varsa sıkı, yoksa legacy access_scope (can_access tek karar).
    from prisma_home.expert_views import can_access

    dept = getattr(current_user, "department", None) or ""
    if not can_access(expert, dept):
        return _json({"error": "Bu uzmana erişim yetkin yok."}, status=403)

    return _json(expert.to_dict())


@prisma_home_bp.route("/api/experts/suggest", methods=["POST"])
@login_required
def api_suggest_experts():
    """Phase 10D — recommend bound_experts for a snapshot in flight.

    Body:
      {
        "manifest": { ... full manifest ... },
        "title": "...",         (optional override)
        "description": "..."    (optional)
      }

    Returns up to 5 suggestions; the UI auto-checks `confidence >= 0.7`
    and stars the top one. Always returns at least keyword-based
    results even when the LLM is unreachable.
    """
    store = current_app.config.get("EXPERT_STORE")
    if store is None:
        return _json({"error": "EXPERT_STORE yapılandırılmamış."}, status=500)

    body = request.get_json(silent=True) or {}
    manifest = body.get("manifest") or {}
    if not isinstance(manifest, dict):
        return _json({"error": "manifest bir nesne olmalı."}, status=400)

    title = (body.get("title") or "").strip()
    description = (body.get("description") or "").strip()

    suggestions = suggest_experts(
        manifest=manifest,
        title=title,
        description=description,
        expert_store=store,
        llm_client=current_app.config.get("LLM_CLIENT"),
    )
    return _json({"suggestions": suggestions})
