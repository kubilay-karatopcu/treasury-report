import { useState } from 'react';
import { Database, Clock, AlertTriangle, Copy, Check, RefreshCw } from 'lucide-react';
import Modal from './Modal.jsx';

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
  const [copied, setCopied] = useState(false);

  if (!block) return null;
  const ds = block.data_source;

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

  const sqlToCopy = ds.original_sql || ds.sql;
  const showedRewritten = ds.rewritten && ds.sql && ds.sql !== ds.original_sql;

  function copy() {
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(sqlToCopy).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
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
          <span>SQL</span>
          <button
            type="button"
            className="src-copy-btn"
            onClick={copy}
            title="SQL'i panoya kopyala"
          >
            {copied
              ? <><Check size={11} strokeWidth={2.2} />Kopyalandı</>
              : <><Copy size={11} strokeWidth={1.8} />Kopyala</>}
          </button>
        </div>
        <pre className="src-sql ts-scroll">{sqlToCopy}</pre>

        {showedRewritten && (
          <details className="src-rewritten">
            <summary>Sistemin gerçek çalıştırdığı sürüm</summary>
            <pre className="src-sql src-sql--rewritten ts-scroll">{ds.sql}</pre>
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
