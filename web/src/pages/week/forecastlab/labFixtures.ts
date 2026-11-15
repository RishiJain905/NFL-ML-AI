// Small synthetic answers for the Availability and Outcomes tabs (FL02). Never real dumps.

import type {
  AvailabilityAtRisk,
  AvailabilityMover,
  AvailabilityPlayer,
  AvailabilityResponse,
  AvailabilityScoreRow,
  CurveScoreRow,
  ForecastLabGate,
  ForecastLabUpdateStep,
  OutcomeCurve,
  OutcomeRow,
  OutcomesResponse,
} from '../../../api/types';

const NO_MODEL_HINT = (family: string) =>
  `This model hasn't been promoted yet: its backtest decides what ships (\`nfl forecastlab backtest --model ${family} --promote\`).`;

const emptyAvail = (status: AvailabilityResponse['status'], hint: string | null, week = 5): AvailabilityResponse => ({
  season: 2026,
  week,
  status,
  hint,
  meta: null,
  tiles: null,
  at_risk: [],
  movers: { snap_up: [], snap_down: [], targets_up: [], carries_up: [] },
  team: null,
  players: [],
  teams: [],
  graded: false,
  scoreboard: { week: [], season: [] },
});

export const AVAIL_NO_MODEL = emptyAvail('no_model', NO_MODEL_HINT('availability'));
export const AVAIL_NONE = emptyAvail('none', "Nothing for week 5 yet. It's made right after Run week 5 (or press Update here).");
export const AVAIL_NONE_W3 = emptyAvail('none', "Nothing for week 3 yet. It's made right after Run week 3 (or press Update here).", 3);

// ---- availability (synthetic names; the shapes and the wording cases of the real week-10 answer)

const player = (over: Partial<AvailabilityPlayer>): AvailabilityPlayer => ({
  player_id: '00-0000100',
  player: 'A. Player',
  team: 'TB',
  opponent: 'ATL',
  home: false,
  game_id: '2026_05_TB_ATL',
  position: 'WR',
  pgroup: 'WR',
  side: 'off',
  view: 'saturday',
  report_status: null,
  practice_status: null,
  prev_status: null,
  roster_status: 'ACT',
  p_play: 0.95,
  p_play_tuesday: 0.93,
  base_p_play: 0.85,
  snap_p10: 0.6,
  snap_p50: 0.8,
  snap_p90: 0.93,
  base_snap: 0.78,
  exp_snap: 0.76,
  tgt_share: 0.2,
  base_tgt_share: 0.18,
  carry_share: null,
  base_carry_share: null,
  at_risk: false,
  games_season: 4,
  played: null,
  actual_snap: null,
  actual_tgt_share: null,
  actual_carry_share: null,
  ...over,
});

/** On this week's report: Questionable, limited; Tuesday 39% to Saturday 65%; one teammate gains
 *  snaps and targets (joined on one line). */
export const AR_QUESTIONABLE: AvailabilityAtRisk = {
  ...player({
    player_id: '00-0000101',
    player: 'Q. Receiver',
    report_status: 'Questionable',
    practice_status: 'limited',
    prev_status: 'Questionable',
    p_play: 0.65,
    p_play_tuesday: 0.39,
    base_p_play: 0.34,
    snap_p50: 0.74,
    snap_p10: 0.37,
    snap_p90: 0.93,
    base_snap: 0.84,
    at_risk: true,
  }),
  risk_group: 'report',
  stake: 0.29,
  inherit: [
    { kind: 'snaps', player_id: '00-0000102', player: 'K. Slot', position: 'WR', share_if_in: 0.4, share_if_out: 0.5, delta: 0.1 },
    { kind: 'targets', player_id: '00-0000102', player: 'K. Slot', position: 'WR', share_if_in: 0.12, share_if_out: 0.13, delta: 0.01 },
  ],
};
/** Out, did not practice: Tuesday 38% to Saturday 0.1%. */
export const AR_OUT: AvailabilityAtRisk = {
  ...player({
    player_id: '00-0000103',
    player: 'D. Corner',
    position: 'DB',
    pgroup: 'CB',
    side: 'def',
    report_status: 'Out',
    practice_status: 'dnp',
    prev_status: 'Out',
    p_play: 0.001,
    p_play_tuesday: 0.38,
    base_p_play: 0.33,
    snap_p50: 0.95,
    base_snap: 0.85,
    tgt_share: null,
    base_tgt_share: null,
    at_risk: true,
  }),
  risk_group: 'report',
  stake: 0.85,
  inherit: [{ kind: 'snaps', player_id: '00-0000104', player: 'K. Nickel', position: 'DB', share_if_in: 0.3, share_if_out: 0.41, delta: 0.11 }],
};
/** A regular with a low chance (Out last week): his backup takes +45 points of the snaps; his
 *  game is already over and he didn't play (the Thursday case). */
