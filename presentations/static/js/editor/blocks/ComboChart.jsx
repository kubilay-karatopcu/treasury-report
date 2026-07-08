import Chart from 'react-apexcharts';
import { comboChartOptions, normalizeLabels } from './chartHelpers.js';
import { comboSeriesDefaults } from '../lib/store.js';

// Combo (dual-axis) chart: single query, column-split. Each series carries a
// user-set kind (bar/line) + axis (left/right), edited in the properties panel.
export default function ComboChart({ block }) {
  const config = block.config || {};
  // Eski manifestlerde tip değişimi config'i taşımamış olabilir — kategori
  // ekseni x_axis'te kalmışsa ona düş (yoksa "veri yok" görünür).
  const rawCats = (config.categories && config.categories.length)
    ? config.categories
    : (config.x_axis || []);
  const categories = normalizeLabels(rawCats);
  const rawSeries = (config.series || []).map((s, i) => ({
    name:   s.name || '',
    values: s.values || [],
    kind:   s.kind === 'line' || s.kind === 'bar' ? s.kind : comboSeriesDefaults(i).kind,
    axis:   s.axis === 'right' || s.axis === 'left' ? s.axis : comboSeriesDefaults(i).axis,
  }));

  if (categories.length === 0 || rawSeries.length === 0) {
    return <div className="chart-empty">Grafik için veri yok.</div>;
  }

  // Resolve unique, non-empty names so ApexCharts can bind each series to its
  // y-axis by `seriesName` — duplicate or empty names would mis-bind the
  // left/right scales. Used for both the options and the data series.
  const seen = {};
  const named = rawSeries.map((s, i) => {
    let name = (s.name || '').trim() || `Seri ${i + 1}`;
    seen[name] = (seen[name] || 0) + 1;
    if (seen[name] > 1) name = `${name} (${seen[name]})`;
    return { ...s, name };
  });

  const options = comboChartOptions({
    categories,
    series: named,
    height:      260,
    leftTitle:   config.left_axis_title || '',
    rightTitle:  config.right_axis_title || '',
    curve:       config.curve || 'smooth',
    strokeWidth: typeof config.stroke_width === 'number' ? config.stroke_width : 2,
    showMarkers: !!config.show_markers,
    stacked:        !!config.stacked,
    showDataLabels: !!config.show_data_labels,
  });

  // ApexCharts mixed chart: each series declares its own type (bar/line).
  const series = named.map((s) => ({ name: s.name, type: s.kind, data: s.values }));

  const remountKey = `${block.id}-${block.width || 'full'}`;
  return (
    <div className="chart-wrapper">
      <Chart key={remountKey} options={options} series={series} type="line" height={260} />
    </div>
  );
}
