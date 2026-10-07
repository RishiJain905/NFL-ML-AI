// Small synthetic answers for the season pages (CR03), shaped like the API's. Numbers are
// anchored on the real 2026 / 2025 values but generated, never pasted from the API.

import type {
  AlertsResponse,
  HealthResponse,
  ModelCardResponse,
  ModelsResponse,
  ScorecardResponse,
  ScorecardWeek,
  TeamInfoResponse,
  TeamRow,
  TeamsResponse,
} from '../../api/types';

// ---- Teams (all 32, so the rankings and the AFC / NFC filter are realistic) ----------------

const TEAM_LIST: [string, string, string, string, string][] = [
  ['BUF', 'Bills', 'Buffalo Bills', '#00338D', 'AFC East'],
  ['MIA', 'Dolphins', 'Miami Dolphins', '#008E97', 'AFC East'],
  ['NE', 'Patriots', 'New England Patriots', '#002244', 'AFC East'],
  ['NYJ', 'Jets', 'New York Jets', '#125740', 'AFC East'],
  ['BAL', 'Ravens', 'Baltimore Ravens', '#241773', 'AFC North'],
  ['CIN', 'Bengals', 'Cincinnati Bengals', '#FB4F14', 'AFC North'],
  ['CLE', 'Browns', 'Cleveland Browns', '#311D00', 'AFC North'],
  ['PIT', 'Steelers', 'Pittsburgh Steelers', '#FFB612', 'AFC North'],
  ['HOU', 'Texans', 'Houston Texans', '#03202F', 'AFC South'],
  ['IND', 'Colts', 'Indianapolis Colts', '#002C5F', 'AFC South'],
  ['JAX', 'Jaguars', 'Jacksonville Jaguars', '#006778', 'AFC South'],
  ['TEN', 'Titans', 'Tennessee Titans', '#4B92DB', 'AFC South'],
  ['DEN', 'Broncos', 'Denver Broncos', '#FB4F14', 'AFC West'],
  ['KC', 'Chiefs', 'Kansas City Chiefs', '#E31837', 'AFC West'],
  ['LV', 'Raiders', 'Las Vegas Raiders', '#A5ACAF', 'AFC West'],
  ['LAC', 'Chargers', 'Los Angeles Chargers', '#0080C6', 'AFC West'],
  ['DAL', 'Cowboys', 'Dallas Cowboys', '#041E42', 'NFC East'],
  ['NYG', 'Giants', 'New York Giants', '#0B2265', 'NFC East'],
  ['PHI', 'Eagles', 'Philadelphia Eagles', '#004C54', 'NFC East'],
  ['WAS', 'Commanders', 'Washington Commanders', '#5A1414', 'NFC East'],
  ['CHI', 'Bears', 'Chicago Bears', '#0B162A', 'NFC North'],
  ['DET', 'Lions', 'Detroit Lions', '#0076B6', 'NFC North'],
  ['GB', 'Packers', 'Green Bay Packers', '#203731', 'NFC North'],
  ['MIN', 'Vikings', 'Minnesota Vikings', '#4F2683', 'NFC North'],
  ['ATL', 'Falcons', 'Atlanta Falcons', '#A71930', 'NFC South'],
  ['CAR', 'Panthers', 'Carolina Panthers', '#0085CA', 'NFC South'],
  ['NO', 'Saints', 'New Orleans Saints', '#D3BC8D', 'NFC South'],
  ['TB', 'Buccaneers', 'Tampa Bay Buccaneers', '#D50A0A', 'NFC South'],
  ['ARI', 'Cardinals', 'Arizona Cardinals', '#97233F', 'NFC West'],
  ['LA', 'Rams', 'Los Angeles Rams', '#003594', 'NFC West'],
  ['SF', '49ers', 'San Francisco 49ers', '#AA0000', 'NFC West'],
  ['SEA', 'Seahawks', 'Seattle Seahawks', '#002244', 'NFC West'],
];

