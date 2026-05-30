import { AgCharts } from 'ag-charts-react';
import { lineChartOptions, normalizeLabels } from './chartHelpers.js';

export default function LineChart({ block }) {
  const config = block.config || {};
  // Accept legacy ``categories`` (Apex-era library blocks) as well as the
  // canonical ``x_axis`` so reused library imports just work.
  const categories = normalizeLabels(config.x_axis ?? config.categories);
  const series = (config.series || []).map((s) => ({
    name: s.name || '',
    values: s.values || [],
  }));

  if (categories.length === 0 || series.length === 0) {
    return <div className="chart-empty">Grafik için veri yok.</div>;
  }

  const options = lineChartOptions({
    categories,
    series,
    height: 260,
    curve:        config.curve || 'smooth',
    strokeWidth:  typeof config.stroke_width === 'number' ? config.stroke_width : 2,
    showMarkers:  !!config.show_markers,
  });

  const remountKey = `${block.id}-${block.width || 'full'}`;
  return (
    <div className="chart-wrapper">
      <AgCharts key={remountKey} options={options} />
    </div>
  );
}
