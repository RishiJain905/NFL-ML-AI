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
  log_source: 'rebuilt' | 'calendar' | 'events' | 'none'; // rebuilt from step details, the plan's summary, or (CR02) the run's own events file
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

// ---------------------------------------------------------------- CR02: run control
// GET /api/preflight, POST /api/run, GET /api/run/current, GET /api/run/stream (SSE).
// The server decides the week and the command; the browser only sends a kind and its
// expected week as a cross-check (README §5.3). Same field-name rule as above.

export type CheckState = 'ok' | 'warn' | 'fail';

export interface PreflightCheck {
  id: 'calendar' | 'last_week_final' | 'not_published' | 'lock' | 'data_root' | 'neo4j' | 'keys' | 'season' | string;
  label: string; // "Week 4 is final"
  status: CheckState;
  value: string; // the short value on the right: "16/16 final · 16/16 in play-by-play"
  reason: string | null; // why it isn't ok (null when ok)
  blocking: boolean; // a `fail` here keeps Run greyed out (Neo4j is warn-only: the run starts it)
}

export type RunKind = 'weekly' | 'resume' | 'injury_update';

export interface ActionGate {
  allowed: boolean;
  reason: string | null; // the greyed-out button's reason (mockup wording); null when allowed
  command: string | null; // what it runs, for the panel / dialog: "nfl weekly run --auto --expect-week 5"
}

/** GET /api/preflight: checked when the page opens, on "Check again", and by POST /api/run. */
export interface PreflightResponse {
  checked_at: string;
  mode: 'live' | 'rehearsal';
  season: number | null; // the server's week (the calendar's; in rehearsal mode the rehearsed week)
  week: number | null;
  checks: PreflightCheck[];
  run: ActionGate & {
    retry_after: string | null; // a recent not-ready run: Run opens again at this time (ISO)
  };
  confirm: {
    // the confirm dialog's facts (mockup: confirmRun)
    last_week: string; // "week 4 · 16/16 final"
    deadline: string | null; // first kickoff, ISO
    hours_to_deadline: number | null;
    retry_until: string | null;
    steps: StepName[]; // the steps the run does, in order (records excluded)
    publishes: string[]; // ["week05-digest.md", "production alias"]
  };
  resume: ActionGate & {
    step: StepName | null; // the failed step it resumes from (only offered for the current week's failed run)
  };
  injury_update: ActionGate & {
    opens: string | null; // Saturday 00:00 ET of the week (ISO), when known
    closes: string | null; // the week's last kickoff (ISO)
    material_last: boolean | null; // the last update's result this week, if one ran
  };
  dry_run_log: LogLine[]; // `nfl weekly run --auto --expect-week N --dry-run`'s lines (read-only)
}

/** POST /api/run body. 202 → RunStarted; 409 locked / running; 403 not allowed (with the reason). */
export interface RunRequest {
  kind: RunKind;
  expect_week: number;
}
export interface RunStarted {
  run_id: string; // "20261006T140012Z-a1b2"
  kind: RunKind | 'rehearsal';
  command: string; // display form: "nfl weekly run --auto --expect-week 5"
  season: number;
  week: number;
}

export interface RunInfo {
  run_id: string | null; // null for a run started from a terminal before it wrote events
  kind: RunKind | 'rehearsal' | 'terminal';
  launched_from: 'app' | 'terminal';
  command: string;
  season: number | null;
  week: number | null;
  started: string | null;
  finished: string | null; // null while running
  // ok | degraded | failed | not_ready (from run_end), or, for a run that wrote no events, from its
  // exit code: already_done / idle (0), failed (1), usage (2), not_ready (3), locked (4),
  // no_data_root (5), week_mismatch (6); interrupted = the process ended without a run_end;
  // unknown = it ended while the app wasn't watching and left no events
  status: string | null;
  exit_code: number | null;
  message: string | null; // run_end's message (scrubbed)
  published: boolean | null; // the week's digest is published after this run
  failed_step: StepName | null;
  material: boolean | null; // injury update only: an addendum was published (run_end's `material`)
  has_events: boolean; // an events file exists: GET /api/run/stream replays it
  output_tail: string[]; // last lines of the command's output (scrubbed), only when the run ended without a run_end event
}

