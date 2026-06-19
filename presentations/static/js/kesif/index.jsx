/* Phase 9.a — Keşif (Atölye / Discover) React entry.
 *
 * One bundle, one file (small enough). The UI is split into local
 * components but they're co-located here to keep the import graph tight
 * — no shared state library, no router, just useState + fetch.
 *
 * Mount: <div id="kesif-root"> in templates/presentations/atolye/kesif.html
 * Seed:  <script id="kesif-data" type="application/json">{...}</script>
 *
 * Catalog data flows from the unified /presentations/catalog API. Basket
 * mutations route to Phase 8's existing /presentations/<pid>/basket. The
 * draft pid arrives in the seed; "Hazırlık'a geç" calls /atolye/kesif/draft/promote
 * and hard-navigates to the returned URL.
 */
import { createRoot } from "react-dom/client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Search, X, Plus, ChevronDown, ChevronRight, Database, Crosshair,
  MessageCircle, ArrowRight, Loader2, Tag, Building2, ShoppingCart,
  Save,
} from "lucide-react";
import GraphCanvas from "./GraphCanvas.jsx";
import { useBlocksState, BlocksFilters, BlocksCenter } from "./blocks_workbench.jsx";
import ChatDrawer from "./ChatDrawer.jsx";
import useResizable from "../editor/lib/useResizable.js";

// ── Bootstrap ──────────────────────────────────────────────────────────

const DATA = JSON.parse(document.getElementById("kesif-data").textContent);
const ENDPOINTS = DATA.endpoints || {};
const USER = DATA.user || {};
const SEED_DRAFT = DATA.draft || {};
const SEED_BASKET = DATA.basket || [];
const SEED_CHAT_HISTORY = (DATA.chat && DATA.chat.history) || [];
const COSMOGRAPH_CONFIG = DATA.cosmograph || {};
// Phase 11.workbench — initial Atölye Workbench view, server-set from the
// path the user landed on (/atolye/kesif → tablolar, /atolye/bloklar → bloklar,
// /atolye/surecler → surecler).
const INITIAL_VIEW = DATA.initial_view || "tablolar";

// Mapping between view keys and their canonical URL paths. Used by the
// graph-tabs pill bar (history.pushState) and the popstate listener.
const VIEW_TO_PATH = {
  tablolar: "/presentations/atolye/kesif",
  bloklar:  "/presentations/atolye/bloklar",
  surecler: "/presentations/atolye/surecler",
};
function viewFromPath(path) {
  for (const [v, p] of Object.entries(VIEW_TO_PATH)) {
    if (path === p) return v;
  }
  return "tablolar";
}

// Helpers
const tableId = (schemaOrEntry, name) => {
  if (typeof schemaOrEntry === "object" && schemaOrEntry !== null) {
    return `${schemaOrEntry.schema}.${schemaOrEntry.name}`;
  }
  return `${schemaOrEntry}.${name}`;
};
const detailUrl = (schema, name) => `/presentations/catalog/${schema}/${name}`;
const conceptDetailUrl = (id) => `/presentations/catalog/concept/${id.replace(/^concept:/, "")}`;
const formatNumber = (n) => {
  if (n == null) return null;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
};

// ── Root app ───────────────────────────────────────────────────────────

