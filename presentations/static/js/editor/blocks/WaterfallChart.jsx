import Chart from 'react-apexcharts';
import { waterfallOptions, waterfallSeries, normalizeLabels } from './chartHelpers.js';

// Waterfall: adım deltalarından kümülatif köprü. SQL sözleşmesi:
//   col0 = adım etiketi, col1 = delta, col2 (ops.) = toplam bayrağı (1/0).
// config: { categories: [str], values: [num], totals?: [bool], unit? }
export default function WaterfallChart({ block }) {
  const config = block.config || {};
  const categories = normalizeLabels(config.categories);
  const values = config.values || [];

  if (categories.length === 0 || values.length === 0) {
    return <div className="chart-empty">Grafik için veri yok.</div>;
  }

  const series = waterfallSeries({
    categories,
    values,
    totals: config.totals || [],
  });
  const options = waterfallOptions({
    height: 280,
    unit: config.unit || '',
    showDataLabels: config.show_data_labels !== false,
    data: series[0].data,
  });

  const remountKey = `${block.id}-${block.width || 'full'}`;
  return (
    <div className="chart-wrapper">
      <Chart key={remountKey} options={options} series={series}
             type="rangeBar" height={280} />
    </div>
  );
}
