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
const tableId = (ref) => (ref.schema ? `${ref.schema}.${ref.name}` : ref.name);
const descFor = (ref) => CATALOG_BY_ID[tableId(ref)]?.desc || "";

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
  const { item, desc } = data;
  const cached = item.routing?.decision === "cached";
  return (
    <div className="hz-node">
      <Handle type="target" position={Position.Left} className="hz-handle" />
      <div className="hz-node-head">
        <span className="hz-node-alias"><Database size={13} /> {item.alias}</span>
        <span className={`hz-badge hz-badge--${cached ? "cached" : "lazy"}`}>{cached ? "cached" : "lazy"}</span>
      </div>
      <div className="hz-node-sub">{item.table_ref.schema}.{item.table_ref.name}</div>
      <div className="hz-node-desc">{desc || <span className="hz-muted">(açıklama yok)</span>}</div>
      <Handle type="source" position={Position.Right} className="hz-handle" />
    </div>
  );
}
const NODE_TYPES = { tableNode: TableNode };

function initialNodes(scope) {
  return scope.basket.map((item, i) => ({
    id: item.alias, type: "tableNode",
    position: item.layout ? { x: item.layout.x, y: item.layout.y }
      : { x: 60 + (i % 3) * 320, y: 60 + Math.floor(i / 3) * 220 },
    data: { item, desc: descFor(item.table_ref) },
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

function JoinKeyModal({ left, right, onSave, onClose }) {
  const lc = COLS_BY_ALIAS[left] || [];
  const rc = COLS_BY_ALIAS[right] || [];
  const [lcol, setLcol] = useState(lc[0]?.name || "");
  const [rcol, setRcol] = useState(rc[0]?.name || "");
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

function PreviewDrawer({ preview, loading, height, onResizeStart, onClose, onSaveFilters, onGridReady }) {
  const colDefs = useMemo(() => (preview?.data_columns || []).map((c) => ({
    field: c, headerName: c, sortable: true, resizable: true, filter: true, minWidth: 110, flex: 1,
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
        <span><Table2 size={14} /> Önizleme{preview ? ` · ${preview.alias}` : ""}{preview && preview.row_count != null ? ` (${preview.row_count} satır)` : ""}</span>
        <div className="hz-preview-actions">
          <button className="ts-btn ts-btn--sm" disabled={!preview || preview.error} onClick={onSaveFilters} title="Grid filtrelerini scope'a kaydet">
            <Save size={13} /> Filtreleri kaydet
          </button>
          <button className="hz-icon-btn" onClick={onClose}><X size={15} /></button>
        </div>
      </div>
      <div className="hz-preview-body">
        {loading && <p className="hz-muted" style={{ padding: 10 }}>Yükleniyor…</p>}
        {!loading && preview && preview.error && <p className="hz-error" style={{ margin: 10 }}>{preview.error}</p>}
        {!loading && preview && !preview.error && (
          <div className="ag-theme-alpine" style={{ width: "100%", height: "100%" }}>
            <AgGridReact columnDefs={colDefs} rowData={rowData} animateRows onGridReady={onGridReady} />
          </div>
        )}
      </div>
    </div>
  );
}

// AG Grid filter model → scope filters (concept if column bound, else raw).
function agModelToFilters(model, alias, colMeta) {
  const pinned = [], raw = [];
  for (const [col, m] of Object.entries(model || {})) {
    const concept = colMeta[col]?.concept || null;
    const base = concept
      ? { id: `pf_${concept}_${rid()}`.slice(0, 58), concept, op: "in", applies_to: [alias] }
      : { id: `rf_${col.toLowerCase()}_${rid()}`.slice(0, 58), alias, column: col, op: "eq" };
    const val = m.filter ?? m.dateFrom ?? null;
    if (m.type === "inRange") {
      base.op = "between"; base.from = m.dateFrom ?? m.filter; base.to = m.dateTo ?? m.filterTo;
    } else if (concept) {
      base.op = "in"; base.values = [val];
    } else {
      base.op = "eq"; base.value = val;
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
  const gridApiRef = useRef(null);

  const [nodes, setNodes, onNodesChange] = useNodesStateCompat(() => initialNodes(DATA.scope));
  const edges = useMemo(() => buildEdges(scope), [scope]);

  const addJoin = useCallback((la, lc, ra, rc, kind = "lookup") => {
    setScope((s) => {
      const k = joinKey(la, lc, ra, rc);
      if (s.joins.some((j) => joinKey(j.left.alias, j.left.column, j.right.alias, j.right.column) === k)) return s;
      return { ...s, joins: [...s.joins, { id: `j_${la}_${ra}_${rid()}`, kind, left: { alias: la, column: lc }, right: { alias: ra, column: rc } }] };
    });
  }, []);

  const onConnect = useCallback((p) => {
    if (!p.source || !p.target || p.source === p.target) return;
    setJoinModal({ left: p.source, right: p.target });   // ask for keys, no projection step
  }, []);

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
    COLS_BY_ALIAS[alias] = (t.columns || []).map((c) => ({ name: c.name || c, type: c.type, concept: null }));
    CATALOG_BY_ID[t.id] = t;
    setScope((s) => ({ ...s, basket: [...s.basket, item] }));
    setNodes((nds) => [...nds, {
      id: alias, type: "tableNode",
      position: { x: 80 + (nds.length % 3) * 320, y: 80 + Math.floor(nds.length / 3) * 220 },
      data: { item, desc: t.desc || "" },
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
              preview={preview} loading={previewLoading} height={drawerH}
              onResizeStart={startResize} onClose={() => setPreview(null)}
              onSaveFilters={saveFilters}
              onGridReady={(p) => { gridApiRef.current = p.api; }}
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
