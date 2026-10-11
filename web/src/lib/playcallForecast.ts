// Play calling's forecast (PC02; mockup: the "PC02: the forecast" block of
// documentation/play-calling/mockup/playcalling.js): how a forecast, its range, its pieces and its
// grade are written. "Don't oversell": every forecast is shown with its 80% range and the team's
// own rate so far (the baseline it has to beat). Units: shares in % (1 decimal), PROE in signed
// points, pass rushers with 2 decimals; a change or a miss is in points for shares and PROE.

import type { PlayCallsResponse, PlaycallForecastMeta, PlaycallForecastValue, PlaycallTarget } from '../api/types';
import { fmtRate, perWord } from './playcall';

type Fmt = Pick<PlaycallTarget, 'unit' | 'digits'>;
type Ctx = Pick<PlaycallTarget, 'context'>;

const MINUS = '−';
const signedNum = (x: number, d: number) => {
  const r = Number(x.toFixed(d));
  return `${r > 0 ? '+' : r < 0 ? MINUS : ''}${Math.abs(r).toFixed(d)}`;
};

/** The targets whose forecast includes the game script (the spread and the total). */
export const SCRIPT_TARGETS = ['proe', 'dropback_rate'];

/** A change in a target's unit: "+2.7 pts" for shares and PROE, "−0.23" for rushers. */
export function fmtChange(t: Fmt, d: number | null | undefined): string {
  if (d == null || !Number.isFinite(d)) return '—';
  if (t.unit === 'mean') return signedNum(d, t.digits); // rounded first: never "−0.00"
  return `${signedNum(d * 100, 1)} pts`;
}

/** A miss (MAE) in a target's unit: "5.7 pts" for shares and PROE, "0.16 rushers". */
export function fmtErr(t: Fmt & { target?: string; metric?: string }, e: number | null | undefined): string {
  if (e == null || !Number.isFinite(e)) return '—';
  if (t.unit === 'mean') return `${e.toFixed(t.digits)}${(t.target ?? t.metric) === 'rushers_avg' ? ' rushers' : ''}`;
  return `${(e * 100).toFixed(1)} pts`;
}

/** A 0-1 share as a whole percent ("81%"). */
export const pctText = (x: number | null | undefined) => (x == null || !Number.isFinite(x) ? '—' : `${Math.round(x * 100)}%`);

/** The baseline's name: the season so far, or last season's rate before a team's first game. */
export const soFarName = (v: Pick<PlaycallForecastValue, 'games_so_far'>) => (v.games_so_far === 0 ? 'last season' : 'so far');
export const soFarLabel = (v: Pick<PlaycallForecastValue, 'games_so_far'>) => (v.games_so_far === 0 ? 'Last season' : 'Season so far');

export type ForecastStatus = Exclude<PlaycallForecastValue['status'], 'graded' | null>;
export const STATUS: Record<ForecastStatus, { tone: 'warn' | 'flat' | 'ghost'; text: string; short: string; tip: string }> = {
  waiting_ftn: {
    tone: 'warn',
    text: 'FTN not in yet',
    short: 'waiting for FTN',
    tip: 'FTN charts a game about two days after it is played: graded once it lands.',
  },
  waiting_build: {
    tone: 'flat',
    text: 'Waiting for the tables',
    short: 'played, waiting for the tables',
    tip: 'Played; graded after the next nfl playcalling build and nfl playcalling grade.',
  },
  not_played: { tone: 'flat', text: 'Not played yet', short: 'not played yet', tip: 'Graded after the game.' },
  no_plays: { tone: 'ghost', text: 'No plays to grade', short: 'with no plays to grade', tip: 'The game had none of the plays this rate counts.' },
};

/** "12 waiting for FTN · 3 not played yet" over a set of forecasts. */
export function statusCounts(values: Pick<PlaycallForecastValue, 'status'>[]): string {
  const c = new Map<ForecastStatus, number>();
  for (const v of values) {
    if (v.status && v.status !== 'graded') c.set(v.status, (c.get(v.status) ?? 0) + 1);
  }
  return [...c].map(([k, n]) => `${n} ${STATUS[k].short}`).join(' · ');
}

