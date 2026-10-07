// Small synthetic API responses for the MLOps tab's tests. Made-up values in the real shapes
// (timings and counts echo a plausible week-5 run); no real data in git.

import type {
  ArtifactVersion,
  DriftRow,
  FreshnessRow,
  MlopsArtifactsResponse,
  MlopsHealthResponse,
  MlopsWandbResponse,
  WandbRun,
  WandbState,
} from '../../api/types';

export const PROJECT_URL = 'https://wandb.ai/example-entity/nfl-analytics-engine';
const run = (id: string) => `${PROJECT_URL}/runs/${id}`;

export const WANDB_OK: WandbState = {
  available: true,
  reason: null,
  fetched_at: '2026-10-06T21:10:00Z',
  stale: false,
  project_url: PROJECT_URL,
};
export const WANDB_DOWN: WandbState = {
  available: false,
  reason: "WANDB_API_KEY isn't set",
  fetched_at: null,
  stale: false,
  project_url: null,
};
export const WANDB_STALE: WandbState = {
  available: true,
  reason: "W&B didn't answer within 10 s",
  fetched_at: '2026-10-06T19:00:00Z',
  stale: true,
  project_url: PROJECT_URL,
};

// ---- Health ---------------------------------------------------------------------------------

const SNAPSHOT = '2026-10-06';

const INGEST: [source: string, dataset: string, rows: number][] = [
  ['nflverse', 'schedules', 285],
  ['nflverse', 'pbp', 18642],
  ['nflverse', 'player_stats', 5120],
  ['nflverse', 'team_stats', 128],
  ['nflverse', 'rosters', 2850],
  ['nflverse', 'injuries', 1904],
  ['nflverse', 'depth_charts', 9322],
  ['nflverse', 'snap_counts', 7210],
  ['nflverse', 'nextgen_passing', 140],
  ['nflverse', 'nextgen_rushing', 160],
  ['nflverse', 'nextgen_receiving', 410],
  ['nflverse', 'pfr_pass', 130],
  ['nflverse', 'pfr_rush', 280],
  ['nflverse', 'pfr_rec', 640],
  ['nflverse', 'pfr_def', 1020],
  ['nflverse', 'ftn_charting', 18110],
  ['nflverse', 'participation', 18040],
  ['nflverse', 'officials', 62],
  ['espn', 'espn_scoreboard', 64],
  ['espn', 'espn_news', 212],
  ['espn', 'espn_injuries', 480],
  ['espn', 'espn_depth', 1120],
  ['espn', 'espn_teams', 32],
  ['ngs_site', 'ngs_site_passing', 38],
  ['ngs_site', 'ngs_site_rushing', 44],
  ['open_meteo', 'weather_forecast', 15],
  ['open_meteo', 'weather_history', 60],
  ['odds_api', 'odds_h2h', 15],
  ['odds_api', 'odds_spreads', 15],
  ['odds_api', 'odds_totals', 15],
];

const FRESHNESS: FreshnessRow[] = INGEST.map(([source, dataset]) => ({
  source,
  dataset,
  snapshot_date: SNAPSHOT,
  age_days: 1,
  newest_week: null,
  expected_week: null,
  status: 'ok',
  stale: false,
  note: dataset === 'participation' ? 'last ingest: ok; research only' : 'last ingest: ok',
}));

const QUALITY: [name: string, level: string][] = [
  ['schedule_keys_unique', 'block'],
  ['pbp_game_ids_in_schedule', 'block'],
  ['pbp_rows_per_game', 'block'],
  ['player_stats_ids_resolved', 'block'],
  ['team_codes_canonical', 'block'],
  ['week_numbers_in_range', 'block'],
  ['scores_non_negative', 'block'],
  ['snap_counts_join_rate', 'block'],
  ['injuries_status_values', 'block'],
  ['depth_chart_one_starter_per_slot', 'block'],
  ['kickoff_utc_present', 'block'],
  ['espn_team_map', 'block'],
  ['nextgen_week_coverage', 'warn'],
  ['pfr_week_coverage', 'warn'],
  ['ftn_week_coverage', 'warn'],
  ['weather_dome_flags', 'warn'],
  ['odds_rows_per_game', 'warn'],
];

