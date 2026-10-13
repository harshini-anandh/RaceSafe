/** Shared Recharts tooltip in the dashboard style. */
export default function ChartTooltip({ active, payload, label, valueFormatter = (v) => v, labelFormatter }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="t-title">{labelFormatter ? labelFormatter(label, payload) : label}</div>
      {payload.map((p) => (
        <div className="t-row" key={p.dataKey}>
          <span className="row" style={{ gap: 6 }}>
            <i style={{ width: 9, height: 9, borderRadius: 3, background: p.color || p.fill, display: 'inline-block' }} />
            {p.name}
          </span>
          <b>{valueFormatter(p.value, p)}</b>
        </div>
      ))}
    </div>
  );
}
