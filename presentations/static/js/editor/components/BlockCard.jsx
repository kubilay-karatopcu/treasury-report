import useStore from '../lib/store.js';
import SectionHeader from '../blocks/SectionHeader.jsx';
import KpiBlock      from '../blocks/KpiBlock.jsx';
import BarChart      from '../blocks/BarChart.jsx';
import LineChart     from '../blocks/LineChart.jsx';
import AreaChart     from '../blocks/AreaChart.jsx';
import PieChart      from '../blocks/PieChart.jsx';
import Heatmap       from '../blocks/Heatmap.jsx';
import RadialBar     from '../blocks/RadialBar.jsx';
import DataTable     from '../blocks/DataTable.jsx';
import Narrative     from '../blocks/Narrative.jsx';

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
};

// Inline lock SVG — avoids pulling in a full icon lib for Phase 1.
function LockIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="2"
         strokeLinecap="round" strokeLinejoin="round" className="lock-icon" aria-label="Kilitli">
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
      <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
    </svg>
  );
}

export default function BlockCard({ block }) {
  const selectedBlockId = useStore((s) => s.selectedBlockId);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);
  const viewMode        = useStore((s) => s.viewMode);

  const Component = BLOCK_MAP[block.type];
  if (!Component) return null;

  // Section headers have no card wrapper — they act as visual dividers.
  if (block.type === 'section_header') {
    return <Component block={block} />;
  }

  const isSelected = selectedBlockId === block.id && viewMode === 'edit';

  return (
    <div
      className={`block-card${isSelected ? ' selected' : ''}`}
      onClick={
        viewMode === 'edit'
          ? () => setSelectedBlock(isSelected ? null : block.id)
          : undefined
      }
    >
      <div className="block-card-header">
        <span className="block-card-title">{block.title}</span>
        {block.locked && <LockIcon />}
      </div>
      <div className="block-card-body">
        <Component block={block} />
      </div>
    </div>
  );
}
