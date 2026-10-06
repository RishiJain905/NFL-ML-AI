import { describe, expect, it } from 'vitest';
import type { RunEvent, StepName } from '../../api/types';
import {
  at,
  clockAt,
  failingRun,
  pipelineBase as base,
  rehearsalRun,
  resumeRun,
  runInfo as info,
  tape,
  upTo,
  weeklyRun,
} from './liveFixtures';
import { describeNow, estimateProgress, foldRun, liveModel, projectRun, runKindOf, runStreamKey } from './live';
import { STEP_DEFS, STEP_INDEX } from './model';

const week5 = (events: RunEvent[], sec: number, extra: Partial<Parameters<typeof projectRun>[1]> = {}) =>
  liveModel({ season: 2026, week: 5, events, nowMs: clockAt(sec), ...extra });

const status = (m: ReturnType<typeof week5>, step: StepName) => m.steps[STEP_INDEX[step]];

describe('foldRun', () => {
  it('takes the events in seq order, each seq once (a replay or a reconnect may repeat or reorder them)', () => {
    const run = weeklyRun();
    const shuffled = [...run.events].reverse();
    const fold = foldRun([...shuffled, ...run.events.slice(0, 5)]);
    expect(fold.count).toBe(run.events.length);
    expect(fold.start?.seq).toBe(1);
    expect(fold.end?.type).toBe('run_end');
    expect(Object.keys(fold.steps)).toHaveLength(9);
  });

  it('keeps the highest progress fraction, so the ball never steps back', () => {
    const t = tape().start(0).stepStart(0, 'graph').progress(10, 'graph', 0.6, 'loading 14/23').progress(11, 'graph', 0.3, 'wiping again');
    const f = foldRun(t.events).steps.graph!;
    expect(f.fraction).toBe(0.6);
    expect(f.label).toBe('wiping again'); // the words are the newest report
  });

  it('ignores events for steps the model does not draw, and progress after a step ended', () => {
    const t = tape()
      .start(0)
      .stepStart(0, 'injury_refresh')
      .progress(1, 'injury_refresh', 0.5, 'x')
      .stepEnd(2, 'injury_refresh', 2)
      .stepStart(2, 'ingest')
      .stepEnd(5, 'ingest', 3)
      .progress(6, 'ingest', 0.2, 'late');
    const fold = foldRun(t.events);
    expect(Object.keys(fold.steps)).toEqual(['ingest']);
    expect(fold.steps.ingest!.label).toBeNull();
  });

  it('is empty-safe: no events, a malformed one, a missing time', () => {
    expect(foldRun([]).count).toBe(0);
    const bad = [{ seq: 1, type: 'log', text: 'x', cls: '', step: null, run_id: 'r' }] as unknown as RunEvent[];
    expect(foldRun(bad).count).toBe(1);
    expect(foldRun([null as unknown as RunEvent, {} as RunEvent]).count).toBe(0);
  });
});

