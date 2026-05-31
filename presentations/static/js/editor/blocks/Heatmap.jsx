import Chart from 'react-apexcharts';
import { heatmapOptions, normalizeLabels } from './chartHelpers.js';

export default function Heatmap({ block }) {
  const config = block.config || {};
  // Manifest schema uses ``x_axis`` but pre-Phase-7 library blocks still
  // ship with ``categories`` (Apex-style). Accept both so existing library
  // imports render without manual repair.
  const categories = normalizeLabels(config.x_axis ?? config.categories);
  // ApexCharts heatmap wants each series row as {x, y} pairs along the
  // shared category axis.
  const series = (config.series || []).map((s) => ({
    name: s.name || '',
    data: (s.values || []).map((v, i) => ({ x: categories[i] ?? String(i), y: v })),
  }));

  if (categories.length === 0 || series.length === 0) {
    return <div className="chart-empty">Grafik için veri yok.</div>;
  }

  const options = heatmapOptions({
    categories,
    series,
    height: 280,
    showValues: config.show_values !== false,
  });

  const remountKey = `${block.id}-${block.width || 'full'}`;
  return (
    <div className="chart-wrapper">
      <Chart key={remountKey} options={options} series={series} type="heatmap" height={280} />
    </div>
  );
}