export const SEASON_TEAM_INFO: TeamInfoResponse = {
  teams: Object.fromEntries(
    TEAM_LIST.map(([t, nick, name, color, div]) => [
      t,
      { nick, name, color, conf: div.slice(0, 3), div },
    ]),
  ),
};

const r1 = (v: number) => Math.round(v * 10) / 10;
const r3 = (v: number) => Math.round(v * 1000) / 1000;

// Week-5 Elo spread 1340–1680, a deterministic wobble for weeks 1–4.
const ELO_WEEKS = [1, 2, 3, 4, 5];
const eloHistory = TEAM_LIST.map(([t], i) => {
  const end = 1680 - ((i * 13) % 32) * (340 / 31);
  const hist = ELO_WEEKS.map((w) =>
    r1(end - (5 - w) * 9 * Math.sin(i * 1.7 + w) - (5 - w) * ((i % 5) - 2) * 2.5),
  );
  return { team: t, hist };
});

const rankBy = (week: number) =>
  [...eloHistory].sort((a, b) => b.hist[week - 1] - a.hist[week - 1]).map((x) => x.team);
const NOW = rankBy(5);
const PREV = rankBy(4);

/** Week 5's slate (a few games), week 4's finals and week 6's slate for the detail row. */
const W5_GAMES: [string, string, number][] = [
  ['NE', 'BUF', 0.71],
  ['DAL', 'PHI', 0.64],
  ['DET', 'GB', 0.48],
  ['SF', 'SEA', 0.42],
];

function brief(t: string, week: number): TeamRow['this_week'] {
  if (week === 5) {
    const g = W5_GAMES.find(([a, h]) => a === t || h === t);
    if (!g)
      return t === 'KC' || t === 'CAR'
        ? null
        : {
            week,
            game_id: `2026_05_X_${t}`,
            away: t,
            home: 'MIA',
            kickoff: '2026-10-11T17:00:00Z',
            p_home: 0.55,
            away_score: null,
            home_score: null,
          };
    return {
      week,
      game_id: `2026_05_${g[0]}_${g[1]}`,
      away: g[0],
      home: g[1],
      kickoff: '2026-10-11T17:00:00Z',
      p_home: g[2],
      away_score: null,
      home_score: null,
    };
  }
  if (t === 'SEA') return null; // a bye next week
  return {
    week,
    game_id: `2026_06_${t}_ATL`,
    away: t,
    home: 'ATL',
    kickoff: '2026-10-18T17:00:00Z',
    p_home: null,
    away_score: null,
    home_score: null,
  };
}

export const TEAMS: TeamsResponse = {
  season: 2026,
  week: 5,
  weeks: ELO_WEEKS,
  teams: NOW.map((t, idx) => {
    const i = TEAM_LIST.findIndex((x) => x[0] === t);
    const h = eloHistory[i].hist;
    const div = TEAM_LIST[i][4];
    const net = r3(0.18 - idx * 0.011);
    return {
      team: t,
      conf: div.slice(0, 3),
      div,
      rank: idx + 1,
      prev_rank: PREV.indexOf(t) + 1,
      elo: h[4],
      elo_change: r1(h[4] - h[3]),
      elo_by_week: ELO_WEEKS.map((w) => ({ week: w, elo: h[w - 1] })),
      net_epa: net,
      off_epa: r3(net / 2 + 0.02),
      def_epa: r3(0.02 - net / 2),
      pass_epa: r3(net * 1.3),
      rush_epa: r3(net * 0.4 - 0.01),
      this_week: brief(t, 5),
      next_week: brief(t, 6),
    };
  }),
  risers: [],
  fallers: [],
};
{
  const moves = [...TEAMS.teams].sort((a, b) => (b.elo_change ?? 0) - (a.elo_change ?? 0));
  TEAMS.risers = moves.slice(0, 5).map((x) => ({ team: x.team, change: x.elo_change ?? 0 }));
  TEAMS.fallers = moves
    .slice(-5)
    .reverse()
    .map((x) => ({ team: x.team, change: x.elo_change ?? 0 }));
}

