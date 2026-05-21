/**
 * FilterBar — Phase 6.5.c dashboard filter widget row.
 *
 * Rendered above the canvas (sticky). Reads ``manifest.filters[]`` and the
 * Zustand ``filterState`` map; calls ``applyFilters()`` on the Güncelle button.
 *
 * Per-type widget choices (spec §5.2):
 *   date_range   → from + to date inputs with relative-date presets
 *   enum_multi   → toggle chips (click to add/remove from selection)
 *   enum_single  → dropdown
 *   number_range → min / max numeric inputs
 *
 * Layout edit mode also surfaces:
 *   + Filtre ekle button → opens AddFilterModal
 *   trash on each chip   → removeDashboardFilter
 */
import { useEffect, useMemo, useState } from 'react';
import { Plus, RefreshCw, Trash2, X } from 'lucide-react';
import useStore from '../lib/store.js';


const STATUS_LABELS = {
  cache_hit:   { text: 'önbellek', color: '#16a34a' },
  subset:      { text: 'subset',  color: '#2563eb' },
  refetched:   { text: 'çekildi', color: '#7c3aed' },
  refetching:  { text: 'çekiliyor', color: '#d97706' },
  error:       { text: 'hata',    color: '#dc2626' },
};


export default function FilterBar() {
  const manifest        = useStore((s) => s.manifest);
  const layoutEditMode  = useStore((s) => s.layoutEditMode);
  const viewMode        = useStore((s) => s.viewMode);
  const filterState     = useStore((s) => s.filterState);
  const filterBusy      = useStore((s) => s.filterBusy);
  const filterStatus    = useStore((s) => s.filterStatus);
  const setFilterValue  = useStore((s) => s.setFilterValue);
  const applyFilters    = useStore((s) => s.applyFilters);
  const removeFilter    = useStore((s) => s.removeDashboardFilter);

  const [addOpen, setAddOpen] = useState(false);
  const [err, setErr] = useState(null);

  const filters = manifest?.filters || [];
  // If neither filters declared nor in layout-edit mode, render nothing —
  // keeps Phase 1-6 presentations visually unchanged.
  if (filters.length === 0 && !(layoutEditMode && viewMode === 'edit')) {
    return null;
  }

  async function handleApply() {
    setErr(null);
    try { await applyFilters(); }
    catch (e) { setErr(e.message || String(e)); }
  }

  const successCount = Object.values(filterStatus).filter(
    (s) => s && s !== 'error' && s !== 'refetching'
  ).length;
  const errorCount = Object.values(filterStatus).filter((s) => s === 'error').length;

  return (
    <>
      <div className="filter-bar" role="region" aria-label="Dashboard filtreleri">
        <div className="filter-bar__widgets">
          {filters.map((f) => (
            <FilterWidget
              key={f.id}
              filter={f}
              value={filterState[f.id]}
              onChange={(v) => setFilterValue(f.id, v)}
              editable={layoutEditMode && viewMode === 'edit'}
              onRemove={() => removeFilter(f.id)}
            />
          ))}
          {layoutEditMode && viewMode === 'edit' && (
            <button
              type="button"
              className="filter-bar__add"
              onClick={() => setAddOpen(true)}
              title="Yeni filtre ekle"
            >
              <Plus size={13} strokeWidth={2.5} />
              <span>Filtre ekle</span>
            </button>
          )}
        </div>
        <div className="filter-bar__actions">
          {Object.keys(filterStatus).length > 0 && !filterBusy && (
            <span className="filter-bar__status">
              {successCount} güncellendi
              {errorCount > 0 && <span className="filter-bar__err"> · {errorCount} hata</span>}
            </span>
          )}
          {err && <span className="filter-bar__err">{err}</span>}
          {filters.length > 0 && (
            <button
              type="button"
              className="filter-bar__apply"
              onClick={handleApply}
              disabled={filterBusy}
            >
              <RefreshCw size={13} strokeWidth={2} className={filterBusy ? 'spin' : ''} />
              <span>{filterBusy ? 'Uygulanıyor…' : 'Güncelle'}</span>
            </button>
          )}
        </div>
      </div>

      {addOpen && <AddFilterModal onClose={() => setAddOpen(false)} />}
    </>
  );
}


