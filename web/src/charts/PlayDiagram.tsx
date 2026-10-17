// PC03: one play drawn on a field at time t (mockup: fieldSvg + dynLayer in
// documentation/play-calling/mockup/playcalling.js). Pure rendering: a PlayDiagram and a time in,
// an SVG out; the clock and the controls are PlayDiagramPlayer's. The SVG's user units are yards:
// x to the right (the offense moves left to right), y down (the offense's left sideline on top).
// Offense --s1, defense --s2; a measured player is a filled dot, an approximate one (`approx`: a
// generic spot) a hollow dashed ring with its label in the side's colour. The field's own margin
// carries "RECONSTRUCTED · NOT TRACKING" (or "REAL PLAY · TRACKING"), so even a crop says it.

import type { ReactNode } from 'react';
import type { DiagramMark, DiagramPlayer, PlayDiagram as Diagram } from '../api/types';
import {
  EPS,
  LIFT,
  ballAt,
  endTime,
  hasBall,
  marksAt,
  nextPhaseT,
  partialPath,
  pathLength,
  pointsAttr,
  positionAt,
  progress,
  throwArc,
  trailOf,
  viewBox,
  viewRange,
  yardNumber,
  type Pt,
} from '../lib/playdiagram';
import './playdiagram.css';

const HASH_IN = 23.08; // the hash ticks: 70 ft 9 in from each sideline (23.58 yd), a yard long

function Field({ dg }: { dg: Diagram }) {
  const f = dg.field;
  const W = f.width;
  const [x0, x1] = viewRange(f);
  const top = -3.2;
  const lines: ReactNode[] = [];
  for (let x = Math.ceil(x0); x <= Math.floor(x1); x++) {
    if (x < 0 || x > 100) continue;
    if (x % 5 === 0) {
      lines.push(<line key={`y${x}`} x1={x} x2={x} y1={0} y2={W} className={`pdyl${x === 0 || x === 100 ? ' goal' : ''}`} />);
    } else {
      lines.push(
        <g key={`t${x}`} className="pdtk">
          <line x1={x} x2={x} y1={0.4} y2={1.2} />
          <line x1={x} x2={x} y1={W - 1.2} y2={W - 0.4} />
          <line x1={x} x2={x} y1={HASH_IN} y2={HASH_IN + 1} />
          <line x1={x} x2={x} y1={W - HASH_IN - 1} y2={W - HASH_IN} />
        </g>,
      );
    }
    if (x % 10 === 0 && x > 0 && x < 100) {
      lines.push(
        <g key={`n${x}`} className="pdno">
          <text x={x} y={8.6} textAnchor="middle">
            {yardNumber(x)}
          </text>
          <text x={x} y={W - 6.6} textAnchor="middle">
            {yardNumber(x)}
          </text>
        </g>,
      );
    }
  }
  const real = dg.kind === 'real';
  return (
    <g>
      <rect x={x0} y={top} width={x1 - x0} height={W - 2 * top} className="pdturf2" />
      <rect x={x0} y={0} width={x1 - x0} height={W} className="pdturf" />
      {x0 < 0 ? <rect x={x0} y={0} width={Math.min(0, x1) - x0} height={W} className="pdez" /> : null}
      {x1 > 100 ? <rect x={Math.max(100, x0)} y={0} width={x1 - Math.max(100, x0)} height={W} className="pdez" /> : null}
      {lines}
      <line x1={x0} x2={x1} y1={0} y2={0} className="pdside" />
      <line x1={x0} x2={x1} y1={W} y2={W} className="pdside" />
      <line x1={f.los} x2={f.los} y1={0} y2={W} className="pdlos" />
      <text x={f.los} y={top + 2.3} className="pdlt" textAnchor="middle">
        LOS
      </text>
      {f.first_down != null && f.first_down < 100 ? (
        <>
          <line x1={f.first_down} x2={f.first_down} y1={0} y2={W} className="pdfd" data-testid="first-down-line" />
          <text x={f.first_down} y={top + 2.3} className="pdlt fd" textAnchor="middle">
            1ST DOWN
          </text>
        </>
      ) : f.first_down == null ? (
        <text x={Math.min(x1 - 6, 104)} y={top + 2.3} className="pdlt fd" textAnchor="middle">
          GOAL TO GO
        </text>
      ) : null}
      {!f.hash && !real ? (
        <text x={x1 - 0.8} y={W + 2.4} className="pdlt" textAnchor="end">
          hash not charted: the ball is drawn in the middle
        </text>
      ) : null}
      <text x={x0 + 0.8} y={W + 2.4} className="pdwm">
        {real ? 'REAL PLAY · TRACKING' : 'RECONSTRUCTED · NOT TRACKING'}
      </text>
    </g>
  );
}

