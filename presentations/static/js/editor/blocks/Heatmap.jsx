import { AgCharts } from 'ag-charts-react';
import { heatmapOptions, normalizeLabels } from './chartHelpers.js';

export default function Heatmap({ block }) {
  const config = block.config || {};
  // Manifest schema uses ``x_axis`` but pre-Phase-7 library blocks still
  // ship with ``categories`` (Apex-style). Accept both so existing library
  // imports render without manual repair.
  const categories = normalizeLabels(config.x_axis ?? config.categories);
  // Manifest series shape stays Apex-style ({name, values}); the helper
  // melts it into AG's long-format heatmap rows.
  const series = (config.series || []).map((s) => ({
    name: s.name || '',
    values: s.values || [],
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
      <AgCharts key={remountKey} options={options} />
    </div>
  );
}
