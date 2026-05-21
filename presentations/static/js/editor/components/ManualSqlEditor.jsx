/**
 * ManualSqlEditor — Phase 6.5 in-Properties block authoring.
 *
 * Used by PropertiesPanel for blocks where `block.manual_sql === true`.
 * Owns:
 *  - SQL textarea (raw query with `:bind` placeholders)
 *  - "Çalıştır" button → POST /<pid>/block/<bid>/run-manual
 *  - Auto-detect `:param` names on Çalıştır → seed empty rows in the variables
 *    list. Names removed from SQL but still in the list prompt the user
 *    before deletion (no silent data loss).
 *  - Variables form (name / type / semantic_tag / default / allowed_values)
 *  - Inline error display
 *
 * The block.type is OWNED by the type dropdown in the Genel section; this
 * component is type-agnostic — it just runs SQL and writes the result into
 * block.data_source via the run-manual endpoint.
 */
import { useEffect, useMemo, useState } from 'react';
import { Play, Plus, Trash2, AlertTriangle } from 'lucide-react';
import useStore from '../lib/store.js';

// Mirror of presentations.variables.semantic_tags.SEMANTIC_TAGS_V0.
// Ordered with 'other' last (escape hatch).
const SEMANTIC_TAGS = [
  { tag: 'as_of_time',      label: 'Snapshot zamanı (as-of)' },
  { tag: 'trade_time',      label: 'İşlem zamanı' },
  { tag: 'value_time',      label: 'Valör zamanı' },
  { tag: 'settle_time',     label: 'Takas zamanı' },
  { tag: 'currency',        label: 'Para birimi' },
  { tag: 'maturity',        label: 'Vade' },
  { tag: 'tenor_bucket',    label: 'Vade dilimi' },
  { tag: 'counterparty',    label: 'Karşı taraf' },
  { tag: 'branch',          label: 'Şube' },
  { tag: 'region',          label: 'Bölge' },
  { tag: 'product_group',   label: 'Ürün grubu' },
  { tag: 'segment',         label: 'Segment' },
  { tag: 'rating_bucket',   label: 'Rating dilimi' },
  { tag: 'user_id',         label: 'Kullanıcı kimliği' },
  { tag: 'deal_id',         label: 'İşlem kimliği' },
  { tag: 'instrument_type', label: 'Enstrüman tipi' },
  { tag: 'other',           label: 'Diğer (kategorisiz)' },
];

const VAR_TYPES = [
  { value: 'date',         label: 'Tarih' },
  { value: 'date_range',   label: 'Tarih aralığı' },
  { value: 'enum_single',  label: 'Enum (tek)' },
  { value: 'enum_multi',   label: 'Enum (çoklu)' },
  { value: 'number_range', label: 'Sayı aralığı' },
];

const TYPE_DEFAULTS = {
  date:         'today',
  date_range:   '{"from": "today - 30d", "to": "today"}',
  enum_single:  '',
  enum_multi:   '',
  number_range: '{"min": 0, "max": 100}',
};

// Extract `:ident` placeholders, ignoring Postgres ::casts.
const BIND_RE = /(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)\b/g;

export function extractBindNames(sql) {
  if (!sql) return [];
  const seen = new Set();
  const out = [];
  let m;
  BIND_RE.lastIndex = 0;
  while ((m = BIND_RE.exec(sql)) !== null) {
    if (!seen.has(m[1])) {
      seen.add(m[1]);
      out.push(m[1]);
    }
  }
  return out;
}

function inferDefaults(name) {
  // Lightweight heuristics: *_from / *_to → date; pluralised / *_list → enum_multi.
  if (/_(from|since|start)$/i.test(name)) {
    return { type: 'date', semantic_tag: 'as_of_time', default: 'today - 30d' };
  }
  if (/_(to|until|end)$/i.test(name)) {
    return { type: 'date', semantic_tag: 'as_of_time', default: 'today' };
  }
  if (/^(date|as_of|trade_date|value_date)$/i.test(name)) {
    return { type: 'date', semantic_tag: 'as_of_time', default: 'today' };
  }
  if (/_(list|s|codes|ids)$/i.test(name) || /currenc/i.test(name)) {
    return { type: 'enum_multi', semantic_tag: 'other', default: '' };
  }
  return { type: 'date', semantic_tag: 'other', default: 'today' };
}

function defaultsToString(def, type) {
  if (def == null) return '';
  if (Array.isArray(def)) return def.join(', ');
  if (typeof def === 'object') return JSON.stringify(def);
  return String(def);
}

