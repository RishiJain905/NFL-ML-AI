// Play calling (PC01): how a tendency is written and explained (mockup: fmt, fmtDiff, ord,
// cellTip, divFill in documentation/play-calling/mockup/playcalling.js). Shares are 0-1 and shown
// as %; PROE (`over_expected`) is 0-1 too and shown x100 as signed points ("+3.7"), compared with
// the league (about -2), never with 0; means (aDOT, rushers, box) keep their unit.

import type { CSSProperties } from 'react';
import type { PlaycallCell, PlaycallMetric, PlaycallSide, PlaycallWindow } from '../api/types';

type Fmt = Pick<PlaycallMetric, 'unit' | 'digits'>;

const MINUS = '−';
const signedNum = (x: number, d: number) =>
  `${x > 0 ? '+' : x < 0 ? MINUS : ''}${Math.abs(x).toFixed(d)}`;

/** A metric's value in its unit. */
export function fmtRate(m: Fmt, v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  if (m.unit === 'share') return `${(v * 100).toFixed(m.digits)}%`;
  if (m.unit === 'over_expected') return signedNum(v * 100, m.digits);
  return v.toFixed(m.digits);
}

/** A difference from the league: points for shares and PROE, the unit for means. */
export function fmtDiff(m: Fmt, d: number | null | undefined): string {
  if (d == null || !Number.isFinite(d)) return '—';
  if (m.unit === 'mean') return signedNum(d, m.digits);
  return `${signedNum(d * 100, m.digits)} pts`;
}

/** 1 → "1st", 12 → "12th", 88.4 → "88th". */
export function ordinal(p: number): string {
  const n = Math.round(p);
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : (['th', 'st', 'nd', 'rd'][n % 10] ?? 'th');
  return `${n}${s}`;
}

const PLURAL: Record<string, string> = {
  play: 'plays',
  dropback: 'dropbacks',
  attempt: 'attempts',
  'designed run': 'designed runs',
  snap: 'snaps',
  target: 'targets',
};
/** "dropback" / "dropbacks". */
export function perWord(per: string, n: number): string {
  return n === 1 ? per : (PLURAL[per] ?? `${per}s`);
}

export const WINDOW_LABEL: Record<PlaycallWindow, string> = {
  season: 'Season',
  last4: 'Last 4',
  last_season: 'Last season',
};

export const ALLOWED_NOTE =
  'What offenses do against this defense depends on the offenses it faced (PC02 adjusts for it).';

/** The tooltip note for a defense's "allowed" rate (an offense's call, read on the defense's page). */
export function allowedNote(m: Pick<PlaycallMetric, 'caller'>, side: PlaycallSide): string | null {
  return side === 'defense' && m.caller === 'offense' ? ALLOWED_NOTE : null;
}

type TipMetric = Pick<PlaycallMetric, 'label' | 'unit' | 'digits' | 'per' | 'help'>;

/** A cell's tooltip lines: the number with n, the league, the percentile ("more of it", not
 *  "better"), a small-sample warning, then any note and the metric's help. */
export function cellTip(
  m: TipMetric,
  c: PlaycallCell | null | undefined,
  who: string,
  where: string | null,
  minN: number,
  note?: string | null,
): string[] {
  const lines = [`${m.label}${where ? ` · ${where}` : ''}`];
  if (!c || c.value == null) {
    lines.push(`${who}: no ${perWord(m.per, 2)} yet`);
    return lines;
  }
  const games = c.games != null ? `, ${c.games} game${c.games === 1 ? '' : 's'}` : '';
  lines.push(`${who}: ${fmtRate(m, c.value)} (${c.n} ${perWord(m.per, c.n)}${games})`);
  if (c.league != null) lines.push(`League: ${fmtRate(m, c.league)} · ${fmtDiff(m, c.diff)}`);
  if (c.pct != null)
    lines.push(`${ordinal(c.pct)} percentile: more of it than ${Math.round(c.pct)}% of teams (not "better")`);
  if (c.small) lines.push(`Small sample: under ${minN} ${perWord(m.per, 2)}, not a tendency yet`);
  if (note) lines.push(note);
  if (m.help) lines.push(m.help);
  return lines;
}

/** The diverging tint for "more / less than the league": --s2 (less) → the panel → --s1 (more).
 *  Small samples and missing values get no tint (they're hatched instead). */
export function divTint(
  m: Pick<PlaycallMetric, 'unit'>,
  c: PlaycallCell | null | undefined,
): CSSProperties | undefined {
  if (!c || c.value == null || c.diff == null || c.small) return undefined;
  const cap = m.unit === 'mean' ? Math.max(0.05, Math.abs(c.league ?? 1) * 0.2) : 0.12;
  const t = Math.max(-1, Math.min(1, c.diff / cap));
  const amount = Math.round(Math.abs(t) * 62);
  if (amount < 4) return undefined;
  return { background: `color-mix(in srgb, var(${t > 0 ? '--s1' : '--s2'}) ${amount}%, var(--panel))` };
}

/** Drawn from the league tick, not from 0: PROE (the league sits near -2 points), and means that
 *  sit near 0 or below (EPA per play). */
export function centred(m: Pick<PlaycallMetric, 'unit'>, v: number, league: number | null): boolean {
  return m.unit === 'over_expected' || (m.unit === 'mean' && (Math.abs(league ?? 0) < 0.5 || v < 0));
}

/** A round top for a 0-based scale (0.27 → 0.3, 7.7 → 8). */
export function niceTop(v: number): number {
  if (!(v > 0)) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const f = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => s * mag >= v - 1e-12) ?? 10;
  return Number((f * mag).toPrecision(12));
}

/** A shared scale: `top` for shares and means (0..top), `span` for PROE (league ± span). */
export interface RateDomain {
  top?: number;
  span?: number;
}

/** One scale for a column of cells, so their bars compare (the teams grid). */
export function rateDomain(m: Pick<PlaycallMetric, 'unit'>, cells: (PlaycallCell | null | undefined)[]): RateDomain {
  const got = cells.filter((c): c is PlaycallCell => c != null && c.value != null);
  if (centred(m, 0, got[0]?.league ?? null)) {
    return { span: Math.max(0.05, ...got.map((c) => Math.abs((c.value as number) - (c.league ?? 0)))) * 1.08 };
  }
  const top = Math.max(0, ...got.map((c) => Math.max(c.value as number, c.league ?? 0)));
  return { top: m.unit === 'share' ? Math.min(1, niceTop(top)) : niceTop(top) };
}

// ---- the Offense / Defense switch, remembered in this browser (every storage call wrapped) ----

export const SIDE_KEY = 'cr.playcallSide';

export function readSide(): PlaycallSide {
  try {
    return localStorage.getItem(SIDE_KEY) === 'defense' ? 'defense' : 'offense';
  } catch {
    return 'offense';
  }
}

export function writeSide(side: PlaycallSide): void {
  try {
    localStorage.setItem(SIDE_KEY, side);
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
}
