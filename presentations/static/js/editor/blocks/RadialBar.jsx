import Chart from 'react-apexcharts';
import { radialBarOptions } from './chartHelpers.js';

export default function RadialBar({ block }) {
  const config = block.config || {};
  const value = typeof config.value === 'number' ? config.value : null;

  if (value == null) {
    return <div className="chart-empty">Grafik için veri yok.</div>;
  }

  const max   = typeof config.max   === 'number' ? config.max   : 100;
  const label = config.label || '';
  const options = radialBarOptions({ value, max, label, height: 260 });

  const remountKey = `${block.id}-${block.width || 'full'}`;
  return (
    <div className="chart-wrapper">
      <Chart key={remountKey} options={options} series={options.series} type="radialBar" height={260} />
    </div>
  );
}