describe('a run in progress', () => {
  const run = weeklyRun();

  it('before run_start: nothing has started, all nine steps are pending, and it is live', () => {
    const m = week5([], 0);
    expect(m.outcome).toBe('live');
    expect(m.idx).toBe(-1);
    expect(m.steps).toHaveLength(9);
    expect(m.steps.every((s) => s.status === 'pending')).toBe(true);
    expect(m.run.started).toBe(false);
    expect(describeNow(m)).toMatchObject({ eyebrow: 'Starting', name: 'Starting the run' });
  });

  it('steps are in STEP_DEFS order; the running step counts up from its step_start with the clock', () => {
    const events = upTo(run, 'graphHalf');
    const m = week5(events, 130);
    expect(m.steps.map((s) => s.key)).toEqual(STEP_DEFS.map((d) => d.name));
    expect(m.idx).toBe(STEP_INDEX.graph);
    expect(status(m, 'graph').status).toBe('running');
    expect(status(m, 'graph').seconds).toBeCloseTo(69); // 130 s on the clock, started at 61 s
    expect(week5(events, 160).steps[STEP_INDEX.graph].seconds).toBeCloseTo(99);
    // finished steps use step_end's own seconds, whatever the clock says
    expect(status(m, 'ingest')).toMatchObject({ status: 'ok', seconds: 25, detail: '30 datasets (0 optional failures)', progress: 1 });
    expect(status(m, 'curate').seconds).toBe(16);
  });

  it('pending steps say what to expect; the running one says what it reported last', () => {
    const m = week5(upTo(run, 'graphHalf'), 130);
    expect(status(m, 'player')).toMatchObject({ status: 'pending', detail: 'expected ~1m 35s', progress: 0 });
    expect(status(m, 'digest').detail).toBe('expected ~9m 00s');
    expect(status(m, 'graph').detail).toBe('loading 12/23 · node:Player');
  });

  it('a running step moves by its reports or by the clock (up to 90% of its usual time), and never claims done', () => {
    // ingest expected 25 s (no base): reported 0.5 at t=12
    const events = upTo(run, 'ingestHalf');
    expect(status(week5(events, 12), 'ingest').progress).toBeCloseTo(0.5); // the report wins over 0.9 × 12/25 = 0.43
    expect(status(week5(events, 20), 'ingest').progress).toBeCloseTo(0.72); // the clock has caught up: 0.9 × 20/25
    expect(status(week5(events, 24.9), 'ingest').progress).toBeLessThan(0.9 + 1e-9);
    expect(status(week5(events, 500), 'ingest').progress).toBe(0.9); // way over its time: 90%, not done
    expect(estimateProgress(1, 1000, 25)).toBe(0.99);
    expect(estimateProgress(Number.NaN, 5, 0)).toBeGreaterThan(0);
  });

  it('uses the last published week\'s step times as the expectation when the response has them', () => {
    const m = week5(upTo(run, 'graphHalf'), 130, { base: base({ graph: 100, player: 60, digest: 300, records: 5 }) });
    expect(status(m, 'graph').expected).toBe(100);
    expect(status(m, 'player').detail).toBe('expected ~1m 00s');
    // a report of 0.5 vs 0.9 × 69/100 = 0.62: the clock leads
    expect(status(m, 'graph').progress).toBeCloseTo(0.621);
  });

  it('elapsed is the steps so far (the running one by the clock); remaining is what is expected still', () => {
    const m = week5(upTo(run, 'graphHalf'), 130);
    const done = 25 + 1 + 16 + 4 + 15; // ingest .. game
    expect(m.elapsed).toBeCloseTo(done + 69);
    expect(m.run.elapsed).toBeCloseTo(done + 69);
    // graph expected 150 − 69 s in, then player 95, digest 540, records 20
    expect(m.remaining).toBeCloseTo(150 - 69 + 95 + 540 + 20);
  });

  it('remaining never goes below zero when a step runs longer than usual', () => {
    const m = week5(upTo(run, 'graphHalf'), 5000);
    expect(status(m, 'graph').seconds).toBeGreaterThan(150);
    expect(m.remaining).toBeCloseTo(95 + 540 + 20);
  });
});

