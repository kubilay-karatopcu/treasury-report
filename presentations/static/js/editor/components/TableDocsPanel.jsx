import { useState, useEffect, useMemo } from 'react';
import { Database, Tag, Hash, Type as TypeIcon, Eye, Loader2, AlertTriangle, X, Sparkles } from 'lucide-react';
import { AgGridReact } from 'ag-grid-react';
import useStore from '../lib/store.js';
import { fetchTablePreview, fetchTableConcepts } from '../lib/api.js';

/**
 * Side-dock variant of the catalog docs (was a modal previously).
 * Rendered next to the left sidebar so the user can read schema + preview
 * data while editing SQL on the right Properties Panel at the same time.
 */
export default function TableDocsPanel({ width, onResizeStart }) {
  const docsTable      = useStore((s) => s.docsTable);
  const closeDocsTable = useStore((s) => s.closeDocsTable);

  const table  = docsTable?.table;
  const domain = docsTable?.domain;

  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [previewErr,  setPreviewErr]  = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  // Phase 7 — per-column concept status, keyed by UPPERCASE column name.
  const [conceptCols, setConceptCols] = useState({});

  // Reset preview state when target table changes (or panel closes).
  useEffect(() => {
    setPreviewOpen(false);
    setPreviewData(null);
    setPreviewErr(null);
    setLoadingPreview(false);
  }, [table?.id]);

  // Phase 7 — fetch concept status for the table's columns.
  useEffect(() => {
    let cancelled = false;
    if (table?.id) {
      fetchTableConcepts(table.id)
        .then((res) => { if (!cancelled) setConceptCols(res.columns || {}); })
        .catch(() => { if (!cancelled) setConceptCols({}); });
    } else {
      setConceptCols({});
    }
    return () => { cancelled = true; };
  }, [table?.id]);

  // ESC ile panel kapansın.
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') closeDocsTable(); }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [closeDocsTable]);

  if (!table) return null;

  const cols    = table.columns || [];
  const filters = table.common_filters || [];
  const hasConceptInfo = Object.keys(conceptCols).length > 0;

  async function showPreview() {
    setPreviewOpen(true);
    if (previewData || loadingPreview) return;
    setLoadingPreview(true);
    setPreviewErr(null);
    try {
      const data = await fetchTablePreview(table.id);
      setPreviewData(data);
    } catch (e) {
      setPreviewErr(e.message);
    } finally {
      setLoadingPreview(false);
    }
  }

  return (
    <aside className="docs-side-panel" style={width ? { width } : undefined}>
      {onResizeStart && (
        <div className="resize-handle resize-handle--right"
             onMouseDown={onResizeStart} />
      )}
      <header className="docs-side-panel__header">
        <div>
          <div className="docs-side-panel__title" title={table.id}>{table.id}</div>
          {domain?.label && (
            <div className="docs-side-panel__sub">
              <Database size={11} strokeWidth={1.8} /> {domain.label}
              {table.rows && <> · ~{table.rows} satır</>}
            </div>
          )}
        </div>
        <button
          type="button"
          className="props-close-btn"
          onClick={closeDocsTable}
          title="Kapat (ESC)"
        >
          <X size={16} strokeWidth={2} />
        </button>
      </header>

      <div className="docs-side-panel__body ts-scroll">
        {table.desc && <p className="docs-desc">{table.desc}</p>}

        <div className="docs-section">
          <div className="docs-section-title">
            <TypeIcon size={12} strokeWidth={2} />
            <span>Kolonlar ({cols.length})</span>
          </div>
          {cols.length === 0 ? (
            <div className="docs-empty">Bu tablo için kolon tanımı yok.</div>
          ) : (
            <table className="docs-cols-table">
              <thead>
                <tr>
                  <th>Kolon</th><th>Tip</th><th className="docs-th-null">Null?</th>
                  <th>Concept / Filtre</th>
                </tr>
              </thead>
              <tbody>
                {cols.map((c) => (
                  <tr key={c.name}>
                    <td className="docs-col-name">{c.name}</td>
                    <td className="docs-col-type">{c.type || '—'}</td>
                    <td className="docs-col-null">
                      {c.nullable === false
                        ? <span className="docs-pill docs-pill--required">NOT NULL</span>
                        : <span className="docs-pill docs-pill--optional">NULL</span>}
                    </td>
                    <td className="docs-col-concept">
                      <ConceptCell info={conceptCols[String(c.name).toUpperCase()]} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {hasConceptInfo && (
            <div className="docs-concept-legend">
              <span className="docs-concept docs-concept--bound">
                <Sparkles size={10} strokeWidth={2} />concept
              </span>
              filtrede kullanılabilir.
              <span className="docs-concept docs-concept--suggested">öneri</span>
              henüz onaylanmadı — Concept &gt; İnceleme&apos;den bağlayın.
            </div>
          )}
        </div>

        {filters.length > 0 && (
          <div className="docs-section">
            <div className="docs-section-title">
              <Tag size={12} strokeWidth={2} />
              <span>Sık Kullanılan Filtreler</span>
            </div>
            <div className="docs-filters">
              {filters.map((f, i) => (
                <div className="docs-filter" key={i}>
                  <div className="docs-filter-label">{f.label}</div>
                  <code className="docs-filter-expr">{f.expression}</code>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="docs-section">
          <div className="docs-section-title">
            <Eye size={12} strokeWidth={2} />
            <span>Veri Önizleme</span>
          </div>

          {!previewOpen && (
            <button type="button" className="btn-secondary docs-preview-load" onClick={showPreview}>
              <Eye size={12} strokeWidth={1.8} />
              <span>İlk 5000 satırı getir</span>
            </button>
          )}
          {previewOpen && loadingPreview && (
            <div className="docs-preview-loading">
              <Loader2 size={14} className="ts-spin" />
              <span>Veri çekiliyor…</span>
            </div>
          )}
          {previewOpen && previewErr && (
            <div className="docs-preview-error">
              <AlertTriangle size={12} strokeWidth={2} />
              <span>{previewErr}</span>
            </div>
          )}
          {previewOpen && previewData && <DataPreviewGrid data={previewData} />}
        </div>
      </div>
    </aside>
  );
}


/**
 * Per-column concept status. Precedence:
 *   bound_concept     → green "X"      (human_verified, compiler-usable)
 *   suggested_concept → yellow "öneri: X" (table-doc hint, not yet bound)
 *   filterable        → muted "filtrelenebilir"
 *   otherwise         → "—"
 */
function ConceptCell({ info }) {
  if (!info) return <span className="docs-col-dash">—</span>;
  const { bound_concept, suggested_concept, transform, filterable } = info;

  if (bound_concept) {
    return (
      <span
        className="docs-concept docs-concept--bound"
        title={transform ? `transform: ${transform}` : 'human_verified binding'}
      >
        <Sparkles size={10} strokeWidth={2} />
        {bound_concept}
      </span>
    );
  }
  if (suggested_concept) {
    return (
      <span
        className="docs-concept docs-concept--suggested"
        title="Table-doc önerisi — Concept > İnceleme'den onaylayın."
      >
        öneri: {suggested_concept}
      </span>
    );
  }
  if (filterable) {
    return <span className="docs-concept docs-concept--plain">filtrelenebilir</span>;
  }
  return <span className="docs-col-dash">—</span>;
}


function DataPreviewGrid({ data }) {
  const columnDefs = useMemo(() =>
    (data.columns || []).map((name) => ({
      field: name, headerName: name,
      sortable: true, filter: true, resizable: true, minWidth: 90,
    })),
    [data.columns],
  );
  const rowData = useMemo(() =>
    (data.rows || []).map((row) => {
      const obj = {};
      (data.columns || []).forEach((col, i) => { obj[col] = row[i]; });
      return obj;
    }),
    [data.rows, data.columns],
  );

  return (
    <div className="docs-preview-grid-wrap">
      <div className="docs-preview-grid-meta">
        <span>
          {(data.row_count || 0).toLocaleString('tr-TR')} satır
          {data.truncated ? ` (ilk ${data.cap?.toLocaleString('tr-TR') || 5000} gösteriliyor)` : ''}
        </span>
        <span>{data.columns?.length || 0} kolon</span>
      </div>
      <div className="ag-theme-alpine docs-preview-grid">
        <AgGridReact
          columnDefs={columnDefs}
          rowData={rowData}
          defaultColDef={{ sortable: true, filter: true, resizable: true, minWidth: 80 }}
          animateRows={false}
          pagination={true}
          paginationPageSize={100}
          suppressMenuHide={true}
        />
      </div>
    </div>
  );
}
