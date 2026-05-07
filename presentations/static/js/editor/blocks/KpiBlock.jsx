export default function KpiBlock({ block }) {
  const { value, unit, delta, delta_label, period } = block.config;

  const hasDelta = typeof delta === 'number' && delta !== 0;
  const hasLabel = delta_label && String(delta_label).trim() !== '';
  const positive = typeof delta === 'number' ? delta >= 0 : true;
  const arrow = positive ? '▲' : '▼';

  return (
    <div className="kpi-block">
      {period && <div className="kpi-period">{period}</div>}
      <div className="kpi-value">
        {value}
        {unit && <span className="kpi-unit">{unit}</span>}
      </div>
      {(hasDelta || hasLabel) && (
        <div className={`kpi-delta ${positive ? 'positive' : 'negative'}`}>
          {hasDelta && (
            <>
              {arrow} {Math.abs(delta)}{unit ? ` ${unit}` : ''}
              {hasLabel ? ' ' : ''}
            </>
          )}
          {hasLabel && delta_label}
        </div>
      )}
    </div>
  );
}