// ── Per-type widgets ──────────────────────────────────────────────────────

function FilterWidget({ filter, value, onChange, editable, onRemove }) {
  return (
    <div className="filter-widget">
      <div className="filter-widget__head">
        <span className="filter-widget__label" title={filter.semantic_tag}>{filter.label}</span>
        {editable && (
          <button
            type="button"
            className="filter-widget__remove"
            onClick={onRemove}
            title="Bu filtreyi sil"
          >
            <Trash2 size={11} strokeWidth={2} />
          </button>
        )}
      </div>
      <div className="filter-widget__body">
        {filter.type === 'date_range' && <DateRangeWidget value={value} onChange={onChange} />}
        {filter.type === 'enum_multi'  && <EnumMultiWidget filter={filter} value={value} onChange={onChange} />}
        {filter.type === 'enum_single' && <EnumSingleWidget filter={filter} value={value} onChange={onChange} />}
        {filter.type === 'number_range' && <NumberRangeWidget value={value} onChange={onChange} />}
      </div>
    </div>
  );
}


function _asIsoDate(v) {
  if (!v) return '';
  if (typeof v === 'string') return v.length >= 10 ? v.slice(0, 10) : v;
  // Hopefully date-shaped already.
  return String(v);
}


function DateRangeWidget({ value, onChange }) {
  const v = value || { from: '', to: '' };
  return (
    <div className="filter-widget__date-range">
      <input
        type="date"
        value={_asIsoDate(v.from)}
        onChange={(e) => onChange({ ...v, from: e.target.value })}
      />
      <span className="filter-widget__date-sep">→</span>
      <input
        type="date"
        value={_asIsoDate(v.to)}
        onChange={(e) => onChange({ ...v, to: e.target.value })}
      />
    </div>
  );
}


function EnumMultiWidget({ filter, value, onChange }) {
  const selected = useMemo(() => new Set(Array.isArray(value) ? value : []), [value]);
  function toggle(v) {
    const next = new Set(selected);
    if (next.has(v)) next.delete(v); else next.add(v);
    // Preserve original order from allowed_values to keep cache key stable.
    const result = (filter.allowed_values || []).filter((x) => next.has(x));
    onChange(result);
  }
  return (
    <div className="filter-widget__chips">
      {(filter.allowed_values || []).map((v) => (
        <button
          key={String(v)}
          type="button"
          className={`filter-chip${selected.has(v) ? ' is-active' : ''}`}
          onClick={() => toggle(v)}
        >
          {String(v)}
        </button>
      ))}
    </div>
  );
}


function EnumSingleWidget({ filter, value, onChange }) {
  return (
    <select
      className="filter-widget__select"
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">—</option>
      {(filter.allowed_values || []).map((v) => (
        <option key={String(v)} value={v}>{String(v)}</option>
      ))}
    </select>
  );
}


function NumberRangeWidget({ value, onChange }) {
  const v = value || { min: 0, max: 0 };
  return (
    <div className="filter-widget__num-range">
      <input
        type="number"
        value={v.min ?? ''}
        onChange={(e) => onChange({ ...v, min: e.target.value === '' ? null : Number(e.target.value) })}
      />
      <span className="filter-widget__date-sep">↔</span>
      <input
        type="number"
        value={v.max ?? ''}
        onChange={(e) => onChange({ ...v, max: e.target.value === '' ? null : Number(e.target.value) })}
      />
    </div>
  );
}


// ── Add filter modal ──────────────────────────────────────────────────────

const SEMANTIC_TAGS = [
  'as_of_time', 'trade_time', 'value_time', 'settle_time',
  'currency', 'maturity', 'tenor_bucket', 'counterparty',
  'branch', 'region', 'product_group', 'segment',
  'rating_bucket', 'user_id', 'deal_id', 'instrument_type',
  'other',
];


