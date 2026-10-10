// The decision review (LD03; mockup: documentation/live-decisions/mockup/review.js): wording and
// number formatting, kept pure so they can be tested on their own. Probabilities are 0-1; `edge`,
// `cost`, `gap` and `wp_lost` are in the same units (0.049 = 4.9 win-probability points; a sum
// of them = expected wins).

import type { LiveChoice, ReviewCoachRow, ReviewPlay } from '../../../api/types';
import { p0, pts, quarter } from './model';

/** The coach's call, short ("Go") and the bot's, long ("Go for it"). */
export const CALL_SHORT: Record<LiveChoice, string> = { go: 'Go', fg: 'Field goal', punt: 'Punt' };
export const CALL_LONG: Record<LiveChoice, string> = { go: 'Go for it', fg: 'Field goal', punt: 'Punt' };
export const CHOICES: LiveChoice[] = ['go', 'fg', 'punt'];
/** Go / field goal / punt as series colours (the week view's "who calls what" bars only). */
export const CALL_COLOR: Record<LiveChoice, string> = { go: 'var(--s1)', fg: 'var(--s2)', punt: 'var(--s3)' };
/** The edge bar is full at 6 points. */
export const EDGE_MAX = 0.06;

const PLAYOFF: Record<number, [string, string, string]> = {
  19: ['Wild card', 'WC', 'the wild-card round'],
  20: ['Divisional round', 'DIV', 'the divisional round'],
  21: ['Conference title games', 'CONF', 'the conference title games'],
  22: ['Super Bowl', 'SB', 'the Super Bowl'],
};
// 18 regular-season weeks from 2021 (17 before): the weeks after them are the playoffs
const playoff = (season: number, week: number) => PLAYOFF[week + (season >= 2021 ? 0 : 1)];
/** "Week 4" / "Super Bowl". */
export const weekName = (season: number, week: number) => playoff(season, week)?.[0] ?? `Week ${week}`;
/** A chart axis label: "4" / "SB". */
export const weekShort = (season: number, week: number) => playoff(season, week)?.[1] ?? String(week);
/** "week 4" / "the Super Bowl", after "through". */
export const throughText = (season: number, week: number) => playoff(season, week)?.[2] ?? `week ${week}`;
/** The season's last week (the Super Bowl). */
export const lastWeek = (season: number) => (season >= 2021 ? 22 : 21);

/** 1.3434 → "1.34" expected wins. */
export const wins = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2));
/** An edge in points with its sign: -0.0493 → "−4.9", 0.051 → "+5.1". */
export const signedPts = (e: number | null | undefined) => (e == null ? '—' : `${e < 0 ? '−' : '+'}${pts(Math.abs(e))}`);
export const playKey = (p: Pick<ReviewPlay, 'game_id' | 'play_id'>) => `${p.game_id}-${p.play_id}`;
/** "Q4 1:51" / "OT 3:02". */
export const playClock = (p: Pick<ReviewPlay, 'qtr' | 'clock'>) => `${quarter(p.qtr)} ${p.clock ?? ''}`.trim();

/** "PIT down 8" / "tied", from the offense's side. */
export function playLead(p: Pick<ReviewPlay, 'posteam' | 'off_score' | 'def_score'>): string {
  if (p.off_score == null || p.def_score == null) return '';
  if (p.off_score === p.def_score) return 'tied';
  return `${p.posteam} ${p.off_score > p.def_score ? 'up' : 'down'} ${Math.abs(p.off_score - p.def_score)}`;
}
/** "16–24" (the offense first). */
export const playScore = (p: Pick<ReviewPlay, 'off_score' | 'def_score'>) =>
  p.off_score == null || p.def_score == null ? '' : `${p.off_score}–${p.def_score}`;

/** The kick: "97% from 29 yards" / "out of range (95 yards)". */
export function kickText(p: Pick<ReviewPlay, 'fg_make' | 'fg_distance'>): string {
  const d = p.fg_distance == null ? '—' : Math.round(p.fg_distance);
  return p.fg_make == null ? `out of range (${d} yards)` : `${p0(p.fg_make)} from ${d} yards`;
}

/** The edge in words, for a screen reader and a tooltip. */
export function edgeWords(p: Pick<ReviewPlay, 'edge'>): string {
  if (p.edge == null) return 'not priced';
  return p.edge < 0 ? `cost ${pts(-p.edge)} points against the bot` : `gained ${pts(p.edge)} points over the next best`;
}

/** The plays of each game, in the games' order (kickoff); games without a play are left out. */
export function playsByGame<G extends { game_id: string }>(games: G[], plays: ReviewPlay[]): { game: G; plays: ReviewPlay[] }[] {
  const by = new Map<string, ReviewPlay[]>();
  for (const p of plays) {
    const list = by.get(p.game_id);
    if (list) list.push(p);
    else by.set(p.game_id, [p]);
  }
  return games.filter((g) => by.has(g.game_id)).map((g) => ({ game: g, plays: by.get(g.game_id)! }));
}

export type LeaderSort = 'rank' | 'spots' | 'lost' | 'agree';
const SORTS: Record<LeaderSort, (a: ReviewCoachRow, b: ReviewCoachRow) => number> = {
  rank: (a, b) => (a.rank ?? 0) - (b.rank ?? 0),
  spots: (a, b) => b.go_spots - a.go_spots || (a.rank ?? 0) - (b.rank ?? 0),
  lost: (a, b) => b.wp_lost - a.wp_lost || (a.rank ?? 0) - (b.rank ?? 0),
  agree: (a, b) => (b.agree_rate ?? -1) - (a.agree_rate ?? -1) || (a.rank ?? 0) - (b.rank ?? 0),
};
export const sortLeaders = (rows: ReviewCoachRow[], by: LeaderSort) => [...rows].sort(SORTS[by]);

/** The small-sample note while the season is young: how far one call moves a coach's rate. */
export function smallSampleText(weeks: number, rows: ReviewCoachRow[]): string | null {
  if (weeks > 6) return null;
  const spots = rows.map((r) => r.go_spots).filter((n) => n > 0);
  if (!spots.length) return null;
  const lo = Math.min(...spots);
  const hi = Math.max(...spots);
  return (
    `After ${weeks} week${weeks === 1 ? '' : 's'} a coach with any has ${lo} to ${hi} go spots, so one call moves his rate ` +
    `by ${Math.round(100 / hi)} to ${Math.round(100 / lo)} points. Read the order loosely until about week 8; the ` +
    'calibration needs about 4 weeks of plays before it says much.'
  );
}

/** The week stepper's targets: the previous week, and the next one up to Game day's week. */
export function stepWeeks(season: number, week: number, liveWeek: { season: number; week: number } | null) {
  const prev = week > 1 ? week - 1 : null;
  let next: number | null = null;
  if (liveWeek && season === liveWeek.season) next = week + 1 <= liveWeek.week ? week + 1 : null;
  else if (!liveWeek || season < liveWeek.season) next = week < lastWeek(season) ? week + 1 : null;
  return { prev, next };
}