const WAITING = '1 graded week(s) so far; the check needs 6 (4-week window, 3 windows in a row).';
const player = (name: string, group: string): DriftRow => ({
  name,
  group,
  status: 'insufficient_data',
  value: null,
  threshold: 0,
  detail: `${group}: 4 scored week(s) so far; the check needs 6 (4-week window, 3 windows in a row).`,
  response: 'No action needed.',
});

export const DRIFT: DriftRow[] = [
  { name: 'game_vs_elo', group: null, status: 'insufficient_data', value: null, threshold: null, detail: WAITING, response: 'No action needed.' },
  {
    name: 'calibration',
    group: null,
    status: 'insufficient_data',
    value: null,
    threshold: null,
    detail: '15 graded game(s) so far; the check needs 64.',
    response: 'No action needed.',
  },
  ...['QB', 'RB', 'WR/TE', 'EDGE/DL', 'LB/S', 'CB/S', 'TEAM'].map((g) => player('player_vs_baseline', g)),
  ...['CB/S', 'EDGE/DL', 'QB', 'RB', 'TEAM', 'WR/TE'].map((g) => player('player_prob_vs_baseline', g)),
  {
    name: 'data_freshness',
    group: null,
    status: 'ok',
    value: 0,
    threshold: 1,
    detail: 'No snapshot is older than 7 days and every source is on its expected week.',
    response: 'No action needed.',
  },
  {
    name: 'checks',
    group: null,
    status: 'insufficient_data',
    value: null,
    threshold: null,
    detail: '1 digest(s) so far; the check needs 4.',
    response: 'No action needed.',
  },
];

const STEPS: [string, number][] = [
  ['ingest', 23],
  ['ready', 0],
  ['curate', 15],
  ['ratings', 3],
  ['game', 17],
  ['graph', 267],
  ['player', 131],
  ['digest', 533],
  ['records', 9],
];

/** Week 5: full run records. No run record for week 4, so "last week" is empty. */
export const HEALTH_W5: MlopsHealthResponse = {
  season: 2026,
  week: 5,
  records: 'full',
  notice: null,
  tiles: {
    run: { status: 'ok', steps_ok: 8, steps_total: 8, degraded: 0, failed_step: null },
    data: { datasets: 30, failures: 0, snapshot_date: SNAPSHOT },
    quality: { passed: 17, total: 17, blocking: 12, blocking_failed: 0 },
    drift: { alerts: 0, signals: 6, insufficient: 16 }, // the tile counts rows as insufficient, names as signals
    projections: { players: 1862, teams: 30, graph_written: 1862 },
  },
  steps: STEPS.map(([step, seconds]) => ({ step, status: 'ok', seconds })),
  long_pole: { step: 'digest', share: 0.5389, seconds: 533 }, // the share is of the 989 s in steps (records isn't in it)
  what_ran: {
    game_model: 'game-model-v0:2026-w05',
    trained_through: '2026-w04',
    player_model: 'player-model-v1:2026-w05',
    player_stats: 23,
    team_model: 'team-model-v1:2026-w05',
    graph: { built_at: '2026-10-06T16:31:12-04:00', nodes: 17837, relationships: 596237, status: 'ok' },
    writer: { model: 'z-ai/glm-5.3-flash', route: 'openrouter', providers: ['Novita'], calls: 1 },
    prompt_id: '248ed5fb0b33',
    promoted: ['game-model', 'player-model', 'team-model'],
    command: 'nfl weekly run --auto --expect-week 5',
    launched_by: 'agent',
    via: 'control-room',
  },
  freshness: FRESHNESS,
  vs_last: [
    { measure: 'Time in steps', unit: 'seconds', this_week: 989, last_week: null, trend: [{ week: 5, value: 989 }] },
    { measure: 'GLM writing the digest', unit: 'seconds', this_week: 527, last_week: null, trend: [{ week: 5, value: 527 }] },
    { measure: 'LLM cost', unit: 'usd', this_week: 0.0108, last_week: null, trend: [{ week: 5, value: 0.0108 }] },
    { measure: 'Games predicted', unit: 'count', this_week: 15, last_week: null, trend: [{ week: 5, value: 15 }] },
    { measure: 'Graph nodes', unit: 'count', this_week: 17837, last_week: null, trend: [{ week: 5, value: 17837 }] },
    { measure: 'Digest passed first time', unit: 'flag', this_week: 1, last_week: null, trend: [{ week: 5, value: 1 }] },
  ],
  ingest: {
    manifest: 'raw/_runs/ingest-20261006T202805.json',
    snapshot_date: SNAPSHOT,
    rows: INGEST.map(([source, dataset, rows]) => ({ source, dataset, rows, status: 'ok', detail: null })),
  },
  quality: {
    run_at: '2026-10-06T20:29:30Z',
    match: 'this_run',
    checks: QUALITY.map(([name, level]) => ({ name, level, passed: true, detail: level === 'block' ? 'stops the run when it fails' : null })),
    failed: [],
  },
  drift: DRIFT,
};

