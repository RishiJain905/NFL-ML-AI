// A line chart (mockup: lineChart): hairline grid, 2 px lines, end dots with a surface ring, a
// legend for two or more series, and a crosshair tooltip on hover. The plot is also focusable:
// focus shows the newest point, Left / Right (Home / End) step the crosshair point by point,
// Escape or leaving hides it, and a polite live region reads the point out. Colours are series
// tokens (--s1..--s3); text stays in text tokens (dataviz rules).

import { useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { nearestIndex, niceTicks } from './scale';
import './linechart.css';

export interface Series {
  name: string;
  color: string; // a CSS colour, normally var(--s1) .. var(--s3)
  values: (number | null)[];
}

export function LineChart({
  label,
  xs,
  series,
  format,
  xFormat = (x) => String(x),
  yFormat,
  height = 230,
  area = false,
}: {
  label: string;
  xs: (string | number)[];
  series: Series[];
  format: (v: number) => string;
  xFormat?: (x: string | number) => string;
  yFormat?: (v: number) => string;
  height?: number;
  area?: boolean;
}) {
  const W = 620;
  const H = height;
  const m = { l: 50, r: 18, t: 12, b: 28 };
  const svgRef = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ i: number; x: number; y: number; w: number } | null>(null);
  const tipId = useId();
  const hintId = useId();

  const all = series.flatMap((s) => s.values.filter((v): v is number => v != null));
  const ticks = niceTicks(Math.min(...all), Math.max(...all), 4);
  const y0 = ticks[0];
  const y1 = ticks[ticks.length - 1];
  const n = xs.length;
  const plotW = W - m.l - m.r;
  const X = (i: number) => m.l + (n <= 1 ? plotW / 2 : (i * plotW) / (n - 1));
  const Y = (v: number) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);
  const every = n > 12 ? 3 : 1;
  const yF = yFormat ?? format;

  const onMove = (e: PointerEvent<SVGRectElement>) => {
    const box = svgRef.current?.getBoundingClientRect();
    const vx = box && box.width > 0 ? ((e.clientX - box.left) / box.width) * W : m.l;
    const i = nearestIndex(vx, m.l, plotW, n);
    // the tooltip lives in the wrapper (legend included), so place it from the wrapper's box
    const wrap = wrapRef.current?.getBoundingClientRect();
    setHover({
      i,
      x: e.clientX - (wrap?.left ?? 0),
      y: e.clientY - (wrap?.top ?? 0),
      w: wrap?.width ?? 0,
    });
  };
  // on the right half the tooltip opens to the left of the pointer, so it never runs off-screen
  const tipStyle = (h: { x: number; y: number; w: number }) =>
    h.w > 0 && h.x > h.w / 2
      ? { right: h.w - h.x + 14, top: h.y + 14 }
      : { left: h.x + 14, top: h.y + 14 };

  // keyboard: place the tooltip at the point itself (the top of the plot), as the pointer would
  const showAt = (i: number) => {
    const box = svgRef.current?.getBoundingClientRect();
    const wrap = wrapRef.current?.getBoundingClientRect();
    const sx = box && box.width > 0 ? box.width / W : 0;
    const x = (box?.left ?? 0) - (wrap?.left ?? 0) + X(i) * sx;
    const y = (box?.top ?? 0) - (wrap?.top ?? 0) + m.t * sx;
    setHover({ i, x, y, w: wrap?.width ?? 0 });
  };
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    const at = hover?.i ?? n - 1;
    const next =
      e.key === 'ArrowRight'
        ? Math.min(n - 1, at + 1)
        : e.key === 'ArrowLeft'
          ? Math.max(0, at - 1)
          : e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? n - 1
              : null;
    if (e.key === 'Escape') {
      setHover(null);
      return;
    }
    if (next == null || n === 0) return;
    e.preventDefault();
    showAt(next);
  };
  const said =
    hover && n > 0
      ? `${xFormat(xs[hover.i])}: ${series
          .map(
            (s) =>
              `${s.name} ${s.values[hover.i] == null ? 'no value' : format(s.values[hover.i] as number)}`,
          )
          .join(', ')}`
      : '';

  return (
    <div className="chart" ref={wrapRef} style={{ position: 'relative' }}>
      {series.length >= 2 ? (
        <div className="legend" style={{ marginBottom: 6 }}>
          {series.map((s) => (
            <span key={s.name}>
              <i className="line" style={{ background: s.color }} />
              {s.name}
            </span>
          ))}
        </div>
      ) : null}
      <svg
        ref={svgRef}
        className="lc-plot"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={label}
        aria-describedby={hover ? tipId : hintId}
        tabIndex={0}
        onFocus={() => {
          if (!hover) showAt(n - 1);
        }}
        onBlur={() => setHover(null)}
        onKeyDown={onKey}
      >
        <g className="grid">
          {ticks.map((t) => (
            <line key={t} x1={m.l} x2={W - m.r} y1={Y(t)} y2={Y(t)} />
          ))}
        </g>
        {ticks.map((t) => (
          <text key={t} x={m.l - 8} y={Y(t) + 4} textAnchor="end">
            {yF(t)}
          </text>
        ))}
        {xs.map((x, i) =>
          i % every === 0 || i === n - 1 ? (
            <text key={`${x}-${i}`} x={X(i)} y={H - 8} textAnchor="middle">
              {String(x)}
            </text>
          ) : null,
        )}
        {series.map((s) => {
          const pts = s.values
            .map((v, i) => (v == null ? null : `${X(i)},${Y(v)}`))
            .filter(Boolean) as string[];
          const last = s.values[n - 1];
          return (
            <g key={s.name}>
              {area && pts.length ? (
                <polygon
                  points={`${X(0)},${Y(y0)} ${pts.join(' ')} ${X(n - 1)},${Y(y0)}`}
                  style={{ fill: s.color }}
                  opacity={0.1}
                />
              ) : null}
              <polyline
                points={pts.join(' ')}
                fill="none"
                style={{ stroke: s.color }}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {last != null ? (
                <circle
                  cx={X(n - 1)}
                  cy={Y(last)}
                  r={4}
                  style={{ fill: s.color, stroke: 'var(--panel)' }}
                  strokeWidth={2}
                />
              ) : null}
            </g>
          );
        })}
        {hover ? (
          <g>
            <line className="xh" x1={X(hover.i)} x2={X(hover.i)} y1={m.t} y2={H - m.b} />
            {series.map((s) =>
              s.values[hover.i] == null ? null : (
                <circle
                  key={s.name}
                  cx={X(hover.i)}
                  cy={Y(s.values[hover.i] as number)}
                  r={4.5}
                  style={{ fill: s.color, stroke: 'var(--panel)' }}
                  strokeWidth={2}
                />
              ),
            )}
          </g>
        ) : null}
        <rect
          data-testid="chart-hover"
          x={m.l}
          y={m.t}
          width={plotW}
          height={H - m.t - m.b}
          fill="transparent"
          aria-describedby={hover ? tipId : undefined}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      {hover ? (
        <div id={tipId} role="tooltip" className="charttip" style={tipStyle(hover)}>
          <div className="th">{xFormat(xs[hover.i])}</div>
          {series.map((s) => (
            <div key={s.name} className="tr">
              <span>
                <i style={{ background: s.color }} />
                {s.name}
              </span>
              <b className="num">
                {s.values[hover.i] == null ? '—' : format(s.values[hover.i] as number)}
              </b>
            </div>
          ))}
        </div>
      ) : null}
      <span id={hintId} className="lc-live">
        Use the left and right arrow keys to read each point.
      </span>
      <span className="lc-live" aria-live="polite">
        {said}
      </span>
    </div>
  );
}