describe('the digest waiting on the GLM call', () => {
  const run = weeklyRun();
  const events = upTo(run, 'glmWait');

  it('says it is waiting, with the elapsed time counting up from the call and the route order', () => {
    const detail = (sec: number) => status(week5(events, sec), 'digest').detail;
    expect(detail(310 + 192)).toBe('GLM writing · waiting for the model · 3m 12s · route baseten/fp8 → novita/fp8 → relace');
    expect(detail(310 + 193)).toBe('GLM writing · waiting for the model · 3m 13s · route baseten/fp8 → novita/fp8 → relace');
  });

  it('keeps a short form for the pipeline map and moves the bar and the clock the whole time', () => {
    const at = (sec: number) => week5(events, sec);
    expect(status(at(502), 'digest').brief).toBe('GLM writing · 3m 12s');
    const now = (sec: number) => describeNow(at(sec));
    expect(now(400)).toMatchObject({ eyebrow: 'Step 8 of 9', name: 'Digest (GLM)', tone: 'run' });
    expect(now(400).pct).toBeLessThan(now(500).pct);
    expect(now(500).pct).toBeLessThan(now(600).pct);
    expect(now(400).elapsed).not.toBe(now(401).elapsed);
    expect(now(400).small).toMatch(/^about .* left$/);
  });

  it('keeps moving after the call has run longer than usual: the bar breathes and the line says so', () => {
    const late = describeNow(week5(events, 310 + 1800));
    expect(late.overrun).toBe(true);
    expect(late.small).toBe('longer than usual · expected ~9m 00s');
    expect(late.pct).toBeLessThan(98.0001);
    expect(late.pct).toBeGreaterThan(describeNow(week5(events, 600)).pct);
  });

  it('fills in the provider, time, reasoning tokens and cost when the call returns', () => {
    const m = week5(upTo(run, 'glmDone'), 840);
    expect(status(m, 'digest').detail).toBe('GLM done on BaseTen · 8m 41s · 41,200 reasoning · $0.012');
    expect(status(m, 'digest').brief).toBe('GLM done on BaseTen');
  });

  it('a rewrite after the checks waits again, and a progress report newer than the call wins the line', () => {
    const t = tape().start(0).stepStart(0, 'digest').llm(1, 'start').llm(100, 'done', { provider: 'BaseTen', seconds: 99 }).llm(120, 'rewrite');
    const m = week5(t.events, 150);
    expect(status(m, 'digest').detail).toBe('GLM rewriting after the checks · waiting for the model · 30 s · route baseten/fp8 → novita/fp8 → relace');
    const t2 = tape().start(0).stepStart(0, 'digest').llm(1, 'start').progress(50, 'digest', 0.9, 'checks 4/11');
    expect(status(week5(t2.events, 60), 'digest').detail).toBe('checks 4/11');
    const failed = tape().start(0).stepStart(0, 'digest').llm(1, 'start').llm(40, 'failed', { seconds: 39 });
    expect(status(week5(failed.events, 50), 'digest').detail).toBe('GLM call failed after 39 s');
  });
});

