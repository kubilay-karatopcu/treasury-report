import { AgCharts } from 'ag-charts-react';
import { pieChartOptions, normalizeLabels } from './chartHelpers.js';

export default function PieChart({ block }) {
  const config = block.config || {};
  const labels = normalizeLabels(config.labels);
  const values = config.values || [];
  const donut = !!config.donut;

  if (labels.length === 0 || values.length === 0) {
    return <div className="chart-empty">Grafik için veri yok.</div>;
  }

  const options = pieChartOptions({
    labels,
    values,
    donut,
    height: 260,
    legendPosition: config.legend_position || 'right',
    showDataLabels: config.show_data_labels !== false,
  });

  const remountKey = `${block.id}-${block.width || 'full'}-${donut ? 'd' : 'p'}`;
  return (
    <div className="chart-wrapper">
      <AgCharts key={remountKey} options={options} />
    </div>
  );
}