/** GET /api/run/current: the run in progress (also after a page reload), else the last one the app launched. */
export interface RunCurrent {
  state: 'idle' | 'running' | 'finished';
  run: RunInfo | null;
}

// ---- The event stream: GET /api/run/stream (server-sent events) ---------------------------
// Each SSE message: `id: <run id>:<seq>`, `event: run`, `data: <RunEvent JSON>`; heartbeats are
// comments. After the run's `run_end` (or when the process is gone without one) the server sends
// one `event: end` message whose data is the final RunInfo, then closes. Reconnects resume
// after `Last-Event-ID` (EventSource sends it by itself) if it names the same run; another
// run's cursor replays the current run from its start.

interface EventBase {
  seq: number; // 1, 2, 3 … within the run
  t: string; // ISO with offset
  run_id: string;
}
export interface RunStartEvent extends EventBase {
  type: 'run_start';
  kind: 'weekly' | 'resume' | 'injury_update' | 'rehearsal';
  command: string;
  season: number;
  week: number;
  steps: string[]; // the steps this invocation will run, in order (records not included)
  from_step: string | null;
  /** A resume: the steps before `from_step` as the earlier sitting left them (draw them as done). */
  earlier: Record<string, { status: string | null; seconds: number | null }>;
  launched_by: string | null;
  via: string | null; // "control-room" when the app launched it
  plan: string[]; // the calendar's plan lines (dry-run wording)
}
export interface StepStartEvent extends EventBase {
  type: 'step_start';
  step: string; // a StepName, or "records"
}
export interface ProgressEvent extends EventBase {
  type: 'progress';
  step: string;
  done: number;
  total: number;
  fraction: number; // 0–1 within the step (sub-parts are mapped onto it: player = scoreboard, refits, team, ...)
  label: string; // "refitting 7/23 · rec_yds-wrte"
}
export interface StepEndEvent extends EventBase {
  type: 'step_end';
  step: string;
  status: 'ok' | 'degraded' | 'failed';
  seconds: number;
  detail: string; // scrubbed; data-root paths made relative by the server
}
export interface LlmEvent extends EventBase {
  type: 'llm';
  phase: 'start' | 'rewrite' | 'done' | 'failed';
  writer: string; // "openrouter" | "placeholder"
  model: string | null; // "z-ai/glm-5.3-flash"
  route: string[]; // the routing order, e.g. ["baseten/fp8", "novita/fp8", "relace"]
  provider: string | null; // who served it (on done)
  seconds: number | null; // on done / failed
  usage: { prompt: number | null; completion: number | null; reasoning: number | null } | null;
  cost: number | null; // dollars
}
export interface LogEvent extends EventBase {
  type: 'log';
  step: string | null; // the step running when it was printed
  text: string; // the console line, markup removed, scrubbed
  cls: LogLine['cls']; // from its colour: green ok, yellow warn, red err, dim, bold acc
}
export interface RunEndEvent extends EventBase {
  type: 'run_end';
  status: string; // ok | degraded | failed | not_ready | ...
  exit_code: number;
  message: string;
  published: boolean;
  failed_step: string | null;
  material: boolean | null; // injury update only
}
export type RunEvent =
  | RunStartEvent
  | StepStartEvent
  | ProgressEvent
  | StepEndEvent
  | LlmEvent
  | LogEvent
  | RunEndEvent;

