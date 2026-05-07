import { useMemo } from 'react';
import { AgGridReact } from 'ag-grid-react';
// AG Grid stylesheets are loaded via CDN <link> in editor.html / snapshot.html
// (esbuild's --loader:.css=empty would silently drop these imports otherwise).

const TYPE_FORMATTERS = {
  number:  (v) => typeof v === 'number' ? v.toLocaleString('tr-TR') : v,
  percent: (v) => typeof v === 'number' ? `%${v.toFixed(2)}` : v,
  date:    (v) => v == null ? '' : String(v).slice(0, 10),
  text:    (v) => v == null ? '' : String(v),
};

export default function DataTable({ block }) {
  const config = block.config || {};
  const cols = config.columns || [];
  const rows = config.rows || [];

  const columnDefs = useMemo(() => cols.map((c) => ({
    field: c.field,
    headerName: c.header || c.field,
    sortable: true,
    resizable: true,
    flex: 1,
    minWidth: 90,
    valueFormatter: c.type && TYPE_FORMATTERS[c.type]
      ? (params) => TYPE_FORMATTERS[c.type](params.value)
      : undefined,
    cellStyle: c.type === 'number' || c.type === 'percent'
      ? { textAlign: 'right' } : undefined,
  })), [cols]);

  if (cols.length === 0) {
    return <div className="chart-empty">Tablo için kolon tanımı yok.</div>;
  }

  // domLayout="autoHeight" lets the block grow with the data; the canvas
  // (which has overflow-y: auto) handles overall page scroll. Combined with
  // the viewport-lock CSS this stays well-behaved.
  return (
    <div className="ag-theme-alpine data-table-wrapper">
      <AgGridReact
        columnDefs={columnDefs}
        rowData={rows}
        animateRows={true}
        suppressMenuHide={false}
        domLayout="autoHeight"
      />
    </div>
  );
}