function App() {
  // Phase 11.workbench — center-area view state. Source of truth comes
  // from the URL path (so refresh restores it correctly). Pill clicks
  // update both state and URL via history.pushState; the popstate
  // listener mirrors back/forward navigation into state.
  const [view, setView] = useState(INITIAL_VIEW);

  // Phase 12.kesif-header — workshop title + save state. The title is
  // editable in the top-left strip and persists to the draft manifest
  // (so promote() carries it across to the real presentation_id).
  const [workshopTitle, setWorkshopTitle] = useState(SEED_DRAFT.title || "");
  const [titleSaving, setTitleSaving] = useState(false);
  const [savedToast, setSavedToast] = useState("");
  const titleDebounceRef = useRef(null);

  const saveWorkshopTitle = useCallback(async (next) => {
    const url = ENDPOINTS.draft_title;
    if (!url) return;
    setTitleSaving(true);
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: next }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      console.warn("workshop title save failed:", e);
    } finally {
      setTitleSaving(false);
    }
  }, []);

  const onTitleChange = useCallback((next) => {
    setWorkshopTitle(next);
    if (titleDebounceRef.current) clearTimeout(titleDebounceRef.current);
    titleDebounceRef.current = setTimeout(() => saveWorkshopTitle(next), 600);
  }, [saveWorkshopTitle]);

  const onWorkshopSave = useCallback(async () => {
    const url = ENDPOINTS.draft_save;
    if (!url) return;
    // Flush any pending title debounce before the explicit save.
    if (titleDebounceRef.current) {
      clearTimeout(titleDebounceRef.current);
      titleDebounceRef.current = null;
      await saveWorkshopTitle(workshopTitle);
    }
    setTitleSaving(true);
    try {
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setSavedToast("Kaydedildi");
      setTimeout(() => setSavedToast(""), 1800);
    } catch (e) {
      console.warn("workshop save failed:", e);
      setSavedToast("Kaydedilemedi");
      setTimeout(() => setSavedToast(""), 2400);
    } finally {
      setTitleSaving(false);
    }
  }, [saveWorkshopTitle, workshopTitle]);

  // Bidirectional URL ↔ state sync.
  useEffect(() => {
    const onPop = () => setView(viewFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigateView = useCallback((next) => {
    if (next === view) return;
    const path = VIEW_TO_PATH[next];
    if (!path) return;
    setView(next);
    // pushState updates the URL without a full page reload. The browser's
    // back button + bookmarks still work because the corresponding Flask
    // routes all render this same shell with their initial_view set.
    if (window.location.pathname !== path) {
      window.history.pushState({ view: next }, "", path);
    }
  }, [view]);

  // Phase 11.workbench — bloklar view state (data fetch + filter state +
  // tree groups + selection). Loaded lazily once on App mount; the legacy
  // bloklar.bundle.js used to do this in its own entry point.
  const blocksState = useBlocksState({
    libraryListUrl:        ENDPOINTS.library_list,
    libraryPreviewTemplate: ENDPOINTS.library_preview_template,
  });

  // ── Catalog state ────────────────────────────────────────────────────
  const [catalog, setCatalog] = useState(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [catalogError, setCatalogError] = useState(null);

  // ── Selection + detail state ─────────────────────────────────────────
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailCache = useRef(new Map());

  // ── Filters ──────────────────────────────────────────────────────────
  // Two-stage filter state: as the user toggles checkboxes, only the
  // `pending` sets update — the left-rail tree filters live (cheap DOM
  // re-render). The graph-side selection only updates when the user
  // clicks "Uygula" (or resets) — without this, each toggle would
  // re-trigger Cosmograph's data pipeline, which on the 2.x DuckDB-WASM
  // build is multi-second slow.
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [selectedConcepts, setSelectedConcepts] = useState(new Set());
  const [selectedSchemas, setSelectedSchemas] = useState(new Set());
  // Schema tree starts fully collapsed — at 7+ schemas the all-expanded
  // default drowned the rail. Tracked as "expanded set" (default empty)
  // so adding a schema name to the set opens it; absence = collapsed.
  const [expandedSchemas, setExpandedSchemas] = useState(new Set());

  // Applied state — only what's currently committed to the graph dim.
  const [appliedConcepts, setAppliedConcepts] = useState(new Set());
  const [appliedSchemas, setAppliedSchemas] = useState(new Set());
  const [appliedSearch, setAppliedSearch] = useState("");
  // True when the pending picks differ from what's currently applied —
  // drives the "Uygula" button's enabled state.
  const filtersDirty = useMemo(() => {
    if (selectedConcepts.size !== appliedConcepts.size) return true;
    for (const c of selectedConcepts) if (!appliedConcepts.has(c)) return true;
    if (selectedSchemas.size !== appliedSchemas.size) return true;
    for (const s of selectedSchemas) if (!appliedSchemas.has(s)) return true;
    if (debouncedSearch !== appliedSearch) return true;
    return false;
  }, [selectedConcepts, selectedSchemas, debouncedSearch, appliedConcepts, appliedSchemas, appliedSearch]);
  const applyFilters = useCallback(() => {
    setAppliedConcepts(new Set(selectedConcepts));
    setAppliedSchemas(new Set(selectedSchemas));
    setAppliedSearch(debouncedSearch);
  }, [selectedConcepts, selectedSchemas, debouncedSearch]);
  const resetFilters = useCallback(() => {
    setSelectedConcepts(new Set());
    setSelectedSchemas(new Set());
    setSearch("");
    setAppliedConcepts(new Set());
    setAppliedSchemas(new Set());
    setAppliedSearch("");
  }, []);
  const filtersActive =
    appliedConcepts.size > 0 ||
    appliedSchemas.size > 0 ||
    !!appliedSearch;

  // ── Basket / draft state ─────────────────────────────────────────────
  const [draftPid, setDraftPid] = useState(SEED_DRAFT.pid || null);
  const [basket, setBasket] = useState(SEED_BASKET);
  const [basketBusy, setBasketBusy] = useState(false);
  const [promoting, setPromoting] = useState(false);

  // ── Chat highlight bridge (9.c) ──────────────────────────────────────
  // When ChatDrawer receives proposals, it pushes the table ids here so
  // GraphCanvas can pulse those nodes. We bump a counter alongside so
  // repeated highlights re-trigger the effect even if the id list is
  // identical (useful when the LLM proposes the same tables twice).
  const [highlightIds, setHighlightIds] = useState([]);
  const [highlightTick, setHighlightTick] = useState(0);
  const pushHighlight = useCallback((ids) => {
    if (!ids || ids.length === 0) return;
    setHighlightIds(ids);
    setHighlightTick((n) => n + 1);
  }, []);

  // Imperative handle into the chat panel — lets the detail card open
  // the chat and prefill the input ("Sohbette göster" button).
  const chatRef = useRef(null);
  const showInChat = useCallback((entry) => {
    if (!entry || !chatRef.current) return;
    const tid = entry.schema && entry.name
      ? `${entry.schema}.${entry.name}`
      : null;
    if (!tid) return;
    chatRef.current.openWithPrompt(
      `${tid} tablosu hakkında sormak istediğim: `
    );
  }, []);
  // Pulse a specific table on the graph (detail card "Grafikte göster").
  const focusOnGraph = useCallback((entry) => {
    if (!entry || !entry.schema || !entry.name) return;
    pushHighlight([`${entry.schema}.${entry.name}`]);
  }, [pushHighlight]);

  // Debounced search (200ms per spec §4.5/§4.6).
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim().toLowerCase()), 200);
    return () => clearTimeout(t);
  }, [search]);

  // Load catalog on mount.
  useEffect(() => {
    let cancelled = false;
    setLoadingCatalog(true);
    fetch(ENDPOINTS.catalog_list, { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`catalog: HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (cancelled) return;
        setCatalog(data);
        setLoadingCatalog(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setCatalogError(String(err.message || err));
        setLoadingCatalog(false);
      });
    return () => { cancelled = true; };
  }, []);

  // Load detail when selection changes. The selectedId can be either a
  // table id ("SCHEMA.TABLE") OR a concept hub id ("concept:as_of_time")
  // — the right rail picks the right card variant; we just route the
  // fetch through the matching endpoint.
  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    const cached = detailCache.current.get(selectedId);
    if (cached) { setDetail(cached); return; }
    const url = selectedId.startsWith("concept:")
      ? conceptDetailUrl(selectedId)
      : (() => {
          const [schema, name] = selectedId.split(/\.(.+)/);
          return detailUrl(schema, name);
        })();
    setDetailLoading(true);
    fetch(url, { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`detail: HTTP ${r.status}`);
        return r.json();
      })
      .then((entry) => {
        detailCache.current.set(selectedId, entry);
        setDetail(entry);
        setDetailLoading(false);
      })
      .catch(() => { setDetail(null); setDetailLoading(false); });
  }, [selectedId]);

  // ── Derived: filtered tables ─────────────────────────────────────────
  const tables = catalog?.tables || [];
  const facets = catalog?.facets || { departments: {}, concepts: {}, sources: {} };

  const filteredTables = useMemo(() => {
    return tables.filter((t) => {
      if (selectedSchemas.size && !selectedSchemas.has(t.schema)) return false;
      if (selectedConcepts.size) {
        const tableConcepts = new Set(t.concepts_bound || []);
        let any = false;
        for (const c of selectedConcepts) if (tableConcepts.has(c)) { any = true; break; }
        if (!any) return false;
      }
      if (debouncedSearch) {
        const hay = `${t.name} ${t.schema} ${t.description || ""}`.toLowerCase();
        if (!hay.includes(debouncedSearch)) return false;
      }
      return true;
    });
  }, [tables, selectedConcepts, selectedSchemas, debouncedSearch]);

  // Filter mask for the graph — only updates on Apply. Computed against
  // `appliedConcepts/Schemas/Search` (not the live `selected*`), so each
  // toggle in the rail doesn't trigger an expensive selectPoints round.
  const filterMaskIds = useMemo(() => {
    if (!filtersActive) return null;
    return new Set(
      tables
        .filter((t) => {
          if (appliedSchemas.size && !appliedSchemas.has(t.schema)) return false;
          if (appliedConcepts.size) {
            const c = new Set(t.concepts_bound || []);
            let any = false;
            for (const x of appliedConcepts) if (c.has(x)) { any = true; break; }
            if (!any) return false;
          }
          if (appliedSearch) {
            const hay = `${t.name} ${t.schema} ${t.description || ""}`.toLowerCase();
            if (!hay.includes(appliedSearch)) return false;
          }
          return true;
        })
        .map(tableId)
    );
  }, [tables, filtersActive, appliedConcepts, appliedSchemas, appliedSearch]);

  // Group by schema for the "Şemalar" tree section. Uploads section
  // dropped — upload now lives in Hazırlık (Phase 8).
  const treeGroups = useMemo(() => {
    const bySchema = new Map();
    for (const t of filteredTables) {
      const k = t.schema || "—";
      if (!bySchema.has(k)) bySchema.set(k, []);
      bySchema.get(k).push(t);
    }
    return { bySchema };
  }, [filteredTables]);

  // ── Filter toggles ───────────────────────────────────────────────────
  const toggleSet = (setter) => (value) => setter((prev) => {
    const next = new Set(prev);
    if (next.has(value)) next.delete(value); else next.add(value);
    return next;
  });
  const toggleConcept = toggleSet(setSelectedConcepts);
  const toggleSchema = toggleSet(setSelectedSchemas);
  const toggleSchemaExpanded = (schema) => setExpandedSchemas((prev) => {
    const next = new Set(prev);
    if (next.has(schema)) next.delete(schema); else next.add(schema);
    return next;
  });

  // ── Basket ops ───────────────────────────────────────────────────────
  const basketTableIds = useMemo(
    () => new Set(basket.map((b) => b.table)),
    [basket]
  );

  const addToBasket = useCallback(async (entry) => {
    if (!draftPid) return;
    const tid = tableId(entry);
    if (basketTableIds.has(tid)) return;
    const newItem = {
      table: tid,
      columns: (entry.columns || []).map((c) => c.name),
      row_filter: null,
    };
    const nextBasket = [...basket, newItem];
    setBasketBusy(true);
    try {
      const resp = await fetch(ENDPOINTS.basket_update, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ basket: nextBasket }),
      });
      if (!resp.ok) throw new Error(`basket HTTP ${resp.status}`);
      const data = await resp.json();
      setBasket(data.basket || nextBasket);
    } catch (err) {
      // Soft fail — basket panel keeps prior state. Surface via console for dev.
      console.warn("Sepete ekleme başarısız:", err);
    } finally {
      setBasketBusy(false);
    }
  }, [basket, basketTableIds, draftPid]);

  // Phase 11.polish-final: block sepete ekleme. Same /basket endpoint
  // accepts items with `kind: "block"` carrying the library_id + display
  // metadata. Existing table items default to kind:"table" (omitted).
  const basketBlockIds = useMemo(
    () => new Set(
      (basket || [])
        .filter((b) => b.kind === "block")
        .map((b) => b.library_id)
        .filter(Boolean),
    ),
    [basket],
  );

  const removeBlockFromBasket = useCallback(async (libraryId) => {
    if (!draftPid || !libraryId) return;
    const nextBasket = basket.filter((b) => !(b.kind === "block" && b.library_id === libraryId));
    setBasketBusy(true);
    try {
      const resp = await fetch(ENDPOINTS.basket_update, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ basket: nextBasket }),
      });
      if (!resp.ok) throw new Error(`basket HTTP ${resp.status}`);
      const data = await resp.json();
      setBasket(data.basket || nextBasket);
    } catch (err) {
      console.warn("Blok sepetten çıkarma başarısız:", err);
    } finally {
      setBasketBusy(false);
    }
  }, [basket, draftPid]);

  const addBlockToBasket = useCallback(async (libraryBlock) => {
    if (!draftPid) return null;
    const lid = libraryBlock.library_id || libraryBlock.id;
    if (!lid || basketBlockIds.has(lid)) return null;
    const newItem = {
      kind: "block",
      library_id: lid,
      name: libraryBlock.name || lid,
      block_type: libraryBlock.block_type || libraryBlock.viz_type || "",
      tags: libraryBlock.tags || [],
      owner_id: libraryBlock.owner_id || "",
    };
    const nextBasket = [...basket, newItem];
    setBasketBusy(true);
    try {
      const resp = await fetch(ENDPOINTS.basket_update, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ basket: nextBasket }),
      });
      if (!resp.ok) throw new Error(`basket HTTP ${resp.status}`);
      const data = await resp.json();
      setBasket(data.basket || nextBasket);
      return lid;
    } catch (err) {
      console.warn("Blok sepete ekleme başarısız:", err);
      return null;
    } finally {
      setBasketBusy(false);
    }
  }, [basket, basketBlockIds, draftPid]);

  // Graph context menu only has the node id — fetch the entry on demand.
  const addToBasketById = useCallback(async (id) => {
    if (!id || basketTableIds.has(id)) return;
    const [schema, name] = id.split(/\.(.+)/);
    try {
      const r = await fetch(detailUrl(schema, name), { credentials: "include" });
      if (!r.ok) throw new Error("detail");
      const entry = await r.json();
      await addToBasket(entry);
    } catch (err) {
      console.warn("Sepete ekleme (id) başarısız:", err);
    }
  }, [addToBasket, basketTableIds]);

  const bulkAddToBasket = useCallback(async (tableIds) => {
    if (!draftPid || !tableIds?.length) return;
    // We need column lists for each new table — fetch detail for ones we
    // haven't loaded yet. The detail endpoint is cheap (server-cached) so
    // doing this in parallel is fine for tens of tables; if 9.b.2 expands
    // bulk-add to hundreds, we'll want a dedicated /catalog/detail-bulk.
    const existing = new Set(basket.map((b) => b.table));
    const toAdd = tableIds.filter((id) => !existing.has(id));
    if (!toAdd.length) return;
    setBasketBusy(true);
    try {
      const details = await Promise.all(toAdd.map(async (id) => {
        const [schema, name] = id.split(/\.(.+)/);
        try {
          const r = await fetch(detailUrl(schema, name), { credentials: "include" });
          if (!r.ok) throw new Error("detail");
          return await r.json();
        } catch {
          // Fall back to a minimal entry — Hazırlık will pull columns later.
          return { schema, name, columns: [] };
        }
      }));
      const newItems = details.map((d) => ({
        table: `${d.schema}.${d.name}`,
        columns: (d.columns || []).map((c) => c.name),
        row_filter: null,
      }));
      const nextBasket = [...basket, ...newItems];
      const resp = await fetch(ENDPOINTS.basket_update, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ basket: nextBasket }),
      });
      if (!resp.ok) throw new Error(`basket HTTP ${resp.status}`);
      const data = await resp.json();
      setBasket(data.basket || nextBasket);
    } catch (err) {
      console.warn("Toplu sepete ekleme başarısız:", err);
    } finally {
      setBasketBusy(false);
    }
  }, [basket, draftPid]);

  const removeFromBasket = useCallback(async (tid) => {
    if (!draftPid) return;
    const nextBasket = basket.filter((b) => b.table !== tid);
    setBasketBusy(true);
    try {
      const resp = await fetch(ENDPOINTS.basket_update, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ basket: nextBasket }),
      });
      if (!resp.ok) throw new Error(`basket HTTP ${resp.status}`);
      const data = await resp.json();
      setBasket(data.basket || nextBasket);
    } catch (err) {
      console.warn("Sepetten çıkarma başarısız:", err);
    } finally {
      setBasketBusy(false);
    }
  }, [basket, draftPid]);

  const promote = useCallback(async () => {
    if (!draftPid || !basket.length || promoting) return;
    setPromoting(true);
    try {
      const resp = await fetch(ENDPOINTS.draft_promote, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ pid: draftPid }),
      });
      if (!resp.ok) throw new Error(`promote HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.hazirlik_url) {
        window.location.assign(data.hazirlik_url);
      } else if (data.presentation_id) {
        window.location.assign(`/presentations/hazirlik/${data.presentation_id}`);
      } else {
        throw new Error("promote returned no URL");
      }
    } catch (err) {
      console.warn("Hazırlık'a geçiş başarısız:", err);
      setPromoting(false);
    }
  }, [draftPid, basket.length, promoting]);

  // ── Render ───────────────────────────────────────────────────────────

  // Right rail collapsible — defaults closed, opens whenever a node is
  // selected (table OR concept), closes when the user clears selection
  // (background click on the graph, Escape, etc.). The body grid swaps
  // its column template when the rail is collapsed so the left rail +
  // canvas reclaim the freed width.
  const isRightOpen = !!selectedId || basket.length > 0;
  // Sürüklenebilir sol panel (Sunum/Hazırlık'la aynı hook + .resize-handle).
  // Grid tabanlı olduğu için genişliği inline grid-template-columns ile veriyoruz.
  const [leftW, startLeftDrag] = useResizable("kesif-left", 280, "right", { min: 220, max: 560 });

  return (
    <>
      {/* Phase 12.kesif-header — workshop title strip pinned above the
          3-column body. Title input on the left + Save button. The Sunum
          editor uses the same pattern (Header.jsx → meta.title input). */}
      <WorkshopHeader
        title={workshopTitle}
        pid={SEED_DRAFT.pid}
        saving={titleSaving}
        toast={savedToast}
        onChange={onTitleChange}
        onSave={onWorkshopSave}
      />
      <div className={`kesif-body${isRightOpen ? "" : " kesif-body--right-collapsed"}`}
           style={{ gridTemplateColumns: isRightOpen ? `${leftW}px 1fr 340px` : `${leftW}px 1fr` }}>
        <div className="resize-handle" style={{ left: (leftW - 3) + "px" }}
             onMouseDown={startLeftDrag} title="Sürükle: panel genişliğini değiştir" />
        <LeftRail
          facets={facets}
          loading={loadingCatalog}
          error={catalogError}
          search={search}
          onSearch={setSearch}
          selectedConcepts={selectedConcepts}
          selectedSchemas={selectedSchemas}
          onToggleConcept={toggleConcept}
          onToggleSchema={toggleSchema}
          filtersDirty={filtersDirty}
          filtersActive={filtersActive}
          onApplyFilters={applyFilters}
          onResetFilters={resetFilters}
          treeGroups={treeGroups}
          expandedSchemas={expandedSchemas}
          onToggleSchemaExpanded={toggleSchemaExpanded}
          selectedId={selectedId}
          onSelect={setSelectedId}
          basketTableIds={basketTableIds}
          onAddToBasket={addToBasketById}
          onRemoveFromBasket={removeFromBasket}
          view={view}
          blocksState={blocksState}
          chatProps={{
            ref: chatRef,
            chatSendUrl: ENDPOINTS.chat_send,
            chatClearUrl: ENDPOINTS.chat_clear,
            seedHistory: SEED_CHAT_HISTORY,
            basketTableIds,
            onAddToBasket: addToBasketById,
            onHighlight: pushHighlight,
          }}
        />
        <div className="kesif-canvas kesif-canvas--graph">
          {/* Phase 11.workbench — view tabs sit OUTSIDE the conditional
              center so the user can swap between Tablolar/Bloklar/Süreçler
              regardless of which center is currently mounted. */}
          <WorkbenchTabs view={view} onChange={navigateView} />
          {view === "tablolar" && (
            <GraphCanvas
              catalogGraphUrl={ENDPOINTS.catalog_graph}
              licenseKey={COSMOGRAPH_CONFIG.license_key}
              selectedId={selectedId}
              basketTableIds={basketTableIds}
              highlightIds={highlightIds}
              highlightTick={highlightTick}
              filterMaskIds={filterMaskIds}
              filterConceptIds={appliedConcepts}
              onSelect={setSelectedId}
              onAddToBasket={addToBasketById}
              onBulkAddToBasket={bulkAddToBasket}
            />
          )}
          {view === "bloklar" && (
            <BlocksCenter
              loading={blocksState.loading}
              error={blocksState.error}
              filteredBlocks={blocksState.filteredBlocks}
              totalCount={blocksState.blocks.length}
              selectedBid={blocksState.selectedBid}
              setSelectedBid={blocksState.setSelectedBid}
              selectedBlock={blocksState.selectedBlock}
              previewUrl={blocksState.previewUrl}
              basketBlockIds={basketBlockIds}
              onAddBlockToBasket={addBlockToBasket}
              basketBusy={basketBusy}
            />
          )}
          {view === "surecler" && <WorkbenchPlaceholder view="surecler" />}
        </div>
        {isRightOpen && (
          <RightRail
            detail={detail}
            detailLoading={detailLoading}
            selectedId={selectedId}
            basket={basket}
            basketTableIds={basketTableIds}
            basketBusy={basketBusy}
            promoting={promoting}
            onAdd={addToBasket}
            onRemove={removeFromBasket}
            onRemoveBlock={removeBlockFromBasket}
            onPromote={promote}
            onShowInChat={showInChat}
            onFocusOnGraph={focusOnGraph}
            onSelect={setSelectedId}
            onClose={() => setSelectedId(null)}
          />
        )}
      </div>
    </>
  );
}