function parseDefault(raw, type) {
  if (raw === '' || raw == null) return null;
  const s = String(raw).trim();
  if (s === '') return null;
  if (type === 'enum_multi') {
    return s.split(',').map((x) => x.trim()).filter(Boolean);
  }
  if (type === 'date_range' || type === 'number_range') {
    try { return JSON.parse(s); } catch (_err) { return s; }
  }
  return s;
}

function parseAllowedValues(raw) {
  if (!raw) return null;
  const parts = String(raw).split(',').map((x) => x.trim()).filter(Boolean);
  return parts.length ? parts : null;
}


/**
 * Props:
 *   block        — the block dict from the manifest.
 *   previewMode  — when true, "Çalıştır" calls /blocks/api/preview (stateless)
 *                  and emits the result via onPreviewResult instead of writing
 *                  to the presentation session. Used by the /blocks/edit/...
 *                  template-edit mini-canvas.
 *   onPreviewResult({query, variables, config, data_source}) — see above.
 */
export default function ManualSqlEditor({ block, previewMode = false, onPreviewResult }) {
  const runBlockManualSql = useStore((s) => s.runBlockManualSql);
  const setBlockField     = useStore((s) => s.setBlockField);

  const initialQuery = block.query || '';
  const initialVars = (block.variables || []).map((v) => ({
    name: v.name || '',
    type: v.type || 'date',
    semantic_tag: v.semantic_tag || 'other',
    required: v.required !== false,
    default_str: defaultsToString(v.default, v.type),
    allowed_values_str: Array.isArray(v.allowed_values) ? v.allowed_values.join(', ') : '',
  }));

  const [sql, setSql] = useState(initialQuery);
  const [vars, setVars] = useState(initialVars);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [warnings, setWarnings] = useState([]);
  const [staleHint, setStaleHint] = useState(false);

  // Reset local state when switching between blocks.
  useEffect(() => {
    setSql(block.query || '');
    setVars((block.variables || []).map((v) => ({
      name: v.name || '',
      type: v.type || 'date',
      semantic_tag: v.semantic_tag || 'other',
      required: v.required !== false,
      default_str: defaultsToString(v.default, v.type),
      allowed_values_str: Array.isArray(v.allowed_values) ? v.allowed_values.join(', ') : '',
    })));
    setErr(null);
    setWarnings([]);
    setStaleHint(false);
  }, [block.id]);

  const bindNames = useMemo(() => extractBindNames(sql), [sql]);
  const declaredNames = useMemo(() => new Set(vars.map((v) => v.name).filter(Boolean)), [vars]);

  // Track which declared vars are no longer referenced — surfaced as a warning,
  // not auto-deleted (user might rename in SQL and want to keep the row).
  const orphaned = useMemo(
    () => vars.filter((v) => v.name && !bindNames.includes(v.name)),
    [vars, bindNames],
  );

  function syncVarsFromSql() {
    // Add empty rows for new bind names; preserve existing rows untouched.
    setVars((prev) => {
      const byName = new Map(prev.map((v) => [v.name, v]));
      const next = [];
      // First: keep existing in their bind order, then any orphans at the end.
      for (const name of bindNames) {
        if (byName.has(name)) {
          next.push(byName.get(name));
          byName.delete(name);
        } else {
          const inf = inferDefaults(name);
          next.push({
            name,
            type: inf.type,
            semantic_tag: inf.semantic_tag,
            required: true,
            default_str: inf.default,
            allowed_values_str: '',
          });
        }
      }
      for (const v of byName.values()) next.push(v);  // orphans at the end
      return next;
    });
  }

  function updateVar(idx, patch) {
    setVars((prev) => prev.map((v, i) => (i === idx ? { ...v, ...patch } : v)));
  }

  function removeVar(idx) {
    setVars((prev) => prev.filter((_, i) => i !== idx));
  }

  function variablesForSubmit() {
    return vars
      .filter((v) => v.name.trim())
      .map((v) => {
        const out = {
          name: v.name.trim(),
          semantic_tag: v.semantic_tag,
          type: v.type,
          required: !!v.required,
        };
        const def = parseDefault(v.default_str, v.type);
        if (def !== null) out.default = def;
        if (v.type === 'enum_single' || v.type === 'enum_multi') {
          const allowed = parseAllowedValues(v.allowed_values_str);
          if (allowed) out.allowed_values = allowed;
        }
        return out;
      });
  }

  async function handleRun() {
    setBusy(true);
    setErr(null);
    setWarnings([]);
    try {
      // Sync new binds into the variables list before the request so the
      // server sees them. Existing entries keep their user-set values.
      syncVarsFromSql();
      // syncVarsFromSql is async vs state — read the latest vars by recomputing.
      // For correctness inside the same click, we recompute the submit list
      // from the current SQL + existing vars map.
      const byName = new Map(vars.map((v) => [v.name, v]));
      const submitVars = bindNames.map((name) => {
        const existing = byName.get(name);
        if (existing) {
          const out = {
            name,
            semantic_tag: existing.semantic_tag,
            type: existing.type,
            required: !!existing.required,
          };
          const def = parseDefault(existing.default_str, existing.type);
          if (def !== null) out.default = def;
          if (existing.type === 'enum_single' || existing.type === 'enum_multi') {
            const allowed = parseAllowedValues(existing.allowed_values_str);
            if (allowed) out.allowed_values = allowed;
          }
          return out;
        }
        const inf = inferDefaults(name);
        return {
          name,
          semantic_tag: inf.semantic_tag,
          type: inf.type,
          required: true,
          default: parseDefault(inf.default, inf.type),
        };
      });

      let result;
      if (previewMode) {
        // Template-edit path — stateless /blocks/api/preview.
        // Pulls the render type from the in-canvas block so the response
        // already has config[categories/series/etc] populated.
        const baseUrl = window.location.pathname.replace(/\/blocks\/edit\/.*/, '/blocks/api');
        const resp = await fetch(`${baseUrl}/preview`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            block: {
              id: block.id.replace(/^preview_/, '') || 'preview_block',
              version: 1,
              title: block.title || 'preview',
              team: 'preview',
              owner: 'preview',
              created_at: new Date().toISOString(),
              query: sql,
              variables: submitVars,
              visualization: { type: block.type, config: {} },
            },
            render_type: block.type,
          }),
        });
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok || !body.ok) {
          const err = new Error((body.errors || [body.error]).filter(Boolean).join('; ') || 'Çalıştırma hatası');
          err.kind = body.phase || body.kind;
          err.warnings = body.meta?.warnings || body.warnings;
          throw err;
        }
        if (onPreviewResult && body.block) {
          onPreviewResult({
            query: sql,
            variables: submitVars,
            config: body.block.config,
            data_source: body.block.data_source,
          });
        }
        result = { warnings: body.meta?.warnings || [] };
      } else {
        result = await runBlockManualSql(block.id, {
          query: sql,
          variables: submitVars,
        });
      }
      setWarnings(result.warnings || []);
      setStaleHint(false);
    } catch (e) {
      setErr({
        message: e.message || String(e),
        kind: e.kind,
        warnings: e.warnings || [],
      });
    } finally {
      setBusy(false);
    }
  }

  // Stale hint: when SQL changed but not yet run.
  useEffect(() => {
    if (sql.trim() !== (block.query || '').trim()) setStaleHint(true);
  }, [sql, block.query]);

  return (
    <>
      <Section title="SQL Sorgusu (manuel)">
        <div className="props-sql-wrap">
          <textarea
            className="props-textarea props-textarea--sql"
            rows={10}
            spellCheck={false}
            placeholder="SELECT … FROM … WHERE x = :param"
            value={sql}
            onChange={(e) => setSql(e.target.value)}
          />
          <div className="props-sql-footer">
            <button
              type="button"
              className="props-btn props-btn--primary"
              onClick={handleRun}
              disabled={busy || !sql.trim()}
              title="SQL'i çalıştır, :param'ları otomatik tanı"
            >
              <Play size={13} strokeWidth={2} className={busy ? 'spin' : ''} />
              <span>{busy ? 'Çalışıyor…' : 'Çalıştır'}</span>
            </button>
            <div className="props-sql-bindcount">
              {bindNames.length === 0
                ? 'bind yok'
                : `${bindNames.length} bind: ${bindNames.join(', ')}`}
            </div>
          </div>
          {staleHint && !busy && (
            <div className="props-stale-hint">
              <AlertTriangle size={12} strokeWidth={2} />
              <span>SQL değişti — yeni veri için Çalıştır.</span>
            </div>
          )}
          {err && (
            <div className="props-sql-error">
              <strong>{err.kind ? `[${err.kind}] ` : ''}Hata:</strong> {err.message}
            </div>
          )}
          {warnings.length > 0 && (
            <ul className="props-sql-warnings">
              {warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          )}
        </div>
      </Section>

      <Section title={`Değişkenler ${bindNames.length ? `(${bindNames.length})` : ''}`}>
        {bindNames.length === 0 && vars.length === 0 && (
          <div className="props-form-hint">
            SQL'inize <code>:isim</code> ekleyin; Çalıştır'da otomatik tanınır.
          </div>
        )}
        {orphaned.length > 0 && (
          <div className="props-orphan-warning">
            <AlertTriangle size={12} strokeWidth={2} />
            <span>
              SQL'de geçmeyen değişken{orphaned.length > 1 ? 'ler' : ''}:{' '}
              <strong>{orphaned.map((v) => v.name).join(', ')}</strong>.
              İstersen aşağıdan sil.
            </span>
          </div>
        )}
        <div className="props-var-list">
          {vars.map((v, i) => (
            <VariableRow
              key={`${v.name}-${i}`}
              v={v}
              onChange={(p) => updateVar(i, p)}
              onRemove={() => removeVar(i)}
              orphaned={!!v.name && !bindNames.includes(v.name)}
            />
          ))}
        </div>
        {bindNames.length > 0 && (
          <button
            type="button"
            className="props-btn props-btn--ghost"
            onClick={syncVarsFromSql}
            title="SQL'deki :isimleri buraya çek"
          >
            <Plus size={12} strokeWidth={2} />
            <span>SQL'den yeniden tara</span>
          </button>
        )}
      </Section>
    </>
  );
}


