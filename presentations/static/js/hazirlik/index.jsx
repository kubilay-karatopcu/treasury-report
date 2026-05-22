/* Hazırlık (Stage 2 / Prepare) — standalone React page. Phase 8.b.
 *
 * Reads the server-embedded payload (#hazirlik-data): the current scope
 * contract, the table catalog, available concepts, and concept value
 * distributions. Lets the user edit the basket (add table, projection),
 * pinned/interactive filters, then "Sunum'a geç" → POST /<pid>/scope/build,
 * which validates + fetches cached tables + writes scope_ref + redirects.
 *
 * No routing override here (that's 8.d); routing badges are read-only.
 */
import { createRoot } from "react-dom/client";
import { useMemo, useState } from "react";
import {
  X, Plus, Trash2, Lock, SlidersHorizontal, Database, ArrowRight, Pencil,
} from "lucide-react";

const DATA = JSON.parse(document.getElementById("hazirlik-data").textContent);
const PID = DATA.presentation_id;
const BUILD_URL = window.location.pathname.replace(`/hazirlik/${PID}`, `/${PID}/scope/build`);

const CONCEPTS = DATA.concepts || [];
const CONCEPT_BY_ID = Object.fromEntries(CONCEPTS.map((c) => [c.id, c]));

const CATALOG_TABLES = (() => {
  const out = [];
  (DATA.catalog?.domains || []).forEach((d) =>
    (d.tables || []).forEach((t) => out.push({ ...t, domain: d.label })));
  return out;
})();

function catalogColumnsFor(schema, name) {
  const id = schema ? `${schema}.${name}` : name;
  const t = CATALOG_TABLES.find((x) => x.id === id || x.id.endsWith("." + name));
  return t && t.columns ? t.columns.map((c) => c.name) : [];
}

function humanBytes(n) {
  if (!n || n < 0) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
}

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

const clone = (o) => JSON.parse(JSON.stringify(o));

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

// ── Routing badge (read-only in 8.b) ───────────────────────────────────────

function RoutingBadge({ routing }) {
  const cached = routing?.decision === "cached";
  return (
    <span className={`hz-badge ${cached ? "hz-badge--cached" : "hz-badge--lazy"}`}
          title={`decided_by: ${routing?.decided_by || "system"}`}>
      <span className="hz-dot" />
      {cached ? "cached" : "lazy (Oracle)"} · {humanBytes(routing?.estimated_bytes)}
    </span>
  );
}

// ── Basket card ─────────────────────────────────────────────────────────────

function BasketCard({ item, onEditProjection, onRemove }) {
  const cols = item.projection?.include_all ? ["(tüm kolonlar)"] : (item.projection?.columns || []);
  return (
    <div className="hz-card">
      <div className="hz-card-head">
        <div>
          <div className="hz-alias"><Database size={14} /> {item.alias}</div>
          <div className="hz-table">{item.table_ref.schema}.{item.table_ref.name}</div>
        </div>
        <div className="hz-card-actions">
          <RoutingBadge routing={item.routing} />
          <button className="hz-icon-btn" title="Projeksiyon" onClick={() => onEditProjection(item.alias)}><Pencil size={15} /></button>
          <button className="hz-icon-btn hz-danger" title="Sepetten çıkar" onClick={() => onRemove(item.alias)}><Trash2 size={15} /></button>
        </div>
      </div>
      <div className="hz-cols">
        <span className="hz-cols-label">Kolonlar:</span>
        {cols.map((c) => <span key={c} className="hz-chip">{c}</span>)}
      </div>
    </div>
  );
}

// ── Projection modal ─────────────────────────────────────────────────────────