// ---- Scorecard -------------------------------------------------------------------------------

export const SCORECARD_LIVE: ScorecardResponse = {
  season: 2026,
  source: 'live',
  label: '2026 live',
  through_week: 4,
  tiles: {
    brier_model: 0.2246,
    brier_elo: 0.2114,
    brier_market: 0.2172,
    pick_accuracy: 0.6,
    ece: 0.153,
    ece_chance: 0.118,
    games_graded: 15,
    weeks_published: 2,
    checks_passed: 2,
    checks_total: 2,
    llm_spend: 0.0271,
    player_improvement: 5.4,
  },
  weeks: [
    {
      week: 4,
      games: 15,
      brier_model: 0.2246,
      brier_elo: 0.2114,
      brier_market: 0.2172,
      cum_brier_model: 0.2246,
      cum_brier_elo: 0.2114,
      cum_brier_market: 0.2172,
      pick_accuracy: 0.6,
      cum_pick_accuracy: 0.6,
    },
  ],
  calibration: [
    { bin: 0, predicted: 0.32, observed: 0.25, games: 4 },
    { bin: 1, predicted: 0.52, observed: 0.4, games: 5 },
    { bin: 2, predicted: 0.68, observed: 0.5, games: 6 },
  ],
  player_groups: [
    { group: 'RB', improvement: 8.7, weeks: 1, live_weeks: 1 },
    { group: 'WR/TE', improvement: 6.1, weeks: 1, live_weeks: 1 },
    { group: 'QB', improvement: 5.7, weeks: 1, live_weeks: 1 },
    { group: 'LB/S', improvement: 0.6, weeks: 1, live_weeks: 1 },
  ],
  pipeline: [
    {
      week: 4,
      status: 'ok',
      on_time: false,
      hours_before_deadline: null,
      checks_passed: true,
      regenerated: true,
      seconds: 4512,
      drift_alerts: 0,
      launched_by: 'agent',
    },
    {
      week: 5,
      status: 'ok',
      on_time: true,
      hours_before_deadline: 47.5,
      checks_passed: true,
      regenerated: false,
      seconds: 989,
      drift_alerts: 0,
      launched_by: 'rishi',
    },
  ],
  dashboard: {
    title: '2026 Season Dashboard',
    url: 'https://wandb.ai/example-entity/nfl-analytics-engine/reports/2026-Season-Dashboard--abc123',
    current_run_id: 'dash0001',
  },
  notes: ["Week 5 is graded by week 6's run."],
};

/** 18 graded weeks; the cumulative lines settle near model 0.2050, Elo 0.2190, market 0.2056. */
function backtestWeeks(): ScorecardWeek[] {
  const out: ScorecardWeek[] = [];
  let n = 0;
  let sm = 0;
  let se = 0;
  let sk = 0;
  let hits = 0;
  for (let w = 1; w <= 18; w++) {
    const games = w === 18 ? 16 : w >= 5 && w <= 14 ? 14 : 16;
    const bm = 0.2005 + 0.012 * Math.sin(w * 1.3) + (w < 5 ? 0.008 : 0);
    const be = 0.2165 + 0.01 * Math.sin(w * 0.9 + 1);
    const bk = 0.2025 + 0.011 * Math.sin(w * 1.1 + 2) + (w < 5 ? 0.004 : 0);
    const acc = 0.67 + 0.08 * Math.sin(w * 1.7);
    n += games;
    sm += bm * games;
    se += be * games;
    sk += bk * games;
    hits += Math.round(acc * games);
    out.push({
      week: w,
      games,
      brier_model: Number(bm.toFixed(4)),
      brier_elo: Number(be.toFixed(4)),
      brier_market: Number(bk.toFixed(4)),
      cum_brier_model: Number((sm / n).toFixed(4)),
      cum_brier_elo: Number((se / n).toFixed(4)),
      cum_brier_market: Number((sk / n).toFixed(4)),
      pick_accuracy: Number((Math.round(acc * games) / games).toFixed(4)),
      cum_pick_accuracy: Number((hits / n).toFixed(4)),
    });
  }
  return out;
}
const BT_WEEKS = backtestWeeks();
const BT_LAST = BT_WEEKS[BT_WEEKS.length - 1];

