import { AgCharts } from 'ag-charts-react';
import { barChartOptions, normalizeLabels } from './chartHelpers.js';

export default function BarChart({ block }) {
  const config = block.config || {};
  const categories = normalizeLabels(config.categories);
  const series = (config.series || []).map((s) => ({
    name: s.name || '',
    values: s.values || [],
  }));

  if (categories.length === 0 || series.length === 0) {
    return <div className="chart-empty">Grafik için veri yok.</div>;
  }

  const options = barChartOptions({
    categories,
    series,
    height: 260,
    stacked:        !!config.stacked,
    horizontal:     !!config.horizontal,
    showDataLabels: !!config.show_data_labels,
    distributed:    !!config.distributed,
    colors:         Array.isArray(config.colors) ? config.colors : undefined,
  });

  // AG Charts reflows automatically when its container resizes; the
  // remountKey is kept only so width changes (CSS-grid columns) trigger a
  // full re-init when stacked/horizontal flags also flip mid-edit.
  const remountKey = `${block.id}-${block.width || 'full'}`;
  return (
    <div className="chart-wrapper">
      <AgCharts key={remountKey} options={options} />
    </div>
  );
}