// ---------------------------------------------------------------- CR03: MLOps and the season
// GET /api/weeks/{season}/{week}/mlops/{health,wandb,artifacts}; GET /api/season/{season}/
// scorecard?source=live|backtest; GET /api/teams?season=&week=; GET /api/models (+ /api/models/
// card/{id}); GET /api/alerts?season=; GET /api/health. Local files first; W&B (read on the
// server, cached) only for run lists, links, versions, aliases and lineage. `?refresh=1` on a
// W&B-backed endpoint (and /api/health) skips the cache once. Field names still avoid "key",
// "token", "auth", "secret" and "password" (the security scan).

/** Whether W&B answered. When it can't be reached (or its variable isn't set) the page shows
 *  a banner with `reason` and still renders everything local. */
export interface WandbState {
  available: boolean; // W&B data is in this answer (fresh, or from the cache)
  reason: string | null; // why not, or why it's stale: "WANDB_API_KEY isn't set", "W&B didn't answer within 10 s"
  fetched_at: string | null; // when the W&B part was fetched (ISO); null when nothing came from W&B
  stale: boolean; // from an older cache because W&B can't be reached now
  project_url: string | null; // https://wandb.ai/<entity>/<project>
}

// ---- MLOps → Health ----------------------------------------------------------------------

export interface FreshnessRow {
  source: string; // nflverse, espn, ngs_site, open_meteo, odds_api
  dataset: string;
  snapshot_date: string | null;
  age_days: number | null;
  newest_week: number | null;
  expected_week: number | null;
  status: string; // ok, failed, missing …
  stale: boolean;
  note: string | null;
}

export interface DriftRow {
  name: string; // game_vs_elo, calibration, player_vs_baseline, player_prob_vs_baseline, data_freshness, checks
  group: string | null; // a position group for the player signals
  status: 'ok' | 'alert' | 'insufficient_data' | string;
  value: number | null;
  threshold: number | null;
  detail: string; // the run's own sentence
  response: string; // doc 08's response ("No action needed." when fine)
}

export interface VsRow {
  measure: string; // "Time in steps", "GLM writing the digest", "LLM cost", "Games predicted", …
  unit: 'seconds' | 'usd' | 'count' | 'flag';
  this_week: number | null; // flag: 1 / 0
  last_week: number | null;
  trend: { week: number; value: number | null }[]; // the season so far, one point per run week
}

export interface MlopsHealthResponse {
  season: number;
  week: number;
  /** full: run_summary.json · partial: only weekly_run.json (2026 week 4, before run records) · none: no run */
  records: 'full' | 'partial' | 'none';
  notice: string | null; // what's missing and why, for partial / none
  tiles: {
    run: { status: string | null; steps_ok: number; steps_total: number; degraded: number; failed_step: string | null };
    data: { datasets: number | null; failures: number | null; snapshot_date: string | null };
    quality: { passed: number | null; total: number | null; blocking: number | null; blocking_failed: number | null };
    drift: { alerts: number | null; signals: number; insufficient: number };
    projections: { players: number | null; teams: number | null; graph_written: number | null };
  };
  steps: { step: string; status: string; seconds: number | null }[];
  long_pole: { step: string; share: number; seconds: number } | null; // the slowest step's share of the time in steps
  what_ran: {
    game_model: string | null; // "game-model-v0:2026-w05"
    trained_through: string | null; // "2026-w04"
    player_model: string | null; // "player-model-v1:2026-w05"
    player_stats: number | null; // live stats projected (23)
    team_model: string | null;
    graph: { built_at: string | null; nodes: number | null; relationships: number | null; status: string | null } | null;
    writer: { model: string | null; route: string | null; providers: string[]; calls: number } | null;
    prompt_id: string | null; // the prompt hash (12 characters)
    promoted: string[]; // artifacts given `production` by this run (`--auto` / `--promote`), e.g. ["game-model", "player-model", "team-model"]
    command: string | null; // "nfl weekly run --auto --expect-week 5" (from the events file), when known
    launched_by: string | null;
    via: string | null; // "control-room" when launched from the app
  };
  freshness: FreshnessRow[];
  vs_last: VsRow[];
  ingest: {
    manifest: string | null; // "raw/_runs/ingest-20261006T202805.json"
    snapshot_date: string | null;
    rows: { source: string; dataset: string; rows: number | null; status: string; detail: string | null }[];
  };
  quality: {
    run_at: string | null; // the curate run whose checks are listed
    /** this_run: the quality file is this run's · later: a later curate replaced it (only the
     *  run summary's counts are this run's) · none: no file */
    match: 'this_run' | 'later' | 'none';
    checks: { name: string; level: 'block' | 'warn' | string; passed: boolean; detail: string | null }[];
    failed: string[]; // from the run summary
  };
  drift: DriftRow[];
}

