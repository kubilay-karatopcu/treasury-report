import Chart from 'react-apexcharts';
import { scatterOptions, scatterSeries } from './chartHelpers.js';

// Bubble/scatter: her satır bir nokta. SQL sözleşmesi:
//   col0 = nokta adı, col1 = x, col2 = y, col3 (ops.) = boyut.
// config: { points: [{name, x, y, size?}], x_title?, y_title? }
export default function ScatterChart({ block }) {
  const config = block.config || {};
  const points = Array.isArray(config.points) ? config.points : [];

  if (points.length === 0) {
    return <div className="chart-empty">Grafik için veri yok.</div>;
  }

  const options = scatterOptions({
    height: 300,
    xTitle: config.x_title || '',
    yTitle: config.y_title || '',
    showDataLabels: !!config.show_data_labels,
  });

  const remountKey = `${block.id}-${block.width || 'full'}`;
  return (
    <div className="chart-wrapper">
      <Chart key={remountKey} options={options} series={scatterSeries(points)}
             type="bubble" height={300} />
    </div>
  );
}