/** Week 6: a run with a week to compare against (week 5's numbers). */
export const HEALTH_W6: MlopsHealthResponse = {
  ...HEALTH_W5,
  week: 6,
  vs_last: [
    {
      measure: 'Time in steps',
      unit: 'seconds',
      this_week: 812,
      last_week: 989,
      trend: [
        { week: 5, value: 989 },
        { week: 6, value: 812 },
      ],
    },
    {
      measure: 'LLM cost',
      unit: 'usd',
      this_week: 0.0149,
      last_week: 0.0108,
      trend: [
        { week: 5, value: 0.0108 },
        { week: 6, value: 0.0149 },
      ],
    },
    {
      measure: 'Games predicted',
      unit: 'count',
      this_week: 14,
      last_week: 15,
      trend: [
        { week: 5, value: 15 },
        { week: 6, value: 14 },
      ],
    },
    {
      measure: 'Graph nodes',
      unit: 'count',
      this_week: 17837,
      last_week: 17837,
      trend: [
        { week: 5, value: 17837 },
        { week: 6, value: 17837 },
      ],
    },
    {
      measure: 'Digest passed first time',
      unit: 'flag',
      this_week: 0,
      last_week: 1,
      trend: [
        { week: 5, value: 1 },
        { week: 6, value: 0 },
      ],
    },
  ],
};

/** Week 5 with a stale source, a failed dataset, a failed warn check and a later quality file. */
export const HEALTH_PROBLEMS: MlopsHealthResponse = {
  ...HEALTH_W5,
  tiles: {
    ...HEALTH_W5.tiles,
    run: { status: 'degraded', steps_ok: 7, steps_total: 8, degraded: 1, failed_step: null },
    data: { datasets: 30, failures: 1, snapshot_date: SNAPSHOT },
    quality: { passed: 16, total: 17, blocking: 12, blocking_failed: 0 },
    drift: { alerts: 1, signals: 5, insufficient: 3 },
  },
  steps: HEALTH_W5.steps.map((s) => (s.step === 'graph' ? { ...s, status: 'degraded' } : s)),
  freshness: FRESHNESS.map((f): FreshnessRow => {
    if (f.dataset === 'pfr_rec') {
      return { ...f, snapshot_date: '2026-09-27', age_days: 9, newest_week: 3, expected_week: 4, stale: true, note: 'nflverse is a week behind' };
    }
    if (f.dataset === 'odds_totals') return { ...f, status: 'failed', note: 'The Odds API answered 429' };
    return f;
  }),
  ingest: {
    ...HEALTH_W5.ingest,
    rows: HEALTH_W5.ingest.rows.map((r) => (r.dataset === 'odds_totals' ? { ...r, rows: null, status: 'failed', detail: 'HTTP 429' } : r)),
  },
  quality: {
    run_at: '2026-10-07T09:00:00Z',
    match: 'later',
    checks: HEALTH_W5.quality.checks.map((c) => (c.name === 'odds_rows_per_game' ? { ...c, passed: false, detail: '13 of 15 games have odds' } : c)),
    failed: ['odds_rows_per_game'],
  },
  drift: DRIFT.map((d) =>
    d.name === 'calibration'
      ? { ...d, status: 'alert', value: 0.061, threshold: 0.05, detail: 'Season ECE is above the noise level.', response: 'Look at the calibration chart before trusting the picks.' }
      : d,
  ),
};

