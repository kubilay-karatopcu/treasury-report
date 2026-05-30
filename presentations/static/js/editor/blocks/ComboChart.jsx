import { AgCharts } from 'ag-charts-react';
import { comboChartOptions, normalizeLabels } from './chartHelpers.js';

// Combo (dual-axis) chart: single query, column-split. Each series carries a
// user-set kind (bar/line) + axis (left/right), edited in the properties panel.
export default function ComboChart({ block }) {
  const config = block.config || {};
  const categories = normalizeLabels(config.categories);
  const series = (config.series || []).map((s) => ({
    name:   s.name || '',
    values: s.values || [],
    kind:   s.kind === 'line' ? 'line' : 'bar',
    axis:   s.axis === 'right' ? 'right' : 'left',
  }));

  if (categories.length === 0 || series.length === 0) {
    return <div className="chart-empty">Grafik için veri yok.</div>;
  }

  const options = comboChartOptions({
    categories,
    series,
    height:      260,
    leftTitle:   config.left_axis_title || '',
    rightTitle:  config.right_axis_title || '',
    curve:       config.curve || 'smooth',
    strokeWidth: typeof config.stroke_width === 'number' ? config.stroke_width : 2,
    showMarkers: !!config.show_markers,
    stacked:        !!config.stacked,
    showDataLabels: !!config.show_data_labels,
  });

  const remountKey = `${block.id}-${block.width || 'full'}`;
  return (
    <div className="chart-wrapper">
      <AgCharts key={remountKey} options={options} />
    </div>
  );
}