describe('the end of a run', () => {
  it('a finished run is published: all steps ok with their seconds, the totals match, and the clock stops', () => {
    const run = weeklyRun();
    const m = week5(run.events, 99999);
    expect(m.outcome).toBe('published');
    expect(m.done).toBe(true);
    expect(m.failed).toBe(false);
    expect(m.idx).toBe(-1);
    expect(m.steps.every((s) => s.status === 'ok' && s.progress === 1)).toBe(true);
    expect(m.elapsed).toBe(25 + 1 + 16 + 4 + 15 + 150 + 95 + 540 + 20);
    expect(m.remaining).toBe(0);
    // a later clock changes nothing
    expect(week5(run.events, 100000).elapsed).toBe(m.elapsed);
    expect(describeNow(m)).toMatchObject({
      eyebrow: 'Final',
      name: 'Published',
      pct: 100,
      tone: 'ok',
      small: 'exit 0 · records written',
      sub: 'published week 5',
    });
  });

  it('a failing step: red, keeps its progress, the steps after it are skipped and the records still run', () => {
    const run = failingRun();
    const m = week5(run.events, 9999);
    expect(m.outcome).toBe('failed');
    expect(m.failed).toBe(true);
    const player = status(m, 'player');
    expect(player.status).toBe('failed');
    expect(player.progress).toBeCloseTo(0.5); // the one report before it failed
    expect(player.detail).toBe('refit failed for sacks-edge: calibration seed missing');
    expect(status(m, 'digest')).toMatchObject({ status: 'skipped', detail: 'skipped (an earlier step failed)' });
    expect(status(m, 'records').status).toBe('ok');
    expect(describeNow(m)).toMatchObject({
      eyebrow: 'Stopped',
      name: 'Failed · player',
      tone: 'err',
      small: 'exit 1 · records written',
      sub: 'refit failed for sacks-edge: calibration seed missing',
    });
  });

  it('while the failure is the latest event the run is still live: the rest is skipped, the records are next', () => {
    const run = failingRun();
    const m = week5(upTo(run, 'playerFailed'), 300);
    expect(m.outcome).toBe('live');
    expect(status(m, 'player').status).toBe('failed');
    expect(status(m, 'digest').status).toBe('skipped');
    expect(status(m, 'records')).toMatchObject({ status: 'pending', detail: 'expected ~20 s' });
    const now = describeNow(m);
    expect(now.name).toBe('Run records'); // the next thing it will do
    expect(now.tone).toBe('run');
  });

  it('a step that failed keeps the progress it had just before it failed (the ball does not jump)', () => {
    const run = failingRun();
    const before = status(week5(upTo(run, 'playerProgress'), 240), 'player');
    const after = status(week5(upTo(run, 'playerFailed'), 241), 'player');
    expect(before.status).toBe('running');
    expect(after.status).toBe('failed');
    expect(after.progress).toBeCloseTo(before.progress);
  });

  it('the stream ending without a run_end means the process died: the running step failed, the rest stopped', () => {
    const run = weeklyRun();
    const end = info({ status: 'interrupted', finished: at(330), exit_code: null, message: 'The process ended without finishing.' });
    const m = week5(upTo(run, 'glmWait'), 99999, { end });
    expect(m.outcome).toBe('failed');
    expect(status(m, 'digest')).toMatchObject({ status: 'failed', detail: 'interrupted: the run ended before this step finished' });
    expect(status(m, 'records').status).toBe('skipped');
    // the clock stops at its last event, not at "now"
    expect(status(m, 'digest').seconds).toBeCloseTo(24); // began at 306 s, the server says the run ended at 330 s
    expect(describeNow(m)).toMatchObject({ eyebrow: 'Stopped', name: 'Failed · digest', tone: 'err' });
  });

  it('not ready: the run stopped before the digest and nothing was published', () => {
    const t = tape()
      .start(0)
      .stepStart(0, 'ingest')
      .stepEnd(25, 'ingest', 25)
      .stepStart(25, 'ready')
      .stepEnd(26, 'ready', 1, 'week 4: 15/16 final', 'failed')
      .end(27, { status: 'not_ready', exit_code: 3, message: 'week 4 is not final yet (ATL@NO)', published: false, failed_step: 'ready' });
    const m = week5(t.events, 9999);
    expect(m.outcome).toBe('stopped');
    expect(m.failed).toBe(false);
    expect(describeNow(m)).toMatchObject({ eyebrow: 'Stopped', name: 'Not ready', tone: 'warn', small: 'exit 3', sub: 'week 4 is not final yet (ATL@NO)' });
    expect(status(m, 'curate').status).toBe('skipped');
  });

  it('a run_end that names a failed step the steps never reported marks it failed', () => {
    const t = tape().start(0).stepStart(0, 'ingest').stepEnd(25, 'ingest', 25).end(26, { status: 'failed', exit_code: 1, message: 'ready: boom', published: false, failed_step: 'ready' });
    const m = week5(t.events, 99);
    expect(status(m, 'ready')).toMatchObject({ status: 'failed', detail: 'ready: boom' });
    expect(m.run.failedStep).toBe('ready');
  });
});