function ProjectionModal({ item, onSave, onClose }) {
  const available = catalogColumnsFor(item.table_ref.schema, item.table_ref.name);
  const [includeAll, setIncludeAll] = useState(!!item.projection?.include_all);
  const [sel, setSel] = useState(new Set(item.projection?.columns || []));
  const toggle = (c) => {
    const n = new Set(sel);
    n.has(c) ? n.delete(c) : n.add(c);
    setSel(n);
  };
  const cols = available.length ? available : (item.projection?.columns || []);
  return (
    <Modal title={`Projeksiyon — ${item.alias}`} onClose={onClose} footer={
      <>
        <button className="ts-btn" onClick={onClose}>Vazgeç</button>
        <button className="ts-btn ts-btn--primary"
                onClick={() => onSave({ include_all: includeAll, columns: includeAll ? [] : [...sel] })}>
          Kaydet
        </button>
      </>
    }>
      <label className="hz-check">
        <input type="checkbox" checked={includeAll} onChange={(e) => setIncludeAll(e.target.checked)} />
        Tüm kolonları al (include_all)
      </label>
      {!includeAll && (
        <div className="hz-col-grid">
          {cols.length === 0 && <p className="hz-muted">Bu tablo için katalog kolon bilgisi yok — kolonları elle ekleyemiyoruz.</p>}
          {cols.map((c) => (
            <label key={c} className="hz-check">
              <input type="checkbox" checked={sel.has(c)} onChange={() => toggle(c)} /> {c}
            </label>
          ))}
        </div>
      )}
    </Modal>
  );
}

// ── Filter modal (pinned / interactive) ─────────────────────────────────────

