// PC03: the geometry helpers behind the diagram player (mockup: the "geometry helpers" block of
// documentation/play-calling/mockup/playcalling.js). A diagram's coordinates are yards: x from the
// offense's own goal line (0) to the opponent's (100), end zones -10..0 / 100..110, the offense
// moving to +x; y from the offense's LEFT sideline (0) to its right (53.33). The SVG draws x to the
// right and y down. Keys are [t, x, y] (the ball [t, x, y, h]): linear between keys, the first key
// before the first time, the last key held after the last time. Pure functions only.

import type { DiagramBallKey, DiagramField, DiagramKey, DiagramMark, DiagramPhase, PlayDiagram, PlaySummary } from '../api/types';

export const EPS = 1e-6;
/** How far a ball in the air is drawn above its shadow, per yard of height above 1. */
export const LIFT = 0.45;
/** The narrowest x range drawn: most views are 32 yards, a tall sliver when x runs to the right. */
export const MIN_SPAN = 50;
/** The margin above and below the sidelines (the LOS / line-to-gain labels, the label tag). */
export const SIDE_MARGIN = 3.2;

/** Linear interpolation over keys [t, ...values]; the first key before, the last key held after. */
export function lerpKeys(keys: readonly (readonly number[])[], t: number): number[] | null {
  if (!keys.length) return null;
  if (t <= keys[0][0]) return keys[0].slice(1);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (t < b[0]) {
      const f = b[0] > a[0] ? (t - a[0]) / (b[0] - a[0]) : 1;
      return a.slice(1).map((v, j) => v + (b[j + 1] - v) * f);
    }
  }
  return keys[keys.length - 1].slice(1);
}

/** A player's [x, y] at time t. */
export function positionAt(keys: readonly DiagramKey[], t: number): [number, number] {
  const p = lerpKeys(keys, t);
  return p ? [p[0], p[1]] : [0, 0];
}

/** The ball's [x, y, h] at time t (null when the diagram has no ball keys). */
export function ballAt(ball: readonly DiagramBallKey[], t: number): [number, number, number] | null {
  const p = lerpKeys(ball, t);
  return p ? [p[0], p[1], p[2]] : null;
}

/** The phase showing at time t: the last one that has started (the first before any). */
export function phaseAt(phases: readonly DiagramPhase[], t: number): DiagramPhase | null {
  let p: DiagramPhase | null = phases[0] ?? null;
  for (const ph of phases) if (ph.t <= t + EPS) p = ph;
  return p;
}

/** One step: forward to the next phase's start (the end after the last phase), back to the previous
 *  phase's start (0 before the first). */
export function stepTime(dg: Pick<PlayDiagram, 'phases' | 'duration'>, t: number, dir: 1 | -1): number {
  const ph = phaseAt(dg.phases, t);
  const i = ph ? dg.phases.indexOf(ph) : -1;
  if (dir > 0) return i + 1 < dg.phases.length ? dg.phases[i + 1].t : dg.duration;
  return i > 0 ? dg.phases[i - 1].t : 0;
}

/** Where the player rests under reduced motion: the last phase, every path drawn. */
export function restTime(dg: Pick<PlayDiagram, 'phases' | 'duration'>): number {
  return dg.phases.length ? dg.phases[dg.phases.length - 1].t : dg.duration;
}

/** The marks on the field at time t (each appears at its `t` and stays). */
export function marksAt(marks: readonly DiagramMark[], t: number): DiagramMark[] {
  return marks.filter((m) => m.t <= t + EPS);
}

/** The next phase's start after t0 (the duration when there's none): a mark's drawing window. */
export function nextPhaseT(dg: Pick<PlayDiagram, 'phases' | 'duration'>, t0: number): number {
  const n = dg.phases.find((p) => p.t > t0 + EPS);
  return n ? n.t : dg.duration;
}

/** When the play is over on the field: the end / incomplete / interception / sack mark's time. */
export function endTime(dg: Pick<PlayDiagram, 'marks' | 'duration'>): number {
  const e = dg.marks.find((m) => m.kind === 'end' || m.kind === 'incomplete' || m.kind === 'interception' || m.kind === 'sack');
  return e ? e.t : dg.duration;
}

/** The snap: a reconstruction's `snap` phase or a real clip's `ball_snap` event. */
export function snapTime(dg: Pick<PlayDiagram, 'phases'>): number {
  const s = dg.phases.find((p) => p.name === 'snap' || p.name === 'ball_snap');
  return s ? s.t : 0;
}

/** The carrier has the ball once, after the snap, the ball is on the ground at his spot (works for
 *  a reconstruction's phases and a real clip's tracking events alike). */
export function hasBall(dg: Pick<PlayDiagram, 'phases' | 'ball'>, x: number, y: number, t: number): boolean {
  if (t <= snapTime(dg) + 0.25) return false;
  const b = ballAt(dg.ball, t);
  return !!b && b[2] < 2.2 && Math.hypot(b[0] - x, b[1] - y) < 1.6;
}

/** The x range to draw: `field.view` widened to MIN_SPAN around its centre, inside -10..110. */
export function viewRange(field: Pick<DiagramField, 'view'>): [number, number] {
  let [a, b] = field.view;
  const span = Math.max(b - a, MIN_SPAN);
  const c = (a + b) / 2;
  a = c - span / 2;
  b = c + span / 2;
  if (a < -10) {
    b += -10 - a;
    a = -10;
  }
  if (b > 110) {
    a -= b - 110;
    b = 110;
  }
  return [Math.max(-10, a), Math.min(110, b)];
}

