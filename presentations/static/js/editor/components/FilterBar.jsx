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
import { fetchConceptFilterSuggestions } from '../lib/api.js';


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
  const [open, setOpen] = useState(false);
  const wrapRef = useState(null);
  const selected = useMemo(() => new Set(Array.isArray(value) ? value : []), [value]);
  const allowed = filter.allowed_values || [];

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      const root = document.getElementById(`fmw-${filter.id}`);
      if (root && !root.contains(e.target)) setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open, filter.id]);

  function toggle(v) {
    const next = new Set(selected);
    if (next.has(v)) next.delete(v); else next.add(v);
    const result = allowed.filter((x) => next.has(x));
    onChange(result);
  }
  function selectAll() { onChange(allowed.slice()); }
  function clearAll()  { onChange([]); }

  const summary = (
    selected.size === 0 ? 'hiçbiri'
    : selected.size === allowed.length ? `Tümü (${allowed.length})`
    : selected.size <= 2 ? [...selected].join(', ')
    : `${selected.size} seçili`
  );

  return (
    <div id={`fmw-${filter.id}`} className="filter-enum-multi">
      <button
        type="button"
        className={`filter-enum-multi__trigger${open ? ' is-open' : ''}`}
        onClick={() => setOpen((v) => !v)}
        title={[...selected].join(', ')}
      >
        <span className="filter-enum-multi__summary">{summary}</span>
        <span className="filter-enum-multi__caret">▾</span>
      </button>
      {open && (
        <div className="filter-enum-multi__panel">
          <div className="filter-enum-multi__actions">
            <button type="button" onClick={selectAll}>Tümünü seç</button>
            <button type="button" onClick={clearAll}>Temizle</button>
          </div>
          <div className="filter-enum-multi__list">
            {allowed.map((v) => (
              <label key={String(v)} className="filter-enum-multi__row">
                <input
                  type="checkbox"
                  checked={selected.has(v)}
                  onChange={() => toggle(v)}
                />
                <span>{String(v)}</span>
              </label>
            ))}
          </div>
        </div>
      )}
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

const SEMANTIC_TAG_LABELS = {
  as_of_time:     'Snapshot zamanı',
  trade_time:     'İşlem zamanı',
  value_time:     'Valör zamanı',
  settle_time:    'Takas zamanı',
  currency:       'Para birimi',
  maturity:       'Vade',
  tenor_bucket:   'Vade dilimi',
  counterparty:   'Karşı taraf',
  branch:         'Şube',
  region:         'Bölge',
  product_group:  'Ürün grubu',
  segment:        'Segment',
  rating_bucket:  'Rating dilimi',
  user_id:        'Kullanıcı kimliği',
  deal_id:        'İşlem kimliği',
  instrument_type:'Enstrüman tipi',
  other:          'Diğer',
};


/**
 * Walk every leaf block in the manifest, collect variables that don't yet
 * have a matching dashboard filter (by semantic_tag), and propose them as
 * one-click filter suggestions.
 *
 * Returns an array of candidates, each shape:
 *   {
 *     semantic_tag,
 *     suggested_id,             // e.g. "f_segment"
 *     label,
 *     type,                     // "date_range" | "enum_multi" | "enum_single" | "number_range"
 *     allowed_values,           // for enum types
 *     default,                  // sensible default (mirror block defaults / allowed_values)
 *     variable_names,           // string[] — the :params this filter will drive
 *     block_count,              // how many blocks use this tag
 *   }
 */
function proposeFiltersFromManifest(manifest) {
  if (!manifest) return [];
  const existingTags = new Set((manifest.filters || []).map((f) => f.semantic_tag));

  // Walk all leaf blocks (inside sections, inside carousels too).
  const variables = [];
  for (const section of manifest.blocks || []) {
    for (const child of section.children || []) {
      if (Array.isArray(child.variables)) {
        for (const v of child.variables) {
          variables.push({ ...v, _block_id: child.id });
        }
      }
      if (child.type === 'carousel' && Array.isArray(child.children)) {
        for (const slide of child.children) {
          if (Array.isArray(slide.variables)) {
            for (const v of slide.variables) {
              variables.push({ ...v, _block_id: slide.id });
            }
          }
        }
      }
    }
  }

  // Group by semantic_tag.
  const byTag = new Map();
  for (const v of variables) {
    if (!v.semantic_tag || existingTags.has(v.semantic_tag)) continue;
    if (!byTag.has(v.semantic_tag)) byTag.set(v.semantic_tag, []);
    byTag.get(v.semantic_tag).push(v);
  }

  const proposals = [];
  for (const [tag, vars] of byTag) {
    // ── Date pairing: as_of_from + as_of_to → date_range ────────────────
    const dateVars = vars.filter((v) => v.type === 'date');
    const fromVar = dateVars.find((v) => /_(from|since|start)$/i.test(v.name));
    const toVar   = dateVars.find((v) => /_(to|until|end)$/i.test(v.name));
    if (fromVar && toVar) {
      proposals.push({
        semantic_tag: tag,
        suggested_id: 'f_' + tag,
        label: SEMANTIC_TAG_LABELS[tag] || tag,
        type: 'date_range',
        default: {
          from: typeof fromVar.default === 'string' ? fromVar.default : 'today - 30d',
          to:   typeof toVar.default === 'string'   ? toVar.default   : 'today',
        },
        variable_names: [fromVar.name, toVar.name],
        block_count: new Set(vars.map((v) => v._block_id)).size,
      });
      // Continue — there might also be a non-paired enum variable for the
      // same tag (rare; fall through to other groupings).
      continue;
    }

    // ── Single date → date_range with same value both ends ───────────────
    if (dateVars.length === 1) {
      const v = dateVars[0];
      proposals.push({
        semantic_tag: tag,
        suggested_id: 'f_' + tag,
        label: SEMANTIC_TAG_LABELS[tag] || tag,
        type: 'date_range',
        default: {
          from: typeof v.default === 'string' ? v.default : 'today - 30d',
          to:   'today',
        },
        variable_names: [v.name],
        block_count: 1,
      });
      continue;
    }

    // ── enum_multi / enum_single ─────────────────────────────────────────
    const enumVars = vars.filter((v) => v.type === 'enum_multi' || v.type === 'enum_single');
    if (enumVars.length) {
      // Union of allowed_values across variables sharing the tag.
      const allowedUnion = new Set();
      for (const v of enumVars) {
        for (const av of (v.allowed_values || [])) allowedUnion.add(av);
      }
      const allowed = [...allowedUnion];
      // Pick type by majority — if any var is enum_multi, propose multi.
      const isMulti = enumVars.some((v) => v.type === 'enum_multi');
      proposals.push({
        semantic_tag: tag,
        suggested_id: 'f_' + tag,
        label: SEMANTIC_TAG_LABELS[tag] || tag,
        type: isMulti ? 'enum_multi' : 'enum_single',
        allowed_values: allowed,
        default: isMulti ? allowed : (enumVars[0].default || allowed[0]),
        variable_names: enumVars.map((v) => v.name),
        block_count: new Set(enumVars.map((v) => v._block_id)).size,
      });
      continue;
    }

    // ── number_range ─────────────────────────────────────────────────────
    const numVars = vars.filter((v) => v.type === 'number_range');
    if (numVars.length) {
      const v = numVars[0];
      proposals.push({
        semantic_tag: tag,
        suggested_id: 'f_' + tag,
        label: SEMANTIC_TAG_LABELS[tag] || tag,
        type: 'number_range',
        default: v.default || { min: 0, max: 100 },
        variable_names: numVars.map((x) => x.name),
        block_count: numVars.length,
      });
    }
  }

  return proposals;
}


function AddFilterModal({ onClose }) {
  const manifest  = useStore((s) => s.manifest);
  const addFilter = useStore((s) => s.addDashboardFilter);

  const [view, setView] = useState('suggest');   // 'suggest' | 'manual'
  const [err, setErr]   = useState(null);
  const [conceptProps, setConceptProps] = useState([]);

  // Phase 7: concepts the blocks' source_tables bind to (server-computed).
  // Concept-native blocks have no `variables`, so the legacy walk finds
  // nothing — these come from the binding catalog instead.
  useEffect(() => {
    let alive = true;
    fetchConceptFilterSuggestions().then((s) => { if (alive) setConceptProps(s); });
    return () => { alive = false; };
  }, []);

  const proposals = useMemo(() => {
    const variableProps = proposeFiltersFromManifest(manifest);
    // Merge: concept-based first; variable-based for any tag not covered.
    const byTag = new Map();
    for (const p of conceptProps) byTag.set(p.semantic_tag, p);
    for (const p of variableProps) if (!byTag.has(p.semantic_tag)) byTag.set(p.semantic_tag, p);
    return [...byTag.values()];
  }, [manifest, conceptProps]);

  function addProposal(prop) {
    setErr(null);
    // Concept proposals (server) carry `id`; variable proposals carry
    // `suggested_id`. Without this fallback the filter id is undefined and
    // filter_state ends up keyed by `undefined`.
    const def = {
      id: prop.id || prop.suggested_id,
      semantic_tag: prop.semantic_tag,
      type: prop.type,
      label: prop.label,
    };
    if (prop.allowed_values) def.allowed_values = prop.allowed_values;
    if (prop.default !== undefined) def.default = prop.default;
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
          <h3>{view === 'suggest' ? 'Filtre Ekle' : 'Özel Filtre'}</h3>
          <button type="button" onClick={onClose}><X size={16} /></button>
        </header>

        {view === 'suggest' ? (
          <SuggestionList
            proposals={proposals}
            onAdd={addProposal}
            onSwitchManual={() => setView('manual')}
            err={err}
          />
        ) : (
          <ManualFilterForm
            onSave={(def) => { try { addFilter(def); onClose(); } catch (e) { setErr(e.message); }}}
            onBack={() => setView('suggest')}
            err={err}
          />
        )}
      </div>
    </div>
  );
}


function SuggestionList({ proposals, onAdd, onSwitchManual, err }) {
  return (
    <>
      <div className="filter-modal__body">
        {proposals.length === 0 ? (
          <div className="filter-suggest-empty">
            Filtreye bağlanabilecek değişken yok — ya bütün block variable'ları
            zaten filtre olarak ekli, ya da hiç değişkenli block yok.
            <br/><br/>
            Yine de özel filtre eklemek istiyorsan aşağıdan.
          </div>
        ) : (
          <>
            <p className="filter-suggest-intro">
              Block'larındaki <code>:param</code>'lara göre önerilen filtreler.
              Tıkla, otomatik eklensin.
            </p>
            <div className="filter-suggest-list">
              {proposals.map((p) => (
                <SuggestionRow key={p.semantic_tag} prop={p} onAdd={() => onAdd(p)} />
              ))}
            </div>
          </>
        )}
        {err && <div className="filter-modal__err">{err}</div>}
      </div>
      <footer className="filter-modal__foot">
        <button type="button" className="filter-modal__cancel" onClick={onSwitchManual}>
          Özel filtre ekle…
        </button>
      </footer>
    </>
  );
}


function SuggestionRow({ prop, onAdd }) {
  const tagLabel = prop.label || SEMANTIC_TAG_LABELS[prop.semantic_tag] || prop.semantic_tag;
  // Concept-based proposals (Phase 7) drive blocks via the binding catalog,
  // not named :params — show the concept instead of variable names.
  const params = prop.source === 'concept'
    ? `concept: ${prop.semantic_tag}`
    : (prop.variable_names || []).map((n) => `:${n}`).join(', ');
  let detail = '';
  if (prop.type === 'date_range') detail = `Tarih aralığı`;
  else if (prop.type === 'enum_multi')  detail = `${(prop.allowed_values || []).length} değer (çoklu)`;
  else if (prop.type === 'enum_single') detail = `${(prop.allowed_values || []).length} değer (tek)`;
  else if (prop.type === 'number_range') detail = `Sayı aralığı`;

  const blockSummary = prop.block_count === 1
    ? '1 blokta kullanılıyor'
    : `${prop.block_count || 1} blokta kullanılıyor`;

  return (
    <button type="button" className="filter-suggest-row" onClick={onAdd}>
      <div className="filter-suggest-row__main">
        <div className="filter-suggest-row__tag">{tagLabel}</div>
        <div className="filter-suggest-row__params">{params}</div>
        <div className="filter-suggest-row__detail">
          {detail} · {blockSummary}
        </div>
        {prop.allowed_values && prop.allowed_values.length > 0 && (
          <div className="filter-suggest-row__values">
            {prop.allowed_values.slice(0, 8).map((v) => (
              <span key={String(v)} className="filter-suggest-row__chip">{String(v)}</span>
            ))}
            {prop.allowed_values.length > 8 && (
              <span className="filter-suggest-row__chip">+{prop.allowed_values.length - 8}</span>
            )}
          </div>
        )}
      </div>
      <div className="filter-suggest-row__cta">+ Ekle</div>
    </button>
  );
}


function ManualFilterForm({ onSave, onBack, err }) {
  const [id, setId]                       = useState('f_');
  const [semanticTag, setSemanticTag]     = useState('as_of_time');
  const [type, setType]                   = useState('date_range');
  const [label, setLabel]                 = useState('');
  const [allowedValues, setAllowedValues] = useState('');
  const [defaultFromExpr, setDefaultFrom] = useState('today - 30d');
  const [defaultToExpr, setDefaultTo]     = useState('today');
  const [defaultSingleVal, setDefaultSingleVal] = useState('');
  const [localErr, setLocalErr]           = useState(null);

  function handleSave() {
    setLocalErr(null);
    if (!id.trim() || !label.trim()) {
      setLocalErr('id ve etiket zorunlu');
      return;
    }
    const cleanId = id.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');
    const def = {
      id: cleanId, semantic_tag: semanticTag, type, label: label.trim(),
    };
    if (type === 'date_range') {
      def.default = { from: defaultFromExpr.trim(), to: defaultToExpr.trim() };
    } else if (type === 'enum_multi' || type === 'enum_single') {
      const parsed = allowedValues.split(',').map((s) => s.trim()).filter(Boolean);
      if (!parsed.length) { setLocalErr('allowed_values gerekli'); return; }
      def.allowed_values = parsed;
      if (type === 'enum_multi') def.default = parsed;
      else if (defaultSingleVal.trim()) def.default = defaultSingleVal.trim();
    } else if (type === 'number_range') {
      def.default = { min: 0, max: 100 };
    }
    onSave(def);
  }

  const shownErr = localErr || err;

  return (
    <>
      <div className="filter-modal__body">
        <label><span>ID</span>
          <input type="text" value={id} onChange={(e) => setId(e.target.value)} placeholder="f_period"/>
        </label>
        <label><span>Etiket</span>
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Tarih Aralığı"/>
        </label>
        <label><span>Anlam</span>
          <select value={semanticTag} onChange={(e) => setSemanticTag(e.target.value)}>
            {SEMANTIC_TAGS.map((t) => (
              <option key={t} value={t}>{t} — {SEMANTIC_TAG_LABELS[t] || t}</option>
            ))}
          </select>
        </label>
        <label><span>Tip</span>
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="date_range">Tarih aralığı</option>
            <option value="enum_multi">Enum (çoklu)</option>
            <option value="enum_single">Enum (tek)</option>
            <option value="number_range">Sayı aralığı</option>
          </select>
        </label>
        {type === 'date_range' && (
          <>
            <label><span>Varsayılan: from</span>
              <input type="text" value={defaultFromExpr} onChange={(e) => setDefaultFrom(e.target.value)} placeholder="today - 30d"/>
            </label>
            <label><span>Varsayılan: to</span>
              <input type="text" value={defaultToExpr} onChange={(e) => setDefaultTo(e.target.value)} placeholder="today"/>
            </label>
          </>
        )}
        {(type === 'enum_multi' || type === 'enum_single') && (
          <label><span>Olası değerler (virgülle)</span>
            <input type="text" value={allowedValues} onChange={(e) => setAllowedValues(e.target.value)} placeholder="TRY, USD, EUR"/>
          </label>
        )}
        {type === 'enum_single' && (
          <label><span>Varsayılan tek değer</span>
            <input type="text" value={defaultSingleVal} onChange={(e) => setDefaultSingleVal(e.target.value)} placeholder="TRY"/>
          </label>
        )}
        {shownErr && <div className="filter-modal__err">{shownErr}</div>}
      </div>
      <footer className="filter-modal__foot">
        <button type="button" className="filter-modal__cancel" onClick={onBack}>← Önerilere dön</button>
        <button type="button" className="filter-modal__save" onClick={handleSave}>Kaydet</button>
      </footer>
    </>
  );
}
