// Live mode (CR02): a run's events → the step model the three pipeline views already draw
// (mockup: simModel). The views take a `PipelineModel` and update in place, so a live run is just
// a model rebuilt from the events and the clock. Two pure steps: `foldRun` (once per batch of
// events) and `projectRun` (every second, for the running step's elapsed time); `liveModel` does
// both. Nothing here reads a file or the network.

import type {
  LlmEvent,
  PipelineResponse,
  RunEndEvent,
  RunEvent,
  RunInfo,
  RunStartEvent,
  StepName,
} from '../../api/types';
import { comma, duration } from '../../lib/format';
import { STEP_DEFS, STEP_INDEX, type Outcome, type PipelineModel, type ViewStep } from './model';

export type RunKindName = RunStartEvent['kind'];

const isStep = (s: string | null | undefined): s is StepName => s != null && Object.hasOwn(STEP_INDEX, s);
const clamp01 = (v: number): number => (Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0);
const num = (v: number | null | undefined): number => (v != null && Number.isFinite(v) ? v : 0);
const toMs = (t: string | null | undefined, fallback: number | null): number | null => {
  const v = t ? Date.parse(t) : Number.NaN;
  return Number.isFinite(v) ? v : fallback;
};

// ---- folding the events -------------------------------------------------------------------------

/** The digest's GLM call, as the `llm` events last left it. */
export interface LlmState {
  phase: 'writing' | 'rewriting' | 'done' | 'failed';
  seq: number;
  /** when this phase began (the wait is measured from here) */
  sinceMs: number;
  route: string[];
  provider: string | null;
  seconds: number | null;
  reasoning: number | null;
  cost: number | null;
}

export interface StepFold {
  startedMs: number | null;
  status: 'running' | 'ok' | 'degraded' | 'failed';
  /** step_end's seconds (null while running) */
  seconds: number | null;
  detail: string | null;
  /** the highest `progress.fraction` seen (0–1) */
  fraction: number;
  label: string | null;
  labelSeq: number;
  endedMs: number | null;
  llm: LlmState | null;
}

export interface RunFold {
  start: RunStartEvent | null;
  startMs: number | null;
  end: RunEndEvent | null;
  endMs: number | null;
  steps: Partial<Record<StepName, StepFold>>;
  /** the newest event's time */
  lastMs: number | null;
  count: number;
}

/** The events in `seq` order, each `seq` once (a replay or a reconnect may repeat or reorder them). */
function ordered(events: readonly RunEvent[]): RunEvent[] {
  const seen = new Set<number>();
  const out: RunEvent[] = [];
  for (const e of events) {
    if (!e || typeof e.seq !== 'number' || seen.has(e.seq)) continue;
    seen.add(e.seq);
    out.push(e);
  }
  return out.sort((a, b) => a.seq - b.seq);
}

function openStep(startedMs: number | null): StepFold {
  return { startedMs, status: 'running', seconds: null, detail: null, fraction: 0, label: null, labelSeq: 0, endedMs: null, llm: null };
}

/** A step's fold while it is still running; a progress or llm event for a step that never started starts it. */
function running(fold: RunFold, name: string, ms: number | null): StepFold | null {
  if (!isStep(name)) return null;
  const f = (fold.steps[name] ??= openStep(ms));
  return f.status === 'running' ? f : null;
}

export function foldRun(events: readonly RunEvent[]): RunFold {
  const fold: RunFold = {
    start: null,
    startMs: null,
    end: null,
    endMs: null,
    steps: {},
    lastMs: null,
    count: 0,
  };
  for (const e of ordered(events)) {
    fold.count += 1;
    const ms = toMs(e.t, fold.lastMs);
    if (ms !== null) fold.lastMs = ms;
    switch (e.type) {
      case 'run_start':
        if (!fold.start) {
          fold.start = e;
          fold.startMs = ms;
        }
        break;
      case 'step_start':
        if (isStep(e.step)) fold.steps[e.step] = openStep(ms);
        break;
      case 'progress': {
        const f = running(fold, e.step, ms);
        if (f) {
          const fraction = Number.isFinite(e.fraction) ? e.fraction : e.total > 0 ? e.done / e.total : 0;
          // never backwards: a ball that stepped back would look like a bug
          f.fraction = Math.max(f.fraction, clamp01(fraction));
          f.label = e.label;
          f.labelSeq = e.seq;
        }
        break;
      }
      case 'step_end': {
        if (!isStep(e.step)) break; // a step this model doesn't draw
        const f = (fold.steps[e.step] ??= openStep(ms === null ? null : ms - num(e.seconds) * 1000));
        f.status = e.status;
        f.seconds = Number.isFinite(e.seconds) ? e.seconds : null;
        f.detail = e.detail ?? null;
        f.endedMs = ms;
        break;
      }
      case 'llm': {
        // the GLM call lives in the digest step; the events don't name a step
        const f = running(fold, 'digest', ms);
        if (f) f.llm = foldLlm(e, ms ?? 0, f.llm);
        break;
      }
      case 'run_end':
        fold.end = e;
        fold.endMs = ms;
        break;
      default:
        break; // `log` lines feed the console, not the model
    }
  }
  return fold;
}

