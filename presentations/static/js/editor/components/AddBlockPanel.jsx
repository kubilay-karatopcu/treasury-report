import { useEffect, useState, useMemo } from 'react';
import {
  X, Plus, Search, Layers,
  TrendingUp, BarChart3, Activity, PieChart as PieIcon,
  Grid3x3, Table as TableIcon, FileText,
} from 'lucide-react';
import useStore from '../lib/store.js';
import { fetchBlockTemplates, fetchBlockTemplate } from '../lib/api.js';

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

// Tek kütüphane = BLOCK_STORE (Phase 6.5). Eski LIBRARY_STORE ("Library" sekmesi)
// kaldırıldı; tüm kütüphane gezinme/ekleme buradan (Kütüphane sekmesi) yapılır.
export default function AddBlockPanel({ width, onResizeStart }) {
  const panel             = useStore((s) => s.addBlockPanel);
  const close             = useStore((s) => s.closeAddBlockPanel);
  const addChildBlock     = useStore((s) => s.addChildBlock);
  const addTemplateToSec  = useStore((s) => s.addBlockTemplateToSection);

  const [tab, setTab] = useState('base');
  const [items, setItems]   = useState([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery]   = useState('');

  // Kütüphane (BLOCK_STORE) bloklarını yükle (tab açılınca + arama değişince)
  useEffect(() => {
    if (tab !== 'library') {
      setItems([]);
      return;
    }
    setLoading(true);
    fetchBlockTemplates({ q: query })
      .then(setItems)
      .finally(() => setLoading(false));
  }, [tab, query]);

  if (!panel) return null;
  const sectionId = panel.sectionId;

  function handleAddBase(type) {
    addChildBlock(sectionId, type);
    close();
  }

  async function handleAddTemplate(team, id, version) {
    try {
      const { block } = await fetchBlockTemplate(team, id, version);
      addTemplateToSec(sectionId, block, { team, id: block.id || id, version });
      close();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  return (
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
        >Kütüphane{tab === 'library' && items.length > 0 ? ` (${items.length})` : ''}</button>
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
                  placeholder="Kütüphane ara (başlık, açıklama, tag)…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
            </div>
            {loading && <div className="lib-loading">Yükleniyor…</div>}
            {!loading && items.length === 0 && (
              <div className="lib-empty">
                Kütüphanede blok yok. Sunum içinde blok oluşturup
                Properties &rsaquo; Kütüphane &rsaquo; <strong>Kütüphaneye
                kaydet</strong> ile ekleyebilirsin.
              </div>
            )}
            <div className="lib-grid">
              {items.map((m) => (
                <article key={`${m.team}-${m.id}`} className="lib-card">
                  <div className="lib-card-head">
                    <span className="lib-card-icon"><TypeIcon type={m.visualization_type} /></span>
                    <span className="lib-card-title" title={m.title}>{m.title}</span>
                  </div>
                  <div className="lib-card-meta" style={{fontSize:'10px',color:'#94a3b8'}}>
                    {m.team} · v{m.version} · {m.visualization_type}
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
                  <div className="lib-card-actions">
                    <button
                      type="button"
                      className="lib-btn lib-btn--add"
                      onClick={() => handleAddTemplate(m.team, m.id, m.version)}
                      title="Bu bloğu sunuma ekle (auto-binding ile)"
                    >Ekle</button>
                  </div>
                </article>
              ))}
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
