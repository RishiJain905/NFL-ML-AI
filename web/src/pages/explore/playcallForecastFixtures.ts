// Small synthetic forecast answers for the web tests (PC02). Made up: never real API dumps.
// One game (KC at JAX), the offense's PROE, dropback rate and runs up the middle (a venue target:
// the home team's stats crew), the defense's blitz and pass rushers; a forecast week, a graded
// week (one row waiting for FTN), week 1 (last season's rates), a descriptive week with a hint.

import type {
  PlayCallsResponse,
  PlaycallForecastMeta,
  PlaycallForecastRecord,
  PlaycallForecastValue,
  PlaycallTarget,
  PlaycallTeamResponse,
  PlaycallWeekGrade,
} from '../../api/types';
import { BLITZ, PLAY_CALLS, PROE, TEAM, TEAM_DEFENSE } from './playcallFixtures';

const backtest = { shipped: true, mae: 0.07, mae_season_to_date: 0.075, wins: 7, seasons: 7, coverage: 0.8 };

const target = (t: Partial<PlaycallTarget> & Pick<PlaycallTarget, 'metric' | 'label' | 'short'>): PlaycallTarget => ({
  target: t.metric,
  unit: 'share',
  digits: 1,
  source: 'pbp',
  situation: 'all',
  per: 'play',
  caller: 'offense',
  help: null,
  context: 'opponent',
  backtest,
  ...t,
});

export const T_PROE = target({ ...PROE, context: 'opponent' });
export const T_DROPBACK = target({ metric: 'dropback_rate', label: 'Dropback rate', short: 'Dropback rate' });
export const T_RUN_MIDDLE = target({
  metric: 'run_middle',
  label: 'Designed runs up the middle',
  short: 'Runs up the middle',
  per: 'designed run',
  context: 'venue',
  help: "Charted by each stadium's stats crew: the forecast's second term is the venue's crew.",
});
export const T_BLITZ = target({ ...BLITZ, context: 'opponent' });
export const T_RUSHERS = target({ metric: 'rushers_avg', label: 'Pass rushers per dropback', short: 'Pass rushers', unit: 'mean', digits: 2, source: 'ftn', per: 'dropback', caller: 'defense' });
export const TARGETS = [T_PROE, T_DROPBACK, T_RUN_MIDDLE, T_BLITZ, T_RUSHERS];

export const fv = (v: Partial<PlaycallForecastValue> & Pick<PlaycallForecastValue, 'target' | 'team' | 'opponent' | 'forecast' | 'lo' | 'hi' | 'season_to_date'>): PlaycallForecastValue => ({
  caller: 'offense',
  league: null,
  shrunk: null,
  team_term: v.forecast,
  opponent_effect: 0,
  context_team: v.opponent,
  script_effect: 0,
  shift: v.forecast != null && v.season_to_date != null ? v.forecast - v.season_to_date : null,
  n_so_far: 120,
  games_so_far: 4,
  small: false,
  status: null,
  actual: null,
  n: null,
  in_range: null,
  beat_season_to_date: null,
  ...v,
});

// KC's calls on offense, then JAX's on defense (the KC-offense matchup)
export const KC_PROE = fv({
  target: 'proe', team: 'KC', opponent: 'JAX', forecast: 0.031, lo: -0.08, hi: 0.142, season_to_date: 0.045, league: -0.019,
  team_term: 0.04, opponent_effect: -0.004, script_effect: -0.005, n_so_far: 140,
});
export const KC_DROPBACK = fv({
  target: 'dropback_rate', team: 'KC', opponent: 'JAX', forecast: 0.612, lo: 0.49, hi: 0.73, season_to_date: 0.64, league: 0.59,
  team_term: 0.63, opponent_effect: 0.004, script_effect: -0.022,
});
export const KC_RUN_MIDDLE = fv({
  target: 'run_middle', team: 'KC', opponent: 'JAX', forecast: 0.31, lo: 0.12, hi: 0.5, season_to_date: 0.18, league: 0.27,
  team_term: 0.21, opponent_effect: 0.1, context_team: 'JAX', n_so_far: 12, small: true,
});
export const JAX_BLITZ = fv({
  target: 'blitz_rate', team: 'JAX', opponent: 'KC', caller: 'defense', forecast: 0.21, lo: 0.1, hi: 0.33, season_to_date: 0.18, league: 0.3,
  team_term: 0.19, opponent_effect: 0.02,
});
export const JAX_RUSHERS = fv({
  target: 'rushers_avg', team: 'JAX', opponent: 'KC', caller: 'defense', forecast: 4.312, lo: 4.08, hi: 4.55, season_to_date: 4.2, league: 4.3,
  team_term: 4.3, opponent_effect: 0.012,
});
// JAX's offense against KC's defense
export const JAX_PROE = fv({ target: 'proe', team: 'JAX', opponent: 'KC', forecast: -0.04, lo: -0.15, hi: 0.07, season_to_date: -0.05, league: -0.019 });
export const KC_BLITZ = fv({ target: 'blitz_rate', team: 'KC', opponent: 'JAX', caller: 'defense', forecast: 0.36, lo: 0.24, hi: 0.48, season_to_date: 0.39, league: 0.3 });