// ---- MLOps → W&B runs --------------------------------------------------------------------

export type WandbJob =
  | 'game'
  | 'graph'
  | 'scoreboard'
  | 'player'
  | 'team'
  | 'digest'
  | 'pipeline'
  | 'dashboard'
  | 'injury_update'
  | 'other';

export interface WandbRun {
  id: string; // "i8fhvizy"
  name: string; // "pipeline-2026-w05"
  group: string; // "weekly-pipeline"
  job_type: string; // "pipeline"
  job: WandbJob;
  state: string; // finished, running, crashed, failed
  created_at: string | null;
  url: string;
  tags: string[];
  current: boolean; // the newest run of its job for this week; older ones are re-runs
}

/** One redrawn chart: the W&B run it mirrors and the local file it's drawn from. */
export interface ChartCardMeta {
  run_name: string; // "train-2026-w05"
  run_id: string | null;
  url: string | null; // the run in W&B (from W&B, or from the step's detail line when W&B is down)
  metrics: string; // the W&B names it mirrors: "slate/*", "words_per_section · check_issue_counts"
  source: string; // the local file it's drawn from: "predictions_games.parquet"
  note: string | null; // e.g. "Week 4 ran before the pipeline run existed (P07)"
}

export interface MlopsWandbResponse {
  season: number;
  week: number;
  wandb: WandbState;
  runs: WandbRun[]; // this week's runs, newest first (W&B; empty when unavailable)
  local_links: { job: WandbJob; run_id: string; url: string }[]; // from the steps' detail lines (work offline)
  dashboard: {
    title: string | null; // "2026 Season Dashboard"
    url: string | null; // the W&B Report
    current_run_id: string | null; // the run the report reads (tag dashboard-current)
    current_run_week: number | null;
    week_run_id: string | null; // this week's dashboard run, when there is one
  };
  cards: {
    game_fit: ChartCardMeta & {
      games: { game_id: string; away: string; home: string; p_model_only: number | null; p_market: number | null }[];
    };
    player_scoreboard: ChartCardMeta & {
      scored_week: number | null; // the week this run graded (week − 1)
      mode: 'live' | 'backtest' | null; // backtest = walk-forward rows (before the live model)
      groups: { group: string; improvement: number | null; scored: number }[];
    };
    player_fit: ChartCardMeta & { stats: { target: string; group: string; label: string; count: number }[] };
    graph_build: ChartCardMeta & {
      stages: { stage: string; seconds: number }[]; // wipe, load, gds, queries, schema, tables, counts, inputs
      nodes: number | null;
      relationships: number | null;
      mismatches: number | null;
      total_seconds: number | null;
    };
    digest: ChartCardMeta & {
      words: { section: string; words: number; budget: number | null }[];
      checks: { name: string; issues: number; level: 'fail' | 'warn' }[];
    };
    pipeline: ChartCardMeta & {
      steps: { step: string; seconds: number | null; status: string }[];
      stale_sources: number | null;
      drift: { name: string; group: string | null; status: string }[];
    };
  };
}

// ---- MLOps → Artifacts -------------------------------------------------------------------

