import { Database, Tag, Hash, Type as TypeIcon } from 'lucide-react';
import Modal from './Modal.jsx';

/**
 * Documentation modal for a single catalog table.
 *
 * Props:
 *   open      : bool
 *   onClose   : () => void
 *   table     : catalog table object — { id, desc, rows, columns, common_filters }
 *   domain    : optional parent domain object (just for the label/icon line)
 *
 * Renders schema (column name + Oracle type + nullable badge) and common_filters
 * (clickable "copy" pills — feels useful for power users writing SQL by hand).
 */
export default function TableDocsModal({ open, onClose, table, domain }) {
  if (!table) return null;

  const cols = table.columns || [];
  const filters = table.common_filters || [];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={table.id}
      size="md"
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