// ── Components ─────────────────────────────────────────────────────────

// Real PRISMA brand asset lives at presentations/static/img/. Topbar
// renders an <img> so the source-of-truth stays the PNG the design
// team owns, not an approximated SVG. Falls back gracefully if the
// file is missing (broken image icon, but layout stays intact).
const PRISMA_LOGO_URL = "/presentations/static/img/prisma_logo.png";

function Topbar({ userName, userDept }) {
  return (
    <header className="kesif-topbar">
      <a href="/presentations/" className="kesif-topbar__logo" title="PRISMA — Tüm sunumlara dön">
        <img src={PRISMA_LOGO_URL} alt="PRISMA" className="kesif-topbar__logo-img" />
      </a>
      <span className="kesif-topbar__brand">
        Atölye
        <span className="kesif-topbar__crumb">/</span>
        Keşif
      </span>
      <nav className="kesif-topbar__tabs">
        <a href="/presentations/atolye/kesif" className="kesif-topbar__tab is-active">Tables</a>
        <a href="/presentations/atolye/bloklar" className="kesif-topbar__tab">Blocks</a>
        <a href="/presentations/atolye/surecler" className="kesif-topbar__tab">Processes</a>
      </nav>
      <span className="kesif-topbar__spacer" />
      {userName && (
        <span style={{ fontSize: 11, color: "#94a3b8" }}>
          {userName}{userDept ? ` · ${userDept}` : ""}
        </span>
      )}
      <a href="/presentations/" className="kesif-topbar__home">← Tüm Sunumlar</a>
    </header>
  );
}

