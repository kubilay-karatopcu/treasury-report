import { useState } from 'react';
import {
  Lock, Unlock, FileText, BarChart3, TrendingUp, Database,
  PieChart as PieIcon, Activity, Grid3x3, Table as TableIcon,
  Eye, AlertTriangle, ChevronUp, ChevronDown,
} from 'lucide-react';
import useStore, { findBlockPath } from '../lib/store.js';
import { fetchBlockSteps } from '../lib/api.js';
import SourceModal    from './SourceModal.jsx';
import SectionHeader  from '../blocks/SectionHeader.jsx';
import KpiBlock       from '../blocks/KpiBlock.jsx';
import BarChart       from '../blocks/BarChart.jsx';
import LineChart      from '../blocks/LineChart.jsx';
import ComboChart     from '../blocks/ComboChart.jsx';
import AreaChart      from '../blocks/AreaChart.jsx';
import PieChart       from '../blocks/PieChart.jsx';
import Heatmap        from '../blocks/Heatmap.jsx';
import RadialBar      from '../blocks/RadialBar.jsx';
import DataTable      from '../blocks/DataTable.jsx';
import Narrative      from '../blocks/Narrative.jsx';
import Carousel       from '../blocks/Carousel.jsx';
import Canvas         from '../blocks/Canvas.jsx';

const BLOCK_MAP = {
  section_header: SectionHeader,
  kpi:            KpiBlock,
  bar_chart:      BarChart,
  line_chart:     LineChart,
  combo_chart:    ComboChart,
  area_chart:     AreaChart,
  pie_chart:      PieChart,
  heatmap:        Heatmap,
  radial_bar:     RadialBar,
  data_table:     DataTable,
  narrative:      Narrative,
  carousel:       Carousel,
  canvas:         Canvas,
};

const TYPE_LABELS = {
  kpi:         'KPI',
  bar_chart:   'Bar Chart',
  line_chart:  'Line Chart',
  combo_chart: 'Combo (Bar+Line)',
  area_chart:  'Area Chart',
  pie_chart:   'Pie Chart',
  heatmap:     'Heatmap',
  radial_bar:  'Radial Bar',
  data_table:  'Tablo',
  narrative:   'Metin',
};

// Blocks that can have a data_source and thus a "Kaynakça" / "Tazele" button.
const DATA_BACKED_TYPES = new Set([
  'kpi', 'bar_chart', 'line_chart', 'combo_chart', 'area_chart',
  'pie_chart', 'heatmap', 'radial_bar', 'data_table',
]);

function TypeIcon({ type }) {
  const iconProps = { size: 12, strokeWidth: 2 };
  switch (type) {
    case 'kpi':        return <TrendingUp {...iconProps} />;
    case 'bar_chart':  return <BarChart3  {...iconProps} />;
    case 'line_chart': return <TrendingUp {...iconProps} />;
    case 'combo_chart': return <BarChart3 {...iconProps} />;
    case 'area_chart': return <Activity   {...iconProps} />;
    case 'pie_chart':  return <PieIcon    {...iconProps} />;
    case 'heatmap':    return <Grid3x3    {...iconProps} />;
    case 'radial_bar': return <Activity   {...iconProps} />;
    case 'data_table': return <TableIcon  {...iconProps} />;
    case 'narrative':  return <FileText   {...iconProps} />;
    default:           return <FileText   {...iconProps} />;
  }
}


