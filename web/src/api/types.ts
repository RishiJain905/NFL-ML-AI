// Shapes of the control room's JSON API (src/nflengine/app/). Times are ISO strings.

export interface Calendar {
  season: number;
  week: number | null;
  phase: 'preseason' | 'regular' | 'playoffs' | 'offseason' | string;
  previous_week: number | null;
  previous_games: number;
  previous_final: number;
  previous_complete: boolean;
  deadline: string | null;
  hours_to_deadline: number | null;
  deadline_passed: boolean;
  retry_until: string | null;
  started_games: number;
  games: number;
  game_type: string | null;
  days: string[];
  special: string[];
  byes: string[];
  neutral_sites: string[];
  international: string[];
  notes: string[];
}

export interface Meta {
  app: string;
  version: string;
  now: string;
  mode: { dev: boolean; rehearsal: boolean };
  data_root: { found: boolean; free_gb?: number; message?: string };
  lock: { held: boolean; command: string | null; started: string | null };
  neo4j: { status: 'up' | 'down' | 'checking' | 'config_problem'; reason: string; checked_at: string | null };
  calendar: Calendar | null;
  current_season?: number;
}

export type WeekStatus =
  | 'published'
  | 'running'
  | 'failed'
  | 'partial'
  | 'ready'
  | 'waiting'
  | 'not_run';

export interface WeekEntry {
  season: number;
  week: number;
  status: WeekStatus;
  label: string;
  detail: string | null;
  is_current: boolean;
  has_run: boolean;
  has_run_summary: boolean;
  published_at: string | null;
  on_time: boolean | null;
  steps: Record<string, string>;
}

