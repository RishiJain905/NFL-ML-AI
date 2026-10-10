// Small synthetic decision reviews for the LD03 tests (never real dumps): two games, four 4th
// downs (a costly punt, a bold go the bot agreed with, a fake punt, a field goal wiped out by a
// penalty), and a season with two coaches, the calibration tables and a two-week trend.

import type {
  LiveReviewResponse,
  LiveSeasonReviewResponse,
  ReviewCalBin,
  ReviewCoachRow,
  ReviewModel,
  ReviewPlay,
} from '../../../api/types';

export const REVIEW_PATH = {
  week: (season: number, week: number) => `/api/live/${season}/${week}/review`,
  season: (season: number, through: number) => `/api/live/${season}/season-review?through=${through}`,
};

const MODEL: ReviewModel = { version: 'test-bundle', in_sample: false, trained_seasons: [2010, 2025] };
export const MODEL_IN_SAMPLE: ReviewModel = { ...MODEL, in_sample: true };

export function play(over: Partial<ReviewPlay> = {}): ReviewPlay {
  return {
    game_id: '2026_04_AAA_BBB',
    play_id: 317,
    posteam: 'AAA',
    defteam: 'BBB',
    coach: 'Alex Alpha',
    qtr: 1,
    clock: '10:27',
    off_score: 0,
    def_score: 0,
    situation: '4th & 2 at BBB 44',
    ydstogo: 2,
    yardline_100: 44,
    choice: 'punt',
    best: 'go',
    agree: false,
    label: 'Confident',
    gap: 0.0349,
    wp: { go: 0.6778, fg: 0.6429, punt: 0.6285 },
    wp_now: 0.6699,
    edge: -0.0493,
    cost: 0.0493,
    convert: 0.5722,
    fg_make: 0.4508,
    fg_distance: 62,
    fake: false,
    wiped: false,
    success: null,
    result: 'BBB ball at own 14',
    desc: '(10:27) 19-P.Punter punts 30 yards to BBB 14, out of bounds.',
    ...over,
  };
}

export const COSTLY = play();
export const BOLD = play({
  game_id: '2026_04_CCC_DDD',
  play_id: 4319,
  posteam: 'CCC',
  defteam: 'DDD',
  coach: 'Casey Gamma',
  qtr: 4,
  clock: '1:29',
  off_score: 26,
  def_score: 32,
  situation: '4th & 13 at CCC 10',
  ydstogo: 13,
  yardline_100: 90,
  choice: 'go',
  best: 'go',
  agree: true,
  gap: 0.026,
  wp: { go: 0.081, fg: null, punt: 0.055 },
  edge: 0.026,
  cost: 0,
  convert: 0.26,
  fg_make: null,
  fg_distance: 107,
  success: false,
  result: 'Stopped (+12 yds, needed 13)',
  desc: '(1:29) (Shotgun) <b>Q.Back</b> pass short middle to CCC 22 for 12 yards.',
});
export const FAKE = play({
  play_id: 900,
  posteam: 'BBB',
  defteam: 'AAA',
  coach: 'Blake Beta',
  qtr: 2,
  clock: '13:21',
  off_score: 3,
  def_score: 0,
  situation: '4th & 9 at BBB 30',
  choice: 'go',
  best: 'punt',
  label: 'Toss-up',
  gap: 0.0046,
  wp: { go: 0.55, fg: null, punt: 0.5546 },
  edge: -0.0046,
  cost: 0.0046,
  fake: true,
  success: true,
  result: 'Fake punt: Converted (+12 yds)',
  desc: '(13:21) (Punt formation) 14-P.Punter right end to BBB 42 for 12 yards.',
});
export const WIPED = play({
  game_id: '2026_04_CCC_DDD',
  play_id: 2000,
  posteam: 'DDD',
  defteam: 'CCC',
  coach: 'Dana Delta',
  qtr: 3,
  clock: '2:34',
  off_score: 14,
  def_score: 10,
  situation: '4th & 8 at CCC 16',
  choice: 'fg',
  best: 'fg',
  agree: true,
  label: 'Confident',
  gap: 0.015,
  wp: { go: 0.7, fg: 0.715, punt: null },
  edge: 0.015,
  cost: 0,
  wiped: true,
  success: true,
  result: 'Wiped out by a penalty: first down',
  desc: '(2:34) 34-K.Kicker 34 yard field goal is GOOD, NULLIFIED by Penalty.',
});

export function review(over: Partial<LiveReviewResponse> = {}): LiveReviewResponse {
  return {
    season: 2026,
    week: 4,
    status: 'ok',
    message: null,
    model: MODEL,
    computed_at: '2026-10-10T22:03:21+00:00',
    source: 'file',
    summary: {
      decisions: 4,
      coach: { go: 2, fg: 1, punt: 1 },
      bot: { go: 2, fg: 1, punt: 1 },
      agree: 2,
      toss_ups: 1,
      go_spots: 2,
      went_in_go_spots: 1,
      wp_lost: 0.0539,
      fakes: 1,
      wiped: 1,
      skipped: 0,
    },
    highlights: { boldest: BOLD, costliest: COSTLY, top: [COSTLY, FAKE] },
    games: [
      { game_id: '2026_04_AAA_BBB', away: 'AAA', home: 'BBB', away_score: 24, home_score: 27, kickoff: '2026-10-02T00:15:00+00:00', decisions: 2, wp_lost: { AAA: 0.0493, BBB: 0.0046 } },
      { game_id: '2026_04_CCC_DDD', away: 'CCC', home: 'DDD', away_score: 26, home_score: 32, kickoff: '2026-10-04T17:00:00+00:00', decisions: 2, wp_lost: { CCC: 0, DDD: 0 } },
    ],
    plays: [COSTLY, FAKE, WIPED, BOLD],
    ...over,
  };
}