function EndTag({ x1, width, p, text, bad }: { x1: number; width: number; p: Pt; text: string | null; bad?: boolean }) {
  if (!text) return null;
  const fs = 1.45;
  const w = text.length * fs * 0.52 + 1.6;
  const h = 2.6;
  const left = p[0] + 2 + w > x1 - 0.5;
  const tx = left ? p[0] - 2 - w : p[0] + 2;
  const ty = Math.max(0.6, Math.min(width - h - 0.6, p[1] - h - 0.8));
  return (
    <g className={`pdtag${bad ? ' bad' : ''}`}>
      <line x1={p[0]} y1={p[1]} x2={left ? tx + w : tx} y2={ty + h / 2} />
      <rect x={tx} y={ty} width={w} height={h} rx={0.5} />
      <text x={tx + 0.8} y={ty + 1.85}>
        {text}
      </text>
    </g>
  );
}

function Mark({ dg, m, t, endT, x1 }: { dg: Diagram; m: DiagramMark; t: number; endT: number; x1: number }) {
  const pts = m.points as Pt[];
  const W = dg.field.width;
  switch (m.kind) {
    case 'motion':
      return <polyline points={pointsAttr(partialPath(pts, progress(t, m.t, nextPhaseT(dg, m.t))))} className="pdmk motion" markerEnd="url(#pdA)" data-mark="motion" />;
    case 'fake':
      return (
        <g data-mark="fake">
          <polyline points={pointsAttr(partialPath(pts, progress(t, m.t, nextPhaseT(dg, m.t))))} className="pdmk fake" markerEnd="url(#pdA)" />
          {pts[1] ? (
            <text x={pts[1][0] - 0.6} y={pts[1][1] + 2.6} className="pdmt" textAnchor="end">
              fake
            </text>
          ) : null}
        </g>
      );
    case 'handoff':
      return (
        <g className="pdho" data-mark="handoff">
          <circle cx={pts[0][0]} cy={pts[0][1]} r={0.9} />
        </g>
      );
    case 'lane': {
      const last = pts[pts.length - 1];
      return (
        <g data-mark="lane">
          <polyline points={pointsAttr(pts)} className="pdmk lane" markerEnd="url(#pdAs)" />
          <text x={last[0] + 0.6} y={last[1] - 2.4} className="pdmt strong">
            {(m.text ?? '').replace(/^Run /, '')}
          </text>
        </g>
      );
    }
    case 'run':
      return (
        <polyline
          points={pointsAttr(partialPath(pts, progress(t, m.t, endT)))}
          className={`pdmk run${m.approx ? ' ap' : ''}`}
          markerEnd="url(#pdA)"
          data-mark="run"
          data-approx={m.approx ? 'true' : undefined}
        />
      );
    case 'throw': {
      const arc = throwArc(dg, m, t);
      return (
        <g data-mark="throw">
          <line x1={pts[0][0]} y1={pts[0][1]} x2={pts[1]?.[0] ?? pts[0][0]} y2={pts[1]?.[1] ?? pts[0][1]} className="pdmk throwg" />
          <polyline points={pointsAttr(arc.pts)} className="pdmk throw" markerEnd={arc.done ? 'url(#pdAs)' : undefined} />
        </g>
      );
    }
    case 'end':
      return (
        <g data-mark="end">
          <EndTag x1={x1} width={W} p={pts[0]} text={m.text} />
        </g>
      );
    case 'incomplete':
    case 'interception':
      return (
        <g data-mark={m.kind}>
          <g className="pdx">
            <path d={`M${pts[0][0] - 0.9},${pts[0][1] - 0.9} l1.8,1.8 m0,-1.8 l-1.8,1.8`} />
          </g>
          <EndTag x1={x1} width={W} p={pts[0]} text={m.text} bad />
        </g>
      );
    case 'sack':
      return (
        <g data-mark="sack">
          <g className="pdx">
            <path
              className="burst"
              d={`M${pts[0][0]},${pts[0][1] - 1.4} l0.45,0.95 1,-0.3 -0.45,0.95 0.9,0.6 -1,0.25 0.1,1 -0.95,-0.5 -0.55,0.85 -0.1,-1 -1,0.15 0.6,-0.85 -0.75,-0.65 1,-0.15z`}
            />
          </g>
          <EndTag x1={x1} width={W} p={pts[0]} text={m.text} bad />
        </g>
      );
    default:
      return null;
  }
}

