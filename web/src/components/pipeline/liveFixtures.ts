// Small synthetic runs for the live-mode tests (never real data): a `tape` records the events a run
// writes (`ops/events.py`: run_start, step_start, progress, step_end, llm, log, run_end) with
// clock offsets in seconds, and the runs below are built from it. `mark(name)` remembers where in
// the tape something happened, so a test can cut the run there (`upTo(run, 'digestWait')`).
//
// A real run's recorded events file (one JSON object per line) drops in through `parseEventsJsonl`;
// see `liveRecorded.test.ts`, which runs invariants over every `*.jsonl` in `src/test/fixtures/`.

import type {
  LlmEvent,
  PipelineResponse,
  PipelineStep,
  RunEndEvent,
  RunEvent,
  RunInfo,
  RunStartEvent,
  StepName,
} from '../../api/types';
import { STEP_DEFS } from './model';

/** Week 5's Tuesday run starts here (the offsets below are seconds after it). */
export const T0 = Date.parse('2026-10-06T14:00:12+00:00');
export const RUN_ID = '20261006T140012Z-a1b2';

/** An event time `sec` seconds after the run began, ISO with an offset. */
export const at = (sec: number): string => new Date(T0 + sec * 1000).toISOString();
/** The same, as the clock a test passes to `projectRun`. */
export const clockAt = (sec: number): number => T0 + sec * 1000;

export const ALL_STEPS: StepName[] = ['ingest', 'ready', 'curate', 'ratings', 'game', 'graph', 'player', 'digest'];

export interface Tape {
  events: RunEvent[];
  marks: Record<string, number>;
  start: (t: number, over?: Partial<RunStartEvent>) => Tape;
  stepStart: (t: number, step: string) => Tape;
  progress: (t: number, step: string, fraction: number, label: string) => Tape;
  stepEnd: (t: number, step: string, seconds: number, detail?: string, status?: 'ok' | 'degraded' | 'failed') => Tape;
  llm: (t: number, phase: LlmEvent['phase'], over?: Partial<LlmEvent>) => Tape;
  log: (t: number, text: string, cls?: '' | 'acc' | 'ok' | 'err' | 'warn' | 'dim', step?: string | null) => Tape;
  end: (t: number, over?: Partial<RunEndEvent>) => Tape;
  mark: (name: string) => Tape;
}

export function tape(runId: string = RUN_ID, over: { season?: number; week?: number } = {}): Tape {
  const season = over.season ?? 2026;
  const week = over.week ?? 5;
  const events: RunEvent[] = [];
  const marks: Record<string, number> = {};
  let seq = 0;
  const base = (t: number) => ({ seq: ++seq, t: at(t), run_id: runId });
  const api: Tape = {
    events,
    marks,
    start: (t, o = {}) => {
      events.push({
        ...base(t),
        type: 'run_start',
        kind: 'weekly',
        command: `nfl weekly run --auto --expect-week ${week}`,
        season,
        week,
        steps: [...ALL_STEPS],
        from_step: null,
        earlier: {},
        launched_by: null,
        via: 'control-room',
        plan: [],
        ...o,
      });
      return api;
    },
    stepStart: (t, step) => {
      events.push({ ...base(t), type: 'step_start', step });
      return api;
    },
    progress: (t, step, fraction, label) => {
      events.push({ ...base(t), type: 'progress', step, done: Math.round(fraction * 100), total: 100, fraction, label });
      return api;
    },
    stepEnd: (t, step, seconds, detail = `${step} done`, status = 'ok') => {
      events.push({ ...base(t), type: 'step_end', step, status, seconds, detail });
      return api;
    },
    llm: (t, phase, o = {}) => {
      events.push({
        ...base(t),
        type: 'llm',
        phase,
        writer: 'openrouter',
        model: 'z-ai/glm-5.3-flash',
        route: ['baseten/fp8', 'novita/fp8', 'relace'],
        provider: null,
        seconds: null,
        usage: null,
        cost: null,
        ...o,
      });
      return api;
    },
    log: (t, text, cls = '', step = null) => {
      events.push({ ...base(t), type: 'log', step, text, cls });
      return api;
    },
    end: (t, o = {}) => {
      events.push({
        ...base(t),
        type: 'run_end',
        status: 'ok',
        exit_code: 0,
        message: `published week ${week}`,
        published: true,
        failed_step: null,
        material: null,
        ...o,
      });
      return api;
    },
    mark: (name) => {
      marks[name] = seq;
      return api;
    },
  };
  return api;
}