/** The second piece's name: the opponent, or (run direction) the home team's stats crew. */
export function contextName(t: Ctx, v: Pick<PlaycallForecastValue, 'context_team'>): string {
  if (t.context === 'venue') return `the home team's stats crew (${v.context_team ?? 'home'})`;
  return `the opponent${v.context_team ? ` (${v.context_team})` : ''}`;
}
export function contextShort(t: Ctx, v: Pick<PlaycallForecastValue, 'context_team'>): string {
  return t.context === 'venue' ? `crew ${v.context_team ?? ''}`.trim() : 'opp';
}

/** A forecast's tooltip: the forecast and its range, the baseline, the league, the pieces (the
 *  second named for the venue's stats crew on run direction; the target's help says why), the grade. */
export function forecastTip(t: PlaycallTarget, v: PlaycallForecastValue): string[] {
  const lines = [`${t.label} · ${v.team} vs ${v.opponent}`];
  lines.push(`Forecast: ${fmtRate(t, v.forecast)} (80% range ${fmtRate(t, v.lo)} to ${fmtRate(t, v.hi)})`);
  const sample = v.games_so_far
    ? ` (${v.n_so_far} ${perWord(t.per, v.n_so_far)}, ${v.games_so_far} game${v.games_so_far === 1 ? '' : 's'})`
    : '';
  lines.push(`${soFarLabel(v)}: ${fmtRate(t, v.season_to_date)}${sample}${v.small ? ' · small sample' : ''}`);
  lines.push(`Change from ${soFarName(v)}: ${fmtChange(t, v.shift)}`);
  if (v.league != null) lines.push(`League: ${fmtRate(t, v.league)}`);
  const script = SCRIPT_TARGETS.includes(v.target) ? `, the game script ${fmtChange(t, v.script_effect)}` : '';
  lines.push(
    `Pieces: ${v.team}'s rate shrunk toward last season and the league ${fmtRate(t, v.team_term)}, ${contextName(t, v)} ${fmtChange(t, v.opponent_effect)}${script}`,
  );
  if (v.status === 'graded') {
    const n = v.n != null ? ` (${v.n} ${perWord(t.per, v.n)})` : '';
    lines.push(`Actual: ${fmtRate(t, v.actual)}${n} · ${v.in_range ? 'inside' : 'outside'} the range`);
    lines.push(v.beat_season_to_date ? `The forecast was closer than ${soFarName(v)}` : `${soFarLabel(v)} was closer (or as close)`);
  } else if (v.status) {
    lines.push(`${STATUS[v.status].text}: ${STATUS[v.status].tip}`);
  }
  if (t.help) lines.push(t.help);
  return lines;
}

export interface StripDomain {
  lo: number;
  hi: number;
}

/** One scale per target over a set of forecasts, so a target's rows compare across games. */
export function forecastDomains(values: PlaycallForecastValue[]): Record<string, StripDomain> {
  const by: Record<string, number[]> = {};
  for (const v of values) {
    const xs = [v.lo, v.hi, v.forecast, v.season_to_date, v.league, v.actual].filter(
      (x): x is number => x != null && Number.isFinite(x),
    );
    (by[v.target] ??= []).push(...xs);
  }
  const out: Record<string, StripDomain> = {};
  for (const [k, xs] of Object.entries(by)) {
    if (!xs.length) continue;
    const lo = Math.min(...xs);
    const hi = Math.max(...xs);
    const pad = (hi - lo) * 0.05 || 0.01;
    out[k] = { lo: lo - pad, hi: hi + pad };
  }
  return out;
}

/** A hint ("No forecast for week 6 yet: `cmd` (after …).") split into its text, command and tail. */
export function splitHint(hint: string): { text: string; cmd: string | null; tail: string | null } {
  const parts = hint.split('`');
  const text = (parts[0] ?? '').replace(/:\s*$/, '.').trim();
  const cmd = parts.length >= 3 ? (parts[1] ?? null) : null;
  const tailRaw = parts.length >= 3 ? parts.slice(2).join('`') : '';
  const tail = tailRaw.replace(/`/g, '').replace(/^\s*\(|\)?\.?\s*$/g, '').trim() || null;
  return { text, cmd, tail };
}

/** The targets of a forecast, by key. */
export const targetMap = (meta: Pick<PlaycallForecastMeta, 'targets'> | null | undefined) =>
  new Map((meta?.targets ?? []).map((t) => [t.target, t]));

/** Every forecast of a week answer. */
export const weekValues = (r: PlayCallsResponse) => r.games.flatMap((g) => g.matchups.flatMap((mu) => mu.forecast));
