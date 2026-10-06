// A small trend line with an emphasised endpoint (mockup: the Teams page's Elo sparkline).

export function Sparkline({
  values,
  color = 'var(--s2)',
  label,
  width = 84,
  height = 26,
}: {
  values: number[];
  color?: string;
  label: string;
  width?: number;
  height?: number;
}) {
  if (values.length === 0) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const step = values.length > 1 ? (width - 12) / (values.length - 1) : 0;
  const pts = values.map((v, i) => [6 + i * step, height - 4 - (hi === lo ? (height - 8) / 2 : ((v - lo) / (hi - lo)) * (height - 8))]);
  const [lx, ly] = pts[pts.length - 1];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      <polyline points={pts.map((p) => p.join(',')).join(' ')} fill="none" style={{ stroke: color }} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r={3.5} style={{ fill: color, stroke: 'var(--panel)' }} strokeWidth={2} />
    </svg>
  );
}