/** The week's pipeline response while a run holds the lock, with expected seconds (the last published week's real times) per step. */
export function pipelineBase(expected: Partial<Record<StepName, number>> = {}, over: Partial<PipelineResponse> = {}): PipelineResponse {
  const step = (name: StepName): PipelineStep => ({
    step: name,
    status: 'pending',
    seconds: null,
    expected_seconds: expected[name] ?? STEP_DEFS.find((d) => d.name === name)?.defaultSeconds ?? 10,
    started: null,
    finished: null,
    detail: null,
    this_run: null,
    wandb_url: null,
  });
  return {
    season: 2026,
    week: 5,
    state: 'running',
    source: 'weekly_run',
    steps: STEP_DEFS.map((d) => step(d.name)),
    sittings: 1,
    step_seconds: null,
    expected_from: { season: 2026, week: 4 },
    failed_step: null,
    error: null,
    log: [],
    log_source: 'rebuilt',
    tiles: { checks_passed: null, checks_total: null, first_time: null, llm_cost: null, llm_calls: 0, llm_provider: null, digest_share: null },
    no_run_records: false,
    ...over,
  };
}

/** What `GET /api/run/current` says about Tuesday's weekly run: launched from the app, still going. */
export function runInfo(over: Partial<RunInfo> = {}): RunInfo {
  return {
    run_id: RUN_ID,
    kind: 'weekly',
    launched_from: 'app',
    command: 'nfl weekly run --auto --expect-week 5',
    season: 2026,
    week: 5,
    started: '2026-10-06T10:00:12-04:00',
    finished: null,
    status: null,
    exit_code: null,
    message: null,
    published: null,
    failed_step: null,
    material: null,
    has_events: true,
    output_tail: [],
    ...over,
  };
}

export interface FixtureRun {
  events: RunEvent[];
  marks: Record<string, number>;
}

/** The events up to and including a mark. */
export const upTo = (run: FixtureRun, mark: string): RunEvent[] => run.events.filter((e) => e.seq <= run.marks[mark]);

/** A whole Tuesday run in about 14 minutes: the GLM call alone is 8m 41s (the real week-5 shape, shortened). */
export function weeklyRun(): FixtureRun {
  const t = tape();
  t.start(0)
    .log(0, 'nfl weekly run --auto --expect-week 5', 'acc')
    .stepStart(0, 'ingest')
    .progress(12, 'ingest', 0.5, 'dataset 15/30 · nflverse · pbp')
    .mark('ingestHalf')
    .stepEnd(25, 'ingest', 25, '30 datasets (0 optional failures)')
    .stepStart(25, 'ready')
    .stepEnd(26, 'ready', 1, 'week 4: 16/16 final')
    .stepStart(26, 'curate')
    .stepEnd(42, 'curate', 16, '32 tables; quality checks pass')
    .stepStart(42, 'ratings')
    .stepEnd(46, 'ratings', 4, 'ratings rebuilt through week 4')
    .stepStart(46, 'game')
    .stepEnd(61, 'game', 15, '15 games · game-model:2026-w05 → production')
    .stepStart(61, 'graph')
    .log(62, 'graph: Neo4j up', 'dim', 'graph')
    .progress(100, 'graph', 0.5, 'loading 12/23 · node:Player')
    .mark('graphHalf')
    .stepEnd(211, 'graph', 150, '≈17.4k nodes · 12 queries · 4 insights picked')
    .stepStart(211, 'player')
    .progress(250, 'player', 0.4, 'refitting 7/23 · rec_yds-wrte')
    .mark('playerRefit')
    .stepEnd(306, 'player', 95, 'graded week 4 · 23 stats · consistency ok')
    .stepStart(306, 'digest')
    .log(307, 'digest: payload built (graph sections: 4 insights)', 'ok', 'digest')
    .llm(310, 'start')
    .mark('digestStart')
    .mark('glmWait'); // the wait begins: nothing arrives until the call returns
  t.llm(831, 'done', { provider: 'BaseTen', seconds: 521, usage: { prompt: 9000, completion: 6000, reasoning: 41200 }, cost: 0.012 })
    .mark('glmDone')
    .log(832, 'checks: complete ✓ number_provenance ✓ entity_binding ✓', 'dim', 'digest')
    .stepEnd(846, 'digest', 540, 'checks passed first time · GLM on BaseTen 8m 41s')
    .stepStart(846, 'records')
    .mark('recordsStart')
    .stepEnd(866, 'records', 20, 'run_summary.json · pipeline-2026-w05')
    .log(866, 'published reports/2026/week05-digest.md', 'ok')
    .end(867);
  return { events: t.events, marks: t.marks };
}

