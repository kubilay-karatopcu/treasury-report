/* Hazırlık (Stage 2 / Prepare) — ER editor, redesign v2. Phase 8.b.
 *
 * Layout mirrors Sunum: left = data-source categories (basket) + chat (reuses
 * editor.css .editor-sidebar / .sources-* / .chat-box for an identical look);
 * right = React Flow ER canvas (top) + a resizable AG Grid preview drawer
 * (bottom). Nodes show the table description (not column lists). Connecting two
 * nodes opens a join-key modal (no "which columns to project" step — the query
 * is written in Sunum). Filtering happens in the AG Grid preview: play with the
 * grid's column filters, then "Filtreleri kaydet" → scope filters (concept if
 * the column is concept-bound, else raw), applied at fetch to shrink + exported
 * to Sunum.
 */
import { createRoot } from "react-dom/client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  Handle, Position, useNodesState,
  BaseEdge, EdgeLabelRenderer, getBezierPath,
} from "@xyflow/react";
import { AgGridReact } from "ag-grid-react";
import "ag-grid-enterprise";   // demo (unlicensed → watermark) — pivot/row-grouping/aggregation
import "@xyflow/react/dist/style.css";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";
// `ag-theme-alpine.css` ships both light + dark variants — switching the
// className on the wrapper is enough to flip themes, no second import.
// Veri Yükle: reuse Sunum's modal — editor/lib/api.js has the upload routes,
// and api.js's API_BASE is hazirlik-aware (strips /hazirlik/ from the path).
import UploadModal from "../editor/components/UploadModal.jsx";
import FilterPanel from "./FilterPanel.jsx";
import {
  X, Plus, Trash2, Database, ArrowLeft, ArrowRight, ChevronLeft, ChevronRight,
  MessageSquare, Save, Eraser, Table2,
  Building2, Percent, Network, Calendar, Upload, Send, Loader2, Eye, EyeOff, Info, Tag, Code2,
} from "lucide-react";

// Same icon picker Sunum's Basket.jsx uses — domain.icon takes the
// catalog-declared value (building / percent / network / …), otherwise we
// fall back to a heuristic on the domain id/label.
const DOMAIN_ICONS = {
  building: Building2, percent: Percent, network: Network,
  calendar: Calendar, database: Database, upload: Upload,
};
function pickDomainIcon(domain) {
  if (domain.icon && DOMAIN_ICONS[domain.icon]) return DOMAIN_ICONS[domain.icon];
  const haystack = `${domain.id || ""} ${domain.label || ""}`.toLowerCase();
  if (/(mevduat|deposit|bilanco)/.test(haystack)) return Building2;
  if (/(nii|faiz|interest|rate)/.test(haystack)) return Percent;
  if (/(rakip|sektor|sector|competitor|market)/.test(haystack)) return Network;
  if (/(takvim|calendar|event)/.test(haystack)) return Calendar;
  if (/(yuklenen|upload)/.test(haystack)) return Upload;
  return Database;
}

const DATA = JSON.parse(document.getElementById("hazirlik-data").textContent);
const PID = DATA.presentation_id;
const _path = window.location.pathname;
const BUILD_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/build`);
const PREVIEW_BUILD_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/preview-build`);
const PREVIEW_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/preview`);
const PREVIEW_SQL_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/preview-sql`);
const CHAT_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/chat`);
const APPLY_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/apply-suggestion`);
const ROUTING_OVERRIDE_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/routing-override`);
const ROUTING_RECOMPUTE_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/recompute-routing`);
const REFINE_SIZES_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/refine-sizes`);
const SAVE_DRAFT_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/save-draft`);
const PROJECTION_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/projection-update`);
const PREVIEW_DERIVATION_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/preview-derivation`);
const DISTINCT_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/distinct`);
const LIST_URL = _path.slice(0, _path.indexOf("/hazirlik")) + "/";

const COLS_BY_ALIAS = DATA.columns_by_alias || {};
const SUGGESTED = DATA.suggested_edges || [];
// Re-group catalog domains by SCHEMA (the prefix before "." in each
// table id). Keşif/Atölye uses schema names; we keep Hazırlık + Sunum
// in sync — domain labels like "Mevduat Verileri" / "NII & Faiz" only
// fit a curated catalog, not the real 30+ table fixture set.
// `dom_uploads` (synthetic user-upload domain) is preserved verbatim
// because it's grouped by upload file, not by schema.
function regroupBySchema(domains) {
  const result = [];
  const bySchema = new Map();
  for (const d of (domains || [])) {
    if (d.id === "dom_uploads") {
      result.push(d);
      continue;
    }
    for (const t of (d.tables || [])) {
      const tid = t.id || "";
      const schema = tid.includes(".") ? tid.split(".")[0] : "Diğer";
      if (!bySchema.has(schema)) {
        const group = { id: `schema_${schema}`, label: schema, tables: [] };
        bySchema.set(schema, group);
        result.push(group);
      }
      bySchema.get(schema).tables.push(t);
    }
  }
  return result;
}
const DOMAINS = regroupBySchema(DATA.catalog?.domains || []);
const ROUTING_CONFIG = DATA.routing_config || { threshold_bytes: 500_000_000, hard_ceiling_bytes: 10_000_000_000 };

// Compact byte formatter — "320 MB", "4.2 GB", "—" for unknown.
function formatBytes(n) {
  if (n == null || n <= 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1_000_000) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(n < 100_000_000 ? 1 : 0)} MB`;
  return `${(n / 1_000_000_000).toFixed(n < 100_000_000_000 ? 1 : 0)} GB`;
}

// Madde 4 — merge background EXPLAIN PLAN size estimates (alias → {estimated_
// bytes, rows, source}) into the scope's routing. Only the size number changes;
// for system-decided tables the cached/lazy decision is recomputed from the
// refined bytes (a tighter estimate can flip lazy→cached), while user overrides
// keep their decision. Returns the same object when nothing changed so React
// can bail on the setScope.
function applySizeEstimates(scope, estimates) {
  if (!estimates || !Object.keys(estimates).length) return scope;
  const threshold = ROUTING_CONFIG.threshold_bytes;
  let changed = false;
  const basket = scope.basket.map((b) => {
    const est = estimates[b.alias];
    if (!est || !b.routing) return b;
    if (b.routing.estimated_bytes === est.estimated_bytes &&
        b.routing.estimate_source === est.source) return b;
    const routing = { ...b.routing, estimated_bytes: est.estimated_bytes, estimate_source: est.source };
    if (b.routing.decided_by !== "user") {
      routing.decision = est.estimated_bytes <= threshold ? "cached" : "lazy";
    }
    changed = true;
    return { ...b, routing };
  });
  return changed ? { ...scope, basket } : scope;
}

// table id ("SCHEMA.NAME") → catalog table (desc, columns…)
const CATALOG_BY_ID = (() => {
  const m = {};
  DOMAINS.forEach((d) => (d.tables || []).forEach((t) => { m[t.id] = t; }));
  return m;
})();
const DOMAIN_BY_TABLE_ID = (() => {
  const m = {};
  DOMAINS.forEach((d) => (d.tables || []).forEach((t) => {
    m[t.id] = { id: d.id, label: d.label, color: d.color || null };
  }));
  return m;
})();
const tableId = (ref) => (ref ? (ref.schema ? `${ref.schema}.${ref.name}` : ref.name) : null);
const descFor = (ref) => (ref ? CATALOG_BY_ID[tableId(ref)]?.desc || "" : "");
const colorForRef = (ref) => DOMAIN_BY_TABLE_ID[tableId(ref)]?.color || null;

const joinKey = (l, lc, r, rc) => [`${l}.${lc}`, `${r}.${rc}`].sort().join("—");
const rid = () => Math.random().toString(36).slice(2, 7);

function makeAlias(name, existing) {
  let base = (name || "table").toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^_+/, "");
  if (!/^[a-z]/.test(base)) base = "t_" + base;
  base = base.slice(0, 40);
  while (base.length < 3) base += "_t";
  let alias = base, i = 2;
  const taken = new Set(existing);
  while (taken.has(alias)) { alias = `${base}_${i}`.slice(0, 40); i++; }
  return alias;
}

function filtersForAlias(scope, alias) {
  // Per spec §2.1: `applies_to: []` resolves to "all basket aliases that bind
  // the concept" — match by the alias's column→concept index.
  const cols = COLS_BY_ALIAS[alias] || [];
  const boundConcepts = new Set(cols.map((c) => c.concept).filter(Boolean));
  const pinned = (scope?.filters?.pinned || []).filter((f) => {
    const at = f.applies_to || [];
    if (at.length === 0) return boundConcepts.has(f.concept);
    return at.includes(alias);
  });
  const raw = (scope?.filters?.raw || []).filter((f) => f.alias === alias);
  return { pinned, raw };
}
function summarizeFilter(f) {
  if (f.op === "between") return `${f.from}…${f.to}`;
  if (f.values && f.values.length) return f.values.slice(0, 3).join(", ") + (f.values.length > 3 ? "…" : "");
  if (f.value != null) return String(f.value);
  return "";
}
// Singleton holder for node-data handlers — App registers once, every
// enrichNodeData() call reads from here. Avoids threading callbacks through
// every node-creating site (initialNodes / addTable / saveAsTable / chat-apply).
const NODE_HANDLERS = { onOverrideRouting: null, onEditRefresh: null };

function enrichNodeData(item, scope) {
  return {
    ...nodeData(item),
    activeFilters: filtersForAlias(scope, item.alias),
    onOverrideRouting: NODE_HANDLERS.onOverrideRouting,
    onEditRefresh: NODE_HANDLERS.onEditRefresh,
  };
}

// Faz C — compact label for a dataset's cron/refresh policy on the node card.
function refreshLabel(refresh) {
  if (!refresh || refresh.kind !== "scheduled") return "manuel";
  if (refresh.interval_seconds) {
    const m = Math.round(refresh.interval_seconds / 60);
    return m >= 60 ? `${Math.round(m / 60)}sa` : `${m}dk`;
  }
  const times = refresh.schedule?.times || [];
  return times.length ? times.join(",") : "zamanlı";
}

// ── Modal shell ────────────────────────────────────────────────────────────

function Modal({ title, onClose, children, footer, size = "sm" }) {
  return (
    <div className="ts-modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`ts-modal ts-modal--${size}`} role="dialog">
        <header className="ts-modal-header">
          <h3 className="ts-modal-title">{title}</h3>
          <button className="ts-modal-close" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="ts-modal-body ts-scroll">{children}</div>
        {footer && <footer className="ts-modal-footer">{footer}</footer>}
      </div>
    </div>
  );
}

// ── Table node (description, card-level handles) ─────────────────────────────

