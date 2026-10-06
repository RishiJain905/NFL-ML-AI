// Horizontal bars (mockup: hbarsHTML): label, a bar from one baseline with a 4 px rounded end,
// the value at the tip in text colour, and a tooltip on hover or focus.

import { useState, type CSSProperties } from 'react';

export interface BarRow {
  label: string;
  value: number;
  tip?: string;
}

export function HBars({
  rows,
  format,
  color = 'var(--s1)',
  max,
  label,
}: {
  rows: BarRow[];
  format: (v: number) => string;
  color?: string;
  max?: number;
  label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const top = max ?? Math.max(...rows.map((r) => Math.abs(r.value)), 0);
  const labelWidth = Math.min(220, Math.max(60, ...rows.map((r) => r.label.length * 7.2 + 8)));
  return (
    <div className="hbars" role="list" aria-label={label} style={{ '--hbar-label': `${labelWidth}px` } as CSSProperties}>
      {rows.map((r, i) => (
        <div
          key={r.label}
          className="hbar-row"
          role="listitem"
          tabIndex={0}
          onPointerEnter={() => setHover(i)}
          onPointerLeave={() => setHover(null)}
          onFocus={() => setHover(i)}
          onBlur={() => setHover(null)}
        >
          <span className="dim hbar-label">{r.label}</span>
          <span className="hbar-track">
            <span
              className="hbar"
              style={{ width: `${top > 0 ? Math.max(0.6, (Math.abs(r.value) / top) * 100) : 0}%`, background: color }}
            />
            {hover === i ? (
              <span role="tooltip" className="charttip" style={{ left: 0, top: 18 }}>
                {(r.tip ?? `${r.label}\n${format(r.value)}`).split('\n').map((line, j) => (
                  <div key={j} className={j === 0 ? 'th' : undefined}>
                    {line}
                  </div>
                ))}
              </span>
            ) : null}
          </span>
          <span className="num hbar-value">{format(r.value)}</span>
        </div>
      ))}
    </div>
  );
}