describe('a resume', () => {
  const run = resumeRun();

  it('draws the steps before it as done from run_start.earlier (their seconds, degraded kept) and runs from there', () => {
    const m = week5(upTo(run, 'playerHalf'), 40, { kind: 'resume' });
    expect(m.run.fromStep).toBe('player');
    expect(m.run.kind).toBe('resume');
    expect(status(m, 'ingest')).toMatchObject({ status: 'ok', seconds: 25, progress: 1 });
    expect(status(m, 'game')).toMatchObject({ status: 'degraded', seconds: 15 });
    expect(status(m, 'graph')).toMatchObject({ status: 'ok', seconds: 150 });
    expect(m.idx).toBe(STEP_INDEX.player);
    expect(status(m, 'digest').status).toBe('pending');
    // the run's own time is this invocation's, not the earlier sitting's
    expect(m.run.elapsed).toBeCloseTo(40);
    expect(m.elapsed).toBeCloseTo(25 + 1 + 16 + 4 + 15 + 150 + 40);
  });

  it('counts the whole nine steps, since the earlier ones are part of the run', () => {
    const m = week5(upTo(run, 'playerHalf'), 40);
    expect(describeNow(m)).toMatchObject({ eyebrow: 'Step 7 of 9', name: 'Player model' });
  });

  it('finishes like any run, with the earlier steps in the totals', () => {
    const m = week5(run.events, 9999);
    expect(m.outcome).toBe('published');
    expect(m.steps.every((s) => s.status === 'ok' || s.status === 'degraded')).toBe(true);
    expect(m.run.elapsed).toBe(95 + 315 + 20);
  });

  it('falls back to the week\'s own records for a resume whose run_start has no `earlier` map', () => {
    const t = tape().start(0, { kind: 'resume', steps: ['player', 'digest'], from_step: 'player', earlier: undefined as never }).stepStart(0, 'player');
    const b = base({});
    b.steps[STEP_INDEX.ingest] = { ...b.steps[STEP_INDEX.ingest], status: 'ok', seconds: 30, detail: '30 datasets' };
    const m = week5(t.events, 5, { base: b });
    expect(status(m, 'ingest')).toMatchObject({ status: 'ok', seconds: 30, detail: '30 datasets' });
  });
});

describe('a rehearsal', () => {
  const run = rehearsalRun();

  it('shows ingest, curate, ratings, graph and the records as "not in a rehearsal", not as pending', () => {
    const m = week5(upTo(run, 'playerHalf'), 50);
    for (const s of ['ingest', 'curate', 'ratings', 'graph', 'records'] as const) {
      expect(status(m, s)).toMatchObject({ status: 'skipped', detail: 'not in a rehearsal', expected: 0 });
    }
    expect(status(m, 'ready').status).toBe('ok');
    expect(status(m, 'game').status).toBe('ok');
    expect(m.idx).toBe(STEP_INDEX.player);
    expect(status(m, 'digest').status).toBe('pending');
    // only what the rehearsal still has to do counts as left
    expect(m.remaining).toBeCloseTo(95 - 34 + 540, 0);
  });

  it('counts its own steps: step 3 of 4, not step 7 of 9', () => {
    expect(describeNow(week5(upTo(run, 'playerHalf'), 50))).toMatchObject({ eyebrow: 'Step 3 of 4', name: 'Player model' });
    expect(describeNow(week5(upTo(run, 'glmWait'), 150))).toMatchObject({ eyebrow: 'Step 4 of 4' });
  });

  it('finishes as a rehearsal, not as a published week', () => {
    const m = week5(run.events, 9999);
    expect(m.outcome).toBe('published');
    expect(describeNow(m)).toMatchObject({ eyebrow: 'Final', name: 'Rehearsal done', tone: 'ok' });
  });

  it('says the rehearsal stopped, not that nothing was published, when it stops before the digest', () => {
    const t = tape('r3')
      .start(0, { kind: 'rehearsal', steps: ['ready', 'game', 'player', 'digest'] })
      .stepStart(0, 'ready')
      .stepEnd(2, 'ready', 2, 'week 4 is not final', 'failed')
      .end(3, { status: 'not_ready', exit_code: 3, message: '', published: false, failed_step: 'ready' });
    const m = week5(t.events, 9999);
    expect(m.rehearsal).toBe(true);
    expect(describeNow(m).sub).toBe('The rehearsal stopped before the digest.');
    expect(week5(weeklyRun().events, 9999).rehearsal).toBe(false);
  });

  it('knows it is a rehearsal before run_start arrives (run/current says so)', () => {
    const m = week5([], 0, { kind: 'rehearsal' });
    expect(status(m, 'ingest').status).toBe('skipped');
    expect(status(m, 'ready').status).toBe('pending');
  });

  it('a rehearsal resume: the earlier ready and game are done, the other steps are still not in a rehearsal', () => {
    const t = tape('r2').start(0, {
      kind: 'rehearsal',
      steps: ['player', 'digest'],
      from_step: 'player',
      earlier: { ready: { status: 'ok', seconds: 1 }, game: { status: 'ok', seconds: 15 } },
    });
    const m = week5(t.events, 1);
    expect(status(m, 'ready')).toMatchObject({ status: 'ok', seconds: 1 });
    expect(status(m, 'game')).toMatchObject({ status: 'ok', seconds: 15 });
    expect(status(m, 'ingest')).toMatchObject({ status: 'skipped', detail: 'not in a rehearsal' });
    expect(status(m, 'graph')).toMatchObject({ status: 'skipped', detail: 'not in a rehearsal' });
    expect(status(m, 'player').status).toBe('pending');
  });

  it('does not borrow the archive\'s W&B links for its steps', () => {
    const b = base({});
    b.steps[STEP_INDEX.game] = { ...b.steps[STEP_INDEX.game], wandb_url: 'https://wandb.ai/e/p/runs/abc' };
    expect(status(week5(run.events, 9999, { base: b }), 'game').wandbUrl).toBeNull();
    const weekly = week5(weeklyRun().events, 9999, { base: b });
    expect(status(weekly, 'game').wandbUrl).toBe('https://wandb.ai/e/p/runs/abc');
  });
});

