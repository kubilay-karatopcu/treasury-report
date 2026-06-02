/**
 * FilterPanel — the "Filtreleme" tab of the Hazırlık preview drawer.
 *
 * Type-aware filter editors per column (read from the alias's column meta):
 *   - date    → Mutlak (date pickers) / Göreli (today - 30d …) sub-tabs → between
 *   - numeric → > ≥ < ≤ = IN
 *   - string  → get_distinct ⇒ checkbox list (fetched on demand);
 *               otherwise an IN text list
 *
 * The panel only builds value *specs* — `{column, concept, type, op, …}`. The
 * parent (index.jsx) turns each into a pinned (concept) or raw (column) scope
 * filter, attaches the alias/id, and triggers the routing recompute. Relative
 * date exprs are stored verbatim; the backend resolves them at fetch time.
 */
import { useEffect, useMemo, useState } from 'react';
import { _resolveDateExpr, isRelativeDateExpr } from '../shared/dateExpr.js';

const NUM_OPS = [
  { v: 'gt', label: '>' }, { v: 'gte', label: '≥' },
  { v: 'lt', label: '<' }, { v: 'lte', label: '≤' },
  { v: 'between', label: 'aralık' }, { v: 'eq', label: '=' }, { v: 'in', label: 'IN' },
];

function classify(type) {
  const t = (type || '').toUpperCase();
  if (/DATE|TIMESTAMP/.test(t)) return 'date';
  if (/NUMBER|NUMERIC|FLOAT|DECIMAL|DOUBLE|\bINT/.test(t)) return 'num';
  return 'str';
}

function splitList(s) {
  return String(s || '')
    .split(/[,\n]/).map((x) => x.trim()).filter(Boolean);
}
function numOrStr(v) {
  const s = String(v).trim();
  return s !== '' && !Number.isNaN(Number(s)) ? Number(s) : s;
}

function relPreview(from, to) {
  const f = _resolveDateExpr(from);
  const t = _resolveDateExpr(to);
  if (!f && !t) return '';
  return `→ ${f || '…'} – ${t || '…'}`;
}

// Inverse of buildSpecs — seed one editor state from a saved scope filter.
function edFromFilter(t, f) {
  if (t === 'date') {
    const rel = isRelativeDateExpr(f.from) || isRelativeDateExpr(f.to);
    return { mode: rel ? 'rel' : 'abs', from: f.from ?? '', to: f.to ?? '' };
  }
  if (t === 'num') {
    if (f.op === 'in') return { op: 'in', values: (f.values || []).join(', ') };
    if (f.op === 'between') return { op: 'between', from: f.from ?? '', to: f.to ?? '' };
    return { op: f.op || 'gt', value: f.value ?? (f.values || [])[0] ?? '' };
  }
  return null;  // str handled inline (needs the get_distinct flag)
}

// Pre-populate the panel from the alias's already-saved filters so re-saving
// preserves untouched columns (saveFilterPanel replaces this alias's set, so
// without this the columns the user didn't re-enter would be dropped). pinned
// filters are keyed by concept → mapped to the column realizing it.
function editsFromExisting(cols, existing) {
  if (!existing) return {};
  const out = {};
  const byConcept = {};
  cols.forEach((c) => {
    if (c.concept) (byConcept[c.concept] = byConcept[c.concept] || []).push(c.name);
  });
  const apply = (col, f) => {
    if (col._t === 'str') {
      const vals = f.values || (f.value != null ? [f.value] : []);
      out[col.name] = col.get_distinct ? { checked: vals } : { text: vals.join(', ') };
    } else {
      const ed = edFromFilter(col._t, f);
      if (ed) out[col.name] = ed;
    }
  };
  (existing.raw || []).forEach((f) => {
    const col = cols.find((c) => c.name === f.column);
    if (col) apply(col, f);
  });
  (existing.pinned || []).forEach((f) => {
    const name = (byConcept[f.concept] || [])[0];
    const col = name && cols.find((c) => c.name === name);
    if (col) apply(col, f);
  });
  return out;
}