export const FORECAST_META: PlaycallForecastMeta = {
  version: '2019-2025_20261011T221755Z',
  made_at: '2026-10-11T22:06:06+00:00',
  as_of_week: 5,
  backfill: true,
  after_kickoff: ['KC at JAX, week 5'],
  range_level: 0.8,
  targets: TARGETS,
  note: 'Each forecast is the team’s rate for this game. The range holds 80% of games in the backtest: a point or two is inside the noise.',
};

const games = (values: { kc: PlaycallForecastValue[]; jax: PlaycallForecastValue[] }): PlayCallsResponse['games'] => [
  {
    ...PLAY_CALLS.games[0],
    matchups: [
      { ...PLAY_CALLS.games[0].matchups[0], forecast: values.kc },
      { ...PLAY_CALLS.games[0].matchups[1], forecast: values.jax },
    ],
  },
];

/** Week 5: forecast made (after kickoff: a backfill), nothing graded yet. */
export const PLAY_CALLS_FORECAST: PlayCallsResponse = {
  ...PLAY_CALLS,
  note: FORECAST_META.note,
  games: games({
    kc: [KC_PROE, KC_DROPBACK, KC_RUN_MIDDLE, JAX_BLITZ, JAX_RUSHERS].map((v) => ({ ...v, status: 'not_played' as const })),
    jax: [JAX_PROE, KC_BLITZ].map((v) => ({ ...v, status: 'not_played' as const })),
  }),
  shifts_kind: 'forecast',
  shifts: [
    {
      game_id: '2026_05_KC_JAX',
      offense: 'KC',
      defense: 'JAX',
      metric: 'run_middle',
      shift: 1.37,
      change: 0.13,
      text: "KC's runs up the middle against JAX: 31.0% expected, 18.0% so far (+13.0 points). In JAX's home games the stats crew has logged more of them (+10.0 points).",
    },
    { game_id: '2026_05_KC_JAX', offense: 'KC', defense: 'JAX', metric: 'rushers_avg', shift: 0.9, change: 0.112, text: "JAX's pass rushers against KC: 4.31 expected, 4.20 so far (+0.11)." },
  ],
  forecast: FORECAST_META,
  graded: { rows: 7, graded: 0, waiting: 7, overall: { graded: 0, mae: null, mae_season_to_date: null, beat: 0, in_range: null }, targets: [] },
  forecast_hint: null,
};

const graded = (v: PlaycallForecastValue, actual: number, n: number): PlaycallForecastValue => {
  const lo = v.lo ?? -Infinity;
  const hi = v.hi ?? Infinity;
  const f = v.forecast ?? 0;
  const s = v.season_to_date ?? 0;
  return { ...v, status: 'graded', actual, n, in_range: actual >= lo && actual <= hi, beat_season_to_date: Math.abs(f - actual) < Math.abs(s - actual) };
};

export const WEEK4_GRADE: PlaycallWeekGrade = {
  rows: 7,
  graded: 6,
  waiting: 1,
  overall: { graded: 6, mae: 0.0731, mae_season_to_date: 0.0761, beat: 4, in_range: 0.8333 },
  targets: [
    { target: 'proe', graded: 2, mae: 0.0568, mae_season_to_date: 0.0615, beat: 1, in_range: 1 },
    { target: 'dropback_rate', graded: 1, mae: 0.063, mae_season_to_date: 0.0623, beat: 0, in_range: 1 },
    { target: 'run_middle', graded: 1, mae: 0.106, mae_season_to_date: 0.1062, beat: 1, in_range: 1 },
    { target: 'blitz_rate', graded: 1, mae: 0.1136, mae_season_to_date: 0.1165, beat: 1, in_range: 0 },
    { target: 'rushers_avg', graded: 1, mae: 0.1603, mae_season_to_date: 0.1843, beat: 1, in_range: 1 },
  ],
};