// Phase 12.kesif-header — workshop title input + Save button strip.
// Sits between the PRISMA shell topbar (56px) and the 3-column kesif-body.
// The input auto-saves the title (debounced 600ms); the Save button
// flushes + bumps updated_at so the workshop floats to the top of
// Atölye Ana / Şablonlar listings.
function WorkshopHeader({ title, pid, saving, toast, onChange, onSave }) {
  const placeholder = "Yeni keşif çalışması";
  return (
    <header className="kesif-workshop-header">
      <div className="kesif-workshop-header__left">
        <input
          type="text"
          className="kesif-workshop-header__title"
          value={title || ""}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          maxLength={200}
          aria-label="Çalışma adı"
          spellCheck={false}
        />
        <span className="kesif-workshop-header__pid" title={pid || ""}>
          {pid ? `· ${(pid + "").slice(0, 22)}` : ""}
        </span>
      </div>
      <div className="kesif-workshop-header__right">
        {toast && (
          <span
            className={`kesif-workshop-header__toast${toast.startsWith("Kaydedil") && toast !== "Kaydedilemedi" ? "" : " is-error"}`}
            aria-live="polite"
          >
            {toast}
          </span>
        )}
        <button
          type="button"
          className="kesif-btn kesif-btn--primary kesif-workshop-header__save"
          onClick={onSave}
          disabled={saving}
          title="Çalışmayı şu anki haliyle kaydet"
        >
          {saving ? <Loader2 size={13} className="kesif-spin" /> : <Save size={13} />}
          <span>Kaydet</span>
        </button>
      </div>
    </header>
  );
}

