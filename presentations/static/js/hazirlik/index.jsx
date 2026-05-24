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
} from "@xyflow/react";
import { AgGridReact } from "ag-grid-react";
import "ag-grid-enterprise";   // demo (unlicensed → watermark) — pivot/row-grouping/aggregation
import "@xyflow/react/dist/style.css";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";
import {
  X, Plus, Trash2, Database, ArrowRight, ChevronLeft, ChevronRight,
  MessageSquare, Save, Eraser, Table2,
  Building2, Percent, Network, Calendar, Upload, Send, Loader2, Eye, Tag,
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
const CHAT_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/chat`);
const APPLY_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/apply-suggestion`);
const ROUTING_OVERRIDE_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/routing-override`);
const ROUTING_RECOMPUTE_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/recompute-routing`);
const PROJECTION_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/projection-update`);
const LIST_URL = _path.slice(0, _path.indexOf("/hazirlik")) + "/";

const COLS_BY_ALIAS = DATA.columns_by_alias || {};
const SUGGESTED = DATA.suggested_edges || [];
const DOMAINS = DATA.catalog?.domains || [];
const ROUTING_CONFIG = DATA.routing_config || { threshold_bytes: 500_000_000, hard_ceiling_bytes: 10_000_000_000 };

// Compact byte formatter — "320 MB", "4.2 GB", "—" for unknown.
function formatBytes(n) {
  if (n == null || n <= 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1_000_000) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(n < 100_000_000 ? 1 : 0)} MB`;
  return `${(n / 1_000_000_000).toFixed(n < 100_000_000_000 ? 1 : 0)} GB`;
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
const NODE_HANDLERS = { onOverrideRouting: null };