/** A run whose player step fails half way through its refits (the rehearsal hook's shape); the records still run. */
export function failingRun(): FixtureRun {
  const t = tape();
  t.start(0)
    .stepStart(0, 'ingest')
    .stepEnd(25, 'ingest', 25, '30 datasets')
    .stepStart(25, 'ready')
    .stepEnd(26, 'ready', 1)
    .stepStart(26, 'curate')
    .stepEnd(42, 'curate', 16)
    .stepStart(42, 'ratings')
    .stepEnd(46, 'ratings', 4)
    .stepStart(46, 'game')
    .stepEnd(61, 'game', 15)
    .stepStart(61, 'graph')
    .stepEnd(211, 'graph', 150)
    .stepStart(211, 'player')
    .progress(240, 'player', 0.5, 'rehearsal test hook: failing here on purpose')
    .mark('playerProgress')
    .log(241, '✕ player: refit failed for sacks-edge', 'err', 'player')
    .stepEnd(241, 'player', 30, 'refit failed for sacks-edge: calibration seed missing', 'failed')
    .mark('playerFailed')
    .log(242, 'step failed → digest skipped; run records still written', 'warn')
    .stepStart(242, 'records')
    .stepEnd(262, 'records', 20, 'run_summary.json')
    .end(263, { status: 'failed', exit_code: 1, message: 'player failed: refit failed for sacks-edge', published: false, failed_step: 'player' });
  return { events: t.events, marks: t.marks };
}

/** A resume from the player step: the earlier sitting left ingest to graph done (`earlier` says how long each took). */
export function resumeRun(): FixtureRun {
  const t = tape('20261006T150000Z-c3d4');
  t.start(0, {
    kind: 'resume',
    command: 'nfl weekly run --season 2026 --week 5 --from-step player',
    steps: ['player', 'digest'],
    from_step: 'player',
    earlier: {
      ingest: { status: 'ok', seconds: 25 },
      ready: { status: 'ok', seconds: 1 },
      curate: { status: 'ok', seconds: 16 },
      ratings: { status: 'ok', seconds: 4 },
      game: { status: 'degraded', seconds: 15 },
      graph: { status: 'ok', seconds: 150 },
    },
  })
    .stepStart(0, 'player')
    .progress(40, 'player', 0.5, 'refitting 12/23 · pd-cbs')
    .mark('playerHalf')
    .stepEnd(95, 'player', 95, 'graded week 4 · 23 stats')
    .stepStart(95, 'digest')
    .llm(98, 'start')
    .mark('glmWait')
    .llm(400, 'done', { provider: 'Novita', seconds: 302, usage: { prompt: 8000, completion: 5000, reasoning: 30000 }, cost: 0.009 })
    .stepEnd(410, 'digest', 315, 'checks passed first time')
    .stepStart(410, 'records')
    .stepEnd(430, 'records', 20)
    .end(431);
  return { events: t.events, marks: t.marks };
}

/** A rehearsal (`nfl weekly rehearse`): only ready, game, player and digest run; nothing is published. */
export function rehearsalRun(): FixtureRun {
  const t = tape('20261006T160000Z-e5f6');
  t.start(0, {
    kind: 'rehearsal',
    command: 'nfl weekly rehearse --season 2026 --week 5 --fresh',
    steps: ['ready', 'game', 'player', 'digest'],
  })
    .stepStart(0, 'ready')
    .stepEnd(1, 'ready', 1, 'week 4: 16/16 final')
    .stepStart(1, 'game')
    .stepEnd(16, 'game', 15, '15 games')
    .stepStart(16, 'player')
    .progress(50, 'player', 0.5, 'refitting 12/23 · pd-cbs')
    .mark('playerHalf')
    .stepEnd(111, 'player', 95, 'graded week 4')
    .stepStart(111, 'digest')
    .llm(113, 'start')
    .mark('glmWait')
    .llm(300, 'done', { provider: 'BaseTen', seconds: 187, usage: { prompt: 8000, completion: 5000, reasoning: 20000 }, cost: 0.007 })
    .stepEnd(310, 'digest', 199, 'checks passed first time')
    .end(311, { message: 'rehearsal finished; nothing live was touched', published: false });
  return { events: t.events, marks: t.marks };
}

/** A recorded events file (one JSON object per line) as the events the app streams. Bad lines are skipped. */
export function parseEventsJsonl(text: string): RunEvent[] {
  const out: RunEvent[] = [];
  for (const line of text.split(/\r?\n/)) {
    const s = line.trim();
    if (!s) continue;
    try {
      const ev = JSON.parse(s) as RunEvent;
      if (ev && typeof ev === 'object' && typeof ev.seq === 'number') out.push(ev);
    } catch {
      /* a half-written last line */
    }
  }
  return out;
}