export default function FilterPanel({ alias, columns = [], existing, onSave, onFetchDistinct, saveRef }) {
  const cols = useMemo(
    () => (columns || []).filter((c) => c && c.name).map((c) => ({ ...c, _t: classify(c.type) })),
    [columns]
  );
  // Seed from the alias's saved filters (once per mount; the drawer is keyed by
  // alias, so switching tables remounts with that table's own filters).
  const [edits, setEdits] = useState(() => editsFromExisting(cols, existing));
  const [distinct, setDistinct] = useState({}); // { col: {loading, values, error} }

  const setEd = (name, patch) =>
    setEdits((e) => ({ ...e, [name]: { ...(e[name] || {}), ...patch } }));

  async function loadDistinct(name) {
    setDistinct((d) => ({ ...d, [name]: { loading: true } }));
    try {
      const values = await onFetchDistinct(name);
      setDistinct((d) => ({ ...d, [name]: { loading: false, values: values || [] } }));
    } catch (e) {
      setDistinct((d) => ({ ...d, [name]: { loading: false, error: String(e.message || e) } }));
    }
  }

  function toggleChecked(name, v) {
    setEdits((e) => {
      const cur = (e[name]?.checked) || [];
      const next = cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v];
      return { ...e, [name]: { ...(e[name] || {}), checked: next } };
    });
  }

  function buildSpecs() {
    const specs = [];
    for (const c of cols) {
      const ed = edits[c.name];
      if (!ed) continue;
      let spec = null;
      if (c._t === 'date') {
        const from = (ed.from || '').trim();
        const to = (ed.to || '').trim();
        if (!from && !to) continue;
        spec = { op: 'between', from: from || to, to: to || from };  // single = from==to
      } else if (c._t === 'num') {
        const op = ed.op || 'gt';
        if (op === 'in') {
          const values = splitList(ed.values).map(numOrStr);
          if (!values.length) continue;
          spec = { op: 'in', values };
        } else if (op === 'between') {
          const from = String(ed.from ?? '').trim();
          const to = String(ed.to ?? '').trim();
          if (from === '' || to === '') continue;
          spec = { op: 'between', from: numOrStr(from), to: numOrStr(to) };
        } else {
          const v = String(ed.value ?? '').trim();
          if (v === '') continue;
          spec = { op, value: numOrStr(v) };
        }
      } else {
        const values = c.get_distinct ? ((ed.checked) || []) : splitList(ed.text);
        if (!values.length) continue;
        spec = { op: 'in', values };
      }
      specs.push({ column: c.name, concept: c.concept || null, type: c._t, ...spec });
    }
    return specs;
  }

  async function save() {
    const specs = buildSpecs();
    if (!specs.length) return;
    await onSave(specs);
  }
  // Bridge the save action up to the drawer header's single save button — the
  // panel has no footer button of its own. Re-set every render so the closure
  // (which reads the latest `edits`) stays current.
  useEffect(() => { if (saveRef) saveRef.current = save; });

  if (!cols.length) {
    return <div className="hz-fp-empty">Bu kaynağın kolon bilgisi yok.</div>;
  }

  return (
    <div className="hz-fp">
      <div className="hz-fp-list">
        {cols.map((c) => {
          const ed = edits[c.name] || {};
          const mode = ed.mode || 'abs';
          const dist = distinct[c.name];
          return (
            <div className="hz-fp-row" key={c.name}>
              <div className="hz-fp-name">
                <span className="hz-fp-cn">{c.name}</span>
                <span className="hz-fp-ty">{c._t === 'num' ? 'sayı' : c._t === 'date' ? 'tarih' : 'metin'}</span>
                {c.concept && <span className="hz-fp-concept" title="concept-bağlı">◈ {c.concept}</span>}
              </div>

              {c._t === 'date' && (
                <div className="hz-fp-ed">
                  <div className="hz-fp-modes">
                    <button type="button" className={mode === 'abs' ? 'on' : ''}
                            onClick={() => setEd(c.name, { mode: 'abs' })}>Mutlak</button>
                    <button type="button" className={mode === 'rel' ? 'on' : ''}
                            onClick={() => setEd(c.name, { mode: 'rel' })}>Göreli</button>
                  </div>
                  {mode === 'abs' ? (
                    <div className="hz-fp-daterow">
                      <input type="date" value={_resolveDateExpr(ed.from)}
                             onChange={(e) => setEd(c.name, { from: e.target.value })} />
                      <span className="hz-fp-sep">→</span>
                      <input type="date" value={_resolveDateExpr(ed.to)}
                             onChange={(e) => setEd(c.name, { to: e.target.value })} />
                      <span className="hz-fp-hint">tek tarih için sadece soldaki</span>
                    </div>
                  ) : (
                    <div className="hz-fp-daterow">
                      <input type="text" placeholder="today - 30d" value={ed.from || ''}
                             onChange={(e) => setEd(c.name, { from: e.target.value })} />
                      <span className="hz-fp-sep">→</span>
                      <input type="text" placeholder="today" value={ed.to || ''}
                             onChange={(e) => setEd(c.name, { to: e.target.value })} />
                      <span className="hz-fp-hint">{relPreview(ed.from, ed.to)}</span>
                    </div>
                  )}
                </div>
              )}

              {c._t === 'num' && (
                <div className="hz-fp-ed">
                  <div className="hz-fp-numrow">
                    <select value={ed.op || 'gt'} onChange={(e) => setEd(c.name, { op: e.target.value })}>
                      {NUM_OPS.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
                    </select>
                    {(ed.op || 'gt') === 'in' ? (
                      <input type="text" placeholder="100, 200, 300" value={ed.values || ''}
                             onChange={(e) => setEd(c.name, { values: e.target.value })} />
                    ) : ed.op === 'between' ? (
                      <>
                        <input type="number" placeholder="min" value={ed.from ?? ''}
                               onChange={(e) => setEd(c.name, { from: e.target.value })} />
                        <span className="hz-fp-sep">ve</span>
                        <input type="number" placeholder="max" value={ed.to ?? ''}
                               onChange={(e) => setEd(c.name, { to: e.target.value })} />
                      </>
                    ) : (
                      <input type="number" placeholder="değer" value={ed.value ?? ''}
                             onChange={(e) => setEd(c.name, { value: e.target.value })} />
                    )}
                  </div>
                </div>
              )}

              {c._t === 'str' && (
                <div className="hz-fp-ed">
                  {c.get_distinct ? (
                    !dist ? (
                      <button type="button" className="hz-fp-btn"
                              onClick={() => loadDistinct(c.name)}>Değerleri getir</button>
                    ) : dist.loading ? (
                      <span className="hz-fp-hint">Yükleniyor…</span>
                    ) : dist.error ? (
                      <span className="hz-fp-err">{dist.error}</span>
                    ) : (
                      <div className="hz-fp-checks">
                        {(dist.values || []).map((v, i) => (
                          <label key={i} className="hz-fp-check">
                            <input type="checkbox"
                                   checked={(ed.checked || []).includes(v)}
                                   onChange={() => toggleChecked(c.name, v)} />
                            {String(v)}
                          </label>
                        ))}
                        {!dist.values.length && <span className="hz-fp-hint">distinct değer yok</span>}
                      </div>
                    )
                  ) : (
                    <div className="hz-fp-inrow">
                      <span className="hz-fp-op">IN</span>
                      <textarea rows={1} placeholder="RETAIL, SME, CORP"
                                value={ed.text || ''}
                                onChange={(e) => setEd(c.name, { text: e.target.value })} />
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
