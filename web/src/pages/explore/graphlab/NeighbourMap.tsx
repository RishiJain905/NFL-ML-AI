// The neighbours map (AE03; mockup: graphlab.js drawMap): ~4.3k players on one canvas, placed by
// a 2-D PCA of their FastRP embeddings and coloured by unit (offense / defense / special teams:
// --s2 / --s1 / --s3). One position group can be highlighted. The picked player is drawn in ink
// with an accent ring and labelled; his neighbours are ringed; a row hovered in the lists is
// ringed and labelled too. Hover a dot for a tooltip; click one to pick him. The canvas is redrawn
// on resize and when the theme changes (it reads the colour tokens when it draws).

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { MapPoint } from '../../../api/types';
import { GROUP_WORD, UNIT_OF, UNIT_VAR } from '../../../lib/graphlab';

interface Domain {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}
const FULL: Domain = { x0: -1.42, x1: 1.42, y0: -1.25, y1: 1.34 };
const PAD = 14;
const HIT = 14; // px: the pointer picks the nearest dot this close

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function NeighbourMap({
  points,
  picked,
  ring,
  hot,
  group,
  zoom,
  label,
  onPick,
}: {
  points: MapPoint[];
  picked: string | null;
  ring: string[];
  hot: string | null;
  group: string | null;
  zoom: boolean;
  label: string;
  onPick: (playerId: string) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [theme, setTheme] = useState(0);
  const [tip, setTip] = useState<{ x: number; y: number; p: MapPoint } | null>(null);
  const byId = useMemo(() => new Map(points.map((p) => [p.player_id, p])), [points]);
  const me = picked ? (byId.get(picked) ?? null) : null;

  // the size (ResizeObserver also reports the first size) and the theme (the <html> attributes)
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r) setSize({ w: r.width, h: r.height });
    });
    ro.observe(el);
    const mo = new MutationObserver(() => setTheme((t) => t + 1));
    mo.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-pal', 'data-mode', 'data-theme'],
    });
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onScheme = () => setTheme((t) => t + 1);
    mq.addEventListener('change', onScheme);
    return () => {
      ro.disconnect();
      mo.disconnect();
      mq.removeEventListener('change', onScheme);
    };
  }, []);

  const dom: Domain = useMemo(
    () =>
      zoom && me ? { x0: me.x - 0.38, x1: me.x + 0.38, y0: me.y - 0.26, y1: me.y + 0.26 } : FULL,
    [zoom, me],
  );
  const sx = (x: number) => PAD + ((x - dom.x0) / (dom.x1 - dom.x0)) * (size.w - PAD * 2);
  const sy = (y: number) => PAD + (1 - (y - dom.y0) / (dom.y1 - dom.y0)) * (size.h - PAD * 2);

  useEffect(() => {
    const cv = canvas.current;
    if (!cv || !size.w || !size.h) return;
    const g = cv.getContext('2d');
    if (!g) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    cv.width = Math.round(size.w * dpr);
    cv.height = Math.round(size.h * dpr);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const d = dom;
    const X = (x: number) => PAD + ((x - d.x0) / (d.x1 - d.x0)) * (size.w - PAD * 2);
    const Y = (y: number) => PAD + (1 - (y - d.y0) / (d.y1 - d.y0)) * (size.h - PAD * 2);
    const col = {
      off: cssVar(UNIT_VAR.off),
      def: cssVar(UNIT_VAR.def),
      spec: cssVar(UNIT_VAR.spec),
    };
    const panel = cssVar('--panel');
    const ink = cssVar('--ink');
    const accent = cssVar('--accent');
    const rad = zoom ? 3.4 : 2.4;
    g.clearRect(0, 0, size.w, size.h);
    for (const p of points) {
      const x = X(p.x);
      const y = Y(p.y);
      if (x < -5 || y < -5 || x > size.w + 5 || y > size.h + 5) continue;
      const on = !group || p.grp === group;
      g.globalAlpha = on ? (group ? 0.95 : 0.62) : 0.1;
      g.fillStyle = col[UNIT_OF[p.grp] ?? 'spec'];
      g.beginPath();
      g.arc(x, y, rad, 0, Math.PI * 2);
      g.fill();
    }
    g.globalAlpha = 1;
    for (const id of ring) {
      const p = byId.get(id);
      if (!p) continue;
      const x = X(p.x);
      const y = Y(p.y);
      g.fillStyle = col[UNIT_OF[p.grp] ?? 'spec'];
      g.strokeStyle = panel;
      g.lineWidth = 2;
      g.beginPath();
      g.arc(x, y, rad + 2.2, 0, Math.PI * 2);
      g.fill();
      g.stroke();
      g.strokeStyle = ink;
      g.lineWidth = 1.2;
      g.beginPath();
      g.arc(x, y, rad + 4.4, 0, Math.PI * 2);
      g.stroke();
    }
    if (me) {
      // ink, not a series colour: the picked player must not read as offense or defense
      const x = X(me.x);
      const y = Y(me.y);
      g.fillStyle = ink;
      g.strokeStyle = panel;
      g.lineWidth = 2.5;
      g.beginPath();
      g.arc(x, y, rad + 4, 0, Math.PI * 2);
      g.fill();
      g.stroke();
      g.strokeStyle = accent;
      g.lineWidth = 2;
      g.beginPath();
      g.arc(x, y, rad + 7.5, 0, Math.PI * 2);
      g.stroke();
    }
    const h = hot ? byId.get(hot) : null;
    if (h) {
      g.strokeStyle = accent;
      g.lineWidth = 2.5;
      g.beginPath();
      g.arc(X(h.x), Y(h.y), rad + 7, 0, Math.PI * 2);
      g.stroke();
    }
  }, [points, byId, size, dom, me, ring, hot, group, zoom, theme]);

  const nearest = (mx: number, my: number): MapPoint | null => {
    let best: MapPoint | null = null;
    let bd = HIT * HIT;
    for (const p of points) {
      if (group && p.grp !== group) continue;
      const dx = sx(p.x) - mx;
      const dy = sy(p.y) - my;
      const dd = dx * dx + dy * dy;
      if (dd < bd) {
        bd = dd;
        best = p;
      }
    }
    return best;
  };
  const at = (e: React.PointerEvent | React.MouseEvent) => {
    const r = e.currentTarget.getBoundingClientRect();
    return nearest(e.clientX - r.left, e.clientY - r.top);
  };

  const labels: { p: MapPoint; me: boolean }[] = [];
  if (me) labels.push({ p: me, me: true });
  const hp = hot ? byId.get(hot) : null;
  if (hp && hp !== me) labels.push({ p: hp, me: false });

  return (
    <div className="gl-map" ref={wrap}>
      <canvas
        ref={canvas}
        role="img"
        aria-label={label}
        onPointerMove={(e) => {
          const p = at(e);
          setTip(p ? { x: e.clientX, y: e.clientY, p } : null);
        }}
        onPointerLeave={() => setTip(null)}
        onClick={(e) => {
          const p = at(e);
          if (p) onPick(p.player_id);
        }}
      />
      <div className="lbls" aria-hidden="true">
        {size.w
          ? labels.map(({ p, me: isMe }) => {
              const x = sx(p.x);
              return (
                <span
                  key={p.player_id}
                  className={`lbl${isMe ? ' me' : ''}${x > size.w - 150 ? ' left' : ''}`}
                  style={{ left: x, top: sy(p.y) }}
                >
                  {p.name}
                </span>
              );
            })
          : null}
      </div>
      {tip
        ? createPortal(
            <span
              role="tooltip"
              className="charttip"
              style={{
                position: 'fixed',
                left: Math.min(tip.x + 14, window.innerWidth - 240),
                top: tip.y + 14,
              }}
            >
              <div className="th">{tip.p.name}</div>
              <div>
                {GROUP_WORD[tip.p.grp] ?? tip.p.grp} · {tip.p.team ?? '—'}
              </div>
              <div className="muted">Click to show his neighbours</div>
            </span>,
            document.body,
          )
        : null}
    </div>
  );
}
