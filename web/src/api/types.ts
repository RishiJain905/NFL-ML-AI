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

export type RunKind = 'weekly' | 'resume' | 'injury_update' | 'playcalling_update';

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
  // D132: the Play calls tab's "Update play calling" button (`nfl playcalling update --auto
  // --expect-week N`): allowed once the week's Tuesday run is published, the lock is free and a
  // step is due; `steps` says what it would do now and why (all five, due or not)
  playcalling: ActionGate & {
    steps: PlaycallUpdateStep[];
  };
  dry_run_log: LogLine[]; // `nfl weekly run --auto --expect-week N --dry-run`'s lines (read-only)
}

/** One step of `nfl playcalling update` (D132), in run order: Monday night's FTN, the tables, last
 * week's 4th-down decision review, last week's forecast grade, this week's forecast. */
export interface PlaycallUpdateStep {
  step: 'ftn' | 'build' | 'review' | 'grade' | 'forecast';
  label: string; // "Grade week 4's forecast"
  due: boolean; // the button runs only the due steps
  reason: string; // why it's due, or why it's skipped ("week 4 is already reviewed")
  week: number | null; // the week it's about (review / grade: the week just played; forecast: this week)
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
  failed_step: StepName | PlaycallUpdateStep['step'] | null; // a play-calling update names its own step
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
  kind: 'weekly' | 'resume' | 'injury_update' | 'playcalling_update' | 'rehearsal';
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
  status: 'ok' | 'degraded' | 'failed' | 'skipped'; // skipped: a play-calling update's step after a failed one
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

// ---- LD03: decision review (finished weeks; documentation/live-decisions/LD03-decision-review.md)
// GET /api/live/{season}/{week}/review and GET /api/live/{season}/season-review?through=N.
// Built from curated play-by-play (not ESPN) and the promoted live-decision models; the server
// reads `live/<S>/week<NN>/decision_review.*` (written by `nfl live review`) or computes the week
// and keeps it in its own cache. Probabilities 0-1; `edge` / `cost` / `wp_lost` in the same
// units (0.049 = 4.9 win-probability points; a sum of them = expected wins).

export type ReviewStatus = 'ok' | 'no_plays' | 'no_models';

export interface ReviewModel {
  version: string | null; // the bundle the review was scored with
  in_sample: boolean | null; // the season is inside the bundle's training seasons (2025: yes)
  trained_seasons: [number, number] | null; // [first, last]
}

/** One 4th down the coach decided (pre-snap penalties, kneels and replayed downs aren't). */
export interface ReviewPlay {
  game_id: string;
  play_id: number;
  posteam: string; // the offense (nflverse code)
  defteam: string;
  coach: string | null; // the offense's head coach
  qtr: number; // 5 = overtime
  clock: string | null; // the quarter clock "10:27"
  off_score: number | null; // the offense's score before the snap
  def_score: number | null;
  situation: string; // "4th & 2 at TB 44"
  ydstogo: number;
  yardline_100: number;
  choice: LiveChoice; // the coach's call (a fake punt / field goal = go)
  best: LiveChoice; // the bot's
  agree: boolean;
  label: LiveLabel | null; // the bot's confidence
  gap: number | null; // the bot's best option's lead over the next
  wp: Record<LiveChoice, number | null>; // the offense's win probability after each option
  wp_now: number | null; // before the snap (the options' average view; not drawn on the card)
  edge: number | null; // the coach's option minus the best other one: + gained, - cost
  cost: number | null; // the bot's best minus the coach's option (>= 0)
  convert: number | null; // the chance a go converts
  fg_make: number | null;
  fg_distance: number | null;
  fake: boolean;
  wiped: boolean; // wiped out by a penalty after the snap (the down wasn't replayed)
  success: boolean | null; // a go converted / a field goal made; null for a punt
  result: string; // "Converted (+3 yds)", "Missed from 52", "TB ball at own 14"
  desc: string; // nflverse's play text (data: render as text)
}

export interface ReviewGame {
  game_id: string;
  away: string;
  home: string;
  away_score: number | null;
  home_score: number | null;
  kickoff: string | null;
  decisions: number;
  wp_lost: Record<string, number | null>; // per team: expected wins given up vs the bot
}

export interface ReviewSummary {
  decisions: number;
  coach: Record<LiveChoice, number>;
  bot: Record<LiveChoice, number>;
  agree: number;
  toss_ups: number;
  go_spots: number; // the bot says go, Confident or Lean
  went_in_go_spots: number;
  wp_lost: number | null; // the week's total, in wins
  fakes: number;
  wiped: number;
  skipped: number; // 4th downs the state rules refused
}

export interface LiveReviewResponse {
  season: number;
  week: number;
  status: ReviewStatus;
  message: string | null; // why there's no review ("Week 5's plays come in with Tuesday's run")
  model: ReviewModel | null;
  computed_at: string | null;
  source: 'file' | 'cache' | 'computed' | 'memory' | null; // `nfl live review`'s file, the app's cache, now, earlier this session
  summary: ReviewSummary | null;
  highlights: {
    boldest: ReviewPlay | null; // the go with the lowest convert chance in a game in doubt
    costliest: ReviewPlay | null;
    top: ReviewPlay[]; // the five costliest
  } | null;
  games: ReviewGame[];
  plays: ReviewPlay[]; // kickoff order, then game order
}

/** A head coach's season (or the league's: no rank, coach, teams). Rates are null at den 0. */
export interface ReviewCoachRow {
  rank?: number; // by go_rate_spots, then go_spots
  coach?: string;
  teams?: string[]; // newest first
  decisions: number;
  go: number;
  go_rate: number | null;
  go_spots: number;
  went_in_go_spots: number;
  go_rate_spots: number | null; // the ranking: went / go spots
  kick_spots: number; // the bot says kick (fg or punt), Confident or Lean
  went_in_kick_spots: number;
  go_rate_kick_spots: number | null;
  agree: number;
  agree_rate: number | null;
  wp_lost: number; // expected wins given up vs the bot
  wp_lost_timid: number; // ... by kicking where the bot says go
  wp_lost_bold: number; // ... by going where the bot says kick
  weeks: number;
}

export interface ReviewTrendRow {
  week: number;
  decisions: number;
  go_rate_coach: number | null;
  go_rate_bot: number | null;
  agree_rate: number | null;
  wp_lost: number | null;
  attempts: number; // 4th-down go-for-its (not wiped)
  conv_actual: number | null;
  conv_pred: number | null;
}

export interface ReviewCalBin {
  bin_lo: number; // 0, 0.1, ... 0.9
  n: number;
  mean_pred: number | null;
  mean_outcome: number | null;
}

export interface ReviewConversionRow {
  down: 3 | 4;
  distance: number; // 1-10 (10 = 10 or more)
  label: string; // "1" ... "10+"
  n: number;
  actual: number | null;
  pred: number | null;
}

export interface ReviewCalibration {
  wp: {
    snaps: number;
    games: number;
    tie_games: number;
    brier: number | null;
    brier_vegas: number | null; // nflfastR's vegas_wp on the same snaps
    ece: number | null;
    ece_vegas: number | null;
    model: ReviewCalBin[];
    vegas: ReviewCalBin[];
  } | null;
  conversion: {
    rows: ReviewConversionRow[];
    overall: { down: 3 | 4; n: number; actual: number | null; pred: number | null; brier: number | null }[];
  } | null;
  fg: {
    n: number;
    made: number | null;
    pred: number | null;
    rows: { band: '<30' | '30-39' | '40-49' | '50+'; n: number; made: number | null; pred: number | null }[];
  } | null;
}

export interface LiveSeasonReviewResponse {
  season: number;
  through_week: number | null; // the season through this week (null: no reviewed week yet)
  status: ReviewStatus;
  message: string | null;
  model: ReviewModel | null;
  source: 'file' | 'cache' | 'computed' | 'memory' | null;
  weeks: number[];
  decisions: number;
  leaderboard: ReviewCoachRow[];
  league: ReviewCoachRow | null;
  trend: ReviewTrendRow[];
  calibration: ReviewCalibration | null;
  computed_at: string | null;
}

// ---------------------------------------------------------------- AE01: Ask the Engine
// (The AE01 block sits before PC01's: the PC01 and PC03 contract tests read their own blocks
// to the next PC header or the end of the file.)
// Explore -> Ask the Engine (documentation/ask-the-engine/AE01-ask-tab.md, D142). The app's first
// free-text endpoint: POST /api/ask (launch token + Origin, a 500-character question, one at a
// time, ~10 a minute) and GET /api/ask/examples. Every string is cleaned on the server; rows are
// scalar cells (numbers, text, dates as text, lists of them; a library template's map arrives as
// one line of text). The session's questions are kept in the browser only (localStorage).

export type AskSource = 'auto' | 'graph' | 'sql';
export type AskStatus = 'answered' | 'empty' | 'refused' | 'clarify' | 'error';
export type AskRoute = 'template' | 'generated' | 'refused' | 'clarify' | 'none';
export type AskCell = string | number | boolean | null | AskCell[];

export interface AskPick {
  text: string; // the name as the "which one?" card showed it
  kind: 'team' | 'player' | 'coach';
  value: string; // one of the card's option values
}
export interface AskRequest {
  question: string; // 1-500 characters
  source?: AskSource; // default auto
  picks?: AskPick[]; // up to 5
}

export interface AskSourceInfo {
  kind: 'graph' | 'sql';
  season: number | null;
  week: number | null; // graph: the week it's built as of; sql: the newest week with a final game
  rebuilt?: string | null; // sql: the curated tables' rebuild date (YYYY-MM-DD)
  label: string; // "weekly graph as of 2026 week 6" | "curated data through 2026 week 5"
}
export interface AskChoice {
  text: string; // "Michael Thomas"
  kind: 'team' | 'player' | 'coach';
  options: { value: string; label: string }[]; // label "Michael Thomas (WR, NO, 2016-2023)"
}
export interface AskLLMCall {
  purpose: 'route' | 'generate' | 'fix' | 'answer' | string;
  provider: string | null; // the endpoint OpenRouter used ("InferenceNet")
  seconds: number | null;
  cost: number | null; // $; null = not reported
  ok: boolean;
}
export interface AskResponse {
  question: string;
  asked_at: string; // ISO time on the server
  status: AskStatus;
  answer: string | null; // the sentences (model or fixed); null only for an error
  // model: the model's sentences passed the number check; fallback: they didn't (or the model
  // failed), so a fixed sentence points to the table; fixed: a refusal, a choice, no rows, an error
  answer_kind: 'model' | 'fallback' | 'fixed';
  columns: string[];
  rows: AskCell[][]; // up to 500 rows
  row_count: number;
  truncated: boolean; // more rows existed than the cap
  query: string | null; // the exact query that ran (or the last one tried)
  language: 'cypher' | 'sql' | null;
  params: { name: string; value: AskCell }[]; // the values bound to the query's $names
  source: AskSourceInfo | null;
  route: AskRoute;
  route_label: string | null; // "template: qb_receivers" | "free-form, 1 retry" | "refused" | "needs a choice"
  template: { id: string; title: string | null } | null;
  retries: number; // correction rounds used
  refusal: { by: 'code' | 'source' | 'router' | 'generator' | 'guard' | string; rule: string; message: string } | null;
  choices: AskChoice[]; // status clarify
  error: {
    code: 'llm' | 'query_failed' | 'out_of_credits' | 'failed' | string;
    message: string; // fixed text
    stage: string | null; // the model call that failed (route / generate / fix / answer)
    provider: string | null;
    model: string | null;
  } | null;
  timing: {
    total_s: number | null;
    route_s: number | null;
    generate_s: number | null;
    run_s: number | null;
    answer_s: number | null;
  };
  cost: number | null; // $ for the question's model calls; 0 = no call; null = a call didn't report one
  llm: { model: string | null; calls: AskLLMCall[]; providers: string[] };
}

export interface AskExample {
  question: string;
  template: string;
  title: string;
  source: 'graph' | 'sql';
  this_week: boolean; // a weekly-graph template read as of the graph's week
}
export interface AskStatusInfo {
  sources: {
    graph: {
      on: boolean;
      status: 'up' | 'down' | 'checking' | 'config_problem' | 'none' | 'off'; // none: no graph build found
      season: number | null;
      week: number | null;
      label: string | null;
    };
    sql: {
      on: boolean;
      status: 'up' | 'off';
      season: number | null;
      week: number | null;
      rebuilt: string | null;
      label: string | null;
    };
  };
  model: { name: string | null; label: string | null; effort: string | null; provider: string; timeout_s: number };
  limits: { question_chars: number; per_minute: number; rows: number; query_timeout_s: number; corrections: number };
  busy: boolean; // the weekly lock is held: questions are refused until the refresh ends
}
export interface AskExamplesResponse extends AskStatusInfo {
  featured: AskExample[]; // the chips
  groups: { source: 'graph' | 'sql'; label: string; examples: AskExample[] }[];
}

// ---- PC01: Play calling (documentation/play-calling/PC01-play-calling-pages.md) ------------
// GET /api/playcalling/teams?season=, /api/playcalling/teams/{team}?season=&side=,
// /api/playcalling/teams/{team}/history?side= and /api/weeks/{season}/{week}/play-calls.
// All read-only, from the tables `nfl playcalling build` writes (PC00, `playcalling/<S>/`).
// Units: `share` is 0-1; `over_expected` (pass rate over expected, PROE) is 0-1 too and is shown
// x100 as points with a sign (0.037 -> "+3.7"); `mean` is in its own unit (air yards, defenders,
// rushers, EPA, seconds; `digits` says how many decimals). The league's PROE isn't 0 (nflverse's
// xpass isn't re-centred), so a page compares a team with `league` / `diff`, never with 0.
// A defense's rows are what offenses do against it (plus its own calls: blitz, rushers, box).

export type PlaycallSide = 'offense' | 'defense';
export type PlaycallWindow = 'season' | 'last4' | 'last_season';
export type PlaycallUnit = 'share' | 'over_expected' | 'mean';
export type PlaycallSource = 'pbp' | 'ftn' | 'pfr' | 'participation';
/** not_built: no tables for the season; not_yet: built, but not as of this week yet. */
export type PlaycallStatus = 'ok' | 'not_built' | 'not_yet';

/** One number: a team's rate in one window and situation, next to the league's. */
export interface PlaycallCell {
  n: number; // plays counted (the denominator: dropbacks for play-action, attempts for deep shots)
  games: number | null; // games in the window behind the rate
  value: number | null;
  league: number | null; // the league's rate over the same window and situation
  diff: number | null; // value - league
  pct: number | null; // 0-100 among the teams with plays: high = more of it, not "better"
  small: boolean; // n < min_n: grey it out (a 1-for-2 isn't a tendency)
}
export type PlaycallWindows = Record<PlaycallWindow, PlaycallCell | null>;

/** How to name and format a metric (labels.METRICS, plus the pages' short names). */
export interface PlaycallMetric {
  metric: string; // "play_action_rate"
  label: string; // "Play-action per dropback"
  short: string; // "Play-action"
  unit: PlaycallUnit;
  digits: number; // decimals: shares and PROE in points 1, aDOT 1, rushers / box 2, EPA 2
  source: PlaycallSource;
  situation: string; // the situation it's read in: "neutral" for PROE and dropback rate, else "all"
  per: string; // what one counted play is: "dropback", "attempt", "play", "snap", "designed run"
  caller: PlaycallSide; // who chooses it: the offense (play-action) or the defense (blitz, box)
  help: string | null; // one plain sentence for a tooltip
}

/** What every play-calling answer says about the tables behind it. */
export interface PlaycallMeta {
  status: PlaycallStatus;
  message: string | null; // the empty state's text, naming the command to run
  season: number;
  seasons: number[]; // seasons with built tables, newest first
  as_of_week: number | null; // rows count the games before this week (what a Tuesday run could see)
  through_week: number | null; // the last week counted (as_of_week - 1; null before week 1's games)
  built_at: string | null;
  min_n: number; // 20: rates on fewer plays are greyed out
  ftn_waiting: string[]; // games FTN hasn't charted yet ("ATL at NO, week 4"): FTN rates wait for them
}

// ---- Explore -> Play calling: the teams grid ----

export interface PlaycallGridColumn extends PlaycallMetric {
  id: string; // "offense.proe": side + metric
  side: PlaycallSide;
  signature: boolean; // on the tile (offense PROE, defense blitz); the others sort the grid
}
export interface PlaycallGridTeam {
  team: string;
  games: number; // games behind the numbers shown (the window's: last season's before week 1)
  cells: Record<string, PlaycallCell | null>; // by column id
}
export interface PlaycallTeamsResponse extends PlaycallMeta {
  window: PlaycallWindow; // 'season', or 'last_season' before week 1's games
  columns: PlaycallGridColumn[];
  teams: PlaycallGridTeam[]; // the 32 teams (empty when not built)
}

// ---- Explore -> Play calling -> a team ----

export interface PlaycallIdentityRow extends PlaycallMetric {
  windows: PlaycallWindows;
}
export interface PlaycallIdentityGroup {
  id: string; // "pass_run" | "formation" | "pass_game" | "results" | "pressure" | "box" | "faced"
  title: string;
  note: string | null; // e.g. a defense's "allowed" rates depend on the offenses it faced
  rows: PlaycallIdentityRow[];
}
export interface PlaycallSituationRow {
  situation: string; // "3rd_long"
  label: string; // "3rd & 7+"
  cells: Record<string, PlaycallWindows>; // by metric (the table's columns)
}
export interface PlaycallSituationFamily {
  family: 'down_distance' | 'field_zone' | 'score';
  title: string; // "Down & distance" | "Field zone" | "Score & clock"
  rows: PlaycallSituationRow[];
}
export interface PlaycallSituations {
  metrics: PlaycallMetric[]; // the columns: dropback rate, PROE, shotgun, deep shots, play-action, blitz (those the season has)
  baseline: PlaycallSituationRow; // all plays, the row to compare with
  families: PlaycallSituationFamily[];
}
export type PlaycallDirection = 'left' | 'middle' | 'right';
export interface PlaycallZone {
  metric: string; // "pass_deep_left"
  depth: 'short' | 'deep' | null; // null: a direction over every depth ("pass_left")
  direction: PlaycallDirection;
  windows: PlaycallWindows; // a share of the attempts with a depth and direction (the 6 add to 1)
}
export interface PlaycallField {
  zones: PlaycallZone[]; // 6: short / deep x left / middle / right
  directions: PlaycallZone[]; // 3: left / middle / right over every depth
  depth: PlaycallIdentityRow[]; // deep shots per attempt, aDOT
}
export type PlaycallLaneId =
  | 'left_end'
  | 'left_tackle'
  | 'left_guard'
  | 'middle'
  | 'right_guard'
  | 'right_tackle'
  | 'right_end';
export interface PlaycallLane {
  lane: PlaycallLaneId;
  metric: string; // "run_left_end"
  label: string; // "Left end"
  windows: PlaycallWindows; // a share of the designed runs with a direction (the 7 add to 1)
}
export interface PlaycallWeeklySeries extends PlaycallMetric {
  league: number | null; // the season's league rate: a reference line
  points: { week: number; value: number | null; n: number }[]; // one per game played
}
export interface PlaycallWeekly {
  games: { week: number; game_id: string; opponent: string; home: boolean }[];
  series: PlaycallWeeklySeries[];
}
export interface PlaycallNextGame {
  season: number;
  week: number;
  game_id: string;
  opponent: string;
  home: boolean;
  kickoff: string | null;
  forecast: PlaycallNextForecast | null; // PC02: null until that week's forecast is made
  forecast_hint: string | null; // then: the command that makes it
}
export interface PlaycallTeamResponse extends PlaycallMeta {
  team: string;
  side: PlaycallSide;
  games: number; // games in the season window (0 before week 1's games)
  summary: string[]; // two or three plain sentences: the identity in five seconds
  identity: PlaycallIdentityGroup[];
  situations: PlaycallSituations | null;
  field: PlaycallField | null;
  runs: PlaycallLane[];
  weekly: PlaycallWeekly | null;
  next_game: PlaycallNextGame | null; // links the week's Play calls tab
  forecast_record: PlaycallForecastRecord | null; // PC02: null before any graded week
  notes: string[];
}

// ---- History, 2023-2025 (nflverse participation: research data, published after each season) ----

export interface PlaycallHistoryRow extends PlaycallMetric {
  seasons: Record<string, PlaycallCell | null>; // by season ("2025"): the whole season, playoffs in
}
export interface PlaycallHistoryGroup {
  id: string; // "personnel" | "formation" | "routes" | "coverage" | "man_zone" | "packages" | "pressure"
  title: string;
  kind: 'stack' | 'bars'; // stack: shares that add to 100% (one bar per season); bars: separate rates
  note: string | null;
  rows: PlaycallHistoryRow[];
}
export interface PlaycallHistoryResponse {
  status: 'ok' | 'not_built';
  message: string | null;
  team: string;
  side: PlaycallSide;
  seasons: number[]; // oldest first
  research: true;
  source_note: string; // what the data is and when the next season's arrives
  min_n: number;
  groups: PlaycallHistoryGroup[];
}

// ---- The Play calls week tab (descriptive in PC01; PC02 adds the forecast) ----

export interface PlayCallsRow {
  metric: string;
  offense: PlaycallCell | null; // the offense's own rate (for a defense's call: how often it has faced it)
  defense: PlaycallCell | null; // what offenses do against this defense (its own call: how often it calls it)
  league: number | null;
  shift: number | null; // (defense - league) in standard deviations of the 32 defenses: how unusual
  same_way: boolean | null; // the offense and the defense sit on the same side of the league
}
export interface PlayCallsMatchup {
  offense: string;
  defense: string;
  rows: PlayCallsRow[];
  // PC02: the shipped forecasts of this matchup, the offense's calls (team = offense) then the
  // defense's (team = defense); [] when the week has no forecast
  forecast: PlaycallForecastValue[];
}
export interface PlayCallsGame {
  game_id: string;
  kickoff: string | null;
  away: string;
  home: string;
  matchups: PlayCallsMatchup[]; // [away offense vs home defense, home offense vs away defense]
}
export interface PlayCallsShift {
  game_id: string;
  offense: string;
  defense: string;
  metric: string; // a PC01 metric (descriptive) or a PC02 target key (forecast)
  // descriptive: the defense's distance from the league in SDs of the 32 defenses, signed;
  // forecast: |forecast - season so far| over the half-width of the 80% range (unsigned)
  shift: number;
  change?: number; // forecast only: forecast - season so far (rate units), signed
  text: string; // one or two plain sentences, numbers included
}
export interface PlayCallsResponse extends PlaycallMeta {
  week: number;
  window: PlaycallWindow; // 'season' (games before this week); 'last_season' for week 1
  note: string; // what the tab is: descriptive (no forecast yet) or how to read the forecast
  metrics: PlaycallMetric[]; // the rows of every matchup, in order (only metrics the season has)
  games: PlayCallsGame[];
  shifts: PlayCallsShift[]; // the week's biggest, largest |shift| first (up to 8)
  shifts_kind: 'descriptive' | 'forecast'; // forecast once the week's forecast is made (PC02)
  forecast: PlaycallForecastMeta | null; // null: no forecast for this week (see forecast_hint)
  graded: PlaycallWeekGrade | null; // null: not graded yet
  forecast_hint: string | null; // the command to run next (forecast, or grade), when one is due
}

// ---- PC02: the tendency forecast (documentation/play-calling/PC02-tendency-forecast.md) ----
// Files: playcalling/<S>/week<NN>/forecast.parquet (+ forecast.json) from `nfl playcalling
// forecast`, playcalling/<S>/playcall_scoreboard.parquet from `nfl playcalling grade`, the model
// models/playcall/<version>/params.json. Only shipped targets reach the pages. Values use the
// target's unit (share 0-1, PROE 0-1 shown x100 as signed points, rushers in rushers).

/** A forecast target: a PC01 metric, or `run_outside` (end runs), with the backtest's record. */
export interface PlaycallTarget extends PlaycallMetric {
  target: string; // "dropback_rate", "run_outside" ...
  // the forecast's second term: what the opponent allows / draws, or (run direction) the venue's
  // stats crew: the home team's crew charts run location, and crews differ by ~9 points
  context: 'opponent' | 'venue';
  backtest: {
    shipped: boolean;
    mae: number | null; // game-level MAE of the forecast, every backtest season pooled
    mae_season_to_date: number | null; // the same games, the team's season-to-date rate
    wins: number | null; // seasons the forecast beat season-to-date
    seasons: number | null; // seasons scored (7 for play-by-play targets, 4 for FTN ones)
    coverage: number | null; // share of backtest games inside the 80% range
  } | null;
}
/** One team's forecast rate for one game (+ how it did, once graded). */
export interface PlaycallForecastValue {
  target: string;
  team: string; // whose call it is (the offense for play-action, the defense for blitz)
  opponent: string;
  caller: PlaycallSide;
  forecast: number | null;
  lo: number | null; // the 80% range of the game's rate (sampling noise included)
  hi: number | null;
  season_to_date: number | null; // baseline 2: the team's rate so far (last season's in week 1)
  league: number | null; // baseline 1: the league rate (dropback rate: + xpass on the game script)
  shrunk: number | null; // baseline 3: the team's rate shrunk toward last season and the league
  team_term: number | null; // the forecast's pieces: team + opponent_effect + script_effect
  opponent_effect: number | null; // the second term: the opponent's effect, or the venue crew's
  context_team: string | null; // whose effect it is: the opponent, or the home team (venue)
  script_effect: number | null; // PROE and dropback rate only (0 otherwise)
  shift: number | null; // forecast - season_to_date
  n_so_far: number; // plays behind season_to_date
  games_so_far: number;
  small: boolean; // n_so_far < min_n
  // after the games (null before grading):
  status: 'graded' | 'waiting_ftn' | 'waiting_build' | 'not_played' | 'no_plays' | null;
  actual: number | null;
  n: number | null; // plays in the game behind `actual`
  in_range: boolean | null;
  beat_season_to_date: boolean | null; // |forecast - actual| < |season_to_date - actual|
}
export interface PlaycallForecastMeta {
  version: string | null; // the playcall-model version
  made_at: string | null; // ISO time the forecast was made
  as_of_week: number | null;
  backfill: boolean; // made after some games kicked off (from the as-of tables: no peeking)
  after_kickoff: string[]; // those games ("KC at LV, week 4")
  range_level: number; // 0.8
  targets: PlaycallTarget[]; // the shipped targets, in display order
  note: string; // how to read a forecast and its range (the noise floor, plainly)
}
export interface PlaycallGradeSummary {
  graded: number;
  mae: number | null;
  mae_season_to_date: number | null;
  beat: number; // forecasts closer than season-to-date
  in_range: number | null; // share inside the 80% range
}
export interface PlaycallWeekGrade {
  rows: number;
  graded: number;
  waiting: number; // rows waiting for FTN, the build or the game
  overall: PlaycallGradeSummary;
  targets: (PlaycallGradeSummary & { target: string })[];
}
/** The team page's Next game card: its own calls and the opponent's in the same matchup. */
export interface PlaycallNextForecast extends PlaycallForecastMeta {
  own: PlaycallForecastValue[]; // this team, this page's side
  opponent: PlaycallForecastValue[]; // the opponent's calls on the other side of the matchup
}
/** The team page's season card: how the forecast has done for this team's calls. */
export interface PlaycallForecastRecord {
  season: number;
  weeks: number[]; // graded weeks
  overall: PlaycallGradeSummary;
  targets: (PlaycallTarget & PlaycallGradeSummary)[];
}

// ---- PC03: the play browser and the play diagrams (documentation/play-calling/PC03-play-diagrams.md) --
// A play's diagram is a playbook card RECONSTRUCTED from the charting data (play-by-play, FTN and,
// 2016-2025, participation): never player tracking, and `label` says so on the player at all
// times. Coordinates: x in yards from the offense's own goal line (0) to the opponent's (100), end
// zones -10..0 and 100..110, the offense always moving towards +x; y in yards from the offense's
// LEFT sideline (0) to its right (53.33): draw with x to the right and y down. Keyframes are
// [t, x, y] (the ball [t, x, y, h], h its height for the arc); interpolate linearly between keys
// and hold the last key. The same shape carries a REAL Big Data Bowl play (`kind: 'real'`, dense
// keys at 10 per second, its own label), so one player draws both.

export type PlayType = 'run' | 'pass' | 'sack' | 'scramble' | 'aborted' | 'dropback';
export type PlayTag =
  | 'play_action'
  | 'screen'
  | 'rpo'
  | 'deep_shot'
  | 'motion'
  | 'no_huddle'
  | 'trick_play'
  | 'fake_kick'
  | 'direct_snap';
export type PlayResultKind =
  'run' | 'complete' | 'incomplete' | 'interception' | 'sack' | 'scramble' | 'aborted' | 'dropback';
export type PlayConcept =
  | 'screen'
  | 'rpo'
  | 'play_action_deep'
  | 'inside_zone'
  | 'outside_zone'
  | 'cover_1'
  | 'cover_3'
  | '2_man';

export interface PlayResult {
  kind: PlayResultKind;
  yards: number | null;
  first_down: boolean;
  touchdown: boolean;
  turnover: boolean; // an interception or a fumble the defense recovered
  fumble_lost: boolean; // read from the play-by-play text
  text: string; // "Complete, +27 (22 air yards), first down"
}
/** One play's row in the play browser (the diagram's `play` block is the same + 2 fields). */
export interface PlaySummary {
  game_id: string;
  play_id: number;
  week: number;
  offense: string | null;
  defense: string | null;
  quarter: number | null;
  quarter_text: string | null; // "Q1" ... "OT"
  clock: string | null; // "5:32" left in the quarter
  down: number | null;
  distance: number | null;
  down_text: string | null; // "3rd & 8", "1st & Goal"
  yards_to_goal: number | null;
  spot: string | null; // "KC 29" (own half), "50", "LV 43" (the opponent's half)
  type: PlayType;
  tags: PlayTag[];
  result: PlayResult;
  epa: number | null;
  explosive: boolean;
  charted: boolean; // FTN charted the play (hash, box, rushers, motion, play-action ...)
  concept: PlayConcept | null; // the Big Data Bowl concept it matches ("See a real one")
  desc: string | null; // the play-by-play text, cleaned (names players)
}
export interface PlaycallPlaysWeek {
  week: number;
  game_id: string;
  opponent: string | null;
  home: boolean | null; // the page's team at home
  plays: number;
  label: string; // "Week 4 · at LV"
}
export interface PlaycallPlaysResponse {
  status: 'ok' | 'not_built' | 'no_plays' | 'no_week';
  message: string | null;
  season: number;
  seasons: number[];
  team: string;
  side: PlaycallSide; // offense: the team's plays; defense: the plays run against it
  label: string; // "Reconstructed from charting data, not player tracking"
  weeks: PlaycallPlaysWeek[]; // every week the team has plays, in order
  week: number | null; // the week shown (default: the newest)
  game: PlaycallPlaysWeek | null;
  filters: { down: number | null; type: string | null; result: string | null };
  options: {
    down: number[];
    type: { value: string; label: string }[];
    result: { value: string; label: string }[];
  };
  counts: { total: number; shown: number };
  plays: PlaySummary[]; // in game order
  library: boolean; // the Big Data Bowl concept library exists on this computer
}

export type DiagramKey = [number, number, number]; // [t s, x yd, y yd]
export type DiagramBallKey = [number, number, number, number]; // [t, x, y, h yd]
export interface DiagramPlayer {
  id: string;
  side: 'offense' | 'defense';
  role: 'ol' | 'qb' | 'back' | 'receiver' | 'dl' | 'lb' | 'db';
  label: string; // "C", "QB", "RB", "WR", "TE", "R" (receiver, type unknown), "DL", "LB", "CB", "S", "DB"
  approx: boolean; // a generic spot: draw it marked (dashed / hollow), never as if measured
  rusher: boolean;
  target: boolean; // the targeted receiver
  carrier: boolean; // carried the ball at the end
  track: DiagramKey[]; // [t, x, y] keyframes, at least one; times ascending
}
export interface DiagramMark {
  kind:
    | 'motion'
    | 'fake'
    | 'handoff'
    | 'lane'
    | 'run'
    | 'throw'
    | 'end'
    | 'sack'
    | 'incomplete'
    | 'interception';
  points: [number, number][]; // [x, y]
  t: number; // when it appears
  approx: boolean;
  text: string | null;
}
export interface DiagramPhase {
  name: string; // presnap, motion, snap, fake, handoff, carry, throw, catch, scramble, end (real: the tracking events)
  t: number;
  text: string;
}
export interface DiagramField {
  los: number;
  first_down: number | null; // null on goal to go
  goal: number; // 100
  ball_y: number;
  hash: 'left' | 'middle' | 'right' | null; // null: not charted (drawn in the middle)
  width: number; // 53.33
  view: [number, number]; // the x range to draw
}
export interface RealExampleRef {
  concept: PlayConcept;
  name: string; // "Inside zone run"
  match: string | null; // "a run inside the tackles"
  count: number; // real examples in the library
}
export interface PlayDiagram {
  version: number;
  kind: 'reconstruction' | 'real';
  label: string; // always shown on the player
  play: PlaySummary & { game_label: string | null; season: number };
  field: DiagramField;
  players: DiagramPlayer[]; // 11 + 11 for a reconstruction
  ball: DiagramBallKey[];
  marks: DiagramMark[];
  phases: DiagramPhase[]; // for the step button, times ascending
  duration: number; // seconds, the last key + a short hold
  known: string[]; // what the data says, with its source
  approximate: string[]; // drawn at generic spots
  unknown: string[]; // not drawn
  sources: string[];
  concept: PlayConcept | null;
  real_example: RealExampleRef | null; // set when the library holds real examples of `concept`
}
export interface PlayDiagramResponse {
  status: 'ok';
  diagram: PlayDiagram;
}

/** PC03 "See a real one": a real Big Data Bowl play of a concept, drawn by the same player.
 * GET /api/playcalling/concepts/{concept}?example=N. Read from playcalling/bdb_library/ on this
 * computer only (the competition's terms: non-commercial use, no redistribution). */
export interface RealClipResponse {
  status: 'ok' | 'no_library' | 'none'; // no_library: not built here; none: no example of it
  message: string | null;
  concept: PlayConcept;
  name: string; // "Inside zone run"
  match: string | null;
  note: string; // the source and terms line, shown under the player
  examples: { index: number; label: string }[]; // "2022 week 6 · KC vs LV · 3rd & 5"
  index: number | null; // the example shown
  diagram: PlayDiagram | null; // kind 'real': dense keys, approx false, its own label
}