export const SCORECARD_BACKTEST: ScorecardResponse = {
  season: 2025,
  source: 'backtest',
  label: '2025 backtest (example)',
  through_week: 18,
  tiles: {
    brier_model: BT_LAST.cum_brier_model,
    brier_elo: BT_LAST.cum_brier_elo,
    brier_market: BT_LAST.cum_brier_market,
    pick_accuracy: BT_LAST.cum_pick_accuracy,
    ece: 0.05,
    ece_chance: 0.041,
    games_graded: BT_WEEKS.reduce((a, w) => a + (w.games ?? 0), 0),
    weeks_published: null,
    checks_passed: null,
    checks_total: null,
    llm_spend: null,
    player_improvement: 5.3,
  },
  weeks: BT_WEEKS,
  calibration: [
    { bin: 0, predicted: 0.07, observed: 0.0, games: 5 },
    { bin: 1, predicted: 0.16, observed: 0.18, games: 17 },
    { bin: 2, predicted: 0.26, observed: 0.22, games: 31 },
    { bin: 3, predicted: 0.35, observed: 0.39, games: 44 },
    { bin: 4, predicted: 0.45, observed: 0.43, games: 60 },
    { bin: 5, predicted: 0.55, observed: 0.58, games: 58 },
    { bin: 6, predicted: 0.65, observed: 0.6, games: 49 },
    { bin: 7, predicted: 0.74, observed: 0.79, games: 38 },
    { bin: 8, predicted: 0.84, observed: 0.86, games: 22 },
    { bin: 9, predicted: 0.93, observed: 1.0, games: 8 },
  ],
  player_groups: [
    { group: 'RB', improvement: 6.3, weeks: 18, live_weeks: 0 },
    { group: 'QB', improvement: 6.0, weeks: 18, live_weeks: 0 },
    { group: 'WR/TE', improvement: 5.6, weeks: 18, live_weeks: 0 },
    { group: 'LB/S', improvement: 4.7, weeks: 18, live_weeks: 0 },
    { group: 'EDGE/DL', improvement: 3.9, weeks: 18, live_weeks: 0 },
  ],
  pipeline: [],
  dashboard: { title: null, url: null, current_run_id: null },
  notes: ['Playoff games are left out of the weekly lines but counted in the calibration bins.'],
};

export const SCORECARD_EMPTY: ScorecardResponse = {
  season: 2026,
  source: 'live',
  label: '2026 live',
  through_week: null,
  tiles: {
    brier_model: null,
    brier_elo: null,
    brier_market: null,
    pick_accuracy: null,
    ece: null,
    ece_chance: null,
    games_graded: 0,
    weeks_published: 0,
    checks_passed: null,
    checks_total: null,
    llm_spend: null,
    player_improvement: null,
  },
  weeks: [],
  calibration: [],
  player_groups: [],
  pipeline: [],
  dashboard: { title: null, url: null, current_run_id: null },
  notes: [],
};

// ---- Models ----------------------------------------------------------------------------------

const PROJECT_URL = 'https://wandb.ai/example-entity/nfl-analytics-engine';

