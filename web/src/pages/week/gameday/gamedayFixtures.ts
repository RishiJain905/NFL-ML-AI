// Small synthetic Game day answers, shaped like the reader's (types.ts, LD02 block). Made-up
// teams (the week fixtures' AAA-DDD) and numbers: never paste real API dumps here.

import type {
  LiveCallResponse,
  LiveContextResponse,
  LiveFeed,
  LiveGame,
  LiveGamesResponse,
  LiveMeasure,
  LiveModels,
  LiveStat,
  LiveTableRow,
} from '../../../api/types';

export const PATH = {
  games: '/api/live/2026/4/games',
  refresh: '/api/live/2026/4/games?refresh=1',
  call: (ev: string) => `/api/live/2026/4/games/${ev}/call`,
  context: (ev: string, off: string) => `/api/live/2026/4/games/${ev}/context?offense=${off}`,
};

const MODELS: LiveModels = { available: true, version: 'test-bundle', message: null };
const FEED: LiveFeed = { as_of: '2026-10-04T19:57:45Z', age_s: 0, stale: false, error: null, cached: false, ms: 80 };

export function game(over: Partial<LiveGame> = {}): LiveGame {
  return {
    event: '100',
    game_id: '2026_04_AAA_BBB',
    home: 'BBB',
    away: 'AAA',
    state: 'in',
    detail: '2:11 - 4th Quarter',
    kickoff: '2026-10-04T17:00:00Z',
    period: 4,
    clock: '2:11',
    home_score: 20,
    away_score: 17,
    possession: 'BBB',
    down: 4,
    distance: 5,
    yardline_100: 34,
    situation: '4th & 5 at AAA 34',
    red_zone: false,
    home_timeouts: 3,
    away_timeouts: 1,
    last_play: {
      id: '1001',
      type: 'Pass Incompletion',
      text: '(Shotgun) <b>Q.Back</b> pass incomplete short left.',
      at: '2026-10-04T19:57:10Z',
      seen_at: '2026-10-04T19:57:44Z',
      age_s: 35,
      age_from: 'snap',
    },
    espn_home_wp: 0.8,
    pregame: { home_win_prob: 0.45, home_spread: -2.5, spread_text: 'AAA -2.5', total: 44.5, line_source: 'nflverse' },
    decision_down: true,
    ...over,
  };
}

const FIRST_DOWN = game({
  event: '101',
  game_id: '2026_04_CCC_DDD',
  home: 'DDD',
  away: 'CCC',
  possession: 'CCC',
  down: 2,
  distance: 5,
  yardline_100: 23,
  situation: '2nd & 5 at DDD 23',
  decision_down: false,
  clock: '2:00',
});
const LATER = game({
  event: '102',
  game_id: '2026_04_EEE_FFF',
  home: 'FFF',
  away: 'EEE',
  state: 'pre',
  detail: 'Scheduled',
  kickoff: '2026-10-04T20:25:00Z',
  period: 0,
  clock: null,
  home_score: 0,
  away_score: 0,
  possession: null,
  down: null,
  distance: null,
  situation: null,
  last_play: null,
  espn_home_wp: null,
  decision_down: false,
});
const DONE = game({
  event: '103',
  game_id: '2026_04_GGG_HHH',
  home: 'HHH',
  away: 'GGG',
  state: 'post',
  detail: 'Final',
  home_score: 13,
  away_score: 30,
  possession: null,
  down: null,
  situation: null,
  last_play: null,
  espn_home_wp: null,
  decision_down: false,
});

export function games(over: Partial<LiveGamesResponse> = {}): LiveGamesResponse {
  return {
    season: 2026,
    week: 4,
    phase: 'live',
    is_current: true,
    games: [game(), FIRST_DOWN, LATER, DONE],
    feed: FEED,
    feed_error: null,
    first_kickoff: '2026-10-02T00:15:00Z',
    next_kickoff: '2026-10-04T20:25:00Z',
    models: MODELS,
    replay: null,
    refresh_s: 30,
    live_week: { season: 2026, week: 4 },
    ...over,
  };
}