// C3 — "Adımlar": bloğun kaynak türetme zincirini (leaf→root, her adımın SQL'i)
// blok altında aç-kapa panelde gösterir. Read-only; lazy fetch (ilk açılışta).
function StepsPanel({ blockId }) {
  const [open, setOpen] = useState(false);
  const [steps, setSteps] = useState(null);   // null=yüklenmedi, []=ara adım yok
  const [loading, setLoading] = useState(false);

  async function toggle(e) {
    e.stopPropagation();
    const next = !open;
    setOpen(next);
    if (next && steps === null && !loading) {
      setLoading(true);
      try { setSteps(await fetchBlockSteps(blockId)); }
      catch { setSteps([]); }
      finally { setLoading(false); }
    }
  }

  return (
    <div className="block-steps" onClick={(e) => e.stopPropagation()}>
      <button type="button" className="block-steps-toggle" onClick={toggle}>
        {open ? <ChevronUp size={11} strokeWidth={2} /> : <ChevronDown size={11} strokeWidth={2} />}
        Adımlar {open ? '(gizle)' : '(göster)'}
      </button>
      {open && (
        <div className="block-steps-body">
          {loading && <div className="block-steps-empty">Yükleniyor…</div>}
          {!loading && steps && steps.length === 0 && (
            <div className="block-steps-empty">Ara adım yok — doğrudan kaynak tablo.</div>
          )}
          {!loading && steps && steps.map((st, i) => (
            <div key={`${st.alias}-${i}`} className="block-step">
              <div className="block-step-head">
                <span className="block-step-num">{i + 1}</span>
                <span className="block-step-alias">{st.alias}</span>
                <span className="block-step-kind">{st.kind}</span>
                {st.sources && st.sources.length > 0 && (
                  <span className="block-step-src">← {st.sources.join(', ')}</span>
                )}
              </div>
              <pre className="block-step-sql">{st.sql}</pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


export default function BlockCard({ block }) {
  const selectedBlockId  = useStore((s) => s.selectedBlockId);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);
  const toggleLock       = useStore((s) => s.toggleLock);
  const refreshBlock     = useStore((s) => s.refreshBlock);
  const runBlockManualSql = useStore((s) => s.runBlockManualSql);
  const viewMode         = useStore((s) => s.viewMode);
  const flashingBlockIds = useStore((s) => s.flashingBlockIds);
  const mode             = useStore((s) => s.mode);
  const layoutEditMode   = useStore((s) => s.layoutEditMode);
  const moveBlock        = useStore((s) => s.moveBlock);
  // Phase 7 — concept compilation outcome for this block (after Güncelle).
  const conceptInfo      = useStore((s) => s.conceptStatus?.[block.id]);
  // Phase B — library cache freshness (only set when served via shared cache).
  const freshnessInfo    = useStore((s) => s.freshnessStatus?.[block.id]);
  // Move bounds — manifest değiştikçe yeniden hesaplanır
  const moveBounds = useStore((s) => {
    if (!s.manifest) return { canUp: false, canDown: false };
    const loc = findBlockPath(s.manifest, block.id);
    if (!loc) return { canUp: false, canDown: false };
    // parentPath/index generic — herhangi derinlik (canvas-in-carousel dahil).
    return { canUp: loc.index > 0, canDown: loc.index < (loc.siblings.length - 1) };
  });

  const [sourceOpen, setSourceOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState(null);

  const Component = BLOCK_MAP[block.type];
  if (!Component) return null;

  // Section headers render as visual dividers — no card wrapper.
  // Container'lar (carousel/canvas) kendi card chrome'unu yönetir — wrap bypass.
  if (block.type === 'section_header' || block.type === 'carousel' || block.type === 'canvas') {
    return <Component block={block} />;
  }

  const isSelected     = selectedBlockId === block.id;
  const isPresentation = viewMode === 'presentation';
  const isSnapshot     = mode === 'snapshot';
  const isFlashing     = Array.isArray(flashingBlockIds) && flashingBlockIds.includes(block.id);

  const hasDataSource = DATA_BACKED_TYPES.has(block.type) && !!block.data_source;
  const isTruncated   = !!block.data_source?.truncated;

  function handleClick(e) {
    if (isPresentation || isSnapshot) return;
    if (block.locked) return;
    e.stopPropagation();
    setSelectedBlock(block.id === selectedBlockId ? null : block.id);
  }

  function handleLockToggle(e) {
    e.stopPropagation();
    toggleLock(block.id);
  }

  function handleSourceOpen(e) {
    e.stopPropagation();
    setSourceOpen(true);
  }

  async function handleRefresh() {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshError(null);
    try {
      // Phase 6.5: block has `query` + `variables` shape → use the
      // variable-aware run-manual endpoint (resolves :binds, expands enum
      // lists, calls DataClient with proper query_params). The legacy
      // /block/<bid>/refresh path doesn't know how to bind variables and
      // crashes with `Parser Error at ":"` on DuckDB.
      const hasPhase65 = typeof block.query === 'string'
        && block.query.trim().length > 0
        && Array.isArray(block.variables);
      if (hasPhase65) {
        await runBlockManualSql(block.id, {
          query: block.query,
          variables: block.variables,
        });
      } else {
        await refreshBlock(block.id);
      }
    } catch (err) {
      setRefreshError(err.message);
    } finally {
      setRefreshing(false);
    }
  }

  const cardClass = [
    'block-card',
    isSelected   ? 'is-selected'   : '',
    block.locked ? 'is-locked'     : '',
    isFlashing   ? 'is-flashing'   : '',
    isSnapshot   ? 'is-snapshot'   : '',
    block.data_stale ? 'is-stale'  : '',
  ].filter(Boolean).join(' ');

  return (
    <>
      <div
        id={`block-${block.id}`}
        data-block-id={block.id}
        className={cardClass}
        onClick={handleClick}
      >
        <div className="block-strip">
          <span className="block-type-icon"><TypeIcon type={block.type} /></span>
          <span className="block-type-label">
            {TYPE_LABELS[block.type] || block.type}
          </span>
          {block.locked && <span className="block-locked-pill">Locked</span>}
          {block.data_stale && (
            <span
              className="block-stale-pill"
              title="Tip değişti veya değişkenler güncellendi — Properties paneli'nden Çalıştır."
            >
              <AlertTriangle size={10} strokeWidth={2.2} />
              veri eski
            </span>
          )}
          {isTruncated && (
            <span
              className="block-trunc-pill"
              title={block.data_source?.reason || 'Sonuç ilk N satıra kesildi'}
            >
              <AlertTriangle size={10} strokeWidth={2.2} />
              kesildi
            </span>
          )}
          {conceptInfo && conceptInfo.injected && conceptInfo.applied.length > 0 && (
            <span
              className="block-concept-pill"
              title={'Uygulanan concept filtreleri: '
                + conceptInfo.applied.map((a) => a.concept).join(', ')}
            >
              <Database size={10} strokeWidth={2.2} />
              {conceptInfo.applied.length} concept filtresi
            </span>
          )}
          {conceptInfo && conceptInfo.blind.length > 0 && (
            <span
              className="block-blind-pill"
              title={'Bu blok şu kavram(lar)ı bilmiyor — filtre uygulanmadı: '
                + conceptInfo.blind.join(', ')
                + '. Kaynak tabloya binding ekleyin (/concepts/review).'}
            >
              <AlertTriangle size={10} strokeWidth={2.2} />
              filtre uygulanmadı: {conceptInfo.blind.join(', ')}
            </span>
          )}
          {freshnessInfo && (
            <span
              className={`block-freshness-pill is-${freshnessInfo.freshness || 'unknown'}${freshnessInfo.refreshing ? ' is-refreshing' : ''}`}
              title={(() => {
                const ago = freshnessInfo.ageSeconds;
                let agoStr = '?';
                if (typeof ago === 'number') {
                  if (ago < 60) agoStr = `${Math.round(ago)} sn önce`;
                  else if (ago < 3600) agoStr = `${Math.round(ago / 60)} dk önce`;
                  else agoStr = `${(ago / 3600).toFixed(1)} sa önce`;
                }
                const head = freshnessInfo.freshness === 'fresh'
                  ? 'Veri taze'
                  : freshnessInfo.freshness === 'stale'
                  ? 'Veri bayat (eski sonuç gösteriliyor)'
                  : 'Veri eski';
                const refreshNote = freshnessInfo.refreshing
                  ? '\nArka planda yeni veri çekiliyor…'
                  : '';
                return `${head} · ${agoStr}\nKütüphane önbelleği${refreshNote}`;
              })()}
            >
              <span className="block-freshness-dot" />
              {freshnessInfo.freshness === 'fresh'
                ? 'taze'
                : freshnessInfo.freshness === 'stale'
                ? (freshnessInfo.refreshing ? 'yenileniyor' : 'bayat')
                : 'eski'}
            </span>
          )}
          <span className="block-strip-spacer" />

          {isSelected && layoutEditMode && !isPresentation && !isSnapshot && (
            <>
              <button
                type="button"
                className="block-strip-btn block-move-btn"
                onClick={(e) => { e.stopPropagation(); moveBlock(block.id, -1); }}
                disabled={!moveBounds.canUp}
                title="Yukarı taşı"
              >
                <ChevronUp size={13} strokeWidth={2} />
              </button>
              <button
                type="button"
                className="block-strip-btn block-move-btn"
                onClick={(e) => { e.stopPropagation(); moveBlock(block.id, +1); }}
                disabled={!moveBounds.canDown}
                title="Aşağı taşı"
              >
                <ChevronDown size={13} strokeWidth={2} />
              </button>
            </>
          )}

          {hasDataSource && (
            <button
              type="button"
              className="block-strip-btn"
              onClick={handleSourceOpen}
              title="Kaynak SQL ve veri örneği"
            >
              <Eye size={12} strokeWidth={1.8} />
            </button>
          )}

          {/* Manual refresh button removed by request — kullanıcılar arayüzden
              query çalıştıramazlar. Veri tazeliği refresh_policy ile yönetilir
              (lazy_ttl, scheduled). */}

          <button
            type="button"
            className={`block-lock-btn${block.locked ? ' is-locked' : ''}`}
            onClick={handleLockToggle}
            title={block.locked ? 'Kilidi kaldır' : 'Kilitle (LLM değiştiremez)'}
          >
            {block.locked
              ? <Lock   size={12} strokeWidth={2} />
              : <Unlock size={12} strokeWidth={1.5} />}
          </button>
        </div>

        {block.title && <h3 className="block-title">{block.title}</h3>}

        <div className="block-body">
          <Component block={block} />
        </div>

        {block.source && !hasDataSource && (
          // Legacy `source` field for non-DB-backed narrative blocks.
          <div className="block-footer">
            <Database size={10} strokeWidth={1.5} />
            <span className="block-footer-text">{block.source}</span>
          </div>
        )}

        {hasDataSource && (
          <button
            type="button"
            className="block-footer block-footer--button"
            onClick={handleSourceOpen}
            title="Kaynak SQL ve veri örneği"
          >
            <Database size={10} strokeWidth={1.5} />
            <span className="block-footer-text">
              {block.data_source.row_count != null
                ? `${block.data_source.row_count.toLocaleString('tr-TR')} satır`
                : 'Kaynak veriyi gör'}
              {isTruncated && ' · ilk N kesildi'}
            </span>
          </button>
        )}

        {/* C3 — bloğun kaynak türetme zinciri (ara adımlar) — aç-kapa, blok altı. */}
        {hasDataSource && !isSnapshot && <StepsPanel blockId={block.id} />}
      </div>

      <SourceModal
        open={sourceOpen}
        onClose={() => setSourceOpen(false)}
        block={block}
        onRefresh={hasDataSource ? handleRefresh : undefined}
        refreshing={refreshing}
      />

      {/* Tiny transient error toast — disappears on next interaction. */}
      {refreshError && sourceOpen && (
        <div className="src-refresh-err" role="alert">{refreshError}</div>
      )}
    </>
  );
}