export const MODELS: ModelsResponse = {
  wandb: {
    available: true,
    reason: null,
    fetched_at: '2026-10-06T14:00:00Z',
    stale: false,
    project_url: PROJECT_URL,
  },
  models: [
    {
      id: 'game',
      title: 'Game model',
      production: {
        version: 'v5',
        label: 'game-model-v0:2026-w05',
        week_alias: '2026-w05',
        run_id: 'g4m3r0n5',
        source: 'wandb',
      },
      headline: [
        { label: 'Model only', value: 0.2199, unit: 'brier', detail: 'vs Elo 0.2221 · 2018–2025' },
        {
          label: 'Market-informed',
          value: 0.2102,
          unit: 'brier',
          detail: 'vs closing market 0.2104',
        },
        {
          label: 'Pick accuracy',
          value: 0.6625,
          unit: 'pct',
          detail: 'market-informed, every backtest game',
        },
      ],
      bars: null,
      settings: [
        { label: 'Version', value: 'v0' },
        { label: 'Refit', value: 'every Tuesday, walk-forward' },
        { label: 'v1 (trees on top)', value: 'evaluated, not promoted (D79)' },
      ],
      cards: [{ id: 'game-model-v0', title: 'Game model v0', kind: 'card' }],
      note: null,
    },
    {
      id: 'ratings',
      title: 'Team ratings & Elo',
      production: {
        version: null,
        label: 'rebuilt every Tuesday (features/team_ratings, team_elo)',
        week_alias: null,
        run_id: 'r4t1ng05',
        source: 'local',
      },
      headline: [
        { label: 'Rating MSE', value: 0.1041, unit: 'mse', detail: 'vs 0.1194 / 0.1249 baselines' },
        { label: 'Elo Brier', value: 0.2214, unit: 'brier', detail: 'walk-forward, 11 seasons' },
      ],
      bars: null,
      settings: [
        { label: 'Settings', value: 'half-life 12 · prior 0.1 · alpha 250 (D46)' },
        { label: 'Trends', value: 'descriptive only (D47)' },
      ],
      cards: [{ id: 'team-ratings', title: 'Team ratings', kind: 'card' }],
      note: 'Refit inside the weekly run; not a W&B artifact.',
    },
    {
      id: 'player',
      title: 'Player models',
      production: {
        version: 'v2',
        label: 'player-model-v1:2026-w05',
        week_alias: '2026-w05',
        run_id: 'p1ay3r05',
        source: 'wandb',
      },
      headline: [{ label: 'Live stats', value: 23, unit: 'count', detail: '11 P06 + 12 P08' }],
      bars: [
        { label: 'rec_yds · WR/TE', value: 9.2, metric: 'mae' },
        { label: 'rush_yds · RB', value: 7.9, metric: 'mae' },
        { label: 'scrim_yds · RB', value: 7.4, metric: 'mae' },
        { label: 'pass_yds · QB', value: 5.0, metric: 'mae' },
        { label: 'receptions · RB', value: 2.7, metric: 'mae' },
        { label: 'anytime_td · RB', value: 4.1, metric: 'brier' },
        { label: 'sack_made · EDGE/DL', value: 2.6, metric: 'brier' },
      ],
      settings: [{ label: 'Backtest', value: '2019–2025 walk-forward' }],
      cards: [
        { id: 'player-model-v1', title: 'Player model v1', kind: 'card' },
        { id: 'player-model-v1-qb', title: 'QB', kind: 'card' },
      ],
      note: 'All 11 P06 stats beat the rolling baseline in 7 of 7 seasons.',
    },
    {
      id: 'team',
      title: 'Team stat totals',
      production: {
        version: 'v0',
        label: 'team-model-v1:2026-w05',
        week_alias: '2026-w05',
        run_id: 't3am0005',
        source: 'wandb',
      },
      headline: [{ label: 'Shipped', value: 4, unit: 'count', detail: 'of 5 evaluated (D80)' }],
      bars: [
        { label: 'pass_yds', value: 2.5, metric: 'mae' },
        { label: 'rush_yds', value: 1.58, metric: 'mae' },
        { label: 'sacks_taken', value: 0.06, metric: 'mae' },
      ],
      settings: [{ label: 'Shipped', value: '4 of 5 (D80)' }],
      cards: [{ id: 'team-totals', title: 'Team stat totals', kind: 'card' }],
      note: null,
    },
    {
      id: 'writer',
      title: 'Digest writer',
      production: {
        version: null,
        label: 'z-ai/glm-5.3-flash',
        week_alias: null,
        run_id: null,
        source: 'local',
      },
      headline: [],
      bars: null,
      settings: [
        { label: 'Writer', value: 'z-ai/glm-5.3-flash', mono: true },
        { label: 'Route', value: 'OpenRouter · BaseTen → Novita → Relace (D88)' },
        { label: 'Prompt', value: '7a529f64c5a6', mono: true },
        { label: 'Checks', value: '11 (provenance, binding, meaning, length …)' },
      ],
      cards: [{ id: 'llm-digest-writer', title: 'The digest writer', kind: 'guide' }],
      note: null,
    },
  ],
};

