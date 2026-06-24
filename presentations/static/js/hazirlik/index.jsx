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
// AG Grid Community (Enterprise kaldırıldı: pivot/row-grouping/sidebar artık yok;
// pivot Python/LLM ile yapılır). Kolon filtreleme kendi "Kolonlar" panelimizle.
import "@xyflow/react/dist/style.css";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";
// `ag-theme-alpine.css` ships both light + dark variants — switching the
// className on the wrapper is enough to flip themes, no second import.
// Veri Yükle: reuse Sunum's modal — editor/lib/api.js has the upload routes,
// and api.js's API_BASE is hazirlik-aware (strips /hazirlik/ from the path).
import UploadModal from "../editor/components/UploadModal.jsx";
import FilterPanel from "./FilterPanel.jsx";
import useResizable from "../editor/lib/useResizable.js";
import CodeMirror from "@uiw/react-codemirror";
import { python as cmPython } from "@codemirror/lang-python";
import { sql as cmSql } from "@codemirror/lang-sql";
import {
  X, Plus, Trash2, Database, ArrowLeft, ArrowRight, ChevronLeft, ChevronRight,
  MessageSquare, Save, Eraser, Table2, Pencil,
  Building2, Percent, Network, Calendar, Upload, Send, Loader2, Eye, EyeOff, Info, Tag, Code2,
  Play, Lock, Columns3, ChevronDown, Search, Sparkles,
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
const BUILD_ASYNC_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/build-async`);
const buildStatusUrl = (jobId) =>
  _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/build-status/${jobId}`);
const buildCancelUrl = (jobId) =>
  _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/build-cancel/${jobId}`);
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
const FILTER_PREVIEW_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/filter-preview`);
const PYTHON_PREVIEW_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/preview-python`);
const SOURCE_SQL_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/source-sql`);
const RESOLVE_SQL_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/resolve-sql`);
const EXPLAIN_SQL_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/explain-sql`);
const DISTINCT_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/distinct`);
// Başlık (meta.title) kaydı: manifest patch endpoint'i (Keşif'teki workshop
// header'ın Hazırlık karşılığı). updated_at'i de bump'lar → listede öne çıkar.
const PATCH_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/patch`);
const LIST_URL = _path.slice(0, _path.indexOf("/hazirlik")) + "/";

const COLS_BY_ALIAS = DATA.columns_by_alias || {};
const CONCEPTS = DATA.concepts || [];   // #4 — {id,label,type,canonical_values}
const VALIDATE_CONCEPT_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/validate-concept`);
// MVP — kolona göre konsept önerisi (sıralı mevcut + yeni taslak), deterministik backend.
const SUGGEST_CONCEPTS_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/suggest-concepts`);
// Yeni konsept oluşturma — Kütüphane ile AYNI endpoint (registry'ye yazar, ~2s'de reload).
const CONCEPT_CREATE_URL = _path.slice(0, _path.indexOf("/hazirlik")) + "/atolye/konseptler/create";
const CONCEPT_UPDATE_URL = _path.slice(0, _path.indexOf("/hazirlik")) + "/atolye/konseptler/update";
const SUGGESTED = DATA.suggested_edges || [];
// Reload'da türetilmiş node'ların kolonlarını hemen doldur (ilk render'da "0
// kolon" görünmesin). hydrateDerivedCols hoist'lu — aşağıda tanımlı.
hydrateDerivedCols(DATA.scope?.basket || []);
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
// Session DuckDB bütçesi (Phase 6.5 spec'teki 2 GB cap). Sol paneldeki
// göstergede cached node'ların tahmini boyut toplamı buna karşı gösterilir.
const SESSION_BUDGET_BYTES = ROUTING_CONFIG.session_budget_bytes || 2_000_000_000;

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
  const taken = new Set(existing);
  if (!taken.has(base)) return base;
  // Çakışma → sayısal ek (`_2`, `_3`…). Ek'e DAİMA yer aç: base'i (40 − ek
  // uzunluğu) kadar kırp. (Eski `${base}_${i}`.slice(0,40) ek'i kesip hep base'i
  // üretiyordu → 40 karakterlik base'te taken.has hep true = SONSUZ DÖNGÜ/donma.
  // Ör. `createPythonNode`'da 40-karakterlik bir source alias'tan `<src>_py`.)
  for (let i = 2; ; i++) {
    const suffix = `_${i}`;
    const alias = base.slice(0, 40 - suffix.length) + suffix;
    if (!taken.has(alias)) return alias;
  }
}