function TableNode({ data }) {
  const { item, desc, size, colCount, keyCols, derived, activeFilters, color, onOverrideRouting, onEditRefresh } = data;
  const cached = item.routing?.decision === "cached";
  // Faz R1 node tipleri: main (table_ref/sql) SABİT — lazy/cache toggle + cron yok;
  // filter-node BOYUTLU (EXPLAIN PLAN) → cached/lazy rozeti + cron; aggregate/
  // calculated DuckDB'de → "türetilmiş" (boyut yok).
  const isFilter = item.derivation?.kind === "filter";
  const sized = !derived || isFilter;          // boyut rozeti gösterilenler
  const filterCount = (activeFilters?.pinned?.length || 0) + (activeFilters?.raw?.length || 0);
  const headStyle = !derived && color ? { background: color } : undefined;
  const estimatedBytes = item.routing?.estimated_bytes;
  const decidedBy = item.routing?.decided_by || "system";
  const sizeText = sized ? formatBytes(estimatedBytes) : null;
  // Madde 4 — flag whether the size came from the optimizer (filter-aware) or
  // the catalog-only partition estimate, so the tooltip is honest about it.
  const measured = item.routing?.estimate_source === "explain_plan";
  const sourceNote = measured
    ? " (EXPLAIN PLAN ile ölçüldü — filtreleri hesaba katar)"
    : " (katalog tahmini — sadece partition filtresini hesaba katar)";
  const decisionTitle = (derived && !isFilter)
    ? "Türetilmiş tablo — DuckDB'de hesaplanır."
    : `Tahmini boyut: ${formatBytes(estimatedBytes)}${sourceNote}. Karar: sistem (boyuta göre).`;
  return (
    <div className={`hz-node${derived ? " hz-node--derived" : ""}`}>
      <div className="hz-node-head" style={headStyle}>
        <span className="hz-node-alias"><Database size={12} /> {item.table_ref ? `${item.table_ref.schema}.${item.table_ref.name}` : item.alias}{item.sql && <span className="hz-sql-tag">SQL</span>}</span>
        <span
          className={`hz-badge hz-badge--${sized ? (cached ? "cached" : "lazy") : "derived"}`}
          title={decisionTitle}
        >
          {sized
            ? (cached ? `cached · ${sizeText}` : `lazy · ${sizeText}`)
            : "türetilmiş"}
        </span>
      </div>
      {!derived && (
        <div className="hz-node-routing">
          <span
            className="hz-proj-count"
            title="Projeksiyon. Main node sabittir — lazy/cache ve cron yalnız filtreli (türetilmiş) node'larda."
          >
            {item.projection?.include_all ? `tümü (${colCount})` : `${item.projection?.columns?.length || 0}/${colCount} kolon`}
          </span>
          <span className="hz-main-lock" title="Main node — boyut sisteme göre; manuel toggle yok. Filtrele → türetilmiş node oluşur.">🔒 main</span>
        </div>
      )}
      {derived && (
        <div className="hz-node-routing">
          <span
            className="hz-proj-count"
            title="Türetilmiş tablo — kaynak(lar)ından DuckDB'de hesaplanıp parquet'e materialise edilir"
          >
            {colCount} kolon
          </span>
          <button
            type="button"
            className="hz-route-override hz-refresh-btn"
            title="Yenileme (cron) ayarla — türetilmiş tablo ne sıklıkla yeniden hesaplanıp materialise edilsin"
            onClick={(e) => { e.stopPropagation(); NODE_HANDLERS.onEditRefresh && NODE_HANDLERS.onEditRefresh(item.alias); }}
          >
            ⟳ {refreshLabel(item.refresh)}
          </button>
        </div>
      )}
      <div className="hz-node-meta">{size ? `${size} · ` : ""}{colCount} kolon</div>
      {desc && <div className="hz-node-desc">{desc}</div>}
      <div className="hz-node-keys">
        {(keyCols || []).map((c) => (
          <div key={c.name} className="hz-node-col" title={c.concept || ""}>
            <Handle type="target" position={Position.Left} id={c.name} className="hz-handle" />
            <span className="hz-col-name">{c.name}</span>
            {c.concept && <span className="hz-col-concept">{c.concept}</span>}
            <Handle type="source" position={Position.Right} id={c.name} className="hz-handle" />
          </div>
        ))}
        <div className="hz-node-col hz-node-col--other">
          <Handle type="target" position={Position.Left} id="__other__" className="hz-handle hz-handle--other" />
          <span className="hz-col-name hz-muted">Diğer Kolonlar…</span>
          <Handle type="source" position={Position.Right} id="__other__" className="hz-handle hz-handle--other" />
        </div>
      </div>
      {filterCount > 0 && (
        <div className="hz-node-filters" title={`${filterCount} aktif filtre`}>
          {activeFilters.pinned.map((f) => (
            <div key={f.id} className="hz-node-filter">
              <span className="hz-node-filter-icon" aria-hidden>🔒</span>
              {f.concept} {f.op} {summarizeFilter(f)}
            </div>
          ))}
          {activeFilters.raw.map((f) => (
            <div key={f.id} className="hz-node-filter">
              <span className="hz-node-filter-icon" aria-hidden>⚲</span>
              {f.column} {f.op} {summarizeFilter(f)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
const NODE_TYPES = { tableNode: TableNode };

function nodeData(item) {
  const cols = COLS_BY_ALIAS[item.alias] || [];
  if (item.derivation) {
    const d = item.derivation;
    const kindLabel = d.kind === "filter" ? "filtre"
      : d.kind === "calculated" ? "hesaplama" : "aggregate";
    return {
      item, derived: true, desc: `${d.source_alias} → ${kindLabel}`,
      // Faz R1: filter-node boyutu hesaplanır (EXPLAIN PLAN) → rozet gösterilir;
      // aggregate/calculated DuckDB'de → boyut yok (size=null).
      size: null, colCount: cols.length, keyCols: cols.filter((c) => c.join_key),
      color: null,   // derived → purple via .hz-node--derived class
    };
  }
  if (item.sql) {
    return {
      item, derived: false, desc: "manuel SQL",
      size: null, colCount: cols.length,
      keyCols: cols.filter((c) => c.join_key), color: null,
    };
  }
  const cat = CATALOG_BY_ID[tableId(item.table_ref)];
  return {
    item, desc: descFor(item.table_ref),
    size: cat?.rows || null, colCount: cols.length,
    keyCols: cols.filter((c) => c.join_key),
    color: colorForRef(item.table_ref),
  };
}

function initialNodes(scope) {
  return scope.basket.map((item, i) => ({
    id: item.alias, type: "tableNode",
    position: item.layout ? { x: item.layout.x, y: item.layout.y }
      : { x: 60 + (i % 3) * 340, y: 60 + Math.floor(i / 3) * 240 },
    data: nodeData(item),
  }));
}

// Concept lookup used when a confirmed join doesn't carry a `concept` field
// yet (older payloads) — falls back to checking the alias's column.
function _conceptForJoinSide(alias, column) {
  const cols = COLS_BY_ALIAS[alias] || [];
  return cols.find((c) => c.name === column)?.concept || null;
}

// Compute suggested join edges for the current basket. We walk every
// alias pair and emit:
//   (a) FK lookup hints — a column on the left has `lookup.table` matching
//       another alias's source table (lookup info comes from the table-doc
//       store + the initial DATA.columns_by_alias payload).
//   (b) Shared concept hints — both aliases bind the same concept; the
//       column on each side is the one that carries it.
// Dedup by sorted column-pair. Confirmed joins are filtered out by the
// caller. Pure function of `scope.basket` + COLS_BY_ALIAS (already kept
// in sync as items are added / removed / Apply'd), so re-runs cheap.
function computeSuggestedEdges(basket) {
  const out = [];
  const seen = new Set();
  // alias → table_ref.name (for lookup matching)
  const byTableName = {};
  basket.forEach((b) => {
    if (b.table_ref?.name) byTableName[b.table_ref.name] = b.alias;
  });
  const aliases = basket.map((b) => b.alias);

  const add = (la, lc, ra, rc, source, concept) => {
    if (la === ra) return;
    const key = [la + "." + lc, ra + "." + rc].sort().join("—");
    if (seen.has(key)) return;
    seen.add(key);
    out.push({
      left:  { alias: la, column: lc },
      right: { alias: ra, column: rc },
      kind:  source === "catalog_lookup" ? "lookup" : "inner",
      source, concept,
    });
  };

  // (a) FK lookup → solid suggestion.
  aliases.forEach((alias) => {
    (COLS_BY_ALIAS[alias] || []).forEach((c) => {
      const lk = c.lookup;
      if (lk && byTableName[lk.table]) {
        add(alias, c.name, byTableName[lk.table], lk.key, "catalog_lookup", c.concept || null);
      }
    });
  });

  // (b) shared concept → softer suggestion. concept → alias → column name.
  const conceptCols = {};
  aliases.forEach((alias) => {
    (COLS_BY_ALIAS[alias] || []).forEach((c) => {
      if (!c.concept) return;
      if (!conceptCols[c.concept]) conceptCols[c.concept] = {};
      if (!(alias in conceptCols[c.concept])) conceptCols[c.concept][alias] = c.name;
    });
  });
  Object.entries(conceptCols).forEach(([concept, byAlias]) => {
    const entries = Object.entries(byAlias);
    for (let i = 0; i < entries.length; i++) {
      for (let j = i + 1; j < entries.length; j++) {
        const [la, lc] = entries[i], [ra, rc] = entries[j];
        add(la, lc, ra, rc, "shared_concept:" + concept, concept);
      }
    }
  });
  return out;
}

// Stable, order-independent key for "edge between this node pair" — used
// to group all edges (confirmed + suggested) between the same two table
// nodes so their labels can be stacked vertically rather than overlap.
function pairKey(a, b) { return a < b ? `${a}::${b}` : `${b}::${a}`; }

function buildEdges(scope) {
  const confirmed = new Set(scope.joins.map(
    (j) => joinKey(j.left.alias, j.left.column, j.right.alias, j.right.column)));
  const edges = scope.joins.map((j) => {
    const concept = j.concept
      || _conceptForJoinSide(j.left.alias, j.left.column)
      || _conceptForJoinSide(j.right.alias, j.right.column);
    return {
      id: j.id, source: j.left.alias, target: j.right.alias,
      // Anchor the line to the *correct* column's handle on each side — without
      // these, React Flow picks the first Handle in the DOM and the line
      // visually snaps to the top row even though the label is right.
      sourceHandle: j.left.column,
      targetHandle: j.right.column,
      type: "hzPairEdge",
      className: "hz-edge hz-edge--confirmed",
      data: {
        confirmed: true, join: j, concept,
        label: concept ? `${concept} · ${j.left.column}=${j.right.column}` : `${j.kind}: ${j.left.column}=${j.right.column}`,
        kind: "confirmed",
      },
    };
  });
  const aliases = new Set(scope.basket.map((b) => b.alias));
  const dismissed = new Set(scope.dismissed_suggestions || []);
  // Suggested edges are now re-derived from the current basket every render
  // (computeSuggestedEdges reads COLS_BY_ALIAS, which we keep in sync as
  // tables are added / removed / Apply'd). The server-provided SUGGESTED
  // list is used as a fallback so initial page-load wiring (lookup info
  // from the table-doc store) still shows up — duplicates are deduped by
  // column pair below; dismissed suggestions are filtered out entirely.
  // Auto-suggested join edges disabled — too cluttered on the canvas. Manual
  // joins (drag node→node) and confirmed scope.joins still render. Flip back to
  // `[...computeSuggestedEdges(scope.basket), ...SUGGESTED]` to re-enable.
  const suggested = [];
  const sugSeen = new Set();
  suggested.forEach((s, i) => {
    if (!aliases.has(s.left.alias) || !aliases.has(s.right.alias)) return;
    const k = joinKey(s.left.alias, s.left.column, s.right.alias, s.right.column);
    if (confirmed.has(k) || sugSeen.has(k) || dismissed.has(k)) return;
    sugSeen.add(k);
    const concept = s.concept || _conceptForJoinSide(s.left.alias, s.left.column);
    const kindLabel = s.source === "catalog_lookup" ? "lookup" : "öneri";
    edges.push({
      id: `sug_${i}`, source: s.left.alias, target: s.right.alias,
      sourceHandle: s.left.column,
      targetHandle: s.right.column,
      type: "hzPairEdge",
      className: "hz-edge hz-edge--suggested",
      // Edge interaction hints (handled in App.onEdgeClick):
      //   click       → confirms suggestion as a real join
      //   shift+click → dismisses (added to scope.dismissed_suggestions)
      data: {
        suggested: true, edge: s, concept, dismissKey: k,
        label: `${concept ? `${concept} · ${kindLabel}` : kindLabel} · ⇧×`,
        kind: "suggested",
      },
    });
  });

  // Faz R1 — derivation lineage edges (kaynak main node → türetilmiş node).
  // filter node'unun edge'ine tıklayınca filtre ekranı + kaynak query açılır
  // (App.onEdgeClick). aggregate/calculated için de lineage gösterilir.
  scope.basket.forEach((b) => {
    if (!b.derivation) return;
    const d = b.derivation;
    const sources = d.kind === "calculated"
      ? (d.source_aliases || [])
      : (d.source_alias ? [d.source_alias] : []);
    sources.forEach((src) => {
      if (!aliases.has(src) || !aliases.has(b.alias)) return;
      edges.push({
        id: `deriv_${b.alias}_${src}`,
        source: src, target: b.alias,
        sourceHandle: "__other__", targetHandle: "__other__",
        type: "hzPairEdge",
        className: `hz-edge hz-edge--derivation hz-edge--deriv-${d.kind}`,
        data: {
          derivation: true, derivedAlias: b.alias, sourceAlias: src,
          derivKind: d.kind,
          label: d.kind === "filter" ? "filtre →" : `${d.kind} →`,
          kind: "derivation",
        },
      });
    });
  });

  // Stack labels: for each (sourceNode, targetNode) pair, annotate each
  // edge in the group with its index + group size so the custom edge
  // component can offset the label vertically and labels stop overlapping.
  const groups = new Map();
  edges.forEach((e) => {
    const pk = pairKey(e.source, e.target);
    const arr = groups.get(pk) || [];
    arr.push(e);
    groups.set(pk, arr);
  });
  groups.forEach((arr) => {
    arr.forEach((e, idx) => {
      e.data = { ...(e.data || {}), stackIndex: idx, stackOf: arr.length };
    });
  });
  return edges;
}

// Custom edge that renders its label via EdgeLabelRenderer with a vertical
// offset derived from `data.stackIndex / stackOf`. This is what stops the
// "öneri" pills from piling on top of each other when several suggestions
// cross between the same two table cards.
function HzPairEdge({
  id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition,
  style, markerEnd, data, selected,
}) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition,
  });
  const stackIndex = data?.stackIndex ?? 0;
  const stackOf = data?.stackOf ?? 1;
  // Center the stack on the midpoint: i=0 of N items sits at top, i=N-1 at
  // bottom. Spacing slightly larger than the chip height (≈18px) so they
  // don't quite touch.
  const offsetY = (stackIndex - (stackOf - 1) / 2) * 22;
  const isSuggested = data?.kind === "suggested";
  const cls = `hz-edge-chip${isSuggested ? " hz-edge-chip--suggested" : " hz-edge-chip--confirmed"}${selected ? " is-selected" : ""}`;
  return (
    <>
      <BaseEdge id={id} path={edgePath} style={style} markerEnd={markerEnd} />
      {data?.label && (
        <EdgeLabelRenderer>
          <div
            className={cls}
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY + offsetY}px)`,
              pointerEvents: "all",
            }}
            title={data.label}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const EDGE_TYPES = { hzPairEdge: HzPairEdge };

// ── Join-key modal ───────────────────────────────────────────────────────────

function JoinKeyModal({ left, right, preLcol, preRcol, onSave, onClose }) {
  const lc = COLS_BY_ALIAS[left] || [];
  const rc = COLS_BY_ALIAS[right] || [];
  const [lcol, setLcol] = useState(preLcol || lc[0]?.name || "");
  const [rcol, setRcol] = useState(preRcol || rc[0]?.name || "");
  const [kind, setKind] = useState("lookup");

  // Each side's selected column's concept — render as a chip so the user
  // sees at a glance whether the pairing makes semantic sense.
  const lConcept = lc.find((c) => c.name === lcol)?.concept || null;
  const rConcept = rc.find((c) => c.name === rcol)?.concept || null;
  const conceptsMatch = lConcept && rConcept && lConcept === rConcept;

  // Format an <option> label that includes the column's concept (if any) so
  // the dropdown also shows the semantic tag inline.
  const optLabel = (c) => (c.concept ? `${c.name} · ${c.concept}` : c.name);

  return (
    <Modal title={`Join: ${left} → ${right}`} onClose={onClose} footer={
      <>
        <button className="ts-btn" onClick={onClose}>Vazgeç</button>
        <button className="ts-btn ts-btn--primary" disabled={!lcol || !rcol}
          onClick={() => onSave({ lcol, rcol, kind })}>Join kur</button>
      </>
    }>
      <p className="hz-muted">Hangi kolonlardan birleşsin?</p>
      <div className="hz-row">
        <label className="hz-field">{left}
          <select value={lcol} onChange={(e) => setLcol(e.target.value)}>
            {lc.map((c) => <option key={c.name} value={c.name}>{optLabel(c)}</option>)}
            {lc.length === 0 && <option value="">(kolon yok)</option>}
          </select>
          {lConcept && (
            <span className={`hz-col-concept hz-join-concept${conceptsMatch ? " hz-join-concept--match" : ""}`}>{lConcept}</span>
          )}
        </label>
        <label className="hz-field">{right}
          <select value={rcol} onChange={(e) => setRcol(e.target.value)}>
            {rc.map((c) => <option key={c.name} value={c.name}>{optLabel(c)}</option>)}
            {rc.length === 0 && <option value="">(kolon yok)</option>}
          </select>
          {rConcept && (
            <span className={`hz-col-concept hz-join-concept${conceptsMatch ? " hz-join-concept--match" : ""}`}>{rConcept}</span>
          )}
        </label>
      </div>
      {lConcept && rConcept && (
        <p className={`hz-join-concept-hint${conceptsMatch ? " is-match" : " is-mismatch"}`}>
          {conceptsMatch
            ? `✓ Her iki tarafta da '${lConcept}' concept'i — anlamsal eşleşme.`
            : `⚠ Concept'ler farklı: '${lConcept}' ↔ '${rConcept}'. Join yine de kurulabilir ama semantik eşleşme yok.`}
        </p>
      )}
      <label className="hz-field">Tür
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="lookup">lookup</option>
          <option value="inner">inner</option>
          <option value="left">left</option>
        </select>
      </label>
    </Modal>
  );
}

// ── Build-confirm modal (re-entry warning, 8.e) ────────────────────────────

// Human-readable filter description for the diff chips (instead of raw ids
// like "pf_as_of_time_buv45") — looks the filter up in the pending scope.
function _opText(f) {
  if (f.op === "between") return `${f.from ?? "…"} → ${f.to ?? "…"}`;
  if (f.op === "in") return `∈ {${(f.values || []).join(", ")}}`;
  if (f.op === "not_in") return `∉ {${(f.values || []).join(", ")}}`;
  if (f.op === "eq") return `= ${f.value ?? (f.values || [])[0] ?? ""}`;
  if (f.op === "last_n_days") return `son ${f.value ?? f.n ?? ""} gün`;
  const sym = { gt: ">", gte: "≥", lt: "<", lte: "≤" }[f.op];
  return sym ? `${sym} ${f.value ?? ""}` : (f.op || "");
}
function describeFilter(id, scope) {
  const fs = (scope && scope.filters) || {};
  const p = (fs.pinned || []).find((x) => x.id === id);
  if (p) return `${p.concept}: ${_opText(p)}`;
  const r = (fs.raw || []).find((x) => x.id === id);
  if (r) return `${r.column}: ${_opText(r)}`;
  return id;
}

function BuildConfirmModal({ preview, scope, onConfirm, onCancel }) {
  const { diff = {}, affected_blocks = [], summary = {} } = preview;
  const breaking = summary.breaking || 0;
  const warning = summary.warning || 0;
  const blocks = affected_blocks;

  // Section-by-section diff summary lines — only the non-empty ones. Filters
  // render as human descriptions (concept/column + op + değerler), not raw ids.
  const fdesc = (id) => describeFilter(id, scope);
  const lines = [];
  if (diff.added?.length) lines.push({ label: "Eklenen tablolar", items: diff.added });
  if (diff.removed?.length) lines.push({ label: "Çıkarılan tablolar", items: diff.removed });
  if (diff.changed?.length) lines.push({ label: "Değişen tablolar", items: diff.changed });
  if (diff.filters?.added?.length) lines.push({ label: "Eklenen filtre", items: diff.filters.added.map(fdesc) });
  if (diff.filters?.removed?.length) lines.push({ label: "Kaldırılan filtre", items: diff.filters.removed.map(fdesc) });
  if (diff.filters?.modified?.length) lines.push({ label: "Değişen filtre", items: diff.filters.modified.map(fdesc) });
  if (diff.pin_flips?.length) {
    lines.push({
      label: "Sabitleme durumu değişen filtre",
      items: diff.pin_flips.map((p) => `${fdesc(p.id)} (${p.direction})`),
    });
  }
  if (diff.joins?.added?.length) lines.push({ label: "Eklenen join", items: diff.joins.added });
  if (diff.joins?.removed?.length) lines.push({ label: "Çıkarılan join", items: diff.joins.removed });
  if (diff.joins?.modified?.length) lines.push({ label: "Değişen join", items: diff.joins.modified });

  return (
    <Modal
      title="Değişiklikleri onayla"
      size="md"
      onClose={onCancel}
      footer={
        <>
          <button className="ts-btn" onClick={onCancel}>Vazgeç</button>
          <button
            className={`ts-btn ts-btn--primary${breaking > 0 ? " ts-btn--danger" : ""}`}
            onClick={onConfirm}
          >
            {breaking > 0 ? "Yine de devam et" : "Onayla ve geç"}
          </button>
        </>
      }
    >
      <p className="hz-muted" style={{ marginTop: 0, fontSize: 12 }}>
        Bu değişiklikler uygulanıp Sunum'a geçilecek (yeni bir sürüm kaydedilir).
      </p>
      {lines.length === 0 ? (
        <p className="hz-muted">Görünür bir değişiklik yok — yine de onaylayıp Sunum'a geçebilirsin.</p>
      ) : (
        <div className="hz-build-diff">
          {lines.map((l, i) => (
            <div key={i} className="hz-build-diff-row">
              <div className="hz-build-diff-label">{l.label}</div>
              <div className="hz-build-diff-items">
                {l.items.map((it) => <span key={it} className="hz-build-diff-chip">{it}</span>)}
              </div>
            </div>
          ))}
        </div>
      )}

      {blocks.length > 0 && (
        <div className="hz-build-affected">
          <div className="hz-build-affected-head">
            Etkilenen bloklar{" "}
            {breaking > 0 && <span className="hz-build-pill hz-build-pill--break">{breaking} kırılan</span>}
            {warning > 0 && <span className="hz-build-pill hz-build-pill--warn">{warning} uyarı</span>}
          </div>
          <ul className="hz-build-affected-list">
            {blocks.map((b) => (
              <li key={b.block_id} className={`hz-build-block hz-build-block--${b.severity}`}>
                <div className="hz-build-block-head">
                  <span className="hz-build-block-type">{b.block_type}</span>
                  <span className="hz-build-block-title">{b.block_title}</span>
                </div>
                <ul className="hz-build-block-reasons">
                  {b.reasons.map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              </li>
            ))}
          </ul>
          {breaking > 0 && (
            <p className="hz-build-affected-note">
              ⚠ Kırılan bloklar Sunum'da hata durumunda render olacak. İlgili blokların kaynak
              tablosunu / filtresini güncellemen gerekecek.
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}


// ── Table docs panel (eye icon → slide-in side dock) ───────────────────────

function TableDocsPanel({ table, onClose }) {
  const ref = useRef(null);
  const cols = table.columns || [];
  const filters = table.common_filters || [];

  // ESC closes, click outside also closes — but ignore clicks on a
  // sources-table-eye button (the same eye that opened this panel — that
  // click toggles via the parent handler).
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    function onDown(e) {
      if (!ref.current) return;
      if (ref.current.contains(e.target)) return;
      if (e.target.closest && e.target.closest(".sources-table-eye")) return;
      onClose();
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [onClose]);

  return (
    <aside className="hz-docs-panel" ref={ref}>
      <header className="hz-docs-panel-head">
        <div>
          <div className="hz-docs-panel-title">{table.id}</div>
          {(table.desc || table.rows) && (
            <div className="hz-docs-panel-sub">
              {table.desc}{table.rows ? ` · ${table.rows} satır` : ""}
            </div>
          )}
        </div>
        <button type="button" className="hz-icon-btn" onClick={onClose} title="Kapat (ESC)">
          <X size={15} />
        </button>
      </header>

      <div className="hz-docs-panel-body ts-scroll">
        <div className="hz-docs-section">
          <div className="hz-docs-section-title">
            <Database size={12} strokeWidth={2} />
            <span>Kolonlar ({cols.length})</span>
          </div>
          {cols.length === 0
            ? <p className="hz-muted">(kolon bilgisi yok)</p>
            : (
              <table className="hz-docs-cols">
                <thead>
                  <tr>
                    <th>İsim</th><th>Tip</th><th>Null</th><th>İşaretler</th>
                  </tr>
                </thead>
                <tbody>
                  {cols.map((c) => (
                    <tr key={c.name}>
                      <td className="hz-docs-col-name">{c.name}</td>
                      <td className="hz-docs-col-type">{c.type || "—"}</td>
                      <td>
                        {c.nullable === false
                          ? <span className="hz-docs-pill hz-docs-pill--req">NOT NULL</span>
                          : <span className="hz-docs-pill">NULL</span>}
                      </td>
                      <td className="hz-docs-col-marks">
                        {c.key && <span className="hz-docs-pill hz-docs-pill--key">key</span>}
                        {c.concept && <span className="hz-col-concept">{c.concept}</span>}
                        {c.common_values && c.common_values.length > 0 && (
                          <span className="hz-docs-vals" title={c.common_values.join(", ")}>
                            {c.common_values.slice(0, 4).join(", ")}
                            {c.common_values.length > 4 ? "…" : ""}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>

        {filters.length > 0 && (
          <div className="hz-docs-section">
            <div className="hz-docs-section-title">
              <Tag size={12} strokeWidth={2} />
              <span>Sık Kullanılan Filtreler ({filters.length})</span>
            </div>
            <div className="hz-docs-filters">
              {filters.map((f, i) => (
                <div className="hz-docs-filter" key={i}>
                  <div className="hz-docs-filter-label">{f.label}</div>
                  <code className="hz-docs-filter-expr">{f.expression}</code>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}


// ── Left sidebar (Sunum design: source categories + chat) ────────────────────

// ── Refresh / cron policy modal (Faz C) ─────────────────────────────────────
const _REFRESH_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

// Shared refresh-policy form. Controlled: emits the computed DatasetRefresh via
// onChange on every edit. Used by RefreshModal (node ⟳) and SqlDatasetModal.
function RefreshFields({ value, onChange }) {
  const v = value || { kind: "manual" };
  const [kind, setKind] = useState(v.kind === "scheduled" ? "scheduled" : "manual");
  const [mode, setMode] = useState(v.schedule ? "schedule" : "interval");
  const [minutes, setMinutes] = useState(v.interval_seconds ? Math.round(v.interval_seconds / 60) : 10);
  const [times, setTimes] = useState((v.schedule?.times || ["09:00"]).join(", "));
  const [days, setDays] = useState(new Set(v.schedule?.days || []));

  const toggleDay = (d) => setDays((s) => {
    const n = new Set(s);
    if (n.has(d)) n.delete(d); else n.add(d);
    return n;
  });

  useEffect(() => {
    let refresh;
    if (kind === "manual") {
      refresh = { kind: "manual" };
    } else if (mode === "interval") {
      const secs = Math.max(60, Math.min(86400, Math.round(Number(minutes || 0) * 60)));
      refresh = { kind: "scheduled", interval_seconds: secs };
    } else {
      const ts = times.split(",").map((s) => s.trim())
        .filter((s) => /^([01]\d|2[0-3]):[0-5]\d$/.test(s));
      refresh = ts.length
        ? { kind: "scheduled", schedule: { times: ts, days: _REFRESH_DAYS.filter((d) => days.has(d)), timezone: "Europe/Istanbul" } }
        : { kind: "scheduled", interval_seconds: 3600 };
    }
    onChange && onChange(refresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, mode, minutes, times, days]);

  return (
    <>
      <label className="hz-field">Yenileme
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="manual">Manuel (yalnız build'de bir kez)</option>
          <option value="scheduled">Zamanlı (cron)</option>
        </select>
      </label>
      {kind === "scheduled" && (
        <>
          <label className="hz-field">Tür
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="interval">Aralık (her N dakika)</option>
              <option value="schedule">Takvim (saat + gün)</option>
            </select>
          </label>
          {mode === "interval" ? (
            <label className="hz-field">Dakika (1–1440)
              <input type="number" min={1} max={1440} value={minutes}
                onChange={(e) => setMinutes(e.target.value)} />
            </label>
          ) : (
            <>
              <label className="hz-field">Saatler (HH:MM, virgülle)
                <input type="text" value={times} placeholder="09:00, 17:00"
                  onChange={(e) => setTimes(e.target.value)} />
              </label>
              <div className="hz-field">Günler (boş = her gün)
                <div className="hz-row" style={{ flexWrap: "wrap", gap: 4 }}>
                  {_REFRESH_DAYS.map((d) => (
                    <button key={d} type="button" className="ts-btn"
                      style={{ padding: "2px 7px", fontSize: 11,
                               background: days.has(d) ? "var(--bs-primary, #2563eb)" : undefined,
                               color: days.has(d) ? "#fff" : undefined }}
                      onClick={() => toggleDay(d)}>{d}</button>
                  ))}
                </div>
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}

function RefreshModal({ item, onSave, onClose }) {
  const [refresh, setRefresh] = useState(item.refresh || { kind: "manual" });
  return (
    <Modal title={`Yenileme — ${item.alias}`} onClose={onClose} footer={
      <>
        <button className="ts-btn" onClick={onClose}>Vazgeç</button>
        <button className="ts-btn ts-btn--primary" onClick={() => onSave(refresh)}>Kaydet</button>
      </>
    }>
      <p className="hz-muted">Bu cached dataset cron ile ne sıklıkla yeniden çekilsin?</p>
      <RefreshFields value={item.refresh} onChange={setRefresh} />
    </Modal>
  );
}

// ── Manuel SQL dataset modal (Faz C) ────────────────────────────────────────
// Sunum'daki blok+manuel SQL akışının Hazırlık karşılığı: tablo adı + SQL yaz →
// önizle/doğrula → saklama (cached/lazy) → cached ise cron. Sonuç scope.basket'e
// bir `sql` dataset olarak eklenir.
function SqlDatasetModal({ existingAliases, existing, onSave, onClose }) {
  const isEdit = !!existing;
  const [alias, setAlias] = useState(existing?.alias || "");
  const [sql, setSql] = useState(existing?.sql || "");
  const [routing, setRouting] = useState(existing?.routing || "cached");
  const [refresh, setRefresh] = useState(existing?.refresh || { kind: "manual" });
  // Edit modunda mevcut kolonlarla başla → kullanıcı SQL'e dokunmadan da
  // "Kaydet" edebilir; SQL'i değiştirince textarea onChange preview'i sıfırlar
  // (setPreview(null)) → yeniden doğrulama gerekir.
  const [preview, setPreview] = useState(
    isEdit && (existing.columns || []).length
      ? { columns: existing.columns, rows: [], row_count: 0 }
      : null,
  );
  const [errors, setErrors] = useState([]);
  const [busy, setBusy] = useState(false);

  const runPreview = async () => {
    setBusy(true); setErrors([]); setPreview(null);
    try {
      const r = await fetch(PREVIEW_SQL_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql }),
      });
      const data = await r.json();
      if (!data.ok) { setErrors(data.errors || ["Bilinmeyen hata"]); return; }
      setPreview({ columns: data.columns || [], rows: data.rows || [], row_count: data.row_count || 0 });
    } catch (e) {
      setErrors([String(e.message || e)]);
    } finally {
      setBusy(false);
    }
  };

  const canSave = sql.trim() && preview && preview.columns.length > 0;
  const save = () => {
    onSave({
      alias: isEdit ? existing.alias : makeAlias(alias || "sql_tablo", existingAliases),
      sql: sql.trim(),
      columns: preview.columns,
      routing,
      refresh: routing === "cached" ? refresh : null,
    });
  };

  return (
    <Modal title={isEdit ? "Manuel SQL Tablo — Düzenle" : "Manuel SQL Tablo"} size="lg" onClose={onClose} footer={
      <>
        <button className="ts-btn" onClick={onClose}>Vazgeç</button>
        <button className="ts-btn ts-btn--primary" disabled={!canSave} onClick={save}>{isEdit ? "Kaydet" : "Tablo Ekle"}</button>
      </>
    }>
      <label className="hz-field">Tablo adı (alias)
        <input type="text" value={alias} placeholder="ör. gunluk_pozisyon"
          disabled={isEdit}
          title={isEdit ? "Düzenlemede tablo adı değiştirilemez (referanslar bozulmasın)" : undefined}
          onChange={(e) => setAlias(e.target.value)} />
      </label>
      <label className="hz-field">SQL (SELECT / WITH)
        <textarea className="hz-sql-textarea" rows={8} value={sql}
          placeholder="SELECT ... FROM ..."
          onChange={(e) => { setSql(e.target.value); setPreview(null); }} />
      </label>
      <div className="hz-row" style={{ gap: 8, alignItems: "center" }}>
        <button type="button" className="ts-btn ts-btn--primary"
          disabled={busy || !sql.trim()} onClick={runPreview}>
          {busy
            ? <><Loader2 size={13} className="ts-spin" /> Çalıştırılıyor…</>
            : <><Eye size={13} /> Önizle / Doğrula</>}
        </button>
        {preview && <span className="hz-muted">{preview.row_count} satır · {preview.columns.length} kolon</span>}
      </div>
      {errors.length > 0 && (
        <div className="hz-error" style={{ marginTop: 8 }}>{errors.join(" · ")}</div>
      )}
      {preview && preview.columns.length > 0 && (
        <div className="hz-row" style={{ flexWrap: "wrap", gap: 4, marginTop: 6 }}>
          {preview.columns.map((c) => <span key={c} className="hz-col-concept">{c}</span>)}
        </div>
      )}
      <label className="hz-field">Saklama
        <select value={routing} onChange={(e) => setRouting(e.target.value)}>
          <option value="cached">cached (S3 parquet'e materialise — cron'lanabilir)</option>
          <option value="lazy">lazy (büyük tablo — cron yok, on-demand)</option>
        </select>
      </label>
      {routing === "cached"
        ? <RefreshFields value={refresh} onChange={setRefresh} />
        : <p className="hz-muted">Lazy tablolara cron bağlanamaz — küçültürsen (aggregate/filtre) cached olur ve cron'lanabilir.</p>}
    </Modal>
  );
}

function SourcesSidebar({
  scope, onOpenDocs, libraryBlocks, chat,
  hiddenAliases, onToggleVisibility,
  goingToSunum, onGoToSunum, onUpload, onAddSql, onEditSql,
}) {
  // Phase 11.hazirlik-polish: sidebar shows ONLY what's in MY basket
  // (no longer the full DOMAINS tree). Split into Tablolar + Bloklar
  // with per-group search inputs, mirroring the Keşif Sepet pattern.
  const [tableSearch, setTableSearch] = useState("");
  const [blockSearch, setBlockSearch] = useState("");

  // Build a flat list of basket tables with the catalog row (for the eye
  // icon → docs) attached. Derived items have no table_ref → skip eye.
  const tableById = useMemo(() => {
    const map = {};
    for (const d of (DOMAINS || [])) {
      for (const t of (d.tables || [])) map[t.id] = t;
    }
    return map;
  }, []);
  const tableItems = useMemo(() => {
    return (scope.basket || [])
      .filter((b) => b.table_ref != null || b.sql != null)
      .map((b) => {
        if (b.sql != null) {
          return {
            alias: b.alias, tid: b.alias, schema: "SQL", name: b.alias,
            catalog: null, isSql: true,
          };
        }
        const tid = tableId(b.table_ref);
        return {
          alias: b.alias,
          tid,
          schema: b.table_ref.schema,
          name: b.table_ref.name,
          catalog: tableById[tid] || null,
        };
      });
  }, [scope.basket, tableById]);
  const derivedItems = useMemo(
    () => (scope.basket || []).filter((b) => b.derivation != null),
    [scope.basket],
  );

  const tablesFiltered = useMemo(() => {
    const q = tableSearch.trim().toLowerCase();
    if (!q) return tableItems;
    return tableItems.filter((it) =>
      it.tid.toLowerCase().includes(q)
      || (it.alias || "").toLowerCase().includes(q)
    );
  }, [tableItems, tableSearch]);
  const blocksFiltered = useMemo(() => {
    const q = blockSearch.trim().toLowerCase();
    const all = libraryBlocks || [];
    if (!q) return all;
    return all.filter((b) =>
      (b.name || "").toLowerCase().includes(q)
      || (b.block_type || "").toLowerCase().includes(q)
      || (b.tags || []).join(" ").toLowerCase().includes(q)
    );
  }, [libraryBlocks, blockSearch]);

  const hasNoBasket = tableItems.length === 0 && derivedItems.length === 0
    && (libraryBlocks || []).length === 0;

  return (
    <aside className="editor-sidebar hz-sidebar">
      <div className="sidebar-inner">
        <div className="sidebar-section sidebar-section--sources ts-scroll">
          <div className="sidebar-label">
            <span className="sidebar-label-icon"><Database size={12} /></span>
            <span>Veri Kaynakları</span>
            <button
              type="button"
              className="back-to-kesif"
              onClick={() => {
                // Hazırlık: .../presentations/hazirlik/<pid>; Keşif workbench:
                // .../presentations/atolye/kesif. Pass ?pid=<pid> so Keşif
                // RESUMES this presentation's basket (not a fresh draft) —
                // otherwise the basket looks empty on return. Swap the trailing
                // segments so the URL survives reverse-proxy SCRIPT_NAME prefixes
                // (same pathname-derivation approach as lib/api.js).
                const path = window.location.pathname.replace(/\/$/, "");
                const m = path.match(/\/hazirlik\/([^/]+)$/);
                const kesif = path.replace(/\/hazirlik\/[^/]+$/, "/atolye/kesif");
                window.location.href = m
                  ? `${kesif}?pid=${encodeURIComponent(m[1])}`
                  : kesif;
              }}
              title="Keşif ekranına dön — sepeti düzenle / tablo ekle"
            >
              <ArrowLeft size={12} strokeWidth={2} />
              <span>Keşife Dön</span>
            </button>
          </div>

          {/* Faz C — add datasets directly in Hazırlık. */}
          <div className="hz-add-row">
            <button
              type="button"
              className="ts-btn hz-add-btn"
              onClick={() => onAddSql && onAddSql()}
              title="Serbest SQL yazıp bir tablo (dataset) oluştur"
            >
              <Database size={13} /> Manuel SQL Tablo
            </button>
            <button
              type="button"
              className="ts-btn hz-add-btn"
              onClick={() => onUpload && onUpload()}
              title="Excel/CSV yükle — yüklenen sayfa bir dataset olur"
            >
              <Upload size={13} /> Veri Yükle (Excel)
            </button>
          </div>

          {hasNoBasket && (
            <div className="hz-basket-empty">
              Sepetin boş. Önce Keşif'ten tablo veya blok ekle, sonra
              buradan ER düzenle.
            </div>
          )}

          {/* ── Tablolar ─────────────────────────────────────────── */}
          {(tableItems.length > 0 || derivedItems.length > 0) && (
            <div className="hz-basket-group">
              <div className="hz-basket-group__title">
                <Table2 size={11} strokeWidth={2} />
                <span>Tablolar</span>
                <span className="hz-basket-group__count">
                  {tableItems.length + derivedItems.length}
                </span>
              </div>
              <input
                type="text"
                className="hz-basket-search"
                placeholder="Tablo ara…"
                value={tableSearch}
                onChange={(e) => setTableSearch(e.target.value)}
              />
              <div className="hz-basket-list">
                {tablesFiltered.map((it) => {
                  const hidden = hiddenAliases?.has(it.alias) || false;
                  return (
                    <div
                      key={it.alias}
                      className={`hz-basket-row sources-table-wrap is-active${hidden ? " is-hidden" : ""}`}
                    >
                      <button
                        type="button"
                        className="hz-basket-row__main sources-table"
                        onClick={() => onToggleVisibility && onToggleVisibility(it.alias)}
                        title={hidden ? "Görünür yap" : "Görünümden gizle"}
                      >
                        <span className="hz-basket-row__viz" aria-hidden>
                          {hidden ? <EyeOff size={12} strokeWidth={1.8} /> : <Eye size={12} strokeWidth={1.8} />}
                        </span>
                        <div className="sources-table-info">
                          <div className="sources-table-name">{it.name}</div>
                          <div className="sources-table-desc">
                            {it.schema}{it.catalog?.desc ? ` · ${it.catalog.desc}` : ""}
                          </div>
                        </div>
                      </button>
                      {it.catalog && (
                        <button
                          type="button"
                          className="sources-table-eye"
                          onClick={(e) => { e.stopPropagation(); onOpenDocs && onOpenDocs(it.catalog); }}
                          title="Tablo dökümanını göster"
                        >
                          <Info size={12} strokeWidth={1.8} />
                        </button>
                      )}
                      {it.isSql && (
                        <button
                          type="button"
                          className="sources-table-eye"
                          onClick={(e) => { e.stopPropagation(); onEditSql && onEditSql(it.alias); }}
                          title="SQL kaynağını düzenle"
                        >
                          <Code2 size={12} strokeWidth={1.8} />
                        </button>
                      )}
                    </div>
                  );
                })}
                {derivedItems.map((b) => {
                  const hidden = hiddenAliases?.has(b.alias) || false;
                  return (
                    <div
                      key={b.alias}
                      className={`hz-basket-row sources-table-wrap is-active is-derived${hidden ? " is-hidden" : ""}`}
                    >
                      <button
                        type="button"
                        className="hz-basket-row__main sources-table"
                        onClick={() => onToggleVisibility && onToggleVisibility(b.alias)}
                        title={hidden ? "Görünür yap" : "Görünümden gizle"}
                      >
                        <span className="hz-basket-row__viz" aria-hidden>
                          {hidden ? <EyeOff size={12} strokeWidth={1.8} /> : <Eye size={12} strokeWidth={1.8} />}
                        </span>
                        <div className="sources-table-info">
                          <div className="sources-table-name">{b.alias}</div>
                          <div className="sources-table-desc">
                            {b.derivation?.kind === "aggregate" ? "agregat" : "hesaplama"}
                            {b.derivation?.source_alias ? ` · ${b.derivation.source_alias}` : ""}
                          </div>
                        </div>
                      </button>
                    </div>
                  );
                })}
                {tablesFiltered.length === 0 && derivedItems.length === 0 && tableSearch && (
                  <div className="hz-basket-empty hz-basket-empty--mini">
                    "{tableSearch}" ile eşleşen tablo yok.
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ── Bloklar ──────────────────────────────────────────── */}
          {(libraryBlocks || []).length > 0 && (
            <div className="hz-basket-group">
              <div className="hz-basket-group__title">
                <Tag size={11} strokeWidth={2} />
                <span>Bloklar</span>
                <span className="hz-basket-group__count">
                  {(libraryBlocks || []).length}
                </span>
              </div>
              <input
                type="text"
                className="hz-basket-search"
                placeholder="Blok ara…"
                value={blockSearch}
                onChange={(e) => setBlockSearch(e.target.value)}
              />
              <div className="hz-basket-list">
                {blocksFiltered.map((b) => (
                  <div key={b.library_id} className="hz-basket-row hz-basket-row--block">
                    <div className="hz-basket-row__main">
                      <div className="sources-table-info">
                        <div className="sources-table-name">{b.name}</div>
                        <div className="sources-table-desc">
                          {b.block_type || "blok"}{b.owner_id ? ` · ${b.owner_id}` : ""}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
                {blocksFiltered.length === 0 && blockSearch && (
                  <div className="hz-basket-empty hz-basket-empty--mini">
                    "{blockSearch}" ile eşleşen blok yok.
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="sidebar-section sidebar-section--chat">
          <ChatPanel {...chat} />
        </div>

        {/* Phase 11.hazirlik-polish: "Sunum'a geç" relocated from a
            top-bar (removed) to a sticky CTA below the chat — same
            visual rhythm as Keşif's "Hazırlık'a geç" bottom CTA. */}
        <div className="hz-sidebar-cta">
          <button
            type="button"
            className="ts-btn ts-btn--primary hz-sidebar-cta__btn"
            disabled={goingToSunum}
            onClick={onGoToSunum}
          >
            {goingToSunum
              ? <><Loader2 size={13} className="ts-spin" /> Hazırlanıyor…</>
              : <>Sunum'a geç <ArrowRight size={14} /></>}
          </button>
        </div>
      </div>
    </aside>
  );
}

// ── Stage-2 LLM scope-refinement chat ────────────────────────────────────────

function ChatPanel({ history, busy, error, draft, onDraftChange, onSend, onApply, onDismiss, applyingId }) {
  const taRef = useRef(null);
  const listRef = useRef(null);

  // Auto-scroll on new messages.
  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [history, busy]);

  const submit = () => {
    const v = (draft || "").trim();
    if (!v || busy) return;
    onSend(v);
  };
  const onKey = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); submit(); }
  };

  return (
    <div className="chat-box">
      <div className="chat-box-header">
        <MessageSquare size={11} strokeWidth={2} />
        <span>Scope Asistanı</span>
      </div>
      <div className="chat-messages ts-scroll" ref={listRef}>
        {history.length === 0 && !busy && (
          <div className="chat-empty">
            Scope hakkında konuş — örn. "Q4 2025'e kilitle", "TL'ye filtrele",
            "şube bazında topla".
          </div>
        )}
        {history.map((m, i) => (
          <ChatTurn key={i} turn={m} onApply={onApply} onDismiss={onDismiss} applyingId={applyingId} />
        ))}
        {busy && <div className="chat-msg chat-msg--loading">Düşünüyor…</div>}
        {error && <div className="chat-msg chat-msg--assistant chat-msg--error">{error}</div>}
      </div>
      <textarea
        ref={taRef}
        className="chat-input"
        rows={3}
        value={draft || ""}
        onChange={(e) => onDraftChange(e.target.value)}
        onKeyDown={onKey}
        placeholder="Scope hakkında soru sor…"
        disabled={busy}
      />
      <div className="chat-footer">
        <span className="chat-footer-hint">⌘/Ctrl + Enter ile gönder</span>
        <button type="button" className="btn-primary" onClick={submit} disabled={busy || !(draft || "").trim()}>
          {busy
            ? <><Loader2 size={12} className="ts-spin" /><span>İşleniyor…</span></>
            : <><Send size={12} strokeWidth={2} /><span>Gönder</span></>}
        </button>
      </div>
    </div>
  );
}

function ChatTurn({ turn, onApply, onDismiss, applyingId }) {
  if (turn.role === "user") {
    return (
      <div className="chat-turn chat-turn--user">
        <div className="chat-bubble">{turn.content}</div>
      </div>
    );
  }
  // assistant turn — explanation + suggestion cards
  const suggestions = turn.suggestions || [];
  return (
    <div className="chat-turn chat-turn--assistant">
      {turn.explanation && <div className="chat-bubble">{turn.explanation}</div>}
      {suggestions.map((s) => (
        <SuggestionCard key={s.id} suggestion={s}
          onApply={() => onApply(turn._turnId, s)}
          onDismiss={() => onDismiss(turn._turnId, s.id)}
          busy={applyingId === s.id}
        />
      ))}
    </div>
  );
}

const KIND_LABEL = {
  pin_filter: "Pin filter",
  add_filter: "Filtre ekle",
  add_projection_column: "Kolon ekle",
  confirm_join: "Join onayla",
  create_aggregate: "Agregat tablo",
  create_calculation: "Hesaplanmış tablo",
};

// Human-friendly Turkish description for a suggestion card. Replaces the
// previous SQL-ish "src ⋈ src2 → alias · COL = expr" format with prose:
// "İki tablo birleşecek (X ile Y), …. Sonuç tabloda 2 kolon olacak: A, B."
function summariseSuggestion(s) {
  switch (s.kind) {
    case "pin_filter":
      return `'${s.filter_id}' interactive filtresini scope'a sabitle (kullanıcı Sunum'da değiştiremesin).`;

    case "add_filter": {
      const mode = s.mode === "interactive"
        ? "kullanıcının değiştirebileceği bir interactive filtre"
        : "sabitlenmiş bir pinned filtre";
      const val = s.op === "between"
        ? `${s.from} – ${s.to} arası`
        : (s.values ? s.values.join(", ") : String(s.value ?? ""));
      return `'${s.concept}' alanında ${val} değeriyle ${mode} oluştur.`;
    }

    case "add_projection_column":
      return `'${s.alias}' tablosuna '${s.column}' kolonunu ekle (projection'a katılsın).`;

    case "confirm_join": {
      const kind = s.kind_of_join === "lookup" ? "lookup" : (s.kind_of_join || "inner");
      return `'${s.left_alias}' ve '${s.right_alias}' tablolarını '${s.left_column}' = '${s.right_column}' üzerinden ${kind} join ile birleştir.`;
    }

    case "create_aggregate": {
      const grp = (s.group_by || []).join(", ") || "(gruplama yok)";
      const meas = (s.measures || []).map((m) => `${m.fn}(${m.column})`).join(", ") || "(measure yok)";
      const cols = [
        ...(s.group_by || []),
        ...(s.measures || []).map((m) => m.as || `${m.fn.toUpperCase()}_${m.column}`),
      ];
      const colsText = cols.length
        ? ` Sonuç tabloda ${cols.length} kolon olacak: ${cols.join(", ")}.`
        : "";
      return (
        `'${s.source_alias}' tablosunu ${grp} bazında agregatla, ${meas} hesapla → ` +
        `'${s.new_alias}' adında yeni bir türetilmiş tablo.${colsText}`
      );
    }

    case "create_calculation": {
      const srcs = s.source_aliases || [];
      const join_keys = s.join_keys || [];
      const cols = s.columns || [];
      let head;
      if (srcs.length === 1) {
        head = `'${srcs[0]}' tablosu üzerinden hesaplama yapılacak`;
      } else if (join_keys.length) {
        const jk = join_keys[0];
        const extra = join_keys.length > 1 ? ` (+${join_keys.length - 1} join daha)` : "";
        head = (
          `'${srcs.join("', '")}' tabloları '${jk.left_alias}.${jk.left_column}' = ` +
          `'${jk.right_alias}.${jk.right_column}' üzerinden birleşecek${extra}`
        );
      } else {
        head = `'${srcs.join("', '")}' tabloları kullanılacak`;
      }
      const colsDesc = cols.map((c, i) => {
        const ord = ["İlk", "İkinci", "Üçüncü", "Dördüncü", "Beşinci"][i] || `${i + 1}.`;
        return `${ord} kolon '${c.name}' = ${c.expr}`;
      }).join("; ");
      const tail = cols.length
        ? ` Sonuç '${s.new_alias}' tablosunda ${cols.length} kolon olacak — ${colsDesc}.`
        : "";
      return `${head}.${tail} Onaylıyor musun?`;
    }

    default:
      return JSON.stringify(s).slice(0, 100);
  }
}

function SuggestionCard({ suggestion, onApply, onDismiss, busy }) {
  return (
    <div className="hz-sugg">
      <div className="hz-sugg-head">
        <span className="hz-sugg-kind">{KIND_LABEL[suggestion.kind] || suggestion.kind}</span>
      </div>
      <div className="hz-sugg-body">{summariseSuggestion(suggestion)}</div>
      {suggestion.rationale && <div className="hz-sugg-rationale">{suggestion.rationale}</div>}
      <div className="hz-sugg-actions">
        <button className="ts-btn ts-btn--sm ts-btn--primary" disabled={busy} onClick={onApply}>
          {busy ? "Uygulanıyor…" : "Apply"}
        </button>
        <button className="ts-btn ts-btn--sm" disabled={busy} onClick={onDismiss}>Reddet</button>
      </div>
    </div>
  );
}

// ── AG Grid preview drawer (resizable) ───────────────────────────────────────

// AG Grid custom header: column name + small concept chip when the column
// is concept-bound (same green chip as the ER node card). Falls back to the
// plain column name when there's no concept.
// Custom AG Grid header: column name + concept chip + the sort arrow and
// filter-menu icon that the default header normally provides. We replace
// the default header (to inject the chip) so we have to re-implement the
// sort/menu affordances ourselves via IHeaderParams.
function ConceptHeader(props) {
  const {
    displayName, concept,
    column, showColumnMenu,
    enableSorting, enableMenu, progressSort,
  } = props;

  // Track sort + filter active state so we can render the right indicators
  // and the menu icon highlight.
  const [sort, setSort] = useState(column?.getSort?.() || null);
  const [filterActive, setFilterActive] = useState(!!column?.isFilterActive?.());
  useEffect(() => {
    if (!column) return;
    const onSort = () => setSort(column.getSort());
    const onFilter = () => setFilterActive(column.isFilterActive());
    column.addEventListener("sortChanged", onSort);
    column.addEventListener("filterChanged", onFilter);
    return () => {
      column.removeEventListener("sortChanged", onSort);
      column.removeEventListener("filterChanged", onFilter);
    };
  }, [column]);

  const onLabelClick = (e) => {
    if (!enableSorting || !progressSort) return;
    progressSort(e.shiftKey);
  };
  const menuRef = useRef(null);
  const onMenuClick = (e) => {
    e.stopPropagation();
    if (showColumnMenu && menuRef.current) showColumnMenu(menuRef.current);
  };

  return (
    <div className="hz-grid-header">
      <div className="hz-grid-header-label" onClick={onLabelClick}>
        <span className="hz-grid-header-name">{displayName}</span>
        {concept && <span className="hz-col-concept hz-grid-header-concept">{concept}</span>}
        {sort === "asc"  && <span className="hz-grid-header-sort">▲</span>}
        {sort === "desc" && <span className="hz-grid-header-sort">▼</span>}
      </div>
      {enableMenu && (
        <span
          ref={menuRef}
          className={`hz-grid-header-menu${filterActive ? " is-active" : ""}`}
          onClick={onMenuClick}
          title={filterActive ? "Aktif filtre — düzenle" : "Filtre / kolon menüsü"}
        >
          ☰
        </span>
      )}
    </div>
  );
}

function PreviewDrawer({ preview, loading, height, onResizeStart, onClose, onSaveFilters, onSaveAsTable, onGridReady, savedGridState, previewLabel, onSaveFilterPanel, onFetchDistinct, existingFilters }) {
  const apiRef = useRef(null);
  const filterSaveRef = useRef(null);
  const [tab, setTab] = useState("data");

  const handleReady = (p) => {
    apiRef.current = p.api;
    if (onGridReady) onGridReady(p);
    if (savedGridState) {
      try {
        if (savedGridState.columnState) p.api.applyColumnState({ state: savedGridState.columnState, applyOrder: true });
        if (savedGridState.filterModel) p.api.setFilterModel(savedGridState.filterModel);
      } catch (e) { /* ignore restore errors */ }
    }
  };

  // Auto-size columns to their content the first time rows render — gives
  // narrow columns for compact values (currency codes) and wider columns
  // for long strings (branch names) without forcing every cell to flex
  // across the full drawer width. Capped at maxWidth (320) so a stray long
  // value doesn't blow up the layout.
  const handleFirstDataRendered = () => {
    if (!apiRef.current) return;
    try {
      if (apiRef.current.autoSizeAllColumns) {
        apiRef.current.autoSizeAllColumns(false);
      } else if (apiRef.current.autoSizeColumns) {
        const allIds = (apiRef.current.getColumns?.() || []).map((c) => c.getColId());
        apiRef.current.autoSizeColumns(allIds, false);
      }
    } catch (e) { /* community build may lack this API — non-fatal */ }
  };

  // Per-alias concept lookup so the header renderer can show the same chip
  // we use on the ER node card.
  const conceptByCol = useMemo(() => {
    const m = {};
    (COLS_BY_ALIAS[preview?.alias] || []).forEach((c) => { m[c.name] = c.concept || null; });
    return m;
  }, [preview?.alias]);

  const colDefs = useMemo(() => (preview?.data_columns || []).map((c) => ({
    field: c, headerName: c,
    sortable: true, resizable: true, filter: true,
    minWidth: 80, maxWidth: 320,
    enableRowGroup: true, enablePivot: true, enableValue: true,
    headerComponent: ConceptHeader,
    headerComponentParams: { concept: conceptByCol[c] || null },
  })), [preview?.data_columns, conceptByCol]);

  const rowData = useMemo(() => {
    if (!preview?.rows) return [];
    const cols = preview.data_columns || [];
    return preview.rows.map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])));
  }, [preview]);

  const isDerived = !!preview?.derived;
  return (
    <div className="hz-preview" style={{ height }}>
      <div className="hz-preview-resize" onMouseDown={onResizeStart} title="Sürükle: yükseklik" />
      <div className="hz-preview-head">
        <span>
          <Database size={14} /> Önizleme{previewLabel ? ` · ${previewLabel}` : ""}
          {isDerived ? " · türetilmiş (örnek)" : ""}
          {preview && preview.row_count != null ? ` (${preview.row_count} satır)` : ""}
        </span>
        <div className="hz-preview-actions">
          {!isDerived && tab === "filter" && (
            <button className="ts-btn ts-btn--sm" disabled={!preview || preview.error}
                    onClick={() => filterSaveRef.current && filterSaveRef.current()}
                    title="Filtreleri scope'a yaz (boyut yeniden hesaplanır)">
              <Save size={13} /> Filtreyi kaydet
            </button>
          )}
          {!isDerived && tab === "data" && (
            <button className="ts-btn ts-btn--sm" disabled={!preview || preview.error}
                    onClick={onSaveAsTable}
                    title="Gruplama/aggregation'ı yeni bir tablo olarak kaydet">
              <Database size={13} /> Tablo olarak kaydet
            </button>
          )}
          <button className="hz-icon-btn" onClick={onClose}><X size={15} /></button>
        </div>
      </div>
      <div className="hz-preview-body">
        {loading && <p className="hz-muted" style={{ padding: 10 }}>Yükleniyor…</p>}
        {!loading && preview && preview.error && <p className="hz-error" style={{ margin: 10 }}>{preview.error}</p>}
        {!loading && preview && !preview.error && (
          <div className="hz-preview-inner">
            {isDerived && (
              <p className="hz-muted" style={{ padding: "6px 10px", fontSize: 11 }}>
                Türetilmiş tablo · kaynak tablonun örneği üzerinde hesaplandı. Sunum tam veri üzerinde yeniden hesaplayacak.
              </p>
            )}
            {!isDerived && (
              <div className="hz-preview-tabs">
                <button type="button" className={tab === "data" ? "on" : ""} onClick={() => setTab("data")}>Veri</button>
                <button type="button" className={tab === "filter" ? "on" : ""} onClick={() => setTab("filter")}>Filtreleme</button>
              </div>
            )}
            <div className="hz-preview-pane">
              {(isDerived || tab === "data") ? (
                <div className="ag-theme-alpine-dark" style={{ width: "100%", height: "100%" }}>
                  <AgGridReact
                    columnDefs={colDefs} rowData={rowData} animateRows
                    onGridReady={handleReady}
                    onFirstDataRendered={handleFirstDataRendered}
                    sideBar={{ toolPanels: ["columns", "filters"] }}
                    rowGroupPanelShow="always"
                    pivotPanelShow="always"
                    headerHeight={36}
                  />
                </div>
              ) : (
                <FilterPanel
                  alias={preview.alias}
                  columns={COLS_BY_ALIAS[preview.alias] || []}
                  existing={existingFilters}
                  saveRef={filterSaveRef}
                  onSave={(specs) => onSaveFilterPanel && onSaveFilterPanel(preview.alias, specs)}
                  onFetchDistinct={(column) => onFetchDistinct(preview.alias, column)}
                />
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Client-side aggregation for derived-table previews. Mirrors the shape of
// scope/fetch.compile_aggregate_sql so what the user sees in the drawer matches
// what Sunum will compute over the full pull (within sampling noise).
function aggregateLocal(srcData, derivation) {
  const { group_by = [], measures = [] } = derivation || {};
  const dataCols = srcData.data_columns || [];
  const colIdx = Object.fromEntries(dataCols.map((c, i) => [c, i]));
  const groups = new Map();
  for (const row of (srcData.rows || [])) {
    const keyParts = group_by.map((c) => row[colIdx[c]]);
    const key = JSON.stringify(keyParts);
    if (!groups.has(key)) groups.set(key, { keyParts, rows: [] });
    groups.get(key).rows.push(row);
  }
  const nums = (vals) => vals.filter((v) => typeof v === "number" && isFinite(v));
  const reduce = (fn, vals) => {
    const ns = nums(vals);
    switch (fn) {
      case "sum":   return ns.reduce((a, b) => a + b, 0);
      case "avg":   return ns.length ? ns.reduce((a, b) => a + b, 0) / ns.length : null;
      case "min":   return ns.length ? Math.min(...ns) : null;
      case "max":   return ns.length ? Math.max(...ns) : null;
      case "count": return vals.length;
      case "count_distinct": return new Set(vals).size;
      default: return null;
    }
  };
  const outRows = [];
  for (const { keyParts, rows } of groups.values()) {
    const out = [...keyParts];
    for (const m of measures) {
      out.push(reduce(m.fn, rows.map((r) => r[colIdx[m.column]])));
    }
    outRows.push(out);
  }
  return {
    columns: [
      ...group_by.map((c) => ({ name: c })),
      ...measures.map((m) => ({ name: m.as })),
    ],
    data_columns: [...group_by, ...measures.map((m) => m.as)],
    rows: outRows,
    row_count: outRows.length,
  };
}

// AG Grid filter model → scope filters (concept if column bound, else raw).
// Handles AG Grid Enterprise set filter (default for categorical) + community
// text/number/date filters.
function agModelToFilters(model, alias, colMeta) {
  const pinned = [], raw = [];
  for (const [col, m] of Object.entries(model || {})) {
    const concept = colMeta[col]?.concept || null;
    const base = concept
      ? { id: `pf_${concept}_${rid()}`.slice(0, 58), concept, applies_to: [alias] }
      : { id: `rf_${col.toLowerCase()}_${rid()}`.slice(0, 58), alias, column: col };

    if (m.filterType === "set" && Array.isArray(m.values)) {
      base.op = "in"; base.values = m.values;
    } else if (m.type === "inRange") {
      base.op = "between";
      base.from = m.dateFrom ?? m.filter;
      base.to = m.dateTo ?? m.filterTo;
    } else if (m.type === "equals" && (m.filter != null || m.dateFrom != null)) {
      const v = m.filter ?? m.dateFrom;
      if (concept) { base.op = "in"; base.values = [v]; }
      else { base.op = "eq"; base.value = v; }
    } else if (m.filter != null) {            // text 'contains'/'startsWith'/… etc.
      if (concept) { base.op = "in"; base.values = [m.filter]; }
      else { base.op = "eq"; base.value = m.filter; }
    } else {
      continue;   // unrecognised filter shape (no value) — skip
    }

    if (concept) pinned.push(base); else raw.push(base);
  }
  return { pinned, raw };
}

// ── App ──────────────────────────────────────────────────────────────────────

function App() {
  const [scope, setScope] = useState(DATA.scope);
  const [joinModal, setJoinModal] = useState(null);
  const [docsTable, setDocsTable] = useState(null);   // 8.c — Eye icon → schema modal
  const [uploadOpen, setUploadOpen] = useState(false); // Polish-4 — Veri Yükle
  const [sqlModalOpen, setSqlModalOpen] = useState(false); // Faz C — Manuel SQL Tablo
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [drawerH, setDrawerH] = useState(260);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [toast, setToast] = useState(null);
  const [gridStateByAlias, setGridStateByAlias] = useState({}); // per-alias AG Grid state (filters + columns), restored on re-open
  const gridApiRef = useRef(null);

  const [nodes, setNodes, onNodesChange] = useNodesStateCompat(() => initialNodes(DATA.scope));
  // Phase 11.hazirlik-polish-2: alias visibility on the canvas. The
  // sidebar's "Tablolar" rows toggle entries in this set; the nodes
  // reconciliation effect applies it to each node's `hidden` flag.
  // React Flow drops edges connected to hidden nodes automatically.
  const [hiddenAliases, setHiddenAliases] = useState(() => new Set());
  const toggleAliasVisibility = useCallback((alias) => {
    setHiddenAliases((s) => {
      const n = new Set(s);
      if (n.has(alias)) n.delete(alias); else n.add(alias);
      return n;
    });
  }, []);
  const edges = useMemo(() => buildEdges(scope), [scope]);

  // Madde 4 — ask the server to refine post-scope sizes with EXPLAIN PLAN
  // cardinality, then fold the results into the node badges. The estimate runs
  // in the background (dedicated Oracle connection), so the endpoint returns
  // ready hits immediately and reports the rest as `pending`; we re-poll a few
  // times until they land. `scopeArg` is the scope to size (not React state) so
  // a poll measures the snapshot it was kicked off for. attempt caps the poll.
  const refineSizes = useCallback((scopeArg, attempt = 0) => {
    fetch(REFINE_SIZES_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: scopeArg }),
    }).then((r) => r.json()).then((data) => {
      if (!data || !data.ok) return;
      if (data.estimates && Object.keys(data.estimates).length) {
        setScope((cur) => applySizeEstimates(cur, data.estimates));
      }
      if (data.pending && data.pending.length && attempt < 6) {
        setTimeout(() => refineSizes(scopeArg, attempt + 1), 1800);
      }
    }).catch(() => { /* best effort — partition estimate stays */ });
  }, []);

  // Refine sizes for whatever filters/tables are already on the canvas at load.
  useEffect(() => { refineSizes(DATA.scope); }, [refineSizes]);

  // Auto-save the draft scope to the session manifest 500ms after the last
  // mutation. Reload picks up where the user left off without going through
  // "Sunum'a geç". Initial DATA.scope mount is skipped (no-op POST).
  const isInitialScopeRef = useRef(true);
  useEffect(() => {
    if (isInitialScopeRef.current) { isInitialScopeRef.current = false; return; }
    const t = setTimeout(() => {
      fetch(SAVE_DRAFT_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope }),
      }).catch(() => { /* best effort */ });
    }, 500);
    return () => clearTimeout(t);
  }, [scope]);

  // Reconcile React Flow nodes with the basket:
  //   - update data for nodes that still match a basket alias
  //   - drop nodes whose alias was removed from the basket
  //   - append nodes for basket items added without a UI mutation (e.g. the
  //     LLM `create_aggregate` suggestion adds a derived item directly).
  // Positions are preserved for known nodes; new nodes get a grid layout.
  useEffect(() => {
    setNodes((nds) => {
      const aliasesInScope = new Set(scope.basket.map((b) => b.alias));
      const kept = nds
        .filter((n) => aliasesInScope.has(n.id))
        .map((n) => {
          const item = scope.basket.find((b) => b.alias === n.id);
          // Apply current visibility on every reconcile — hiddenAliases
          // mutates independently of `scope`, so this effect picks it up
          // via the dependency list below.
          const hidden = hiddenAliases.has(n.id);
          return item
            ? { ...n, hidden, data: enrichNodeData(item, scope) }
            : { ...n, hidden };
        });
      const known = new Set(kept.map((n) => n.id));
      const added = [];
      scope.basket.forEach((item, i) => {
        if (known.has(item.alias)) return;
        // Populate COLS_BY_ALIAS for newly-added derived items so the node
        // can display them in the "Diğer Kolonlar" handle. Two derivation
        // kinds:
        //   aggregate  — group_by + measures
        //   calculated — columns (each with a free-form SQL expr)
        if (item.derivation && !COLS_BY_ALIAS[item.alias]) {
          const d = item.derivation;
          if (d.kind === "filter") {
            // Faz R1 — filter-node şemayı değiştirmez; kaynağın kolonlarını miras alır.
            COLS_BY_ALIAS[item.alias] = COLS_BY_ALIAS[d.source_alias] || [];
          } else if (d.kind === "calculated") {
            COLS_BY_ALIAS[item.alias] = (d.columns || []).map((c) => ({
              name: c.name, concept: null, join_key: false,
              expr: c.expr,
            }));
          } else {
            const srcCols = Object.fromEntries((COLS_BY_ALIAS[d.source_alias] || []).map((c) => [c.name, c]));
            COLS_BY_ALIAS[item.alias] = [
              ...(d.group_by || []).map((g) => ({ name: g, concept: srcCols[g]?.concept || null, join_key: true })),
              ...(d.measures || []).map((m) => ({ name: m.as, concept: null, join_key: false })),
            ];
          }
        }
        const seq = kept.length + added.length;
        added.push({
          id: item.alias, type: "tableNode",
          position: { x: 100 + (seq % 3) * 340, y: 100 + Math.floor(seq / 3) * 240 },
          hidden: hiddenAliases.has(item.alias),
          data: enrichNodeData(item, scope),
        });
      });
      return [...kept, ...added];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, hiddenAliases]);

  // ── Stage-2 LLM chat (8.f) ─────────────────────────────────────────────────
  const [chatHistory, setChatHistory] = useState([]);
  const [chatBusy, setChatBusy] = useState(false);
  const [chatError, setChatError] = useState(null);
  const [chatDraft, setChatDraft] = useState("");
  const [applyingId, setApplyingId] = useState(null);

  const sendChat = useCallback(async (message) => {
    setChatError(null);
    setChatBusy(true);
    setChatDraft("");
    const userTurn = { role: "user", content: message };
    // Build the history payload from the *previous* turns (exclude the new user turn).
    const historyPayload = chatHistory.map((t) => (
      t.role === "user"
        ? { role: "user", content: t.content }
        : { role: "assistant", content: t.explanation || "" }
    ));
    setChatHistory((h) => [...h, userTurn]);
    try {
      const r = await fetch(CHAT_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, message, history: historyPayload }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
      const turnId = `t_${rid()}`;
      setChatHistory((h) => [...h, {
        role: "assistant", _turnId: turnId,
        explanation: data.explanation || "",
        suggestions: data.suggestions || [],
      }]);
    } catch (e) {
      setChatError(String(e.message || e));
    } finally {
      setChatBusy(false);
    }
  }, [scope, chatHistory]);

  const dismissSuggestion = useCallback((turnId, suggestionId) => {
    setChatHistory((h) => h.map((t) => {
      if (t._turnId !== turnId) return t;
      return { ...t, suggestions: (t.suggestions || []).filter((s) => s.id !== suggestionId) };
    }));
  }, []);

  // Update an alias's projection from the current AG Grid column-visibility
  // state — called from saveFilters() ("Görünümü kaydet"). Pulls visible
  // columns from the live grid api and posts to /scope/projection-update.
  // Returns true on success, false on validation failure (toast already set).
  const updateProjectionFromGrid = useCallback(async (alias) => {
    const api = gridApiRef.current;
    if (!api || !alias) return false;
    const item = scope.basket.find((b) => b.alias === alias);
    if (!item || item.derivation) return false;   // skip derived items
    // Visible columns = AG Grid columns minus hidden ones (auto-generated
    // row-grouping cols are filtered out by `getColDef().field` presence).
    const allCols = api.getAllGridColumns ? api.getAllGridColumns() : api.getColumns();
    const visible = (allCols || [])
      .filter((c) => c.isVisible() && c.getColDef().field)
      .map((c) => c.getColDef().field);
    if (visible.length === 0) return false;
    try {
      const r = await fetch(PROJECTION_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, alias, columns: visible, include_all: false }),
      });
      const data = await r.json();
      if (!data.ok) {
        let msg = data.error || (data.errors || []).join("; ");
        if (data.blocked_by_joins) {
          const cols = data.blocked_by_joins.map((b) => b.column).join(", ");
          msg = `${msg} (etkilenen kolonlar: ${cols})`;
        }
        setToast(`Projection güncellenemedi: ${msg}`);
        return false;
      }
      setScope(data.scope);
      return true;
    } catch (e) {
      setToast(`Projection hatası: ${e.message || e}`);
      return false;
    }
  }, [scope]);

  const overrideRouting = useCallback(async (alias, forced) => {
    try {
      const r = await fetch(ROUTING_OVERRIDE_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, alias, forced }),
      });
      const data = await r.json();
      if (!data.ok) {
        setToast(`Override reddedildi: ${data.error || "bilinmeyen hata"}`);
        return;
      }
      setScope(data.scope);
      setToast(`'${alias}' → ${forced}`);
    } catch (e) {
      setToast(`Hata: ${e.message || e}`);
    }
  }, [scope]);

  // Register the override + projection handlers on the module singleton so
  // every node re-render (driven by setNodes inside the [scope] effect) sees
  // the latest reference. NODE_HANDLERS itself is module-level so this is
  // one-shot per handler.
  useEffect(() => {
    NODE_HANDLERS.onOverrideRouting = overrideRouting;
    return () => { NODE_HANDLERS.onOverrideRouting = null; };
  }, [overrideRouting]);

  // Faz C — per-dataset cron/refresh. Pure-local: mutate basket[i].refresh and
  // let the debounced save-draft persist it; the dataset cron picks it up after
  // "Sunum'a geç" (build → SCOPE_STORE version).
  const [refreshAlias, setRefreshAlias] = useState(null);
  const editRefresh = useCallback((alias) => setRefreshAlias(alias), []);
  useEffect(() => {
    NODE_HANDLERS.onEditRefresh = editRefresh;
    return () => { NODE_HANDLERS.onEditRefresh = null; };
  }, [editRefresh]);
  const saveRefresh = useCallback((alias, refresh) => {
    setScope((s) => ({
      ...s,
      basket: (s.basket || []).map((b) => (b.alias === alias ? { ...b, refresh } : b)),
    }));
    setRefreshAlias(null);
    setToast(`'${alias}' yenileme güncellendi`);
  }, []);

  // Madde 8 — manuel SQL dataset kaynağını düzenle. Pure-local: basket[i].sql'i
  // güncelle; debounced save-draft persist eder, [scope] effect node'u (colCount)
  // yeniler. Routing user-decided olduğundan recompute-routing gerekmez.
  const [editSqlAlias, setEditSqlAlias] = useState(null);
  const saveSqlEdit = useCallback(({ alias, sql, columns, routing, refresh }) => {
    setScope((s) => ({
      ...s,
      basket: (s.basket || []).map((b) => (b.alias === alias ? {
        ...b,
        sql,
        projection: { ...(b.projection || {}), columns: columns || [], include_all: false },
        routing: { ...(b.routing || {}), decision: routing, decided_by: "user" },
        refresh: (routing === "cached" && refresh) ? refresh : undefined,
      } : b)),
    }));
    COLS_BY_ALIAS[alias] = (columns || []).map((c) => ({
      name: c, type: null, concept: null, join_key: false, lookup: null,
    }));
    setEditSqlAlias(null);
    setToast(`'${alias}' SQL güncellendi`);
  }, []);


  const applySuggestion = useCallback(async (turnId, suggestion) => {
    setApplyingId(suggestion.id);
    try {
      const r = await fetch(APPLY_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, suggestion }),
      });
      const data = await r.json();
      if (!data.ok) {
        setToast(`Uygulanamadı: ${data.error || (data.errors || []).join("; ")}`);
        return;
      }
      setScope(data.scope);
      setToast(`'${KIND_LABEL[suggestion.kind] || suggestion.kind}' uygulandı.`);
      dismissSuggestion(turnId, suggestion.id);
      if ((data.warnings || []).length) {
        setChatHistory((h) => [...h, {
          role: "assistant", _turnId: `t_${rid()}`,
          explanation: `Uygulandı, uyarı: ${data.warnings.join("; ")}`,
          suggestions: [],
        }]);
      }
    } catch (e) {
      setToast(`Hata: ${e.message || e}`);
    } finally {
      setApplyingId(null);
    }
  }, [scope, dismissSuggestion]);

  const captureGridState = () => {
    const api = gridApiRef.current;
    if (!api) return null;
    try {
      return {
        columnState: api.getColumnState ? api.getColumnState() : null,
        filterModel: api.getFilterModel ? api.getFilterModel() : null,
      };
    } catch { return null; }
  };

  const addJoin = useCallback((la, lc, ra, rc, kind = "lookup") => {
    setScope((s) => {
      const k = joinKey(la, lc, ra, rc);
      if (s.joins.some((j) => joinKey(j.left.alias, j.left.column, j.right.alias, j.right.column) === k)) return s;
      return { ...s, joins: [...s.joins, { id: `j_${la}_${ra}_${rid()}`, kind, left: { alias: la, column: lc }, right: { alias: ra, column: rc } }] };
    });
  }, []);

  const onConnect = useCallback((p) => {
    if (!p.source || !p.target || p.source === p.target) return;
    const lc = p.sourceHandle, rc = p.targetHandle;
    if (lc && rc && lc !== "__other__" && rc !== "__other__") {
      addJoin(p.source, lc, p.target, rc);   // catalog key → key: direct, no modal
    } else {
      setJoinModal({ left: p.source, right: p.target,
        preLcol: lc && lc !== "__other__" ? lc : null,
        preRcol: rc && rc !== "__other__" ? rc : null });
    }
  }, [addJoin]);

  const onEdgeClick = useCallback((_e, edge) => {
    if (edge.data?.suggested) {
      // Shift-click on a suggested edge dismisses it (stored in
      // scope.dismissed_suggestions so it survives reloads).
      // Plain click still confirms the suggestion into a real join.
      if (_e?.shiftKey) {
        const dk = edge.data.dismissKey;
        if (!dk) return;
        setScope((s) => ({
          ...s,
          dismissed_suggestions: Array.from(
            new Set([...(s.dismissed_suggestions || []), dk])
          ),
        }));
        setToast("Öneri reddedildi (tabloyu çıkarıp tekrar eklersen geri gelir).");
        return;
      }
      const s = edge.data.edge;
      addJoin(s.left.alias, s.left.column, s.right.alias, s.right.column, s.kind || "lookup");
    } else if (edge.data?.confirmed && window.confirm("Bu join silinsin mi?")) {
      const jid = edge.data.join.id;
      setScope((s) => ({ ...s, joins: s.joins.filter((j) => j.id !== jid) }));
    }
  }, [addJoin]);

  const addTableFromCatalog = (t) => {
    const [schema, ...rest] = t.id.split(".");
    const name = rest.length ? rest.join(".") : schema;
    const realSchema = rest.length ? schema : "";
    const alias = makeAlias(name, scope.basket.map((b) => b.alias));
    const item = {
      table_ref: { schema: realSchema || schema, name }, alias,
      projection: { columns: (t.columns || []).map((c) => c.name), include_all: (t.columns || []).length === 0 },
      routing: { decision: "cached", decided_by: "system", estimated_bytes: 0 },
    };
    COLS_BY_ALIAS[alias] = (t.columns || []).map((c) => ({
      name: c.name || c, type: c.type,
      concept: c.concept || null,    // catalog server-side enriches from table-docs
      join_key: !!c.key,             // honour the catalog's explicit `key` flag
      lookup: c.lookup || null,      // FK lookup hint — fuels computeSuggestedEdges
    }));
    CATALOG_BY_ID[t.id] = t;
    setScope((s) => {
      const next = { ...s, basket: [...s.basket, item] };
      // Best-effort recompute of routing decisions on the server. The badge
      // stays at "—" if this fails (offline / 500) — that's tolerable since
      // the build endpoint re-runs the decision authoritatively anyway.
      fetch(ROUTING_RECOMPUTE_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: next }),
      }).then((r) => r.json()).then((data) => {
        if (data && data.ok) { setScope(data.scope); refineSizes(data.scope); }
      }).catch(() => {});
      return next;
    });
    setNodes((nds) => [...nds, {
      id: alias, type: "tableNode",
      position: { x: 80 + (nds.length % 3) * 340, y: 80 + Math.floor(nds.length / 3) * 240 },
      data: enrichNodeData(item, scope),
    }]);
  };

  // Faz C — manuel SQL dataset → scope.basket'e `sql` kaynağı olarak ekle.
  // addTableFromCatalog'un sql karşılığı; recompute-routing YOK — saklama
  // kararını kullanıcı modalda verdi, decided_by:"user" ile korunur (sql
  // tablonun katalog satırı yok, recompute onu lazy'ye iterdi).
  const addSqlDataset = ({ alias, sql, columns, routing, refresh }) => {
    const item = {
      sql, alias,
      projection: { columns: columns || [], include_all: false },
      routing: { decision: routing, decided_by: "user", estimated_bytes: 0 },
      provenance: "Hazırlık — manuel SQL",
      ...(routing === "cached" && refresh ? { refresh } : {}),
    };
    COLS_BY_ALIAS[alias] = (columns || []).map((c) => ({
      name: c, type: null, concept: null, join_key: false, lookup: null,
    }));
    setScope((s) => ({ ...s, basket: [...(s.basket || []), item] }));
    setNodes((nds) => [...nds, {
      id: alias, type: "tableNode",
      position: { x: 80 + (nds.length % 3) * 340, y: 80 + Math.floor(nds.length / 3) * 240 },
      data: enrichNodeData(item, scope),
    }]);
    setSqlModalOpen(false);
    setToast(`'${alias}' SQL tablosu eklendi`);
  };

  const removeTable = (alias) => {
    setScope((s) => {
      // Drop any dismissed-suggestion keys that reference this alias.
      // joinKey shape is "alias.col—alias.col" — a substring search keyed
      // on `"<alias>."` catches both sides without false positives because
      // column names can't contain `.`.
      const aliasPrefix = `${alias}.`;
      const kept = (s.dismissed_suggestions || []).filter(
        (k) => !k.includes(aliasPrefix)
      );
      return {
        ...s,
        basket: s.basket.filter((b) => b.alias !== alias),
        joins: s.joins.filter((j) => j.left.alias !== alias && j.right.alias !== alias),
        filters: { ...s.filters, raw: (s.filters.raw || []).filter((f) => f.alias !== alias) },
        dismissed_suggestions: kept,
      };
    });
    setNodes((nds) => nds.filter((n) => n.id !== alias));
  };

  const toggleTable = (t) => {
    const id = t.id;
    const existing = scope.basket.find((b) => tableId(b.table_ref) === id);
    if (existing) removeTable(existing.alias); else addTableFromCatalog(t);
  };

  const showPreview = useCallback(async (alias) => {
    const item = scope.basket.find((b) => b.alias === alias);
    if (!item) return;
    setPreviewLoading(true); setPreview({ alias });
    try {
      let data;
      if (item.derivation && item.derivation.kind === "calculated") {
        // Calculated derivations (window functions, multi-source joins) can't
        // be computed in-browser — the server runs the compiled SQL over a
        // DuckDB sample of the sources. Illustrative; Sunum re-runs it full.
        const r = await fetch(PREVIEW_DERIVATION_URL, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scope, alias }),
        });
        data = await r.json();
        if (!data.ok) throw new Error((data.errors || ["Türetilmiş önizleme başarısız"]).join("; "));
        data = { ...data, derived: true };
      } else if (item.derivation) {
        // Aggregate: fetch source's raw rows, aggregate in-browser. Preview is a
        // sample so aggregates are illustrative — Sunum re-runs the derivation
        // on the full pull via DuckDB (see scope/fetch.compile_aggregate_sql).
        const src = scope.basket.find((b) => b.alias === item.derivation.source_alias);
        if (!src || !src.table_ref) throw new Error("Kaynak tablo basketta yok.");
        const u = new URL(PREVIEW_URL, window.location.origin);
        u.searchParams.set("schema", src.table_ref.schema);
        u.searchParams.set("table", src.table_ref.name);
        u.searchParams.set("limit", "1000");
        const srcData = await (await fetch(u.pathname + u.search)).json();
        if (srcData.error) throw new Error(srcData.error);
        data = { ...aggregateLocal(srcData, item.derivation), derived: true };
      } else if (item.sql) {
        // Manuel SQL dataset: re-run the authored query (design-time trigger).
        // preview-sql returns {columns, data_columns, rows} — same shape the
        // drawer expects from the table-preview GET.
        const r = await fetch(PREVIEW_SQL_URL, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sql: item.sql }),
        });
        data = await r.json();
        if (!data.ok) throw new Error((data.errors || ["SQL önizleme başarısız"]).join("; "));
      } else {
        const u = new URL(PREVIEW_URL, window.location.origin);
        u.searchParams.set("schema", item.table_ref.schema);
        u.searchParams.set("table", item.table_ref.name);
        u.searchParams.set("limit", "100");
        data = await (await fetch(u.pathname + u.search)).json();
      }
      setPreview({ alias, ...data });
    } catch (e) { setPreview({ alias, error: String(e) }); }
    finally { setPreviewLoading(false); }
  }, [scope]);

  // Click a node → open its preview drawer. Click the SAME node again →
  // collapse the drawer (toggle behavior). Click anywhere on the empty
  // canvas (onPaneClick below) also closes the drawer.
  const onNodeClick = useCallback((_e, node) => {
    if (preview && preview.alias === node.id) {
      setPreview(null);
      return;
    }
    showPreview(node.id);
  }, [preview, showPreview]);
  const onPaneClick = useCallback(() => {
    if (preview) setPreview(null);
  }, [preview]);

  // "Görünümü kaydet" — captures the current AG Grid state in one shot:
  //   - filter model     → scope.filters (pinned if column has concept, else raw)
  //   - visible columns  → basket item's projection (hidden cols dropped at fetch)
  // Single user-facing button so both stay in sync. Derived items don't carry
  // a projection (computed from derivation) so we skip the projection write
  // for them.
  const saveFilters = async () => {
    if (!gridApiRef.current || !preview) return;
    const alias = preview.alias;
    const model = gridApiRef.current.getFilterModel();
    const colMeta = Object.fromEntries((COLS_BY_ALIAS[alias] || []).map((c) => [c.name, c]));
    const { pinned, raw } = agModelToFilters(model, alias, colMeta);

    // Update projection from visible cols first (server-validated; survives
    // even when there are no filters to save).
    const projUpdated = await updateProjectionFromGrid(alias);

    const hasFilters = pinned.length > 0 || raw.length > 0;
    if (hasFilters) {
      setScope((s) => {
        const next = {
          ...s,
          filters: {
            ...s.filters,
            pinned: [...(s.filters.pinned || []), ...pinned],
            // replace previous raw filters for this alias with the current grid state
            raw: [...(s.filters.raw || []).filter((f) => f.alias !== alias), ...raw],
          },
        };
        // A freshly-pinned date range on a partition column can flip the table
        // lazy→cached, and a tighter projection shrinks bytes/row — recompute so
        // the node badge/size reflect the filter now, not only at build. Mirrors
        // addTableFromCatalog; best-effort (build re-runs it authoritatively).
        fetch(ROUTING_RECOMPUTE_URL, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scope: next }),
        }).then((r) => r.json()).then((data) => {
          if (data && data.ok) { setScope(data.scope); refineSizes(data.scope); }
        }).catch(() => {});
        return next;
      });
    }

    if (!hasFilters && !projUpdated) {
      setToast("Kaydedilecek aktif filtre veya kolon değişikliği yok.");
    } else {
      const parts = [];
      if (hasFilters) parts.push(`${pinned.length + raw.length} filtre`);
      if (projUpdated) parts.push("projection");
      setToast(`${parts.join(" + ")} kaydedildi.`);
    }

    const snap = captureGridState();
    if (snap) setGridStateByAlias((s) => ({ ...s, [alias]: snap }));
  };

  // Filtreleme tab → turn the panel's value specs into pinned (concept) / raw
  // (column) scope filters, replace this alias's panel-managed filters, then
  // recompute routing so the node badge reflects the new size (a date range on
  // a partition column can flip lazy→cached).
  // Faz R1 — "Filtreyi kaydet" artık scope.filters metadata'sı yazmaz; kaynak
  // (main) node'a bağlı bir CACHED türetilmiş filter-node üretir/günceller.
  // Filtre node'a GÖMÜLÜ (derivation.kind='filter'); backend onu Oracle'dan
  // çekip parquet'e materialise eder, boyutunu EXPLAIN PLAN ile hesaplar.
  // Deterministik alias (`<kaynak>_f`) → tekrar kaydedince aynı node güncellenir
  // (her küçük değişiklikte yeni node patlaması olmaz).
  const saveFilterPanel = (alias, specs) => {
    const derivedAlias = `${alias}_f`;
    const pinned = [], raw = [];
    for (const s of specs) {
      const concept = s.concept || null;
      const compilerSafe = s.type !== "num" && (s.op === "between" || s.op === "in" || s.op === "eq");
      if (concept && compilerSafe) {
        const f = { id: `pf_${concept}_${rid()}`, concept, op: s.op, applies_to: [derivedAlias] };
        if (s.op === "between") { f.from = s.from; f.to = s.to; }
        else if (s.op === "in") { f.values = s.values; }
        else { f.values = [s.value]; }
        pinned.push(f);
      } else {
        const f = { id: `rf_${String(s.column).toLowerCase()}_${rid()}`, alias: derivedAlias, column: s.column, op: s.op };
        if (s.op === "between") { f.from = s.from; f.to = s.to; }
        else if (s.op === "in") { f.values = s.values; }
        else { f.value = s.value; }
        raw.push(f);
      }
    }
    // Türetilmiş node kaynağın kolonlarını miras alır (render için).
    COLS_BY_ALIAS[derivedAlias] = COLS_BY_ALIAS[alias] || [];
    const existed = (scope.basket || []).some((b) => b.alias === derivedAlias);

    setScope((cur) => {
      let basket = [...cur.basket];
      // Geçiş temizliği: kaynağın eski scope.filters metadata'sını sök (artık node).
      const keptPinned = (cur.filters.pinned || []).filter(
        (f) => !(Array.isArray(f.applies_to) && f.applies_to.length === 1 && f.applies_to[0] === alias)
      );
      const keptRaw = (cur.filters.raw || []).filter((f) => f.alias !== alias);

      if (pinned.length === 0 && raw.length === 0) {
        // Filtre temizlendi → türetilmiş node'u kaldır.
        return {
          ...cur,
          basket: basket.filter((b) => b.alias !== derivedAlias),
          filters: { ...cur.filters, pinned: keptPinned, raw: keptRaw },
        };
      }

      const filterNode = {
        alias: derivedAlias,
        derivation: { kind: "filter", source_alias: alias, filters: { pinned, raw } },
        projection: { columns: [], include_all: true },   // kaynağı miras al
        routing: { decision: "cached", decided_by: "system", estimated_bytes: 0 },
      };
      const idx = basket.findIndex((b) => b.alias === derivedAlias);
      if (idx >= 0) basket[idx] = { ...basket[idx], ...filterNode };
      else basket = [...basket, filterNode];

      const next = {
        ...cur, basket,
        filters: { ...cur.filters, pinned: keptPinned, raw: keptRaw },
      };
      fetch(ROUTING_RECOMPUTE_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: next }),
      }).then((r) => r.json()).then((data) => {
        if (data && data.ok) { setScope(data.scope); refineSizes(data.scope); }
      }).catch(() => {});
      return next;
    });
    setToast(existed
      ? `'${derivedAlias}' filtreli node güncellendi.`
      : `'${derivedAlias}' filtreli node oluşturuldu (cached).`);
  };

  // Distinct values for a get_distinct string column → Filtreleme checkbox list.
  const fetchDistinct = async (alias, column) => {
    const item = scope.basket.find((b) => b.alias === alias);
    const tr = item?.table_ref;
    if (!tr) throw new Error("Kaynak tablo bulunamadı");
    const u = new URL(DISTINCT_URL, window.location.origin);
    u.searchParams.set("schema", tr.schema || "");
    u.searchParams.set("table", tr.name);
    u.searchParams.set("column", column);
    const r = await fetch(u.pathname + u.search);
    const data = await r.json();
    if (!data.ok) throw new Error(data.error || "Distinct değerler alınamadı");
    return data.values || [];
  };

  // Read the grid's row-grouping + value (aggregation) state → derived table.
  const saveAsTable = () => {
    const api = gridApiRef.current;
    if (!api || !preview) return;
    const groupBy = (api.getRowGroupColumns?.() || []).map((c) => c.getColId());
    const valueCols = (api.getValueColumns?.() || []).map((c) => ({
      col: c.getColId(),
      fn: (c.getAggFunc && c.getAggFunc()) || c.getColDef?.().aggFunc || "sum",
    }));
    if (groupBy.length === 0 && valueCols.length === 0) {
      setToast("Önce kolonları 'Row Groups' / 'Values' alanlarına sürükle."); return;
    }
    const FN = { sum: "sum", avg: "avg", count: "count", min: "min", max: "max" };
    const measures = valueCols.map((v) => {
      const fn = FN[v.fn] || "sum";
      return { column: v.col, fn, as: `${fn.toUpperCase()}_${v.col}` };
    });
    const source = preview.alias;
    const alias = makeAlias(`${source}_agg`, scope.basket.map((b) => b.alias));
    const item = {
      derivation: { kind: "aggregate", source_alias: source, group_by: groupBy, measures },
      alias,
      projection: { columns: [...groupBy, ...measures.map((m) => m.as)], include_all: false },
      routing: { decision: "cached", decided_by: "system", estimated_bytes: 0 },
    };
    const srcCols = Object.fromEntries((COLS_BY_ALIAS[source] || []).map((c) => [c.name, c]));
    COLS_BY_ALIAS[alias] = [
      ...groupBy.map((g) => ({ name: g, concept: srcCols[g]?.concept || null, join_key: true })),
      ...measures.map((m) => ({ name: m.as, concept: null, join_key: false })),
    ];
    setScope((s) => ({ ...s, basket: [...s.basket, item] }));
    setNodes((nds) => [...nds, {
      id: alias, type: "tableNode",
      position: { x: 100 + (nds.length % 3) * 340, y: 100 + Math.floor(nds.length / 3) * 240 },
      data: enrichNodeData(item, scope),
    }]);
    setToast(`'${alias}' türetilmiş tablo eklendi (${groupBy.length} grup, ${measures.length} measure).`);
    const snap = captureGridState();
    if (snap) setGridStateByAlias((s) => ({ ...s, [preview.alias]: snap }));
  };

  // Drawer resize (drag the top edge up/down).
  const startResize = (e) => {
    e.preventDefault();
    const move = (ev) => {
      const h = window.innerHeight - ev.clientY - 6;
      setDrawerH(Math.max(120, Math.min(window.innerHeight - 160, h)));
    };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  };

  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(null), 2500); return () => clearTimeout(t); } }, [toast]);

  // Build-confirm modal state — populated by /scope/preview-build, cleared
  // on confirm/cancel. ``pendingScope`` is the layout-embedded scope we will
  // POST to /scope/build once the user confirms.
  const [buildPreview, setBuildPreview] = useState(null);
  const [pendingScope, setPendingScope] = useState(null);

  const _finalisedScope = () => {
    const pos = Object.fromEntries(nodes.map((n) => [n.id, n.position]));
    return {
      ...scope,
      basket: scope.basket.map((b) => pos[b.alias] ? { ...b, layout: { x: pos[b.alias].x, y: pos[b.alias].y } } : b),
    };
  };

  const _commitBuild = async (finalScope) => {
    setBusy(true); setErr(null);
    try {
      const data = await (await fetch(BUILD_URL, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope: finalScope }),
      })).json();
      if (!data.ok) { setErr((data.errors || ["Bilinmeyen hata"]).join(" · ")); setBusy(false); return; }
      window.location.href = data.redirect;
    } catch (e) { setErr(String(e)); setBusy(false); }
  };

  const goToSunum = async () => {
    setErr(null);
    setBusy(true);
    const finalScope = _finalisedScope();
    try {
      const r = await fetch(PREVIEW_BUILD_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: finalScope }),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        setErr((data.errors || ["Bilinmeyen hata"]).join(" · "));
        setBusy(false);
        return;
      }
      const diffEmpty = !data.diff || (
        !data.diff.added && !data.diff.removed && !data.diff.changed
        && !data.diff.filters && !data.diff.pin_flips && !data.diff.joins
      );
      const noBlocks = (data.summary?.total || 0) === 0;
      // First build (no parent_version) OR nothing has changed AND nothing
      // would surface — go straight to build. Otherwise pop the confirm
      // modal so the user can see what's about to happen.
      if (data.parent_version == null || (diffEmpty && noBlocks)) {
        await _commitBuild(finalScope);
        return;
      }
      setBusy(false);
      setPendingScope(finalScope);
      setBuildPreview(data);
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  const confirmBuild = () => {
    const fs = pendingScope;
    setBuildPreview(null);
    setPendingScope(null);
    if (fs) _commitBuild(fs);
  };

  const cancelBuild = () => {
    setBuildPreview(null);
    setPendingScope(null);
  };

  return (
    <div className="hz-app">
      {/* Phase 11.hazirlik-polish: internal hz-topbar removed. The PRISMA
          shell topbar already shows the breadcrumb + brand; an extra
          row below it just cut the canvas off without adding info.
          "Sunum'a geç" + "Raporlar" link relocated into the sidebar. */}

      {err && <div className="hz-error hz-error--bar">{err}</div>}

      <div className="hz-body">
        <SourcesSidebar
          scope={scope}
          libraryBlocks={DATA.library_blocks || []}
          hiddenAliases={hiddenAliases}
          onToggleVisibility={toggleAliasVisibility}
          onOpenDocs={(t) => setDocsTable((cur) => (cur && cur.id === t.id ? null : t))}
          goingToSunum={busy}
          onGoToSunum={goToSunum}
          onUpload={() => setUploadOpen(true)}
          onAddSql={() => setSqlModalOpen(true)}
          onEditSql={(alias) => setEditSqlAlias(alias)}
          chat={{
            history: chatHistory, busy: chatBusy, error: chatError,
            draft: chatDraft, onDraftChange: setChatDraft,
            onSend: sendChat, onApply: applySuggestion, onDismiss: dismissSuggestion,
            applyingId,
          }}
        />
        {docsTable && (
          <TableDocsPanel table={docsTable} onClose={() => setDocsTable(null)} />
        )}
        <main className="hz-right">
          <div className="hz-canvas">
            <ReactFlow
              nodes={nodes} edges={edges}
              onNodesChange={onNodesChange}
              onConnect={onConnect} onEdgeClick={onEdgeClick}
              onNodeClick={onNodeClick} onPaneClick={onPaneClick}
              nodeTypes={NODE_TYPES} edgeTypes={EDGE_TYPES}
              fitView proOptions={{ hideAttribution: true }}
            >
              <Background gap={16} />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
            {scope.basket.length === 0 && (
              <div className="hz-canvas-empty">Soldan bir tablo seç → ER diyagramına eklensin.</div>
            )}
          </div>
          {preview && (
            <PreviewDrawer
              key={preview.alias}
              preview={preview} loading={previewLoading} height={drawerH}
              previewLabel={(() => {
                const it = scope.basket.find((b) => b.alias === preview.alias);
                return it?.table_ref ? `${it.table_ref.schema}.${it.table_ref.name}` : preview.alias;
              })()}
              onResizeStart={startResize} onClose={() => setPreview(null)}
              onSaveFilters={saveFilters} onSaveAsTable={saveAsTable}
              onSaveFilterPanel={saveFilterPanel} onFetchDistinct={fetchDistinct}
              existingFilters={{
                pinned: (scope.filters.pinned || []).filter((f) => Array.isArray(f.applies_to) && f.applies_to.length === 1 && f.applies_to[0] === preview.alias),
                raw: (scope.filters.raw || []).filter((f) => f.alias === preview.alias),
              }}
              savedGridState={gridStateByAlias[preview.alias]}
              onGridReady={(p) => { gridApiRef.current = p.api; window.__hzGridApi = p.api; }}
            />
          )}
        </main>
      </div>

      {joinModal && (
        <JoinKeyModal left={joinModal.left} right={joinModal.right}
          onClose={() => setJoinModal(null)}
          onSave={({ lcol, rcol, kind }) => { addJoin(joinModal.left, lcol, joinModal.right, rcol, kind); setJoinModal(null); }} />
      )}
      {buildPreview && (
        <BuildConfirmModal preview={buildPreview} scope={pendingScope} onConfirm={confirmBuild} onCancel={cancelBuild} />
      )}
      {refreshAlias && (() => {
        const it = (scope.basket || []).find((b) => b.alias === refreshAlias);
        return it ? (
          <RefreshModal
            item={it}
            onSave={(r) => saveRefresh(refreshAlias, r)}
            onClose={() => setRefreshAlias(null)}
          />
        ) : null;
      })()}
      {sqlModalOpen && (
        <SqlDatasetModal
          existingAliases={(scope.basket || []).map((b) => b.alias)}
          onSave={addSqlDataset}
          onClose={() => setSqlModalOpen(false)}
        />
      )}
      {editSqlAlias && (() => {
        const it = (scope.basket || []).find((b) => b.alias === editSqlAlias);
        return it ? (
          <SqlDatasetModal
            existingAliases={(scope.basket || []).map((b) => b.alias)}
            existing={{
              alias: it.alias,
              sql: it.sql || "",
              routing: it.routing?.decision || "cached",
              refresh: it.refresh || { kind: "manual" },
              columns: it.projection?.columns || [],
            }}
            onSave={saveSqlEdit}
            onClose={() => setEditSqlAlias(null)}
          />
        ) : null;
      })()}
      <UploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onCommit={() => {
          // After a successful commit the new sheets are now under the
          // manifest's `uploads`; reload to pick them up in DATA.catalog
          // (dom_uploads is injected server-side at page load).
          setUploadOpen(false);
          window.location.reload();
        }}
      />
      {toast && <div className="hz-toast">{toast}</div>}
    </div>
  );
}

function useNodesStateCompat(init) {
  const [initial] = useState(init);
  return useNodesState(initial);
}

createRoot(document.getElementById("hazirlik-root")).render(
  <ReactFlowProvider><App /></ReactFlowProvider>
);
