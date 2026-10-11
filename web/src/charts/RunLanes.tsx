// Play calling's offensive-line diagram (mockup: linesSVG): the five linemen as a playbook draws
// them (tackles and guards as circles, the center a square), the ends dashed, the QB and the back;
// a run path from the back to each of the seven lanes (left end … right end, named for the
// lineman the gamebook says the run went at), as thick as its share, and above each lane its
// share next to the league's in a box tinted by the difference. Each box is a focusable overlay
// with the tooltip; small samples are dashed and untinted.

import type { CSSProperties } from 'react';
import type { PlaycallCell, PlaycallLane, PlaycallLaneId, PlaycallSide, PlaycallWindow } from '../api/types';
import { allowedNote, cellTip, divTint, fmtRate } from '../lib/playcall';
import { TipTarget } from './TipTarget';
import './playcall.css';

const SHARE = { unit: 'share', digits: 0 } as const;
const W = 440;
const H = 300;
const LOS = 170;
const RB = { x: 220, y: LOS + 92 };
const BOX = { w: 46, h: 62, y: 40 };

const LANE_X: Record<PlaycallLaneId, number> = {
  left_end: 46,
  left_tackle: 120,
  left_guard: 170,
  middle: 220,
  right_guard: 270,
  right_tackle: 320,
  right_end: 394,
};
const LANE_SHORT: Record<PlaycallLaneId, string> = {
  left_end: 'L END',
  left_tackle: 'L TACKLE',
  left_guard: 'L GUARD',
  middle: 'MIDDLE',
  right_guard: 'R GUARD',
  right_tackle: 'R TACKLE',
  right_end: 'R END',
};
const OL: [string, number][] = [
  ['T', 120],
  ['G', 170],
  ['C', 220],
  ['G', 270],
  ['T', 320],
];

const laneMetric = (l: PlaycallLane) => ({
  ...SHARE,
  label: `Runs: ${l.label.toLowerCase()}`,
  per: 'designed run',
  help: 'A share of the designed runs with a direction (the seven add to 100%).',
});

const svgFill = (c: PlaycallCell | null | undefined): CSSProperties => {
  const t = divTint(SHARE, c);
  return { fill: (t?.background as string | undefined) ?? 'var(--panel)' };
};

export function RunLanes({
  lanes,
  win,
  who,
  minN,
  side,
}: {
  lanes: PlaycallLane[];
  win: PlaycallWindow;
  who: string;
  minN: number;
  side: PlaycallSide;
}) {
  const placed = lanes.map((l) => ({ l, c: l.windows[win], x: LANE_X[l.lane] }));
  const first = placed.find((o) => o.c)?.c;
  const note = allowedNote({ caller: 'offense' }, side);
  return (
    <figure className="fieldfig">
      <div className="fieldbox">
        <svg viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
          <defs>
            <marker id="pc-arwr" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" style={{ fill: 'var(--chalk)' }} />
            </marker>
          </defs>
          <rect x={0} y={0} width={W} height={H} rx={8} style={{ fill: 'var(--field)' }} />
          {[LOS - 76, LOS - 38].map((y) => (
            <line key={y} x1={8} x2={W - 8} y1={y} y2={y} className="ch" opacity={0.35} />
          ))}
          <line x1={8} x2={W - 8} y1={LOS} y2={LOS} className="los" strokeWidth={2.5} />
          <text x={12} y={LOS - 6} className="zl">
            LINE OF SCRIMMAGE
          </text>
          {OL.map(([p, x]) => (
            <g key={x}>
              {p === 'C' ? (
                <rect x={x - 11} y={LOS + 5} width={22} height={22} rx={2} className="pl" />
              ) : (
                <circle cx={x} cy={LOS + 16} r={11} className="pl" />
              )}
              <text x={x} y={LOS + 20} textAnchor="middle" className="plt">
                {p}
              </text>
            </g>
          ))}
          {[46, 394].map((x) => (
            <g key={x}>
              <circle cx={x} cy={LOS + 16} r={11} className="pl end" />
              <text x={x} y={LOS + 20} textAnchor="middle" className="plt">
                E
              </text>
            </g>
          ))}
          <circle cx={220} cy={LOS + 46} r={10} className="pl qb" />
          <text x={220} y={LOS + 50} textAnchor="middle" className="plt">
            QB
          </text>
          <circle cx={RB.x} cy={RB.y} r={10} className="pl qb" />
          <text x={RB.x} y={RB.y + 4} textAnchor="middle" className="plt">
            RB
          </text>
          {placed.map(({ l, c, x }) =>
            c && c.value != null ? (
              <path
                key={l.lane}
                d={`M${RB.x},${RB.y - 12} C${RB.x},${RB.y - 46} ${x},${LOS + 52} ${x},${LOS - 22}`}
                fill="none"
                className={`route${c.small ? ' small' : ''}`}
                strokeWidth={(1.2 + c.value * 26).toFixed(1)}
                markerEnd="url(#pc-arwr)"
              />
            ) : null,
          )}
          {placed.map(({ l, c, x }) => {
            const bx = Math.max(BOX.w / 2 + 1, Math.min(W - BOX.w / 2 - 1, x));
            return (
              <g key={l.lane} className={`zbox${c?.small ? ' small' : ''}`}>
                <rect x={bx - BOX.w / 2} y={BOX.y} width={BOX.w} height={BOX.h} rx={6} style={c?.small ? undefined : svgFill(c)} />
                <text x={bx} y={54} textAnchor="middle" className="zn">
                  {LANE_SHORT[l.lane]}
                </text>
                <text x={bx} y={76} textAnchor="middle" className="zv">
                  {fmtRate(SHARE, c?.value)}
                </text>
                <text x={bx} y={92} textAnchor="middle" className="zs">
                  lg {fmtRate(SHARE, c?.league)}
                </text>
              </g>
            );
          })}
        </svg>
        {placed.map(({ l, c, x }) => {
          const bx = Math.max(BOX.w / 2 + 1, Math.min(W - BOX.w / 2 - 1, x));
          return (
            <TipTarget
              key={l.lane}
              className="hit"
              style={{
                left: `${(((bx - BOX.w / 2) / W) * 100).toFixed(2)}%`,
                top: `${((BOX.y / H) * 100).toFixed(2)}%`,
                width: `${((BOX.w / W) * 100).toFixed(2)}%`,
                height: `${((BOX.h / H) * 100).toFixed(2)}%`,
              }}
              lines={cellTip(laneMetric(l), c, who, null, minN, note)}
            />
          );
        })}
      </div>
      <figcaption className="muted">
        {first ? `${first.n} designed runs with a direction${first.small ? ' · small sample' : ''}` : 'No designed runs yet'} · each lane
        named for the lineman the run goes at (the gamebook's left end … right end)
      </figcaption>
    </figure>
  );
}
