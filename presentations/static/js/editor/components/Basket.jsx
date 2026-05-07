import { useEffect, useState } from 'react';
import useStore from '../lib/store.js';
import { fetchSources, updateBasket, previewView } from '../lib/api.js';

function basketHas(basket, tableId) {
  return basket.some((b) => b.table === tableId);
}

function Preview({ tableId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const viewName = tableId.split('.').pop().toLowerCase();

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    previewView(viewName)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [viewName]);

  if (error) return <div className="basket-preview-error">{error}</div>;
  if (!data) return <div className="basket-preview-loading">Yükleniyor…</div>;
  if (data.row_count === 0) {
    return <div className="basket-preview-empty">Veri yok (lokal CSV mock yok).</div>;
  }

  return (
    <div className="basket-preview">
      <div className="basket-preview-meta">{data.row_count.toLocaleString('tr-TR')} satır</div>
      <table className="basket-preview-table">
        <thead>
          <tr>{data.columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {data.rows.slice(0, 5).map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => <td key={j}>{String(cell)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TableRow({ table, isInBasket, onToggle, expanded, onToggleExpand }) {
  return (
    <div className="basket-table">
      <div className="basket-table-row">
        <button
          className="basket-table-expand"
          onClick={onToggleExpand}
          aria-label={expanded ? 'Kapat' : 'Aç'}
        >
          {expanded ? '▾' : '▸'}
        </button>
        <div className="basket-table-info" onClick={onToggleExpand}>
          <div className="basket-table-name">{table.id}</div>
          <div className="basket-table-desc">{table.desc} · {table.rows} satır</div>
        </div>
        <button
          className={`basket-add-btn${isInBasket ? ' is-added' : ''}`}
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          title={isInBasket ? 'Sepetten çıkar' : 'Sepete ekle'}
        >
          {isInBasket ? '✓' : '+'}
        </button>
      </div>
      {expanded && (
        <div className="basket-table-body">
          <div className="basket-table-cols">
            {table.columns.map((c) => (
              <span key={c.name} className="basket-col-pill" title={c.type}>{c.name}</span>
            ))}
          </div>
          {isInBasket && <Preview tableId={table.id} />}
        </div>
      )}
    </div>
  );
}

export default function Basket() {
  const manifest = useStore((s) => s.manifest);
  const setManifest = useStore((s) => s.setManifest);
  const [catalog, setCatalog] = useState(null);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(new Set());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchSources()
      .then(setCatalog)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="basket-error">{error}</div>;
  if (!catalog) return <div className="basket-loading">Kaynaklar yükleniyor…</div>;

  const basket = manifest?.basket || [];

  async function toggle(table) {
    if (busy) return;
    const newBasket = basketHas(basket, table.id)
      ? basket.filter((b) => b.table !== table.id)
      : [...basket, { table: table.id, columns: table.columns.map((c) => c.name), row_filter: null }];

    setBusy(true);
    try {
      const result = await updateBasket(newBasket);
      // Optimistic local update — backend persisted, mirror it.
      setManifest({ ...manifest, basket: result.basket, version: result.version });
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function toggleExpand(tableId) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(tableId)) next.delete(tableId); else next.add(tableId);
      return next;
    });
  }

  return (
    <div className="basket-panel">
      <div className="sidebar-label">
        Veri Kaynakları
        {basket.length > 0 && <span className="basket-count">{basket.length}</span>}
      </div>
      {catalog.domains.map((domain) => (
        <div key={domain.id} className="basket-domain">
          <div className="basket-domain-label">{domain.label}</div>
          {domain.tables.map((t) => (
            <TableRow
              key={t.id}
              table={t}
              isInBasket={basketHas(basket, t.id)}
              onToggle={() => toggle(t)}
              expanded={expanded.has(t.id)}
              onToggleExpand={() => toggleExpand(t.id)}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