/** The SVG viewBox (in yards): the x range, and the field's width with a margin on both sides. */
export function viewBox(field: Pick<DiagramField, 'view' | 'width'>): { x: number; y: number; w: number; h: number } {
  const [x0, x1] = viewRange(field);
  return { x: x0, y: -SIDE_MARGIN, w: x1 - x0, h: field.width + 2 * SIDE_MARGIN };
}

export type Pt = [number, number];

/** Where a player has been up to t: the keys before t, then where he is now. */
export function trailOf(keys: readonly DiagramKey[], t: number): Pt[] {
  const pts: Pt[] = keys.filter((k) => k[0] < t).map((k) => [k[1], k[2]]);
  return pts.concat([positionAt(keys, t)]);
}

export function pathLength(pts: readonly Pt[]): number {
  let s = 0;
  for (let i = 1; i < pts.length; i++) s += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return s;
}

/** The first `frac` (0-1) of a polyline, by length. */
export function partialPath(pts: readonly Pt[], frac: number): Pt[] {
  if (frac >= 1 || pts.length < 2) return [...pts];
  const want = pathLength(pts) * Math.max(0, frac);
  const out: Pt[] = [pts[0]];
  let s = 0;
  for (let i = 1; i < pts.length; i++) {
    const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    if (s + d >= want) {
      const f = d ? (want - s) / d : 0;
      out.push([pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f]);
      return out;
    }
    s += d;
    out.push(pts[i]);
  }
  return out;
}

/** How far into its drawing window [a, b] a mark is at t (0-1). */
export const progress = (t: number, a: number, b: number) => Math.max(0, Math.min(1, (t - a) / Math.max(0.05, b - a)));

/** A throw's arc up to t: from the release to the ball's position, each point lifted by its height. */
export function throwArc(dg: Pick<PlayDiagram, 'ball'>, m: DiagramMark, t: number): { pts: Pt[]; done: boolean } {
  const [a, b] = m.points;
  const keys = dg.ball.filter((k) => k[0] >= m.t - EPS);
  const catchKey = b ? keys.find((k) => Math.abs(k[1] - b[0]) < 0.05 && Math.abs(k[2] - b[1]) < 0.05) : undefined;
  const tEnd = catchKey ? catchKey[0] : keys.length ? keys[keys.length - 1][0] : m.t;
  const lift = (x: number, y: number, h: number): Pt => [x, y - Math.max(0, h - 1) * LIFT];
  const pts: Pt[] = [[a[0], a[1]]];
  for (const k of keys) if (k[0] <= Math.min(t, tEnd) + EPS) pts.push(lift(k[1], k[2], k[3]));
  if (t < tEnd) {
    const bb = ballAt(dg.ball, t);
    if (bb) pts.push(lift(bb[0], bb[1], bb[2]));
  }
  return { pts, done: t >= tEnd };
}

/** The yard number painted at x (distance to the nearer goal line). */
export const yardNumber = (x: number) => Math.round(x <= 50 ? x : 100 - x);

export const pointsAttr = (pts: readonly Pt[]) => pts.map((p) => `${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(' ');

// ---- the play browser's words ------------------------------------------------------------------

export const PLAY_TYPE_NAME: Record<PlaySummary['type'], string> = {
  run: 'Run',
  pass: 'Pass',
  sack: 'Sack',
  scramble: 'Scramble',
  aborted: 'Aborted',
  dropback: 'Dropback',
};
export const PLAY_TAG_NAME: Record<PlaySummary['tags'][number], string> = {
  play_action: 'PA',
  screen: 'Screen',
  rpo: 'RPO',
  deep_shot: 'Deep',
  motion: 'Motion',
  no_huddle: 'No huddle',
  trick_play: 'Trick',
  fake_kick: 'Fake kick',
  direct_snap: 'Direct snap',
};
export const PLAY_TAG_HELP: Record<PlaySummary['tags'][number], string> = {
  play_action: 'Play-action (FTN)',
  screen: 'Screen (FTN)',
  rpo: 'Run-pass option (FTN)',
  deep_shot: 'Deep shot: 20+ air yards',
  motion: 'Motion before the snap (FTN)',
  no_huddle: 'No huddle',
  trick_play: 'Trick play (FTN)',
  fake_kick: 'Fake punt or field goal',
  direct_snap: 'Direct snap',
};
export const DOWN_NAME: Record<number, string> = { 1: '1st', 2: '2nd', 3: '3rd', 4: '4th' };

const MINUS = '−';
/** EPA, signed to two decimals ("+1.73", "−0.57", "0.00"). */
export function epaText(e: number | null | undefined): string {
  if (e == null || !Number.isFinite(e)) return '—';
  const r = Number(e.toFixed(2));
  return `${r > 0 ? '+' : r < 0 ? MINUS : ''}${Math.abs(r).toFixed(2)}`;
}

export const playKey = (p: Pick<PlaySummary, 'game_id' | 'play_id'>) => `${p.game_id}/${p.play_id}`;

/** The OS's reduced-motion setting (false when it can't be read). */
export function prefersReducedMotion(): boolean {
  try {
    return typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}
