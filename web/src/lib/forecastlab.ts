// Small helpers for the Availability and Outcomes week tabs (FL02, D158).

import { kickoffLabel } from './format';

/** "Tue Nov 10, 9:42 AM ET" (a missing or bad time reads "—"). */
export const madeAt = (iso: string | null | undefined) => (iso ? kickoffLabel(iso) : '—');

/** A server hint split into its words and the command between backticks ("... ships
 *  (`nfl forecastlab backtest ...`)." -> "... ships." and the command). */
export function splitCmd(hint: string | null): { text: string; cmd: string | null } {
  const msg = hint ?? '';
  const cmd = /`([^`]+)`/.exec(msg)?.[1] ?? null;
  return { text: msg.replace(/\s*\(?`[^`]+`\)?/g, '').replace(/:\s*$/, '.').trim(), cmd };
}

// ---- The Outcomes tab: a curve's arithmetic and its words (mockup: outcomes.js) ---------------
// A curve is whole numbers: `cdf[i]` = P(Y <= grid_lo + i); below `grid_lo` it's 0, past the
// list's end it's 1 (the list stops at the first 1.0). Marks are x.5: "more than 49.5" (amounts)
// reads "50 or more" for counts. The chances match the reader's (curves.py `p_over`,
// `band_prob`, `bell_cdf`).

export interface CurveLike {
  grid_lo: number;
  cdf: number[];
}
/** The fields of a row the simple curve needs. */
export interface ProjectionLike {
  kind: string | null; // amount | count
  p10: number | null;
  p50: number | null;
  p90: number | null;
  mean: number | null;
}
export type Cdf = (k: number) => number;

/** P(Y <= k) of a curve, on whole numbers. */
export function cdfOf(c: CurveLike): Cdf {
  return (k) => {
    const i = Math.floor(k) - c.grid_lo;
    if (i < 0) return 0;
    return i >= c.cdf.length ? 1 : (c.cdf[i] ?? 1);
  };
}

/** P10-P90 of a normal spans 2.563 sigma (curves.py `BELL_Z`). */
const BELL_Z = 2.5631031310892007;

function erf(x: number): number {
  // Abramowitz and Stegun 7.1.26: accurate to 1.5e-7
  const s = Math.sign(x);
  const a = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * a);
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-a * a);
  return s * y;
}
const normal = (z: number) => 0.5 * (1 + erf(z / Math.SQRT2));

/** The simple curve, the hand method (curves.py `bell_cdf`): yardage a bell curve on P50 through
 *  P10 / P90, P(Y <= k) = Phi((k + 0.5 - P50) / sigma); counts a Poisson at the mean. Nothing
 *  below the stat's floor `lo`. */
export function simpleCdf(r: ProjectionLike, lo: number): Cdf {
  if (r.kind === 'count') {
    const mu = Math.max(r.mean ?? 0, 0);
    return (k) => {
      const n = Math.floor(k);
      if (n < lo || n < 0) return 0;
      let term = Math.exp(-mu);
      let sum = term;
      for (let j = 1; j <= n; j++) {
        term *= mu / j;
        sum += term;
      }
      return Math.min(1, sum);
    };
  }
  const sigma = Math.max(((r.p90 ?? 0) - (r.p10 ?? 0)) / BELL_Z, 1e-6);
  const p50 = r.p50 ?? 0;
  return (k) => (Math.floor(k) < lo ? 0 : normal((Math.floor(k) + 0.5 - p50) / sigma));
}

/** The chance of "more than x" (yardage) or "x or more" (counts). */
export function chanceAbove(F: Cdf, kind: string | null, x: number): number {
  return kind === 'count' ? 1 - F(Math.ceil(x) - 1) : 1 - F(Math.floor(x));
}

/** The chance of landing between a and b, both included (whole numbers). */
export function chanceBetween(F: Cdf, a: number, b: number): number {
  return Math.max(0, F(Math.floor(b)) - F(Math.ceil(a) - 1));
}

/** The projection a band is centred on (curves.py: round(P50); counts: round(mean)). */
export function centreOf(r: ProjectionLike): number {
  return Math.floor(((r.kind === 'count' ? r.mean : r.p50) ?? 0) + 0.5); // round half up
}

/** The projection shown: yardage the median, counts the mean. */
export const projectionOf = (r: ProjectionLike): number | null => (r.kind === 'count' ? r.mean : r.p50);

/** "77%", "<1%", ">99%". */
export function pctText(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return '—';
  if (p < 0.005) return '<1%';
  if (p > 0.995) return '>99%';
  return `${Math.round(p * 100)}%`;
}

/** "about 1 in 9", "about 8 in 10". */
export function oneIn(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p) || p <= 0) return '';
  if (p < 0.005) return 'less than 1 in 100';
  if (p < 0.45) return `about 1 in ${Math.round(1 / p)}`;
  if (p > 0.95) return 'almost always';
  return `about ${Math.round(p * 10)} in 10`;
}