/** Week 4: ran before run records existed (no run_summary.json). */
export const HEALTH_W4: MlopsHealthResponse = {
  season: 2026,
  week: 4,
  records: 'partial',
  notice: 'Week 4 ran before run records existed (P07), so there is no run_summary.json. This is what weekly_run.json holds.',
  tiles: {
    run: { status: 'ok', steps_ok: 8, steps_total: 8, degraded: 0, failed_step: null },
    data: { datasets: null, failures: null, snapshot_date: null },
    quality: { passed: null, total: null, blocking: null, blocking_failed: null },
    drift: { alerts: null, signals: 0, insufficient: 0 },
    projections: { players: null, teams: null, graph_written: null },
  },
  steps: [
    { step: 'ingest', status: 'ok', seconds: 20 },
    { step: 'curate', status: 'ok', seconds: 14 },
    { step: 'graph', status: 'ok', seconds: 250 },
    { step: 'digest', status: 'ok', seconds: 1485 },
  ],
  long_pole: { step: 'digest', share: 0.84, seconds: 1485 },
  what_ran: {
    game_model: 'game-model-v0:2026-w04',
    trained_through: null,
    player_model: null,
    player_stats: null,
    team_model: null,
    graph: null,
    writer: null,
    prompt_id: null,
    promoted: [],
    command: null,
    launched_by: null,
    via: null,
  },
  freshness: [],
  vs_last: [],
  ingest: { manifest: null, snapshot_date: null, rows: [] },
  quality: { run_at: '2026-10-06T20:28:20', match: 'later', checks: [], failed: [] },
  drift: [],
};

/** A week with no run at all. */
export const HEALTH_NONE: MlopsHealthResponse = {
  ...HEALTH_W4,
  week: 7,
  records: 'none',
  notice: null,
  steps: [],
  long_pole: null,
};

// ---- W&B runs -------------------------------------------------------------------------------

const wr = (id: string, name: string, group: string, job_type: string, job: WandbRun['job'], extra: Partial<WandbRun> = {}): WandbRun => ({
  id,
  name,
  group,
  job_type,
  job,
  state: 'finished',
  created_at: '2026-10-06T20:30:00Z',
  url: run(id),
  tags: ['season:2026', 'week:05'],
  current: true,
  ...extra,
});

const RUNS: WandbRun[] = [
  wr('hkf2hana', 'season-2026-w05', 'season-dashboard', 'dashboard', 'dashboard'),
  wr('i8fhvizy', 'pipeline-2026-w05', 'weekly-pipeline', 'pipeline', 'pipeline'),
  wr('qlr4f8yk', 'digest-2026-w05', 'weekly-pipeline', 'main', 'digest'),
  wr('ae8potfn', 'train-2026-w05', 'track1-team', 'train', 'team'),
  wr('zjwbooy5', 'train-2026-w05', 'track1-player', 'train', 'player'),
  wr('212dlxyp', 'scoreboard-2026-w04', 'track1-player', 'eval', 'scoreboard'),
  wr('73xjb0l7', 'graph-2026-w05', 'track1-graph', 'build', 'graph'),
  wr('1jmmh8g9', 'train-2026-w05', 'track1-game', 'train', 'game'),
  wr('b7d2k9ve', 'digest-2026-w05', 'weekly-pipeline', 'main', 'digest', { current: false, created_at: '2026-10-06T19:00:00Z' }),
];