function VariableRow({ v, onChange, onRemove, orphaned }) {
  const isOther = v.semantic_tag === 'other';
  const isEnum = v.type === 'enum_single' || v.type === 'enum_multi';

  return (
    <div className={`props-var-card${isOther ? ' is-other-tag' : ''}${orphaned ? ' is-orphan' : ''}`}>
      <div className="props-var-head">
        <span className="props-var-name" title={v.name || '(isimsiz)'}>{v.name || '(isimsiz)'}</span>
        <button
          type="button"
          className="props-btn props-btn--icon props-btn--icon-danger"
          onClick={onRemove}
          title="Bu değişkeni listeden sil"
        >
          <Trash2 size={11} strokeWidth={2} />
        </button>
      </div>
      <div className="props-var-row">
        <label className="props-var-label">Tip</label>
        <select
          className="props-select"
          value={v.type}
          onChange={(e) => onChange({
            type: e.target.value,
            default_str: v.default_str || TYPE_DEFAULTS[e.target.value] || '',
          })}
        >
          {VAR_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </div>
      <div className="props-var-row">
        <label className="props-var-label">Anlam (semantic_tag)</label>
        <select
          className="props-select"
          value={v.semantic_tag}
          onChange={(e) => onChange({ semantic_tag: e.target.value })}
        >
          {SEMANTIC_TAGS.map((t) => (
            <option key={t.tag} value={t.tag}>{t.tag} — {t.label}</option>
          ))}
        </select>
        {isOther && (
          <div className="props-var-warning-text">
            "other" kaçış kapısı — ilerde elle gözden geçirilecek.
          </div>
        )}
      </div>
      <div className="props-var-row">
        <label className="props-var-label">Varsayılan</label>
        <input
          type="text"
          className="props-input"
          value={v.default_str}
          onChange={(e) => onChange({ default_str: e.target.value })}
          placeholder={TYPE_DEFAULTS[v.type] || ''}
        />
      </div>
      {isEnum && (
        <div className="props-var-row">
          <label className="props-var-label">Olası değerler (virgülle)</label>
          <input
            type="text"
            className="props-input"
            value={v.allowed_values_str}
            onChange={(e) => onChange({ allowed_values_str: e.target.value })}
            placeholder="TRY, USD, EUR"
          />
        </div>
      )}
      <div className="props-var-row">
        <label className="props-var-label">Zorunlu mu?</label>
        <select
          className="props-select"
          value={v.required ? 'true' : 'false'}
          onChange={(e) => onChange({ required: e.target.value === 'true' })}
        >
          <option value="true">Evet</option>
          <option value="false">Hayır</option>
        </select>
      </div>
    </div>
  );
}


// Mini Section wrapper matching the rest of PropertiesPanel's visual language.
function Section({ title, children }) {
  return (
    <section className="props-section">
      <h4 className="props-section__title">{title}</h4>
      <div className="props-section__body">{children}</div>
    </section>
  );
}
