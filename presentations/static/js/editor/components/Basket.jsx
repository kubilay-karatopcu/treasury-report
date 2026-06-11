import { useEffect, useMemo, useState } from 'react';
import {
  ChevronRight, Building2, Percent, Calendar, Network, Database, Tag, Eye,
  Upload, Trash2, Search,
} from 'lucide-react';
import useStore from '../lib/store.js';
import { fetchSources, updateBasket, uploadDelete } from '../lib/api.js';

// Phase 12.sunum-basket — Sunum sidebar now mirrors Hazırlık's basket-
// only pattern. We no longer render the full catalog tree; instead, only
// items the user has already added via Keşif/Hazırlık show up here,
// grouped into Tablolar + Bloklar with per-group search inputs.
//
// We still fetch the catalog because basket items store table IDs only;
// the catalog gives us human-readable schema/name/desc + the column
// list for the docs panel.

export default function Basket() {
  const manifest    = useStore((s) => s.manifest);
  const setManifest = useStore((s) => s.setManifest);

  const [catalog, setCatalog]   = useState(null);
  const [error, setError]       = useState(null);
  const [busy, setBusy]         = useState(false);
  const [tableSearch, setTableSearch] = useState('');
  const [blockSearch, setBlockSearch] = useState('');
  const setDocsTable = useStore((s) => s.setDocsTable);

  // Index catalog tables by ID for fast lookup when enriching basket items.
  const tableById = useMemo(() => {
    const map = {};
    if (!catalog) return map;
    for (const d of (catalog.domains || [])) {
      for (const t of (d.tables || [])) {
        if (t.id) map[t.id] = { table: t, domain: d };
      }
    }
    return map;
  }, [catalog]);

  async function reloadCatalog() {
    try {
      const data = await fetchSources();
      setCatalog(data);
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    reloadCatalog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) return <div className="sidebar-error">{error}</div>;

  const basket = manifest?.basket || [];

  // Split basket: items with kind="block" go into "Bloklar"; everything
  // else (legacy and explicit kind="table") goes into "Tablolar".
  const { tableItems, blockItems } = useMemo(() => {
    const tables = [];
    const blocks = [];
    for (const b of basket) {
      if (b.kind === 'block') blocks.push(b);
      else tables.push(b);
    }
    return { tableItems: tables, blockItems: blocks };
  }, [basket]);

  // Enrich table-basket items with catalog metadata (desc, rows, etc.).
  const enrichedTables = useMemo(() => {
    return tableItems.map((b) => {
      const meta = tableById[b.table] || null;
      const tid = b.table || '';
      const schema = tid.includes('.') ? tid.split('.')[0] : '';
      const name = tid.includes('.') ? tid.split('.').slice(1).join('.') : tid;
      return {
        ...b,
        tid,
        schema,
        name,
        catalog: meta?.table || null,
        domain: meta?.domain || null,
        typeBadge: sourceBadge(b),
        // Hazırlık'ta üretilen tablolar (manuel SQL / join / filter / aggregate)
        // katalogda yok ama materialise edilmiş DuckDB view'ları var → docs
        // panelinde kolonlarını + önizlemelerini gösterebiliriz.
        isProduced: b.source === 'sql' || b.source === 'derived',
      };
    });
  }, [tableItems, tableById]);

  const filteredTables = useMemo(() => {
    const q = tableSearch.trim().toLowerCase();
    if (!q) return enrichedTables;
    return enrichedTables.filter((it) =>
      (it.tid || '').toLowerCase().includes(q)
      || (it.catalog?.desc || '').toLowerCase().includes(q),
    );
  }, [enrichedTables, tableSearch]);

  const filteredBlocks = useMemo(() => {
    const q = blockSearch.trim().toLowerCase();
    if (!q) return blockItems;
    return blockItems.filter((b) =>
      (b.name || '').toLowerCase().includes(q)
      || (b.block_type || '').toLowerCase().includes(q)
      || (b.tags || []).join(' ').toLowerCase().includes(q),
    );
  }, [blockItems, blockSearch]);

  function openDocs(table, domain) {
    const cur = useStore.getState().docsTable;
    if (cur && cur.table?.id === table.id) {
      useStore.getState().closeDocsTable();
    } else {
      setDocsTable({ table, domain });
    }
  }

  // Katalog tablosu → katalog dökümanı; üretilmiş tablo (manuel SQL/türetilmiş)
  // → DuckDB view'ından kolon + önizleme gösteren sentetik doc.
  function openDocsFor(it) {
    if (it.catalog) {
      openDocs(it.catalog, it.domain);
    } else if (it.isProduced) {
      openDocs({
        id: it.tid, name: it.name, produced: true,
        source: it.source, derivation_kind: it.derivation_kind,
        badge: it.typeBadge,
      }, null);
    }
  }

  async function handleDeleteUpload(uploadId) {
    if (!confirm('Bu yüklü dosyayı silmek istediğinden emin misin?')) return;
    setBusy(true);
    try {
      await uploadDelete(uploadId);
      const remainingBasket = basket.filter(
        (b) => !(b.table || '').startsWith(`upload__${uploadId}__`),
      );
      if (remainingBasket.length !== basket.length) {
        const result = await updateBasket(remainingBasket);
        setManifest({ ...manifest, basket: result.basket, version: result.version });
      }
      await reloadCatalog();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const hasNothing = tableItems.length === 0 && blockItems.length === 0;

  return (
    <div className="sources-list sources-list--basket">
      {hasNothing && (
        <div className="sidebar-empty">
          Sepetin boş. Önce Keşif'ten tablo veya blok ekle — burada
          gözükecekler.
        </div>
      )}

      {/* ── Tablolar ────────────────────────────────────────────── */}
      {tableItems.length > 0 && (
        <div className="sources-basket-group">
          <div className="sources-basket-group__title">
            <Database size={11} strokeWidth={2} />
            <span>Tablolar</span>
            <span className="sources-basket-group__count">{tableItems.length}</span>
          </div>
          <div className="sources-basket-search">
            <Search size={11} strokeWidth={2} />
            <input
              type="text"
              placeholder="Tablo ara…"
              value={tableSearch}
              onChange={(e) => setTableSearch(e.target.value)}
            />
          </div>
          <div className="sources-basket-list">
            {filteredTables.map((it) => (
              <BasketTableRow
                key={it.tid}
                item={it}
                onOpenDocs={() => openDocsFor(it)}
                onDeleteUpload={handleDeleteUpload}
              />
            ))}
            {filteredTables.length === 0 && tableSearch && (
              <div className="sidebar-empty sidebar-empty--mini">
                "{tableSearch}" ile eşleşen tablo yok.
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Bloklar ─────────────────────────────────────────────── */}
      {blockItems.length > 0 && (
        <div className="sources-basket-group">
          <div className="sources-basket-group__title">
            <Tag size={11} strokeWidth={2} />
            <span>Bloklar</span>
            <span className="sources-basket-group__count">{blockItems.length}</span>
          </div>
          <div className="sources-basket-search">
            <Search size={11} strokeWidth={2} />
            <input
              type="text"
              placeholder="Blok ara…"
              value={blockSearch}
              onChange={(e) => setBlockSearch(e.target.value)}
            />
          </div>
          <div className="sources-basket-list">
            {filteredBlocks.map((b) => (
              <div key={b.library_id} className="sources-table-wrap sources-table-wrap--block">
                <div className="sources-table sources-table--readonly">
                  <div className="sources-table-info">
                    <div className="sources-table-name">{b.name || b.library_id}</div>
                    <div className="sources-table-desc">
                      {b.block_type || 'blok'}
                      {b.owner_id ? ` · ${b.owner_id}` : ''}
                    </div>
                  </div>
                </div>
              </div>
            ))}
            {filteredBlocks.length === 0 && blockSearch && (
              <div className="sidebar-empty sidebar-empty--mini">
                "{blockSearch}" ile eşleşen blok yok.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}


// A short type label for non-table basket entries derived from the scope
// (manual SQL / filter / aggregate nodes) so the Sunum sidebar shows where
// the data comes from. Plain Oracle tables get no badge.
function sourceBadge(b) {
  if (b.source === 'sql') return 'SQL';
  if (b.source === 'derived') {
    const k = b.derivation_kind;
    if (k === 'aggregate') return 'agregat';
    if (k === 'filter') return 'filtre';
    if (k === 'calculated') return 'hesaplama';
    return 'türetilmiş';
  }
  return null;
}

function BasketTableRow({ item, onOpenDocs, onDeleteUpload }) {
  const filter = item.row_filter;
  const isUpload = (item.tid || '').startsWith('upload__');

  return (
    <div className="sources-table-wrap is-active">
      <button
        type="button"
        className="sources-table sources-table--readonly"
        onClick={onOpenDocs}
        title={item.catalog ? `${item.tid} — önizle`
          : item.isProduced ? `${item.tid} — kolonlar + önizleme` : item.tid}
        disabled={!item.catalog && !item.isProduced}
      >
        <div className="sources-table-info">
          <div className="sources-table-name">
            {item.name}
            {item.typeBadge && (
              <span className="sources-table-badge">{item.typeBadge}</span>
            )}
          </div>
          <div className="sources-table-desc">
            {item.schema}
            {item.catalog?.desc ? ` · ${item.catalog.desc}` : ''}
            {item.catalog?.rows ? ` · ${item.catalog.rows} satır` : ''}
          </div>
          {filter && (
            <div className="sources-table-filter">
              <Tag size={9} strokeWidth={2} />
              <span className="sources-table-filter-text">{filter}</span>
            </div>
          )}
        </div>
        {(item.catalog || item.isProduced) && (
          <span className="sources-table-eye-hint" title={item.isProduced ? 'Kolonlar + önizleme' : 'Önizle'}>
            <Eye size={12} strokeWidth={1.8} />
          </span>
        )}
      </button>
      {isUpload && (
        <button
          type="button"
          className="sources-upload-delete"
          onClick={(e) => {
            e.stopPropagation();
            // Recover upload id from `upload__<uploadId>__<sheet>`.
            const m = (item.tid || '').match(/^upload__([^_]+)__/);
            if (m) onDeleteUpload(m[1]);
          }}
          title="Yüklü dosyayı sil"
        >
          <Trash2 size={11} strokeWidth={1.8} />
        </button>
      )}
    </div>
  );
}
