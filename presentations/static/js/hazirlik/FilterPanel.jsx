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
import { useMemo, useState } from 'react';
import { _resolveDateExpr } from '../shared/dateExpr.js';

const NUM_OPS = [
  { v: 'gt', label: '>' }, { v: 'gte', label: '≥' },
  { v: 'lt', label: '<' }, { v: 'lte', label: '≤' },
  { v: 'eq', label: '=' }, { v: 'in', label: 'IN' },
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

export default function FilterPanel({ alias, columns = [], onSave, onFetchDistinct }) {
  const [edits, setEdits] = useState({});       // { col: editorState }
  const [distinct, setDistinct] = useState({}); // { col: {loading, values, error} }
  const [savedMsg, setSavedMsg] = useState('');

  const cols = useMemo(
    () => (columns || []).filter((c) => c && c.name).map((c) => ({ ...c, _t: classify(c.type) })),
    [columns]
  );

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
    if (!specs.length) { setSavedMsg('Aktif filtre yok.'); return; }
    await onSave(specs);
    setSavedMsg(`${specs.length} filtre kaydedildi.`);
  }

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
                <div className="hz-fp-ed hz-fp-numrow">
                  <select value={ed.op || 'gt'} onChange={(e) => setEd(c.name, { op: e.target.value })}>
                    {NUM_OPS.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
                  </select>
                  {(ed.op || 'gt') === 'in'
                    ? <input type="text" placeholder="100, 200, 300" value={ed.values || ''}
                             onChange={(e) => setEd(c.name, { values: e.target.value })} />
                    : <input type="number" placeholder="değer" value={ed.value ?? ''}
                             onChange={(e) => setEd(c.name, { value: e.target.value })} />}
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
      <div className="hz-fp-foot">
        <button type="button" className="hz-fp-save" onClick={save}>Filtreyi kaydet</button>
        {savedMsg && <span className="hz-fp-saved">{savedMsg}</span>}
      </div>
    </div>
  );
}
