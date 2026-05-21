import { useEffect, useState, useMemo } from 'react';
import {
  X, Plus, Search, Eye, Tag, Database, Layers,
  TrendingUp, BarChart3, Activity, PieChart as PieIcon,
  Grid3x3, Table as TableIcon, FileText,
} from 'lucide-react';
import useStore from '../lib/store.js';
import { fetchLibraryBlocks, fetchLibraryBlock } from '../lib/api.js';

const BASE_BLOCKS = [
  { type: 'kpi',        label: 'KPI',         desc: 'Tek satır tek sayı — büyük göstergeler için' },
  { type: 'bar_chart',  label: 'Bar Chart',   desc: 'Kategori karşılaştırma' },
  { type: 'line_chart', label: 'Line Chart',  desc: 'Zaman serisi / trend' },
  { type: 'area_chart', label: 'Area Chart',  desc: 'Trend + hacim hissi' },
  { type: 'pie_chart',  label: 'Pie Chart',   desc: 'Dağılım / pay' },
  { type: 'heatmap',    label: 'Heatmap',     desc: 'Matris (2D yoğunluk)' },
  { type: 'radial_bar', label: 'Radial Bar',  desc: 'Hedef / yüzde göstergesi' },
  { type: 'data_table', label: 'Tablo',       desc: 'Veri tablosu (AG Grid)' },
  { type: 'narrative',  label: 'Metin',       desc: 'Markdown narrative — yorum/açıklama' },
  { type: 'carousel',   label: 'Carousel',    desc: 'Aynı yerde birden çok blok (toggleable)' },
];

function TypeIcon({ type, size = 14 }) {
  const p = { size, strokeWidth: 1.8 };
  switch (type) {
    case 'kpi':        return <TrendingUp {...p} />;
    case 'bar_chart':  return <BarChart3  {...p} />;
    case 'line_chart': return <TrendingUp {...p} />;
    case 'area_chart': return <Activity   {...p} />;
    case 'pie_chart':  return <PieIcon    {...p} />;
    case 'heatmap':    return <Grid3x3    {...p} />;
    case 'radial_bar': return <Activity   {...p} />;
    case 'data_table': return <TableIcon  {...p} />;
    case 'narrative':  return <FileText   {...p} />;
    case 'carousel':   return <Layers     {...p} />;
    default:           return <FileText   {...p} />;
  }
}