function LeftRail({
  facets, loading, error,
  search, onSearch,
  selectedConcepts, selectedSchemas,
  onToggleConcept, onToggleSchema,
  filtersDirty, filtersActive, onApplyFilters, onResetFilters,
  treeGroups, expandedSchemas, onToggleSchemaExpanded,
  selectedId, onSelect,
  basketTableIds, onAddToBasket, onRemoveFromBasket,
  view, blocksState,
  chatProps,
}) {
  // Phase 11.workbench — bloklar view replaces the catalog filter group +
  // schema tree with the block-side facets + team tree. Sepet + chat stay
  // shared across views (rendered by the parent App).
  if (view === "bloklar" && blocksState) {
    return (
      <aside className="kesif-left">
        <div className="kesif-left__scroll">
          <BlocksFilters
            facets={blocksState.facets}
            loading={blocksState.loading}
            error={blocksState.error}
            search={blocksState.search}
            onSearch={blocksState.setSearch}
            selectedTeams={blocksState.selectedTeams}
            selectedVizTypes={blocksState.selectedVizTypes}
            selectedTags={blocksState.selectedTags}
            onToggleTeam={blocksState.toggleTeam}
            onToggleViz={blocksState.toggleViz}
            onToggleTag={blocksState.toggleTag}
            filtersActive={blocksState.filtersActive}
            onResetFilters={blocksState.resetFilters}
            treeGroups={blocksState.treeGroups}
            expandedTeams={blocksState.expandedTeams}
            onToggleTeamExpanded={blocksState.toggleTeamExpanded}
            selectedBid={blocksState.selectedBid}
            onSelect={blocksState.setSelectedBid}
            totalCount={blocksState.blocks.length}
          />
        </div>
        <ChatDrawer {...chatProps} />
      </aside>
    );
  }

  return (
    <aside className="kesif-left">
      <div className="kesif-left__scroll">
      <FilterGroup
        title="Kavram"
        items={facets.concepts}
        selected={selectedConcepts}
        onToggle={onToggleConcept}
      />
      <FilterGroup
        title="Kaynak"
        items={facets.sources}
        selected={selectedSchemas}
        onToggle={onToggleSchema}
      />

      {(filtersDirty || filtersActive) && (
        <div className="kesif-filter-apply">
          <button
            type="button"
            className="kesif-btn kesif-btn--primary kesif-filter-apply__btn"
            onClick={onApplyFilters}
            disabled={!filtersDirty}
            title={filtersDirty ? "Seçimleri grafiğe uygula" : "Aktif uygulanan filtreler"}
          >
            Uygula
          </button>
          {filtersActive && (
            <button
              type="button"
              className="kesif-btn kesif-filter-apply__btn"
              onClick={onResetFilters}
              title="Filtreleri sıfırla"
            >
              Sıfırla
            </button>
          )}
        </div>
      )}

      <div className="kesif-tree">
        <h3 className="kesif-tree__section">
          Şemalar
          <span className="kesif-filter-option__count" style={{marginLeft:'auto'}}>
            {Object.values(facets.sources).reduce((a, b) => a + b, 0)}
          </span>
        </h3>
        {/* Tablo araması artık ağacın başında — sol panelin en üstüne
            koymuştuk ama kavram + kaynak filtreleriyle ağacın arasına
            sıkışıyordu. Şemalar bölgesinin altına aldık, tablo
            sonuçları aramayı görüş alanında tutuyor. */}
        <div className="kesif-search kesif-search--inline">
          <input
            type="search"
            placeholder="🔍 Tablo ara…"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            aria-label="Tablo ara"
          />
        </div>
        {loading ? (
          <div className="kesif-tree__empty"><Loader2 size={12} className="kesif-spin" /> Yükleniyor…</div>
        ) : error ? (
          <div className="kesif-tree__empty" style={{ color: "#b91c1c" }}>{error}</div>
        ) : treeGroups.bySchema.size === 0 ? (
          <div className="kesif-tree__empty">Sonuç bulunamadı</div>
        ) : (
          [...treeGroups.bySchema.entries()].sort().map(([schema, items]) => {
            const expanded = expandedSchemas.has(schema);
            return (
              <div key={schema}>
                <div
                  className="kesif-tree__dept"
                  onClick={() => onToggleSchemaExpanded(schema)}
                  role="button"
                  tabIndex={0}
                >
                  <span className="kesif-tree__dept-caret">
                    {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                  </span>
                  {schema === "—" ? "Diğer" : schema}
                  <span className="kesif-filter-option__count" style={{marginLeft:'auto'}}>
                    {items.length}
                  </span>
                </div>
                {expanded && (
                  <div className="kesif-tree__tables">
                    {items.map((t) => (
                      <TableRow
                        key={tableId(t)} t={t}
                        isSelected={selectedId === tableId(t)}
                        isInBasket={basketTableIds?.has(tableId(t))}
                        onSelect={onSelect}
                        onAddToBasket={onAddToBasket}
                        onRemoveFromBasket={onRemoveFromBasket}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
      </div>{/* /.kesif-left__scroll */}

      {chatProps && <ChatDrawer {...chatProps} />}
    </aside>
  );
}

function FilterGroup({ title, items, selected, onToggle, labels = {}, defaultLimit = 5 }) {
  const entries = Object.entries(items || {});
  const [showAll, setShowAll] = useState(false);
  if (entries.length === 0) return null;
  // Show top-N facet rows by default; "Daha fazla göster" reveals the
  // rest. Keeps the rail tidy at 16+ concept catalogs without hiding
  // anything — every row is one click away.
  const visible = showAll ? entries : entries.slice(0, defaultLimit);
  const hiddenCount = entries.length - visible.length;
  return (
    <div>
      <h4 className="kesif-left__heading">{title}</h4>
      <div className="kesif-filter-group">
        {visible.map(([k, count]) => (
          <label key={k} className="kesif-filter-option">
            <input
              type="checkbox"
              checked={selected.has(k)}
              onChange={() => onToggle(k)}
            />
            <span>{labels[k] || k}</span>
            <span className="kesif-filter-option__count">{count}</span>
          </label>
        ))}
        {hiddenCount > 0 && (
          <button
            type="button"
            className="kesif-filter-group__more"
            onClick={() => setShowAll(true)}
          >
            +{hiddenCount} daha göster
          </button>
        )}
        {showAll && entries.length > defaultLimit && (
          <button
            type="button"
            className="kesif-filter-group__more"
            onClick={() => setShowAll(false)}
          >
            Daha az göster
          </button>
        )}
      </div>
    </div>
  );
}

function TableRow({
  t, isSelected, isInBasket = false, onSelect,
  onAddToBasket, onRemoveFromBasket, isUser = false,
}) {
  const tid = tableId(t);
  return (
    <div
      className={
        `kesif-tree__table` +
        (isSelected ? " is-selected" : "") +
        (isInBasket ? " is-in-basket" : "") +
        (isUser ? " is-user" : "")
      }
      onClick={() => onSelect(tid)}
      role="button"
      tabIndex={0}
      title={t.description || tid}
    >
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
        {t.name}
      </span>
      {isUser && <span className="kesif-tree__table-source">YÜKL</span>}
      {/* Phase 11.kesif polish — quick add/remove cart icon. Stop propagation
          so click doesn't also select the row. */}
      {(onAddToBasket || onRemoveFromBasket) && (
        <button
          type="button"
          className={`kesif-tree__cart${isInBasket ? " is-in" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            if (isInBasket) onRemoveFromBasket?.(tid);
            else onAddToBasket?.(tid);
          }}
          aria-label={isInBasket ? "Sepetten çıkar" : "Sepete ekle"}
          title={isInBasket ? "Sepetten çıkar" : "Sepete ekle"}
        >
          {isInBasket ? <X size={12} strokeWidth={2.2} /> : <ShoppingCart size={12} strokeWidth={2.2} />}
        </button>
      )}
    </div>
  );
}

function RightRail({
  detail, detailLoading, selectedId,
  basket, basketTableIds, basketBusy, promoting,
  onAdd, onRemove, onRemoveBlock, onPromote,
  onShowInChat, onFocusOnGraph,
  onSelect, onClose,
}) {
  const isConcept = selectedId && selectedId.startsWith("concept:");
  return (
    <aside className="kesif-right">
      {onClose && selectedId && (
        <button
          type="button"
          className="kesif-right__close"
          onClick={onClose}
          title="Detayı kapat"
          aria-label="Kapat"
        >
          ×
        </button>
      )}
      {isConcept ? (
        <ConceptDetailCard
          detail={detail}
          loading={detailLoading}
          selectedId={selectedId}
        />
      ) : (
        <DetailCard
          detail={detail}
          loading={detailLoading}
          selectedId={selectedId}
          inBasket={detail ? basketTableIds.has(tableId(detail)) : false}
          onAdd={onAdd}
          busy={basketBusy}
          onShowInChat={onShowInChat}
          onFocusOnGraph={onFocusOnGraph}
        />
      )}
      <BasketPanel
        basket={basket}
        onRemove={onRemove}
        onRemoveBlock={onRemoveBlock}
        onPromote={onPromote}
        onSelect={onSelect}
        promoting={promoting}
        busy={basketBusy}
        selectedId={selectedId}
      />
    </aside>
  );
}

function DetailCard({
  detail, loading, selectedId, inBasket, onAdd, busy,
  onShowInChat, onFocusOnGraph,
}) {
  if (!selectedId) {
    return (
      <div className="kesif-card">
        <h3 className="kesif-left__heading">Detay</h3>
        <div className="kesif-card__empty">
          Sol taraftan bir tablo seçin
        </div>
      </div>
    );
  }
  if (loading) {
    return (
      <div className="kesif-card">
        <h3 className="kesif-left__heading">Detay</h3>
        <div className="kesif-card__empty">
          <Loader2 size={14} className="kesif-spin" /> Yükleniyor…
        </div>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="kesif-card">
        <h3 className="kesif-left__heading">Detay</h3>
        <div className="kesif-card__empty">Detay yüklenemedi</div>
      </div>
    );
  }

  const isUserUpload = detail.source === "user_upload";
  const rowLabel = (() => {
    const n = detail.row_count_estimate;
    if (!n) return null;
    const fmt = formatNumber(n);
    if (detail.row_count_basis === "daily") return `${fmt} satır/gün`;
    return `${fmt} satır`;
  })();

  return (
    <div className="kesif-card">
      <div className="kesif-card__header">
        <div>
          <div className="kesif-card__title">{detail.name}</div>
          <div className="kesif-card__schema">
            {isUserUpload ? "Yüklemelerim" : detail.schema}
            {detail.department ? ` · ${detail.department}` : ""}
          </div>
        </div>
        <span className={`kesif-card__source-badge${isUserUpload ? " is-user" : ""}`}>
          {isUserUpload ? "Yükl." : "Kurumsal"}
        </span>
      </div>

      <div className="kesif-card__metaline">
        {rowLabel && <span>📊 {rowLabel}</span>}
        {detail.partition_column && <span>📅 Partition: {detail.partition_column}</span>}
        {isUserUpload && detail.original_filename && (
          <span title={detail.original_filename}>📎 {detail.original_filename}</span>
        )}
      </div>

      {detail.description && (
        <div className="kesif-card__section">
          <div className="kesif-card__section-title">Açıklama</div>
          <div className="kesif-card__description">{detail.description}</div>
        </div>
      )}

      <ConceptsBlock bound={detail.concepts_bound} />
      <LookupsBlock lookups={detail.lookups} />
      <ColumnsBlock columns={detail.columns} />

      <div className="kesif-actions">
        <button
          type="button"
          className="kesif-btn kesif-btn--primary"
          onClick={() => onAdd(detail)}
          disabled={busy || inBasket}
          title={inBasket ? "Zaten sepette" : "Sepete ekle"}
        >
          <Plus size={14} />
          {inBasket ? "Sepette" : "Sepete ekle"}
        </button>
        <button
          type="button"
          className="kesif-btn"
          onClick={() => onShowInChat?.(detail)}
          disabled={!onShowInChat}
          title="Bu tablo hakkında sohbet kutusuna prefill"
        >
          <MessageCircle size={14} />
          Sohbette göster
        </button>
        <button
          type="button"
          className="kesif-btn"
          onClick={() => onFocusOnGraph?.(detail)}
          disabled={!onFocusOnGraph}
          title="Grafikte parlat"
        >
          <Crosshair size={14} />
          Grafikte göster
        </button>
      </div>
    </div>
  );
}

// ── Concept detail card (variant for concept-hub clicks) ─────────────
//
// Shape comes from /catalog/concept/<id>: id, name, type, scope,
// description, canonical_values (when registry knows the concept),
// related_concepts, bound_tables, usage_count. When the registry is
// empty the endpoint synthesises a minimal record so we still render
// "which tables bind this concept" — the useful answer for discovery.

function ConceptDetailCard({ detail, loading, selectedId }) {
  if (!selectedId) return null;
  if (loading) {
    return (
      <div className="kesif-card">
        <h3 className="kesif-left__heading">Kavram</h3>
        <div className="kesif-card__empty">
          <Loader2 size={14} className="kesif-spin" /> Yükleniyor…
        </div>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="kesif-card">
        <h3 className="kesif-left__heading">Kavram</h3>
        <div className="kesif-card__empty">Kavram yüklenemedi</div>
      </div>
    );
  }

  return (
    <div className="kesif-card">
      <div className="kesif-card__header">
        <div>
          <div className="kesif-card__title">{detail.name || detail.id}</div>
          <div className="kesif-card__schema">
            Kavram
            {detail.type && detail.type !== "unknown" ? ` · ${detail.type}` : ""}
            {detail.scope ? ` · ${detail.scope}` : ""}
          </div>
        </div>
        <span className="kesif-card__source-badge" style={{ background: "#fee2e2", color: "#991b1b" }}>
          Kavram
        </span>
      </div>

      <div className="kesif-card__metaline">
        <span>📎 {detail.usage_count} tablo bağlı</span>
      </div>

      {detail.description && (
        <div className="kesif-card__section">
          <div className="kesif-card__section-title">Açıklama</div>
          <div className="kesif-card__description">{detail.description}</div>
        </div>
      )}

      {detail.canonical_values && detail.canonical_values.length > 0 && (
        <div className="kesif-card__section">
          <div className="kesif-card__section-title">
            Kanonik değerler ({detail.canonical_values.length})
          </div>
          <div className="kesif-columns">
            {detail.canonical_values.slice(0, 30).map((v) => v.code).join(", ")}
            {detail.canonical_values.length > 30 ? "…" : ""}
          </div>
        </div>
      )}

      {detail.related_concepts && detail.related_concepts.length > 0 && (
        <div className="kesif-card__section">
          <div className="kesif-card__section-title">İlişkili kavramlar</div>
          <div className="kesif-card__description">
            {detail.related_concepts.join(", ")}
          </div>
        </div>
      )}

      {detail.bound_tables && detail.bound_tables.length > 0 && (
        <div className="kesif-card__section">
          <div className="kesif-card__section-title">
            Bağlı tablolar ({detail.bound_tables.length})
          </div>
          <div className="kesif-concepts">
            {detail.bound_tables.map((t) => (
              <div key={`${t.schema}.${t.name}`} className="kesif-concept is-bound">
                <span className="kesif-concept__icon">→</span>
                <span><strong>{t.name}</strong> <span style={{color:"#94a3b8"}}>· {t.schema}</span></span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ConceptsBlock({ bound }) {
  // Phase 9 UX revision: only the concepts the table actually binds
  // are listed. The "unbound" list was visual noise — every absent
  // concept is implicitly unbound; calling them out distracted from
  // the bindings that DO exist.
  const b = bound || [];
  if (b.length === 0) return null;
  return (
    <div className="kesif-card__section">
      <div className="kesif-card__section-title">Kavramlar</div>
      <div className="kesif-concepts">
        {b.map((c) => (
          <div key={c} className="kesif-concept is-bound">
            <span className="kesif-concept__icon">✓</span>
            <span>{c}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function LookupsBlock({ lookups }) {
  if (!lookups || lookups.length === 0) return null;
  return (
    <div className="kesif-card__section">
      <div className="kesif-card__section-title">Lookuplar</div>
      <div className="kesif-lookups">
        {lookups.map((lk, i) => (
          <div key={i}>
            <span className="kesif-lookup__from">{lk.from_column}</span>
            <ArrowRight size={11} style={{ margin: "0 4px", verticalAlign: "-2px" }} />
            <strong>{lk.to_table}</strong>
            {lk.to_display ? ` (${lk.to_key} → ${lk.to_display})` : ` (${lk.to_key})`}
          </div>
        ))}
      </div>
    </div>
  );
}

function ColumnsBlock({ columns }) {
  if (!columns || columns.length === 0) return null;
  return (
    <div className="kesif-card__section">
      <div className="kesif-card__section-title">
        Kolonlar ({columns.length})
      </div>
      <div className="kesif-columns">
        {columns.map((c) => c.name).join(", ")}
      </div>
    </div>
  );
}

function BasketPanel({ basket, onRemove, onRemoveBlock, onPromote, onSelect, promoting, busy, selectedId }) {
  // Phase 11.basket-blocks: split basket into Tablolar / Bloklar.
  // Items without an explicit `kind` are treated as tables (legacy compat).
  const { tables, blocks } = useMemo(() => {
    const tables = [];
    const blocks = [];
    for (const b of basket || []) {
      if (b.kind === "block") blocks.push(b);
      else tables.push(b);
    }
    return { tables, blocks };
  }, [basket]);

  const total = basket.length;
  return (
    <div className="kesif-basket">
      <div className="kesif-basket__title">
        Sepet
        <span className="kesif-basket__count">{total}</span>
      </div>
      {total === 0 ? (
        <div className="kesif-card__empty">Sepetiniz boş</div>
      ) : (
        <>
          {tables.length > 0 && (
            <div className="kesif-basket__group">
              <div className="kesif-basket__group-title">
                Tablolar
                <span className="kesif-basket__group-count">{tables.length}</span>
              </div>
              <div className="kesif-basket__items">
                {tables.map((b) => {
                  const [schema, name] = b.table.split(/\.(.+)/);
                  const isSelected = selectedId === b.table;
                  return (
                    <div
                      key={b.table}
                      className={`kesif-basket__item${isSelected ? " is-selected" : ""}${onSelect ? " is-clickable" : ""}`}
                      onClick={onSelect ? () => onSelect(b.table) : undefined}
                      role={onSelect ? "button" : undefined}
                      tabIndex={onSelect ? 0 : undefined}
                      title={onSelect ? `${b.table} — detayı aç` : undefined}
                    >
                      <div>
                        <div className="kesif-basket__item-name">{name}</div>
                        <div className="kesif-basket__item-schema">{schema}</div>
                      </div>
                      <button
                        type="button"
                        className="kesif-basket__remove"
                        onClick={(e) => { e.stopPropagation(); onRemove(b.table); }}
                        disabled={busy}
                        title="Sepetten çıkar"
                        aria-label={`${b.table} sepetten çıkar`}
                      >
                        ×
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {blocks.length > 0 && (
            <div className="kesif-basket__group">
              <div className="kesif-basket__group-title">
                Bloklar
                <span className="kesif-basket__group-count">{blocks.length}</span>
              </div>
              <div className="kesif-basket__items">
                {blocks.map((b) => (
                  <div
                    key={b.library_id}
                    className="kesif-basket__item"
                    title={b.name}
                  >
                    <div>
                      <div className="kesif-basket__item-name">{b.name || b.library_id}</div>
                      <div className="kesif-basket__item-schema">
                        {b.block_type || "blok"}{b.owner_id ? ` · ${b.owner_id}` : ""}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="kesif-basket__remove"
                      onClick={(e) => { e.stopPropagation(); onRemoveBlock?.(b.library_id); }}
                      disabled={busy}
                      title="Sepetten çıkar"
                      aria-label={`${b.name || b.library_id} sepetten çıkar`}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="kesif-basket__cta">
            <button
              type="button"
              className="kesif-btn kesif-btn--primary"
              onClick={onPromote}
              disabled={promoting || total === 0}
            >
              {promoting ? <Loader2 size={14} className="kesif-spin" /> : <ArrowRight size={14} />}
              Hazırlık'a geç
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ── Mount ──────────────────────────────────────────────────────────────

// ── Atölye Workbench shared bits (Phase 11.workbench) ────────────────

// Top-left pills above the canvas. Replaces the old GraphTabsOverlay that
// lived inside GraphCanvas. Centralized here so it can drive `view` state
// + URL sync via the App's navigateView.
function WorkbenchTabs({ view, onChange }) {
  const items = [
    { key: "tablolar", label: "Tablolar" },
    { key: "bloklar",  label: "Bloklar"  },
    { key: "surecler", label: "Süreçler" },
  ];
  return (
    <nav className="kesif-graph-tabs" aria-label="Atölye Workbench sekmeler">
      {items.map((it) => (
        <a
          key={it.key}
          href={VIEW_TO_PATH[it.key]}
          className={`kesif-graph-tab${view === it.key ? " is-active" : ""}`}
          onClick={(e) => {
            // Honor middle-click / cmd-click → let the browser open a new tab.
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
            e.preventDefault();
            onChange(it.key);
          }}
        >
          {it.label}
        </a>
      ))}
    </nav>
  );
}

// Placeholder rendered for views that haven't been merged into the
// Workbench yet (bloklar list + surecler observatory). The shell + filter
// rail + basket still render; only the center is a stub until the next
// turn extracts the real content.
function WorkbenchPlaceholder({ view }) {
  const copy = {
    bloklar: {
      title:    "Bloklar görünümü",
      subtitle: "Blok listesi bir sonraki iterasyonda buraya entegre olacak.",
      hint:     "Şu an için tek-shell navigasyon iskeleti çalışıyor — sepet + chat + filtreler korunur.",
    },
    surecler: {
      title:    "Süreç İzleme",
      subtitle: "İş süreci kataloğu Phase 13 ile birlikte gelir.",
      hint:     "Şimdilik placeholder — sepet ve sol panel canlı kalır.",
    },
  }[view] || { title: view, subtitle: "", hint: "" };
  return (
    <div className="kesif-workbench-placeholder">
      <div className="kesif-workbench-placeholder__inner">
        <div className="kesif-workbench-placeholder__eyebrow">Yakında</div>
        <h2>{copy.title}</h2>
        <p className="kesif-workbench-placeholder__sub">{copy.subtitle}</p>
        <p className="kesif-workbench-placeholder__hint">{copy.hint}</p>
      </div>
    </div>
  );
}


// Tiny CSS animation for the spinner — avoids pulling lucide-react classes
// out of the bundle.
const style = document.createElement("style");
style.textContent = `
  @keyframes kesif-spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
  .kesif-spin { animation: kesif-spin 0.8s linear infinite; }
`;
document.head.appendChild(style);

createRoot(document.getElementById("kesif-root")).render(<App />);