export const AR_UNCERTAIN: AvailabilityAtRisk = {
  ...player({
    player_id: '00-0000105',
    player: 'M. Passer',
    position: 'QB',
    pgroup: 'QB',
    prev_status: 'Out',
    p_play: 0.184,
    p_play_tuesday: 0.184,
    base_p_play: 0.23,
    snap_p50: 0.98,
    snap_p10: 0.37,
    snap_p90: 1,
    base_snap: 1,
    tgt_share: null,
    base_tgt_share: null,
    carry_share: 0.012,
    base_carry_share: 0.048,
    at_risk: true,
    played: false,
  }),
  risk_group: 'uncertain',
  stake: 0.82,
  inherit: [{ kind: 'snaps', player_id: '00-0000106', player: 'B. Backup', position: 'QB', share_if_in: 0.25, share_if_out: 0.7, delta: 0.45 }],
};
/** On a reserve list: old news, kept for who took his work. */
export const AR_RESERVE: AvailabilityAtRisk = {
  ...player({
    player_id: '00-0000107',
    player: 'J. Backer',
    team: 'ATL',
    opponent: 'TB',
    home: true,
    position: 'LB',
    pgroup: 'LB',
    side: 'def',
    prev_status: 'Out',
    roster_status: 'RES',
    p_play: 0.036,
    p_play_tuesday: 0.036,
    base_p_play: 0.06,
    snap_p50: 0.96,
    base_snap: 0.885,
    tgt_share: null,
    base_tgt_share: null,
    at_risk: true,
  }),
  risk_group: 'reserve',
  stake: 0.85,
  inherit: [{ kind: 'snaps', player_id: '00-0000108', player: 'M. Linebacker', position: 'LB', share_if_in: 0.64, share_if_out: 0.89, delta: 0.25 }],
};

const mover = (p: AvailabilityPlayer, change: number): AvailabilityMover => ({ ...p, change });

/** Saturday's view, before the games: the report, a regular, a reserve. */
export const AVAIL_OK: AvailabilityResponse = {
  ...emptyAvail('ok', null),
  meta: {
    views: {
      tuesday: { made_at: '2026-10-06T14:40:00+00:00', rows: 1400 },
      saturday: { made_at: '2026-10-10T15:05:00+00:00', rows: 1400 },
    },
    version: 'avail-v1',
    trained_through: '2026-w04',
    shipped: { tuesday: ['play', 'snap'], saturday: ['play', 'snap', 'tgt_share'] },
    games_after_kickoff: ['2026_05_TB_ATL'],
  },
  tiles: {
    players: 1400,
    at_risk: 4,
    by_group: { report: 2, uncertain: 1, reserve: 1 },
    role_floor: 0.3,
    out: 18,
    doubtful: 6,
    questionable: 37,
    view: 'saturday',
  },
  at_risk: [AR_OUT, AR_QUESTIONABLE, AR_UNCERTAIN, AR_RESERVE],
  movers: {
    snap_up: [mover(player({ player_id: '00-0000110', player: 'S. Up', snap_p50: 0.69, base_snap: 0.38 }), 0.31)],
    snap_down: [mover(player({ player_id: '00-0000111', player: 'S. Down', snap_p50: 0.22, base_snap: 0.5 }), -0.28)],
    targets_up: [mover(player({ player_id: '00-0000112', player: 'T. Up', tgt_share: 0.147, base_tgt_share: 0.104 }), 0.043)],
    carries_up: [mover(player({ player_id: '00-0000113', player: 'C. Up', position: 'RB', carry_share: 0.47, base_carry_share: 0.31 }), 0.16)],
  },
  teams: ['ATL', 'TB'],
};