function foldLlm(e: LlmEvent, ms: number, prev: LlmState | null): LlmState {
  const route = e.route?.length ? e.route : (prev?.route ?? []);
  const base = { seq: e.seq, sinceMs: ms, route, provider: null, seconds: null, reasoning: null, cost: null };
  switch (e.phase) {
    case 'start':
      return { ...base, phase: 'writing' };
    case 'rewrite':
      return { ...base, phase: 'rewriting' };
    case 'failed':
      return { ...base, phase: 'failed', seconds: e.seconds ?? null };
    default:
      return {
        ...base,
        phase: 'done',
        provider: e.provider ?? null,
        seconds: e.seconds ?? null,
        reasoning: e.usage?.reasoning ?? null,
        cost: e.cost ?? null,
      };
  }
}


// ---- projecting onto the clock ------------------------------------------------------------------

/** What a rehearsal runs (`ops/rehearsal`): the other five steps (and the records) are "not in a rehearsal". */
const REHEARSAL_STEPS: StepName[] = ['ready', 'game', 'player', 'digest'];

export interface LiveRun {
  kind: RunKindName;
  /** the first step this invocation runs, for a resume */
  fromStep: StepName | null;
  /** run_start has been seen, or some step has begun */
  started: boolean;
  /** this invocation's own time: the steps it ran (a resume doesn't count the earlier sittings) */
  elapsed: number;
  status: string | null;
  exitCode: number | null;
  message: string | null;
  published: boolean | null;
  failedStep: StepName | null;
  /** per step, in `STEP_DEFS` order: it belongs to this run (done now, or earlier in a resume) */
  inRun: boolean[];
}

/** A `PipelineModel` the views draw as it is, plus what the now-bar needs. */
export interface LiveModel extends PipelineModel {
  run: LiveRun;
}

export interface ProjectInput {
  season: number;
  week: number;
  /** the week's pipeline response: expected seconds, and the details of steps a resume doesn't redo */
  base?: PipelineResponse | null;
  /** the stream's final RunInfo, once the server said the run is over */
  end?: RunInfo | null;
  /** the run's kind until its `run_start` has been seen (from `run/current`) */
  kind?: RunKindName;
  nowMs: number;
}

export interface LiveInput extends ProjectInput {
  events: readonly RunEvent[];
}

/** A step that is running moves the ball by its reports, or by the clock (up to 90% of its usual time). */
export function estimateProgress(fraction: number, seconds: number, expected: number): number {
  return Math.min(0.99, Math.max(clamp01(fraction), 0.9 * Math.min(1, seconds / Math.max(expected, 1))));
}

const join = (parts: (string | null | false | undefined)[]): string => parts.filter(Boolean).join(' · ');

/** What a running step is doing, in words: the last progress label, or the GLM wait. */
function runningText(name: StepName, f: StepFold, nowMs: number, seconds: number): { detail: string; brief?: string } {
  const llm = f.llm;
  if (name === 'digest' && llm && (!f.label || llm.seq > f.labelSeq)) {
    const waited = duration(Math.max(0, (nowMs - llm.sinceMs) / 1000));
    const route = llm.route.length ? `route ${llm.route.join(' → ')}` : null;
    if (llm.phase === 'writing' || llm.phase === 'rewriting') {
      const what = llm.phase === 'writing' ? 'GLM writing' : 'GLM rewriting after the checks';
      return {
        detail: join([what, 'waiting for the model', waited, route]),
        brief: `${llm.phase === 'writing' ? 'GLM writing' : 'GLM rewriting'} · ${waited}`,
      };
    }
    if (llm.phase === 'failed') {
      const text = `GLM call failed${llm.seconds != null ? ` after ${duration(llm.seconds)}` : ''}`;
      return { detail: text };
    }
    const cost = llm.cost != null ? `$${llm.cost.toFixed(3)}` : null;
    const reasoning = llm.reasoning != null ? `${comma(llm.reasoning)} reasoning` : null;
    const text = join([
      llm.provider ? `GLM done on ${llm.provider}` : 'GLM done',
      llm.seconds != null ? duration(llm.seconds) : null,
      reasoning,
      cost,
    ]);
    return { detail: text, brief: llm.provider ? `GLM done on ${llm.provider}` : 'GLM done' };
  }
  if (f.label) return { detail: f.label };
  return { detail: `running · ${duration(seconds)}` };
}

