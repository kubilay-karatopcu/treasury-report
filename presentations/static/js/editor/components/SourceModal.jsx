import { useMemo, useState } from 'react';
import { Database, Clock, AlertTriangle, Copy, Check, RefreshCw, Loader2 } from 'lucide-react';
import Modal from './Modal.jsx';
import useStore from '../lib/store.js';
import { copyToClipboard } from '../lib/clipboard.js';

/**
 * Block source modal — shows the SQL that produced the block, plus preview
 * rows and a copy-SQL button. Optional "Tazele" button at the bottom if a
 * refresh handler is provided.
 *
 * Props:
 *   open        : bool
 *   onClose     : () => void
 *   block       : the block whose data_source we render
 *   onRefresh   : optional () => Promise<void>   — wires the refresh button
 *   refreshing  : optional bool                  — shows loading state
 */
export default function SourceModal({ open, onClose, block, onRefresh, refreshing = false }) {
  const [copied, setCopied] = useState(null);  // null | "template" | "runnable"

  // ALL hooks must run unconditionally, BEFORE any early return — otherwise
  // a block whose data_source transitions empty→present changes the hook
  // count between renders (React #310). Compute the runnable SQL up front;
  // it yields '' when there's no data_source.
  const ds = block && block.data_source;
  const runnableSql = useMemo(
    () => substituteBindParams((ds && (ds.sql || ds.original_sql)) || '', ds && ds.bind_params),
    [ds && ds.sql, ds && ds.original_sql, ds && ds.bind_params],
  );

  if (!block) return null;

  // ── Gracefully handle "no data_source yet" (e.g. snapshot of an old block)
  if (!ds || (!ds.sql && !ds.original_sql)) {
    return (
      <Modal open={open} onClose={onClose} title={block.title || 'Kaynakça'} size="md">
        <div className="src-empty">
          Bu blokta kayıtlı bir SQL yok. Henüz veri-bağımlı bir blok
          olarak güncellenmemiş olabilir.
        </div>
      </Modal>
    );
  }

  // Template = user-written SQL with `:binds` (original_sql preferred).
  const templateSql = ds.original_sql || ds.sql;
  const showedRewritten = ds.rewritten && ds.sql && ds.sql !== ds.original_sql;
  const hasResolved = ds.bind_params && Object.keys(ds.bind_params).length > 0;

  function doCopy(text, key) {
    copyToClipboard(text)
      .then(() => {
        setCopied(key);
        setTimeout(() => setCopied(null), 1600);
      })
      .catch((e) => {
        console.error('clipboard copy failed:', e);
        alert('Panoya kopyalanamadı: ' + (e.message || String(e)));
      });
  }

  const footer = onRefresh && (
    <button
      type="button"
      className="btn-secondary"
      onClick={onRefresh}
      disabled={refreshing}
      title="Bu bloğun verilerini Oracle'dan yeniden çek"
    >
      <RefreshCw size={13} strokeWidth={1.8} className={refreshing ? 'ts-spin' : ''} />
      <span>{refreshing ? 'Yenileniyor…' : 'Veriyi Tazele'}</span>
    </button>
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Kaynakça — ${block.title || block.id}`}
      size="lg"
      footer={footer}
    >
      {/* ── Meta strip ────────────────────────────────────────────── */}
      <div className="src-meta">
        {ds.row_count != null && (
          <span className="src-meta-item">
            <Database size={11} strokeWidth={1.8} />
            {ds.row_count.toLocaleString('tr-TR')} satır
          </span>
        )}
        {ds.executed_at && (
          <span className="src-meta-item">
            <Clock size={11} strokeWidth={1.8} />
            {formatTimestamp(ds.executed_at)}
          </span>
        )}
        {ds.truncated && (
          <span className="src-meta-item src-meta-item--warn">
            <AlertTriangle size={11} strokeWidth={2} />
            İlk {ds.cap?.toLocaleString('tr-TR') || '—'} satır (kesildi)
          </span>
        )}
      </div>

      {ds.reason && <div className="src-reason">{ds.reason}</div>}

      {/* ── SQL block ─────────────────────────────────────────────── */}
      <div className="src-section">
        <div className="src-section-title">
          <span>SQL (şablon, :bind'li)</span>
          <button
            type="button"
            className="src-copy-btn"
            onClick={() => doCopy(templateSql, 'template')}
            title="Şablon SQL'i panoya kopyala"
          >
            {copied === 'template'
              ? <><Check size={11} strokeWidth={2.2} />Kopyalandı</>
              : <><Copy size={11} strokeWidth={1.8} />Kopyala</>}
          </button>
        </div>
        <pre className="src-sql ts-scroll">{templateSql}</pre>

        {hasResolved && (
          <>
            <div className="src-section-title" style={{ marginTop: 14 }}>
              <span>Çalıştırılabilir SQL (değerler yerleştirilmiş)</span>
              <button
                type="button"
                className="src-copy-btn"
                onClick={() => doCopy(runnableSql, 'runnable')}
                title="Bind değerleri substitute edilmiş halini kopyala (SQL Developer / DBeaver'a yapıştırıp çalıştırabilirsin)"
              >
                {copied === 'runnable'
                  ? <><Check size={11} strokeWidth={2.2} />Kopyalandı</>
                  : <><Copy size={11} strokeWidth={1.8} />Kopyala</>}
              </button>
            </div>
            <pre className="src-sql src-sql--runnable ts-scroll">{runnableSql}</pre>
          </>
        )}

        {showedRewritten && (
          <details className="src-rewritten">
            <summary>Sistemin gerçek çalıştırdığı sürüm (positional bind'lerle)</summary>
            <pre className="src-sql src-sql--rewritten ts-scroll">{ds.sql}</pre>
          </details>
        )}

        {/* Concept filtresi enjekte edilerek koşulduysa fiilen çalıştırılan
            SQL'i göster — dashboard filtresinin sorguya nasıl indiğini
            görmenin tek yolu bu (şablon SQL'e asla yazılmaz). */}
        {ds.executed_sql && (
          <details className="src-rewritten" open>
            <summary>Filtreler uygulanmış hali (son çalıştırılan SQL)</summary>
            <pre className="src-sql src-sql--rewritten ts-scroll">
              {substituteBindParams(ds.executed_sql, ds.executed_params || ds.bind_params)}
            </pre>
          </details>
        )}
      </div>

      {/* ── Preview rows ──────────────────────────────────────────── */}
      <div className="src-section">
        <div className="src-section-title">
          <span>Örnek satırlar (ilk {ds.preview_rows?.length || 0})</span>
        </div>
        {ds.preview_rows && ds.preview_rows.length > 0 ? (
          <div className="src-preview-wrap ts-scroll">
            <table className="src-preview">
              <thead>
                <tr>
                  {(ds.columns || []).map((c, i) => (
                    <th key={i}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ds.preview_rows.map((row, ri) => (
                  <tr key={ri}>
                    {row.map((v, ci) => (
                      <td key={ci}>{formatCell(v)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="docs-empty">Sorgu satır döndürmedi.</div>
        )}
      </div>
    </Modal>
  );
}


/**
 * Substitute bind parameter values back into the SQL so the user can copy
 * a directly-runnable query. Inverse of presentations/sql/binder.py's
 * expansion.
 *
 * Per-type literal formatting:
 *   - string  → 'TRY'  (single-quoted; embedded ' doubled per SQL escape rule)
 *   - number  → 42 / 3.14   (as-is)
 *   - bool    → 1 / 0  (Oracle has no boolean literal)
 *   - null    → NULL
 *   - ISO date string (YYYY-MM-DD) → DATE '2026-04-21'
 *
 * Matching uses the same `:name` regex as the binder (negative lookbehind
 * for `::` to skip Postgres casts). enum_multi values are already
 * positional (`:foo_0`, `:foo_1`) by the time we get here.
 */
export function substituteBindParams(sql, params) {
  if (!sql) return '';
  if (!params || typeof params !== 'object') return sql;
  // Match :ident not preceded by ':' (skip Postgres ::cast).
  const out = sql.replace(/(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)\b/g, (match, name) => {
    if (!(name in params)) return match;          // unknown bind — leave as-is
    return _formatLiteral(params[name]);
  });
  // DuckDB tarafında koşan sorgular $name bind'i kullanır (apply-filters
  // dataset_sql yolu) — bilinen anahtarları aynı şekilde yerleştir.
  return out.replace(/\$([a-zA-Z_][a-zA-Z0-9_]*)\b/g, (match, name) => {
    if (!(name in params)) return match;
    return _formatLiteral(params[name]);
  });
}


function _formatLiteral(value) {
  if (value === null || value === undefined) return 'NULL';
  if (typeof value === 'boolean') return value ? '1' : '0';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') {
    // ISO date detection: "YYYY-MM-DD" or "YYYY-MM-DDT..." → emit DATE literal.
    if (/^\d{4}-\d{2}-\d{2}(T|$)/.test(value)) {
      const isoDate = value.slice(0, 10);
      return `DATE '${isoDate}'`;
    }
    // Generic string: single-quote + escape embedded quotes.
    return "'" + value.replace(/'/g, "''") + "'";
  }
  // Fallback (Date object, etc.) — toString then re-quote.
  return "'" + String(value).replace(/'/g, "''") + "'";
}


function formatCell(v) {
  if (v === null || v === undefined) return <span className="src-null">∅</span>;
  if (typeof v === 'number') return v.toLocaleString('tr-TR', { maximumFractionDigits: 4 });
  return String(v);
}


function formatTimestamp(iso) {
  // Lightweight: assume server emits `YYYY-MM-DDTHH:MM:SSZ`. Falls back gracefully.
  try {
    const d = new Date(iso);
    if (!isNaN(d.getTime())) {
      return d.toLocaleString('tr-TR', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit',
      });
    }
  } catch {}
  return iso;
}