/** Tuesday's view: no injury report yet, so nobody is on the report. */
export const AVAIL_TUESDAY: AvailabilityResponse = {
  ...AVAIL_OK,
  meta: { ...AVAIL_OK.meta!, views: { tuesday: AVAIL_OK.meta!.views.tuesday } },
  tiles: { ...AVAIL_OK.tiles!, view: 'tuesday', at_risk: 2, by_group: { report: 0, uncertain: 1, reserve: 1 }, out: 0, doubtful: 0, questionable: 0 },
  at_risk: [
    { ...AR_UNCERTAIN, view: 'tuesday' },
    { ...AR_RESERVE, view: 'tuesday' },
  ],
};

/** `?team=TB`: that team's players, offense then defense. */
export const AVAIL_TEAM_TB: AvailabilityResponse = {
  ...AVAIL_OK,
  team: 'TB',
  players: [
    player({
      player_id: '00-0000120',
      player: 'T. Quarterback',
      position: 'QB',
      pgroup: 'QB',
      tgt_share: null,
      base_tgt_share: null,
      carry_share: 0.05,
      base_carry_share: 0.04,
      snap_p50: 1,
      base_snap: 1,
    }),
    player({ player_id: '00-0000101', player: 'Q. Receiver', at_risk: true, p_play: 0.65, played: true, actual_snap: 0.61 }),
    player({ player_id: '00-0000121', player: 'T. Linebacker', position: 'LB', pgroup: 'LB', side: 'def', tgt_share: null, base_tgt_share: null, played: false }),
  ],
};

const avScore = (output: string, n: number, over: Partial<AvailabilityScoreRow>): AvailabilityScoreRow => ({
  view: 'tuesday',
  output,
  group: 'ALL',
  n,
  brier_model: null,
  brier_baseline: null,
  mae_model: null,
  mae_baseline: null,
  coverage_80: null,
  ...over,
});
/** After the games (synthetic): the scoreboard, this week and the season. */
export const AVAIL_GRADED: AvailabilityResponse = {
  ...AVAIL_OK,
  graded: true,
  scoreboard: {
    week: [
      avScore('play', 1073, { brier_model: 0.101, brier_baseline: 0.146 }),
      avScore('snap', 905, { mae_model: 0.118, mae_baseline: 0.129, coverage_80: 0.81 }),
      avScore('tgt_share', 240, { mae_model: 0.05, mae_baseline: 0.05 }),
    ],
    season: [{ ...avScore('play', 9000, { brier_model: 0.104, brier_baseline: 0.15 }), weeks: 9 }],
  },
};

const emptyOutcomes = (status: OutcomesResponse['status'], hint: string | null): OutcomesResponse => ({
  season: 2026,
  week: 5,
  status,
  hint,
  meta: null,
  tiles: null,
  targets: [],
  stat: null,
  rows: [],
  gaps: [],
  graded: false,
  scoreboard: { week: [], season: [] },
});

export const OUTCOMES_NO_MODEL = emptyOutcomes('no_model', NO_MODEL_HINT('curves'));
export const OUTCOMES_NONE = emptyOutcomes('none', "Nothing for week 5 yet. It's made right after Run week 5 (or press Update here).");