function outcomeOf(
  terminated: boolean,
  status: string | null,
  published: boolean,
  kind: RunKindName,
  anyFailed: boolean,
): Outcome {
  if (!terminated) return 'live';
  if (status === 'not_ready' || status === 'week_mismatch') return 'stopped';
  if (status === 'failed' || status === 'interrupted') return 'failed';
  if (status === 'ok' || status === 'degraded') {
    // a rehearsal never publishes (nothing live is touched): its own digest is the finish
    return published || kind === 'rehearsal' ? 'published' : 'stopped';
  }
  if (anyFailed) return 'failed';
  return published || kind === 'rehearsal' ? 'published' : 'stopped';
}

export function projectRun(fold: RunFold, { season, week, base, end, kind: fallbackKind, nowMs }: ProjectInput): LiveModel {
  const kind: RunKindName = fold.start?.kind ?? fallbackKind ?? 'weekly';
  const rehearsal = kind === 'rehearsal';
  const fromName = fold.start?.from_step ?? null;
  const fromIdx = isStep(fromName) ? STEP_INDEX[fromName] : -1;
  const earlier = fold.start?.earlier ?? {};
  const terminated = fold.end !== null || end != null;
  // a run that ended without saying so (the process is gone) stopped at its last event or its end time
  const stoppedMs = fold.endMs ?? toMs(end?.finished, null) ?? fold.lastMs ?? nowMs;
  const clock = terminated ? stoppedMs : nowMs;
  const byName = new Map((base?.steps ?? []).map((s) => [s.step, s]));
  const planned = new Set<StepName>(
    fold.start
      ? fold.start.steps.filter(isStep)
      : rehearsal
        ? REHEARSAL_STEPS
        : STEP_DEFS.filter((d) => d.name !== 'records').map((d) => d.name),
  );
  for (const name of Object.keys(fold.steps)) if (isStep(name)) planned.add(name);
  const inRun = STEP_DEFS.map((d, i) =>
    d.name === 'records' ? !rehearsal : planned.has(d.name) || Object.hasOwn(earlier, d.name) || (i < fromIdx && !rehearsal),
  );

  let failedEarlier = false;
  const steps: ViewStep[] = STEP_DEFS.map((def, i) => {
    const b = byName.get(def.name);
    const expected = b?.expected_seconds ?? def.defaultSeconds;
    // a rehearsal's steps are not the archive's: its W&B links would point at the wrong runs
    const common = { key: def.name, label: def.label, wandbUrl: rehearsal ? null : (b?.wandb_url ?? null) };
    const f = fold.steps[def.name];

    if (f) {
      if (f.status === 'running') {
        const seconds = f.startedMs === null ? 0 : Math.max(0, (clock - f.startedMs) / 1000);
        const progress = estimateProgress(f.fraction, seconds, expected);
        if (terminated) {
          // the run is over and this step never ended: the process died under it
          failedEarlier = true;
          return { ...common, status: 'failed', seconds, expected, detail: 'interrupted: the run ended before this step finished', progress };
        }
        const text = runningText(def.name, f, nowMs, seconds);
        return { ...common, status: 'running', seconds, expected, detail: text.detail, brief: text.brief, progress };
      }
      const seconds =
        f.seconds ?? (f.startedMs !== null && f.endedMs !== null ? Math.max(0, (f.endedMs - f.startedMs) / 1000) : 0);
      if (f.status === 'failed') {
        failedEarlier = true;
        const progress = estimateProgress(f.fraction, seconds, expected);
        return { ...common, status: 'failed', seconds, expected, detail: f.detail || fold.end?.message || 'failed', progress };
      }
      return { ...common, status: f.status, seconds, expected, detail: f.detail ?? '', progress: 1 };
    }

    const prior = earlier[def.name];
    if (prior || (i < fromIdx && !rehearsal)) {
      // a resume: the steps before it were done by an earlier sitting (run_start's `earlier` says how they ended)
      const status = prior?.status === 'degraded' ? 'degraded' : 'ok';
      const seconds = num(prior ? prior.seconds : b?.seconds);
      const detail = (!rehearsal && b?.detail) || 'done in an earlier sitting';
      return { ...common, status, seconds, expected, detail, progress: 1 };
    }
    if (!inRun[i]) {
      const detail = rehearsal ? 'not in a rehearsal' : 'not part of this run';
      return { ...common, status: 'skipped', seconds: 0, expected: 0, detail, progress: 0 };
    }
    if (terminated || (failedEarlier && def.name !== 'records')) {
      const detail = failedEarlier ? 'skipped (an earlier step failed)' : 'skipped (the run stopped before it)';
      return { ...common, status: 'skipped', seconds: 0, expected, detail, progress: 0 };
    }
    // records still run after a failure, so they stay pending
    return { ...common, status: 'pending', seconds: 0, expected, detail: `expected ~${duration(expected)}`, progress: 0 };
  });

  const message = fold.end?.message ?? end?.message ?? null;
  // the run's own account of where it failed wins over a step that never reported
  const failedName = fold.end?.failed_step ?? end?.failed_step ?? null;
  if (terminated && isStep(failedName)) {
    const s = steps[STEP_INDEX[failedName]];
    if (s.status === 'pending' || s.status === 'skipped' || s.status === 'running') {
      steps[STEP_INDEX[failedName]] = { ...s, status: 'failed', detail: message || 'failed' };
    }
  }

  const status = fold.end?.status ?? end?.status ?? null;
  const published = fold.end?.published ?? end?.published ?? null;
  const outcome = outcomeOf(terminated, status, published === true, kind, steps.some((s) => s.status === 'failed'));
  const idx = steps.reduce((last, s, i) => (s.status === 'running' ? i : last), -1);
  const elapsed = steps.reduce((a, s) => a + s.seconds, 0);
  const remaining = terminated
    ? 0
    : steps.reduce(
        (a, s) => a + (s.status === 'pending' ? s.expected : s.status === 'running' ? Math.max(0, s.expected - s.seconds) : 0),
        0,
      );
  const ranHere = steps.reduce((a, s, i) => a + (fold.steps[STEP_DEFS[i].name] ? s.seconds : 0), 0);
  const failedStep = steps.find((s) => s.status === 'failed')?.key ?? (isStep(failedName) ? failedName : null);

  return {
    season,
    week,
    outcome,
    steps,
    idx,
    done: outcome !== 'live',
    failed: outcome === 'failed',
    elapsed,
    remaining,
    idleLabel: null,
    rehearsal,
    run: {
      kind,
      fromStep: isStep(fromName) ? fromName : null,
      started: fold.start !== null || Object.keys(fold.steps).length > 0,
      elapsed: ranHere,
      status,
      exitCode: fold.end?.exit_code ?? end?.exit_code ?? null,
      message,
      published,
      failedStep,
      inRun,
    },
  };
}