/** A standard mark in words: "more than 49.5" (yardage), "5 or more" (counts). */
export const markWords = (kind: string | null, mark: number) => (kind === 'count' ? `${Math.ceil(mark)} or more` : `more than ${mark}`);

/** Where a curve's chart starts and ends: the numbers that hold all but about 0.4% each side
 *  (yardage starts at 0 when the curve gets near it). */
export function chartDomain(c: CurveLike, kind: string | null): [number, number] {
  const F = cdfOf(c);
  let lo = c.grid_lo;
  let hi = c.grid_lo + c.cdf.length - 1;
  while (lo < hi && F(lo) < 0.004) lo++;
  while (hi > lo && F(hi - 1) > 0.996) hi--;
  if (kind !== 'count' && c.grid_lo <= 0 && lo > 0 && (F(0) >= 0.004 || lo < 10)) lo = 0;
  return [lo, hi];
}

/** Round chart ticks between lo and hi (about n of them). */
export function niceTicks(lo: number, hi: number, n = 5): number[] {
  const raw = (hi - lo) / n || 1;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? p * 10;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 100) / 100);
  return out;
}

/** "yards" for the yardage stats, the stat itself for counts. */
export const unitOf = (t: { kind: string; label: string }) => (t.kind === 'count' ? t.label : 'yards');

/** The number the "chance of more than" box opens on: the stat's standard mark nearest the
 *  player's projection (a low-projection tight end opens on 14.5, a star receiver on 74.5), so the
 *  first chance he reads is about him. */
export function defaultAbove(t: { kind: string; marks: number[] }, projection: number | null = null): number {
  const marks = t.marks.length ? t.marks : [0];
  const m =
    projection == null
      ? marks[Math.floor(marks.length / 2)]
      : marks.reduce((best, x) => (Math.abs(x - projection) < Math.abs(best - projection) ? x : best), marks[0]);
  return t.kind === 'count' ? Math.ceil(m) : m;
}

/** A band in words: "51–65", or "−5 to 9" when it starts below zero. */
export function spanText(a: number, b: number): string {
  return a < 0 ? `−${Math.abs(a)} to ${b}` : `${a}–${b}`;
}

/** Of a scoreboard row's three values (the curve, the simple curve, his own games), the lowest,
 *  only when it's clearly the lowest (2% under the next): a 0.1-yard gap should look like nothing. */
export function clearBest<K extends string>(row: Partial<Record<K, unknown>>, keys: K[]): K | null {
  const vals = keys
    .map((k) => ({ k, v: row[k] }))
    .filter((x): x is { k: K; v: number } => typeof x.v === 'number')
    .sort((a, b) => a.v - b.v);
  return vals.length > 1 && vals[0].v <= vals[1].v * 0.98 ? vals[0].k : null;
}

/** Rows by the projection shown (counts: the mean; the reader sorts on P50, which ties counts at
 *  whole numbers), biggest first; ties keep the reader's order. */
export function byProjection<T extends ProjectionLike>(rows: T[]): T[] {
  return rows
    .map((r, i) => ({ r, i, p: projectionOf(r) ?? -Infinity }))
    .sort((a, b) => b.p - a.p || a.i - b.i)
    .map((x) => x.r);
}

// ---- The Availability tab's words (mockup: availability.js) -----------------------------------

/** A share (0-1) as a whole percent: 100% is a real share (a chance uses pctText). */
export const shareText = (x: number | null | undefined) => (x == null ? '—' : `${Math.round(x * 100)}%`);

/** A change of share in points: "+16 points", "−27 points", "+1 point". */
export function ptsText(d: number | null | undefined): string {
  if (d == null) return '—';
  const n = Math.abs(Math.round(d * 100));
  return `${d >= 0 ? '+' : '−'}${n} point${n === 1 ? '' : 's'}`;
}

export const SHARE_KIND: Record<string, string> = { snaps: 'of the snaps', targets: 'of the targets', carries: 'of the carries' };
export const PRACTICE_WORDS: Record<string, string> = { full: 'full practice', limited: 'limited practice', dnp: 'did not practice' };
export const ROSTER_WORDS: Record<string, string> = { RES: 'reserve list', DEV: 'practice squad', INA: 'inactive' };

/** "MIN at GB" / "DET vs NE". */
export const whereText = (r: { team: string | null; opponent: string | null; home: boolean | null }) =>
  `${r.team ?? ''} ${r.home ? 'vs' : 'at'} ${r.opponent ?? ''}`;

/** "If he sits" gains joined per teammate (one player can gain snaps and targets). */
export function inheritByPlayer<T extends { player_id: string }>(gains: T[]): { first: T; gains: T[] }[] {
  const out: { first: T; gains: T[] }[] = [];
  for (const g of gains) {
    const e = out.find((x) => x.first.player_id === g.player_id);
    if (e) e.gains.push(g);
    else out.push({ first: g, gains: [g] });
  }
  return out;
}
