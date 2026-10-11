// Play calling's half-field (mockup: fieldSVG / dirStrip): pass depth × direction drawn like a
// coach's whiteboard (5-yard stripes, yard numbers, hash marks, the line of scrimmage, the line
// and the QB in --field / --chalk). Six zones, short / deep (the gamebook's 16+ air yards) × left /
// middle / right: a route from the QB to each, as thick as its share, and each zone's share next
// to the league's in a box tinted by the difference. Each box is a focusable overlay with the
// tooltip; small samples are dashed and untinted.

import type { CSSProperties } from 'react';
import type { PlaycallCell, PlaycallDirection, PlaycallSide, PlaycallWindow, PlaycallZone } from '../api/types';
import { allowedNote, cellTip, divTint, fmtRate } from '../lib/playcall';
import { TipTarget } from './TipTarget';
import './playcall.css';

const SHARE = { unit: 'share', digits: 0 } as const;
const W = 360;
const H = 340;
const LOS = 272;
const YD = 7.6;
const L = 12;
const R = W - 12;
const COLS = [L, L + (R - L) / 3, L + (2 * (R - L)) / 3, R];
const DEEP_Y = LOS - 16 * YD;
const QB = { x: W / 2, y: LOS + 40 };
const BOX = { w: 80, h: 34 };

const colX = (d: PlaycallDirection) =>
  d === 'left' ? (COLS[0] + COLS[1]) / 2 : d === 'middle' ? (COLS[1] + COLS[2]) / 2 : (COLS[2] + COLS[3]) / 2;
const boxY = (z: PlaycallZone) => (z.depth === 'deep' ? DEEP_Y - 64 : LOS - 74);
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** How a zone is named and explained in its tooltip. */
function zoneMetric(z: PlaycallZone) {
  const where = `${z.depth ? `${cap(z.depth)} ${z.direction}` : cap(z.direction)}`;
  return {
    ...SHARE,
    label: `${where} passes`,
    per: 'attempt',
    help: z.depth
      ? 'A share of the pass attempts with a depth and a direction (the six add to 100%). Deep is 16+ air yards (the gamebook).'
      : 'A share of the pass attempts with a direction, over every depth.',
  };
}

const svgFill = (c: PlaycallCell | null | undefined): CSSProperties => {
  const t = divTint(SHARE, c);
  return { fill: (t?.background as string | undefined) ?? 'var(--panel)' };
};

function Turf() {
  const out = [];
  for (let k = 0; k * 5 * YD < LOS; k++) {
    const y1 = Math.max(0, LOS - (k + 1) * 5 * YD);
    const y2 = LOS - k * 5 * YD;
    if (k % 2) out.push(<rect key={`s${k}`} x={L} y={y1} width={R - L} height={y2 - y1} style={{ fill: 'var(--field-2)' }} />);
  }
  for (let y = 1; LOS - y * YD > 4; y++) {
    const yy = LOS - y * YD;
    if (y % 5 === 0) {
      out.push(<line key={`y${y}`} x1={L} x2={R} y1={yy} y2={yy} className="ch" strokeWidth={1.2} opacity={0.55} />);
      out.push(
        <text key={`t${y}`} x={L + 6} y={yy - 3} className="ydl">
          {y}
        </text>,
      );
    } else {
      out.push(
        <g key={`h${y}`} className="ch" opacity={0.5}>
          <line x1={W * 0.41} x2={W * 0.41 + 7} y1={yy} y2={yy} />
          <line x1={W * 0.59 - 7} x2={W * 0.59} y1={yy} y2={yy} />
          <line x1={L} x2={L + 6} y1={yy} y2={yy} />
          <line x1={R - 6} x2={R} y1={yy} y2={yy} />
        </g>,
      );
    }
  }
  return <>{out}</>;
}