export function liveModel(input: LiveInput): LiveModel {
  return projectRun(foldRun(input.events), input);
}

// ---- which run it is ------------------------------------------------------------------------

/** A run's identity for the stream: its run_id, or its start time for a terminal run that hasn't written events yet. */
export const runStreamKey = (run: RunInfo): string => run.run_id ?? run.started ?? '';

// ---- which kind of run it is --------------------------------------------------------------------

/** The run's kind from `run/current` (a terminal run: judged by its command), until its `run_start` is seen. */
export function runKindOf(info?: { kind: string; command: string } | null): RunKindName {
  const kind = info?.kind;
  if (kind === 'weekly' || kind === 'resume' || kind === 'injury_update' || kind === 'rehearsal') return kind;
  const cmd = info?.command ?? '';
  if (/injury-update/.test(cmd)) return 'injury_update';
  if (/rehears/.test(cmd)) return 'rehearsal';
  if (/--from-step/.test(cmd)) return 'resume';
  return 'weekly';
}

// ---- the now-bar's words ------------------------------------------------------------------------

export interface NowView {
  /** "Step 5 of 9", "Starting", "Final", "Stopped" */
  eyebrow: string;
  name: string;
  sub: string;
  /** 0–100 */
  pct: number;
  elapsed: string;
  small: string;
  tone: 'run' | 'ok' | 'warn' | 'err';
  /** running longer than the step usually takes: the bar pulses so the page never looks frozen */
  overrun: boolean;
}

