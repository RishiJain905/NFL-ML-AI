// A calibration curve (mockup: calChart): predicted chance on x, how often it happened on y,
// one point per bin sized by its games (√n), the dashed diagonal for perfect calibration, and a
// tooltip on hover or keyboard focus. Colour is the first series token; text stays in text tokens.
// The diagonal is named in the legend, not on the plot (the mockup's label sat on the top bins).

import { useId, useState } from 'react';

export interface CalibrationBin {
  predicted: number; // 0–1
  observed: number; // 0–1
  games: number;
}

const TICKS = [0, 0.25, 0.5, 0.75, 1];
const pctText = (v: number) => `${(v * 100).toFixed(1)}%`;

export function CalibrationChart({
  bins,
  label,
  xLabel = 'predicted home win chance →',
}: {
  bins: CalibrationBin[];
  label: string;
  xLabel?: string;
}) {
  const W = 620;
  const H = 260;
  const m = { l: 50, r: 18, t: 12, b: 34 };
  const X = (v: number) => m.l + v * (W - m.l - m.r);
  const Y = (v: number) => m.t + (1 - v) * (H - m.t - m.b);
  const [active, setActive] = useState<number | null>(null);
  const tipId = useId();
  const top = Math.max(1, ...bins.map((b) => b.games));
  const radius = (n: number) => 4 + 6 * Math.sqrt(Math.max(0, n) / top);
  const pts = [...bins].sort((a, b) => a.predicted - b.predicted);
  const tipLines = (b: CalibrationBin) => [
    `Predicted ${pctText(b.predicted)}`,
    `Happened ${pctText(b.observed)}`,
    `${b.games} games`,
  ];
  const at = active != null ? pts[active] : null;

  return (
    <div className="chart">
      <div className="legend" style={{ marginBottom: 6 }}>
        <span>
          <i style={{ background: 'var(--s1)' }} />
          Bins, sized by games
        </span>
        <span>
          <i
            className="line"
            style={{ background: 'none', borderTop: '2px dashed var(--ink-3)', height: 0 }}
          />
          Perfect calibration
        </span>
      </div>
      <div style={{ position: 'relative' }}>
        <svg viewBox={`0 0 ${W} ${H}`} role="group" aria-label={label}>
          <g className="grid">
            {TICKS.map((t) => (
              <line key={t} x1={m.l} x2={W - m.r} y1={Y(t)} y2={Y(t)} />
            ))}
          </g>
          {TICKS.map((t) => (
            <g key={t} aria-hidden="true">
              <text x={m.l - 8} y={Y(t) + 4} textAnchor="end">
                {t * 100}%
              </text>
              <text x={X(t)} y={H - 14} textAnchor="middle">
                {t * 100}%
              </text>
            </g>
          ))}
          <line
            x1={X(0)}
            y1={Y(0)}
            x2={X(1)}
            y2={Y(1)}
            style={{ stroke: 'var(--ink-3)' }}
            strokeWidth={1.5}
            strokeDasharray="4 4"
          />
          {pts.length > 1 ? (
            <polyline
              points={pts.map((b) => `${X(b.predicted)},${Y(b.observed)}`).join(' ')}
              fill="none"
              style={{ stroke: 'var(--s1)' }}
              strokeWidth={2}
              strokeLinejoin="round"
            />
          ) : null}
          {pts.map((b, i) => (
            <circle
              key={`${b.predicted}-${i}`}
              cx={X(b.predicted)}
              cy={Y(b.observed)}
              r={radius(b.games)}
              style={{
                fill: 'var(--s1)',
                stroke: active === i ? 'var(--ink)' : 'var(--panel)',
                outline: 'none',
              }}
              strokeWidth={2}
              tabIndex={0}
              role="img"
              aria-label={tipLines(b).join(', ')}
              aria-describedby={active === i ? tipId : undefined}
              onPointerEnter={() => setActive(i)}
              onPointerLeave={() => setActive(null)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setActive(null);
              }}
            />
          ))}
          <text x={W - m.r} y={H - 1} textAnchor="end" aria-hidden="true">
            {xLabel}
          </text>
        </svg>
        {at ? (
          <div
            id={tipId}
            role="tooltip"
            className="charttip"
            style={
              at.predicted > 0.6
                ? {
                    right: `${100 - (X(at.predicted) / W) * 100 + 2}%`,
                    top: `${(Y(at.observed) / H) * 100}%`,
                  }
                : {
                    left: `${(X(at.predicted) / W) * 100 + 2}%`,
                    top: `${(Y(at.observed) / H) * 100}%`,
                  }
            }
          >
            {tipLines(at).map((line, i) => (
              <div key={i} className={i === 0 ? 'th' : undefined}>
                {line}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