export function FieldZones({
  zones,
  win,
  who,
  minN,
  side,
}: {
  zones: PlaycallZone[];
  win: PlaycallWindow;
  who: string;
  minN: number;
  side: PlaycallSide;
}) {
  const placed = zones.map((z) => ({ z, c: z.windows[win], x: colX(z.direction), y: boxY(z) }));
  const first = placed.find((o) => o.c)?.c;
  const note = allowedNote({ caller: 'offense' }, side);
  return (
    <figure className="fieldfig">
      <div className="fieldbox">
        <svg viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
          <defs>
            <marker id="pc-arw" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" style={{ fill: 'var(--chalk)' }} />
            </marker>
          </defs>
          <rect x={0} y={0} width={W} height={H} rx={8} style={{ fill: 'var(--field)' }} />
          <Turf />
          <line x1={L} y1={0} x2={L} y2={H} className="ch" strokeWidth={2} />
          <line x1={R} y1={0} x2={R} y2={H} className="ch" strokeWidth={2} />
          {[COLS[1], COLS[2]].map((x) => (
            <line key={x} x1={x} x2={x} y1={6} y2={LOS} className="ch" strokeDasharray="3 5" opacity={0.7} />
          ))}
          <line x1={L} x2={R} y1={DEEP_Y} y2={DEEP_Y} className="ch" strokeDasharray="7 5" strokeWidth={1.5} />
          <text x={R - 6} y={DEEP_Y - 5} textAnchor="end" className="zl">
            DEEP · 16+ YDS
          </text>
          <text x={R - 6} y={DEEP_Y + 13} textAnchor="end" className="zl">
            SHORT
          </text>
          <line x1={L} x2={R} y1={LOS} y2={LOS} className="los" strokeWidth={2.5} />
          <text x={L + 6} y={LOS + 14} className="zl">
            LINE OF SCRIMMAGE
          </text>
          {[-2, -1, 0, 1, 2].map((i) =>
            i === 0 ? (
              <rect key={i} x={W / 2 - 6} y={LOS + 3} width={12} height={12} rx={1.5} className="pl" />
            ) : (
              <circle key={i} cx={W / 2 + i * 18} cy={LOS + 9} r={6.5} className="pl" />
            ),
          )}
          <circle cx={QB.x} cy={QB.y} r={8} className="pl qb" />
          <text x={QB.x} y={QB.y + 3.5} textAnchor="middle" className="plt">
            QB
          </text>
          {placed.map(({ z, c, x, y }) => {
            if (!c || c.value == null) return null;
            const ey = y + 30;
            const bend = z.direction === 'middle' ? 0 : (z.direction === 'left' ? -1 : 1) * (z.depth === 'deep' ? 26 : 10);
            const cy = (QB.y + ey) / 2 + (z.depth === 'deep' ? -30 : 0);
            return (
              <path
                key={z.metric}
                d={`M${QB.x},${QB.y - 10} Q${(QB.x + x) / 2 + bend},${cy} ${x},${ey}`}
                fill="none"
                className={`route${c.small ? ' small' : ''}`}
                strokeWidth={(1.2 + c.value * 22).toFixed(1)}
                markerEnd="url(#pc-arw)"
              />
            );
          })}
          {placed.map(({ z, c, x, y }) => (
            <g key={z.metric} className={`zbox${c?.small ? ' small' : ''}`}>
              <rect x={x - BOX.w / 2} y={y - 2} width={BOX.w} height={BOX.h} rx={6} style={c?.small ? undefined : svgFill(c)} />
              <text x={x} y={y + 15} textAnchor="middle" className="zv">
                {fmtRate(SHARE, c?.value)}
              </text>
              <text x={x} y={y + 27} textAnchor="middle" className="zs">
                lg {fmtRate(SHARE, c?.league)}
              </text>
            </g>
          ))}
        </svg>
        {placed.map(({ z, c, x, y }) => (
          <TipTarget
            key={z.metric}
            className="hit"
            style={{
              left: `${(((x - BOX.w / 2) / W) * 100).toFixed(2)}%`,
              top: `${(((y - 2) / H) * 100).toFixed(2)}%`,
              width: `${((BOX.w / W) * 100).toFixed(2)}%`,
              height: `${((BOX.h / H) * 100).toFixed(2)}%`,
            }}
            lines={cellTip(zoneMetric(z), c, who, null, minN, note)}
          />
        ))}
      </div>
      <figcaption className="muted">
        {first ? `${first.n} attempts with a depth and direction${first.small ? ' · small sample' : ''}` : 'No attempts yet'} · offense
        moving up the page · left and right from the quarterback's view
      </figcaption>
    </figure>
  );
}

/** The three directions over every depth, under the field. */
export function DirectionStrip({
  directions,
  win,
  who,
  minN,
  side,
}: {
  directions: PlaycallZone[];
  win: PlaycallWindow;
  who: string;
  minN: number;
  side: PlaycallSide;
}) {
  return (
    <div className="dirstrip">
      {directions.map((z) => {
        const c = z.windows[win];
        return (
          <TipTarget
            as="div"
            key={z.metric}
            className={`dcell${c?.small ? ' small' : ''}`}
            style={divTint(SHARE, c)}
            lines={cellTip(zoneMetric(z), c, who, 'every depth', minN, allowedNote({ caller: 'offense' }, side))}
          >
            <span className="eyebrow">{z.direction}</span>
            <b className="num">{fmtRate(SHARE, c?.value)}</b>
            <small className="num">lg {fmtRate(SHARE, c?.league)}</small>
          </TipTarget>
        );
      })}
    </div>
  );
}
