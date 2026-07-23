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
import { Play, Plus, Trash2, AlertTriangle, Search, Database } from 'lucide-react';
import useStore from '../lib/store.js';

// Mirror of presentations.variables.semantic_tags.SEMANTIC_TAGS_V0.
// Ordered with 'other' last (escape hatch).
const SEMANTIC_TAGS = [
  { tag: 'as_of_time',      label: 'Veri zamanı (as-of)' },
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

/**
 * SQL-context inference: scan the query for how :name is *used*, then pick
 * a type that matches. Falls back to name-based heuristics, then to
 * enum_multi as the safer default (a date bind misused as a list value
 * crashes DuckDB; a list bind misused as a date is at least a clear error).
 */
function inferDefaults(name, sql) {
  const ctx = inferTypeFromContext(name, sql || '');
  if (ctx) return ctx;

  // Name-based fallback heuristics.
  if (/_(from|since|start)$/i.test(name)) {
    return { type: 'date', semantic_tag: 'as_of_time', default: 'today - 30d' };
  }
  if (/_(to|until|end)$/i.test(name)) {
    return { type: 'date', semantic_tag: 'as_of_time', default: 'today' };
  }
  if (/^(date|as_of|trade_date|value_date)$/i.test(name)) {
    return { type: 'date', semantic_tag: 'as_of_time', default: 'today' };
  }
  if (/_(list|codes|ids)$/i.test(name) || /currenc/i.test(name) || /s$/i.test(name)) {
    return { type: 'enum_multi', semantic_tag: 'other', default: '' };
  }
  // Final default — enum_multi, not date. A misclassified enum is recoverable
  // (user just adds allowed_values); a misclassified date silently corrupts
  // the WHERE clause and produces baffling cast errors.
  return { type: 'enum_multi', semantic_tag: 'other', default: '' };
}


function inferTypeFromContext(name, sql) {
  if (!sql) return null;
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  // `IN (:name)` — clear enum_multi signal.
  const inRe = new RegExp(`\\bIN\\s*\\(\\s*:${escapedName}\\s*\\)`, 'i');
  if (inRe.test(sql)) {
    // Heuristic semantic_tag from the *column* on the LHS of IN.
    const colTagRe = new RegExp(`\\b([A-Za-z_][A-Za-z0-9_]*)\\s+IN\\s*\\(\\s*:${escapedName}`, 'i');
    const colMatch = sql.match(colTagRe);
    const colName = colMatch ? colMatch[1].toLowerCase() : '';
    const tag = colName.includes('curr') || colName === 'ccy'        ? 'currency'
              : colName.includes('matur')                             ? 'maturity'
              : colName.includes('branch')                            ? 'branch'
              : colName.includes('product')                           ? 'product_group'
              : colName.includes('segment')                           ? 'segment'
              : colName.includes('region')                            ? 'region'
              : colName.includes('counter')                           ? 'counterparty'
              : 'other';
    return { type: 'enum_multi', semantic_tag: tag, default: '' };
  }

  // `BETWEEN :a AND :b` — both ends are dates.
  const betweenRe = new RegExp(
    `\\b([A-Za-z_][A-Za-z0-9_]*)\\s+BETWEEN\\s+:${escapedName}\\s+AND\\s+:[A-Za-z_][A-Za-z0-9_]*`,
    'i',
  );
  const betweenRe2 = new RegExp(
    `\\b([A-Za-z_][A-Za-z0-9_]*)\\s+BETWEEN\\s+:[A-Za-z_][A-Za-z0-9_]*\\s+AND\\s+:${escapedName}\\b`,
    'i',
  );
  const between1 = sql.match(betweenRe);
  const between2 = sql.match(betweenRe2);
  if (between1 || between2) {
    const col = (between1 || between2)[1].toLowerCase();
    const tag = col.includes('trade')                                          ? 'trade_time'
              : col.includes('settle')                                          ? 'settle_time'
              : col.includes('value_date') || col.includes('valuation')         ? 'value_time'
              : 'as_of_time';
    // Lower bound (from) defaults to 30d ago; upper bound to today.
    const isLowerBound = !!between1;
    return {
      type: 'date',
      semantic_tag: tag,
      default: isLowerBound ? 'today - 30d' : 'today',
    };
  }

  // `>= :name` or `> :name` — date lower bound (heuristic).
  if (new RegExp(`>=?\\s*:${escapedName}\\b`).test(sql)) {
    return { type: 'date', semantic_tag: 'as_of_time', default: 'today - 30d' };
  }
  if (new RegExp(`<=?\\s*:${escapedName}\\b`).test(sql)) {
    return { type: 'date', semantic_tag: 'as_of_time', default: 'today' };
  }

  // `= :name` — likely a single-value scalar. Without table docs we can't
  // tell enum vs free-form scalar; default to enum_single so the user has
  // to fill allowed_values (safer than free-form text in v0).
  if (new RegExp(`=\\s*:${escapedName}\\b`).test(sql)) {
    return { type: 'enum_single', semantic_tag: 'other', default: '' };
  }

  return null;
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
  const addDashboardFilter = useStore((s) => s.addDashboardFilter);
  const setFilterValue    = useStore((s) => s.setFilterValue);

  // Backward-compat: pre-Phase 6.5 blocks store their SQL on
  // data_source.original_sql with no variables. Seed the textarea from there
  // so legacy LLM-generated blocks open with their existing query visible
  // (and the user can incrementally add :binds + variables to migrate).
  const initialQuery = block.query || block.data_source?.original_sql || '';
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
  // Phase 7 auto-conceptualize result (from Şemayı Tara): what got lifted
  // into concept filters + what stayed hardcoded.
  const [conceptMsg, setConceptMsg] = useState(null);

  // Reset local state when switching between blocks. Falls back to
  // data_source.original_sql for legacy LLM-generated blocks (see
  // initialQuery computation above).
  useEffect(() => {
    setSql(block.query || block.data_source?.original_sql || '');
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
          const inf = inferDefaults(name, sql);
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
    setVars((prev) => prev.map((v, i) => {
      if (i !== idx) return v;
      const merged = { ...v, ...patch };
      // UX assist: when the user types allowed_values for an enum_multi /
      // enum_single var and hasn't supplied a default yet, mirror the
      // allowed list into the default (enum_multi → all values; enum_single
      // → first value). They can override; this just prevents the
      // "required, no default" trap which is the most common second-step
      // stumble after auto-detect.
      const allowedChanged = patch.allowed_values_str !== undefined;
      const defaultEmpty = !(v.default_str || '').trim();
      if (allowedChanged && defaultEmpty && (patch.allowed_values_str || '').trim()) {
        if (merged.type === 'enum_multi') {
          merged.default_str = patch.allowed_values_str;
        } else if (merged.type === 'enum_single') {
          const first = String(patch.allowed_values_str).split(',')[0].trim();
          if (first) merged.default_str = first;
        }
      }
      return merged;
    }));
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

  /**
   * Push the current local SQL + variables into the manifest block. This
   * runs on textarea blur, on each Çalıştır click, and on each variable
   * form change — so even if the user never clicks Çalıştır, "Şablon olarak
   * kaydet" finds the current draft on block.query / block.variables.
   *
   * Without this sync the save modal sees stale (empty) fields and Pydantic
   * rejects with "block.query: String should have at least 1 character".
   */
  function pushDraftToManifest() {
    if (previewMode) return;  // template-edit handles its own state plumbing
    if (sql !== (block.query || '')) {
      setBlockField(block.id, 'query', sql);
    }
    // Compare a serialised projection to avoid no-op patches.
    const submit = variablesForSubmit();
    const current = block.variables || [];
    if (JSON.stringify(submit) !== JSON.stringify(current)) {
      setBlockField(block.id, 'variables', submit);
    }
  }

  /**
   * Client-side pre-flight: stop submissions that would inevitably fail
   * server-side schema validation. Cleaner UX than a Pydantic / resolver
   * error string.
   *
   * Catches:
   *   - enum_multi / enum_single with no allowed_values.
   *   - Required var with no default value (resolver fails since there's
   *     no dashboard override mechanism in this v0 path).
   */
  function preflightErrors(submitVars) {
    const out = [];
    for (const v of submitVars) {
      // Phase 6.5.c UX update: enum types no longer require explicit
      // allowed_values OR default — server auto-discovers allowed_values
      // from SELECT DISTINCT, and resolver falls back to allowed_values as
      // default. Only date / range types still need a default.
      if ((v.type === 'enum_multi' || v.type === 'enum_single')) {
        continue;
      }
      if (v.required && (v.default === undefined || v.default === null
                        || (Array.isArray(v.default) && v.default.length === 0))) {
        out.push(
          `"${v.name}": Zorunlu değişken. "Varsayılan" alanını doldurun.`,
        );
      }
    }
    return out;
  }

  async function handleRun(mode = 'execute') {
    // mode = 'scan'     → POST scan_only=true; server discovers :params +
    //                     allowed_values, returns enriched variables, no
    //                     execute. Used by "Şemayı Tara" button.
    // mode = 'execute'  → full resolve+bind+execute; used by "Çalıştır".
    const isScan = mode === 'scan';
    setBusy(true);
    setErr(null);
    setWarnings([]);
    setConceptMsg(null);
    // Persist the user's current draft to the manifest BEFORE the request,
    // so a subsequent "Şablon olarak kaydet" doesn't lose it if Çalıştır fails.
    pushDraftToManifest();
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
        const inf = inferDefaults(name, sql);
        return {
          name,
          semantic_tag: inf.semantic_tag,
          type: inf.type,
          required: true,
          default: parseDefault(inf.default, inf.type),
        };
      });

      // Pre-flight only on execute. Scan mode hasn't asked the user to fill
      // in defaults yet — that's literally the next step in the workflow.
      if (!isScan) {
        const pre = preflightErrors(submitVars);
        if (pre.length > 0) {
          setErr({
            message: pre.join('\n'),
            kind: 'incomplete',
          });
          setBusy(false);
          return;
        }
      }

      let result;
      if (previewMode) {
        // Template-edit path — stateless /blocks/api/preview. Doesn't have a
        // scan_only mode (the template editor doesn't iterate on variables
        // the same way as the in-properties path), so in scan mode we just
        // bail out with a hint. Auto-preview on mount already runs once.
        if (isScan) {
          setErr({
            message: 'Şablon düzenleyicide şema taraması desteklenmiyor — düzenlemek için sunum içine ekleyin.',
            kind: 'preview_scan',
          });
          setBusy(false);
          return;
        }
        // Pulls the render type from the in-canvas block so the response
        // already has config[categories/series/etc] populated.
        const baseUrl = window.location.pathname.replace(/\/blocks\/(edit|new).*/, '/blocks/api');
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
          scanOnly: isScan,
        });
      }
      // Reflect discovered allowed_values (server-side SELECT DISTINCT) into
      // the variable rows so the user sees what was filled in.
      if (Array.isArray(result.block?.variables) && result.block.variables.length) {
        setVars(result.block.variables.map((vv) => ({
          name: vv.name || '',
          type: vv.type || 'date',
          semantic_tag: vv.semantic_tag || 'other',
          required: vv.required !== false,
          default_str: defaultsToString(vv.default, vv.type),
          allowed_values_str: Array.isArray(vv.allowed_values) ? vv.allowed_values.join(', ') : '',
        })));
      }
      // Phase 7 auto-conceptualize: Şemayı Tara detected concept-bound literal
      // predicates → rewrite the SQL (predicate → {{concept_filters}}) and seed
      // the matching dashboard filters with the extracted values.
      const cz = result.conceptualize;
      if (!previewMode && cz && Array.isArray(cz.seeded_filters) && cz.seeded_filters.length) {
        setSql(cz.rewritten_sql);
        setBlockField(block.id, 'query', cz.rewritten_sql);
        for (const f of cz.seeded_filters) {
          try { addDashboardFilter(f); } catch (_e) { /* already exists — ignore */ }
          if (f.default !== undefined) setFilterValue(f.id, f.default);
        }
        setConceptMsg({ converted: cz.converted || [], skipped: cz.skipped || [] });
      } else if (cz && Array.isArray(cz.skipped) && cz.skipped.length) {
        setConceptMsg({ converted: [], skipped: cz.skipped });
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
    const baseline = (block.query || block.data_source?.original_sql || '').trim();
    if (sql.trim() !== baseline) setStaleHint(true);
  }, [sql, block.query, block.data_source]);

  // Debounced sync of local vars state → manifest. Persists user edits even
  // if they never click Çalıştır before opening the save modal.
  useEffect(() => {
    if (previewMode) return;  // template-edit handles its own state plumbing
    const handle = setTimeout(() => {
      const submit = variablesForSubmit();
      const current = block.variables || [];
      if (JSON.stringify(submit) !== JSON.stringify(current)) {
        setBlockField(block.id, 'variables', submit);
      }
    }, 600);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vars]);

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
            onBlur={pushDraftToManifest}
          />
          <div className="props-sql-footer">
            <button
              type="button"
              className="props-btn props-btn--primary"
              onClick={() => handleRun('scan')}
              disabled={busy || !sql.trim()}
              title="SQL'deki :param'ları + olası değerleri otomatik keşfet (sorgu çalıştırılmaz)"
            >
              <Search size={13} strokeWidth={2} className={busy ? 'spin' : ''} />
              <span>{busy ? 'Taranıyor…' : 'Şemayı Tara'}</span>
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
              <span>SQL değişti — şemayı tekrar tara, sonra çalıştır.</span>
            </div>
          )}
          {conceptMsg && (conceptMsg.converted.length > 0 || conceptMsg.skipped.length > 0) && (
            <div className="props-concept-note">
              {conceptMsg.converted.map((c, i) => (
                <div key={i} className="props-concept-note__row">
                  <Database size={11} strokeWidth={2} />
                  <span><code>{c.column}</code> → <strong>{c.concept}</strong> concept
                    filtresine çevrildi ({c.values.join(', ')})</span>
                </div>
              ))}
              {conceptMsg.skipped.map((s, i) => (
                <div key={`s${i}`} className="props-concept-note__row props-concept-note__row--skip">
                  <AlertTriangle size={11} strokeWidth={2} />
                  <span>{s}</span>
                </div>
              ))}
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

      {/* "Değişkenler" bölümü kaldırıldı — :bind değişkenleri yerine artık
          concept filtreleri kullanılıyor. Yalnızca Çalıştır butonu kalır. */}
      <div className="props-run-row">
        <button
          type="button"
          className="props-btn props-btn--primary props-btn--run"
          onClick={() => handleRun('execute')}
          disabled={busy || !sql.trim()}
          title="Sorguyu çalıştır"
        >
          <Play size={14} strokeWidth={2.2} className={busy ? 'spin' : ''} />
          <span>{busy ? 'Çalışıyor…' : 'Çalıştır'}</span>
        </button>
      </div>
    </>
  );
}