export const MODELS_NO_WANDB: ModelsResponse = {
  wandb: {
    available: false,
    reason: "WANDB_API_KEY isn't set",
    fetched_at: null,
    stale: false,
    project_url: null,
  },
  models: MODELS.models.map((m) =>
    m.production.version
      ? { ...m, production: { ...m.production, source: 'local' as const, run_id: null } }
      : m,
  ),
};

export const MODEL_CARD: ModelCardResponse = {
  id: 'game-model-v0',
  title: 'Game model v0',
  markdown: [
    '# Game model v0',
    '',
    'Predicts the **home win chance** and the score.',
    '',
    '| Season | Model | Elo |',
    '|---|---|---|',
    '| 2024 | 0.2190 | 0.2230 |',
    '| 2025 | 0.2181 | 0.2205 |',
    '',
    '- Walk-forward, refit every Tuesday',
    '',
    'See [the ratings card](team-ratings.md), [the design doc](../04-track1-models.md#b-game-model)',
    'and [the W&B run](https://wandb.ai/example-entity/nfl-analytics-engine/runs/abc123).',
  ].join('\n'),
};

export const RATINGS_CARD: ModelCardResponse = {
  id: 'team-ratings',
  title: 'Team ratings',
  markdown: '# Team ratings\n\nOpponent-adjusted EPA with a 12-week half-life.',
};

// ---- Alerts ----------------------------------------------------------------------------------

const EXAMPLE_ALERT = {
  season: 2025,
  week: 10,
  level: 'warn',
  title: 'calibration',
  text: 'ECE 0.053 over 135 graded games, above the flat 0.05 limit.',
  source: 'drift:calibration',
  run_url: null,
  notes: [
    'A perfectly calibrated model averages 0.082 on that many games, so the flat limit sat below the noise floor.',
    'D75: the limit became the noise level, falling from about 0.16 after five weeks to 0.08 by week 18.',
  ],
};

export const ALERTS_EMPTY: AlertsResponse = {
  season: 2026,
  current_week: 5,
  alerts: [],
  signals: [
    {
      name: 'data_freshness',
      label: 'Every source fresh for the week',
      needs: 'any run',
      first_week: null,
      first_text: 'any run',
      status_now: 'ok',
      detail_now: 'all sources fresh',
    },
    {
      name: 'checks',
      label: 'Digest checks pass rate',
      needs: '4 weeks of digests',
      first_week: 8,
      first_text: "week 8's run",
      status_now: 'insufficient_data',
      detail_now: '2 of 4 weeks',
    },
    {
      name: 'player_vs_baseline',
      label: 'Player model vs the rolling baseline',
      needs: '3 graded weeks',
      first_week: 7,
      first_text: "week 7's run",
      status_now: 'insufficient_data',
      detail_now: null,
    },
    {
      name: 'calibration',
      label: 'Calibration error vs the noise level',
      needs: '64 graded games',
      first_week: 8,
      first_text: 'about week 8',
      status_now: 'insufficient_data',
      detail_now: '15 of 64 games',
    },
    {
      name: 'game_vs_elo',
      label: 'Brier vs Elo over rolling 4-week windows',
      needs: '6 graded weeks',
      first_week: 10,
      first_text: "week 10's run",
      status_now: 'insufficient_data',
      detail_now: null,
    },
  ],
  runs: [
    {
      week: 5,
      alerts: 0,
      drift: [
        { name: 'data_freshness', group: null, status: 'ok' },
        { name: 'calibration', group: null, status: 'insufficient_data' },
        { name: 'player_vs_baseline', group: 'RB', status: 'insufficient_data' },
      ],
    },
  ],
  example: EXAMPLE_ALERT,
  example_note: 'From the 2025 week-10 simulation (P07): the alert that led to D75.',
};