const GAMES = [
  ['BAL', 'KC', 0.58, 0.51],
  ['BUF', 'MIA', 0.64, 0.7],
  ['DET', 'GB', 0.55, 0.52],
  ['DAL', 'PHI', 0.37, 0.44],
  ['SEA', 'SF', 0.4, 0.41],
  ['NYJ', 'NE', 0.46, 0.38],
] as const;

const WANDB_CARDS: MlopsWandbResponse['cards'] = {
  game_fit: {
    run_name: 'train-2026-w05',
    run_id: '1jmmh8g9',
    url: run('1jmmh8g9'),
    metrics: 'slate/*',
    source: 'predictions_games.parquet',
    note: null,
    games: [
      ...GAMES.map(([away, home, m, k]) => ({ game_id: `2026_05_${away}_${home}`, away, home, p_model_only: m, p_market: k })),
      { game_id: '2026_05_CHI_MIN', away: 'CHI', home: 'MIN', p_model_only: 0.49, p_market: null },
    ],
  },
  player_scoreboard: {
    run_name: 'scoreboard-2026-w04',
    run_id: '212dlxyp',
    url: run('212dlxyp'),
    metrics: 'scoreboard/improvement_*',
    source: 'results/player_scoreboard.parquet',
    note: null,
    scored_week: 4,
    mode: 'live',
    groups: [
      { group: 'QB', improvement: 5.8, scored: 92 },
      { group: 'WR', improvement: 4.1, scored: 520 },
      { group: 'RB', improvement: 3.2, scored: 310 },
      { group: 'DB', improvement: -0.8, scored: 520 },
      { group: 'TE', improvement: null, scored: 0 },
    ],
  },
  player_fit: {
    run_name: 'train-2026-w05',
    run_id: 'zjwbooy5',
    url: run('zjwbooy5'),
    metrics: 'projections_per_target',
    source: 'predictions_players.parquet',
    note: null,
    stats: [
      { target: 'passing_yards', group: 'QB', label: 'passing yards', count: 32 },
      { target: 'rushing_yards', group: 'RB', label: 'rushing yards', count: 110 },
      { target: 'receiving_yards', group: 'WR', label: 'receiving yards', count: 260 },
      { target: 'receptions', group: 'WR', label: 'receptions', count: 260 },
    ],
  },
  graph_build: {
    run_name: 'graph-2026-w05',
    run_id: '73xjb0l7',
    url: run('73xjb0l7'),
    metrics: 'load/* · query_seconds',
    source: 'graph_results.json',
    note: null,
    stages: [
      { stage: 'wipe', seconds: 122.3 },
      { stage: 'load', seconds: 63.1 },
      { stage: 'gds', seconds: 8.2 },
      { stage: 'queries', seconds: 4.3 },
      { stage: 'tables', seconds: 0.6 },
      { stage: 'counts', seconds: 0.4 },
      { stage: 'schema', seconds: 0.4 },
      { stage: 'inputs', seconds: 0.2 },
    ],
    nodes: 17837,
    relationships: 596237,
    mismatches: 0,
    total_seconds: 199.6,
  },
  digest: {
    run_name: 'digest-2026-w05',
    run_id: 'qlr4f8yk',
    url: run('qlr4f8yk'),
    metrics: 'words_per_section · check_issue_counts',
    source: 'checks.json',
    note: null,
    words: [
      { section: 'game_outlook', words: 79, budget: 120 },
      { section: 'matchup_risk', words: 84, budget: 120 },
      { section: 'non_obvious', words: 105, budget: 150 },
      { section: 'players_to_watch', words: 196, budget: 180 },
      { section: 'report_card', words: 54, budget: 80 },
      { section: 'team_trends', words: 130, budget: 160 },
      { section: 'under_the_hood', words: 96, budget: null },
    ],
    checks: [
      { name: 'number_provenance', issues: 0, level: 'fail' },
      { name: 'entity_binding', issues: 0, level: 'fail' },
      { name: 'banned_language', issues: 0, level: 'warn' },
    ],
  },
  pipeline: {
    run_name: 'pipeline-2026-w05',
    run_id: 'i8fhvizy',
    url: run('i8fhvizy'),
    metrics: 'step_seconds',
    source: 'run_summary.json',
    note: null,
    steps: STEPS.map(([step, seconds]) => ({ step, seconds, status: 'ok' })),
    stale_sources: 0,
    drift: DRIFT.map(({ name, group, status }) => ({ name, group, status })),
  },
};