export interface ArtifactVersion {
  version: string; // "v5"
  created_at: string | null;
  aliases: string[]; // "production", "2026-w05", "latest"
  logged_by: string | null; // run id
  logged_by_url: string | null;
  size: number | null; // bytes
}

export interface ArtifactCollection {
  name: string; // "game-model"
  type: string; // "model" | "graph" | "digest"
  note: string; // "weekly game fit"
  url: string | null; // the collection in W&B
  total: number; // versions in all
  versions: ArtifactVersion[]; // newest first
  first_note: string | null; // when there are none yet: "The first one comes from Saturday's injury update"
}

export interface MlopsArtifactsResponse {
  season: number;
  week: number;
  wandb: WandbState;
  production: { name: string; version: string | null; week_alias: string | null; run_id: string | null }[]; // game-model, player-model, team-model
  total_versions: number | null;
  collections_count: number | null; // collections with at least one version
  lineage: {
    source: 'pipeline_run' | 'aliases' | 'none'; // the week's pipeline run's used artifacts (week 5 on), or the week aliases (week 4)
    pipeline_run: string | null;
    items: { role: 'model' | 'graph' | 'published'; name: string; version: string | null }[];
    note: string | null;
  };
  collections: ArtifactCollection[];
  local_models: Record<string, string[]> | null; // run_summary.json → models (shown when W&B is down)
}

// ---- Season → Scorecard ------------------------------------------------------------------

export interface ScorecardWeek {
  week: number; // the graded week
  games: number | null;
  brier_model: number | null;
  brier_elo: number | null;
  brier_market: number | null;
  cum_brier_model: number | null;
  cum_brier_elo: number | null;
  cum_brier_market: number | null;
  pick_accuracy: number | null;
  cum_pick_accuracy: number | null;
}

export interface ScorecardResponse {
  season: number;
  source: 'live' | 'backtest';
  label: string; // "2026 live" | "2025 backtest (example)"
  through_week: number | null; // the newest graded week
  tiles: {
    brier_model: number | null;
    brier_elo: number | null;
    brier_market: number | null;
    pick_accuracy: number | null;
    ece: number | null;
    ece_chance: number | null; // a perfectly calibrated model's ECE on the same games
    games_graded: number;
    weeks_published: number | null; // live only
    checks_passed: number | null; // digests whose final checks passed (live only)
    checks_total: number | null;
    llm_spend: number | null; // dollars, season to date (live only)
    player_improvement: number | null; // % MAE vs the rolling baseline, players pooled
  };
  weeks: ScorecardWeek[];
  calibration: { bin: number; predicted: number; observed: number; games: number }[];
  player_groups: { group: string; improvement: number | null; weeks: number; live_weeks: number }[]; // season to date
  pipeline: {
    week: number;
    status: string;
    on_time: boolean | null;
    hours_before_deadline: number | null;
    checks_passed: boolean | null;
    regenerated: boolean | null;
    seconds: number | null;
    drift_alerts: number | null;
    launched_by: string | null;
  }[]; // live only: one row per weekly run (the last of each week)
  dashboard: { title: string | null; url: string | null; current_run_id: string | null };
  notes: string[];
}

// ---- Season → Teams & rankings -------------------------------------------------------------

export interface TeamGameBrief {
  week: number;
  game_id: string;
  away: string;
  home: string;
  kickoff: string | null;
  p_home: number | null; // the model's home win chance as shown, when predicted
  away_score: number | null;
  home_score: number | null;
}

export interface TeamRow {
  team: string;
  conf: string | null;
  div: string | null;
  rank: number; // by Elo entering the week (all 32)
  prev_rank: number | null;
  elo: number;
  elo_change: number | null; // vs entering the week before
  elo_by_week: { week: number; elo: number }[];
  net_epa: number | null;
  off_epa: number | null;
  def_epa: number | null; // EPA/play allowed (lower is better)
  pass_epa: number | null; // net
  rush_epa: number | null; // net
  this_week: TeamGameBrief | null; // null = bye (or no slate)
  next_week: TeamGameBrief | null;
}