export const ALERTS_WITH: AlertsResponse = {
  ...ALERTS_EMPTY,
  current_week: 9,
  alerts: [
    {
      season: 2026,
      week: 8,
      level: 'warn',
      title: 'Calibration drifting',
      text: 'ECE 0.171 over 70 graded games, above the noise level 0.160.',
      source: 'drift:calibration',
      run_url: `${PROJECT_URL}/runs/pipe0008`,
      notes: ['Mostly the 60–70% bin; watching one more week before acting.'],
    },
    {
      season: 2026,
      week: 7,
      level: 'error',
      title: 'The graph step failed',
      text: 'Neo4j did not start within 120 s.',
      source: 'step:graph',
      run_url: null,
      notes: [],
    },
  ],
  runs: [
    { week: 8, alerts: 1, drift: [{ name: 'calibration', group: null, status: 'alert' }] },
    {
      week: 7,
      alerts: 1,
      drift: [{ name: 'calibration', group: null, status: 'insufficient_data' }],
    },
  ],
};

// ---- Health ----------------------------------------------------------------------------------

export const HEALTH: HealthResponse = {
  checked_at: '2026-10-06T14:00:00Z',
  checking: false,
  services: [
    { name: 'Neo4j', status: 'ok', detail: 'neo4j 5.26.31 (community), gds 2.13.13, apoc 5.26.31' },
    { name: 'Docker', status: 'ok', detail: 'running · container nfl-neo4j' },
    { name: 'Weights & Biases', status: 'ok', detail: 'reachable · project nfl-analytics-engine' },
    { name: 'OpenRouter', status: 'ok', detail: 'routing check ok · BaseTen first' },
    { name: 'Data drive', status: 'ok', detail: '497 GB free' },
    { name: 'Run lock', status: 'ok', detail: 'free' },
  ],
  variables: [
    { name: 'NFL_DATA_ROOT', required: true, set: true },
    { name: 'NEO4J_PASSWORD', required: true, set: true },
    { name: 'WANDB_API_KEY', required: true, set: true },
    { name: 'OPENROUTER_API_KEY', required: false, set: true },
    { name: 'ODDS_API_KEY', required: false, set: true },
    { name: 'ODDS_API_KEY2', required: false, set: true },
    { name: 'KAGGLE_USERNAME', required: false, set: false },
    { name: 'KAGGLE_KEY', required: false, set: false },
  ],
  recent_runs: [
    {
      run_id: '20261006T140012Z-a1b2',
      season: 2026,
      week: 5,
      kind: 'weekly',
      started: '2026-10-06T14:00:12Z',
      finished: '2026-10-06T14:16:41Z',
      seconds: 989,
      command: 'nfl weekly run --auto --expect-week 5',
      status: 'ok',
      launched_by: 'rishi',
      via: 'control-room',
    },
    {
      run_id: null,
      season: 2026,
      week: 4,
      kind: 'terminal',
      started: '2026-10-04T08:23:00Z',
      finished: '2026-10-04T08:49:00Z',
      seconds: 1560,
      command: 'nfl weekly run --season 2026 --week 4 --from-step digest',
      status: 'ok',
      launched_by: 'agent',
      via: null,
    },
  ],
};

export const HEALTH_CHECKING: HealthResponse = {
  ...HEALTH,
  checked_at: null,
  checking: true,
  services: HEALTH.services.map((s) =>
    s.name === 'Weights & Biases' || s.name === 'OpenRouter'
      ? { ...s, status: 'checking' as const, detail: 'checking…' }
      : s,
  ),
};
