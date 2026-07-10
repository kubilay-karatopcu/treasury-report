import Chart from 'react-apexcharts';
import { barChartOptions, normalizeLabels } from './chartHelpers.js';

export default function BarChart({ block }) {
  const config = block.config || {};
  const categories = normalizeLabels(config.categories);
  const series = (config.series || []).map((s) => ({
    name: s.name || '',
    data: s.values || [],
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
    borderRadius:   typeof config.border_radius === 'number' ? config.border_radius : 4,
    distributed:    !!config.distributed,
    colors:         Array.isArray(config.colors) ? config.colors : undefined,
    refLines:       config.ref_lines,
  });

  // key forces ApexCharts to re-measure when the user changes width
  // (CSS-grid resizes are flaky for SVG canvases).
  const remountKey = `${block.id}-${block.width || 'full'}`;
  return (
    <div className="chart-wrapper">
      <Chart key={remountKey} options={options} series={series} type="bar" height={260} />
    </div>
  );
}