export interface TeamsResponse {
  season: number;
  week: number | null; // Elo and ratings entering this week
  weeks: number[]; // weeks with Elo this season
  teams: TeamRow[]; // by rank
  risers: { team: string; change: number }[]; // top 5
  fallers: { team: string; change: number }[]; // bottom 5, most negative first
}

// ---- Models --------------------------------------------------------------------------------

export interface ModelInfo {
  id: 'game' | 'ratings' | 'player' | 'team' | 'writer';
  title: string;
  production: {
    version: string | null; // "v5"
    label: string | null; // "game-model-v0:2026-w05"
    week_alias: string | null; // "2026-w05"
    run_id: string | null;
    source: 'wandb' | 'local' | null;
  };
  // pct = a 0–1 share ("66.3%"); improvements over a baseline are in `bars` (percent points)
  headline: { label: string; value: number | null; unit: 'brier' | 'mse' | 'pct' | 'count'; detail: string }[];
  bars: { label: string; value: number; metric: 'mae' | 'brier' }[] | null; // player / team: % better than the baseline per stat
  settings: { label: string; value: string; mono?: boolean }[]; // mono: an id, a model name or a hash
  cards: { id: string; title: string; kind: 'card' | 'guide' }[]; // GET /api/models/card/{id}
  note: string | null;
}

export interface ModelsResponse {
  wandb: WandbState;
  models: ModelInfo[];
}

export interface ModelCardResponse {
  id: string;
  title: string;
  markdown: string;
}

// ---- Alerts --------------------------------------------------------------------------------

export interface AlertItem {
  season: number;
  week: number; // the run week
  level: 'warn' | 'error' | string;
  title: string;
  text: string;
  source: string; // "drift:calibration", "step:graph", …
  run_url: string | null; // the week's pipeline run in W&B, when known
  notes: string[]; // investigation notes from PROGRESS.md → Season log, under that week
}

export interface AlertsResponse {
  season: number;
  current_week: number | null;
  alerts: AlertItem[]; // newest first
  signals: {
    name: string;
    label: string; // "Brier vs Elo over rolling 4-week windows"
    needs: string; // "6 graded weeks", "64 graded games"
    first_week: number | null; // the first run week it can fire
    first_text: string; // "week 10's run"
    status_now: string | null; // the latest run's status for it
    detail_now: string | null;
  }[];
  runs: { week: number; alerts: number; drift: { name: string; group: string | null; status: string }[] }[];
  example: AlertItem | null; // the 2025 week-10 simulation's calibration alert (P07, D75)
  example_note: string | null;
}

// ---- System → Health -----------------------------------------------------------------------

export interface HealthResponse {
  checked_at: string | null; // when the slow checks (W&B, OpenRouter, Neo4j versions) last ran
  checking: boolean; // a check is running in the background now
  services: { name: string; status: 'ok' | 'warn' | 'fail' | 'busy' | 'checking'; detail: string }[];
  variables: { name: string; required: boolean; set: boolean }[]; // names only, never a value
  recent_runs: {
    run_id: string | null;
    season: number;
    week: number;
    kind: string;
    started: string | null;
    finished: string | null;
    seconds: number | null;
    command: string | null;
    status: string;
    launched_by: string | null;
    via: string | null;
  }[];
}

// ---- LD02: Game day (the live 3rd / 4th-down bot; documentation/live-decisions/) ----------
// GET /api/live/{season}/{week}/games, .../games/{event}/call, .../games/{event}/context.
// Probabilities are 0-1; a `gap` is in the same units (0.014 = 1.4 win-probability points).
// ESPN text (play descriptions, status lines) is data: render it as text, never as HTML.

export type LiveChoice = 'go' | 'fg' | 'punt';
export type LiveLabel = 'Confident' | 'Lean' | 'Toss-up';

