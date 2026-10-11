// A small trend line with an emphasised endpoint (mockup: the Teams page's Elo sparkline).
// Play calling's week-by-week view (PC01) adds a dashed reference line (the league) and points
// with their own tooltips on hover and focus (`points`); small samples can be drawn hollow.

import { TipTarget } from './TipTarget';

export interface SparkPoint {
  tip: string[]; // the first line is the tooltip's heading
  hollow?: boolean;
  xLabel?: string;
}

export function Sparkline({
  values,
  color = 'var(--s2)',
  label,
  width = 84,
  height = 26,
  reference,
  points,
}: {
  values: number[];
  color?: string;
  label: string;
  width?: number;
  height?: number;
  reference?: number | null;
  points?: SparkPoint[]; // one per value
}) {
  if (values.length === 0) return null;
  if (reference == null && !points) {
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
  // the extended form: a reference line in the scale, every point marked, labels under the axis
  const labels = points?.some((p) => p.xLabel != null);
  const m = { l: 8, r: 8, t: 8, b: labels ? 16 : 6 };
  const all = reference != null ? [...values, reference] : values;
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  if (hi - lo < 1e-9) {
    lo -= Math.abs(lo) * 0.1 || 0.05;
    hi += Math.abs(hi) * 0.1 || 0.05;
  }
  const pad = (hi - lo) * 0.15;
  lo -= pad;
  hi += pad;
  const n = values.length;
  const X = (i: number) => m.l + (n > 1 ? (i * (width - m.l - m.r)) / (n - 1) : (width - m.l - m.r) / 2);
  const Y = (v: number) => m.t + (1 - (v - lo) / (hi - lo)) * (height - m.t - m.b);
  return (
    <div className="spark" style={{ position: 'relative', width, height }}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
        {reference != null ? (
          <line x1={m.l} x2={width - m.r} y1={Y(reference)} y2={Y(reference)} className="refline" style={{ stroke: 'var(--ink-2)' }} strokeWidth={1.5} strokeDasharray="4 4" />
        ) : null}
        <polyline
          points={values.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')}
          fill="none"
          style={{ stroke: color }}
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {values.map((v, i) => {
          const hollow = points?.[i]?.hollow;
          return (
            <circle
              key={i}
              cx={X(i)}
              cy={Y(v)}
              r={hollow ? 3.4 : 4}
              style={hollow ? { fill: 'var(--panel)', stroke: color } : { fill: color, stroke: 'var(--panel)' }}
              strokeWidth={2}
            />
          );
        })}
        {labels
          ? points?.map((p, i) => (
              <text key={i} x={X(i)} y={height - 3} textAnchor="middle" style={{ fill: 'var(--ink-3)', fontSize: 10 }}>
                {p.xLabel}
              </text>
            ))
          : null}
      </svg>
      {points?.map((p, i) => (
        <TipTarget
          key={i}
          className="spark-hit"
          style={{ position: 'absolute', left: X(i) - 9, top: Y(values[i]) - 9, width: 18, height: 18, borderRadius: '50%' }}
          lines={p.tip}
        />
      ))}
    </div>
  );
}
