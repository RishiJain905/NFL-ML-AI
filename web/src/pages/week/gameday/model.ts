// Game day (LD02): the wording and number formatting of the mockup's gameday.js, kept pure so
// it can be tested on its own. Probabilities are 0-1; a `gap` is in the same units (0.014 = 1.4
// win-probability points).

import type {
  LiveChoice,
  LiveFourth,
  LiveGame,
  LiveLabel,
  LiveTableRow,
} from '../../../api/types';
import { fmtET } from '../../../lib/format';

/** The list's query key (the same as `useLiveGames`'s): the tab label reads it from the cache. */
export const liveGamesKey = (season: number, week: number) => ['live', season, week, 'games'] as const;

export const CHOICE_ART: Record<LiveChoice, string> = { go: 'going for it', fg: 'the field goal', punt: 'punting' };
const CHOICE_SHORT: Record<LiveChoice, string> = { go: 'Go', fg: 'Field goal', punt: 'Punt' };

/** 0.733 → "73.3%" */
export const p1 = (p: number | null | undefined) => (p == null ? '—' : `${(p * 100).toFixed(1)}%`);
/** 0.733 → "73%" */
export const p0 = (p: number | null | undefined) => (p == null ? '—' : `${Math.round(p * 100)}%`);
/** a gap of 0.0139 → "1.4" (win-probability points) */
export const pts = (gap: number) => (gap * 100).toFixed(1);
/** 35 → "35 s ago", 78 → "1 min ago" */
export const ago = (s: number | null | undefined) =>
  s == null ? '—' : s < 60 ? `${Math.max(0, Math.round(s))} s ago` : `${Math.floor(s / 60)} min ago`;
export const quarter = (period: number) => (period >= 5 ? 'OT' : `Q${period}`);
export const capFirst = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

/** "NYG up 3" / "tied" / "PHI down 4", from the offense's side. */
export function leadText(g: LiveGame, offense: string | null): string {
  if (!offense) return '';
  const own = offense === g.home ? g.home_score : g.away_score;
  const opp = offense === g.home ? g.away_score : g.home_score;
  return own === opp ? 'tied' : `${offense} ${own > opp ? 'up' : 'down'} ${Math.abs(own - opp)}`;
}

/** Our pre-game pick as "ARI 55%" (the favourite's side). */
export function pregamePick(g: LiveGame): string {
  const p = g.pregame.home_win_prob;
  if (p == null) return '—';
  return p >= 0.5 ? `${g.home} ${p0(p)}` : `${g.away} ${p0(1 - p)}`;
}

/** "4th & 5 at ARI 34" → "4th & 5 · ARI 34" for a game card. */
export const cardSituation = (s: string | null) => (s ? s.replace(' at ', ' · ') : '');

/** The live board's groups, in the mockup's order: on now, later, final. */
export function groupGames(games: LiveGame[]) {
  return {
    live: games.filter((g) => g.state === 'in'),
    later: games.filter((g) => g.state === 'pre'),
    final: games.filter((g) => g.state === 'post'),
  };
}

/** The game the panel opens on in a live week: the first 3rd / 4th down, else the first game on. */
export function defaultPick(games: LiveGame[]): string | null {
  const on = games.filter((g) => g.state === 'in');
  return (on.find((g) => g.decision_down) ?? on[0])?.event ?? null;
}

export function gameLabel(g: LiveGame, kickoff: string): string {
  if (g.state === 'pre') return `${g.away} at ${g.home}, ${kickoff}`;
  const score = `${g.away} ${g.away_score} at ${g.home} ${g.home_score}`;
  if (g.state === 'post') return `${score}, final`;
  return `${score}, ${quarter(g.period)} ${g.clock ?? ''}${g.situation ? `, ${g.situation}` : ''}`;
}

export const LABEL_PIPS: Record<LiveLabel, number> = { Confident: 3, Lean: 2, 'Toss-up': 1 };

/** The plain line under the label (the guide's §5 rules: 90% / 60% of 20 refits, the 1-point floor). */
export function labelLine(f: Pick<LiveFourth, 'label' | 'gap' | 'boot_share'>): string {
  const n = f.boot_share == null ? null : Math.round(f.boot_share * 20);
  if (f.label === 'Confident')
    return f.gap >= 0.05 || n == null
      ? 'More than 5 points clear: no re-check of the models could flip it.'
      : `The call held in ${n} of 20 re-checks of the models.`;
  if (f.label === 'Lean') return `The call held in ${n ?? '—'} of 20 re-checks: likely right, not certain.`;
  if (f.gap < 0.01) return 'Under 1 point apart: either choice is defensible, and nobody should be second-guessed for it.';
  return `It flipped in ${n == null ? '—' : 20 - n} of 20 re-checks: the data can't separate them.`;
}