export const OUTCOMES_OK: OutcomesResponse = {
  ...emptyOutcomes('ok', null),
  meta: {
    made_at: '2026-10-06T14:45:00+00:00',
    rows: 2,
    version: 'curves-v1',
    shipped: ['rec_yds-wrte'],
    source_version: 'player-v12',
    games_after_kickoff: [],
  },
  tiles: { curves: 1381, players: 631, stats: 2 },
  targets: [
    { stat: 'rec_yds-wrte', label: 'receiving yards', group: 'WR/TE', kind: 'amount', marks: [24.5, 49.5, 74.5], band: 7, n: 2 },
    { stat: 'carries-rb', label: 'carries', group: 'RB', kind: 'count', marks: [9.5, 12.5, 14.5], band: 2, n: 1 },
  ],
  stat: 'rec_yds-wrte',
  rows: [
    {
      player_id: '00-0000001',
      player: 'A. Receiver',
      team: 'TB',
      opponent: 'ATL',
      game_id: '2026_05_TB_ATL',
      position: 'WR',
      group: 'WR/TE',
      stat: 'rec_yds-wrte',
      target_label: 'receiving yards',
      kind: 'amount',
      p10: 22,
      p50: 58,
      p90: 104,
      mean: 61.2,
      q05: 15,
      q10: 22,
      q25: 38,
      q50: 57,
      q75: 80,
      q90: 104,
      q95: 121,
      band: 7,
      p_band: 0.12,
      bell_p_band: 0.14,
      marks: [
        { mark: 24.5, p_over: 0.86, bell_p_over: 0.8 },
        { mark: 49.5, p_over: 0.6, bell_p_over: 0.64 },
        { mark: 74.5, p_over: 0.33, bell_p_over: 0.3 },
      ],
      gap: { mark: 24.5, p_over: 0.86, bell_p_over: 0.8, diff: 0.06 },
      n_analogs: 900,
      actual: null,
      played: null,
    },
    {
      player_id: '00-0000002',
      player: 'B. End',
      team: 'ATL',
      opponent: 'TB',
      game_id: '2026_05_TB_ATL',
      position: 'TE',
      group: 'WR/TE',
      stat: 'rec_yds-wrte',
      target_label: 'receiving yards',
      kind: 'amount',
      p10: 8,
      p50: 31,
      p90: 66,
      mean: 34,
      q05: 3,
      q10: 8,
      q25: 18,
      q50: 30,
      q75: 46,
      q90: 66,
      q95: 79,
      band: 7,
      p_band: 0.2,
      bell_p_band: 0.21,
      marks: [
        { mark: 24.5, p_over: 0.62, bell_p_over: 0.6 },
        { mark: 49.5, p_over: 0.27, bell_p_over: 0.3 },
        { mark: 74.5, p_over: 0.08, bell_p_over: 0.06 },
      ],
      gap: { mark: 49.5, p_over: 0.27, bell_p_over: 0.3, diff: -0.03 },
      n_analogs: 900,
      actual: null,
      played: null,
    },
  ],
};

// ---- pre-flight's `forecastlab` gate -----------------------------------------------------------

export const LAB_COMMAND = 'nfl forecastlab update --auto --expect-week 5';

export const LAB_STEPS: ForecastLabUpdateStep[] = [
  { step: 'availability', label: 'Availability for week 5 (Tuesday view)', due: true, reason: 'no Tuesday view yet', week: 5 },
  { step: 'curves', label: 'Outcome curves for week 5', due: true, reason: 'no curves for week 5 yet', week: 5 },
  { step: 'grade', label: 'Grade week 4', due: false, reason: 'week 4 has nothing to grade', week: 4 },
];

export const labGate = (over: Partial<ForecastLabGate> = {}): ForecastLabGate => ({
  allowed: true,
  reason: null,
  command: LAB_COMMAND,
  steps: LAB_STEPS,
  ...over,
});

