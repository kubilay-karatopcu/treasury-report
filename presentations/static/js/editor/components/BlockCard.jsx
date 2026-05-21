import { useState } from 'react';
import {
  Lock, Unlock, FileText, BarChart3, TrendingUp, Database,
  PieChart as PieIcon, Activity, Grid3x3, Table as TableIcon,
  Eye, AlertTriangle, ChevronUp, ChevronDown,
} from 'lucide-react';
import useStore, { findBlockPath } from '../lib/store.js';
import SourceModal    from './SourceModal.jsx';
import SectionHeader  from '../blocks/SectionHeader.jsx';
import KpiBlock       from '../blocks/KpiBlock.jsx';
import BarChart       from '../blocks/BarChart.jsx';
import LineChart      from '../blocks/LineChart.jsx';
import AreaChart      from '../blocks/AreaChart.jsx';
import PieChart       from '../blocks/PieChart.jsx';
import Heatmap        from '../blocks/Heatmap.jsx';
import RadialBar      from '../blocks/RadialBar.jsx';
import DataTable      from '../blocks/DataTable.jsx';
import Narrative      from '../blocks/Narrative.jsx';
import Carousel       from '../blocks/Carousel.jsx';

const BLOCK_MAP = {
  section_header: SectionHeader,
  kpi:            KpiBlock,
  bar_chart:      BarChart,
  line_chart:     LineChart,
  area_chart:     AreaChart,
  pie_chart:      PieChart,
  heatmap:        Heatmap,
  radial_bar:     RadialBar,
  data_table:     DataTable,
  narrative:      Narrative,
  carousel:       Carousel,
};

const TYPE_LABELS = {
  kpi:         'KPI',
  bar_chart:   'Bar Chart',
  line_chart:  'Line Chart',
  area_chart:  'Area Chart',
  pie_chart:   'Pie Chart',
  heatmap:     'Heatmap',
  radial_bar:  'Radial Bar',
  data_table:  'Tablo',
  narrative:   'Metin',
};

// Blocks that can have a data_source and thus a "Kaynakça" / "Tazele" button.
const DATA_BACKED_TYPES = new Set([
  'kpi', 'bar_chart', 'line_chart', 'area_chart',
  'pie_chart', 'heatmap', 'radial_bar', 'data_table',
]);

function TypeIcon({ type }) {
  const iconProps = { size: 12, strokeWidth: 2 };
  switch (type) {
    case 'kpi':        return <TrendingUp {...iconProps} />;
    case 'bar_chart':  return <BarChart3  {...iconProps} />;
    case 'line_chart': return <TrendingUp {...iconProps} />;
    case 'area_chart': return <Activity   {...iconProps} />;
    case 'pie_chart':  return <PieIcon    {...iconProps} />;
    case 'heatmap':    return <Grid3x3    {...iconProps} />;
    case 'radial_bar': return <Activity   {...iconProps} />;
    case 'data_table': return <TableIcon  {...iconProps} />;
    case 'narrative':  return <FileText   {...iconProps} />;
    default:           return <FileText   {...iconProps} />;
  }
}


export default function BlockCard({ block }) {
  const selectedBlockId  = useStore((s) => s.selectedBlockId);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);
  const toggleLock       = useStore((s) => s.toggleLock);
  const refreshBlock     = useStore((s) => s.refreshBlock);
  const viewMode         = useStore((s) => s.viewMode);
  const flashingBlockIds = useStore((s) => s.flashingBlockIds);
  const mode             = useStore((s) => s.mode);
  const layoutEditMode   = useStore((s) => s.layoutEditMode);
  const moveBlock        = useStore((s) => s.moveBlock);
  // Move bounds — manifest değiştikçe yeniden hesaplanır
  const moveBounds = useStore((s) => {
    if (!s.manifest) return { canUp: false, canDown: false };
    const loc = findBlockPath(s.manifest, block.id);
    if (!loc) return { canUp: false, canDown: false };
    let arr, idx;
    if (loc.slideIdx != null)      { arr = loc.child.children || [];   idx = loc.slideIdx; }
    else if (loc.childIdx != null) { arr = loc.section.children || []; idx = loc.childIdx; }
    else                            { arr = s.manifest.blocks || [];    idx = loc.sectionIdx; }
    return { canUp: idx > 0, canDown: idx < arr.length - 1 };
  });

  const [sourceOpen, setSourceOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState(null);

  const Component = BLOCK_MAP[block.type];
  if (!Component) return null;

  // Section headers render as visual dividers — no card wrapper.
  // Carousel kendi card chrome'unu yönetir — BlockCard wrap'i bypass.
  if (block.type === 'section_header' || block.type === 'carousel') {
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
      await refreshBlock(block.id);
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