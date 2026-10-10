// Predicted vs what happened by category (mockup: documentation/live-decisions/mockup/review.js
// convChart): the forecast as a line with dots (a series token), what happened as hollow ink
// circles (dashed when the category has fewer than `minN` events), the count under each category,
// one 0-100% axis, and a hover / keyboard-focus column per category with a tooltip.

import { useId, useState } from 'react';

export interface ConversionPoint {
  label: string; // "1" ... "10+"
  n: number;
  predicted: number | null;
  actual: number | null;
}

const TICKS = [0, 0.25, 0.5, 0.75, 1];

export function ConversionChart({
  label,
  points,
  tip,
  color = 'var(--s1)',
  minN = 10,
  xLabel,
}: {
  label: string;
  points: ConversionPoint[];
  tip: (p: ConversionPoint) => string[];
  color?: string;
  minN?: number;
  xLabel?: string;
}) {
  const W = 520;
  const H = 250;
  const m = { l: 40, r: 10, t: 10, b: 48 };
  const n = Math.max(1, points.length);
  const step = (W - m.l - m.r) / n;
  const X = (i: number) => m.l + (i + 0.5) * step;
  const Y = (v: number) => m.t + (1 - v) * (H - m.t - m.b);
  const [active, setActive] = useState<number | null>(null);
  const tipId = useId();
  const line = points
    .map((p, i) => (p.predicted == null ? null : `${X(i)},${Y(p.predicted)}`))
    .filter(Boolean)
    .join(' ');
  const at = active != null ? points[active] : null;
  const atX = active != null ? (X(active) / W) * 100 : 0;

  return (
    <div style={{ position: 'relative' }} className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="group" aria-label={label}>
        <g className="grid">
          {TICKS.map((t) => (
            <line key={t} x1={m.l} x2={W - m.r} y1={Y(t)} y2={Y(t)} />
          ))}
        </g>
        {TICKS.map((t) => (
          <text key={t} x={m.l - 6} y={Y(t) + 4} textAnchor="end" aria-hidden="true">
            {t * 100}%
          </text>
        ))}
        <polyline points={line} fill="none" style={{ stroke: color }} strokeWidth={2} strokeLinejoin="round" />
        {points.map((p, i) => (
          <g key={p.label}>
            {p.predicted != null ? (
              <circle cx={X(i)} cy={Y(p.predicted)} r={4} style={{ fill: color, stroke: 'var(--panel)' }} strokeWidth={2} />
            ) : null}
            {p.actual != null ? (
              <circle
                cx={X(i)}
                cy={Y(p.actual)}
                r={4.5}
                style={{ fill: 'var(--panel)', stroke: 'var(--ink-2)' }}
                strokeWidth={2}
                strokeDasharray={p.n < minN ? '2 2' : undefined}
                opacity={p.n < minN ? 0.6 : undefined}
                data-small={p.n < minN ? 'true' : undefined}
              />
            ) : null}
            <text x={X(i)} y={H - 30} textAnchor="middle" aria-hidden="true">
              {p.label}
            </text>
            <text x={X(i)} y={H - 16} textAnchor="middle" className="nlab" aria-hidden="true">
              {p.n}
            </text>
            <rect
              className="hit"
              x={m.l + i * step}
              y={m.t}
              width={step}
              height={H - m.t - m.b}
              tabIndex={0}
              role="img"
              aria-label={tip(p).join(', ')}
              aria-describedby={active === i ? tipId : undefined}
              onPointerEnter={() => setActive(i)}
              onPointerLeave={() => setActive(null)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setActive(null);
              }}
            />
          </g>
        ))}
        <text x={m.l - 6} y={H - 16} textAnchor="end" className="nlab" aria-hidden="true">
          n
        </text>
        {xLabel ? (
          <text x={W - m.r} y={H - 1} textAnchor="end" aria-hidden="true">
            {xLabel}
          </text>
        ) : null}
      </svg>
      {at ? (
        <div
          id={tipId}
          role="tooltip"
          className="charttip"
          style={atX > 60 ? { right: `${100 - atX + 3}%`, top: '10%' } : { left: `${atX + 3}%`, top: '10%' }}
        >
          {tip(at).map((l, i) => (
            <div key={i} className={i === 0 ? 'th' : undefined}>
              {l}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