/** Week 4: graded; JAX's blitz waits for FTN. */
export const PLAY_CALLS_GRADED: PlayCallsResponse = {
  ...PLAY_CALLS_FORECAST,
  week: 4,
  games: games({
    kc: [graded(KC_PROE, 0.09, 30), graded(KC_DROPBACK, 0.7, 64), graded(KC_RUN_MIDDLE, 0.29, 20), { ...JAX_BLITZ, status: 'waiting_ftn' }, graded(JAX_RUSHERS, 4.2, 30)],
    jax: [graded(JAX_PROE, -0.2, 28), graded(KC_BLITZ, 0.5, 33)],
  }),
  forecast: { ...FORECAST_META, as_of_week: 4, backfill: false, after_kickoff: [] },
  graded: WEEK4_GRADE,
};

/** Week 1: the baseline is last season's rate (no games so far). */
export const PLAY_CALLS_WEEK1: PlayCallsResponse = {
  ...PLAY_CALLS_FORECAST,
  week: 1,
  window: 'last_season',
  games: games({
    kc: [{ ...KC_PROE, games_so_far: 0, n_so_far: 0, small: true }],
    jax: [],
  }),
  shifts: [],
  forecast: { ...FORECAST_META, as_of_week: 1, backfill: false, after_kickoff: [] },
  graded: null,
  forecast_hint: 'Not graded yet: `uv run nfl playcalling grade --season 2026 --week 1` after the week\'s games are in the play-calling tables.',
};

/** A week without a forecast: PC01's descriptive answer plus the command that makes one. */
export const PLAY_CALLS_HINT: PlayCallsResponse = {
  ...PLAY_CALLS,
  forecast_hint: "No forecast for week 5 yet: `uv run nfl playcalling forecast --season 2026 --week 5` (after the week's `nfl playcalling build`).",
};

/** KC's offense page: the next game's forecast (own: KC's calls; opponent: JAX's defense calls). */
export const TEAM_FORECAST: PlaycallTeamResponse = {
  ...TEAM,
  next_game: {
    ...(TEAM.next_game as NonNullable<PlaycallTeamResponse['next_game']>),
    forecast: { ...FORECAST_META, own: [KC_PROE, KC_DROPBACK, KC_RUN_MIDDLE], opponent: [JAX_BLITZ, JAX_RUSHERS] },
    forecast_hint: null,
  },
  forecast_record: {
    season: 2026,
    weeks: [1, 2, 3, 4],
    overall: { graded: 12, mae: 0.0671, mae_season_to_date: 0.0803, beat: 8, in_range: 0.8333 },
    targets: [
      { ...T_PROE, graded: 4, mae: 0.05, mae_season_to_date: 0.054, beat: 3, in_range: 1 },
      { ...T_DROPBACK, graded: 4, mae: 0.083, mae_season_to_date: 0.087, beat: 3, in_range: 0.75 },
      { ...T_RUN_MIDDLE, graded: 4, mae: 0.073, mae_season_to_date: 0.117, beat: 2, in_range: 1 },
    ],
  } satisfies PlaycallForecastRecord,
};

/** KC's defense page: a bye this week, so the next game (week 6) has no forecast yet. */
export const TEAM_DEFENSE_HINT: PlaycallTeamResponse = {
  ...TEAM_DEFENSE,
  next_game: {
    season: 2026,
    week: 6,
    game_id: '2026_06_LAC_KC',
    opponent: 'LAC',
    home: true,
    kickoff: '2026-10-18T20:25:00Z',
    forecast: null,
    forecast_hint: 'No forecast for week 6 yet: `uv run nfl playcalling forecast --season 2026 --week 6`.',
  },
  forecast_record: {
    season: 2026,
    weeks: [1, 2, 3, 4],
    overall: { graded: 8, mae: 0.12, mae_season_to_date: 0.11, beat: 3, in_range: 0.75 },
    targets: [
      { ...T_BLITZ, graded: 4, mae: 0.12, mae_season_to_date: 0.11, beat: 1, in_range: 0.75 },
      { ...T_RUSHERS, graded: 4, mae: 0.21, mae_season_to_date: 0.25, beat: 2, in_range: 0.75 },
    ],
  },
};