export interface WeeksResponse {
  season: number;
  current_week: number | null;
  phase: string | null;
  go_live_week: number | null;
  weeks: WeekEntry[];
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

// ---------------------------------------------------------------- CR01: the week archive
// One endpoint per week tab: GET /api/weeks/{season}/{week}/{pipeline,digest,games,players,
// results,graph}, plus the header (GET /api/weeks/{season}/{week}) and team colours
// (GET /api/team-info). Field names never contain "key", "token", "auth", "secret" or
// "password": the security scan treats `"<such a name>": value` as a leaked credential.
// Probabilities are 0–1 for the HOME team unless named otherwise; times are ISO strings (UTC
// for kickoffs, local with an offset for step times).

export interface TeamInfo {
  nick: string; // "Commanders"
  name: string; // "Washington Commanders"
  color: string; // "#5A1414"
  conf: string; // "NFC"
  div: string; // "NFC East"
}
export interface TeamInfoResponse {
  teams: Record<string, TeamInfo>; // by abbreviation ("WAS")
}

/** GET /api/weeks/{season}/{week}: the header above the tabs. */
export interface WeekDetail {
  season: number;
  week: number;
  title: string; // "Week 4", or the playoff round's name
  entry: WeekEntry; // the same row the sidebar shows
  plan: Calendar | null; // the calendar's plan for this week (slate tags, deadline, retry window)
  is_current: boolean;
  published_at: string | null;
  on_time: boolean | null; // published before the first kickoff
  on_time_source: 'summary' | 'deadline' | null; // run_summary.json, or computed from the slate
  first_live_week: boolean; // the season's first week with a run (week 4 in 2026)
  wandb_url: string | null; // the week's digest run in W&B (from the digest step), if any
  tab_counts: { games: number | null; players: number | null; graph: number | null };
  last_published_week: number | null; // the newest published week of this season (empty-state links)
}

// ---- Pipeline ------------------------------------------------------------------------------

export type StepStatus = 'ok' | 'failed' | 'running' | 'pending' | 'skipped' | 'degraded' | 'none';
export type StepName = 'ingest' | 'ready' | 'curate' | 'ratings' | 'game' | 'graph' | 'player' | 'digest' | 'records';

export interface PipelineStep {
  step: StepName;
  status: StepStatus; // 'none' = not recorded (records before P07), 'pending' = not run yet
  seconds: number | null; // real time, finished steps only
  expected_seconds: number; // the plan's estimate (last published week's time, or a default)
  started: string | null;
  finished: string | null;
  detail: string | null; // scrubbed, data-root paths made relative, W&B URLs removed
  this_run: boolean | null; // ran in the last invocation (run_summary.json), when known
  wandb_url: string | null; // the step's W&B run, when its detail named one
}

export interface LogLine {
  ts: string; // "03:18:17" (local time of the run) or "" when unknown
  text: string;
  cls: '' | 'acc' | 'ok' | 'err' | 'warn' | 'dim';
}

/** GET /api/weeks/{season}/{week}/pipeline */
export interface PipelineResponse {
  season: number;
  week: number;
  /** finished: published · failed: a step failed · partial: stopped before the digest ·
   *  plan: the current week before its run · running: a run of this week holds the lock
   *  (CR01 shows the steps as weekly_run.json has them; CR02 streams them) · none: no run */
  state: 'finished' | 'failed' | 'partial' | 'plan' | 'running' | 'none';
  source: 'run_summary' | 'weekly_run' | 'plan' | 'none';
  steps: PipelineStep[]; // always the 9 steps in order, `records` last
  sittings: number; // separate invocations seen in the step times (week 4: 3)
  step_seconds: number | null; // sum of the finished steps' seconds
  expected_from: { season: number; week: number } | null; // where expected_seconds come from
  failed_step: StepName | null;
  error: string | null; // the run's error line (scrubbed), if any
  log: LogLine[];
  log_source: 'rebuilt' | 'calendar' | 'none'; // rebuilt from step details, or the plan's dry-run-like summary
  tiles: {
    checks_passed: number | null; // final checks that passed
    checks_total: number | null;
    first_time: boolean | null; // passed without a rewrite
    llm_cost: number | null; // dollars, all calls
    llm_calls: number;
    llm_provider: string | null; // the first call's provider ("DeepInfra")
    digest_share: number | null; // digest seconds / step_seconds (0–1)
  };
  no_run_records: boolean; // a week that ran before P07's run records (week 4)
}

// ---- Digest --------------------------------------------------------------------------------

export interface CheckResult {
  name: string; // "number_provenance"
  level: 'fail' | 'warn' | string;
  passed: boolean;
  issues: string[]; // scrubbed, capped
}
export interface CheckRound {
  passed: boolean;
  failed: string[];
  warnings: string[];
  word_counts: Record<string, number>; // section → words
  checks: CheckResult[];
}
export interface LlmCall {
  model: string;
  provider: string | null; // the upstream that served it ("DeepInfra", "BaseTen")
  latency_s: number | null;
  usage: { prompt: number | null; completion: number | null; reasoning: number | null };
  cost: number | null;
  finish_reason: string | null;
  regeneration: boolean;
  parsed: boolean | null;
}

/** GET /api/weeks/{season}/{week}/digest. Never carries the prompt or the LLM's raw text. */
export interface DigestResponse {
  season: number;
  week: number;
  status: 'published' | 'unpublished' | 'none'; // unpublished: the run folder's digest.md only
  markdown: string | null;
  checks: {
    passed: boolean;
    regenerated: boolean;
    banner: boolean;
    writers: string[];
    fallback_notes: string[];
    first_attempt: CheckRound | null;
    final: CheckRound | null;
  } | null;
  writer: {
    provider: string; // "openrouter" | "placeholder" | ...
    model: string;
    prompt: string | null; // the prompt id from the digest footer ("248ed5fb0b33")
    writers: string[];
    calls: LlmCall[];
    total_cost: number | null;
    total_seconds: number | null;
  } | null;
  wandb_url: string | null; // the digest run
  files: { label: string; path: string; exists: boolean }[]; // paths relative to the data root
  addendum: {
    markdown: string | null; // reports/<S>/week<NN>-injury-update.md, when material
    material: boolean | null;
    at: string | null;
  } | null;
}

// ---- Games ---------------------------------------------------------------------------------

export interface GameRow {
  game_id: string;
  kickoff: string; // UTC ISO
  home: string;
  away: string;
  neutral: boolean;
  stadium: string | null;
  p_home: number | null; // the digest's (market-informed when lines exist) home win chance
  p_home_model_only: number | null;
  p_elo: number | null;
  p_market: number | null;
  pts_home: number | null; // predicted
  pts_away: number | null;
  margin: number | null; // expected home margin (shown row)
  spread: number | null; // market spread, nflverse convention: + = the home team is favoured
  total: number | null; // market total
  confidence: string | null; // "toss-up" | "lean" | "solid" | "strong"
  market_fallback: boolean | null;
  qb_home: string | null;
  qb_away: string | null;
  qb_change_home: string | null; // the digest's QB-change text (payload.json → qb_changes)
  qb_change_away: string | null;
  predicted_after_kickoff: boolean; // made after its kickoff: never graded (the report card's rule)
  final: { home: number; away: number } | null;
  hit: boolean | null; // the pick was right (null: not final, not graded, a tie or a 50% call)
}

/** GET /api/weeks/{season}/{week}/games */
export interface GamesResponse {
  season: number;
  week: number;
  status: 'predicted' | 'slate' | 'none'; // slate: the current week before its run
  model: { version: string | null; trained_through: string | null; created_at: string | null } | null;
  games: GameRow[]; // kickoff order
  gaps: { game_id: string; gap: number }[]; // model-only minus market (home), biggest |gap| first, up to 3
  source: { kind: 'predictions' | 'schedule' | 'none'; snapshot_date: string | null };
  byes: string[];
  finals: number;
  lines_used: number; // games whose shown row used market lines
}

// ---- Players -------------------------------------------------------------------------------

export interface ProjectionRow {
  player: string; // a team row: the team's name
  player_id: string; // a team row: the team code
  team: string;
  opponent: string;
  home: boolean;
  position: string; // "WR"; "TEAM" for team rows
  group: string; // "QB" | "RB" | "WR/TE" | "EDGE/DL" | "LB/S" | "CB/S" | "TEAM"
  target: string;
  target_label: string;
  kind: 'amount' | 'count' | 'prob' | string;
  unit: string;
  is_main: boolean;
  projection: number | null; // the digest's center: P50, or the mean for counts
  chance: number | null; // P(at least one), for count and yes/no stats (TD, sack chance)
  p10: number | null;
  p90: number | null;
  baseline: number | null;
  vs_baseline: number | null;
  confidence: string | null;
  injury: string | null;
}
export interface WatchPick extends ProjectionRow {
  rank: number | null;
  side: 'offense' | 'defense' | null;
  source: string | null; // "model"
  driver: string | null; // the main driver's phrase, or null ("no single factor stands out")
  display: { projection: string; range: string; vs_baseline: string } | null; // as the digest wrote them
}
export interface ToughSpot {
  player: string;
  player_id: string;
  team: string;
  opponent: string;
  position: string;
  target_label: string;
  projection: number | null;
  baseline: number | null;
  display: { projection: string; range: string; vs_baseline: string };
}

/** GET /api/weeks/{season}/{week}/players?stats=main|all */
export interface PlayersResponse {
  season: number;
  week: number;
  status: 'projected' | 'none';
  stats: 'main' | 'all';
  model: { version: string | null; trained_through: string | null } | null;
  counts: { total: number; main: number; teams: number; stats: number; by_group: Record<string, number> };
  groups: string[]; // groups present, in display order
  watch: WatchPick[];
  tough_spots: ToughSpot[];
  rows: ProjectionRow[]; // main stat per group (or all stats), team rows included
}

// ---- Results -------------------------------------------------------------------------------

export interface ResultGame {
  game_id: string;
  home: string;
  away: string;
  pick: string | null; // the team the digest favoured
  pick_prob: number | null;
  final: { home: number; away: number } | null;
  graded: boolean; // final and predicted before kickoff
  hit: boolean | null;
  brier_model: number | null;
  brier_elo: number | null;
  brier_market: number | null;
  margin_error: number | null; // actual home margin minus the expected margin
  note: string | null; // "predicted after kickoff: not graded"
}
export interface ResultWatch {
  player: string;
  player_id: string;
  team: string;
  position: string;
  side: 'offense' | 'defense' | null;
  target_label: string;
  unit: string;
  projection: number | null;
  p10: number | null;
  p90: number | null;
  baseline: number | null;
  actual: number | null;
  played: boolean;
  inside: boolean | null; // inside the 80% range
  hit: boolean | null; // beat his baseline in the projected direction (the report card's rule)
  status: string; // "" | "did not play" | "no result yet"
  text: string; // the digest's look-back line
}
export interface ResultGroup {
  group: string;
  improvement_pct: number; // n-weighted MAE improvement vs the rolling baseline (+ = better)
  n: number;
  coverage_80: number | null;
  targets: { target: string; label: string; improvement_pct: number; n: number }[];
}

/** GET /api/weeks/{season}/{week}/results */
export type ResultsResponse =
  | {
      season: number;
      week: number;
      status: 'not_graded';
      graded_by_week: number | null; // null for the Super Bowl: the season review grades it
      graded_on: string | null; // the Tuesday the grading run is due (YYYY-MM-DD, ET)
      reason: string;
    }
  | {
      season: number;
      week: number;
      status: 'graded';
      graded_by_week: number;
      tiles: {
        picks_correct: number | null;
        picks_total: number | null;
        brier_model: number | null;
        brier_elo: number | null;
        brier_market: number | null;
        points_mae: number | null;
        watch_hits: number | null;
        watch_total: number | null;
        not_graded: number;
      };
      note: string | null; // the report card's note
      consistent: boolean; // recomputed numbers equal the report card's
      games: ResultGame[];
      players: ResultGroup[];
      watch: ResultWatch[];
    };

// ---- Graph ---------------------------------------------------------------------------------

export interface Insight {
  id: string;
  type: string; // "injury_ripple"
  section: string; // "matchup_risk" | "non_obvious" | "more"
  query: string; // "q2_injury_ripple"
  strength: number; // 0–1
  confidence: string; // "low" | "medium" | "high"
  game_id: string | null;
  matchup: string | null;
  headline: string;
  brief: string;
  note: string | null;
}
export interface UnusedInsight extends Insight {
  reason: string; // "game already started" | "used in a recent digest" | "same player as a pick" | "not picked"
}

/** GET /api/weeks/{season}/{week}/graph */
export interface GraphResponse {
  season: number;
  week: number;
  status: string; // "ok" | "failed" | "none" (no graph_results.json)
  built_at: string | null;
  mode: string | null;
  totals: { nodes: number; rels: number; mismatches: number };
  nodes_by_label: { label: string; count: number }[]; // biggest first
  rels_by_type: { type: string; count: number }[];
  stages: { stage: string; seconds: number }[]; // inputs, tables, wipe, schema, load, counts, queries
  total_seconds: number | null;
  queries: { name: string; rows: number; seconds: number | null; error: string | null }[];
  used: Insight[];
  candidates: number;
  not_used: UnusedInsight[]; // strongest first
  skipped: { started: number; novelty: number; duplicate_player: number };
  gds: { status: string; version: string | null; seconds: number | null; jobs: { name: string; status: string; seconds: number | null }[] } | null;
  browser_url: string;
}