function FilterModal({ initial, basketAliases, onSave, onClose }) {
  const isNew = !initial.id;
  const [pinned, setPinned] = useState(initial.pinned ?? true);
  const [concept, setConcept] = useState(initial.concept || (CONCEPTS[0]?.id ?? ""));
  const cdef = CONCEPT_BY_ID[concept] || { ops: ["in"], canonical_values: [] };
  const [op, setOp] = useState(initial.op || cdef.ops[0]);
  const [from, setFrom] = useState(initial.from || "");
  const [to, setTo] = useState(initial.to || "");
  const [values, setValues] = useState(new Set(initial.values || initial.default_values || []));
  const [label, setLabel] = useState(initial.label || cdef.label || "");
  const [appliesTo, setAppliesTo] = useState(new Set(initial.applies_to || basketAliases));

  const toggleVal = (v) => { const n = new Set(values); n.has(v) ? n.delete(v) : n.add(v); setValues(n); };
  const toggleAlias = (a) => { const n = new Set(appliesTo); n.has(a) ? n.delete(a) : n.add(a); setAppliesTo(n); };

  const isBetween = op === "between";
  const isEnum = ["in", "not_in"].includes(op);

  const save = () => {
    const base = {
      id: initial.id || `${pinned ? "pf" : "if"}_${concept}_${Date.now().toString(36)}`.slice(0, 58),
      concept, op, applies_to: [...appliesTo],
    };
    if (isBetween) { base.from = from; base.to = to; }
    if (pinned) {
      if (isEnum) base.values = [...values];
      onSave("pinned", base, initial);
    } else {
      base.label = label || cdef.label || concept;
      if (isEnum) {
        base.allowed_values = cdef.canonical_values || [...values];
        base.default_values = [...values];
      }
      onSave("interactive", base, initial);
    }
  };

  return (
    <Modal title={isNew ? "Filtre ekle" : "Filtreyi düzenle"} onClose={onClose} footer={
      <>
        <button className="ts-btn" onClick={onClose}>Vazgeç</button>
        <button className="ts-btn ts-btn--primary" onClick={save}>Kaydet</button>
      </>
    }>
      <div className="hz-toggle">
        <button className={pinned ? "active" : ""} onClick={() => setPinned(true)}><Lock size={13} /> Pinned (kilitli)</button>
        <button className={!pinned ? "active" : ""} onClick={() => setPinned(false)}><SlidersHorizontal size={13} /> Interactive</button>
      </div>

      <label className="hz-field">Concept
        <select value={concept} disabled={!isNew}
                onChange={(e) => { setConcept(e.target.value); const d = CONCEPT_BY_ID[e.target.value]; setOp(d?.ops?.[0] || "in"); setValues(new Set()); }}>
          {CONCEPTS.map((c) => <option key={c.id} value={c.id}>{c.label} ({c.id})</option>)}
        </select>
      </label>

      <label className="hz-field">Operatör
        <select value={op} onChange={(e) => setOp(e.target.value)}>
          {(cdef.ops || ["in"]).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </label>

      {isBetween && (
        <div className="hz-row">
          <label className="hz-field">Başlangıç<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label className="hz-field">Bitiş<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        </div>
      )}

      {isEnum && (
        <div className="hz-field">Değerler
          <div className="hz-col-grid">
            {(cdef.canonical_values || []).map((v) => (
              <label key={v} className="hz-check">
                <input type="checkbox" checked={values.has(v)} onChange={() => toggleVal(v)} /> {v}
              </label>
            ))}
            {(cdef.canonical_values || []).length === 0 && <p className="hz-muted">Bu concept için kanonik değer tanımı yok.</p>}
          </div>
        </div>
      )}

      {!pinned && (
        <label className="hz-field">Etiket (widget başlığı)
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder={concept} />
        </label>
      )}

      <div className="hz-field">Hangi tablolara uygulansın (applies_to)
        <div className="hz-col-grid">
          {basketAliases.map((a) => (
            <label key={a} className="hz-check">
              <input type="checkbox" checked={appliesTo.has(a)} onChange={() => toggleAlias(a)} /> {a}
            </label>
          ))}
        </div>
      </div>
    </Modal>
  );
}

// ── Add-table modal ──────────────────────────────────────────────────────────

function AddTableModal({ existingAliases, onAdd, onClose }) {
  const [q, setQ] = useState("");
  const results = useMemo(() => {
    const s = q.trim().toLowerCase();
    return CATALOG_TABLES.filter((t) =>
      !s || t.id.toLowerCase().includes(s) || (t.desc || "").toLowerCase().includes(s)).slice(0, 50);
  }, [q]);
  return (
    <Modal title="Tablo ekle" onClose={onClose} size="lg">
      <input className="hz-search" autoFocus placeholder="Tablo ara (ad / açıklama)…"
             value={q} onChange={(e) => setQ(e.target.value)} />
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

// ── Concept distribution chips ───────────────────────────────────────────────

function ConceptChips({ distributions, onPick }) {
  const entries = Object.entries(distributions || {});
  if (entries.length === 0) return null;
  return (
    <section className="hz-section">
      <h2>Concept dağılımları</h2>
      {entries.map(([concept, vals]) => (
        <div key={concept} className="hz-dist-row">
          <span className="hz-dist-label">{CONCEPT_BY_ID[concept]?.label || concept}</span>
          {(vals || []).slice(0, 12).map((v) => (
            <button key={String(v)} className="hz-chip hz-chip--click"
                    title="Bu değerle filtrele" onClick={() => onPick(concept, v)}>{String(v)}</button>
          ))}
        </div>
      ))}
    </section>
  );
}

// ── App ──────────────────────────────────────────────────────────────────────

function App() {
  const [scope, setScope] = useState(DATA.scope);
  const [projAlias, setProjAlias] = useState(null);
  const [filterModal, setFilterModal] = useState(null); // {initial}
  const [addOpen, setAddOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const aliases = scope.basket.map((b) => b.alias);

  const updateProjection = (alias, projection) => {
    const next = clone(scope);
    const it = next.basket.find((b) => b.alias === alias);
    if (it) it.projection = projection;
    setScope(next);
    setProjAlias(null);
  };

  const removeTable = (alias) => {
    const next = clone(scope);
    next.basket = next.basket.filter((b) => b.alias !== alias);
    setScope(next);
  };

  const addTable = (t) => {
    const [schema, ...rest] = t.id.split(".");
    const name = rest.length ? rest.join(".") : schema;
    const realSchema = rest.length ? schema : "";
    const next = clone(scope);
    const alias = makeAlias(name, next.basket.map((b) => b.alias));
    next.basket.push({
      table_ref: { schema: realSchema || schema, name },
      alias,
      projection: { columns: (t.columns || []).map((c) => c.name), include_all: (t.columns || []).length === 0 },
      routing: { decision: "cached", decided_by: "system", estimated_bytes: 0 },
    });
    setScope(next);
    setAddOpen(false);
  };

  const saveFilter = (kind, filt, prev) => {
    const next = clone(scope);
    if (!next.filters) next.filters = { pinned: [], interactive: [] };
    // Remove the previous instance (it may have switched pinned↔interactive).
    if (prev?.id) {
      next.filters.pinned = (next.filters.pinned || []).filter((f) => f.id !== prev.id);
      next.filters.interactive = (next.filters.interactive || []).filter((f) => f.id !== prev.id);
    }
    (next.filters[kind] = next.filters[kind] || []).push(filt);
    setScope(next);
    setFilterModal(null);
  };

  const removeFilter = (kind, id) => {
    const next = clone(scope);
    next.filters[kind] = (next.filters[kind] || []).filter((f) => f.id !== id);
    setScope(next);
  };

  const goToSunum = async () => {
    setBusy(true); setErr(null);
    try {
      const resp = await fetch(BUILD_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        setErr((data.errors || ["Bilinmeyen hata"]).join(" · "));
        setBusy(false);
        return;
      }
      window.location.href = data.redirect;
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  const pinned = scope.filters?.pinned || [];
  const interactive = scope.filters?.interactive || [];

  return (
    <div className="hz-wrap">
      <header className="hz-top">
        <div>
          <div className="hz-eyebrow">Hazırlık</div>
          <h1 className="hz-title">{DATA.title}</h1>
        </div>
        <button className="ts-btn ts-btn--primary hz-go" disabled={busy} onClick={goToSunum}>
          {busy ? "Hazırlanıyor…" : <>Sunum'a geç <ArrowRight size={16} /></>}
        </button>
      </header>

      {err && <div className="hz-error">{err}</div>}

      <section className="hz-section">
        <div className="hz-section-head">
          <h2>Sepet ({scope.basket.length} tablo)</h2>
          <button className="ts-btn" onClick={() => setAddOpen(true)}><Plus size={15} /> Tablo</button>
        </div>
        {scope.basket.length === 0 && <p className="hz-muted">Sepet boş — başlamak için bir tablo ekleyin.</p>}
        {scope.basket.map((it) => (
          <BasketCard key={it.alias} item={it} onEditProjection={setProjAlias} onRemove={removeTable} />
        ))}
      </section>

      <section className="hz-section">
        <div className="hz-section-head">
          <h2>Filtreler</h2>
          <button className="ts-btn" disabled={aliases.length === 0}
                  onClick={() => setFilterModal({ initial: {} })}><Plus size={15} /> Filtre</button>
        </div>

        <div className="hz-filter-group">
          <div className="hz-filter-group-title"><Lock size={13} /> Pinned</div>
          {pinned.length === 0 && <p className="hz-muted">Yok.</p>}
          {pinned.map((f) => (
            <div key={f.id} className="hz-filter-row">
              <span><strong>{f.id}</strong>: {f.concept} {f.op} {f.op === "between" ? `${f.from} – ${f.to}` : (f.values || []).join(", ")}</span>
              <span className="hz-filter-actions">
                <button className="hz-icon-btn" onClick={() => setFilterModal({ initial: { ...f, pinned: true } })}><Pencil size={14} /></button>
                <button className="hz-icon-btn hz-danger" onClick={() => removeFilter("pinned", f.id)}><Trash2 size={14} /></button>
              </span>
            </div>
          ))}
        </div>

        <div className="hz-filter-group">
          <div className="hz-filter-group-title"><SlidersHorizontal size={13} /> Interactive</div>
          {interactive.length === 0 && <p className="hz-muted">Yok.</p>}
          {interactive.map((f) => (
            <div key={f.id} className="hz-filter-row">
              <span><strong>{f.id}</strong>: {f.concept} {f.op} [{(f.default_values || []).join(", ")}] <span className="hz-muted">— {f.label}</span></span>
              <span className="hz-filter-actions">
                <button className="hz-icon-btn" onClick={() => setFilterModal({ initial: { ...f, pinned: false } })}><Pencil size={14} /></button>
                <button className="hz-icon-btn hz-danger" onClick={() => removeFilter("interactive", f.id)}><Trash2 size={14} /></button>
              </span>
            </div>
          ))}
        </div>
      </section>

      <ConceptChips distributions={DATA.distributions}
                    onPick={(concept, v) => setFilterModal({ initial: { concept, op: "in", values: [v], pinned: true } })} />

      {projAlias && (
        <ProjectionModal item={scope.basket.find((b) => b.alias === projAlias)}
                         onSave={(p) => updateProjection(projAlias, p)} onClose={() => setProjAlias(null)} />
      )}
      {filterModal && (
        <FilterModal initial={filterModal.initial} basketAliases={aliases}
                     onSave={saveFilter} onClose={() => setFilterModal(null)} />
      )}
      {addOpen && (
        <AddTableModal existingAliases={aliases} onAdd={addTable} onClose={() => setAddOpen(false)} />
      )}
    </div>
  );
}

createRoot(document.getElementById("hazirlik-root")).render(<App />);