export const RECHECK_LINES = [
  "What's a re-check?",
  'The bot refits its yards-gained and field-goal models 20 times on resampled data.',
  'If the call comes out the same every time, the data really support it.',
];
export const LABEL_KEY_LINES = [
  'How sure the bot is',
  'Confident: the call held in at least 18 of 20 re-checks, or it is 5+ points clear',
  'Lean: it held in 12 to 17 of 20',
  'Toss-up: under 12 of 20, or the options are under 1 point apart',
];

/** The big line and the line under it on a 4th-down card. */
export function verdict(f: LiveFourth): { big: string; sub: string; toss: boolean } {
  const avail = f.options.filter((o) => o.wp != null).sort((a, b) => (b.wp ?? 0) - (a.wp ?? 0));
  const best = avail[0];
  const next = avail[1];
  const toss = f.label === 'Toss-up';
  if (!next) return { big: f.best_name, sub: 'The only option here', toss };
  if (toss) return { big: 'Toss-up', sub: `${best.name} by ${pts(f.gap)} points over ${CHOICE_ART[next.choice]}`, toss };
  return { big: f.best_name, sub: `Worth ${pts(f.gap)} more points of win chance than ${CHOICE_ART[next.choice]}`, toss };
}

/** On a Toss-up no option takes the full accent: those within a point of the best share a half one. */
export function optionTone(f: LiveFourth, choice: LiveChoice): 'best' | 'tied' | '' {
  const o = f.options.find((x) => x.choice === choice);
  if (!o || o.wp == null) return '';
  const top = Math.max(...f.options.map((x) => x.wp ?? 0));
  if (f.label === 'Toss-up') return top - o.wp < 0.01 ? 'tied' : '';
  return o.best ? 'best' : '';
}

/** The words for one if-stopped row's tooltip. */
export function rowTip(r: LiveTableRow): string[] {
  const wp = (['go', 'fg', 'punt'] as LiveChoice[])
    .filter((k) => r.wp[k] != null)
    .map((k) => `${CHOICE_SHORT[k]} ${p1(r.wp[k])}`)
    .join(' · ');
  const lines = [r.situation, `${r.best_name}${r.label ? ` · ${r.label}` : ''}, by ${pts(r.gap)} points`, wp];
  if (r.wp.fg == null) lines.push('No field goal: out of range');
  return lines;
}

export type KickBand = '<30' | '30-39' | '40-49' | '50+';
export const kickBand = (yards: number): KickBand => (yards < 30 ? '<30' : yards < 40 ? '30-39' : yards < 50 ? '40-49' : '50+');
export const distanceBand = (togo: number): '1-2' | '3-5' | '6+' => (togo <= 2 ? '1-2' : togo <= 5 ? '3-5' : '6+');
export const bandText = (b: string) => b.replace('-', '–');

/** The kicker's long: the server sends the season of his career long as `long_season` (a year). */
export function kickerLong(long: number | null, longSeason: number | null): string {
  if (longSeason != null && longSeason > 1900) return `career long ${long ?? '—'} (${longSeason})`;
  return `long ${long ?? '—'} (career) · ${longSeason ?? '—'} this season`;
}

/** Seconds since the last play, as of now: the answer's age plus the time since this browser got
 *  it (`receivedAt`, the query's dataUpdatedAt; not ESPN's clock, which a replay sets in the past). */
export function lastPlayAge(ageS: number | null | undefined, receivedAt: number | null, nowMs: number): number | null {
  if (ageS == null) return null;
  return receivedAt ? ageS + Math.max(0, (nowMs - receivedAt) / 1000) : ageS;
}

/** The newer of two ISO times (the list's and the call's copy of a game). */
export function newer(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a) return false;
  if (!b) return true;
  return new Date(a).getTime() > new Date(b).getTime();
}

/** Times in US Eastern: "3:57:45 PM", "3:57 PM", "Sun 4:25 PM". */
export const tSec = (iso: string | null | undefined) =>
  fmtET(iso, { hour: 'numeric', minute: '2-digit', second: '2-digit' });
export const tMin = (iso: string | null | undefined) => fmtET(iso, { hour: 'numeric', minute: '2-digit' });
export const tKick = (iso: string | null | undefined) =>
  fmtET(iso, { weekday: 'short', hour: 'numeric', minute: '2-digit' });