function VariableRow({ v, onChange, onRemove, orphaned }) {
  const isOther = v.semantic_tag === 'other';
  const isEnum = v.type === 'enum_single' || v.type === 'enum_multi';
  const isDate = v.type === 'date';

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

      {/* Phase 6.5.c UX: enum'larda 'Varsayılan' alanını gizle — resolver
          allowed_values'i otomatik default kabul eder. Date / range tiplerinde
          görünür kalır çünkü orada explicit default şart. */}
      {!isEnum && (
        <div className="props-var-row">
          <label className="props-var-label">
            Varsayılan
            {isDate && (
              <span className="props-var-hint">
                {' '}— ISO tarih veya göreceli ifade (today / today - 30d / start_of_month)
              </span>
            )}
          </label>
          {isDate
            ? <DateDefaultInput value={v.default_str} onChange={(s) => onChange({ default_str: s })} />
            : (
              <input
                type="text"
                className="props-input"
                value={v.default_str}
                onChange={(e) => onChange({ default_str: e.target.value })}
                placeholder={TYPE_DEFAULTS[v.type] || ''}
              />
            )}
        </div>
      )}

      {isEnum && (
        <div className="props-var-row">
          <label className="props-var-label">
            Olası değerler
            <span className="props-var-hint">
              {' '}— Şemayı Tara'da otomatik keşfedilir
            </span>
          </label>
          <AllowedValuesChips
            valuesStr={v.allowed_values_str}
            onChange={(s) => onChange({ allowed_values_str: s })}
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


/**
 * Date variable default input: text field for relative expressions
 * ("today - 30d", "start_of_month", or ISO) + a 🗓 calendar quick-pick that
 * opens the native date picker and writes the chosen ISO date into the
 * text field. Preserves the user's right to type a relative expression
 * (essential for templates that should "always show last 30 days") while
 * keeping the picker one click away for fixed-date use cases.
 */
function DateDefaultInput({ value, onChange }) {
  // The native picker needs a controlled <input type="date">. We keep it
  // hidden; clicking the visible icon focuses + showPicker()s it.
  const pickerRef = useState(null);
  function openPicker() {
    const el = document.getElementById(`dvp-${Math.random()}`);
    // Easier: dispatch via a ref-less hack — find sibling by class.
  }
  return (
    <div className="props-date-default">
      <input
        type="text"
        className="props-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="today - 30d / 2026-01-01 / start_of_month"
      />
      <input
        type="date"
        className="props-date-picker"
        value={value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : ''}
        onChange={(e) => { if (e.target.value) onChange(e.target.value); }}
        title="Takvim ile tarih seç"
      />
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


/**
 * Chip-style editor for enum allowed_values. Each value renders as a
 * removable chip; an inline input accepts new ones (Enter or comma to
 * commit). Storage stays as a comma-separated string so we don't have to
 * rewrite the rest of the variable form, but the visual is much friendlier
 * than a raw text field — especially after Şemayı Tara auto-fills 10+ items.
 */
function AllowedValuesChips({ valuesStr, onChange }) {
  const [draft, setDraft] = useState('');
  const values = (valuesStr || '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s !== '');

  function commit(items) {
    onChange(items.join(', '));
  }

  function addValue(raw) {
    const trimmed = (raw || '').trim();
    if (!trimmed) return;
    if (values.includes(trimmed)) {
      setDraft('');
      return;
    }
    commit([...values, trimmed]);
    setDraft('');
  }

  function removeAt(i) {
    const next = values.slice();
    next.splice(i, 1);
    commit(next);
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addValue(draft);
    } else if (e.key === 'Backspace' && draft === '' && values.length > 0) {
      e.preventDefault();
      removeAt(values.length - 1);
    }
  }

  return (
    <div className="props-chips">
      <div className="props-chips__list">
        {values.map((v, i) => (
          <span key={`${v}-${i}`} className="props-chip">
            <span className="props-chip__text">{v}</span>
            <button
              type="button"
              className="props-chip__remove"
              onClick={() => removeAt(i)}
              title="Bu değeri sil"
            >×</button>
          </span>
        ))}
        <input
          type="text"
          className="props-chips__input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onBlur={() => addValue(draft)}
          placeholder={values.length === 0 ? 'değer ekle (Enter veya virgül)' : '+ ekle'}
        />
      </div>
    </div>
  );
}