const exitText = (code: number | null): string | null => (code === null ? null : `exit ${code}`);

export function describeNow(m: LiveModel): NowView {
  const r = m.run;
  const total = r.elapsed + m.remaining;
  const pct = total > 0 ? (r.elapsed / total) * 100 : 0;
  const elapsed = duration(r.elapsed);
  // a rehearsal runs four of the nine steps: count the ones this run has
  const slots = r.inRun.filter(Boolean).length;
  const stepNo = (i: number): number => r.inRun.slice(0, i + 1).filter(Boolean).length;

  if (m.outcome === 'live') {
    const cur = m.idx >= 0 ? m.steps[m.idx] : null;
    const failed = m.steps.find((s) => s.status === 'failed');
    if (cur) {
      const overrun = cur.seconds > cur.expected;
      return {
        eyebrow: `Step ${stepNo(m.idx)} of ${slots}`,
        name: cur.label,
        sub: failed ? `${failed.label} failed · ${cur.detail}` : cur.detail,
        pct: Math.min(98, pct),
        elapsed,
        small: overrun ? `longer than usual · expected ~${duration(cur.expected)}` : `about ${duration(m.remaining)} left`,
        tone: failed ? 'err' : 'run',
        overrun,
      };
    }
    const next = m.steps.findIndex((s, i) => s.status === 'pending' && r.inRun[i]);
    if (!r.started || next < 0) {
      return {
        eyebrow: r.started ? 'Wrapping up' : 'Starting',
        name: r.started ? 'Finishing' : 'Starting the run',
        sub: r.started ? 'Waiting for the run to finish' : 'Taking the lock and checking the calendar',
        pct: Math.min(98, pct),
        elapsed,
        small: `about ${duration(m.remaining)} left`,
        tone: 'run',
        overrun: false,
      };
    }
    return {
      eyebrow: `Step ${stepNo(next)} of ${slots}`,
      name: m.steps[next].label,
      sub: 'Starting',
      pct: Math.min(98, pct),
      elapsed,
      small: `about ${duration(m.remaining)} left`,
      tone: 'run',
      overrun: false,
    };
  }

  const recordsOk = ['ok', 'degraded'].includes(m.steps[STEP_INDEX.records].status);
  // a run that stopped short: how far through the plan it got (the steps it never reached count as left)
  const unreached = m.steps.reduce((a, s) => a + (s.status === 'skipped' ? s.expected : 0), 0);
  const reached = r.elapsed + unreached > 0 ? (r.elapsed / (r.elapsed + unreached)) * 100 : 0;
  if (m.outcome === 'published') {
    return {
      eyebrow: 'Final',
      name: r.kind === 'rehearsal' ? 'Rehearsal done' : 'Published',
      sub: r.message || (r.kind === 'rehearsal' ? 'The rehearsal finished; nothing live was touched.' : 'The digest is written and the week is published.'),
      pct: 100,
      elapsed,
      small: join([exitText(r.exitCode), recordsOk && 'records written']) || 'done',
      tone: 'ok',
      overrun: false,
    };
  }
  if (m.outcome === 'failed') {
    const step = m.steps.find((s) => s.status === 'failed');
    return {
      eyebrow: 'Stopped',
      name: r.failedStep ? `Failed · ${r.failedStep}` : 'Failed',
      sub: step?.detail || r.message || 'The run failed.',
      pct: reached,
      elapsed,
      small: join([exitText(r.exitCode), recordsOk && 'records written']) || 'failed',
      tone: 'err',
      overrun: false,
    };
  }
  return {
    eyebrow: 'Stopped',
    name: r.status === 'not_ready' ? 'Not ready' : r.status === 'week_mismatch' ? 'Wrong week' : 'Stopped',
    sub:
      r.message ||
      (r.kind === 'rehearsal'
        ? 'The rehearsal stopped before the digest.'
        : 'The run stopped before the digest, and nothing was published.'),
    pct: reached,
    elapsed,
    small: exitText(r.exitCode) ?? 'stopped',
    tone: 'warn',
    overrun: false,
  };
}