export const WANDB_W5: MlopsWandbResponse = {
  season: 2026,
  week: 5,
  wandb: WANDB_OK,
  runs: RUNS,
  local_links: [],
  dashboard: {
    title: '2026 Season Dashboard',
    url: `${PROJECT_URL}/reports/2026-Season-Dashboard--example`,
    current_run_id: 'hkf2hana',
    current_run_week: 5,
    week_run_id: 'hkf2hana',
  },
  cards: WANDB_CARDS,
};

/** W&B can't be reached: no run list, the links come from the steps' log lines, charts are local. */
export const WANDB_W5_DOWN: MlopsWandbResponse = {
  ...WANDB_W5,
  wandb: WANDB_DOWN,
  runs: [],
  local_links: [
    { job: 'game', run_id: '1jmmh8g9', url: run('1jmmh8g9') },
    { job: 'graph', run_id: '73xjb0l7', url: run('73xjb0l7') },
    { job: 'digest', run_id: 'qlr4f8yk', url: run('qlr4f8yk') },
  ],
  dashboard: { title: null, url: null, current_run_id: null, current_run_week: null, week_run_id: null },
};

/** Week 4: before the pipeline run existed, so that card is empty with its note. */
export const WANDB_W4: MlopsWandbResponse = {
  ...WANDB_W5,
  week: 4,
  runs: RUNS.slice(2, 3).map((r) => ({ ...r, name: 'digest-2026-w04' })),
  dashboard: { ...WANDB_W5.dashboard, week_run_id: null },
  cards: {
    ...WANDB_CARDS,
    pipeline: {
      ...WANDB_CARDS.pipeline,
      run_name: 'pipeline-2026-w04',
      run_id: null,
      url: null,
      steps: [],
      stale_sources: null,
      drift: [],
      note: 'Week 4 ran before the pipeline run existed (P07).',
    },
  },
};

// ---- Artifacts ------------------------------------------------------------------------------

const ver = (version: string, created: string, aliases: string[], by: string, size: number): ArtifactVersion => ({
  version,
  created_at: created,
  aliases,
  logged_by: by,
  logged_by_url: run(by),
  size,
});

/** Newest first: versions v(n-1) … v0, with the given aliases on the newest two. */
function versions(prefix: string, count: number, newest: [string[], string, number], previous: [string[], string], size: number): ArtifactVersion[] {
  return Array.from({ length: count }, (_, i) => {
    const n = count - 1 - i;
    if (i === 0) return ver(`v${n}`, '2026-10-06T20:40:00Z', newest[0], newest[1], newest[2]);
    if (i === 1) return ver(`v${n}`, '2026-10-02T14:10:00Z', previous[0], previous[1], size);
    return ver(`v${n}`, `2026-09-${String(30 - i).padStart(2, '0')}T12:00:00Z`, [], `${prefix}${n}`, size);
  });
}