/** Where an answer came from: ESPN's site API, through the server's one shared client. */
export interface LiveFeed {
  as_of: string | null; // when ESPN's answer arrived
  age_s: number | null; // seconds since then
  stale: boolean; // an older answer, served because ESPN failed or the client is backing off
  error: string | null; // why (short, fixed text)
  cached: boolean; // served from the server's memory (at most one call per game every 2 s)
  ms: number | null; // how long ESPN took
}

export interface LivePregame {
  home_win_prob: number | null; // our pre-game chance (the week's predictions, primary row)
  home_spread: number | null; // the pre-game market line, nflverse's sign: + = home favoured
  spread_text: string | null; // "PHI -3.5", "Pick'em"
  total: number | null;
  line_source: string | null;
}

export interface LiveLastPlay {
  id: string | null;
  type: string | null; // ESPN's type ("Pass Reception")
  text: string | null; // ESPN's description
  at: string | null; // its snap time, from ESPN's play log, when the server has it
  seen_at: string | null; // when the app first saw it in an ESPN answer
  age_s: number | null; // seconds since `at`, else since `seen_at`
  age_from: 'snap' | 'seen' | null;
}

export interface LiveGame {
  event: string; // ESPN's event id
  game_id: string | null; // ours (2026_04_LA_PHI)
  home: string; // nflverse codes
  away: string;
  state: 'pre' | 'in' | 'post';
  detail: string | null; // ESPN's status line ("3:10 - 4th", "Final", "Halftime")
  kickoff: string | null;
  period: number;
  clock: string | null; // "3:10"
  home_score: number;
  away_score: number;
  possession: string | null; // who has the ball
  down: number | null;
  distance: number | null;
  yardline_100: number | null; // yards to the offense's goal
  situation: string | null; // "3rd & 4 at PHI 46" (our words, from ESPN's numbers)
  red_zone: boolean;
  home_timeouts: number | null;
  away_timeouts: number | null;
  last_play: LiveLastPlay | null;
  espn_home_wp: number | null; // ESPN's own win probability for the home team
  pregame: LivePregame;
  decision_down: boolean; // a 3rd or 4th down is next
}

export interface LiveModels {
  available: boolean;
  version: string | null; // the promoted bundle ("2010-2025_20261010T054343Z")
  message: string | null; // "The decision models aren't trained yet (LD00)."
}

export interface LiveGamesResponse {
  season: number;
  week: number;
  phase: 'before' | 'live' | 'between' | 'final' | 'past' | 'future';
  is_current: boolean; // the week Game day is live for (the calendar's, or the replayed one)
  live_week: { season: number; week: number } | null; // Game day's week now (null: season over)
  games: LiveGame[];
  feed: LiveFeed | null; // null when ESPN wasn't asked (a past or future week)
  feed_error: string | null; // ESPN failed with no earlier answer: the games are the schedule's
  first_kickoff: string | null;
  next_kickoff: string | null; // the next game not started yet
  models: LiveModels;
  replay: { event: string; at: string; speed: number; lag_s: number } | null; // nfl app --live-replay
  refresh_s: number; // how often the list may refresh while the tab is visible (30)
}

export interface LiveOption {
  choice: LiveChoice;
  name: string; // "Go for it", "Field goal", "Punt"
  wp: number | null; // the offense's win probability after this choice (null: not an option)
  best: boolean;
}

export interface LiveFourth {
  best: LiveChoice;
  best_name: string;
  gap: number; // the best option's lead over the next best
  label: LiveLabel | null;
  boot_share: number | null; // share of the bootstrap refits that agree
  options: LiveOption[]; // always go, fg, punt in that order
  convert: number; // the chance a go converts
  fg_make: number | null;
  fg_distance: number; // yards
  punt_start: number | null; // after a punt, the receiver's expected start (yards from its goal)
  wp_now: number; // the offense's win probability before the snap
  ms: number;
}