export const REVIEW = review();
export const REVIEW_NO_PLAYS = review({
  status: 'no_plays',
  message: "Week 4's plays aren't in the curated data yet: they come in with the Tuesday run after the week (ingest + curate). The review builds then.",
  model: null,
  source: null,
  computed_at: null,
  summary: null,
  highlights: null,
  games: [],
  plays: [],
});
export const REVIEW_NO_MODELS = review({
  ...REVIEW_NO_PLAYS,
  status: 'no_models',
  message: "The decision models aren't trained yet (LD00): run `uv run nfl live train --promote`.",
});
export const REVIEW_2025 = review({ season: 2025, week: 22, model: MODEL_IN_SAMPLE, highlights: { boldest: null, costliest: COSTLY, top: [COSTLY] } });

function coach(over: Partial<ReviewCoachRow>): ReviewCoachRow {
  return {
    decisions: 26,
    go: 10,
    go_rate: 0.3846,
    go_spots: 12,
    went_in_go_spots: 8,
    go_rate_spots: 0.6667,
    kick_spots: 5,
    went_in_kick_spots: 0,
    go_rate_kick_spots: 0,
    agree: 17,
    agree_rate: 0.6538,
    wp_lost: 0.1079,
    wp_lost_timid: 0.1079,
    wp_lost_bold: 0,
    weeks: 4,
    ...over,
  };
}

const bins = (shift: number): ReviewCalBin[] =>
  Array.from({ length: 10 }, (_, i) => ({ bin_lo: i / 10, n: 100 + i, mean_pred: i / 10 + 0.05, mean_outcome: Math.min(1, i / 10 + 0.05 + shift) }));

export function season(over: Partial<LiveSeasonReviewResponse> = {}): LiveSeasonReviewResponse {
  return {
    season: 2026,
    through_week: 4,
    status: 'ok',
    message: null,
    model: MODEL,
    source: 'computed',
    weeks: [3, 4],
    decisions: 52,
    leaderboard: [
      coach({ rank: 1, coach: 'Alex Alpha', teams: ['AAA'] }),
      coach({ rank: 2, coach: 'Blake Beta', teams: ['BBB'], go_spots: 4, went_in_go_spots: 1, go_rate_spots: 0.25, wp_lost: 0.3, wp_lost_timid: 0.25, wp_lost_bold: 0.02, agree_rate: 0.9 }),
    ],
    league: coach({ decisions: 52, go_spots: 16, went_in_go_spots: 9, go_rate_spots: 0.5625, wp_lost: 0.4079, wp_lost_timid: 0.3579, wp_lost_bold: 0.02, agree_rate: 0.7 }),
    trend: [
      { week: 3, decisions: 26, go_rate_coach: 0.2, go_rate_bot: 0.55, agree_rate: 0.6, wp_lost: 0.2, attempts: 5, conv_actual: 0.6, conv_pred: 0.55 },
      { week: 4, decisions: 26, go_rate_coach: 0.25, go_rate_bot: 0.5, agree_rate: 0.65, wp_lost: 0.2, attempts: 6, conv_actual: 0.5, conv_pred: 0.52 },
    ],
    calibration: {
      wp: { snaps: 2303, games: 16, tie_games: 0, brier: 0.168, brier_vegas: 0.1659, ece: 0.0347, ece_vegas: 0.0264, model: bins(0.01), vegas: bins(-0.01) },
      conversion: {
        rows: [3, 4].flatMap((down) =>
          Array.from({ length: 10 }, (_, i) => ({
            down: down as 3 | 4,
            distance: i + 1,
            label: i === 9 ? '10+' : String(i + 1),
            n: down === 3 ? 50 : i < 2 ? 20 : 3,
            actual: 0.7 - i * 0.05,
            pred: 0.68 - i * 0.05,
          })),
        ),
        overall: [
          { down: 3, n: 500, actual: 0.4176, pred: 0.4238, brier: 0.2147 },
          { down: 4, n: 64, actual: 0.5856, pred: 0.5419, brier: 0.2335 },
        ],
      },
      fg: {
        n: 59,
        made: 0.8571,
        pred: 0.8334,
        rows: [
          { band: '<30', n: 10, made: 1, pred: 0.98 },
          { band: '30-39', n: 15, made: 0.93, pred: 0.93 },
          { band: '40-49', n: 18, made: 0.83, pred: 0.83 },
          { band: '50+', n: 16, made: 0.69, pred: 0.66 },
        ],
      },
    },
    computed_at: '2026-10-10T22:03:42+00:00',
    ...over,
  };
}

export const SEASON = season();
export const SEASON_2025 = season({ season: 2025, through_week: 22, weeks: Array.from({ length: 22 }, (_, i) => i + 1), model: MODEL_IN_SAMPLE });
