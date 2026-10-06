// A focusable chart mark with the mockup's tooltip (showTip): opens on hover and on keyboard
// focus, follows the pointer, flips near the window's edges. The tooltip is portalled to <body>
// so a table's scroll box (or a mark's own transform) can't clip or shift it.

import { useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

const GAP = 14;

export function TipTarget({
  as: Tag = 'span',
  lines,
  label,
  className,
  style,
  children,
}: {
  as?: 'span' | 'div';
  lines: string[]; // the first line is the tooltip's heading
  label?: string; // the accessible name; defaults to the lines joined
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
}) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const pos: CSSProperties = {};
  if (at) {
    const w = typeof window === 'undefined' ? 1024 : window.innerWidth;
    const h = typeof window === 'undefined' ? 768 : window.innerHeight;
    if (at.x + GAP + 260 > w) pos.right = Math.max(8, w - at.x + GAP);
    else pos.left = at.x + GAP;
    if (at.y + GAP + 30 + lines.length * 18 > h) pos.bottom = Math.max(8, h - at.y + GAP);
    else pos.top = at.y + GAP;
  }
  return (
    <Tag
      className={className}
      style={style}
      role="img"
      tabIndex={0}
      aria-label={label ?? lines.join(', ')}
      onPointerMove={(e) => setAt({ x: e.clientX, y: e.clientY })}
      onPointerLeave={() => setAt(null)}
      onFocus={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        setAt({ x: r.left + r.width / 2, y: r.bottom });
      }}
      onBlur={() => setAt(null)}
    >
      {children}
      {at
        ? createPortal(
            <span role="tooltip" className="charttip" style={{ position: 'fixed', ...pos }}>
              {lines.map((line, i) => (
                <div key={i} className={i === 0 ? 'th' : undefined}>
                  {line}
                </div>
              ))}
            </span>,
            document.body,
          )
        : null}
    </Tag>
  );
}