function Player({ dg, p, t, endT }: { dg: Diagram; p: DiagramPlayer; t: number; endT: number }) {
  const [x, y] = positionAt(p.track, t);
  const side = p.side === 'offense' ? 'o' : 'd';
  const ap = p.approx ? ' ap' : '';
  const carrying = p.carrier && hasBall(dg, x, y, t);
  return (
    <g data-player={p.id} data-approx={p.approx ? 'true' : 'false'}>
      {p.rusher ? <circle cx={x} cy={y} r={1.75} className="pdrush" /> : null}
      {p.target && !(p.carrier && t >= endT - EPS) ? <circle cx={x} cy={y} r={2.05} className="pdtgt" /> : null}
      {carrying ? <circle cx={x} cy={y} r={2.05} className="pdcar" /> : null}
      <circle cx={x} cy={y} r={1.12} className={`pdp ${side}${ap}`} />
      <text x={x} y={y + 0.38} className={`pdpl ${side}${ap}`} textAnchor="middle">
        {p.label}
      </text>
    </g>
  );
}

/** The field and everything on it at time `t` (seconds into the diagram's own timeline). */
export function PlayDiagramSvg({ diagram: dg, t }: { diagram: Diagram; t: number }) {
  const vb = viewBox(dg.field);
  const [, x1] = viewRange(dg.field);
  const endT = endTime(dg);
  const order = [...dg.players].sort(
    (a, b) =>
      (a.side === 'defense' ? 0 : 1) - (b.side === 'defense' ? 0 : 1) || (a.target || a.carrier ? 1 : 0) - (b.target || b.carrier ? 1 : 0),
  );
  const b = ballAt(dg.ball, t);
  const p = dg.play;
  const aria = `${dg.label}. ${p.game_label ?? ''}: ${p.down_text ?? ''}${p.spot ? ` at ${p.spot}` : ''}. ${p.result.text}.`;
  return (
    <svg className="pdsvg" viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`} role="img" aria-label={aria} preserveAspectRatio="xMidYMid meet">
      <defs>
        <marker id="pdA" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" className="pdah" />
        </marker>
        <marker id="pdAs" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" className="pdah" />
        </marker>
      </defs>
      <Field dg={dg} />
      <g className="pdtrails">
        {dg.players.map((pl) => {
          if (pl.track.length < 2) return null;
          const tr = trailOf(pl.track, t);
          if (pathLength(tr) < 1.5) return null;
          return <polyline key={pl.id} points={pointsAttr(tr)} className={`pdtr ${pl.side === 'offense' ? 'o' : 'd'}${pl.approx ? ' ap' : ''}`} />;
        })}
      </g>
      <g className="pdmarks">
        {marksAt(dg.marks, t).map((m, i) => (
          <Mark key={`${m.kind}-${i}`} dg={dg} m={m} t={t} endT={endT} x1={x1} />
        ))}
      </g>
      <g className="pdplayers">
        {order.map((pl) => (
          <Player key={pl.id} dg={dg} p={pl} t={t} endT={endT} />
        ))}
      </g>
      {b ? (
        <g className="pdballg" data-testid="ball">
          {b[2] > 1.2 ? <ellipse cx={b[0]} cy={b[1]} rx={0.55} ry={0.32} className="pdbs" /> : null}
          <ellipse
            cx={b[0]}
            cy={b[1] - Math.max(0, b[2] - 1) * LIFT}
            rx={0.62 * (1 + Math.max(0, b[2] - 1) * 0.06)}
            ry={0.4 * (1 + Math.max(0, b[2] - 1) * 0.06)}
            className="pdball"
          />
        </g>
      ) : null}
    </svg>
  );
}