export interface LiveTableRow {
  gain: number; // yards gained on 3rd down (negative = a loss)
  gain_text: string; // "no gain", "a 3-yard gain", "a 2-yard loss"
  ydstogo: number;
  yardline_100: number;
  situation: string; // "4th & 4 at PHI 46"
  best: LiveChoice;
  best_name: string;
  gap: number;
  label: LiveLabel | null;
  wp: Record<LiveChoice, number | null>;
}

export interface LiveThird {
  convert: number;
  pass_prob: number;
  wp_now: number;
  note: string | null; // "The clock runs out on this play: there is no 4th down to plan."
  table: LiveTableRow[]; // 4th & 1 ... 4th & max(10, distance), shortest first
  ms: number;
}

export interface LiveCallResponse {
  season: number;
  week: number;
  event: string;
  game_id: string | null;
  game: LiveGame; // the fresh state behind the call
  feed: LiveFeed;
  kind: 'fourth' | 'third' | 'none';
  reason: string | null; // why there's no call ("1st & 10: checks are for 3rd and 4th downs")
  warnings: string[]; // what was assumed ("no pre-game line: spread 0 assumed")
  offense: string | null;
  defense: string | null;
  source: 'situation' | 'summary'; // the next snap from ESPN's situation, or its play log
  fourth: LiveFourth | null;
  third: LiveThird | null;
  espn_offense_wp: number | null; // ESPN's own win probability, from the offense's side
  behind: { likely: boolean; text: string } | null; // ESPN may not have posted the latest snap
  repeat: { same: boolean; last_check_at: string | null; text: string | null }; // no new play
  models: LiveModels;
}

export interface LiveStat {
  num: number; // e.g. conversions
  den: number; // e.g. attempts
  rate: number | null; // num / den (null when den is 0)
}

/** One measure for a team, this season and last, with the league's rate beside it. */
export interface LiveMeasure {
  this: LiveStat;
  last: LiveStat;
  league_this: LiveStat;
  league_last: LiveStat;
}

export interface LiveKickerBand {
  band: '<30' | '30-39' | '40-49' | '50+';
  this: LiveStat;
  last: LiveStat;
  league_last: LiveStat;
}

export interface LivePunting {
  punts: number;
  net: number | null; // (gross yards - return yards - 20 per touchback) / punts
  gross: number | null;
}

export interface LiveContextTeam {
  team: string;
  coach: string | null; // the head coach (nflverse + config/head_coach_fixes.csv)
  coach_last_team: string | null; // his team last season (null: not a head coach then)
  go_rate: LiveMeasure; // went for it / 4th downs decided (runs, passes, kicks, punts)
  fourth_conv: LiveMeasure; // converted / went for it
  short_conv: LiveMeasure; // 3rd and 4th & 2 or less: converted / runs and passes
  red_zone_td: LiveMeasure; // touchdown drives / drives that reached the 20
  coach_go: LiveMeasure | null; // where the bot says go: went / such spots (the coach's own)
  coach_go_by_distance: {
    band: '1-2' | '3-5' | '6+';
    this: LiveStat;
    last: LiveStat;
    league_last: LiveStat;
  }[];
  kicker: {
    name: string | null;
    long: number | null; // career long make in the curated data (2010+)
    long_season: number | null; // the season of that long make (latest, on a tie)
    bands: LiveKickerBand[];
  } | null;
  punter: {
    name: string | null;
    this: LivePunting;
    last: LivePunting;
    league_this: LivePunting;
    league_last: LivePunting;
  } | null;
}

export interface LiveContextResponse {
  season: number;
  week: number;
  this_season: number;
  last_season: number;
  through_week: number | null; // the newest week of this season in the curated plays (< week)
  through: string; // "2026 weeks 1-3 and the 2025 season"
  team: LiveContextTeam;
  computed_at: string;
  model_version: string | null; // the bundle behind coach_go
}