function AddFilterModal({ onClose }) {
  const addFilter = useStore((s) => s.addDashboardFilter);
  const [id, setId]                       = useState('f_');
  const [semanticTag, setSemanticTag]     = useState('as_of_time');
  const [type, setType]                   = useState('date_range');
  const [label, setLabel]                 = useState('');
  const [allowedValues, setAllowedValues] = useState('');
  const [defaultFromExpr, setDefaultFrom] = useState('today - 30d');
  const [defaultToExpr, setDefaultTo]     = useState('today');
  const [defaultSingleVal, setDefaultSingleVal] = useState('');
  const [err, setErr]                     = useState(null);

  function handleSave() {
    setErr(null);
    if (!id.trim() || !label.trim()) {
      setErr('id ve etiket zorunlu');
      return;
    }
    const cleanId = id.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');
    const def = {
      id: cleanId,
      semantic_tag: semanticTag,
      type,
      label: label.trim(),
    };
    if (type === 'date_range') {
      def.default = { from: defaultFromExpr.trim(), to: defaultToExpr.trim() };
    } else if (type === 'enum_multi' || type === 'enum_single') {
      const parsed = allowedValues.split(',').map((s) => s.trim()).filter(Boolean);
      if (!parsed.length) { setErr('allowed_values gerekli'); return; }
      def.allowed_values = parsed;
      if (type === 'enum_multi') {
        def.default = parsed;
      } else if (defaultSingleVal.trim()) {
        def.default = defaultSingleVal.trim();
      }
    } else if (type === 'number_range') {
      def.default = { min: 0, max: 100 };
    }

    try {
      addFilter(def);
      onClose();
    } catch (e) {
      setErr(e.message || String(e));
    }
  }

  return (
    <div className="filter-modal-backdrop" onClick={onClose}>
      <div className="filter-modal" onClick={(e) => e.stopPropagation()}>
        <header className="filter-modal__head">
          <h3>Yeni Filtre</h3>
          <button type="button" onClick={onClose}><X size={16} /></button>
        </header>
        <div className="filter-modal__body">
          <label>
            <span>ID</span>
            <input type="text" value={id} onChange={(e) => setId(e.target.value)} placeholder="f_period"/>
          </label>
          <label>
            <span>Etiket</span>
            <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Tarih Aralığı"/>
          </label>
          <label>
            <span>Anlam (semantic_tag)</span>
            <select value={semanticTag} onChange={(e) => setSemanticTag(e.target.value)}>
              {SEMANTIC_TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label>
            <span>Tip</span>
            <select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="date_range">Tarih aralığı</option>
              <option value="enum_multi">Enum (çoklu)</option>
              <option value="enum_single">Enum (tek)</option>
              <option value="number_range">Sayı aralığı</option>
            </select>
          </label>
          {type === 'date_range' && (
            <>
              <label>
                <span>Varsayılan: from</span>
                <input type="text" value={defaultFromExpr}
                       onChange={(e) => setDefaultFrom(e.target.value)} placeholder="today - 30d"/>
              </label>
              <label>
                <span>Varsayılan: to</span>
                <input type="text" value={defaultToExpr}
                       onChange={(e) => setDefaultTo(e.target.value)} placeholder="today"/>
              </label>
            </>
          )}
          {(type === 'enum_multi' || type === 'enum_single') && (
            <label>
              <span>Olası değerler (virgülle)</span>
              <input type="text" value={allowedValues}
                     onChange={(e) => setAllowedValues(e.target.value)} placeholder="TRY, USD, EUR"/>
            </label>
          )}
          {type === 'enum_single' && (
            <label>
              <span>Varsayılan tek değer</span>
              <input type="text" value={defaultSingleVal}
                     onChange={(e) => setDefaultSingleVal(e.target.value)} placeholder="TRY"/>
            </label>
          )}
          {err && <div className="filter-modal__err">{err}</div>}
        </div>
        <footer className="filter-modal__foot">
          <button type="button" className="filter-modal__cancel" onClick={onClose}>İptal</button>
          <button type="button" className="filter-modal__save" onClick={handleSave}>Kaydet</button>
        </footer>
      </div>
    </div>
  );
}