function enrichNodeData(item, scope) {
  return {
    ...nodeData(item),
    activeFilters: filtersForAlias(scope, item.alias),
    onOverrideRouting: NODE_HANDLERS.onOverrideRouting,
  };
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
  const { item, desc, size, colCount, keyCols, derived, activeFilters, color, onOverrideRouting } = data;
  const cached = item.routing?.decision === "cached";
  const filterCount = (activeFilters?.pinned?.length || 0) + (activeFilters?.raw?.length || 0);
  const headStyle = !derived && color ? { background: color } : undefined;
  const estimatedBytes = item.routing?.estimated_bytes;
  const decidedBy = item.routing?.decided_by || "system";
  const hardCeilingExceeded = (estimatedBytes || 0) > ROUTING_CONFIG.hard_ceiling_bytes;
  const sizeText = derived ? null : formatBytes(estimatedBytes);
  const overrideLabel = cached ? "→ lazy" : "→ cached";
  const overrideDisabled = !cached && hardCeilingExceeded;
  const overrideTitle = overrideDisabled
    ? `Force cached reddedildi: tahmin ${formatBytes(estimatedBytes)} > hard ceiling ${formatBytes(ROUTING_CONFIG.hard_ceiling_bytes)}.`
    : (cached ? "Lazy'ye geç (Oracle'dan blok zamanında çek)" : "Cached'a geç (DuckDB'ye materialise)");
  const decisionTitle = derived
    ? "Türetilmiş tablo — DuckDB'de hesaplanır."
    : `Tahmini boyut: ${formatBytes(estimatedBytes)}. Karar: ${decidedBy === "user" ? "kullanıcı override'ı" : "sistem"}.`;
  return (
    <div className={`hz-node${derived ? " hz-node--derived" : ""}`}>
      <div className="hz-node-head" style={headStyle}>
        <span className="hz-node-alias"><Database size={12} /> {derived ? item.alias : `${item.table_ref.schema}.${item.table_ref.name}`}</span>
        <span
          className={`hz-badge hz-badge--${derived ? "derived" : (cached ? "cached" : "lazy")}`}
          title={decisionTitle}
        >
          {derived
            ? "türetilmiş"
            : (cached ? `cached · ${sizeText}` : `lazy · ${sizeText}`)}
          {decidedBy === "user" && !derived && <span className="hz-badge-user-mark"> ✦</span>}
        </span>
      </div>
      {!derived && (
        <div className="hz-node-routing">
          <span
            className="hz-proj-count"
            title="Projection: önizleme'de gizlediğin kolonlar buradan düşer ('Görünümü kaydet' ile)"
          >
            {item.projection?.include_all ? `tümü (${colCount})` : `${item.projection?.columns?.length || 0}/${colCount} kolon`}
          </span>
          <button
            type="button"
            className="hz-route-override"
            disabled={overrideDisabled}
            title={overrideTitle}
            onClick={(e) => { e.stopPropagation(); onOverrideRouting && onOverrideRouting(item.alias, cached ? "lazy" : "cached"); }}
          >
            {overrideLabel}
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
    return {
      item, derived: true, desc: `${item.derivation.source_alias} → aggregate`,
      size: null, colCount: cols.length, keyCols: cols.filter((c) => c.join_key),
      color: null,   // derived → purple via .hz-node--derived class
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
      // Confirmed edge label: prefer the concept (shows *why* the join exists)
      // over the raw kind, then append the column pair for clarity.
      label: concept ? `${concept} · ${j.left.column}=${j.right.column}` : `${j.kind}: ${j.left.column}=${j.right.column}`,
      className: "hz-edge hz-edge--confirmed",
      data: { confirmed: true, join: j, concept },
    };
  });
  const aliases = new Set(scope.basket.map((b) => b.alias));
  SUGGESTED.forEach((s, i) => {
    if (!aliases.has(s.left.alias) || !aliases.has(s.right.alias)) return;
    if (confirmed.has(joinKey(s.left.alias, s.left.column, s.right.alias, s.right.column))) return;
    // Edge label shows the concept that proposed the suggestion (for FK
    // lookups this is the lookup's display concept; for shared-concept it's
    // the concept itself). Falls back to "lookup" / "öneri" otherwise.
    const concept = s.concept || _conceptForJoinSide(s.left.alias, s.left.column);
    const kindLabel = s.source === "catalog_lookup" ? "lookup" : "öneri";
    edges.push({
      id: `sug_${i}`, source: s.left.alias, target: s.right.alias,
      sourceHandle: s.left.column,
      targetHandle: s.right.column,
      label: concept ? `${concept} · ${kindLabel}` : kindLabel,
      className: "hz-edge hz-edge--suggested",
      data: { suggested: true, edge: s, concept },
    });
  });
  return edges;
}

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

function BuildConfirmModal({ preview, onConfirm, onCancel }) {
  const { diff = {}, affected_blocks = [], summary = {}, parent_version } = preview;
  const breaking = summary.breaking || 0;
  const warning = summary.warning || 0;
  const blocks = affected_blocks;

  // Section-by-section diff summary lines — only the non-empty ones.
  const lines = [];
  if (diff.added?.length) lines.push({ label: "Eklenen tablolar", items: diff.added });
  if (diff.removed?.length) lines.push({ label: "Çıkarılan tablolar", items: diff.removed });
  if (diff.changed?.length) lines.push({ label: "Değişen tablolar", items: diff.changed });
  if (diff.filters?.added?.length) lines.push({ label: "Eklenen pinned filter", items: diff.filters.added });
  if (diff.filters?.removed?.length) lines.push({ label: "Çıkarılan pinned filter", items: diff.filters.removed });
  if (diff.filters?.modified?.length) lines.push({ label: "Değişen pinned filter", items: diff.filters.modified });
  if (diff.pin_flips?.length) {
    lines.push({
      label: "Pin durumu değişen filter",
      items: diff.pin_flips.map((p) => `${p.id} (${p.direction})`),
    });
  }
  if (diff.joins?.added?.length) lines.push({ label: "Eklenen join", items: diff.joins.added });
  if (diff.joins?.removed?.length) lines.push({ label: "Çıkarılan join", items: diff.joins.removed });
  if (diff.joins?.modified?.length) lines.push({ label: "Değişen join", items: diff.joins.modified });

  return (
    <Modal
      title={`Scope güncellemesi · scope_v${parent_version} → yeni sürüm`}
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
      {lines.length === 0 ? (
        <p className="hz-muted">Scope'ta görsel bir değişiklik yok ama yeni bir sürüm yazılacak.</p>
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

function SourcesSidebar({ scope, onToggleTable, onRemove, onOpenDocs, chat }) {
  const [open, setOpen] = useState({});
  const inBasket = new Set(scope.basket.map((b) => tableId(b.table_ref)));
  return (
    <aside className="editor-sidebar hz-sidebar">
      <div className="sidebar-inner">
        <div className="sidebar-section sidebar-section--sources ts-scroll">
          <div className="sidebar-label"><span className="sidebar-label-icon"><Database size={12} /></span><span>Veri Kaynakları</span></div>
          <div className="sources-list">
            {DOMAINS.map((d) => {
              const isOpen = !!open[d.id];
              const cnt = (d.tables || []).filter((t) => inBasket.has(t.id)).length;
              const DomainIcon = pickDomainIcon(d);
              return (
                <div key={d.id} className={`sources-domain${isOpen ? " is-open" : ""}`}>
                  <button type="button" className="sources-domain-header"
                    onClick={() => setOpen((o) => ({ ...o, [d.id]: !o[d.id] }))}>
                    <ChevronRight size={12} strokeWidth={2} className="sources-domain-chevron" />
                    <DomainIcon size={14} strokeWidth={1.8} className="sources-domain-icon" />
                    <span className="sources-domain-label">{d.label}</span>
                    {cnt > 0 && <span className="sources-domain-count">{cnt}</span>}
                  </button>
                  {isOpen && (
                    <div className="sources-tables">
                      {(d.tables || []).map((t) => {
                        const active = inBasket.has(t.id);
                        return (
                          <div key={t.id} className={`sources-table-wrap${active ? " is-active" : ""}`}>
                            <button type="button" className="sources-table"
                              onClick={() => onToggleTable(t)}
                              title={active ? "Sepetten çıkar" : "Sepete ekle"}>
                              <div className="sources-table-info">
                                <div className="sources-table-name">{(t.id || "").split(".").pop()}</div>
                                <div className="sources-table-desc">
                                  {t.desc}{t.rows ? ` · ${t.rows}` : ""}
                                </div>
                              </div>
                              {/* Only show the Trash icon when the table is already in
                                  the basket — Plus is implicit (clicking the row adds). */}
                              {active && (
                                <span className="sources-table-toggle" title="Sepetten çıkar">
                                  <Trash2 size={12} strokeWidth={1.8} />
                                </span>
                              )}
                            </button>
                            <button
                              type="button"
                              className="sources-table-eye"
                              onClick={(e) => { e.stopPropagation(); onOpenDocs && onOpenDocs(t); }}
                              title="Tablo dökümanını göster"
                            >
                              <Eye size={12} strokeWidth={1.8} />
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div className="sidebar-section sidebar-section--chat">
          <ChatPanel {...chat} />
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
};

function summariseSuggestion(s) {
  switch (s.kind) {
    case "pin_filter": return `interactive filter ${s.filter_id} → pinned`;
    case "add_filter": {
      const tail = s.from ? `${s.from} – ${s.to}` : (s.values ? s.values.join(", ") : (s.value ?? ""));
      return `${s.mode || "pinned"} · ${s.concept} ${s.op} ${tail}`;
    }
    case "add_projection_column": return `${s.alias}.${s.column}`;
    case "confirm_join": return `${s.left_alias}.${s.left_column} ↔ ${s.right_alias}.${s.right_column} (${s.kind_of_join || "inner"})`;
    case "create_aggregate": {
      const grp = (s.group_by || []).join(", ");
      const mes = (s.measures || []).map((m) => `${m.fn}(${m.column})`).join(", ");
      return `${s.source_alias} → ${s.new_alias} · group_by [${grp}] · ${mes}`;
    }
    default: return JSON.stringify(s).slice(0, 100);
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

function PreviewDrawer({ preview, loading, height, onResizeStart, onClose, onSaveFilters, onSaveAsTable, onGridReady, savedGridState, previewLabel }) {
  const apiRef = useRef(null);

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
          <button className="ts-btn ts-btn--sm" disabled={!preview || preview.error} onClick={onSaveFilters} title="Görünür kolonları + aktif filtreleri scope'a yaz">
            <Save size={13} /> Görünümü kaydet
          </button>
          <button
            className="ts-btn ts-btn--sm"
            disabled={!preview || preview.error || isDerived}
            onClick={onSaveAsTable}
            title={isDerived ? "Türetilmiş tablodan yeniden agregat yapılamaz" : "Gruplama/aggregation'ı yeni bir tablo olarak kaydet"}
          >
            <Database size={13} /> Tablo olarak kaydet
          </button>
          <button className="hz-icon-btn" onClick={onClose}><X size={15} /></button>
        </div>
      </div>
      <div className="hz-preview-body">
        {loading && <p className="hz-muted" style={{ padding: 10 }}>Yükleniyor…</p>}
        {!loading && preview && preview.error && <p className="hz-error" style={{ margin: 10 }}>{preview.error}</p>}
        {!loading && preview && !preview.error && (
          <>
            {isDerived && (
              <p className="hz-muted" style={{ padding: "6px 10px", fontSize: 11 }}>
                Türetilmiş tablo · kaynak tablonun örneği üzerinde hesaplandı. Sunum tam veri üzerinde yeniden hesaplayacak.
              </p>
            )}
            <div className="ag-theme-alpine" style={{ width: "100%", height: "100%" }}>
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
          </>
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
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [drawerH, setDrawerH] = useState(260);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [toast, setToast] = useState(null);
  const [gridStateByAlias, setGridStateByAlias] = useState({}); // per-alias AG Grid state (filters + columns), restored on re-open
  const gridApiRef = useRef(null);

  const [nodes, setNodes, onNodesChange] = useNodesStateCompat(() => initialNodes(DATA.scope));
  const edges = useMemo(() => buildEdges(scope), [scope]);

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
          return item ? { ...n, data: enrichNodeData(item, scope) } : n;
        });
      const known = new Set(kept.map((n) => n.id));
      const added = [];
      scope.basket.forEach((item, i) => {
        if (known.has(item.alias)) return;
        // Populate COLS_BY_ALIAS for newly-added derived items so the node
        // can display them in the "Diğer Kolonlar" handle.
        if (item.derivation && !COLS_BY_ALIAS[item.alias]) {
          const srcCols = Object.fromEntries((COLS_BY_ALIAS[item.derivation.source_alias] || []).map((c) => [c.name, c]));
          COLS_BY_ALIAS[item.alias] = [
            ...(item.derivation.group_by || []).map((g) => ({ name: g, concept: srcCols[g]?.concept || null, join_key: true })),
            ...(item.derivation.measures || []).map((m) => ({ name: m.as, concept: null, join_key: false })),
          ];
        }
        const seq = kept.length + added.length;
        added.push({
          id: item.alias, type: "tableNode",
          position: { x: 100 + (seq % 3) * 340, y: 100 + Math.floor(seq / 3) * 240 },
          data: enrichNodeData(item, scope),
        });
      });
      return [...kept, ...added];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

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
        if (data && data.ok) setScope(data.scope);
      }).catch(() => {});
      return next;
    });
    setNodes((nds) => [...nds, {
      id: alias, type: "tableNode",
      position: { x: 80 + (nds.length % 3) * 340, y: 80 + Math.floor(nds.length / 3) * 240 },
      data: enrichNodeData(item, scope),
    }]);
  };

  const removeTable = (alias) => {
    setScope((s) => ({
      ...s,
      basket: s.basket.filter((b) => b.alias !== alias),
      joins: s.joins.filter((j) => j.left.alias !== alias && j.right.alias !== alias),
      filters: { ...s.filters, raw: (s.filters.raw || []).filter((f) => f.alias !== alias) },
    }));
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
      if (item.derivation) {
        // Derived: fetch source's raw rows, aggregate in-browser. Preview is a
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

  const onNodeClick = useCallback((_e, node) => showPreview(node.id), [showPreview]);

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
      setScope((s) => ({
        ...s,
        filters: {
          ...s.filters,
          pinned: [...(s.filters.pinned || []), ...pinned],
          // replace previous raw filters for this alias with the current grid state
          raw: [...(s.filters.raw || []).filter((f) => f.alias !== alias), ...raw],
        },
      }));
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
      <header className="hz-topbar">
        <div className="hz-topbar-left">
          <a className="hz-back" href={LIST_URL}><ChevronLeft size={14} /> Raporlar</a>
          <span className="hz-eyebrow">HAZIRLIK</span>
          <span className="hz-title">{DATA.title}</span>
        </div>
        <button className="ts-btn ts-btn--primary" disabled={busy} onClick={goToSunum}>
          {busy ? "Hazırlanıyor…" : <>Sunum'a geç <ArrowRight size={15} /></>}
        </button>
      </header>

      {err && <div className="hz-error hz-error--bar">{err}</div>}

      <div className="hz-body">
        <SourcesSidebar
          scope={scope} onToggleTable={toggleTable} onRemove={removeTable}
          onOpenDocs={(t) => setDocsTable((cur) => (cur && cur.id === t.id ? null : t))}
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
              onConnect={onConnect} onEdgeClick={onEdgeClick} onNodeClick={onNodeClick}
              nodeTypes={NODE_TYPES} fitView proOptions={{ hideAttribution: true }}
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
        <BuildConfirmModal preview={buildPreview} onConfirm={confirmBuild} onCancel={cancelBuild} />
      )}
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
