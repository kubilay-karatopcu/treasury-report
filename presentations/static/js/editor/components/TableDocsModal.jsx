import { useState, useEffect, useMemo } from 'react';
import { Database, Tag, Hash, Type as TypeIcon, Eye, Loader2, AlertTriangle } from 'lucide-react';
import { AgGridReact } from 'ag-grid-react';
import Modal from './Modal.jsx';
import { fetchTablePreview } from '../lib/api.js';

/**
 * Documentation modal for a single catalog table.
 *
 * Props:
 *   open      : bool
 *   onClose   : () => void
 *   table     : catalog table object — { id, desc, rows, columns, common_filters }
 *   domain    : optional parent domain object (just for the label/icon line)
 *
 * Three sections, all collapsible/lazy:
 *   - Schema (column name + Oracle type + nullable)
 *   - Common filters (Oracle catalog tables only)
 *   - Data preview (AG Grid, up to 5000 rows) — lazy, fetched on demand
 */
export default function TableDocsModal({ open, onClose, table, domain }) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [previewErr, setPreviewErr] = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);

  // Reset preview state when the modal closes or the table changes.
  useEffect(() => {
    if (!open) {
      setPreviewOpen(false);
      setPreviewData(null);
      setPreviewErr(null);
      setLoadingPreview(false);
    }
  }, [open, table?.id]);

  if (!table) return null;

  const cols = table.columns || [];
  const filters = table.common_filters || [];

  async function showPreview() {
    setPreviewOpen(true);
    if (previewData || loadingPreview) return;   // cached / in-flight
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
    <Modal
      open={open}
      onClose={onClose}
      title={table.id}
      size="lg"
    >
      <div className="docs-section">
        <div className="docs-meta">
          {domain?.label && (
            <span className="docs-meta-item">
              <Database size={11} strokeWidth={1.8} />
              {domain.label}
            </span>
          )}
          {table.rows && (
            <span className="docs-meta-item">
              <Hash size={11} strokeWidth={1.8} />
              ~{table.rows} satır
            </span>
          )}
        </div>
        {table.desc && <p className="docs-desc">{table.desc}</p>}
      </div>

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
                <th>Kolon</th>
                <th>Tip</th>
                <th className="docs-th-null">Null?</th>
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
                </tr>
              ))}
            </tbody>
          </table>
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
              <FilterRow key={i} filter={f} />
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
          <button
            type="button"
            className="btn-secondary docs-preview-load"
            onClick={showPreview}
          >
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

        {previewOpen && previewData && (
          <DataPreviewGrid data={previewData} />
        )}
      </div>
    </Modal>
  );
}


function FilterRow({ filter }) {
  return (
    <div className="docs-filter">
      <div className="docs-filter-label">{filter.label}</div>
      <code className="docs-filter-expr">{filter.expression}</code>
    </div>
  );
}


/**
 * AG Grid (React) for the data preview.
 */
function DataPreviewGrid({ data }) {
  const columnDefs = useMemo(() =>
    (data.columns || []).map((name) => ({
      field:      name,
      headerName: name,
      sortable:   true,
      filter:     true,
      resizable:  true,
      minWidth:   90,
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
          {data.truncated ? ` (ilk ${data.cap.toLocaleString('tr-TR')} gösteriliyor)` : ''}
        </span>
        <span>{data.columns?.length || 0} kolon</span>
      </div>
      <div className="ag-theme-alpine docs-preview-grid">
        <AgGridReact
          columnDefs={columnDefs}
          rowData={rowData}
          defaultColDef={{
            sortable: true, filter: true, resizable: true, minWidth: 80,
          }}
          animateRows={false}
          pagination={true}
          paginationPageSize={100}
          suppressMenuHide={true}
        />
      </div>
    </div>
  );
}