// Bir node'a filtre atınca oluşan deterministik child alias (`<kaynak>_f`).
// Alias şeması 40 karakterle sınırlı (scope schema) → uzun isimli (ör. zincirli
// join/union) kaynaklarda kaynak kısmı kırpılır ki `_f` daima sığsın. Hem
// oluşturma (saveFilterPanel) hem child arama AYNI fonksiyonu kullanmalı.
function filterChildAlias(alias) {
  return `${(alias || "").length <= 38 ? alias : alias.slice(0, 38)}_f`;
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
  const { item, desc, size, colCount, keyCols, derived, activeFilters, color, inactive } = data;
  const cached = item.routing?.decision === "cached";
  // Faz R1 node tipleri: main (table_ref/sql) SABİT — lazy/cache toggle + cron yok;
  // filter-node BOYUTLU (EXPLAIN PLAN) → cached/lazy rozeti + cron; aggregate/
  // calculated DuckDB'de → "türetilmiş" (boyut yok).
  const isFilter = item.derivation?.kind === "filter";
  const isPython = item.derivation?.kind === "python";   // Faz P — yuvarlak script node
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
    <div className={`hz-node${derived ? " hz-node--derived" : ""}${isPython ? " hz-node--python" : ""}${inactive ? " is-inactive" : ""}`}>
      <div className="hz-node-head" style={headStyle}>
        {/* Başlık-seviyesi handle = union ("tırnak"). Başlıktan başlığa
            sürükle → iki tabloyu unionla (kolon sayısı + tip kontrolü modalda). */}
        <Handle type="target" position={Position.Left} id="__table__" className="hz-handle hz-handle--table" />
        {inactive && <span className="hz-node-inactive-tag" title="Pasif — Sunum'a alınmaz (sol menüden tıklayıp aktif et)">pasif</span>}
        <span className="hz-node-alias">{isPython ? <Code2 size={12} /> : <Database size={12} />} {item.table_ref ? `${item.table_ref.schema}.${item.table_ref.name}` : item.alias}{item.sql && <span className="hz-sql-tag">SQL</span>}{isPython && <span className="hz-sql-tag hz-py-tag">PY</span>}</span>
        <span
          className={`hz-badge hz-badge--${sized ? (cached ? "cached" : "lazy") : (isPython ? "python" : "derived")}`}
          title={isPython ? "Python dönüşüm node'u — script DuckDB/parquet üzerinde sandbox'ta koşar." : decisionTitle}
        >
          {sized
            ? (cached ? `cached · ${sizeText}` : `lazy · ${sizeText}`)
            : (isPython ? "python" : "türetilmiş")}
        </span>
        <Handle type="source" position={Position.Right} id="__table__" className="hz-handle hz-handle--table" />
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
            title="Türetilmiş dataset — kaynağından üretilip parquet'e materialise edilir. Cron: node'a tıkla → Cron tab."
          >
            {colCount} kolon
          </span>
          <span className="hz-main-lock" title="Cron için node'a tıkla → Cron tab.">⟳ {refreshLabel(item.refresh)}</span>
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

// Bir derivation'ın kaynak alias listesi — TEK yer (kind'a göre dallanmayı
// her çağrı yerinde tekrarlamamak için). calculated/join/union çoklu kaynak;
// aggregate/filter tekil source_alias.
function derivSourceAliases(d) {
  if (!d) return [];
  if (d.kind === "calculated" || d.kind === "join" || d.kind === "union") {
    return d.source_aliases || [];
  }
  return d.source_alias ? [d.source_alias] : [];
}

// join çıktısının kolon listesi: sol kolonlar + sağ kolonlar; çakışan sağ
// isimler sağ alias ile prefix'lenir — backend compile_join_sql ile AYNI kural.
function joinColsFor(leftAlias, rightAlias) {
  const lcols = COLS_BY_ALIAS[leftAlias] || [];
  const rcols = COLS_BY_ALIAS[rightAlias] || [];
  const lnames = new Set(lcols.map((c) => c.name));
  const out = lcols.map((c) => ({ ...c }));
  for (const c of rcols) {
    out.push({ ...c, name: lnames.has(c.name) ? `${rightAlias}_${c.name}` : c.name });
  }
  return out;
}

// Bir türetilmiş item'ın çıktı kolonlarını hesapla. filter/join/union kaynak
// kolonlarına bağlıdır (kaynak henüz COLS_BY_ALIAS'ta yoksa null → sonraki tur);
// aggregate/calculated kolon adlarını derivation'dan alır (kaynak gerekmez).
function computeDerivedCols(d) {
  if (!d) return null;
  if (d.kind === "filter") {
    return COLS_BY_ALIAS[d.source_alias]
      ? COLS_BY_ALIAS[d.source_alias].map((c) => ({ ...c })) : null;
  }
  if (d.kind === "join") {
    const [a, b] = d.source_aliases || [];
    return (COLS_BY_ALIAS[a] && COLS_BY_ALIAS[b]) ? joinColsFor(a, b) : null;
  }
  if (d.kind === "union") {
    const a = (d.source_aliases || [])[0];
    return COLS_BY_ALIAS[a] ? COLS_BY_ALIAS[a].map((c) => ({ ...c })) : null;
  }
  if (d.kind === "calculated") {
    return (d.columns || []).map((c) => ({ name: c.name, concept: null, join_key: false, expr: c.expr }));
  }
  if (d.kind === "python") {
    // Çıktı kolonları script çalıştırılınca (output_columns) belli olur; öncesinde boş.
    return (d.output_columns || []).map((n) => ({ name: n, concept: null, join_key: false }));
  }
  // aggregate — group_by + measures adları derivation'da explicit.
  const srcCols = Object.fromEntries((COLS_BY_ALIAS[d.source_alias] || []).map((c) => [c.name, c]));
  return [
    ...(d.group_by || []).map((g) => ({ name: g, concept: srcCols[g]?.concept || null, join_key: true })),
    ...(d.measures || []).map((m) => ({ name: m.as, concept: null, join_key: false })),
  ];
}

// scope.basket'teki TÜM türetilmiş + manuel-SQL item'lar için COLS_BY_ALIAS'ı
// bağımlılık sırasıyla doldur. Reload'da (özellikle Sunum'a gidip dönünce)
// DATA.columns_by_alias YALNIZ main tabloları taşır → SQL ve türetilmiş node'lar
// boş kalıp "0 kolon" görünürdü; bu onu giderir. SQL node kolonlarını kendi
// projection.columns'undan alır (önizlemede yakalanıp scope'a yazılmıştı).
function hydrateDerivedCols(basket) {
  let progressed = true;
  while (progressed) {
    progressed = false;
    for (const b of (basket || [])) {
      if ((COLS_BY_ALIAS[b.alias] || []).length) continue;
      let cols = null;
      if (b.sql && !b.derivation) {
        const names = (b.projection && b.projection.columns) || [];
        cols = names.map((n) => ({ name: n, type: null, concept: null, join_key: false }));
      } else if (b.derivation) {
        cols = computeDerivedCols(b.derivation);
      }
      if (cols && cols.length) { COLS_BY_ALIAS[b.alias] = cols; progressed = true; }
    }
  }
}

// Union uyum kontrolü: pozisyonel kolon sayısı + tip-ailesi eşleşmesi.
function typeFamily(t) {
  const s = String(t || "").toUpperCase();
  if (/(NUMBER|INT|DEC|FLOAT|DOUBLE|REAL|NUMERIC)/.test(s)) return "number";
  if (/(DATE|TIMESTAMP|TIME)/.test(s)) return "date";
  return "text";
}
function unionCompat(leftAlias, rightAlias) {
  const l = COLS_BY_ALIAS[leftAlias] || [];
  const r = COLS_BY_ALIAS[rightAlias] || [];
  const countOk = l.length > 0 && l.length === r.length;
  const pairs = [];
  const n = Math.max(l.length, r.length);
  for (let i = 0; i < n; i++) {
    const lc = l[i], rc = r[i];
    pairs.push({
      left: lc?.name ?? "—", right: rc?.name ?? "—",
      leftType: lc?.type || "?", rightType: rc?.type || "?",
      ok: !!lc && !!rc && typeFamily(lc.type) === typeFamily(rc.type),
    });
  }
  const typesOk = countOk && pairs.every((p) => p.ok);
  return { countOk, typesOk, pairs };
}

function nodeData(item) {
  const cols = COLS_BY_ALIAS[item.alias] || [];
  if (item.derivation) {
    const d = item.derivation;
    const kindLabel = d.kind === "filter" ? "filtre"
      : d.kind === "calculated" ? "hesaplama"
      : d.kind === "join" ? "join"
      : d.kind === "union" ? "union"
      : d.kind === "python" ? "python" : "aggregate";
    const srcLabel = d.source_alias || derivSourceAliases(d).join(" + ");
    return {
      item, derived: true, desc: `${srcLabel} → ${kindLabel}`,
      // Faz R1: filter-node boyutu hesaplanır (EXPLAIN PLAN) → rozet gösterilir;
      // aggregate/calculated/join/union DuckDB'de → boyut yok (size=null).
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
    // Faz R4/#1 — "Çözümle" ile üretilen sql node'unun kaynak tablolara lineage'ı.
    if (!b.derivation && Array.isArray(b.derived_from) && b.derived_from.length) {
      b.derived_from.forEach((src) => {
        if (!aliases.has(src) || !aliases.has(b.alias)) return;
        edges.push({
          id: `deriv_${b.alias}_${src}`,
          source: src, target: b.alias,
          sourceHandle: "__other__", targetHandle: "__other__",
          type: "hzPairEdge",
          className: "hz-edge hz-edge--derivation hz-edge--deriv-sql",
          data: { derivation: true, derivedAlias: b.alias, sourceAlias: src, derivKind: "sql", label: "sql →", kind: "derivation" },
        });
      });
      return;
    }
    if (!b.derivation) return;
    const d = b.derivation;
    const sources = derivSourceAliases(d);
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
  // Multi-key: a list of {lcol, rcol} pairs, all AND'ed in the join condition
  // (e.g. date + currency). Seeded with the dragged columns (or first of each).
  const [keys, setKeys] = useState([{
    lcol: preLcol || lc[0]?.name || "",
    rcol: preRcol || rc[0]?.name || "",
  }]);
  const [kind, setKind] = useState("inner");

  const optLabel = (c) => (c.concept ? `${c.name} · ${c.concept}` : c.name);
  const conceptOf = (cols, name) => cols.find((c) => c.name === name)?.concept || null;

  const setKey = (i, patch) =>
    setKeys((ks) => ks.map((k, j) => (j === i ? { ...k, ...patch } : k)));
  const addKey = () =>
    setKeys((ks) => [...ks, { lcol: lc[0]?.name || "", rcol: rc[0]?.name || "" }]);
  const removeKey = (i) => setKeys((ks) => ks.filter((_, j) => j !== i));

  const validKeys = keys.filter((k) => k.lcol && k.rcol);
  const anyMismatch = validKeys.some((k) => {
    const l = conceptOf(lc, k.lcol), r = conceptOf(rc, k.rcol);
    return l && r && l !== r;
  });

  return (
    <Modal title={`Join Tablosu: ${left} + ${right}`} size="lg" onClose={onClose} footer={
      <>
        <button className="ts-btn" onClick={onClose}>Vazgeç</button>
        <button className="ts-btn ts-btn--primary" disabled={!validKeys.length}
          onClick={() => onSave({ keys: validKeys, kind })}>Join tablosu oluştur</button>
      </>
    }>
      <p className="hz-muted">
        İki tabloyu birleştirip <strong>yeni bir türetilmiş tablo</strong> üretir
        (ortada bir node). Hangi kolon(lar)dan birleşsin? Birden çok anahtar
        eklersen hepsi <strong>AND</strong>’lenir.
      </p>
      <div className="hz-join-keys">
        <div className="hz-join-keys__head">
          <span>{left}</span><span></span><span>{right}</span><span></span>
        </div>
        {keys.map((k, i) => {
          const lConcept = conceptOf(lc, k.lcol);
          const rConcept = conceptOf(rc, k.rcol);
          const match = lConcept && rConcept && lConcept === rConcept;
          const chip = (c) => c && (
            <span className={`hz-col-concept hz-join-concept${match ? " hz-join-concept--match" : ""}`}>{c}</span>
          );
          return (
            <div className="hz-join-key-row" key={i}>
              <div className="hz-join-key-cell">
                <select value={k.lcol} onChange={(e) => setKey(i, { lcol: e.target.value })}>
                  {lc.map((c) => <option key={c.name} value={c.name}>{optLabel(c)}</option>)}
                  {lc.length === 0 && <option value="">(kolon yok)</option>}
                </select>
                {chip(lConcept)}
              </div>
              <div className="hz-join-key-eq">=</div>
              <div className="hz-join-key-cell">
                <select value={k.rcol} onChange={(e) => setKey(i, { rcol: e.target.value })}>
                  {rc.map((c) => <option key={c.name} value={c.name}>{optLabel(c)}</option>)}
                  {rc.length === 0 && <option value="">(kolon yok)</option>}
                </select>
                {chip(rConcept)}
              </div>
              <button type="button" className="hz-join-key-del" title="Anahtarı kaldır"
                disabled={keys.length === 1} onClick={() => removeKey(i)}>×</button>
            </div>
          );
        })}
        <button type="button" className="hz-add-key" onClick={addKey}>+ Anahtar ekle</button>
      </div>
      {anyMismatch && (
        <p className="hz-join-concept-hint is-mismatch">
          ⚠ Bazı anahtar çiftlerinde concept’ler farklı. Join yine kurulabilir
          ama o kolonların anlamsal eşleşmesi yok — kolon adlarını kontrol et.
        </p>
      )}
      <label className="hz-field">Join türü
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="inner">inner — yalnız eşleşen satırlar</option>
          <option value="left">left — sol tablonun tüm satırları</option>
        </select>
      </label>
    </Modal>
  );
}


// Union modalı: iki tabloyu alt alta ekler. Pozisyonel kolon sayısı + tip
// uyumu gösterilir; sayı uymuyorsa oluşturma engellenir (DuckDB pozisyonel
// union yapar). union_all=false → tekrar eden satırlar elenir (DISTINCT).
function UnionModal({ left, right, onSave, onClose }) {
  const [unionAll, setUnionAll] = useState(true);
  const { countOk, typesOk, pairs } = unionCompat(left, right);
  return (
    <Modal title={`Union Tablosu: ${left} + ${right}`} size="lg" onClose={onClose} footer={
      <>
        <button className="ts-btn" onClick={onClose}>Vazgeç</button>
        <button className="ts-btn ts-btn--primary" disabled={!countOk}
          onClick={() => onSave({ unionAll })}>Union tablosu oluştur</button>
      </>
    }>
      <p className="hz-muted">
        İki tabloyu alt alta ekleyip <strong>yeni bir türetilmiş tablo</strong> üretir.
        Kolonlar sırayla eşleşir — sayı ve tipleri uyumlu olmalı.
      </p>
      {!countOk && (
        <p className="hz-join-concept-hint is-mismatch">
          ⚠ Kolon sayıları farklı: {left} ({(COLS_BY_ALIAS[left] || []).length}) ↔ {right} ({(COLS_BY_ALIAS[right] || []).length}).
          Union için eşit olmalı.
        </p>
      )}
      {countOk && !typesOk && (
        <p className="hz-join-concept-hint is-mismatch">
          ⚠ Bazı kolon tipleri farklı (aşağıda kırmızı). Union yine kurulabilir
          ama DuckDB tip dönüşümü deneyecek — sonucu kontrol et.
        </p>
      )}
      {countOk && typesOk && (
        <p className="hz-join-concept-hint is-match">✓ Kolon sayısı + tipleri uyumlu.</p>
      )}
      <table className="hz-docs-cols hz-union-table">
        <thead><tr><th>#</th><th>{left}</th><th>{right}</th><th>Tip</th></tr></thead>
        <tbody>
          {pairs.map((p, i) => (
            <tr key={i} className={p.ok ? "" : "hz-union-row--bad"}>
              <td className="hz-docs-col-type">{i + 1}</td>
              <td className="hz-docs-col-name">{p.left} <span className="hz-docs-col-type">{p.leftType}</span></td>
              <td className="hz-docs-col-name">{p.right} <span className="hz-docs-col-type">{p.rightType}</span></td>
              <td>{p.ok ? "✓" : "✗"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <label className="hz-field hz-union-allrow">
        <input type="checkbox" checked={unionAll} onChange={(e) => setUnionAll(e.target.checked)} />
        <span>Tüm satırlar (UNION ALL) — kapalıysa tekrarlananlar elenir (DISTINCT)</span>
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

function TableDocsPanel({ table, onClose, onOpen }) {
  const ref = useRef(null);
  const cols = table.columns || [];
  const filters = table.common_filters || [];
  const sources = table.sources || [];

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
                        {c.expr && (
                          <code className="hz-docs-expr" title={c.expr}>
                            {c.expr.length > 28 ? `${c.expr.slice(0, 28)}…` : c.expr}
                          </code>
                        )}
                        {c.computed && !c.expr && !c.concept && (
                          <span className="hz-docs-pill hz-docs-pill--computed"
                            title="Kaynak tabloda eşleşmedi — SQL'de hesaplanmış/yeniden adlandırılmış">
                            hesaplanmış
                          </span>
                        )}
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

        {/* Üretilmiş/manuel-SQL node'larda gerçek kaynak tabloların docs'una
            köprü (Faz: Option A). Tıklayınca panel o tablonun tam dökümanına
            geçer. Filter node'ları zaten doğrudan kaynak doc'unu açtığından
            burada görünmez. */}
        {sources.length > 0 && (
          <div className="hz-docs-section">
            <div className="hz-docs-section-title">
              <Database size={12} strokeWidth={2} />
              <span>Kaynak Tablolar ({sources.length})</span>
            </div>
            <p className="hz-muted" style={{ margin: "0 0 6px", fontSize: 11 }}>
              Bu {table.synthetic ? "üretilmiş tablo" : "tablo"} aşağıdaki gerçek
              tablo(lar)dan türetildi — tam dökümantasyon için birine tıkla.
            </p>
            <div className="hz-docs-sources">
              {sources.map((s) => (
                <button
                  type="button"
                  key={s.id}
                  className="hz-docs-source-link"
                  onClick={() => onOpen && onOpen(s)}
                  title={`${s.id} dökümantasyonunu aç`}
                >
                  <Database size={11} strokeWidth={1.8} />
                  <span className="hz-docs-source-id">{s.id}</span>
                  {(s.columns || []).length > 0 && (
                    <span className="hz-docs-source-meta">{s.columns.length} kolon</span>
                  )}
                </button>
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
  // Faz R4/#1 — "Çözümle" planı: {source_tables, warnings}. Kaydet'te bu plandaki
  // tablolar main node, sonuç sql node (derived_from ile bağlı) olarak eklenir.
  const [plan, setPlan] = useState(null);
  // Önizleme UX: geçen süre sayacı + iptal + optimizer satır tahmini (EXPLAIN,
  // paralel — önizlemeyi bloklamaz). İptal yalnız beklemeyi keser; Oracle'da
  // başlamış sorgu sunucuda tamamlanabilir.
  const [elapsed, setElapsed] = useState(0);
  const [explain, setExplain] = useState(null);
  const abortRef = useRef(null);
  const timerRef = useRef(null);
  useEffect(() => () => {       // unmount temizliği
    if (timerRef.current) clearInterval(timerRef.current);
    if (abortRef.current) abortRef.current.abort();
  }, []);

  const cancelPreview = () => {
    if (abortRef.current) abortRef.current.abort();
  };

  // Tek buton: Önizle/Doğrula HEM örnek satır+kolonları getirir HEM (yeni
  // eklemede) kaynak tabloları çözümler (eski ayrı "Çözümle" butonu birleşti).
  // Önizleme birincil; çözümleme + EXPLAIN best-effort (başarısızlıkları
  // önizlemeyi engellemez).
  const runPreview = async () => {
    setBusy(true); setErrors([]); setPreview(null); setPlan(null); setExplain(null);
    setElapsed(0);
    const t0 = Date.now();
    timerRef.current = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    const ac = new AbortController();
    abortRef.current = ac;
    // EXPLAIN paraleli — sorgu koşarken "~N satır" bilgisini düşürür.
    fetch(EXPLAIN_SQL_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }), signal: ac.signal,
    }).then((r) => r.json()).then((d) => {
      if (d && d.ok && d.rows != null) setExplain({ rows: d.rows });
    }).catch(() => {});
    try {
      const reqs = [fetch(PREVIEW_SQL_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql }), signal: ac.signal,
      })];
      if (!isEdit) reqs.push(fetch(RESOLVE_SQL_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql }), signal: ac.signal,
      }));
      const [pRes, rRes] = await Promise.all(reqs);
      const pData = await pRes.json();
      if (!pData.ok) { setErrors(pData.errors || ["Bilinmeyen hata"]); return; }
      setPreview({
        columns: pData.columns || [], rows: pData.rows || [],
        row_count: pData.row_count || 0,
        truncated: !!pData.truncated, cap: pData.cap || null,
      });
      if (rRes) {
        const rData = await rRes.json().catch(() => null);
        if (rData && rData.ok) {
          setPlan({ source_tables: rData.source_tables || [], warnings: rData.warnings || [] });
        }
      }
    } catch (e) {
      if (e && e.name === "AbortError") {
        setErrors(["Önizleme iptal edildi (sunucuda başlamış sorgu arka planda bitebilir)."]);
      } else {
        setErrors([String(e.message || e)]);
      }
    } finally {
      setBusy(false);
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
      abortRef.current = null;
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
      // Faz R4/#1 — Çözümle planı (yeni eklemede): kaynak tabloları main node +
      // bu sonucu derived_from ile onlara bağla. Edit modunda gönderme.
      resolvePlan: (!isEdit && plan) ? plan : null,
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
          onChange={(e) => { setSql(e.target.value); setPreview(null); setPlan(null); }} />
      </label>
      <div className="hz-row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" className="ts-btn ts-btn--primary"
          disabled={busy || !sql.trim()} onClick={runPreview}
          title="Sorguyu doğrula + örnek satırları getir; kaynak tabloları çıkar (dökümante olanlar node olarak eklenir, sonuç onlara bağlanır)">
          {busy
            ? <><Loader2 size={13} className="ts-spin" /> Çalıştırılıyor… {elapsed > 0 ? `${elapsed}sn` : ""}</>
            : <><Eye size={13} /> Önizle / Doğrula</>}
        </button>
        {busy && (
          <button type="button" className="ts-btn" onClick={cancelPreview}
            title="Beklemeyi kes — Oracle'da başlamış sorgu sunucuda bitebilir">
            İptal
          </button>
        )}
        {busy && explain && (
          <span className="hz-muted">
            optimizer tahmini ~{Number(explain.rows).toLocaleString("tr-TR")} satır
            {explain.rows > 1_000_000 ? " — uzun sürebilir" : ""}
          </span>
        )}
        {preview && (
          <span className="hz-muted">
            {preview.row_count} satır · {preview.columns.length} kolon
            {preview.truncated ? ` (örnek ilk ${preview.cap} satırla sınırlı)` : ""}
          </span>
        )}
      </div>
      {plan && (
        <div className="hz-resolve-plan" style={{ marginTop: 8 }}>
          <div className="hz-muted" style={{ fontSize: 11, marginBottom: 4 }}>
            Çözümleme — eklenecek kaynak tablolar (node) + bu sonuç onlara bağlı dataset:
          </div>
          {plan.source_tables.map((t) => (
            <div key={t.id} className={`hz-resolve-row${t.documented ? "" : " is-undoc"}`}>
              <span>{t.documented ? "✓" : "⚠"}</span>
              <strong>{t.id}</strong>
              <span className="hz-muted">
                {t.documented ? `${t.columns.length} kolon` : "dökümante değil"}
              </span>
            </div>
          ))}
          {plan.warnings.length > 0 && (
            <div className="hz-muted" style={{ fontSize: 11, marginTop: 4 }}>
              {plan.warnings.map((w, i) => <div key={i}>• {w}</div>)}
            </div>
          )}
        </div>
      )}
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
        ? <p className="hz-muted">Cron (yenileme sıklığı) burada değil — tabloyu ekledikten sonra node'una tıkla → <strong>Cron</strong> tab'ından ayarla.</p>
        : <p className="hz-muted">Lazy tablolara cron bağlanamaz — küçültürsen (aggregate/filtre) cached olur ve cron'lanabilir.</p>}
    </Modal>
  );
}

// Sentetik (üretilmiş/SQL) tablonun çıktı kolonlarını docs şekline çevir;
// `srcColMap` (kaynak tablo kolonları, isim → meta) ile isim eşleştirip
// type/concept/key miras al. Eşleşmeyen ve `expr`'siz kolonlar — kaynaklar
// dökümante ise — SQL'de hesaplanmış kabul edilir (computed: true).
function _enrichCols(cols, srcColMap) {
  const have = Object.keys(srcColMap || {}).length > 0;
  return (cols || []).map((c) => {
    const m = srcColMap && srcColMap[String(c.name).toUpperCase()];
    return {
      name: c.name,
      type: c.type || m?.type || null,
      nullable: c.nullable !== undefined ? c.nullable : true,
      key: !!(c.key || c.join_key || m?.join_key),
      concept: c.concept || m?.concept || null,
      expr: c.expr || null,
      computed: c.expr ? true : (m ? false : have),
    };
  });
}

// Sol panel bütçe göstergesi: cached (materialise edilen) tabloların tahmini
// boyut toplamı / session limiti. Lazy tablolar sayılmaz (materialise olmaz);
// türetilmiş node'lar boyutsuz (DuckDB) → 0 ekler. ≥%70 amber, >%100 kırmızı.
function BudgetPanel({ scope }) {
  // Pasif (Sunum'a gitmeyecek) alias'lar materialise edilmez → bütçeye sayma.
  // (#1: pasif büyük tablolar toplamı şişiriyordu.)
  const inactive = new Set(scope.inactive_aliases || []);
  const used = (scope.basket || []).reduce(
    (s, b) => s + (b.routing?.decision === "cached" && !inactive.has(b.alias)
      ? (b.routing?.estimated_bytes || 0) : 0),
    0,
  );
  const pct = SESSION_BUDGET_BYTES > 0 ? (used / SESSION_BUDGET_BYTES) * 100 : 0;
  const over = used > SESSION_BUDGET_BYTES;
  const level = over ? "over" : pct >= 70 ? "warn" : "ok";
  return (
    <div className={`hz-budget hz-budget--${level}`}
      title="Cached (materialise edilen) tabloların tahmini toplam boyutu. Lazy + türetilmiş node'lar sayılmaz.">
      <div className="hz-budget-row">
        <span className="hz-budget-label">Tahmini kullanım</span>
        {/* used=0 → "0 MB" (formatBytes 0'ı "—" döndürüyor; burada rakam isteniyor).
            Boyut tahminleri EXPLAIN PLAN ile arka planda dolar → değer yükselir. */}
        <span className="hz-budget-val">{used > 0 ? formatBytes(used) : "0 MB"} / {formatBytes(SESSION_BUDGET_BYTES)}</span>
      </div>
      <div className="hz-budget-bar">
        <div className="hz-budget-fill" style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      {over && <div className="hz-budget-note">⚠ Limit aşıldı — bazı tabloları lazy yap ya da filtrele.</div>}
    </div>
  );
}

// Sol aksiyon slotu: aktifken göz (→ pasifleştir), pasifken — silinebilir
// node'larda kırmızı çöp (→ sil), değilse eye-off (→ tekrar aktif et). İki
// kademeli güvenlik: bir node ancak PASİF iken silinebilir; aktif node'da
// çöp ikonu hiç görünmez.
function VizSlot({ hidden, canDelete, onToggle, onDelete }) {
  const isDelete = hidden && canDelete;
  return (
    <button
      type="button"
      className={`hz-basket-row__viz-btn${isDelete ? " is-delete" : ""}`}
      onClick={(e) => { e.stopPropagation(); (isDelete ? onDelete : onToggle)(); }}
      title={isDelete
        ? "Sil — bu tabloyu scope'tan tamamen kaldır"
        : hidden
          ? "Pasif — tıkla: tekrar aktif et"
          : "Aktif — tıkla: pasif yap (Sunum'a alma)"}
    >
      {isDelete
        ? <Trash2 size={12} strokeWidth={2} />
        : hidden
          ? <EyeOff size={12} strokeWidth={1.8} />
          : <Eye size={12} strokeWidth={1.8} />}
    </button>
  );
}

// Minimal SQL pretty-printer for the read-only "Kaynak Query" tab — the
// backend emits the query on a single line; break before major clauses and put
// each top-level (paren-depth 0) SELECT column on its own indented line so it's
// readable. Not a full parser; good enough for the SELECT/FROM/JOIN/WHERE/GROUP
// BY shapes we generate.
function formatSql(sql) {
  if (!sql) return sql;
  let s = String(sql).replace(/\s+/g, " ").trim();
  s = s.replace(
    /\s+\b(FROM|WHERE|GROUP BY|HAVING|ORDER BY|LIMIT|UNION ALL|UNION|LEFT JOIN|RIGHT JOIN|INNER JOIN|FULL JOIN|CROSS JOIN|JOIN)\b/gi,
    "\n$1",
  );
  let out = "", depth = 0;
  for (const ch of s) {
    if (ch === "(") depth++;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    if (ch === "," && depth === 0) { out += ",\n  "; continue; }
    out += ch;
  }
  return out;
}

function SourcesSidebar({
  scope, onOpenDocs, libraryBlocks, chat,
  hiddenAliases, onToggleVisibility, onRemove,
  goingToSunum, onGoToSunum, onUpload, onAddSql, onEditSql,
}) {
  // Phase 11.hazirlik-polish: sidebar shows ONLY what's in MY basket
  // (no longer the full DOMAINS tree). Split into Tablolar + Bloklar
  // with per-group search inputs, mirroring the Keşif Sepet pattern.
  const [tableSearch, setTableSearch] = useState("");
  const [blockSearch, setBlockSearch] = useState("");
  // Sürüklenebilir sidebar genişliği (Sunum'la aynı hook + .resize-handle).
  const [sidebarW, startSidebarDrag] = useResizable("hz-sidebar", 320, "right", { min: 260, max: 680 });

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
  // Bug 4 — pasifler listenin EN ALTINA iner (stable sort: kendi aralarındaki
  // sıra korunur). Aktif/pasif toggle'ı listeyi canlı yeniden sıralar.
  const sinkInactive = useCallback((arr, keyOf) => {
    return [...arr].sort((a, b) => {
      const ha = hiddenAliases?.has(keyOf(a)) ? 1 : 0;
      const hb = hiddenAliases?.has(keyOf(b)) ? 1 : 0;
      return ha - hb;
    });
  }, [hiddenAliases]);

  const derivedItems = useMemo(
    () => sinkInactive((scope.basket || []).filter((b) => b.derivation != null), (b) => b.alias),
    [scope.basket, sinkInactive],
  );

  const tablesFiltered = useMemo(() => {
    const q = tableSearch.trim().toLowerCase();
    const base = q
      ? tableItems.filter((it) =>
          it.tid.toLowerCase().includes(q)
          || (it.alias || "").toLowerCase().includes(q))
      : tableItems;
    return sinkInactive(base, (it) => it.alias);
  }, [tableItems, tableSearch, sinkInactive]);
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
    <aside className="editor-sidebar hz-sidebar" style={{ "--ts-sidebar-w": sidebarW + "px" }}>
      <div className="resize-handle resize-handle--right" onMouseDown={startSidebarDrag}
           title="Sürükle: panel genişliğini değiştir" />
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

          {/* Faz C — add datasets directly in Hazırlık. Kompakt + yan yana (#5). */}
          <div className="hz-add-row">
            <button
              type="button"
              className="ts-btn ts-btn--sm hz-add-btn"
              onClick={() => onAddSql && onAddSql()}
              title="Serbest SQL yazıp bir tablo (dataset) oluştur"
            >
              <Database size={12} /> SQL Tablo
            </button>
            <button
              type="button"
              className="ts-btn ts-btn--sm hz-add-btn"
              onClick={() => onUpload && onUpload()}
              title="Excel/CSV yükle — yüklenen sayfa bir dataset olur"
            >
              <Upload size={12} /> Excel
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
              <BudgetPanel scope={scope} />
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
                  // Katalog tabloları Keşif'ten gelir → silme burada değil
                  // (Keşife Dön). Yalnızca burada doğan manuel-SQL silinebilir.
                  const canDelete = it.isSql;
                  return (
                    <div
                      key={it.alias}
                      className={`hz-basket-row sources-table-wrap is-active${hidden ? " is-hidden" : ""}`}
                    >
                      <VizSlot
                        hidden={hidden}
                        canDelete={canDelete}
                        onToggle={() => onToggleVisibility && onToggleVisibility(it.alias)}
                        onDelete={() => onRemove && onRemove(it.alias)}
                      />
                      <button
                        type="button"
                        className="hz-basket-row__main sources-table"
                        onClick={() => onToggleVisibility && onToggleVisibility(it.alias)}
                        title={hidden ? "Pasif — tıkla: tekrar aktif et (Sunum'a al)" : "Aktif — tıkla: pasif yap (Sunum'a alma, node kararır)"}
                      >
                        <div className="sources-table-info">
                          <div className="sources-table-name">{it.name}</div>
                          <div className="sources-table-desc" title={it.catalog?.desc || ""}>
                            {it.schema}{it.catalog?.desc
                              ? ` · ${it.catalog.desc.length > 200 ? it.catalog.desc.slice(0, 200) + "…" : it.catalog.desc}`
                              : ""}
                          </div>
                        </div>
                      </button>
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
                      {/* Tablo dökümanı butonu HER ZAMAN en sağda. */}
                      <button
                        type="button"
                        className="sources-table-eye"
                        onClick={(e) => { e.stopPropagation(); onOpenDocs && onOpenDocs(it.alias); }}
                        title="Tablo dökümanını göster"
                      >
                        <Info size={12} strokeWidth={1.8} />
                      </button>
                    </div>
                  );
                })}
                {derivedItems.map((b) => {
                  const hidden = hiddenAliases?.has(b.alias) || false;
                  const dk = b.derivation?.kind;
                  const kindLabel = dk === "aggregate" ? "agregat"
                    : dk === "filter" ? "filtre"
                    : dk === "join" ? "join"
                    : dk === "union" ? "union" : "hesaplama";
                  return (
                    <div
                      key={b.alias}
                      className={`hz-basket-row sources-table-wrap is-active is-derived${hidden ? " is-hidden" : ""}`}
                    >
                      <VizSlot
                        hidden={hidden}
                        canDelete
                        onToggle={() => onToggleVisibility && onToggleVisibility(b.alias)}
                        onDelete={() => onRemove && onRemove(b.alias)}
                      />
                      <button
                        type="button"
                        className="hz-basket-row__main sources-table"
                        onClick={() => onToggleVisibility && onToggleVisibility(b.alias)}
                        title={hidden ? "Pasif — tıkla: tekrar aktif et (Sunum'a al)" : "Aktif — tıkla: pasif yap (Sunum'a alma, node kararır)"}
                      >
                        <div className="sources-table-info">
                          <div className="sources-table-name">{b.alias}</div>
                          <div className="sources-table-desc">
                            {kindLabel}
                            {b.derivation?.source_alias ? ` · ${b.derivation.source_alias}` : ""}
                          </div>
                        </div>
                      </button>
                      <button
                        type="button"
                        className="sources-table-eye"
                        onClick={(e) => { e.stopPropagation(); onOpenDocs && onOpenDocs(b.alias); }}
                        title="Tablo dökümanını göster (kaynak tablo + çıktı kolonları)"
                      >
                        <Info size={12} strokeWidth={1.8} />
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

function ChatPanel({ history, busy, error, draft, onDraftChange, onSend, onApply, onDismiss, applyingId, selectedAlias, selectedSource }) {
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
        <span>Asistan</span>
        {selectedAlias && (
          <span className="chat-scope-chip" title="Bu tablo sohbet odağında — buradan yeni node türetebilirsin">
            <Code2 size={10} /> {selectedAlias}
          </span>
        )}
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
        placeholder={selectedAlias
          ? `'${selectedAlias}' üzerinde Python ile işlem iste — örn. "kümülatif topla", "şu oranı hesapla"…`
          : "Scope hakkında soru sor…"}
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
  create_python_node: "Python node",
  edit_python_node: "Python script düzenle",
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

    case "create_python_node":
      return `'${s.source_alias}' node'undan Python ile yeni node ('${s.new_alias || s.source_alias + "_py"}'): input_node_df → output_node_df.`;
    case "edit_python_node":
      return `'${s.alias}' python node'unun script'i güncellenecek.`;
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
      {(suggestion.kind === "create_python_node" || suggestion.kind === "edit_python_node") && suggestion.python_code && (
        <div className="hz-sugg-code"><CodeArea language="python" value={suggestion.python_code} readOnly /></div>
      )}
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

// Faz P — CodeMirror tabanlı kod alanı (satır no + syntax highlight). Python
// (script editör, düzenlenebilir) ve SQL (Kaynak Query, salt-okunur) için ortak.
const _CM_LANG = { python: cmPython, sql: cmSql };
function CodeArea({ value, onChange, language = "python", readOnly = false }) {
  const extensions = useMemo(() => {
    const make = _CM_LANG[language];
    return make ? [make()] : [];
  }, [language]);
  return (
    <CodeMirror
      className="hz-cm"
      value={value || ""}
      onChange={onChange}
      editable={!readOnly}
      readOnly={readOnly}
      theme="dark"
      extensions={extensions}
      basicSetup={{
        lineNumbers: true,
        foldGutter: false,
        highlightActiveLine: !readOnly,
        highlightActiveLineGutter: !readOnly,
        autocompletion: false,
      }}
    />
  );
}

// #4 — aranabilir konsept seçici (dropdown + search).
function ConceptPicker({ value, onChange, onBrowse }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return CONCEPTS.filter((c) => !s || c.id.toLowerCase().includes(s)
      || (c.label || "").toLowerCase().includes(s)).slice(0, 60);
  }, [q]);
  const cur = CONCEPTS.find((c) => c.id === value);
  return (
    <div className="hz-concept-picker" ref={ref}>
      <button type="button" className="hz-concept-pick-btn" onClick={() => setOpen((v) => !v)}>
        <span className={cur ? "" : "hz-muted"}>{cur ? (cur.label || cur.id) : "konsept seç…"}</span>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div className="hz-concept-menu">
          <input autoFocus className="hz-concept-search" placeholder="konsept ara…"
                 value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="hz-concept-list">
            {onBrowse && (
              <button type="button" className="hz-concept-item hz-concept-item--browse"
                      onClick={() => { setOpen(false); onBrowse(); }}>
                <span><Search size={11} /> Detaylı ara…</span>
              </button>
            )}
            {value && (
              <button type="button" className="hz-concept-item is-clear"
                      onClick={() => { onChange(null); setOpen(false); }}>— kaldır —</button>
            )}
            {filtered.map((c) => (
              <button type="button" key={c.id} className="hz-concept-item"
                      onClick={() => { onChange(c.id); setOpen(false); }}>
                <span>{c.label || c.id}</span>
                <span className="hz-concept-type">{CONCEPT_TYPE_LABEL[c.type] || c.type}</span>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="hz-muted" style={{ padding: 8, fontSize: 11 }}>eşleşme yok</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// #4 — "Konseptler" sekmesi: her kolon | bağlı concept (kaynaktan izlenen ya da
// kullanıcı seçimi). Seçimden sonra distinct değerlerle uygunluk test edilir.
function ConceptsTab({ columns, colMeta, columnConcepts, onSetConcept, onValidate, onBrowse }) {
  const [valid, setValid] = useState({});   // col -> {level,message,loading}
  const tracked = (col) => columnConcepts[col] || colMeta[col]?.concept || null;
  const fromSource = (col) => !columnConcepts[col] && !!colMeta[col]?.concept;
  const lastValidated = useRef(null);  // null = henüz tohumlanmadı (mount)
  // Bir kolonun concept'ini doğrula (distinct vs tanım). Hem dropdown hem panel
  // "Seç" aşağıdaki effect üzerinden buraya düşer → #2: panel seçimi de test edilir.
  const runValidate = (col, conceptId) => {
    lastValidated.current[col] = conceptId || null;
    if (!conceptId) { setValid((v) => ({ ...v, [col]: null })); return; }
    setValid((v) => ({ ...v, [col]: { loading: true } }));
    Promise.resolve(onValidate(col, conceptId))
      .then((res) => setValid((v) => ({ ...v, [col]: res })))
      .catch((e) => setValid((v) => ({ ...v, [col]: { level: "warn", message: String(e.message || e) } })));
  };
  const setConcept = (col, conceptId) => { onSetConcept(col, conceptId); };
  // SADECE kullanıcı bir concept SEÇİNCE (dropdown VEYA panel "Seç" → columnConcepts)
  // doğrula. Mount'ta ya da kaynaktan-izlenen (colMeta) concept'lerde OTOMATİK
  // tetikleme YOK → Konseptler açılınca validate-concept seli/donması olmaz: mevcut
  // (persist) seçimler "doğrulandı" diye tohumlanır, çalıştırılmaz.
  useEffect(() => {
    if (lastValidated.current === null) {
      lastValidated.current = {};
      (columns || []).forEach((col) => { lastValidated.current[col] = columnConcepts[col] || null; });
      return;
    }
    (columns || []).forEach((col) => {
      const c = columnConcepts[col] || null;
      if (lastValidated.current[col] !== c) runValidate(col, c);
    });
  }, [columnConcepts]);  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="hz-concepts-tab ts-scroll">
      <table className="hz-concepts-table">
        <thead><tr><th>Kolon</th><th>Konsept</th></tr></thead>
        <tbody>
          {(columns || []).map((col) => {
            const t = tracked(col); const v = valid[col];
            return (
              <tr key={col}>
                <td className="hz-concepts-col" title={col}>{col}</td>
                <td>
                  <div className="hz-concepts-cell">
                    <ConceptPicker value={t} onChange={(cid) => setConcept(col, cid)}
                      onBrowse={onBrowse ? () => onBrowse(col, t) : undefined} />
                    {fromSource(col) && <span className="hz-concept-src" title="Kaynak tablonun dökümanından geldi">kaynak</span>}
                    {v?.loading && <Loader2 size={12} className="ts-spin" />}
                    {v && !v.loading && v.level === "warn" && <span className="hz-concept-warn">⚠</span>}
                    {v && !v.loading && v.level === "ok" && !v.message && <span className="hz-concept-ok">✓</span>}
                  </div>
                  {v && !v.loading && v.message && (
                    <div className={`hz-concept-msg${v.level === "warn" ? " is-warn" : ""}`}>{v.message}</div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Konsept Tarayıcı (Hazırlık side panel) ─────────────────────────────────
// "Detaylı ara" ile sağda açılır: kategori (Global/Departman/Kullanıcı) + arama
// + kart listesi + "Konsept ekle". Kart "Seç" → açık kolonun concept'ini set eder.
function conceptCategory(c) {
  const s = (c.scope || "global").toLowerCase();
  if (s === "user") return "user";
  if (s.startsWith("dept")) return "dept";
  return "global";
}
const CAT_LABEL = { global: "Global", dept: "Departman", user: "Kullanıcı" };
// Konsept türleri — kullanıcı-dostu ad + açıklama. İç kod (enum/bucket/time/scalar)
// değişmiyor (registry/derleyici onları bekler); yalnız görünen etiket + yardım.
const CONCEPT_TYPE_LABEL = { enum: "Kategori", bucket: "Aralık", time: "Tarih/Zaman", scalar: "Sayısal" };
const CONCEPT_TYPE_HELP = {
  enum: "Tablodaki farklı yazımları tek bir değere toplar. Örn: tabloda \"Dolar\", \"$\", \"USD\" geçiyorsa → hepsini tek \"USD\" değerine bağlarsın.",
  bucket: "Sayıyı aralıklara bölüp her birine ad verir. Örn: vade (gün) → 0–30: \"0-1A\", 31–90: \"1A-3A\".",
  time: "Tarih kolonu — tarihe göre filtre + gün/ay/yıl gruplama. Örn: snapshot tarihi.",
  scalar: "Düz sayı (oran, tutar, adet) — eşleme yok.",
};

function ConceptBrowser({ concepts, target, current, height, editableIds, suggest, onSelect, onClose, onAddNew, onEdit }) {
  const [cat, setCat] = useState("all");
  const [q, setQ] = useState("");
  const ref = useRef(null);
  // Dışarı tıkla → kapat (yeni-konsept modal'ı hariç; o panelin ÜSTÜNDE açılır).
  useEffect(() => {
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target) && !e.target.closest(".hz-ncm-overlay")) onClose();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [onClose]);
  const counts = useMemo(() => {
    const m = { global: 0, dept: 0, user: 0 };
    for (const c of concepts) m[conceptCategory(c)]++;
    return m;
  }, [concepts]);
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return concepts.filter((c) => {
      if (cat !== "all" && conceptCategory(c) !== cat) return false;
      if (!s) return true;
      // Semantik-ish: başlık + id + açıklama + tip üzerinde tarama.
      return [c.id, c.label, c.description, c.type].some((x) => (x || "").toLowerCase().includes(s));
    });
  }, [concepts, cat, q]);
  return (
    <aside className="hz-cbrowser" ref={ref} style={height ? { height: `${height}px` } : undefined}>
      <div className="hz-cbrowser__head">
        <span className="hz-cbrowser__title">
          <Search size={13} /> Konsept Tarayıcı
          {target ? <span className="hz-cbrowser__target">{target}</span> : null}
        </span>
        <button type="button" className="hz-icon-btn" onClick={onClose} title="Kapat"><X size={14} /></button>
      </div>
      <input className="hz-cbrowser__search" autoFocus placeholder="ara — başlık · açıklama · tip…"
             value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="hz-cbrowser__cats">
        {["all", "global", "dept", "user"].map((k) => (
          <button key={k} type="button"
                  className={`hz-cbrowser__cat${cat === k ? " on" : ""}`}
                  onClick={() => setCat(k)}>
            {k === "all" ? "Tümü" : CAT_LABEL[k]}
            {k !== "all" ? <span className="hz-cbrowser__cat-n">{counts[k]}</span> : null}
          </button>
        ))}
      </div>
      {suggest && (suggest.loading || (suggest.ranked && suggest.ranked.length > 0)) && (
        <div className="hz-cbrowser__sugg">
          <div className="hz-cbrowser__sugg-h"><Sparkles size={12} /> Bu kolona uygun</div>
          {suggest.loading ? (
            <div className="hz-cbrowser__sugg-load"><Loader2 size={12} className="hz-spin" /> aranıyor…</div>
          ) : suggest.ranked.map((s) => (
            <div key={s.id} className="hz-cbrowser__sugg-card">
              <div className="hz-cbrowser__sugg-top">
                <span className="hz-cbrowser__sugg-name" title={s.id}>{s.label}</span>
                <span className="hz-cbrowser__sugg-score" title="tahmini uyum">%{Math.round((s.score || 0) * 100)}</span>
              </div>
              <div className="hz-cbrowser__sugg-why">{s.rationale}</div>
              <button type="button" className="ts-btn ts-btn--sm ts-btn--primary hz-cbrowser__sugg-pick"
                      onClick={() => onSelect(s.id)}>
                {current === s.id ? "✓ Seçili" : "Seç"}
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="hz-cbrowser__list ts-scroll">
        {filtered.map((c) => {
          const ca = conceptCategory(c);
          return (
            <div key={c.id} className={`hz-cbrowser__card${current === c.id ? " is-sel" : ""}`}>
              <div className="hz-cbrowser__card-head">
                <span className="hz-cbrowser__card-name" title={c.id}>{c.label || c.id}</span>
                <span className="hz-cbrowser__card-type">{CONCEPT_TYPE_LABEL[c.type] || c.type}</span>
              </div>
              <div className="hz-cbrowser__card-meta">
                <span className={`hz-cbrowser__scope hz-cbrowser__scope--${ca}`}>{CAT_LABEL[ca]}</span>
                <code className="hz-cbrowser__card-id">{c.id}</code>
              </div>
              {c.description && <div className="hz-cbrowser__card-desc">{c.description}</div>}
              <div className="hz-cbrowser__card-actions">
                {editableIds && editableIds[c.id] && onEdit && (
                  <button type="button" className="ts-btn ts-btn--sm" onClick={() => onEdit(c.id)}
                          title="Bu ekrandan eklediğin konsepti düzenle">
                    <Pencil size={12} /> Düzenle
                  </button>
                )}
                <button type="button" className="ts-btn ts-btn--sm ts-btn--primary"
                        onClick={() => onSelect(c.id)}>
                  {current === c.id ? "✓ Seçili" : "Seç"}
                </button>
              </div>
            </div>
          );
        })}
        {filtered.length === 0 && <div className="hz-cbrowser__empty">Eşleşen konsept yok.</div>}
      </div>
      <div className="hz-cbrowser__foot">
        <button type="button" className="ts-btn ts-btn--sm hz-cbrowser__add"
                onClick={() => onAddNew(suggest && suggest.draft)}
                title={target ? `'${target}' kolonuna göre ön-dolu açılır` : undefined}>
          <Plus size={13} /> Konsept ekle{suggest && suggest.draft ? " (öneri dolu)" : ""}
        </button>
      </div>
    </aside>
  );
}

// Yeni/var-olan konsept tanımlama modal'ı — Kütüphane formunun React karşılığı.
// `initial` verilirse DÜZENLEME modu (update endpoint), yoksa OLUŞTURMA (create).
// Aynı /atolye/konseptler/{create,update} endpoint'lerine POST eder (registry'ye
// yazar, ~2s reload). Başarılıysa onSaved(summary, form, isEdit) ile döner.
function NewConceptModal({ initial, isEdit = false, onClose, onSaved }) {
  const [id, setId] = useState(initial?.id || "");
  const [name, setName] = useState(initial?.name || "");
  const [type, setType] = useState(initial?.type || "enum");
  const [scope, setScope] = useState(initial?.scope || "user");
  const [desc, setDesc] = useState(initial?.description || "");
  const [rows, setRows] = useState(initial?.rows || []);  // Kategori: {code,aliases}; Aralık: {label,lo,hi}
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const isBucket = type === "bucket";
  const needsCanon = type === "enum" || type === "bucket";
  const valid = /^[a-z][a-z0-9_]*$/.test(id.trim()) && name.trim();
  // Tür değişince değer satırlarını sıfırla (Kategori ↔ Aralık kolonları farklı).
  const onType = (t) => { setType(t); setRows([]); };
  const addRow = () => setRows((r) => [...r, isBucket ? { label: "", lo: "", hi: "" } : { code: "", aliases: "" }]);
  const updRow = (i, k, v) => setRows((r) => r.map((row, j) => (j === i ? { ...row, [k]: v } : row)));
  const delRow = (i) => setRows((r) => r.filter((_, j) => j !== i));
  // Satırlardan canonical_values üret. Kategori: konsept değeri = code = label,
  // tablodaki karşılıklar = aliases. Aralık: etiket + [alt, üst] → day_range.
  const buildCanon = () => isBucket
    ? rows.filter((r) => (r.label || "").trim()).map((r) => ({
        code: r.label.trim(), label: r.label.trim(),
        day_range: [r.lo === "" || r.lo == null ? null : Number(r.lo), r.hi === "" || r.hi == null ? null : Number(r.hi)],
      }))
    : rows.filter((r) => (r.code || "").trim()).map((r) => ({
        code: r.code.trim(), label: r.code.trim(),
        aliases: (r.aliases || "").split(",").map((a) => a.trim()).filter(Boolean),
      }));
  const submit = async () => {
    setBusy(true); setErr(null);
    const canonical_values = needsCanon ? buildCanon() : [];
    try {
      const r = await fetch(isEdit ? CONCEPT_UPDATE_URL : CONCEPT_CREATE_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: id.trim(), name: name.trim(), type, scope, description: desc.trim(), canonical_values }),
      });
      const data = await r.json();
      if (!data.ok) { setErr((data.errors || [data.error || "Kaydedilemedi"]).join("; ")); return; }
      const summary = {
        id: data.id || id.trim(), label: name.trim(), type,
        ops: type === "time" ? ["between", "last_n_days", "eq"] : (needsCanon ? ["in", "not_in", "eq"] : ["eq", "between"]),
        canonical_values: canonical_values.map((c) => c.code),
        scope: data.scope || scope, description: desc.trim(),
      };
      const form = { id: id.trim(), name: name.trim(), type, scope: data.scope || scope, description: desc.trim(), rows };
      onSaved(summary, form, isEdit);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="hz-ncm-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="hz-ncm-modal" role="dialog" aria-modal="true">
        <h3 className="hz-ncm-title">{isEdit ? "Konsepti düzenle" : "Yeni konsept tanımla"}</h3>
        {err && <div className="hz-ncm-err">{err}</div>}
        <div className="hz-ncm-field">
          <label>Kimlik (lower_snake, benzersiz)</label>
          <input type="text" value={id} onChange={(e) => setId(e.target.value)} readOnly={isEdit}
                 placeholder="ör. counterparty_segment" />
        </div>
        <div className="hz-ncm-field">
          <label>Ad</label>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="ör. Karşı Taraf Segmenti" />
        </div>
        <div className="hz-ncm-row">
          <div className="hz-ncm-field">
            <label>Tür</label>
            <select value={type} onChange={(e) => onType(e.target.value)}>
              <option value="enum">Kategori</option>
              <option value="bucket">Aralık</option>
              <option value="time">Tarih/Zaman</option>
              <option value="scalar">Sayısal</option>
            </select>
          </div>
          <div className="hz-ncm-field">
            <label>Kapsam</label>
            <select value={scope} onChange={(e) => setScope(e.target.value)} disabled={isEdit}>
              <option value="user">Kullanıcı</option>
              <option value="global">Global</option>
            </select>
          </div>
        </div>
        <div className="hz-ncm-help">{CONCEPT_TYPE_HELP[type]}</div>
        <div className="hz-ncm-field">
          <label>Açıklama</label>
          <input type="text" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="kısa tanım (opsiyonel)" />
        </div>
        {needsCanon && (
          <div className="hz-ncm-field">
            <label>{isBucket ? "Aralıklar" : "Değer eşlemeleri"}</label>
            <table className="hz-ncm-table">
              <thead>
                <tr>
                  {isBucket
                    ? (<><th>Etiket</th><th>Alt</th><th>Üst</th></>)
                    : (<><th>Konsept değeri</th><th>Tablodaki karşılık(lar)</th></>)}
                  <th aria-label="sil"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    {isBucket ? (
                      <>
                        <td><input value={r.label || ""} onChange={(e) => updRow(i, "label", e.target.value)} placeholder="1A-3A" /></td>
                        <td><input value={r.lo || ""} onChange={(e) => updRow(i, "lo", e.target.value)} placeholder="31" /></td>
                        <td><input value={r.hi || ""} onChange={(e) => updRow(i, "hi", e.target.value)} placeholder="90" /></td>
                      </>
                    ) : (
                      <>
                        <td><input value={r.code || ""} onChange={(e) => updRow(i, "code", e.target.value)} placeholder="USD" /></td>
                        <td><input value={r.aliases || ""} onChange={(e) => updRow(i, "aliases", e.target.value)} placeholder="Dolar, $, Dollar" /></td>
                      </>
                    )}
                    <td><button type="button" className="hz-ncm-rowdel" onClick={() => delRow(i)} title="Satırı sil"><X size={12} /></button></td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr><td className="hz-ncm-table-empty" colSpan={isBucket ? 4 : 3}>Henüz satır yok — "Satır ekle" ile başla.</td></tr>
                )}
              </tbody>
            </table>
            <button type="button" className="hz-ncm-addrow" onClick={addRow}><Plus size={12} /> Satır ekle</button>
            <div className="hz-ncm-sub">{isBucket
              ? "Etiket zorunlu; alt/üst boş bırakılabilir (açık uç). Sayı = gün/birim."
              : "Konsept değeri zorunlu; tablodaki karşılıkları virgülle ayır."}</div>
          </div>
        )}
        <div className="hz-ncm-actions">
          <button type="button" onClick={onClose}>Vazgeç</button>
          <button type="button" className="primary" disabled={!valid || busy} onClick={submit}>
            {busy ? (isEdit ? "Kaydediliyor…" : "Oluşturuluyor…") : (isEdit ? "Kaydet" : "+ Oluştur")}
          </button>
        </div>
      </div>
    </div>
  );
}

// Faz P — bir python node'unun "Kaynak Script" sekmesi. Giriş bağlama satırı
// salt-okunur (input_node_df = bağlı tablo); kod alanı düzenlenir; sağ altta
// Çalıştır (output_node_df kontrolü + örnek satır) ve Kaydet.
function PythonScriptTab({ item, sourceAlias, onRun, onSave, run }) {
  const [code, setCode] = useState(item?.derivation?.python_code || "");
  // Node değişince VE kod dışarıdan değişince (LLM edit_python_node) senkronla.
  useEffect(() => { setCode(item?.derivation?.python_code || ""); }, [item?.alias, item?.derivation?.python_code]);
  const running = !!run?.running;
  return (
    <div className="hz-py-tab ts-scroll">
      <div className="hz-py-bind" title="Bu node'un girişi — bağlandığı tablonun verisi. Düzenlenemez.">
        <Lock size={12} /> <code>input_node_df&nbsp;←&nbsp;{sourceAlias || "?"}</code>
      </div>
      <div className="hz-py-code">
        <CodeArea language="python" value={code} onChange={setCode} />
      </div>
      {run?.error && <pre className="hz-error hz-py-error">{run.error}</pre>}
      {run?.summary && !run?.error && <p className="hz-py-ok">{run.summary} — örnek <strong>Veri</strong> sekmesinde.</p>}
      <div className="hz-py-actions">
        <button className="ts-btn ts-btn--sm" onClick={() => onSave(code)}
                title="Script'i bu node'a kaydet">
          <Save size={13} /> Kaydet
        </button>
        <button className="ts-btn ts-btn--primary ts-btn--sm" disabled={running}
                onClick={() => onRun(code)}
                title="Script'i çalıştır — output_node_df kontrol edilir, örnek satırlar Veri sekmesine gelir">
          {running ? <Loader2 size={13} className="ts-spin" /> : <Play size={13} />} Çalıştır
        </button>
      </div>
    </div>
  );
}

function PreviewDrawer({ preview, loading, height, onResizeStart, onClose, onSaveFilters, onSaveAsTable, onGridReady, savedGridState, previewLabel, onSaveFilterPanel, onFetchDistinct, existingFilters, item, onSaveRefresh, onDelete, onRename, onCreatePython, onRunPython, onSavePython, pythonRun, onSetConcept, onValidateConcept, onOpenConceptBrowser }) {
  const apiRef = useRef(null);
  const filterSaveRef = useRef(null);
  const [tab, setTab] = useState("data");
  // Kolon filtreleme (Enterprise sidebar yerine kendi panelimiz): gizlenen kolonlar.
  const [hiddenCols, setHiddenCols] = useState(() => new Set());
  const [colMenuOpen, setColMenuOpen] = useState(false);
  const colMenuRef = useRef(null);
  // Dışarı tıkla → menüyü kapat (seçimler zaten canlı uygulanıyor → "kaydedip
  // kapanır" + tablo değerlerini bloklamaz, #2).
  useEffect(() => {
    if (!colMenuOpen) return;
    const onDown = (e) => {
      if (colMenuRef.current && !colMenuRef.current.contains(e.target)) setColMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [colMenuOpen]);
  const [cronDraft, setCronDraft] = useState(null);   // RefreshFields'ten gelen DatasetRefresh
  // Tablo adı (alias) inline düzenleme — yalnız üretilmiş node'larda.
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");

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
    hide: hiddenCols.has(c),
    headerComponent: ConceptHeader,
    headerComponentParams: { concept: conceptByCol[c] || null },
  })), [preview?.data_columns, conceptByCol, hiddenCols]);

  const rowData = useMemo(() => {
    if (!preview?.rows) return [];
    const cols = preview.data_columns || [];
    return preview.rows.map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])));
  }, [preview]);

  const isDerived = !!preview?.derived;
  const isFilter = !!preview?.isFilter;   // Faz R1/F3 — filter-node (query tab'lı)
  const isPython = item?.derivation?.kind === "python";   // Faz P — script node
  const pySource = item?.derivation?.source_alias || null;
  // "Kaynak Query" tab — artık tüm SQL döndüren türetilmişlerde (filter +
  // join/union/calculated/aggregate), yalnız filter'da değil.
  const hasQuery = !!(preview && preview.sql);
  // Cron yalnız cached dataset'lerde: türetilmiş (filter/aggregate/calculated)
  // veya manuel SQL node. Main lazy tablolarda cron yok.
  const canCron = isDerived || !!(item && item.sql);
  // Sil + ad düzenle yalnız ÜRETİLMİŞ node'larda (manuel SQL / türetilmiş).
  // Katalog main tabloları Keşif'ten yönetilir; adları gerçek tablo adıdır.
  const produced = !!(item && (item.sql || item.derivation));
  // Node değişince tab'ı + ad düzenlemeyi sıfırla; edge-click "filter" tab'ı isteyebilir.
  useEffect(() => { setTab(preview?.openTab || "data"); setCronDraft(null); setEditingName(false); setHiddenCols(new Set()); setColMenuOpen(false); }, [preview?.alias]);

  const startRename = () => { setNameDraft(preview?.alias || ""); setEditingName(true); };
  const commitRename = () => {
    const v = (nameDraft || "").trim();
    setEditingName(false);
    if (v && v !== preview?.alias && onRename) onRename(preview.alias, v);
  };
  return (
    <div className="hz-preview" style={{ height }}>
      <div className="hz-preview-resize" onMouseDown={onResizeStart} title="Sürükle: yükseklik" />
      <div className="hz-preview-head">
        <span className="hz-preview-title">
          <Database size={14} /> Önizleme ·{" "}
          {editingName ? (
            <input
              className="hz-preview-rename"
              autoFocus
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitRename();
                else if (e.key === "Escape") setEditingName(false);
              }}
              onBlur={commitRename}
              title="Yeni tablo adı (snake_case'e çevrilir, 40 karaktere kadar)"
            />
          ) : (
            <>
              <strong>{previewLabel}</strong>
              {produced && (
                <button className="hz-preview-rename-btn" onClick={startRename}
                        title="Tablo adını düzenle">
                  <Pencil size={12} strokeWidth={2} />
                </button>
              )}
            </>
          )}
          {isDerived ? " · türetilmiş (örnek)" : ""}
          {preview && preview.row_count != null ? ` (${preview.row_count} satır)` : ""}
        </span>
        <div className="hz-preview-actions">
          {tab === "filter" && (
            <button className="ts-btn ts-btn--sm" disabled={!preview || preview.error}
                    onClick={() => filterSaveRef.current && filterSaveRef.current()}
                    title="Filtreleri scope'a yaz (boyut yeniden hesaplanır)">
              <Save size={13} /> Filtreyi kaydet
            </button>
          )}
          {tab === "data" && (() => {
            // #1 — Python YALNIZ cache'li (materialised) veride çalışır: lazy/main
            // tabloda veri cache'lenmediğinden örnekleyip python koşamayız. Uygun:
            // türetilmiş node'lar (zaten materialised) + cache'li main + SQL dataset.
            const canPy = !!item && (item.derivation != null || item.sql != null || item.routing?.decision === "cached");
            return canPy ? (
              <button className="ts-btn ts-btn--sm hz-py-create" disabled={!preview || preview.error}
                      onClick={() => onCreatePython && onCreatePython(preview.alias)}
                      title="Bu tablodan Python ile yeni bir node üret (input_node_df → output_node_df)">
                <Code2 size={13} /> <ArrowRight size={12} /> <Table2 size={13} /> Python
              </button>
            ) : (
              <span className="hz-py-blocked"
                    title="Bu main tablo cache'li değil (lazy) — veriyi cache'lemediğimiz için python çalıştıramayız. Node'u cached yap, sonra Python üret. (Lazy tablo için SQL → Tablo kullanılabilir.)">
                <Lock size={12} /> Python: tablo cache'li değil
              </span>
            );
          })()}
          {tab === "data" && (
            <div className="hz-colmenu-wrap" ref={colMenuRef}>
              <button className="ts-btn ts-btn--sm" disabled={!preview || preview.error}
                      onClick={() => setColMenuOpen((v) => !v)}
                      title="Kolonları seç — kaldırdıkların yeni tabloda olmaz">
                <Columns3 size={13} /> Kolonlar
                {hiddenCols.size > 0 ? ` (${(preview?.data_columns || []).length - hiddenCols.size}/${(preview?.data_columns || []).length})` : ""}
              </button>
              {colMenuOpen && (
                <div className="hz-colmenu">
                  <div className="hz-colmenu-head">
                    <button onClick={() => setHiddenCols(new Set())}>Tümü</button>
                    <button onClick={() => setHiddenCols(new Set(preview?.data_columns || []))}>Hiçbiri</button>
                  </div>
                  <div className="hz-colmenu-list">
                    {(preview?.data_columns || []).map((c) => (
                      <label key={c} className="hz-colmenu-item">
                        <input type="checkbox" checked={!hiddenCols.has(c)}
                               onChange={() => setHiddenCols((s) => {
                                 const n = new Set(s);
                                 if (n.has(c)) n.delete(c); else n.add(c);
                                 return n;
                               })} />
                        <span>{c}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
          {tab === "data" && (
            <button className="ts-btn ts-btn--sm" disabled={!preview || preview.error}
                    onClick={() => onSaveAsTable(preview.data_columns.filter((c) => !hiddenCols.has(c)))}
                    title="Seçili kolonlardan yeni bir (kolon-filtreli) tablo oluştur">
              <Database size={13} /> Tablo olarak kaydet
            </button>
          )}
          {produced && (
            <button className="ts-btn ts-btn--sm ts-btn--danger" disabled={!preview}
                    onClick={() => onDelete && onDelete(preview.alias)}
                    title="Bu tabloyu scope'tan tamamen kaldır">
              <Trash2 size={13} /> Sil
            </button>
          )}
          <button className="hz-icon-btn" onClick={onClose}><X size={15} /></button>
        </div>
      </div>
      <div className="hz-preview-body">
        {loading && <p className="hz-muted" style={{ padding: 10 }}>Yükleniyor…</p>}
        {!loading && preview && preview.error && !isPython && (
          <div className="hz-preview-errbox">
            <p className="hz-error" style={{ margin: 10 }}>{preview.error}</p>
            {preview.sql && (
              <div className="hz-err-sql">
                <div className="hz-err-sql-label">Üreten sorgu (kaynak):</div>
                <div className="hz-sql-cm"><CodeArea language="sql" value={preview.sql} readOnly /></div>
              </div>
            )}
          </div>
        )}
        {!loading && preview && (!preview.error || isPython) && (
          <div className="hz-preview-inner">
            <div className="hz-preview-tabs">
              <button type="button" className={tab === "data" ? "on" : ""} onClick={() => setTab("data")}>Veri</button>
              <button type="button" className={tab === "filter" ? "on" : ""} onClick={() => setTab("filter")}>Filtreleme</button>
              {(preview.data_columns || []).length > 0 && (
                <button type="button" className={tab === "concepts" ? "on" : ""} onClick={() => setTab("concepts")}>Konseptler</button>
              )}
              {isPython && (
                <button type="button" className={tab === "script" ? "on" : ""} onClick={() => setTab("script")}>Kaynak Script</button>
              )}
              {hasQuery && (
                <button type="button" className={tab === "query" ? "on" : ""} onClick={() => setTab("query")}>Kaynak</button>
              )}
              {canCron && (
                <button type="button" className={tab === "cron" ? "on" : ""} onClick={() => setTab("cron")}>Cron</button>
              )}
            </div>
            <div className="hz-preview-pane">
              {canCron && tab === "cron" ? (
                <div className="hz-cron-tab ts-scroll" style={{ padding: 10 }}>
                  <RefreshFields value={item?.refresh} onChange={setCronDraft} />
                  <div style={{ marginTop: 12 }}>
                    <button className="ts-btn ts-btn--primary ts-btn--sm"
                            onClick={() => onSaveRefresh && onSaveRefresh(preview.alias, cronDraft || item?.refresh || { kind: "manual" })}>
                      <Save size={13} /> Cron kaydet
                    </button>
                  </div>
                  <p className="hz-muted" style={{ fontSize: 11, marginTop: 8 }}>
                    Bu cached dataset ne sıklıkla tazelensin — Oracle'dan yeniden çekilip parquet'e yazılır.
                    (Main lazy tablolarda cron yok; sadece türetilmiş/SQL dataset'lerde.)
                  </p>
                </div>
              ) : hasQuery && tab === "query" ? (
                <div className="hz-filter-sql ts-scroll">
                  <div className="hz-sql-cm"><CodeArea language="sql" value={preview.sql ? formatSql(preview.sql) : "—"} readOnly /></div>
                  <p className="hz-muted" style={{ padding: "6px 10px", fontSize: 11 }}>
                    {isFilter
                      ? <>Bu, dataset'i materialise eden Oracle sorgusu (relative tarihler her
                          materialize'da dinamik çözülür). Filtreyi düzenlemek için mor edge'e
                          tıkla ya da kaynak (main) node'u seçip <strong>Filtreleme</strong>'den değiştir.</>
                      : (item && item.table_ref)
                        ? <>Bu node'un kaynağı: Oracle'dan bu sorguyla çekilir (projeksiyon + pinned
                            filtreler + partition pushdown). Build/cron her tazelemede bunu koşar.</>
                      : (item && item.sql)
                        ? <>Bu, kullanıcı tarafından yazılan kaynak Oracle sorgusu. Düzenlemek için
                            sol menüden <strong>kalem</strong> ikonuna tıkla.</>
                      : <>Bu, türetilmiş tabloyu üreten sorgu — kaynak view'ler üzerinde DuckDB'de
                          çalışır. Build/cron sırasında tam veri üzerinde yeniden koşar.</>}
                  </p>
                </div>
              ) : tab === "concepts" ? (
                <ConceptsTab
                  columns={preview.data_columns || []}
                  colMeta={Object.fromEntries((COLS_BY_ALIAS[preview.alias] || []).map((c) => [c.name, c]))}
                  columnConcepts={(item && item.column_concepts) || {}}
                  onSetConcept={(col, cid) => onSetConcept && onSetConcept(preview.alias, col, cid)}
                  onValidate={(col, cid) => onValidateConcept(preview.alias, col, cid)}
                  onBrowse={(col, cur) => onOpenConceptBrowser && onOpenConceptBrowser(preview.alias, col, cur)}
                />
              ) : isPython && tab === "script" ? (
                <PythonScriptTab
                  item={item}
                  sourceAlias={pySource}
                  run={pythonRun && pythonRun.alias === preview.alias ? pythonRun : null}
                  onRun={(code) => onRunPython && onRunPython(preview.alias, code)}
                  onSave={(code) => onSavePython && onSavePython(preview.alias, code)}
                />
              ) : (tab === "filter") ? (
                <FilterPanel
                  alias={preview.alias}
                  columns={COLS_BY_ALIAS[preview.alias] || []}
                  existing={existingFilters}
                  derivedSource={isDerived}
                  saveRef={filterSaveRef}
                  onSave={(specs) => onSaveFilterPanel && onSaveFilterPanel(preview.alias, specs)}
                  onFetchDistinct={(column) => onFetchDistinct(preview.alias, column)}
                />
              ) : preview.error ? (
                <div className="hz-preview-errbox">
                  <p className="hz-error" style={{ margin: 10 }}>{preview.error}</p>
                  {isPython && (
                    <p className="hz-muted" style={{ margin: "0 10px", fontSize: 11 }}>
                      <strong>Kaynak Script</strong> sekmesinden düzeltip Çalıştır'a bas.
                    </p>
                  )}
                </div>
              ) : (
                <div className="ag-theme-alpine-dark" style={{ width: "100%", height: "100%" }}>
                  <AgGridReact
                    columnDefs={colDefs} rowData={rowData} animateRows
                    onGridReady={handleReady}
                    onFirstDataRendered={handleFirstDataRendered}
                    headerHeight={36}
                    pagination
                    paginationPageSize={50}
                    paginationPageSizeSelector={[25, 50, 100, 200]}
                  />
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
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

// Hazırlık başlık şeridi — Keşif'teki WorkshopHeader'ın karşılığı. Sunum adını
// (meta.title) debounced auto-save eder + pid'i gösterir + Kaydet (başlık +
// scope draft flush, updated_at bump). PRISMA shell topbar'ın hemen altında.
function WorkshopHeader({ title, pid, saving, toast, onChange, onSave }) {
  return (
    <header className="hz-workshop-header">
      <div className="hz-workshop-header__left">
        <input
          type="text"
          className="hz-workshop-header__title"
          value={title || ""}
          placeholder="Adsız hazırlık"
          onChange={(e) => onChange(e.target.value)}
          maxLength={200}
          aria-label="Sunum adı"
          spellCheck={false}
        />
        <span className="hz-workshop-header__pid" title={pid || ""}>
          {pid ? `· ${(pid + "").slice(0, 24)}` : ""}
        </span>
      </div>
      <div className="hz-workshop-header__right">
        {toast && (
          <span
            className={`hz-workshop-header__toast${toast === "Kaydedildi" ? "" : " is-error"}`}
            aria-live="polite"
          >
            {toast}
          </span>
        )}
        <button
          type="button"
          className="ts-btn ts-btn--primary hz-workshop-header__save"
          onClick={onSave}
          disabled={saving}
          title="Sunumu şu anki haliyle kaydet"
        >
          {saving ? <Loader2 size={13} className="ts-spin" /> : <Save size={13} />}
          <span>Kaydet</span>
        </button>
      </div>
    </header>
  );
}

function App() {
  const [scope, setScope] = useState(DATA.scope);
  const [joinModal, setJoinModal] = useState(null);
  const [unionModal, setUnionModal] = useState(null);
  const [docsTable, setDocsTable] = useState(null);   // 8.c — Eye icon → schema modal
  const [uploadOpen, setUploadOpen] = useState(false); // Polish-4 — Veri Yükle
  const [sqlModalOpen, setSqlModalOpen] = useState(false); // Faz C — Manuel SQL Tablo
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  // Faz P — python node "Çalıştır" durumu: { alias, running, error, summary, columns }.
  const [pythonRun, setPythonRun] = useState(null);
  // Konsept tarayıcı side panel ("Detaylı ara") + yeni-konsept modal.
  const [conceptBrowser, setConceptBrowser] = useState(null);  // {alias, column, current}
  const [conceptModal, setConceptModal] = useState(null);  // {initial, isEdit} | null → yeni / taslak / düzenle
  const [conceptList, setConceptList] = useState(CONCEPTS);
  const [createdForms, setCreatedForms] = useState({});   // bu oturumda üretilen konsept form'ları (id → form) → buradan editlenebilir
  // MVP — "Detaylı ara" açılınca kolona göre öneri (sıralı mevcut + yeni taslak).
  const [conceptSuggest, setConceptSuggest] = useState(null);  // {loading, ranked, draft, column} | null
  // #1 — preview drawer kapanınca (canvas/pane tıkı, X) konsept panelini de kapat.
  useEffect(() => { if (!preview) setConceptBrowser(null); }, [preview]);
  // MVP — konsept tarayıcı açılınca kolona göre öneri çek (deterministik backend).
  // Deps yalnız alias+column: seçim (current değişimi) yeniden çekmesin.
  const conceptSuggestCacheRef = useRef({});
  useEffect(() => {
    if (!conceptBrowser) { setConceptSuggest(null); return; }
    const { alias, column } = conceptBrowser;
    // E4: aynı (alias,column) tekrar açılınca anında cache'ten ver (Oracle'a gitme);
    // ilk açılışta 8s AbortController timeout → spinner sonsuz dönmesin (alttaki
    // tam liste zaten suggest'e bağlı değil, kullanılabilir kalır).
    const ckey = `${alias}::${column}`;
    const cached = conceptSuggestCacheRef.current[ckey];
    if (cached) { setConceptSuggest({ ...cached, column }); return; }
    let alive = true;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    setConceptSuggest({ loading: true, ranked: [], draft: null, column });
    fetch(SUGGEST_CONCEPTS_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, alias, column }), signal: ctrl.signal,
    })
      .then((r) => r.json())
      .then((d) => {
        if (!alive) return;
        const res = d && d.ok
          ? { loading: false, ranked: d.ranked_existing || [], draft: d.draft_new || null }
          : { loading: false, ranked: [], draft: null };
        conceptSuggestCacheRef.current[ckey] = res;
        setConceptSuggest({ ...res, column });
      })
      .catch(() => { if (alive) setConceptSuggest({ loading: false, ranked: [], draft: null, column }); })
      .finally(() => clearTimeout(timer));
    return () => { alive = false; clearTimeout(timer); ctrl.abort(); };
  }, [conceptBrowser?.alias, conceptBrowser?.column]);  // eslint-disable-line react-hooks/exhaustive-deps
  const [drawerH, setDrawerH] = useState(260);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [toast, setToast] = useState(null);
  const [gridStateByAlias, setGridStateByAlias] = useState({}); // per-alias AG Grid state (filters + columns), restored on re-open
  const gridApiRef = useRef(null);

  // Başlık şeridi (Keşif WorkshopHeader karşılığı). Başlık meta.title'a
  // debounced patch ile yazılır; Kaydet başlığı + scope draft'ı flush eder.
  const [workshopTitle, setWorkshopTitle] = useState(DATA.title || "");
  const [titleSaving, setTitleSaving] = useState(false);
  const [savedToast, setSavedToast] = useState("");
  const titleDebounceRef = useRef(null);

  const saveTitle = useCallback(async (next) => {
    setTitleSaving(true);
    try {
      const r = await fetch(PATCH_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patches: [{
          op: "replace", path: "/meta/title", value: (next || "").trim() || PID,
        }] }),
      });
      if (!r.ok) throw new Error(await r.text());
    } catch (e) {
      console.warn("hazırlık başlık kaydı başarısız:", e);
    } finally {
      setTitleSaving(false);
    }
  }, []);

  const onTitleChange = useCallback((next) => {
    setWorkshopTitle(next);
    if (titleDebounceRef.current) clearTimeout(titleDebounceRef.current);
    titleDebounceRef.current = setTimeout(() => saveTitle(next), 600);
  }, [saveTitle]);

  const onSaveWorkshop = useCallback(async () => {
    if (titleDebounceRef.current) { clearTimeout(titleDebounceRef.current); titleDebounceRef.current = null; }
    setTitleSaving(true);
    let okSave = true;
    try {
      await saveTitle(workshopTitle);
      // Scope draft'ı da flush et (debounced auto-save'i beklemeden).
      const r = await fetch(SAVE_DRAFT_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope }),
      });
      if (!r.ok) okSave = false;
    } catch (e) {
      okSave = false;
    } finally {
      setTitleSaving(false);
      setSavedToast(okSave ? "Kaydedildi" : "Kaydedilemedi");
      setTimeout(() => setSavedToast(""), 2000);
    }
  }, [saveTitle, workshopTitle, scope]);

  const [nodes, setNodes, onNodesChange] = useNodesStateCompat(() => initialNodes(DATA.scope));
  // Phase 11.hazirlik-polish-2: alias visibility on the canvas. The
  // sidebar's "Tablolar" rows toggle entries in this set; the nodes
  // reconciliation effect applies it to each node's `hidden` flag.
  // React Flow drops edges connected to hidden nodes automatically.
  // Faz R/B — pasif (Sunum'a gitmeyecek) alias'lar scope'ta kalıcı tutulur.
  // Sol menüden tıklayınca aktif/pasif olur; pasif node canvas'ta kararır
  // (gizlenmez); build yalnız aktifleri Sunum'a alır.
  const inactiveAliases = useMemo(() => new Set(scope.inactive_aliases || []), [scope.inactive_aliases]);
  const toggleAliasVisibility = useCallback((alias) => {
    setScope((s) => {
      const set = new Set(s.inactive_aliases || []);
      if (set.has(alias)) set.delete(alias); else set.add(alias);
      return { ...s, inactive_aliases: [...set] };
    });
  }, []);
  const edges = useMemo(() => buildEdges(scope), [scope]);

  // Bug 2 (F5 düzen kaybı) — sürükleme bitince pozisyonu scope.basket[].layout'a
  // yaz; debounced save-draft persist eder, reload initialNodes'ta geri okur.
  // Önceden layout YALNIZ build'de (_finalisedScope) yazılıyordu → build'den
  // önce atılan her F5 node'ları grid dizilimine düşürüyordu. Çoklu seçim
  // sürüklemesinde üçüncü argüman tüm taşınan node'ları getirir.
  const onNodeDragStop = useCallback((_e, node, draggedNodes) => {
    const moved = (draggedNodes && draggedNodes.length ? draggedNodes : [node]).filter(Boolean);
    if (!moved.length) return;
    const pos = Object.fromEntries(moved.map((n) => [n.id, n.position]));
    setScope((s) => ({
      ...s,
      basket: (s.basket || []).map((b) => (pos[b.alias]
        ? { ...b, layout: { x: pos[b.alias].x, y: pos[b.alias].y } }
        : b)),
    }));
  }, []);

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
    // Türetilmiş node kolonlarını bağımlılık sırasıyla doldur — `kept` dalı
    // (reload'da tüm node'lar burada) doğru colCount okusun. Eski "yalnız yeni
    // eklenende doldur" mantığı reload'da atlanıp "0 kolon" gösteriyordu.
    hydrateDerivedCols(scope.basket);
    setNodes((nds) => {
      const aliasesInScope = new Set(scope.basket.map((b) => b.alias));
      const kept = nds
        .filter((n) => aliasesInScope.has(n.id))
        .map((n) => {
          const item = scope.basket.find((b) => b.alias === n.id);
          // Faz R/B — pasif node GİZLENMEZ, kararır (data.inactive → TableNode dim).
          const inactive = inactiveAliases.has(n.id);
          return item
            ? { ...n, hidden: false, data: { ...enrichNodeData(item, scope), inactive } }
            : { ...n, hidden: false };
        });
      const known = new Set(kept.map((n) => n.id));
      const added = [];
      scope.basket.forEach((item) => {
        if (known.has(item.alias)) return;
        // Konum önceliği: (1) item.layout (saveAsTable/python/join/union yazıyor),
        // (2) üretilmiş node ise KAYNAĞININ yanına (chat-apply'de layout yok),
        // (3) son çare grid. Eskiden hep grid'di → üretilen node uzağa düşüyordu (#4).
        let pos = null;
        if (item.layout && Number.isFinite(item.layout.x)) {
          pos = { x: item.layout.x, y: item.layout.y };
        } else if (item.derivation) {
          const srcs = derivSourceAliases(item.derivation);
          const srcNode = srcs
            .map((a) => [...kept, ...added].find((n) => n.id === a))
            .find(Boolean);
          if (srcNode && srcNode.position) {
            pos = { x: srcNode.position.x + 360, y: srcNode.position.y + 120 };
          }
        }
        if (!pos) {
          // #7 — grid son çaresinde PASİF node'ları sayma → yeni node'lar aktif
          // bölgeye (disabled node'ların üstüne) gelir, en alta yığılmaz.
          const activeCount = kept.filter((n) => !inactiveAliases.has(n.id)).length
            + added.filter((n) => !inactiveAliases.has(n.id)).length;
          pos = { x: 100 + (activeCount % 3) * 340, y: 100 + Math.floor(activeCount / 3) * 240 };
        }
        added.push({
          id: item.alias, type: "tableNode", position: pos, hidden: false,
          data: { ...enrichNodeData(item, scope), inactive: inactiveAliases.has(item.alias) },
        });
      });
      return [...kept, ...added];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, inactiveAliases]);

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
        // Faz P — açık node varsa node-scope: LLM o node'dan create_python_node önerebilir.
        body: JSON.stringify({ scope, message, history: historyPayload, selected_alias: preview?.alias || null }),
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
  }, [scope, chatHistory, preview]);

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

  // Bir alias + ondan TÜREYEN tüm kalemleri (transitif) topla. Bir filter
  // node'unu silmek ondan türeyen agregatı öksüz bırakacağından birlikte
  // gitmeli. derived_from (SQL lineage) sert bağımlılık değildir (SQL kendi
  // içinde tamdır), o yüzden cascade DIŞIdır.
  const collectDoomed = useCallback((basket, root) => {
    const doomed = new Set([root]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const b of basket) {
        if (doomed.has(b.alias) || !b.derivation) continue;
        const srcs = derivSourceAliases(b.derivation);
        if (srcs.some((s) => doomed.has(s))) { doomed.add(b.alias); grew = true; }
      }
    }
    return doomed;
  }, []);

  // Türetilmiş/manuel-SQL node'unu scope'tan tamamen kaldır. Göz toggle
  // pasifleştirir (scope'ta tutar); bu gerçekten siler. Node'lar [scope]
  // reconcile effect'iyle düşer, edge'ler buildEdges'la yeniden kurulur —
  // ek setNodes gerekmez. Silinen alias'a değen join'ler de temizlenir.
  const removeAlias = useCallback((alias) => {
    const doomed = collectDoomed(scope.basket || [], alias);
    const deps = [...doomed].filter((a) => a !== alias);
    if (deps.length > 0) {
      const ok = window.confirm(
        `'${alias}' siliniyor. Buna bağlı ${deps.length} türetilmiş tablo da kaldırılacak:\n`
        + deps.join(", ") + "\n\nDevam edilsin mi?");
      if (!ok) return;
    }
    setScope((s) => {
      const live = collectDoomed(s.basket || [], alias);
      for (const a of live) delete COLS_BY_ALIAS[a];
      return {
        ...s,
        basket: (s.basket || []).filter((b) => !live.has(b.alias)),
        joins: (s.joins || []).filter(
          (j) => !live.has(j.left.alias) && !live.has(j.right.alias)),
        inactive_aliases: (s.inactive_aliases || []).filter((a) => !live.has(a)),
      };
    });
    // Açık paneller silinen alias'ı gösteriyorsa kapat.
    setPreview((p) => (p && doomed.has(p.alias) ? null : p));
    setDocsTable((t) => (t && doomed.has(t.id) ? null : t));
    setEditSqlAlias((cur) => (cur && doomed.has(cur) ? null : cur));
    setRefreshAlias((cur) => (cur && doomed.has(cur) ? null : cur));
    setToast(doomed.size > 1 ? `${doomed.size} tablo kaldırıldı` : `'${alias}' kaldırıldı`);
  }, [scope, collectDoomed]);

  // Bir node'un alias'ını (tablo adını) yeniden adlandır + TÜM referansları
  // güncelle: derivation kaynakları, derived_from, join'ler, filtreler,
  // inactive_aliases, COLS_BY_ALIAS, canvas node id'si, açık preview + grid state.
  // Otomatik üretilen filtre-child (`<alias>_f`) da birlikte yeniden adlandırılır
  // ki Filtreleme pre-fill'i bozulmasın. Alias snake_case 40-cap (makeAlias).
  const renameAlias = useCallback((oldAlias, newName) => {
    const others = (scope.basket || [])
      .map((b) => b.alias)
      .filter((a) => a !== oldAlias && a !== filterChildAlias(oldAlias));
    const clean = makeAlias(newName, others);
    if (!clean || clean === oldAlias) { setToast("Geçerli/yeni bir ad gir"); return; }

    // Rename haritası: node + (varsa) otomatik filtre-child'ı.
    const map = { [oldAlias]: clean };
    const oldChild = filterChildAlias(oldAlias);
    const newChild = filterChildAlias(clean);
    if ((scope.basket || []).some((b) => b.alias === oldChild) && oldChild !== newChild) {
      map[oldChild] = newChild;
    }
    const ren = (a) => map[a] || a;

    setScope((s) => {
      const basket = (s.basket || []).map((b) => {
        const nb = { ...b, alias: ren(b.alias) };
        if (b.derivation) {
          const d = { ...b.derivation };
          if (d.source_alias) d.source_alias = ren(d.source_alias);
          if (Array.isArray(d.source_aliases)) d.source_aliases = d.source_aliases.map(ren);
          if (Array.isArray(d.join_keys)) {
            d.join_keys = d.join_keys.map((jk) => ({
              ...jk, left_alias: ren(jk.left_alias), right_alias: ren(jk.right_alias),
            }));
          }
          if (d.filters) {
            d.filters = {
              ...d.filters,
              raw: (d.filters.raw || []).map((f) => ({ ...f, alias: ren(f.alias) })),
              pinned: (d.filters.pinned || []).map((f) => (Array.isArray(f.applies_to)
                ? { ...f, applies_to: f.applies_to.map(ren) } : f)),
            };
          }
          nb.derivation = d;
        }
        if (Array.isArray(b.derived_from)) nb.derived_from = b.derived_from.map(ren);
        return nb;
      });
      const joins = (s.joins || []).map((j) => ({
        ...j,
        left: { ...j.left, alias: ren(j.left.alias) },
        right: { ...j.right, alias: ren(j.right.alias) },
      }));
      const filters = {
        ...(s.filters || {}),
        pinned: ((s.filters || {}).pinned || []).map((f) => (Array.isArray(f.applies_to)
          ? { ...f, applies_to: f.applies_to.map(ren) } : f)),
        raw: ((s.filters || {}).raw || []).map((f) => ({ ...f, alias: ren(f.alias) })),
      };
      const inactive_aliases = (s.inactive_aliases || []).map(ren);
      return { ...s, basket, joins, filters, inactive_aliases };
    });

    // Module-level lookups + canvas nodes + open panels.
    for (const [from, to] of Object.entries(map)) {
      if (COLS_BY_ALIAS[from]) { COLS_BY_ALIAS[to] = COLS_BY_ALIAS[from]; delete COLS_BY_ALIAS[from]; }
    }
    setNodes((nds) => nds.map((n) => (map[n.id] ? { ...n, id: map[n.id] } : n)));
    setGridStateByAlias((g) => {
      let changed = false; const ng = { ...g };
      for (const [from, to] of Object.entries(map)) {
        if (ng[from]) { ng[to] = ng[from]; delete ng[from]; changed = true; }
      }
      return changed ? ng : g;
    });
    setPreview((p) => (p && map[p.alias] ? { ...p, alias: map[p.alias] } : p));
    setToast(`'${oldAlias}' → '${clean}' olarak yeniden adlandırıldı`);
  }, [scope]);

  // Bir basket alias'ını docs paneli nesnesine çöz (Option A):
  //   - katalog tablosu  → CATALOG_BY_ID tam dökümanı
  //   - filter node      → kaynağın gerçek tablo dökümanı (şemayı miras alır)
  //   - agregat/hesaplama/sql → sentetik doc: çıktı kolonları (COLS_BY_ALIAS)
  //     + "Kaynak Tablolar" gerçek tablo docs'una köprü.
  const docForAlias = useCallback((alias) => {
    const find = (a) => (scope.basket || []).find((b) => b.alias === a);
    const item = find(alias);
    if (!item) return null;

    // Düz katalog tablosu → gerçek doc.
    if (item.table_ref && !item.derivation) {
      const tid = `${item.table_ref.schema}.${item.table_ref.name}`;
      return CATALOG_BY_ID[tid] || { id: tid, columns: [] };
    }

    // Bir alias'ı nihai gerçek kaynak tablo BASKET ITEM'larına kadar yürü
    // (alias + table_ref birlikte gerekli: link için katalog satırı, kolon
    // zenginleştirmesi için COLS_BY_ALIAS[leafAlias]).
    const resolveLeaves = (a, seen) => {
      if (seen.has(a)) return [];
      seen.add(a);
      const b = find(a);
      if (!b) return [];
      if (b.table_ref && !b.derivation) return [b];
      const next = b.derivation
        ? derivSourceAliases(b.derivation)
        : (Array.isArray(b.derived_from) ? b.derived_from : []);
      return next.flatMap((s) => resolveLeaves(s, seen));
    };
    const leaves = [];
    const seenLeaf = new Set();
    for (const lf of resolveLeaves(alias, new Set())) {
      if (!seenLeaf.has(lf.alias)) { seenLeaf.add(lf.alias); leaves.push(lf); }
    }

    // Filter node → doğrudan ilk kaynak tablonun tam dökümanı (kullanıcı onayı).
    if (item.derivation && item.derivation.kind === "filter" && leaves.length) {
      const tid = `${leaves[0].table_ref.schema}.${leaves[0].table_ref.name}`;
      return CATALOG_BY_ID[tid] || { id: tid, columns: [] };
    }

    // Kaynak kolon sözlüğü (isim → meta). COLS_BY_ALIAS leaf'leri type + concept
    // binding taşır → çıktı kolonlarını isimle eşleştirip dökümante ederiz.
    const srcColMap = {};
    for (const lf of leaves) {
      for (const c of (COLS_BY_ALIAS[lf.alias] || [])) {
        if (c && c.name) srcColMap[String(c.name).toUpperCase()] = c;
      }
    }
    const sources = [];
    const seenSrc = new Set();
    for (const lf of leaves) {
      const tid = `${lf.table_ref.schema}.${lf.table_ref.name}`;
      if (!seenSrc.has(tid)) {
        seenSrc.add(tid);
        sources.push(CATALOG_BY_ID[tid] || { id: tid, columns: [] });
      }
    }
    const kindLabel = item.sql ? "manuel SQL"
      : item.derivation?.kind === "aggregate" ? "agregat"
      : item.derivation?.kind === "calculated" ? "hesaplama"
      : item.derivation?.kind === "join" ? "join"
      : item.derivation?.kind === "union" ? "union"
      : "türetilmiş";
    return {
      id: alias,
      synthetic: true,
      desc: kindLabel + (sources.length ? ` · kaynak: ${sources.map((s) => s.id).join(", ")}` : ""),
      columns: _enrichCols(COLS_BY_ALIAS[alias] || [], srcColMap),
      common_filters: [],
      sources,
    };
  }, [scope]);

  // (i) tıklaması: sentetik doc'u hemen göster (çıktı kolonları), sonra manuel
  // SQL node'larda kaynak tablolar scope'ta yoksa (Çözümle çalıştırılmamışsa)
  // resolve-sql ile kaynak tabloları + dökümante kolonlarını (concept dahil)
  // çekip kolonları isimle eşleştirerek zenginleştir. Toggle: aynı doc açıksa
  // kapatır.
  const openDocsForAlias = useCallback((alias) => {
    const doc = docForAlias(alias);
    if (!doc) return;
    setDocsTable((cur) => (cur && cur.id === doc.id ? null : doc));

    const item = (scope.basket || []).find((b) => b.alias === alias);
    if (!item || !item.sql || !doc.synthetic) return;
    // Her kolon zaten dökümanlı VE kaynak linki varsa ağ turuna gerek yok.
    const needs = (doc.columns || []).some((c) => !c.type && !c.concept && !c.expr);
    if (!needs && (doc.sources || []).length) return;

    fetch(RESOLVE_SQL_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql: item.sql }),
    }).then((r) => r.json()).then((data) => {
      if (!data || !data.ok || !Array.isArray(data.source_tables)) return;
      const srcColMap = {};
      const sources = [];
      const seen = new Set();
      for (const st of data.source_tables) {
        for (const c of (st.columns || [])) {
          if (c && c.name) srcColMap[String(c.name).toUpperCase()] = c;
        }
        if (st.id && !seen.has(st.id)) {
          seen.add(st.id);
          sources.push(CATALOG_BY_ID[st.id] || { id: st.id, columns: st.columns || [] });
        }
      }
      const enriched = {
        ...doc,
        columns: _enrichCols(doc.columns || [], srcColMap),
        sources: (doc.sources && doc.sources.length) ? doc.sources : sources,
        desc: (doc.sources && doc.sources.length) || !sources.length
          ? doc.desc
          : `manuel SQL · kaynak: ${sources.map((s) => s.id).join(", ")}`,
      };
      setDocsTable((cur) => (cur && cur.id === doc.id ? enriched : cur));
    }).catch(() => { /* best effort — çıktı kolonları yine de gösterildi */ });
  }, [scope, docForAlias]);


  const applySuggestion = useCallback(async (turnId, suggestion) => {
    setApplyingId(suggestion.id);
    try {
      const before = new Set((scope.basket || []).map((b) => b.alias));
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
      // Faz P — yeni python node'u LLM'den TAM kodla geldi → OTOMATİK çalıştır ve
      // sonucu Veri sekmesinde göster (kullanıcı "Çalıştır"a basmasın; node boş
      // "no rows" görünmesin). Çalıştırma data.scope üzerinden yapılır (React
      // state henüz güncellenmedi). Hata olursa Kaynak Script'e düşülür.
      if (suggestion.kind === "create_python_node") {
        const added = (data.scope.basket || []).map((b) => b.alias).find((a) => !before.has(a));
        const node = (data.scope.basket || []).find((b) => b.alias === added);
        const deriv = (node && node.derivation) || {};
        if (added) {
          setPythonRun({ alias: added, running: true, error: null, summary: null });
          setPreviewLoading(true);
          setPreview({ alias: added, openTab: "data", derived: true, isPython: true, data_columns: [], rows: [] });
          try {
            const pr = await fetch(PYTHON_PREVIEW_URL, {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                scope: data.scope, source_alias: deriv.source_alias,
                python_code: deriv.python_code || "",
              }),
            });
            const pres = await pr.json();
            if (pres.ok) {
              // Çıktı kolonlarını node'a YAZ (runPythonNode ile aynı) → node kartı
              // "N kolon" gösterir, Konseptler sekmesi bu kolonları listeler,
              // downstream görür. Yoksa node "0 kolon" + Konseptler boş kalır.
              const outCols = pres.output_columns || pres.data_columns || [];
              setScope((s) => ({
                ...s,
                basket: (s.basket || []).map((b) => (b.alias === added && b.derivation ? {
                  ...b, derivation: { ...b.derivation, output_columns: outCols },
                } : b)),
              }));
              if (outCols.length) {
                COLS_BY_ALIAS[added] = outCols.map((n) => ({ name: n, type: null, concept: null, join_key: false }));
              }
              // summary STRING olmalı — PythonScriptTab {run.summary}'yi doğrudan
              // render ediyor; obje verilirse React #31 (beyaz ekran). runPythonNode
              // ile aynı format.
              setPythonRun({ alias: added, running: false, error: null,
                summary: `✓ ${pres.row_count} satır · ${outCols.length} kolon`, columns: outCols });
              setPreview({ alias: added, openTab: "data", derived: true, isPython: true, ...pres });
            } else {
              const emsg = (pres.errors || ["Python önizleme başarısız"]).join("; ");
              setPythonRun({ alias: added, running: false, error: emsg, summary: null });
              setPreview({ alias: added, openTab: "script", derived: true, isPython: true,
                data_columns: [], rows: [], error: emsg });
            }
          } catch (e) {
            setPythonRun({ alias: added, running: false, error: String(e), summary: null });
            setPreview({ alias: added, openTab: "script", derived: true, isPython: true,
              data_columns: [], rows: [] });
          } finally {
            setPreviewLoading(false);
          }
        }
      }
      // edit_python_node: kod güncellendi → script editörü re-sync olur; eski
      // çalıştırma özetini temizle ve script sekmesinde kal.
      if (suggestion.kind === "edit_python_node") {
        setPythonRun({ alias: suggestion.alias, running: false, error: null, summary: null });
        setPreview((p) => (p && p.alias === suggestion.alias ? { ...p, openTab: "script" } : p));
      }
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
    // Başlık → başlık (her iki uç __table__) = UNION node (kolon sayısı + tip
    // uyumu modalda kontrol edilir).
    if (lc === "__table__" && rc === "__table__") {
      setUnionModal({ left: p.source, right: p.target });
      return;
    }
    // Kolon → kolon = JOIN node. Modal kolonları + join tipini sorar; kaydet'te
    // ortada türetilmiş bir join node üretir (eski metadata-join DEĞİL — o yol
    // yalnız LLM önerili edge onayında kaldı, onEdgeClick).
    setJoinModal({ left: p.source, right: p.target,
      preLcol: lc && lc !== "__other__" && lc !== "__table__" ? lc : null,
      preRcol: rc && rc !== "__other__" && rc !== "__table__" ? rc : null });
  }, []);

  // showPreview aşağıda tanımlı (TDZ) → onEdgeClick içinde ref ile çağır.
  const showPreviewRef = useRef(null);
  const onEdgeClick = useCallback((_e, edge) => {
    // Faz R1/F3 — derivation lineage edge (main → türetilmiş): filter ise
    // türetilmiş node'un drawer'ını aç (Kaynak Query görünür) + kaynağı
    // Filtreleme'den düzenle ipucu. agg/calc ise türetilmiş önizlemeyi aç.
    if (edge.data?.derivation) {
      // Edge'e tıkla → ÇIKTI (türetilmiş) node'u aç; böylece sohbet o node'a
      // (source → output) kapsanır ve yalnız o node'un kaynağını etkiler (#2).
      // python → "Kaynak Script" (düzenle), filter → kaynağı "Filtreleme",
      // agg/calc/join/union → çıktı node'unun önizlemesi.
      const out = edge.data.derivedAlias;
      const outItem = scope.basket.find((b) => b.alias === out);
      if (outItem?.derivation?.kind === "python") {
        showPreviewRef.current && showPreviewRef.current(out, "script");
      } else if (edge.data.derivKind === "filter") {
        // E3: edge = "kaynak → bu türev"; çıktı (türev) node'unu aç → sohbet
        // o node'u ÜRETEN script/query'yi düzenler (kaynağı değil).
        showPreviewRef.current && showPreviewRef.current(out, "filter");
      } else {
        showPreviewRef.current && showPreviewRef.current(out);
      }
      return;
    }
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
  }, [addJoin, scope]);

  // Yeni node'un başlangıç pozisyonu — hem React Flow node'una hem basket
  // item'ının layout'una yazılır ki F5 (draft reload) aynı yerde açsın (Bug 2).
  const nextNodePos = (offset = 0) => ({
    x: 80 + ((nodes.length + offset) % 3) * 340,
    y: 80 + Math.floor((nodes.length + offset) / 3) * 240,
  });

  // Üretilen node'u KAYNAĞININ hemen sağına konumla (uzağa düşmesin). Aynı
  // kaynaktan türeyen mevcut node sayısına göre dikey kaydır → üst üste binmez.
  const posNearSource = (srcAlias) => {
    // Önce CANLI node konumu (sürükleme yansır), yoksa basket layout'u; ikisi de
    // yoksa grid. Eskiden yalnız `nodes`'a bakıp bulamayınca grid'e düşüyordu →
    // üretilen node "saçma yere" gidiyordu (E1).
    const live = nodes.find((n) => n.id === srcAlias);
    const srcItem = (scope.basket || []).find((b) => b.alias === srcAlias);
    const pos = (live && live.position) || (srcItem && srcItem.layout) || null;
    if (!pos) return nextNodePos();
    const kids = (scope.basket || []).filter(
      (b) => b.derivation && derivSourceAliases(b.derivation).includes(srcAlias)).length;
    return { x: pos.x + 360, y: pos.y + kids * 160 };
  };

  const addTableFromCatalog = (t) => {
    const [schema, ...rest] = t.id.split(".");
    const name = rest.length ? rest.join(".") : schema;
    const realSchema = rest.length ? schema : "";
    const alias = makeAlias(name, scope.basket.map((b) => b.alias));
    const pos = nextNodePos();
    const item = {
      table_ref: { schema: realSchema || schema, name }, alias,
      projection: { columns: (t.columns || []).map((c) => c.name), include_all: (t.columns || []).length === 0 },
      routing: { decision: "cached", decided_by: "system", estimated_bytes: 0 },
      layout: { x: pos.x, y: pos.y },
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
      position: pos,
      data: enrichNodeData(item, scope),
    }]);
  };

  // Faz C — manuel SQL dataset → scope.basket'e `sql` kaynağı olarak ekle.
  // addTableFromCatalog'un sql karşılığı; recompute-routing YOK — saklama
  // kararını kullanıcı modalda verdi, decided_by:"user" ile korunur (sql
  // tablonun katalog satırı yok, recompute onu lazy'ye iterdi).
  const addSqlDataset = ({ alias, sql, columns, routing, refresh, resolvePlan }) => {
    // Faz R4/#1 — "Çözümle" planı varsa: kaynak tabloları (yoksa) LAZY main node
    // ekle, bu sonucu derived_from ile onlara bağla. Bug 4 — bu main'ler PASİF
    // doğar: SQL Oracle'a kendisi gittiğinden main'in verisine ihtiyaç yok;
    // pasif + lineage-only main'i build de çekmez (fetch_cached_tables skip).
    const taken = new Set((scope.basket || []).map((b) => b.alias));
    const derivedFrom = [];
    const newMains = [];
    if (resolvePlan && Array.isArray(resolvePlan.source_tables)) {
      for (const t of resolvePlan.source_tables) {
        const existing = (scope.basket || []).find(
          (b) => b.table_ref && `${b.table_ref.schema}.${b.table_ref.name}` === t.id);
        if (existing) { derivedFrom.push(existing.alias); continue; }
        const a = makeAlias(t.name, [...taken]);
        taken.add(a); derivedFrom.push(a);
        const m = {
          alias: a, table_ref: { schema: t.schema, name: t.name },
          projection: { columns: (t.columns || []).map((c) => c.name), include_all: !(t.columns || []).length },
          routing: { decision: "lazy", decided_by: "system", estimated_bytes: 0 },
        };
        COLS_BY_ALIAS[a] = (t.columns || []).map((c) => ({
          name: c.name, type: c.type, concept: c.concept || null, join_key: false, lookup: null,
        }));
        newMains.push(m);
      }
    }

    const item = {
      sql, alias,
      projection: { columns: columns || [], include_all: false },
      routing: { decision: routing, decided_by: "user", estimated_bytes: 0 },
      provenance: "Hazırlık — manuel SQL",
      ...(derivedFrom.length ? { derived_from: derivedFrom } : {}),
      ...(routing === "cached" && refresh ? { refresh } : {}),
    };
    COLS_BY_ALIAS[alias] = (columns || []).map((c) => ({
      name: c, type: null, concept: null, join_key: false, lookup: null,
    }));
    const allNew = [...newMains, item];
    // Başlangıç pozisyonlarını layout olarak da yaz (Bug 2 — F5 aynı düzen).
    const positioned = allNew.map((it, i) => {
      const p = nextNodePos(i);
      return { ...it, layout: { x: p.x, y: p.y } };
    });
    const newMainAliases = new Set(newMains.map((m) => m.alias));
    setScope((s) => ({
      ...s,
      basket: [...(s.basket || []), ...positioned],
      inactive_aliases: newMains.length
        ? [...new Set([...(s.inactive_aliases || []), ...newMainAliases])]
        : (s.inactive_aliases || []),
    }));
    setNodes((nds) => [
      ...nds,
      ...positioned.map((it) => ({
        id: it.alias, type: "tableNode",
        position: { x: it.layout.x, y: it.layout.y },
        data: { ...enrichNodeData(it, scope), inactive: newMainAliases.has(it.alias) },
      })),
    ]);
    setSqlModalOpen(false);
    setToast(newMains.length
      ? `'${alias}' + ${newMains.length} kaynak tablo eklendi (kaynaklar pasif — Sunum'a gitmez)`
      : `'${alias}' SQL tablosu eklendi`);
  };

  // Hazırlık ER — kolon→kolon sürükleme artık türetilmiş bir JOIN node üretir
  // (iki tablonun bağlantısının ortasında yeni tablo). DuckDB'de hesaplanır,
  // cron/cache normal türetilmiş node gibi. (Eski scope.joins metadata yolu
  // yalnız LLM önerili edge onayında kaldı.)
  const addJoinNode = ({ left, right, keys, joinType }) => {
    // Kaynak adlarını kırp ki `_join` suffix'i daima sığsın + sonradan filtre
    // (`_f`) eklenince 40 sınırını taşmasın.
    const alias = makeAlias(`${left}_${right}`.slice(0, 32) + "_join", scope.basket.map((b) => b.alias));
    const cols = joinColsFor(left, right);
    const pos = posNearSource(left);
    const item = {
      alias,
      derivation: {
        kind: "join",
        source_aliases: [left, right],
        // Multi-key: every pair AND'ed in the join condition (compile_join_sql).
        join_keys: (keys || []).map((k) => ({
          left_alias: left, left_column: k.lcol, right_alias: right, right_column: k.rcol,
        })),
        join_type: joinType === "left" ? "left" : "inner",
      },
      projection: { columns: cols.map((c) => c.name), include_all: false },
      routing: { decision: "cached", decided_by: "system", estimated_bytes: 0 },
      provenance: "Hazırlık — join",
      layout: { x: pos.x, y: pos.y },
    };
    COLS_BY_ALIAS[alias] = cols;
    setScope((s) => ({ ...s, basket: [...(s.basket || []), item] }));
    setNodes((nds) => [...nds, {
      id: alias, type: "tableNode",
      position: pos,
      data: enrichNodeData(item, scope),
    }]);
    setJoinModal(null);
    setToast(`'${alias}' join tablosu oluşturuldu`);
  };

  // Başlık→başlık sürükleme ile UNION node. Kolon sayısı + tip uyumu UnionModal'da
  // kontrol edilir; çıktı şeması ilk kaynakla aynıdır.
  const addUnionNode = ({ left, right, unionAll }) => {
    const alias = makeAlias(`${left}_${right}`.slice(0, 32) + "_union", scope.basket.map((b) => b.alias));
    const cols = (COLS_BY_ALIAS[left] || []).map((c) => ({ ...c }));
    const upos = posNearSource(left);
    const item = {
      alias,
      derivation: { kind: "union", source_aliases: [left, right], union_all: unionAll !== false },
      projection: { columns: cols.map((c) => c.name), include_all: false },
      routing: { decision: "cached", decided_by: "system", estimated_bytes: 0 },
      provenance: "Hazırlık — union",
      layout: { x: upos.x, y: upos.y },
    };
    COLS_BY_ALIAS[alias] = cols;
    setScope((s) => ({ ...s, basket: [...(s.basket || []), item] }));
    setNodes((nds) => [...nds, {
      id: alias, type: "tableNode",
      position: { x: upos.x, y: upos.y },   // E1: layout ile tutarlı (grid değil)
      data: enrichNodeData(item, scope),
    }]);
    setUnionModal(null);
    setToast(`'${alias}' union tablosu oluşturuldu`);
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

  const showPreview = useCallback(async (alias, openTab) => {
    const item = scope.basket.find((b) => b.alias === alias);
    if (!item) return;
    // Sekme yalnız ÇAĞIRAN açıkça verirse seçilir; aksi halde drawer "Veri"ye
    // düşer. (Eskiden python her açılışta "script"e zorlanıyordu → panel kendi
    // kendine Kaynak Script'e atlıyordu, #3.) Yeni python node'u + edge-click
    // "script" geçer; düz tıklama Veri açar.
    // Python: doldurulmuşsa (output_columns var) "Veri", değilse "Kaynak Script".
    const isPy = item.derivation?.kind === "python";
    const pyHasOut = isPy && ((item.derivation.output_columns || []).length > 0);
    const tab0 = openTab || (isPy ? (pyHasOut ? "data" : "script") : undefined);
    setPreviewLoading(true); setPreview({ alias, openTab: tab0 });
    try {
      let data;
      if (isPy) {
        // Script sekmesinde açılıyorsa (doldurulmamış ya da düzenlenecek) Oracle+
        // sandbox koşturmadan SADECE drawer'ı aç (hızlı + hatasız → #2: node havada
        // kalıp hata vermesin, script doldurulabilsin).
        if (tab0 === "script") {
          setPreviewLoading(false);
          setPreview({ alias, openTab: "script", derived: true, isPython: true,
            data_columns: item.derivation.output_columns || [], rows: [] });
          return;
        }
        // Veri sekmesi → kaydedilmiş script'i kaynak örneği üzerinde çalıştır.
        const r = await fetch(PYTHON_PREVIEW_URL, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            scope, source_alias: item.derivation.source_alias,
            python_code: item.derivation.python_code || "",
          }),
        });
        const res = await r.json();
        // Kod kırıksa drawer yine açık kalır; isPython sayesinde Kaynak Script
        // sekmesi hata durumunda DA erişilebilir (aşağıda drawer).
        data = res.ok
          ? { ...res, derived: true, isPython: true }
          : { derived: true, isPython: true,
              error: (res.errors || ["Python önizleme başarısız"]).join("; ") };
        setPreview({ alias, openTab: "data", ...data });
        return;
      }
      if (item.derivation && item.derivation.kind === "filter") {
        // Faz R1/F3 — filter-node: sunucu compile_filter_sql ile kaynak query'yi
        // üretir, capped örneği koşar ve SQL'i döner ("Kaynak Query" tab için).
        const r = await fetch(FILTER_PREVIEW_URL, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scope, alias }),
        });
        data = await r.json();
        if (!data.ok) {
          data = { derived: true, isFilter: true,
                   error: (data.errors || ["Filtre önizleme başarısız"]).join("; "),
                   sql: data.sql || null };
        } else {
          data = { ...data, derived: true, isFilter: true };
        }
      } else if (item.derivation) {
        // aggregate / calculated / join / union — hepsi sunucuda derlenir. Sunucu
        // kaynakları (GEREKİRSE ÖZYİNELEMELİ — türetilmiş/filter kaynaklar dahil)
        // DuckDB'ye örnekler, derlenmiş SQL'i koşar ve "Kaynak Query" için SQL
        // döner. (Eski yerel aggregate yolu türetilmiş kaynakta "Kaynak tablo
        // basketta yok" patlıyordu — join/union'da da aynı buguydu.)
        const r = await fetch(PREVIEW_DERIVATION_URL, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scope, alias }),
        });
        data = await r.json();
        if (!data.ok) {
          // Hatada SQL'i de taşı — drawer "Kaynak Query" yerine hatayı + üreten
          // sorguyu gösterir ki kullanıcı bozuk derlenmiş SQL'i görebilsin.
          data = { derived: true,
                   error: (data.errors || ["Türetilmiş önizleme başarısız"]).join("; "),
                   sql: data.sql || null };
        } else {
          data = { ...data, derived: true };
        }
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
      // Faz 4 (madde 6) — uniform "Kaynak": main (table_ref) + sql node'lar için
      // kanonik kaynak SQL'ini ekle → "Kaynak" tab'ı HER node'da görünür.
      if (!data.error && (item.table_ref || item.sql) && !data.sql) {
        try {
          const sr = await fetch(SOURCE_SQL_URL, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scope, alias }),
          });
          const sd = await sr.json();
          if (sd.ok && sd.sql) data = { ...data, sql: sd.sql, sourceEditable: !!sd.editable };
        } catch { /* best effort — Kaynak tab gizli kalır */ }
      }
      setPreview({ alias, openTab, ...data });
    } catch (e) { setPreview({ alias, openTab, error: String(e) }); }
    finally { setPreviewLoading(false); }
  }, [scope]);
  showPreviewRef.current = showPreview;   // onEdgeClick'in çağırması için güncel tut

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
    const derivedAlias = filterChildAlias(alias);
    // Faz A — kaynak başka bir türetilmiş node ise (table_ref yok), filtre
    // DuckDB view'ı üstünde çalışır; o view'da concept binding yok. Bu yüzden
    // zincirleme filtrelerde HER ZAMAN raw (kolon) üret — pinned (concept)
    // backend'de derived kaynak için zaten atlanır, sessizce kaybolurdu.
    const srcItem = (scope.basket || []).find((b) => b.alias === alias);
    const srcIsDerived = !!(srcItem && (srcItem.derivation ||
      (Array.isArray(srcItem.derived_from) && srcItem.derived_from.length)));
    const pinned = [], raw = [];
    for (const s of specs) {
      const concept = s.concept || null;
      const compilerSafe = s.type !== "num" && (s.op === "between" || s.op === "in" || s.op === "eq");
      if (concept && compilerSafe && !srcIsDerived) {
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
      // Yeni filtre node'u kaynağın sağına doğar; güncellemede kullanıcının
      // sürüklediği layout korunur (filterNode layout taşımaz → spread ezmez).
      if (idx >= 0) basket[idx] = { ...basket[idx], ...filterNode };
      else {
        const srcNode = nodes.find((n) => n.id === alias);
        const fpos = srcNode
          ? { x: srcNode.position.x + 360, y: srcNode.position.y + 40 }
          : nextNodePos();
        basket = [...basket, { ...filterNode, layout: { x: fpos.x, y: fpos.y } }];
      }

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
    // Türetilmiş node'un distinct kaynağı (Oracle tablo) yok — DuckDB view'dan
    // distinct çekmek için endpoint yok; kullanıcı değeri elle girsin.
    if (!tr) return [];
    const u = new URL(DISTINCT_URL, window.location.origin);
    u.searchParams.set("schema", tr.schema || "");
    u.searchParams.set("table", tr.name);
    u.searchParams.set("column", column);
    const r = await fetch(u.pathname + u.search);
    const data = await r.json();
    if (!data.ok) throw new Error(data.error || "Distinct değerler alınamadı");
    return data.values || [];
  };

  // Kolon-filtreli tablo kaydet: kullanıcı "Kolonlar" panelinden bazılarını
  // kaldırdıysa, KALAN kolonları taşıyan yeni bir node üret. Aggregation/pivot
  // artık burada değil — onu Python node ya da LLM yapıyor (Enterprise kaldırıldı).
  // Çıktı, kaynağın kolon alt-kümesini SELECT'leyen bir "calculated" identity node.
  const saveAsTable = (visibleCols) => {
    if (!preview) return;
    const all = preview.data_columns || [];
    let cols = (visibleCols && visibleCols.length ? visibleCols : all);
    // AG Grid'in GÜNCEL kolon sırası + görünürlüğü — kullanıcı kolonları
    // sürükleyip sıraladıysa o sıra korunur (#3), gizlediği kolon düşer (#2).
    const api = gridApiRef.current;
    if (api && api.getColumnState) {
      try {
        const ordered = api.getColumnState()
          .filter((c) => !c.hide && all.includes(c.colId))
          .map((c) => c.colId);
        if (ordered.length) cols = ordered;
      } catch { /* fallback: visibleCols / all */ }
    }
    if (cols.length === 0) { setToast("En az bir kolon seçili olmalı."); return; }
    // Hiç değişiklik yoksa (aynı kolonlar, aynı sıra) → yeni tablo üretme.
    const unchanged = cols.length === all.length && cols.every((c, i) => c === all[i]);
    if (unchanged) {
      setToast("Önce kolon kaldır ya da sürükleyip sırala — sonra kaydet."); return;
    }
    const source = preview.alias;
    const alias = makeAlias(`${source}_secili`, scope.basket.map((b) => b.alias));
    const apos = posNearSource(source);
    const item = {
      derivation: {
        kind: "calculated", source_aliases: [source], join_keys: [],
        // identity projeksiyon: "KOL" AS "KOL" (kolon adı şemada identifier-güvenli)
        columns: cols.map((c) => ({ name: c, expr: `"${c}"` })),
      },
      alias,
      projection: { columns: cols, include_all: false },
      routing: { decision: "cached", decided_by: "system", estimated_bytes: 0 },
      layout: { x: apos.x, y: apos.y },
    };
    const srcCols = Object.fromEntries((COLS_BY_ALIAS[source] || []).map((c) => [c.name, c]));
    COLS_BY_ALIAS[alias] = cols.map((c) => ({
      name: c, concept: srcCols[c]?.concept || null, join_key: !!srcCols[c]?.join_key,
    }));
    setScope((s) => ({ ...s, basket: [...s.basket, item] }));
    setNodes((nds) => [...nds, {
      id: alias, type: "tableNode", position: apos,
      data: enrichNodeData(item, scope),
    }]);
    setToast(`'${alias}' kolon-filtreli tablo eklendi (${cols.length} kolon).`);
  };

  // Faz P — bir node'dan (sourceAlias) yeni bir python dönüşüm node'u üret.
  // Yeni node tek-girişlidir: input_node_df = sourceAlias verisi. Drawer "Kaynak
  // Script" sekmesinde açılır; kullanıcı script'i yazıp Çalıştır + Kaydet eder.
  const createPythonNode = useCallback((sourceAlias) => {
    const alias = makeAlias(`${sourceAlias}_py`, scope.basket.map((b) => b.alias));
    const apos = posNearSource(sourceAlias);
    const starter =
      `# input_node_df: '${sourceAlias}' verisinin DataFrame'i (pandas — pd, np hazır)\n`
      + `# Dönüşümünü yaz; sonunda output_node_df adlı bir DataFrame üret:\n`
      + `output_node_df = input_node_df\n`;
    const item = {
      derivation: { kind: "python", source_alias: sourceAlias, python_code: starter, output_columns: [] },
      alias,
      projection: { columns: [], include_all: true },
      routing: { decision: "cached", decided_by: "system", estimated_bytes: 0 },
      layout: { x: apos.x, y: apos.y },
    };
    setScope((s) => ({ ...s, basket: [...s.basket, item] }));
    setNodes((nds) => [...nds, {
      id: alias, type: "tableNode",
      position: { x: apos.x, y: apos.y },
      data: enrichNodeData(item, scope),
    }]);
    setPythonRun({ alias, running: false, error: null, summary: null });
    setPreviewLoading(false);
    setPreview({ alias, openTab: "script", derived: true, isPython: true, data_columns: [], rows: [] });
    setToast(`'${alias}' python node'u eklendi — script'i yazıp Çalıştır.`);
  }, [scope]);

  // Faz P — taslak script'i sandbox'ta çalıştır (preview-python). Başarılıysa
  // örnek satırlar Veri sekmesine yansır; özet/çıktı kolonları Kaydet'te kullanılır.
  const runPythonNode = useCallback(async (alias, code) => {
    const item = scope.basket.find((b) => b.alias === alias);
    const src = item?.derivation?.source_alias;
    if (!src) { setPythonRun({ alias, running: false, error: "Kaynak node yok.", summary: null }); return; }
    setPythonRun({ alias, running: true, error: null, summary: null });
    try {
      const r = await fetch(PYTHON_PREVIEW_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, source_alias: src, python_code: code }),
      });
      const data = await r.json();
      if (!data.ok) {
        const err = (data.errors || ["Çalıştırma başarısız"]).join("; ")
          + (data.detail ? `\n\n${data.detail}` : "");
        setPythonRun({ alias, running: false, error: err, summary: null });
        return;
      }
      // Veri sekmesi son çalıştırmayı göstersin (aynı alias açıksa).
      setPreview((p) => (p && p.alias === alias ? { ...p, ...data, derived: true, isPython: true } : p));
      const outCols = data.output_columns || data.data_columns || [];
      // #1 — Çalıştırma BAŞARILI olduğunda kodu + çıktı kolonlarını node'a YAZ
      // (ayrı "Kaydet" beklemeden). Böylece python node'unun kolonları belli olur
      // ve join/union yapılabilir (eskiden "0 kolon" kalıyordu).
      setScope((s) => ({
        ...s,
        basket: (s.basket || []).map((b) => (b.alias === alias && b.derivation ? {
          ...b,
          derivation: { ...b.derivation, python_code: code, output_columns: outCols },
        } : b)),
      }));
      if (outCols.length) {
        COLS_BY_ALIAS[alias] = outCols.map((n) => ({ name: n, type: null, concept: null, join_key: false }));
      }
      setPythonRun({
        alias, running: false, error: null,
        summary: `✓ ${data.row_count} satır · ${outCols.length} kolon`,
        columns: outCols,
      });
    } catch (e) {
      setPythonRun({ alias, running: false, error: String(e.message || e), summary: null });
    }
  }, [scope]);

  // Faz P — script'i node'a kaydet. Son çalıştırmanın çıktı kolonları varsa
  // node'un output_columns'ı + COLS_BY_ALIAS güncellenir (downstream node'lar
  // bu kolonları görür).
  const savePythonNode = useCallback((alias, code) => {
    const cols = (pythonRun && pythonRun.alias === alias && pythonRun.columns) || null;
    setScope((s) => ({
      ...s,
      basket: (s.basket || []).map((b) => (b.alias === alias && b.derivation ? {
        ...b,
        derivation: { ...b.derivation, python_code: code, ...(cols ? { output_columns: cols } : {}) },
      } : b)),
    }));
    if (cols && cols.length) {
      COLS_BY_ALIAS[alias] = cols.map((n) => ({ name: n, type: null, concept: null, join_key: false }));
    }
    setToast(cols ? `'${alias}' script + ${cols.length} kolon kaydedildi` : `'${alias}' script kaydedildi`);
  }, [pythonRun]);

  // #4 — kolona concept bağla (column_concepts'e yaz; debounced save-draft persist eder).
  const setColumnConcept = useCallback((alias, column, conceptId) => {
    setScope((s) => ({
      ...s,
      basket: (s.basket || []).map((b) => {
        if (b.alias !== alias) return b;
        const cc = { ...(b.column_concepts || {}) };
        if (conceptId) cc[column] = conceptId; else delete cc[column];
        return { ...b, column_concepts: cc };
      }),
    }));
  }, []);

  // #4 — seçilen concept'in kolona uygunluğunu sunucuda test et (distinct vs tanım).
  const validateConcept = useCallback(async (alias, column, concept) => {
    const r = await fetch(VALIDATE_CONCEPT_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, alias, column, concept }),
    });
    const d = await r.json();
    if (!d.ok) return { level: "warn", message: (d.errors || ["doğrulanamadı"]).join("; ") };
    return { level: d.level, message: d.message };
  }, [scope]);

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
  // Bug 1 UX — tam ekran build overlay'i. phase: 'check' (preview-build) →
  // 'fetch' (Oracle çekimi, asıl bekleme) → 'leave' (fade-out + redirect).
  const [buildState, setBuildState] = useState(null);
  // 2.6: aktif async build job_id — F5/sekme-kapatma/unmount'ta sunucuya "iptal"
  // (sendBeacon) gönderir. Aksi halde orphan build worker session._exec_lock'u
  // tutup re-entry'yi (B2) ve Sunum yüklemesini (B3) kilitler.
  const buildJobRef = useRef(null);
  useEffect(() => {
    const beacon = () => {
      const jid = buildJobRef.current;
      if (jid && navigator.sendBeacon) {
        try { navigator.sendBeacon(buildCancelUrl(jid)); } catch { /* yut */ }
      }
    };
    window.addEventListener("beforeunload", beacon);
    return () => {
      window.removeEventListener("beforeunload", beacon);
      const jid = buildJobRef.current;
      if (jid) fetch(buildCancelUrl(jid), { method: "POST" }).catch(() => {});
    };
  }, []);

  // Overlay'de listelenecek dataset'ler: build'in fiilen hazırlayacağı cached
  // item'lar (pasif + lineage-only main'ler hariç — backend onları çekmez).
  const buildFetchList = useMemo(() => {
    const s = buildState?.scope;
    if (!s) return [];
    const inactive = new Set(s.inactive_aliases || []);
    const needed = new Set();
    for (const b of (s.basket || [])) {
      if (b.derivation && b.routing?.decision === "cached") {
        for (const src of derivSourceAliases(b.derivation)) needed.add(src);
      }
    }
    return (s.basket || [])
      .filter((b) => {
        if (b.routing?.decision !== "cached") return false;
        if (!b.derivation && inactive.has(b.alias) && !needed.has(b.alias)) return false;
        return true;
      })
      .map((b) => ({
        alias: b.alias,
        kind: b.sql ? "manuel SQL"
          : b.derivation ? (b.derivation.kind === "filter" ? "filtre" : b.derivation.kind)
          : "tablo",
      }));
  }, [buildState]);

  const _finalisedScope = () => {
    const pos = Object.fromEntries(nodes.map((n) => [n.id, n.position]));
    // Faz R/B — Sunum'a yalnız AKTİF node'lar gider. Pasifleri çıkar; ama bir
    // AKTİF node'un KAYNAĞI pasif olsa bile geri eklenir (aşağıda inactive_aliases
    // ile işaretlenip Sunum sidebar'ından gizlenir, ama Hazırlık'ta kalır →
    // "kaynak node silindi" olmaz). İki tür kaynak:
    //   - derivation kaynağı (filter/aggregate/calculated/join/union) — node'un
    //     compile/materialize'ı için GEREKLİ.
    //   - derived_from (manuel-SQL lineage) — SQL gerçek tabloyu doğrudan
    //     sorgular, ama kullanıcının kaynak node'u Hazırlık'tan silinmesin.
    const inactive = new Set(scope.inactive_aliases || []);
    const keep = new Set(scope.basket.filter((b) => !inactive.has(b.alias)).map((b) => b.alias));
    let changed = true;
    while (changed) {
      changed = false;
      for (const b of scope.basket) {
        if (!keep.has(b.alias)) continue;
        const srcs = b.derivation
          ? derivSourceAliases(b.derivation)
          : (Array.isArray(b.derived_from) ? b.derived_from : []);
        for (const s of srcs) {
          if (!keep.has(s)) { keep.add(s); changed = true; }
        }
      }
    }
    const basket = scope.basket
      .filter((b) => keep.has(b.alias))
      .map((b) => pos[b.alias] ? { ...b, layout: { x: pos[b.alias].x, y: pos[b.alias].y } } : b);
    const joins = (scope.joins || []).filter(
      (j) => keep.has(j.left.alias) && keep.has(j.right.alias));
    // Yalnız kullanıcının pasifleştirdiği ama aktif bir türetilmiş node'un
    // materialize bağımlılığı olduğu için geri-eklenen kaynaklar `inactive_aliases`'te
    // kalır → scope'ta dururlar (node materialize olur) ama build'in
    // _manifest_basket_from_scope'u bunları Sunum sidebar'ından GİZLER. Kullanıcının
    // pasifleştirdiği tablo böylece Sunum'a "veri kaynağı" olarak geçmez.
    const hiddenSources = [...keep].filter((a) => inactive.has(a));
    return { ...scope, basket, joins, inactive_aliases: hiddenSources };
  };

  // D3 — build async: POST build-async hemen {job_id} döner, fetch arka planda
  // koşar; overlay build-status'ü poll'layıp hazır olan dataset'lere ✓ işler.
  // Bitince fade-out ("leave") animasyonu + redirect. Endpoint yoksa (eski
  // backend) senkron /scope/build'e düşülür.
  const _pollBuild = (jobId, finalScope) => {
    const tick = async () => {
      if (buildJobRef.current !== jobId) return;   // kullanıcı iptal etti → poll dur
      try {
        const r = await fetch(buildStatusUrl(jobId));
        const s = await r.json();
        if (!r.ok || !s.ok) throw new Error(s.error || `HTTP ${r.status}`);
        if (s.phase === "failed") {
          buildJobRef.current = null;
          setErr(s.error || "Build başarısız");
          setBusy(false); setBuildState(null);
          return;
        }
        if (s.phase === "cancelled" || s.phase === "gone") {
          buildJobRef.current = null;
          setBusy(false); setBuildState(null);
          return;
        }
        setBuildState((b) => (b && b.phase === "fetch" ? { ...b, done: s.done || [] } : b));
        if (s.phase === "done") {
          buildJobRef.current = null;
          setBuildState({ phase: "leave", scope: finalScope });
          setTimeout(() => { window.location.href = s.redirect; }, 450);
          return;
        }
        setTimeout(tick, 1000);
      } catch (e) {
        buildJobRef.current = null;
        setErr(String(e.message || e));
        setBusy(false); setBuildState(null);
      }
    };
    setTimeout(tick, 700);
  };

  const _commitBuildSync = async (finalScope) => {
    const data = await (await fetch(BUILD_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: finalScope }),
    })).json();
    if (!data.ok) {
      setErr((data.errors || ["Bilinmeyen hata"]).join(" · "));
      setBusy(false); setBuildState(null);
      return;
    }
    // Fade-out animasyonu bitmeden navigate etme — sert sıçrama yerine
    // "Sunum'a geçiliyor…" geçişi (hazirlik.css .hz-build-overlay.is-leaving).
    setBuildState({ phase: "leave", scope: finalScope });
    setTimeout(() => { window.location.href = data.redirect; }, 450);
  };

  const _commitBuild = async (finalScope) => {
    setBusy(true); setErr(null);
    setBuildState({ phase: "fetch", scope: finalScope, done: [] });
    try {
      const r = await fetch(BUILD_ASYNC_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: finalScope }),
      });
      if (r.status === 404 || r.status === 405) {
        await _commitBuildSync(finalScope);
        return;
      }
      const data = await r.json();
      if (!r.ok || !data.ok) {
        setErr((data.errors || ["Bilinmeyen hata"]).join(" · "));
        setBusy(false); setBuildState(null);
        return;
      }
      buildJobRef.current = data.job_id;
      _pollBuild(data.job_id, finalScope);
    } catch (e) { setErr(String(e)); setBusy(false); setBuildState(null); }
  };

  // 2.6 — aktif build'i kullanıcı iptali: sunucuya build-cancel + overlay'i kapat.
  // CancelToken tetiklenince fetch döngüsü sınırda durur, _exec_lock serbest →
  // re-entry açık. (Mevcut `cancelBuild` onay-modalini kapatır — farklı amaç.)
  const abortActiveBuild = () => {
    const jid = buildJobRef.current;
    buildJobRef.current = null;
    setBusy(false); setBuildState(null);
    if (jid) fetch(buildCancelUrl(jid), { method: "POST" }).catch(() => {});
  };

  const goToSunum = async () => {
    setErr(null);
    setBusy(true);
    const finalScope = _finalisedScope();
    setBuildState({ phase: "check", scope: finalScope });
    try {
      const r = await fetch(PREVIEW_BUILD_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: finalScope }),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        setErr((data.errors || ["Bilinmeyen hata"]).join(" · "));
        setBusy(false);
        setBuildState(null);
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
      setBuildState(null);   // onay modali açılıyor — overlay kalkar
      setPendingScope(finalScope);
      setBuildPreview(data);
    } catch (e) {
      setErr(String(e));
      setBusy(false);
      setBuildState(null);
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
      {/* Başlık şeridi — Keşif'teki gibi sunum adı + pid + Kaydet. PRISMA
          shell topbar (breadcrumb) bunun üstünde kalır. */}
      <WorkshopHeader
        title={workshopTitle}
        pid={PID}
        saving={titleSaving}
        toast={savedToast}
        onChange={onTitleChange}
        onSave={onSaveWorkshop}
      />

      {err && <div className="hz-error hz-error--bar">{err}</div>}

      <div className="hz-body">
        <SourcesSidebar
          scope={scope}
          libraryBlocks={DATA.library_blocks || []}
          hiddenAliases={inactiveAliases}
          onToggleVisibility={toggleAliasVisibility}
          onRemove={removeAlias}
          onOpenDocs={openDocsForAlias}
          goingToSunum={busy}
          onGoToSunum={goToSunum}
          onUpload={() => setUploadOpen(true)}
          onAddSql={() => setSqlModalOpen(true)}
          onEditSql={(alias) => setEditSqlAlias(alias)}
          chat={{
            history: chatHistory, busy: chatBusy, error: chatError,
            draft: chatDraft, onDraftChange: setChatDraft,
            onSend: sendChat, onApply: applySuggestion, onDismiss: dismissSuggestion,
            applyingId, selectedAlias: preview?.alias || null,
            selectedSource: (() => {
              const it = preview ? scope.basket.find((b) => b.alias === preview.alias) : null;
              return it?.derivation ? (derivSourceAliases(it.derivation)[0] || null) : null;
            })(),
          }}
        />
        {docsTable && (
          <TableDocsPanel
            table={docsTable}
            onClose={() => setDocsTable(null)}
            onOpen={(t) => t && setDocsTable(t)}
          />
        )}
        <main className="hz-right">
          <div className="hz-canvas">
            <ReactFlow
              nodes={nodes} edges={edges}
              onNodesChange={onNodesChange}
              onNodeDragStop={onNodeDragStop}
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
              existingFilters={(() => {
                // Filtreler artık `<alias>_f` türetilmiş node'una gömülü (node
                // modeli). Önce onu pre-fill et; yoksa legacy scope.filters'a düş.
                const child = (scope.basket || []).find((b) => b.alias === filterChildAlias(preview.alias));
                if (child && child.derivation && child.derivation.filters) {
                  return {
                    pinned: child.derivation.filters.pinned || [],
                    raw: child.derivation.filters.raw || [],
                  };
                }
                return {
                  pinned: (scope.filters.pinned || []).filter((f) => Array.isArray(f.applies_to) && f.applies_to.length === 1 && f.applies_to[0] === preview.alias),
                  raw: (scope.filters.raw || []).filter((f) => f.alias === preview.alias),
                };
              })()}
              savedGridState={gridStateByAlias[preview.alias]}
              onGridReady={(p) => { gridApiRef.current = p.api; window.__hzGridApi = p.api; }}
              item={(scope.basket || []).find((b) => b.alias === preview.alias)}
              onSaveRefresh={saveRefresh}
              onDelete={removeAlias}
              onRename={renameAlias}
              onCreatePython={createPythonNode}
              onRunPython={runPythonNode}
              onSavePython={savePythonNode}
              pythonRun={pythonRun}
              onSetConcept={setColumnConcept}
              onValidateConcept={validateConcept}
              onOpenConceptBrowser={(alias, col, cur) => setConceptBrowser({ alias, column: col, current: cur || null })}
            />
          )}
        </main>
        {conceptBrowser && (
          <ConceptBrowser
            concepts={conceptList}
            target={conceptBrowser.column}
            current={conceptBrowser.current}
            height={drawerH}
            editableIds={createdForms}
            suggest={conceptSuggest}
            onClose={() => setConceptBrowser(null)}
            onAddNew={(draft) => setConceptModal({ initial: draft || null, isEdit: false })}
            onEdit={(cid) => setConceptModal({ initial: createdForms[cid], isEdit: true })}
            onSelect={(cid) => {
              setColumnConcept(conceptBrowser.alias, conceptBrowser.column, cid);
              setConceptBrowser((b) => (b ? { ...b, current: cid } : b));
            }}
          />
        )}
      </div>

      {conceptModal && (
        <NewConceptModal
          initial={conceptModal.initial}
          isEdit={conceptModal.isEdit}
          onClose={() => setConceptModal(null)}
          onSaved={(summary, form, isEdit) => {
            const i = CONCEPTS.findIndex((c) => c.id === summary.id);
            if (i >= 0) CONCEPTS[i] = summary; else CONCEPTS.push(summary);
            setConceptList([...CONCEPTS]);
            setCreatedForms((m) => ({ ...m, [summary.id]: form }));
            setConceptModal(null);
            setToast(isEdit ? `'${summary.label || summary.id}' güncellendi` : `'${summary.label || summary.id}' eklendi`);
          }}
        />
      )}
      {joinModal && (
        <JoinKeyModal left={joinModal.left} right={joinModal.right}
          preLcol={joinModal.preLcol} preRcol={joinModal.preRcol}
          onClose={() => setJoinModal(null)}
          onSave={({ keys, kind }) => addJoinNode({
            left: joinModal.left, right: joinModal.right, keys, joinType: kind,
          })} />
      )}
      {unionModal && (
        <UnionModal left={unionModal.left} right={unionModal.right}
          onClose={() => setUnionModal(null)}
          onSave={({ unionAll }) => addUnionNode({
            left: unionModal.left, right: unionModal.right, unionAll,
          })} />
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
      {buildState && (
        <div className={`hz-build-overlay${buildState.phase === "leave" ? " is-leaving" : ""}`}>
          <div className="hz-build-overlay__card">
            <div className="hz-build-overlay__head">
              <Loader2 size={20} className="ts-spin" />
              <span>
                {buildState.phase === "check" && "Değişiklikler kontrol ediliyor…"}
                {buildState.phase === "fetch" && "Veriler hazırlanıyor…"}
                {buildState.phase === "leave" && "Sunum'a geçiliyor…"}
              </span>
            </div>
            {buildState.phase === "fetch" && (
              <>
                <p className="hz-build-overlay__sub">
                  Cached dataset'ler Oracle'dan çekilip oturuma yazılıyor —
                  süre tablo boyutlarına bağlı.
                </p>
                {buildFetchList.length > 0 && (
                  <ul className="hz-build-overlay__list ts-scroll">
                    {buildFetchList.map((d) => {
                      const ok = (buildState.done || []).includes(d.alias);
                      return (
                        <li key={d.alias} className={ok ? "is-done" : ""}>
                          {ok
                            ? <span className="hz-build-overlay__ok">✓</span>
                            : <Loader2 size={11} className="ts-spin" />}
                          <span className="hz-build-overlay__alias">{d.alias}</span>
                          <span className="hz-build-overlay__kind">{d.kind}</span>
                        </li>
                      );
                    })}
                  </ul>
                )}
                <button
                  type="button"
                  onClick={abortActiveBuild}
                  style={{ marginTop: 14, padding: "6px 16px", borderRadius: 6,
                           border: "1px solid rgba(255,255,255,0.28)",
                           background: "transparent", color: "inherit",
                           cursor: "pointer", fontSize: 12, fontFamily: "inherit" }}
                >
                  İptal
                </button>
              </>
            )}
          </div>
        </div>
      )}
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
