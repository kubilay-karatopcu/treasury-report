/* Hazırlık (Stage 2 / Prepare) — ChartDB-style ER editor. Phase 8.b redesign.
 *
 * Layout mirrors Sunum: left = basket + chat; right = React Flow ER canvas
 * (top) + preview drawer (bottom). Tables are nodes whose column rows carry
 * connection handles; an edge between two column handles IS a join. Filtering
 * happens per-column (concept → concept filter, else raw WHERE). Everything is
 * saved to the scope contract and consumed by Sunum (spec §6R).
 *
 * Built at the office (build.sh / npm) — requires @xyflow/react.
 */
import { createRoot } from "react-dom/client";
import { useCallback, useMemo, useState } from "react";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  Handle, Position, useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  X, Plus, Trash2, Lock, Database, ArrowRight, ChevronLeft, SlidersHorizontal,
} from "lucide-react";

const DATA = JSON.parse(document.getElementById("hazirlik-data").textContent);
const PID = DATA.presentation_id;
const _path = window.location.pathname;
const BUILD_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/build`);
const PREVIEW_URL = _path.replace(`/hazirlik/${PID}`, `/${PID}/scope/preview`);
const LIST_URL = _path.slice(0, _path.indexOf("/hazirlik")) + "/";

const CONCEPTS = DATA.concepts || [];
const CONCEPT_BY_ID = Object.fromEntries(CONCEPTS.map((c) => [c.id, c]));
const COLS_BY_ALIAS = DATA.columns_by_alias || {};
const SUGGESTED = DATA.suggested_edges || [];

const CATALOG_TABLES = (() => {
  const out = [];
  (DATA.catalog?.domains || []).forEach((d) =>
    (d.tables || []).forEach((t) => out.push({ ...t, domain: d.label })));
  return out;
})();

const clone = (o) => JSON.parse(JSON.stringify(o));

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

const joinKey = (l, lc, r, rc) => [`${l}.${lc}`, `${r}.${rc}`].sort().join("—");

// ── Modal shell (reuses editor.css .ts-modal) ──────────────────────────────

function Modal({ title, onClose, children, footer, size = "md" }) {
  return (
    <div className="ts-modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`ts-modal ts-modal--${size}`} role="dialog">
        <header className="ts-modal-header">
          <h3 className="ts-modal-title">{title}</h3>
          <button className="ts-modal-close" onClick={onClose} aria-label="Kapat"><X size={18} /></button>
        </header>
        <div className="ts-modal-body ts-scroll">{children}</div>
        {footer && <footer className="ts-modal-footer">{footer}</footer>}
      </div>
    </div>
  );
}

// ── Table node ───────────────────────────────────────────────────────────────

function TableNode({ data }) {
  const { item, columns, onColumnClick } = data;
  const cached = item.routing?.decision === "cached";
  return (
    <div className="hz-node">
      <div className="hz-node-head">
        <span className="hz-node-alias"><Database size={13} /> {item.alias}</span>
        <span className={`hz-badge hz-badge--${cached ? "cached" : "lazy"}`}>
          {cached ? "cached" : "lazy"}
        </span>
      </div>
      <div className="hz-node-sub">{item.table_ref.schema}.{item.table_ref.name}</div>
      <div className="hz-node-cols">
        {(columns || []).map((col) => (
          <div key={col.name} className="hz-node-col" onClick={() => onColumnClick(item.alias, col)}>
            <Handle type="target" position={Position.Left} id={col.name} className="hz-handle" />
            <span className="hz-col-name">{col.name}</span>
            {col.concept
              ? <span className="hz-col-concept" title={col.concept}>{col.concept}</span>
              : <span className="hz-col-concept hz-col-concept--none">—</span>}
            <Handle type="source" position={Position.Right} id={col.name} className="hz-handle" />
          </div>
        ))}
        {(!columns || columns.length === 0) &&
          <div className="hz-node-col hz-muted">(kolon bilgisi yok)</div>}
      </div>
    </div>
  );
}

const NODE_TYPES = { tableNode: TableNode };

// ── Initial nodes ──────────────────────────────────────────────────────────

function initialNodes(scope, onColumnClick) {
  return scope.basket.map((item, i) => ({
    id: item.alias,
    type: "tableNode",
    position: item.layout
      ? { x: item.layout.x, y: item.layout.y }
      : { x: 40 + (i % 3) * 300, y: 40 + Math.floor(i / 3) * 280 },
    data: { item, columns: COLS_BY_ALIAS[item.alias] || [], onColumnClick },
  }));
}

function buildEdges(scope) {
  const confirmed = new Set(scope.joins.map(
    (j) => joinKey(j.left.alias, j.left.column, j.right.alias, j.right.column)));
  const edges = scope.joins.map((j) => ({
    id: j.id, source: j.left.alias, sourceHandle: j.left.column,
    target: j.right.alias, targetHandle: j.right.column,
    label: j.kind, className: "hz-edge hz-edge--confirmed",
    data: { confirmed: true, join: j },
  }));
  const aliases = new Set(scope.basket.map((b) => b.alias));
  SUGGESTED.forEach((s, i) => {
    if (!aliases.has(s.left.alias) || !aliases.has(s.right.alias)) return;
    if (confirmed.has(joinKey(s.left.alias, s.left.column, s.right.alias, s.right.column))) return;
    edges.push({
      id: `sug_${i}`, source: s.left.alias, sourceHandle: s.left.column,
      target: s.right.alias, targetHandle: s.right.column,
      label: s.source && s.source.startsWith("shared_concept") ? "≈ concept" : "fk",
      className: "hz-edge hz-edge--suggested",
      data: { suggested: true, edge: s },
    });
  });
  return edges;
}

// ── Filter popover (concept or raw) ──────────────────────────────────────────

function FilterPopover({ ctx, onSave, onClose }) {
  const { alias, col } = ctx;
  const concept = col.concept ? CONCEPT_BY_ID[col.concept] : null;
  const ops = concept ? concept.ops : ["eq", "in", "between"];
  const [op, setOp] = useState(ops[0]);
  const [values, setValues] = useState(new Set());
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [value, setValue] = useState("");

  const isBetween = op === "between";
  const isIn = op === "in" || op === "not_in";
  const canon = concept ? (concept.canonical_values || []) : [];
  const toggle = (v) => { const n = new Set(values); n.has(v) ? n.delete(v) : n.add(v); setValues(n); };

  const save = () => {
    if (concept) {
      const f = { id: `pf_${col.concept}_${Date.now().toString(36)}`.slice(0, 58),
        concept: col.concept, op, applies_to: [alias] };
      if (isBetween) { f.from = from; f.to = to; }
      else if (isIn) { f.values = [...values]; }
      else { f.value = value; }
      onSave("concept", f);
    } else {
      const f = { id: `rf_${col.name.toLowerCase()}_${Date.now().toString(36)}`.slice(0, 58),
        alias, column: col.name, op };
      if (isBetween) { f.from = from; f.to = to; }
      else if (isIn) { f.values = value.split(",").map((s) => s.trim()).filter(Boolean); }
      else { f.value = value; }
      onSave("raw", f);
    }
  };

  return (
    <Modal title={`Filtre · ${alias}.${col.name}`} onClose={onClose} size="sm" footer={
      <>
        <button className="ts-btn" onClick={onClose}>Vazgeç</button>
        <button className="ts-btn ts-btn--primary" onClick={save}>Ekle</button>
      </>
    }>
      <div className="hz-pop-meta">
        {concept
          ? <>Concept: <strong>{concept.label}</strong> <span className="hz-muted">({col.concept})</span></>
          : <span className="hz-muted">Concept yok — hard-coded filtre olarak kaydedilecek.</span>}
      </div>
      <label className="hz-field">Operatör
        <select value={op} onChange={(e) => setOp(e.target.value)}>
          {ops.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </label>
      {isBetween && (
        <div className="hz-row">
          <label className="hz-field">Başlangıç<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label className="hz-field">Bitiş<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        </div>
      )}
      {isIn && concept && canon.length > 0 && (
        <div className="hz-field">Değerler
          <div className="hz-col-grid">
            {canon.map((v) => (
              <label key={v} className="hz-check">
                <input type="checkbox" checked={values.has(v)} onChange={() => toggle(v)} /> {v}
              </label>
            ))}
          </div>
        </div>
      )}
      {isIn && (!concept || canon.length === 0) && (
        <label className="hz-field">Değerler (virgülle)
          <input type="text" value={value} onChange={(e) => setValue(e.target.value)} placeholder="TRY, USD" />
        </label>
      )}
      {!isBetween && !isIn && (
        <label className="hz-field">Değer
          <input type="text" value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
      )}
    </Modal>
  );
}

// ── Add-columns popover (after a join is drawn) ──────────────────────────────

function AddColumnsPopover({ join, onSave, onClose }) {
  const cols = COLS_BY_ALIAS[join.right.alias] || [];
  const [sel, setSel] = useState(new Set());
  const toggle = (c) => { const n = new Set(sel); n.has(c) ? n.delete(c) : n.add(c); setSel(n); };
  return (
    <Modal title={`Kolon ekle · ${join.right.alias} → ${join.left.alias}`} onClose={onClose} footer={
      <>
        <button className="ts-btn" onClick={onClose}>Atla</button>
        <button className="ts-btn ts-btn--primary" onClick={() => onSave([...sel])}>Ekle</button>
      </>
    }>
      <p className="hz-muted">Join kuruldu ({join.left.alias}.{join.left.column} → {join.right.alias}.{join.right.column}).
        {join.right.alias} tablosundan hangi kolonları {join.left.alias}'a getirelim?</p>
      <div className="hz-col-grid">
        {cols.map((c) => (
          <label key={c.name} className="hz-check">
            <input type="checkbox" checked={sel.has(c.name)} onChange={() => toggle(c.name)} /> {c.name}
          </label>
        ))}
        {cols.length === 0 && <p className="hz-muted">Bu tablo için kolon bilgisi yok.</p>}
      </div>
    </Modal>
  );
}

// ── Add-table modal ──────────────────────────────────────────────────────────

function AddTableModal({ onAdd, onClose }) {
  const [q, setQ] = useState("");
  const results = useMemo(() => {
    const s = q.trim().toLowerCase();
    return CATALOG_TABLES.filter((t) =>
      !s || t.id.toLowerCase().includes(s) || (t.desc || "").toLowerCase().includes(s)).slice(0, 50);
  }, [q]);
  return (
    <Modal title="Tablo ekle" onClose={onClose} size="lg">
      <input className="hz-search" autoFocus placeholder="Tablo ara…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="hz-results">
        {results.map((t) => (
          <div key={t.id} className="hz-result" onClick={() => onAdd(t)}>
            <div><strong>{t.id}</strong> <span className="hz-muted">· {t.domain}</span></div>
            <div className="hz-muted">{t.desc || ""}</div>
          </div>
        ))}
        {results.length === 0 && <p className="hz-muted">Sonuç yok.</p>}
      </div>
    </Modal>
  );
}

// ── Preview drawer ───────────────────────────────────────────────────────────

function PreviewDrawer({ preview, loading, onClose }) {
  return (
    <div className="hz-preview">
      <div className="hz-preview-head">
        <span><Database size={14} /> Önizleme{preview ? ` · ${preview.alias}` : ""}{preview && preview.row_count != null ? ` (${preview.row_count} satır örnek)` : ""}</span>
        <button className="hz-icon-btn" onClick={onClose}><X size={15} /></button>
      </div>
      <div className="hz-preview-body ts-scroll">
        {loading && <p className="hz-muted">Yükleniyor…</p>}
        {!loading && preview && preview.error && <p className="hz-error">{preview.error}</p>}
        {!loading && preview && !preview.error && (
          <table className="hz-table-grid">
            <thead><tr>{preview.data_columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {preview.rows.slice(0, 50).map((r, i) => (
                <tr key={i}>{r.map((v, j) => <td key={j}>{v === null ? "" : String(v)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── App ──────────────────────────────────────────────────────────────────────

function App() {
  const [scope, setScope] = useState(DATA.scope);
  const [filterCtx, setFilterCtx] = useState(null);
  const [addCols, setAddCols] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const onColumnClick = useCallback((alias, col) => setFilterCtx({ alias, col }), []);

  // React Flow owns node positions/drag; edges are derived from the scope.
  const [nodes, setNodes, onNodesChange] = useNodesStateCompat(
    () => initialNodes(DATA.scope, onColumnClick));
  const edges = useMemo(() => buildEdges(scope), [scope]);

  const addJoin = useCallback((la, lc, ra, rc, kind = "lookup") => {
    const id = `j_${la}_${ra}_${Math.random().toString(36).slice(2, 6)}`;
    const join = { id, kind, left: { alias: la, column: lc }, right: { alias: ra, column: rc } };
    setScope((s) => {
      const key = joinKey(la, lc, ra, rc);
      if (s.joins.some((j) => joinKey(j.left.alias, j.left.column, j.right.alias, j.right.column) === key)) return s;
      return { ...s, joins: [...s.joins, join] };
    });
    return join;
  }, []);

  const onConnect = useCallback((params) => {
    if (!params.source || !params.target || params.source === params.target) return;
    const join = addJoin(params.source, params.sourceHandle, params.target, params.targetHandle);
    setAddCols({ join });
  }, [addJoin]);

  const onEdgeClick = useCallback((_e, edge) => {
    if (edge.data?.suggested) {
      const s = edge.data.edge;
      addJoin(s.left.alias, s.left.column, s.right.alias, s.right.column, s.kind || "lookup");
    } else if (edge.data?.confirmed) {
      if (window.confirm("Bu join silinsin mi?")) {
        const jid = edge.data.join.id;
        setScope((s) => ({
          ...s,
          joins: s.joins.filter((j) => j.id !== jid),
          basket: s.basket.map((b) => ({
            ...b,
            projection: { ...b.projection, joined: (b.projection.joined || []).filter((c) => c.via_join !== jid) },
          })),
        }));
      }
    }
  }, [addJoin]);

  const saveJoinedColumns = (join, columns) => {
    setScope((s) => ({
      ...s,
      basket: s.basket.map((b) => b.alias === join.left.alias
        ? { ...b, projection: { ...b.projection, joined: [
            ...(b.projection.joined || []),
            ...columns.map((c) => ({ via_join: join.id, column: c })),
          ] } }
        : b),
    }));
    setAddCols(null);
  };

  const saveFilter = (kind, f) => {
    setScope((s) => {
      const filters = { ...s.filters };
      if (kind === "concept") filters.pinned = [...(filters.pinned || []), f];
      else filters.raw = [...(filters.raw || []), f];
      return { ...s, filters };
    });
    setFilterCtx(null);
  };

  const removeFilter = (kind, id) => setScope((s) => {
    const filters = { ...s.filters };
    filters[kind] = (filters[kind] || []).filter((f) => f.id !== id);
    return { ...s, filters };
  });

  const addTable = (t) => {
    const [schema, ...rest] = t.id.split(".");
    const name = rest.length ? rest.join(".") : schema;
    const realSchema = rest.length ? schema : "";
    const alias = makeAlias(name, scope.basket.map((b) => b.alias));
    const item = {
      table_ref: { schema: realSchema || schema, name }, alias,
      projection: { columns: (t.columns || []).map((c) => c.name), include_all: (t.columns || []).length === 0 },
      routing: { decision: "cached", decided_by: "system", estimated_bytes: 0 },
    };
    COLS_BY_ALIAS[alias] = (t.columns || []).map((c) => ({ name: c.name, type: c.type, concept: null, lookup: null }));
    setScope((s) => ({ ...s, basket: [...s.basket, item] }));
    setNodes((nds) => [...nds, {
      id: alias, type: "tableNode",
      position: { x: 60 + (nds.length % 3) * 300, y: 60 + Math.floor(nds.length / 3) * 280 },
      data: { item, columns: COLS_BY_ALIAS[alias], onColumnClick },
    }]);
    setAddOpen(false);
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

  const showPreview = useCallback(async (alias) => {
    const item = scope.basket.find((b) => b.alias === alias);
    if (!item) return;
    setPreviewLoading(true);
    setPreview({ alias });
    try {
      const u = new URL(PREVIEW_URL, window.location.origin);
      u.searchParams.set("schema", item.table_ref.schema);
      u.searchParams.set("table", item.table_ref.name);
      u.searchParams.set("limit", "50");
      const resp = await fetch(u.pathname + u.search);
      const data = await resp.json();
      setPreview({ alias, ...data });
    } catch (e) {
      setPreview({ alias, error: String(e) });
    } finally {
      setPreviewLoading(false);
    }
  }, [scope]);

  const onNodeClick = useCallback((_e, node) => showPreview(node.id), [showPreview]);

  const goToSunum = async () => {
    setBusy(true); setErr(null);
    const posByAlias = Object.fromEntries(nodes.map((n) => [n.id, n.position]));
    const finalScope = {
      ...scope,
      basket: scope.basket.map((b) => posByAlias[b.alias]
        ? { ...b, layout: { x: posByAlias[b.alias].x, y: posByAlias[b.alias].y } } : b),
    };
    try {
      const resp = await fetch(BUILD_URL, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: finalScope }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) { setErr((data.errors || ["Bilinmeyen hata"]).join(" · ")); setBusy(false); return; }
      window.location.href = data.redirect;
    } catch (e) { setErr(String(e)); setBusy(false); }
  };

  const pinned = scope.filters?.pinned || [];
  const raw = scope.filters?.raw || [];

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
        <aside className="hz-left">
          <div className="hz-left-section">
            <div className="hz-section-head">
              <strong>Sepet ({scope.basket.length})</strong>
              <button className="ts-btn ts-btn--sm" onClick={() => setAddOpen(true)}><Plus size={13} /> Tablo</button>
            </div>
            {scope.basket.map((b) => (
              <div key={b.alias} className="hz-basket-row" onClick={() => showPreview(b.alias)}>
                <span><Database size={12} /> {b.alias}</span>
                <button className="hz-icon-btn hz-danger" title="Çıkar"
                  onClick={(e) => { e.stopPropagation(); removeTable(b.alias); }}><Trash2 size={13} /></button>
              </div>
            ))}
            {scope.basket.length === 0 && <p className="hz-muted">Sepet boş — tablo ekleyin.</p>}
          </div>

          {(pinned.length > 0 || raw.length > 0) && (
            <div className="hz-left-section">
              <strong>Filtreler</strong>
              {pinned.map((f) => (
                <div key={f.id} className="hz-filter-mini">
                  <span><Lock size={11} /> {f.concept} {f.op} {f.op === "between" ? `${f.from}…${f.to}` : (f.values || [f.value]).join(",")}</span>
                  <button className="hz-icon-btn hz-danger" onClick={() => removeFilter("pinned", f.id)}><X size={12} /></button>
                </div>
              ))}
              {raw.map((f) => (
                <div key={f.id} className="hz-filter-mini">
                  <span><SlidersHorizontal size={11} /> {f.alias}.{f.column} {f.op} {f.op === "between" ? `${f.from}…${f.to}` : (f.values || [f.value]).join(",")}</span>
                  <button className="hz-icon-btn hz-danger" onClick={() => removeFilter("raw", f.id)}><X size={12} /></button>
                </div>
              ))}
            </div>
          )}

          <div className="hz-left-section hz-chat">
            <strong>Asistan</strong>
            <div className="hz-chat-log hz-muted">Stage-2 scope asistanı yakında (8.f).</div>
            <textarea className="hz-chat-input" placeholder="Scope hakkında soru sor… (yakında)" disabled />
          </div>
        </aside>

        <main className="hz-right">
          <div className="hz-canvas">
            <ReactFlow
              nodes={nodes} edges={edges}
              onNodesChange={onNodesChange}
              onConnect={onConnect}
              onEdgeClick={onEdgeClick}
              onNodeClick={onNodeClick}
              nodeTypes={NODE_TYPES}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={16} />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
          </div>
          {preview && <PreviewDrawer preview={preview} loading={previewLoading} onClose={() => setPreview(null)} />}
        </main>
      </div>

      {filterCtx && <FilterPopover ctx={filterCtx} onSave={saveFilter} onClose={() => setFilterCtx(null)} />}
      {addCols && <AddColumnsPopover join={addCols.join} onSave={(cols) => saveJoinedColumns(addCols.join, cols)} onClose={() => setAddCols(null)} />}
      {addOpen && <AddTableModal onAdd={addTable} onClose={() => setAddOpen(false)} />}
    </div>
  );
}

// useNodesState accepts an initial array; wrap to allow a lazy initializer.
function useNodesStateCompat(init) {
  const [initial] = useState(init);
  return useNodesState(initial);
}

createRoot(document.getElementById("hazirlik-root")).render(
  <ReactFlowProvider><App /></ReactFlowProvider>
);