const pregame = (g: LiveGame): LiveGame => ({
  ...g,
  state: 'pre',
  detail: 'Scheduled',
  period: 0,
  clock: null,
  home_score: 0,
  away_score: 0,
  possession: null,
  down: null,
  situation: null,
  last_play: null,
  espn_home_wp: null,
  decision_down: false,
});
const final = (g: LiveGame): LiveGame => ({ ...pregame(g), state: 'post', detail: 'Final', home_score: 24, away_score: 21 });

export const GAMES_LIVE = games();
export const GAMES_BEFORE = games({ phase: 'before', week: 5, games: [game(), FIRST_DOWN].map(pregame), feed: null, first_kickoff: '2026-10-09T00:15:00Z' });
export const GAMES_FUTURE = games({ phase: 'future', week: 6, is_current: false, games: [pregame(game())], feed: null });
export const GAMES_PAST = games({ phase: 'past', week: 3, is_current: false, games: [game(), FIRST_DOWN].map(final), feed: null });
export const GAMES_BETWEEN = games({ phase: 'between', games: [final(game()), final(FIRST_DOWN), LATER] });
export const GAMES_FINAL = games({ phase: 'final', games: [final(game()), final(FIRST_DOWN)], next_kickoff: null });
export const GAMES_NO_MODELS = games({ models: { available: false, version: null, message: "The decision models aren't trained yet (LD00)." } });
export const GAMES_FEED_ERROR = games({ feed: null, feed_error: 'timeout', games: [pregame(game()), LATER] , phase: 'live' });
export const GAMES_REPLAY = games({ replay: { event: '100', at: '2026-10-04T19:57:15Z', speed: 1, lag_s: 10 } });

function call(over: Partial<LiveCallResponse>): LiveCallResponse {
  return {
    season: 2026,
    week: 4,
    event: '100',
    game_id: '2026_04_AAA_BBB',
    game: game(),
    feed: { ...FEED, as_of: '2026-10-04T19:57:50Z' },
    kind: 'fourth',
    reason: null,
    warnings: [],
    offense: 'BBB',
    defense: 'AAA',
    source: 'situation',
    fourth: null,
    third: null,
    espn_offense_wp: 0.8,
    behind: null,
    repeat: { same: false, last_check_at: null, text: null },
    models: MODELS,
    ...over,
  };
}

export const CALL_FOURTH = call({
  fourth: {
    best: 'go',
    best_name: 'Go for it',
    gap: 0.014,
    label: 'Confident',
    boot_share: 1.0,
    options: [
      { choice: 'go', name: 'Go for it', wp: 0.731, best: true },
      { choice: 'fg', name: 'Field goal', wp: 0.717, best: false },
      { choice: 'punt', name: 'Punt', wp: 0.64, best: false },
    ],
    convert: 0.48,
    fg_make: 0.7,
    fg_distance: 52,
    punt_start: 11.4,
    wp_now: 0.81, // above every option on purpose: the card must not show it
    ms: 5.2,
  },
});

export const CALL_TOSS = call({
  game: game({ situation: '4th & Goal at AAA 2', distance: 2, yardline_100: 2, clock: '7:31' }),
  fourth: {
    best: 'fg',
    best_name: 'Field goal',
    gap: 0.002,
    label: 'Toss-up',
    boot_share: 0.9,
    options: [
      { choice: 'go', name: 'Go for it', wp: 0.928, best: false },
      { choice: 'fg', name: 'Field goal', wp: 0.93, best: true },
      { choice: 'punt', name: 'Punt', wp: 0.88, best: false },
    ],
    convert: 0.45,
    fg_make: 0.99,
    fg_distance: 20,
    punt_start: 13,
    wp_now: 0.94,
    ms: 5.5,
  },
});

