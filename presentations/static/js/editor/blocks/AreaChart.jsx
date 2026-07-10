import Chart from 'react-apexcharts';
import { areaChartOptions, normalizeLabels } from './chartHelpers.js';

export default function AreaChart({ block }) {
  const config = block.config || {};
  // Accept legacy ``categories`` alongside the canonical ``x_axis`` so
  // older library imports render without manual repair.
  const categories = normalizeLabels(config.x_axis ?? config.categories);
  const series = (config.series || []).map((s) => ({
    name: s.name || '',
    data: s.values || [],
  }));

  if (categories.length === 0 || series.length === 0) {
    return <div className="chart-empty">Grafik için veri yok.</div>;
  }

  const options = areaChartOptions({
    categories,
    series,
    height: 260,
    curve:        config.curve || 'smooth',
    strokeWidth:  typeof config.stroke_width === 'number' ? config.stroke_width : 2,
    showMarkers:  !!config.show_markers,
    fillOpacity:  typeof config.fill_opacity === 'number' ? config.fill_opacity : 0.45,
    refLines:     config.ref_lines,
  });

  const remountKey = `${block.id}-${block.width || 'full'}`;
  return (
    <div className="chart-wrapper">
      <Chart key={remountKey} options={options} series={series} type="area" height={260} />
    </div>
  );
}
