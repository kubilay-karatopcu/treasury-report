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
} from "lucide-react";

const DATA = JSON.parse(document.getElementById("hazirlik-data").textContent);
const PID = DATA.presentation_id;
const _path = window.location.pathname;
const BUILD_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/build`);
const PREVIEW_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/preview`);
const LIST_URL = _path.slice(0, _path.indexOf("/hazirlik")) + "/";

const COLS_BY_ALIAS = DATA.columns_by_alias || {};
const SUGGESTED = DATA.suggested_edges || [];
const DOMAINS = DATA.catalog?.domains || [];

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
  const pinned = (scope?.filters?.pinned || []).filter((f) => (f.applies_to || []).includes(alias));
  const raw = (scope?.filters?.raw || []).filter((f) => f.alias === alias);
  return { pinned, raw };
}
function summarizeFilter(f) {
  if (f.op === "between") return `${f.from}…${f.to}`;
  if (f.values && f.values.length) return f.values.slice(0, 3).join(", ") + (f.values.length > 3 ? "…" : "");
  if (f.value != null) return String(f.value);
  return "";
}
function enrichNodeData(item, scope) {
  return { ...nodeData(item), activeFilters: filtersForAlias(scope, item.alias) };
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
  const { item, desc, size, colCount, keyCols, derived, activeFilters, color } = data;
  const cached = item.routing?.decision === "cached";
  const filterCount = (activeFilters?.pinned?.length || 0) + (activeFilters?.raw?.length || 0);
  const headStyle = !derived && color ? { background: color } : undefined;
  return (
    <div className={`hz-node${derived ? " hz-node--derived" : ""}`}>
      <div className="hz-node-head" style={headStyle}>
        <span className="hz-node-alias"><Database size={12} /> {derived ? item.alias : `${item.table_ref.schema}.${item.table_ref.name}`}</span>
        <span className={`hz-badge hz-badge--${derived ? "derived" : (cached ? "cached" : "lazy")}`}>
          {derived ? "türetilmiş" : (cached ? "cached" : "lazy")}
        </span>
      </div>
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

function buildEdges(scope) {
  const confirmed = new Set(scope.joins.map(
    (j) => joinKey(j.left.alias, j.left.column, j.right.alias, j.right.column)));
  const edges = scope.joins.map((j) => ({
    id: j.id, source: j.left.alias, target: j.right.alias,
    label: `${j.kind}: ${j.left.column}=${j.right.column}`,
    className: "hz-edge hz-edge--confirmed", data: { confirmed: true, join: j },
  }));
  const aliases = new Set(scope.basket.map((b) => b.alias));
  SUGGESTED.forEach((s, i) => {
    if (!aliases.has(s.left.alias) || !aliases.has(s.right.alias)) return;
    if (confirmed.has(joinKey(s.left.alias, s.left.column, s.right.alias, s.right.column))) return;
    edges.push({
      id: `sug_${i}`, source: s.left.alias, target: s.right.alias,
      label: "öneri", className: "hz-edge hz-edge--suggested",
      data: { suggested: true, edge: s },
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
            {lc.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
            {lc.length === 0 && <option value="">(kolon yok)</option>}
          </select>
        </label>
        <label className="hz-field">{right}
          <select value={rcol} onChange={(e) => setRcol(e.target.value)}>
            {rc.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
            {rc.length === 0 && <option value="">(kolon yok)</option>}
          </select>
        </label>
      </div>
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

// ── Left sidebar (Sunum design: source categories + chat) ────────────────────

function SourcesSidebar({ scope, onToggleTable, onRemove }) {
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
              return (
                <div key={d.id} className={`sources-domain${isOpen ? " is-open" : ""}`}>
                  <button type="button" className="sources-domain-header"
                    onClick={() => setOpen((o) => ({ ...o, [d.id]: !o[d.id] }))}>
                    <ChevronRight size={12} className="sources-domain-chevron" />
                    <Database size={14} className="sources-domain-icon" />
                    <span className="sources-domain-label">{d.label}</span>
                    {cnt > 0 && <span className="sources-domain-count">{cnt}</span>}
                  </button>
                  {isOpen && (
                    <div className="sources-tables">
                      {(d.tables || []).map((t) => {
                        const active = inBasket.has(t.id);
                        return (
                          <div key={t.id} className={`sources-table-wrap${active ? " is-active" : ""}`}>
                            <button type="button" className="sources-table" onClick={() => onToggleTable(t)} title={active ? "Sepetten çıkar" : "Sepete ekle"}>
                              <div className="sources-table-info">
                                <div className="sources-table-name">{(t.id || "").split(".").pop()}</div>
                                <div className="sources-table-desc">{t.desc}{t.rows ? ` · ${t.rows}` : ""}</div>
                              </div>
                              <span className="sources-table-eye-hint">{active ? <Trash2 size={12} /> : <Plus size={12} />}</span>
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
          <div className="chat-box">
            <div className="chat-box-header"><MessageSquare size={11} /><span>Asistan</span></div>
            <div className="chat-messages ts-scroll">
              <div className="chat-empty">Scope asistanı yakında (Stage 2 · 8.f).</div>
            </div>
            <textarea className="chat-input" rows={3} disabled placeholder="Scope hakkında soru sor… (yakında)" />
            <div className="chat-footer">
              <span className="chat-footer-hint">⌘/Ctrl + Enter ile gönder</span>
              <button type="button" className="btn-primary" disabled>Gönder</button>
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}

// ── AG Grid preview drawer (resizable) ───────────────────────────────────────

function PreviewDrawer({ preview, loading, height, onResizeStart, onClose, onSaveFilters, onSaveAsTable, onGridReady, savedGridState, previewLabel }) {
  const handleReady = (p) => {
    if (onGridReady) onGridReady(p);
    if (savedGridState) {
      try {
        if (savedGridState.columnState) p.api.applyColumnState({ state: savedGridState.columnState, applyOrder: true });
        if (savedGridState.filterModel) p.api.setFilterModel(savedGridState.filterModel);
      } catch (e) { /* ignore restore errors */ }
    }
  };
  const colDefs = useMemo(() => (preview?.data_columns || []).map((c) => ({
    field: c, headerName: c, sortable: true, resizable: true, filter: true, minWidth: 110, flex: 1,
    enableRowGroup: true, enablePivot: true, enableValue: true,
  })), [preview?.data_columns]);
  const rowData = useMemo(() => {
    if (!preview?.rows) return [];
    const cols = preview.data_columns || [];
    return preview.rows.map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])));
  }, [preview]);

  return (
    <div className="hz-preview" style={{ height }}>
      <div className="hz-preview-resize" onMouseDown={onResizeStart} title="Sürükle: yükseklik" />
      <div className="hz-preview-head">
        <span><Database size={14} /> Önizleme{previewLabel ? ` · ${previewLabel}` : ""}{preview && preview.row_count != null ? ` (${preview.row_count} satır)` : ""}</span>
        <div className="hz-preview-actions">
          <button className="ts-btn ts-btn--sm" disabled={!preview || preview.error} onClick={onSaveFilters} title="Grid filtrelerini scope'a kaydet">
            <Save size={13} /> Filtreleri kaydet
          </button>
          <button className="ts-btn ts-btn--sm" disabled={!preview || preview.error} onClick={onSaveAsTable} title="Gruplama/aggregation'ı yeni bir tablo olarak kaydet">
            <Database size={13} /> Tablo olarak kaydet
          </button>
          <button className="hz-icon-btn" onClick={onClose}><X size={15} /></button>
        </div>
      </div>
      <div className="hz-preview-body">
        {loading && <p className="hz-muted" style={{ padding: 10 }}>Yükleniyor…</p>}
        {!loading && preview && preview.error && <p className="hz-error" style={{ margin: 10 }}>{preview.error}</p>}
        {!loading && preview && !preview.error && (
          <div className="ag-theme-alpine" style={{ width: "100%", height: "100%" }}>
            <AgGridReact
              columnDefs={colDefs} rowData={rowData} animateRows
              onGridReady={handleReady}
              sideBar={{ toolPanels: ["columns", "filters"] }}
              rowGroupPanelShow="always"
              pivotPanelShow="always"
            />
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

function App() {
  const [scope, setScope] = useState(DATA.scope);
  const [joinModal, setJoinModal] = useState(null);
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

  // Refresh each node's data (activeFilters, etc.) whenever the scope changes,
  // preserving position (RF owns positions via setNodes).
  useEffect(() => {
    setNodes((nds) => nds.map((n) => {
      const item = scope.basket.find((b) => b.alias === n.id);
      return item ? { ...n, data: enrichNodeData(item, scope) } : n;
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

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
      name: c.name || c, type: c.type, concept: null,
      join_key: !!c.key,   // honour the catalog's explicit `key` flag
    }));
    CATALOG_BY_ID[t.id] = t;
    setScope((s) => ({ ...s, basket: [...s.basket, item] }));
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
      const u = new URL(PREVIEW_URL, window.location.origin);
      u.searchParams.set("schema", item.table_ref.schema);
      u.searchParams.set("table", item.table_ref.name);
      u.searchParams.set("limit", "100");
      const data = await (await fetch(u.pathname + u.search)).json();
      setPreview({ alias, ...data });
    } catch (e) { setPreview({ alias, error: String(e) }); }
    finally { setPreviewLoading(false); }
  }, [scope]);

  const onNodeClick = useCallback((_e, node) => showPreview(node.id), [showPreview]);

  const saveFilters = () => {
    if (!gridApiRef.current || !preview) return;
    const model = gridApiRef.current.getFilterModel();
    const colMeta = Object.fromEntries((COLS_BY_ALIAS[preview.alias] || []).map((c) => [c.name, c]));
    const { pinned, raw } = agModelToFilters(model, preview.alias, colMeta);
    if (pinned.length === 0 && raw.length === 0) { setToast("Kaydedilecek aktif filtre yok."); return; }
    setScope((s) => ({
      ...s,
      filters: {
        ...s.filters,
        pinned: [...(s.filters.pinned || []), ...pinned],
        // replace previous raw filters for this alias with the current grid state
        raw: [...(s.filters.raw || []).filter((f) => f.alias !== preview.alias), ...raw],
      },
    }));
    setToast(`${pinned.length + raw.length} filtre kaydedildi.`);
    const snap = captureGridState();
    if (snap) setGridStateByAlias((s) => ({ ...s, [preview.alias]: snap }));
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

  const goToSunum = async () => {
    setBusy(true); setErr(null);
    const pos = Object.fromEntries(nodes.map((n) => [n.id, n.position]));
    const finalScope = {
      ...scope,
      basket: scope.basket.map((b) => pos[b.alias] ? { ...b, layout: { x: pos[b.alias].x, y: pos[b.alias].y } } : b),
    };
    try {
      const data = await (await fetch(BUILD_URL, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope: finalScope }),
      })).json();
      if (!data.ok) { setErr((data.errors || ["Bilinmeyen hata"]).join(" · ")); setBusy(false); return; }
      window.location.href = data.redirect;
    } catch (e) { setErr(String(e)); setBusy(false); }
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
        <SourcesSidebar scope={scope} onToggleTable={toggleTable} onRemove={removeTable} />
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