export default function AddBlockPanel({ width, onResizeStart }) {
  const panel             = useStore((s) => s.addBlockPanel);
  const close             = useStore((s) => s.closeAddBlockPanel);
  const addChildBlock     = useStore((s) => s.addChildBlock);
  const addLibraryToSec   = useStore((s) => s.addLibraryBlockToSection);

  const [tab, setTab] = useState('base');
  const [items, setItems]   = useState([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery]   = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [detail, setDetail] = useState(null);  // {block, meta} for modal

  // Library blokları yükle (tab açılınca + filter değişince)
  useEffect(() => {
    if (tab !== 'library') return;
    setLoading(true);
    fetchLibraryBlocks({ q: query, tag: tagFilter })
      .then(setItems)
      .finally(() => setLoading(false));
  }, [tab, query, tagFilter]);

  // Tüm tag'leri unique olarak topla (tag dropdown için)
  const allTags = useMemo(() => {
    const s = new Set();
    for (const it of items) for (const t of (it.tags || [])) s.add(t);
    return Array.from(s).sort();
  }, [items]);

  if (!panel) return null;
  const sectionId = panel.sectionId;

  function handleAddBase(type) {
    addChildBlock(sectionId, type);
    close();
  }

  async function handleAddLibrary(libraryId) {
    try {
      const { block } = await fetchLibraryBlock(libraryId);
      addLibraryToSec(sectionId, block);
      close();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function showDetail(libraryId) {
    try {
      const data = await fetchLibraryBlock(libraryId);
      setDetail(data);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  return (
    <>
      <aside className="add-block-panel" style={width ? { width } : undefined}>
        {onResizeStart && (
          <div className="resize-handle resize-handle--left" onMouseDown={onResizeStart} />
        )}
        <header className="add-block-panel__header">
          <div className="add-block-panel__title">
            <Plus size={14} strokeWidth={2} />
            <span>Blok Ekle</span>
          </div>
          <button type="button" className="props-close-btn" onClick={close} title="Kapat">
            <X size={16} strokeWidth={2} />
          </button>
        </header>

        <div className="add-block-tabs" role="tablist">
          <button
            type="button"
            className={`add-block-tab${tab === 'base' ? ' is-active' : ''}`}
            onClick={() => setTab('base')}
          >Base ({BASE_BLOCKS.length})</button>
          <button
            type="button"
            className={`add-block-tab${tab === 'library' ? ' is-active' : ''}`}
            onClick={() => setTab('library')}
          >Library{items.length > 0 ? ` (${items.length})` : ''}</button>
        </div>

        <div className="add-block-panel__body ts-scroll">
          {tab === 'base' && (
            <div className="lib-grid">
              {BASE_BLOCKS.map((b) => (
                <article key={b.type} className="lib-card">
                  <div className="lib-card-head">
                    <span className="lib-card-icon"><TypeIcon type={b.type} /></span>
                    <span className="lib-card-title">{b.label}</span>
                  </div>
                  <p className="lib-card-desc">{b.desc}</p>
                  <div className="lib-card-actions">
                    <button
                      type="button"
                      className="lib-btn lib-btn--add"
                      onClick={() => handleAddBase(b.type)}
                    >Ekle</button>
                  </div>
                </article>
              ))}
            </div>
          )}

          {tab === 'library' && (
            <>
              <div className="lib-filters">
                <div className="lib-search">
                  <Search size={12} strokeWidth={1.8} className="lib-search-icon" />
                  <input
                    type="text"
                    className="lib-search-input"
                    placeholder="İsim veya açıklama ara…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </div>
                {allTags.length > 0 && (
                  <select
                    className="lib-tag-select"
                    value={tagFilter}
                    onChange={(e) => setTagFilter(e.target.value)}
                  >
                    <option value="">Tüm tag'ler</option>
                    {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                )}
              </div>

              {loading && <div className="lib-loading">Yükleniyor…</div>}
              {!loading && items.length === 0 && (
                <div className="lib-empty">
                  Library'de blok yok. Bir bloğun properties panelinde
                  "Blok kütüphanesine kaydet" ile ekleyebilirsin.
                </div>
              )}

              <div className="lib-grid">
                {items.map((m) => (
                  <article key={m.library_id} className="lib-card">
                    <div className="lib-card-head">
                      <span className="lib-card-icon"><TypeIcon type={m.block_type} /></span>
                      <span className="lib-card-title" title={m.name}>{m.name}</span>
                    </div>
                    {m.description && (
                      <p className="lib-card-desc" title={m.description}>{m.description}</p>
                    )}
                    {(m.tags || []).length > 0 && (
                      <div className="lib-card-tags">
                        {m.tags.map((t) => (
                          <span key={t} className="lib-tag-chip">{t}</span>
                        ))}
                      </div>
                    )}
                    {(m.used_tables || []).length > 0 && (
                      <div className="lib-card-tables" title={m.used_tables.join(', ')}>
                        <Database size={10} strokeWidth={1.8} />
                        <span>{m.used_tables.join(', ')}</span>
                      </div>
                    )}
                    <div className="lib-card-actions">
                      <button
                        type="button"
                        className="lib-btn lib-btn--detail"
                        onClick={() => showDetail(m.library_id)}
                        title="Detay"
                      >
                        <Eye size={12} strokeWidth={1.8} />
                      </button>
                      <button
                        type="button"
                        className="lib-btn lib-btn--add"
                        onClick={() => handleAddLibrary(m.library_id)}
                      >Ekle</button>
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
        </div>
      </aside>

      {detail && (
        <BlockDetailModal data={detail} onClose={() => setDetail(null)} />
      )}
    </>
  );
}


function BlockDetailModal({ data, onClose }) {
  const meta = data.meta || {};
  const block = data.block || {};
  return (
    <div className="save-modal-backdrop" onClick={onClose}>
      <div className="save-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 640 }}>
        <div className="save-modal-header">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <TypeIcon type={meta.block_type} size={16} />
            {meta.name}
          </h3>
          <button className="save-modal-close" onClick={onClose} aria-label="Kapat">
            <X size={16} strokeWidth={2} />
          </button>
        </div>
        <div className="save-modal-body">
          {meta.description && (
            <p className="save-tab-desc">{meta.description}</p>
          )}
          <div className="lib-detail-meta">
            <div><strong>Tip:</strong> <code>{meta.block_type}</code></div>
            {meta.tags?.length > 0 && (
              <div>
                <strong>Tag'ler:</strong>{' '}
                {meta.tags.map((t) => <span key={t} className="lib-tag-chip">{t}</span>)}
              </div>
            )}
            {meta.used_tables?.length > 0 && (
              <div>
                <strong>Tablolar:</strong>{' '}
                <code>{meta.used_tables.join(', ')}</code>
              </div>
            )}
            {meta.owner_id && (
              <div><strong>Sahibi:</strong> {meta.owner_id} ({meta.owner_department})</div>
            )}
          </div>
          {block.data_source?.original_sql && (
            <details className="lib-detail-sql">
              <summary>SQL</summary>
              <pre><code>{block.data_source.original_sql}</code></pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