const row = (gain: number, togo: number, best: 'go' | 'punt', gap: number, label: LiveTableRow['label']): LiveTableRow => ({
  gain,
  gain_text: gain === 0 ? 'no gain' : gain > 0 ? `a ${gain}-yard gain` : `a ${-gain}-yard loss`,
  ydstogo: togo,
  yardline_100: 54 - gain,
  situation: `4th & ${togo} at BBB ${46 + gain}`,
  best,
  best_name: best === 'go' ? 'Go for it' : 'Punt',
  gap,
  label,
  wp: { go: 0.62, fg: null, punt: 0.6 },
});

export const CALL_THIRD = call({
  kind: 'third',
  game: game({ down: 3, distance: 3, situation: '3rd & 3 at BBB 46', yardline_100: 54, clock: '3:10' }),
  third: {
    convert: 0.52,
    pass_prob: 0.8,
    wp_now: 0.69,
    note: null,
    table: [row(2, 1, 'go', 0.08, 'Confident'), row(1, 2, 'go', 0.05, 'Confident'), row(0, 3, 'go', 0.03, 'Confident'), row(-1, 4, 'punt', 0.003, 'Toss-up')],
    ms: 16,
  },
});

export const CALL_BEHIND = {
  ...CALL_THIRD,
  behind: { likely: true, text: 'ESPN still shows 3rd & 3 at BBB 46: the 4th down may not have posted.' },
};
export const CALL_STALE: LiveCallResponse = { ...CALL_FOURTH, feed: { ...CALL_FOURTH.feed, stale: true, error: 'HTTP 503' } };
export const CALL_REPEAT: LiveCallResponse = {
  ...CALL_FOURTH,
  repeat: { same: true, last_check_at: '2026-10-04T19:57:40Z', text: 'No new play since your last check (10 s ago).' },
  warnings: ['no pre-game line: spread 0 assumed'],
};
export const CALL_NONE = call({
  event: '101',
  game_id: '2026_04_CCC_DDD',
  game: FIRST_DOWN,
  kind: 'none',
  reason: '2nd & 5: checks are for 3rd and 4th downs.',
  offense: 'CCC',
  defense: 'DDD',
  espn_offense_wp: null,
});

const st = (num: number, den: number): LiveStat => ({ num, den, rate: den ? num / den : null });
const measure = (a: number, b: number): LiveMeasure => ({ this: st(a, b), last: st(a * 3, b * 4), league_this: st(100, 600), league_last: st(900, 4000) });

export const CONTEXT: LiveContextResponse = {
  season: 2026,
  week: 4,
  this_season: 2026,
  last_season: 2025,
  through_week: 3,
  through: '2026 weeks 1-3 and the 2025 season',
  computed_at: '2026-10-10T17:30:00Z',
  model_version: 'test-bundle',
  team: {
    team: 'BBB',
    coach: 'Pat Coach',
    coach_last_team: 'CCC',
    go_rate: measure(3, 20),
    fourth_conv: measure(2, 3),
    short_conv: measure(5, 8),
    red_zone_td: measure(4, 8),
    coach_go: measure(2, 7),
    coach_go_by_distance: [
      { band: '1-2', this: st(1, 1), last: st(10, 15), league_last: st(400, 600) },
      { band: '3-5', this: st(0, 2), last: st(5, 20), league_last: st(200, 700) },
      { band: '6+', this: st(0, 3), last: st(4, 14), league_last: st(100, 600) },
    ],
    kicker: {
      name: 'K.Leg',
      long: 55,
      long_season: 2024,
      bands: [
        { band: '<30', this: st(2, 2), last: st(0, 0), league_last: st(200, 204) },
        { band: '30-39', this: st(3, 3), last: st(0, 0), league_last: st(280, 300) },
        { band: '40-49', this: st(0, 0), last: st(0, 0), league_last: st(260, 310) },
        { band: '50+', this: st(1, 1), last: st(0, 0), league_last: st(180, 260) },
      ],
    },
    punter: {
      name: 'P.Boot',
      this: { punts: 12, net: 43.5, gross: 50 },
      last: { punts: 50, net: 44.2, gross: 49.5 },
      league_this: { punts: 300, net: 42, gross: 47.5 },
      league_last: { punts: 2000, net: 41.3, gross: 47 },
    },
  },
};