describe('describeNow while live', () => {
  it('names the step, its words, how far through, and how long is left', () => {
    const m = week5(upTo(weeklyRun(), 'graphHalf'), 130);
    const now = describeNow(m);
    expect(now.eyebrow).toBe('Step 6 of 9');
    expect(now.name).toBe('Knowledge graph');
    expect(now.sub).toBe('loading 12/23 · node:Player');
    expect(now.elapsed).toBe('2m 10s');
    expect(now.small).toBe('about 12m 16s left'); // graph 81 s, player 95, digest 540, records 20
    expect(now.pct).toBeCloseTo((130 / 866) * 100, 0);
    expect(now.tone).toBe('run');
  });

  it('between two steps it points at the next one', () => {
    const t = tape().start(0).stepStart(0, 'ingest').stepEnd(25, 'ingest', 25);
    expect(describeNow(week5(t.events, 26))).toMatchObject({ eyebrow: 'Step 2 of 9', name: 'Ready check', sub: 'Starting' });
  });

  it('flags a failure while the records are still running', () => {
    const run = failingRun();
    const t = tape().start(0).stepStart(0, 'player').stepEnd(5, 'player', 5, 'boom', 'failed').stepStart(6, 'records');
    expect(describeNow(week5(t.events, 8))).toMatchObject({ name: 'Run records', sub: 'Player model failed · running · 2 s', tone: 'err' });
    expect(run.events.length).toBeGreaterThan(0);
  });
});

describe('kinds and keys', () => {
  it('runKindOf reads run/current, and a terminal run by its command', () => {
    expect(runKindOf({ kind: 'resume', command: '' })).toBe('resume');
    expect(runKindOf({ kind: 'rehearsal', command: '' })).toBe('rehearsal');
    expect(runKindOf({ kind: 'terminal', command: 'uv run nfl weekly run --season 2026 --week 5 --from-step player' })).toBe('resume');
    expect(runKindOf({ kind: 'terminal', command: 'nfl weekly rehearse --season 2026 --week 5' })).toBe('rehearsal');
    expect(runKindOf({ kind: 'terminal', command: 'nfl weekly run --auto' })).toBe('weekly');
    expect(runKindOf(null)).toBe('weekly');
  });

  it('a run is identified by its run_id, or by its start time while a terminal run has no events yet', () => {
    expect(runStreamKey(info({}))).toBe('20261006T140012Z-a1b2');
    expect(runStreamKey(info({ run_id: null }))).toBe('2026-10-06T10:00:12-04:00');
    expect(runStreamKey(info({ run_id: null, started: null }))).toBe('');
  });
});
