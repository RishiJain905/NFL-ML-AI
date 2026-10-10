// A reliability (calibration) chart for two or more forecasters on the same events (mockup:
// documentation/live-decisions/mockup/review.js calCard): predicted chance on x, how often it
// happened on y, one line + marks per series (circle / square, so identity isn't colour alone),
// the dashed diagonal, the events per bin under the axis, and one hover / keyboard-focus column per
// bin with a tooltip. Colours are series tokens; text stays in text tokens (dataviz rules).

import { useId, useState } from 'react';

export interface ReliabilityBin {
  lo: number; // the bin's lower edge, 0-1
  hi: number;
  n: number;
  predicted: number | null; // the mean forecast in the bin
  observed: number | null; // how often it happened
}

export interface ReliabilitySeries {
  name: string;
  color: string; // var(--s1) ...
  shape: 'circle' | 'square';
  bins: ReliabilityBin[];
}

const TICKS = [0, 0.25, 0.5, 0.75, 1];

export function ReliabilityChart({
  label,
  series,
  tip,
  nLabel = 'n',
  xLabel,
  yLabel,
}: {
  label: string;
  series: ReliabilitySeries[]; // the first series' bins set the columns and the counts
  tip: (i: number) => string[]; // a column's tooltip lines (the first is the heading)
  nLabel?: string;
  xLabel?: string;
  yLabel?: string;
}) {
  const W = 620;
  const H = 290;
  const m = { l: 46, r: 16, t: 12, b: 58 };
  const X = (v: number) => m.l + v * (W - m.l - m.r);
  const Y = (v: number) => m.t + (1 - v) * (H - m.t - m.b);
  const [active, setActive] = useState<number | null>(null);
  const tipId = useId();
  const cols = series[0]?.bins ?? [];
  const drawn = (s: ReliabilitySeries) =>
    s.bins.filter((b): b is ReliabilityBin & { predicted: number; observed: number } => b.n > 0 && b.predicted != null && b.observed != null);
  const at = active != null ? cols[active] : null;
  const atX = at ? (X((at.lo + at.hi) / 2) / W) * 100 : 0;

  return (
    <div className="chart">
      <div className="legend" style={{ marginBottom: 6 }}>
        {series.map((s) => (
          <span key={s.name}>
            <i style={{ background: s.color, borderRadius: s.shape === 'square' ? 1 : undefined }} />
            {s.name}
          </span>
        ))}
        <span>
          <i className="line" style={{ background: 'none', borderTop: '2px dashed var(--ink-3)', height: 0 }} />
          Perfectly calibrated
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
              <text x={X(t)} y={H - 38} textAnchor="middle">
                {t * 100}%
              </text>
            </g>
          ))}
          <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} style={{ stroke: 'var(--ink-3)' }} strokeWidth={1.5} strokeDasharray="4 4" />
          {series.map((s) => (
            <polyline
              key={s.name}
              points={drawn(s)
                .map((b) => `${X(b.predicted)},${Y(b.observed)}`)
                .join(' ')}
              fill="none"
              style={{ stroke: s.color }}
              strokeWidth={2}
              strokeLinejoin="round"
            />
          ))}
          {series.map((s) =>
            drawn(s).map((b) =>
              s.shape === 'circle' ? (
                <circle key={`${s.name}-${b.lo}`} cx={X(b.predicted)} cy={Y(b.observed)} r={4.5} style={{ fill: s.color, stroke: 'var(--panel)' }} strokeWidth={2} />
              ) : (
                <rect
                  key={`${s.name}-${b.lo}`}
                  x={X(b.predicted) - 4}
                  y={Y(b.observed) - 4}
                  width={8}
                  height={8}
                  style={{ fill: s.color, stroke: 'var(--panel)' }}
                  strokeWidth={2}
                />
              ),
            ),
          )}
          <text x={m.l - 8} y={H - 20} textAnchor="end" className="nlab" aria-hidden="true">
            {nLabel}
          </text>
          {cols.map((b, i) => (
            <g key={b.lo}>
              <text x={(X(b.lo) + X(b.hi)) / 2} y={H - 20} textAnchor="middle" className="nlab" aria-hidden="true">
                {b.n.toLocaleString('en-US')}
              </text>
              <rect
                className="hit"
                x={X(b.lo)}
                y={m.t}
                width={X(b.hi) - X(b.lo)}
                height={H - m.t - m.b}
                tabIndex={0}
                role="img"
                aria-label={tip(i).join(', ')}
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
          {xLabel ? (
            <text x={W - m.r} y={H - 2} textAnchor="end" aria-hidden="true">
              {xLabel}
            </text>
          ) : null}
          {yLabel ? (
            <text x={m.l} y={H - 2} textAnchor="start" aria-hidden="true">
              {yLabel}
            </text>
          ) : null}
        </svg>
        {at && active != null ? (
          <div
            id={tipId}
            role="tooltip"
            className="charttip"
            style={atX > 60 ? { right: `${100 - atX + 3}%`, top: '18%' } : { left: `${atX + 3}%`, top: '18%' }}
          >
            {tip(active).map((line, i) => (
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