export const LAB_UP_TO_DATE = labGate({
  allowed: false,
  reason: 'Everything is up to date.',
  steps: LAB_STEPS.map((s) => ({ ...s, due: false, reason: s.step === 'grade' ? s.reason : 'up to date (Tuesday view)' })),
});

// ---- the curves (synthetic: a smooth spread, not a real player's) ------------------------------

/** A whole-number curve from grid_lo up, built from a normal-ish spread (for tests only). */
function makeCdf(lo: number, n: number, centre: number, spread: number): number[] {
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const z = (lo + i + 0.5 - centre) / spread;
    out.push(Math.round((1 / (1 + Math.exp(-1.7 * z))) * 1e5) / 1e5);
  }
  out[out.length - 1] = 1;
  return out;
}

// where the two curves disagree most: both receivers (the reader keeps two per stat)
OUTCOMES_OK.gaps = [OUTCOMES_OK.rows[0], OUTCOMES_OK.rows[1]];

export const RECEIVER = OUTCOMES_OK.rows[0];
export const CURVE_RECEIVER: OutcomeCurve = { ...RECEIVER, grid_lo: -15, cdf: makeCdf(-15, 200, 58, 30) };
export const CURVE_END: OutcomeCurve = { ...OUTCOMES_OK.rows[1], grid_lo: -15, cdf: makeCdf(-15, 140, 31, 20) };

export const BACK: OutcomeRow = {
  ...RECEIVER,
  player_id: '00-0000009',
  player: 'C. Back',
  position: 'RB',
  group: 'RB',
  stat: 'carries-rb',
  target_label: 'carries',
  kind: 'count',
  p10: 10,
  p50: 18,
  p90: 27,
  mean: 18.4,
  q05: 9,
  q10: 11,
  q25: 14,
  q50: 18,
  q75: 22,
  q90: 26,
  q95: 28,
  band: 2,
  p_band: 0.31,
  bell_p_band: 0.42,
  marks: [
    { mark: 9.5, p_over: 0.93, bell_p_over: 0.99 },
    { mark: 12.5, p_over: 0.83, bell_p_over: 0.93 },
    { mark: 14.5, p_over: 0.74, bell_p_over: 0.83 },
  ],
  gap: { mark: 9.5, p_over: 0.93, bell_p_over: 0.99, diff: -0.06 },
  n_analogs: 500,
};
export const CURVE_BACK: OutcomeCurve = { ...BACK, grid_lo: 0, cdf: makeCdf(0, 45, 18, 4.5) };

export const OUTCOMES_CARRIES: OutcomesResponse = { ...OUTCOMES_OK, stat: 'carries-rb', rows: [BACK], meta: { ...OUTCOMES_OK.meta!, rows: 1 } };

const score = (stat: string, n: number, crps: [number, number, number]): CurveScoreRow => ({
  stat,
  n,
  crps_model: crps[0],
  crps_bell: crps[1],
  crps_own: crps[2],
  brier_model: 0.18,
  brier_bell: 0.19,
  brier_own: 0.205,
  band_pred: 0.12,
  band_hit: 0.11,
  pit_ece: 0.02,
});

/** After the games (synthetic): one played with an actual, one who didn't play, and the scoreboard. */
export const OUTCOMES_GRADED: OutcomesResponse = {
  ...OUTCOMES_OK,
  graded: true,
  rows: [
    { ...OUTCOMES_OK.rows[0], played: true, actual: 94 },
    { ...OUTCOMES_OK.rows[1], played: false, actual: null },
  ],
  scoreboard: {
    week: [score('rec_yds-wrte', 2, [15.2, 15.9, 16.8]), score('pass_yds-qb', 26, [47.9, 48.0, 52.3])],
    season: [{ ...score('rec_yds-wrte', 470, [15.4, 16.0, 16.9]), weeks: 2 }],
  },
};
export const CURVE_RECEIVER_GRADED: OutcomeCurve = { ...CURVE_RECEIVER, played: true, actual: 94 };