export const ARTIFACTS_W5: MlopsArtifactsResponse = {
  season: 2026,
  week: 5,
  wandb: WANDB_OK,
  production: [
    { name: 'game-model', version: 'v5', week_alias: '2026-w05', run_id: '1jmmh8g9' },
    { name: 'player-model', version: 'v2', week_alias: '2026-w05', run_id: 'zjwbooy5' },
    { name: 'team-model', version: 'v0', week_alias: '2026-w05', run_id: 'ae8potfn' },
  ],
  total_versions: 23,
  collections_count: 5,
  lineage: {
    source: 'pipeline_run',
    pipeline_run: 'i8fhvizy',
    items: [
      { role: 'model', name: 'game-model', version: 'v5' },
      { role: 'graph', name: 'graph-results', version: 'v2' },
      { role: 'model', name: 'player-model', version: 'v2' },
      { role: 'model', name: 'team-model', version: 'v0' },
      { role: 'published', name: 'digest', version: 'v9' },
    ],
    note: "From the week's pipeline run (i8fhvizy): the artifacts it used in W&B.",
  },
  collections: [
    {
      name: 'game-model',
      type: 'model',
      note: 'weekly game fit',
      url: `${PROJECT_URL}/artifacts/model/game-model`,
      total: 6,
      versions: versions('gm', 6, [['production', '2026-w05', 'latest'], '1jmmh8g9', 7792], [['2026-w04'], 'st8440zw'], 7600),
      first_note: null,
    },
    {
      name: 'player-model',
      type: 'model',
      note: 'all player stats, one artifact',
      url: `${PROJECT_URL}/artifacts/model/player-model`,
      total: 3,
      versions: versions('pm', 3, [['2026-w05', 'latest', 'production'], 'zjwbooy5', 11_400_000], [['2026-w04'], 'kl4fzvl8'], 10_900_000),
      first_note: null,
    },
    {
      name: 'graph-results',
      type: 'graph',
      note: 'graph_results.json per build',
      url: `${PROJECT_URL}/artifacts/graph/graph-results`,
      total: 3,
      versions: versions('gr', 3, [['2026-w05', 'latest'], '73xjb0l7', 480_000], [['2026-w04'], 'g95ulxqj'], 470_000),
      first_note: null,
    },
    {
      name: 'digest',
      type: 'digest',
      note: 'payload, checks, raw LLM output, digest.md',
      url: `${PROJECT_URL}/artifacts/digest/digest`,
      total: 10,
      versions: versions('dg', 10, [['2026-w05', 'latest'], 'qlr4f8yk', 96_000], [['2026-w04'], '0eh6h6ll'], 91_000),
      first_note: null,
    },
    {
      name: 'team-model',
      type: 'model',
      note: 'team stat totals (P08)',
      url: `${PROJECT_URL}/artifacts/model/team-model`,
      total: 1,
      versions: [ver('v0', '2026-10-06T20:38:00Z', ['production', '2026-w05', 'latest'], 'ae8potfn', 640_907)],
      first_note: null,
    },
    {
      name: 'injury-update',
      type: 'digest',
      note: 'Saturday comparison',
      url: `${PROJECT_URL}/artifacts/digest/injury-update`,
      total: 0,
      versions: [],
      first_note: 'The first one comes from the first Saturday injury update.',
    },
  ],
  local_models: null,
};

export const ARTIFACTS_W5_DOWN: MlopsArtifactsResponse = {
  ...ARTIFACTS_W5,
  wandb: WANDB_DOWN,
  production: [],
  total_versions: null,
  collections_count: null,
  lineage: { source: 'none', pipeline_run: null, items: [], note: null },
  collections: [],
  local_models: {
    game: ['game-model-v0:2026-w05'],
    player: ['player-model-v1:2026-w05'],
    team: ['team-model-v1:2026-w05'],
  },
};

/** W&B answered but left `project_url` null (as the first real answers did): links come from the rows. */
export const WANDB_W5_NO_PROJECT: MlopsWandbResponse = { ...WANDB_W5, wandb: { ...WANDB_OK, project_url: null } };
export const ARTIFACTS_W5_NO_PROJECT: MlopsArtifactsResponse = {
  ...ARTIFACTS_W5,
  wandb: { ...WANDB_OK, project_url: null },
  lineage: { ...ARTIFACTS_W5.lineage, note: null },
};
