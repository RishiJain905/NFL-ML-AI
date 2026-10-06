// The Pipeline tab's states around a run (CR02): the plan with its pre-flight panel, a run going
// (drawn from the event stream), the end of a run without a flash back to the plan, a failure, and
// rehearsal mode. The finished and failed weeks as their files have them are in PipelineTab.test.tsx.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PipelineResponse, PreflightResponse, RunCurrent, RunEvent } from '../../api/types';
import {
  ALL_STEPS,
  clockAt,
  failingRun,
  pipelineBase,
  rehearsalRun,
  runInfo,
  upTo,
  weeklyRun,
} from '../../components/pipeline/liveFixtures';
import { META, mockApi, renderApp } from '../../test/utils';
import { installFakeEventSource } from '../../test/fakeEventSource';
import { PipelineTab } from './PipelineTab';

const clock = vi.hoisted(() => {
  let now = 0;
  const subs = new Set<() => void>();
  return {
    get: () => now,
    set: (v: number) => {
      now = v;
      subs.forEach((f) => f());
    },
    subscribe: (f: () => void) => {
      subs.add(f);
      return () => {
        subs.delete(f);
      };
    },
  };
});
vi.mock('../../lib/useNow', async () => {
  const { useSyncExternalStore } = await import('react');
  return { useNow: () => useSyncExternalStore(clock.subscribe, clock.get) };
});

const PIPELINE = '/api/weeks/2026/5/pipeline';
const weekly = weeklyRun();

const PLAN: PipelineResponse = pipelineBase(
  {},
  {
    state: 'plan',
    source: 'plan',
    sittings: 0,
    log: [{ ts: '10:00:00', text: "the calendar's plan for this week", cls: 'acc' }],
    log_source: 'calendar',
  },
);

function finishedWeek(over: Partial<PipelineResponse> = {}): PipelineResponse {
  const seconds: Record<string, number> = { ingest: 25, ready: 1, curate: 16, ratings: 4, game: 15, graph: 150, player: 95, digest: 540, records: 20 };
  const base = pipelineBase({}, { state: 'finished', source: 'run_summary', step_seconds: 866, log_source: 'events', ...over });
  return {
    ...base,
    steps: base.steps.map((s) => ({ ...s, status: 'ok', seconds: seconds[s.step], detail: `${s.step} done` })),
  };
}

const PREFLIGHT: PreflightResponse = {
  checked_at: '2026-10-06T14:00:00+00:00',
  mode: 'live',
  season: 2026,
  week: 5,
  checks: [
    { id: 'calendar', label: 'Week 5 is the calendar week', status: 'ok', value: 'Tue Oct 6', reason: null, blocking: true },
    { id: 'last_week_final', label: 'Week 4 is final', status: 'ok', value: '16/16 final', reason: null, blocking: true },
  ],
  run: { allowed: true, reason: null, command: 'nfl weekly run --auto --expect-week 5', retry_after: null },
  confirm: {
    last_week: 'week 4 · 16/16 final',
    deadline: '2026-10-09T00:15:00+00:00',
    hours_to_deadline: 58.2,
    retry_until: null,
    steps: ALL_STEPS,
    publishes: ['week05-digest.md', 'production alias'],
  },
  resume: { allowed: false, reason: 'No failed run to resume.', command: null, step: null },
  injury_update: { allowed: false, reason: 'Opens Saturday.', command: null, opens: null, closes: null, material_last: null },
  dry_run_log: [
    { ts: '10:00:01', text: 'previous week 4: 16/16 final, complete', cls: 'ok' },
    { ts: '10:00:01', text: 'dry run: would run ingest, ready, curate, ratings, game, graph, player, digest for 2026 week 05', cls: 'ok' },
  ],
};

const RUNNING: RunCurrent = { state: 'running', run: runInfo() };
const IDLE: RunCurrent = { state: 'idle', run: null };

let es: ReturnType<typeof installFakeEventSource>;
beforeEach(() => {
  es = installFakeEventSource();
  clock.set(clockAt(0));
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const send = (events: RunEvent[]) => act(() => es.last().emitRuns(events));
const tick = (sec: number) => act(() => clock.set(clockAt(sec)));
const nowBar = () => within(screen.getByRole('region', { name: 'Run progress' }));
const planHeading = () => screen.queryByRole('heading', { name: 'The plan for this run' });

describe('PipelineTab: the current week before its run', () => {
  it('puts the pre-flight panel above the plan, with the dry run as the log', async () => {
    mockApi({ [PIPELINE]: PLAN, '/api/preflight': PREFLIGHT, '/api/run/current': IDLE, '/api/meta': META });
    renderApp(<PipelineTab season={2026} week={5} />);
    const panel = await screen.findByRole('region', { name: 'Pre-flight' });
    expect(within(panel).getByText('Week 4 is final')).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: 'Run week 5' })).toBeEnabled();
    const heading = await screen.findByRole('heading', { name: 'The plan for this run' });
    expect(panel.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText(/Week 5 hasn.t run yet/)).toBeInTheDocument();
    expect(screen.queryByText(/come with CR02/)).toBeNull();
    // the log card is the pre-flight's dry run, labelled as such
    const log = screen.getByRole('region', { name: 'Log' });
    await within(log).findByText('previous week 4: 16/16 final, complete');
    expect(within(log).getByText('the dry run (read-only) · secrets scrubbed')).toBeInTheDocument();
    expect(es.all()).toHaveLength(0); // nothing running: no stream
  });

  it('shows no pre-flight when it is about another week, and falls back to the calendar\'s plan log', async () => {
    mockApi({
      [PIPELINE]: PLAN,
      '/api/preflight': { ...PREFLIGHT, week: 6, season: 2026 },
      '/api/run/current': IDLE,
      '/api/meta': META,
    });
    renderApp(<PipelineTab season={2026} week={5} />);
    expect(await screen.findByRole('heading', { name: 'The plan for this run' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("from the calendar's plan · secrets scrubbed")).toBeInTheDocument());
    expect(screen.queryByRole('region', { name: 'Pre-flight' })).toBeNull();
    expect(screen.getByText("the calendar's plan for this week")).toBeInTheDocument();
  });

  it('does not ask for a pre-flight on a published week', async () => {
    const api = mockApi({ [PIPELINE]: finishedWeek(), '/api/run/current': IDLE, '/api/meta': META });
    renderApp(<PipelineTab season={2026} week={5} />);
    expect(await screen.findByRole('heading', { name: 'The run' })).toBeInTheDocument();
    expect(api.mock.calls.some((c) => c[0] === '/api/preflight')).toBe(false);
    expect(screen.getByText(/real step times from run_summary.json/)).toBeInTheDocument();
  });
});

describe('PipelineTab: a run of this week is going', () => {
  it('draws it from the stream: notice, now-bar, the view (live) and the streaming log; no plan, no tiles', async () => {
    mockApi({ [PIPELINE]: pipelineBase(), '/api/run/current': RUNNING, '/api/meta': META, '/api/preflight': PREFLIGHT });
    renderApp(<PipelineTab season={2026} week={5} />);
    expect(await screen.findByText(/A run of week 5 is in progress\./)).toBeInTheDocument();
    expect(screen.getByText(/Closing this tab doesn.t stop a run/)).toBeInTheDocument();
    expect(es.all()).toHaveLength(1);
    tick(130);
    send(upTo(weekly, 'graphHalf'));
    expect(await nowBar().findByText('Step 6 of 9')).toBeInTheDocument();
    expect(nowBar().getByText('loading 12/23 · node:Player')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'The run' })).toBeInTheDocument();
    expect(screen.getByText('Drive chart · live')).toBeInTheDocument();
    const log = screen.getByRole('region', { name: 'Log' });
    expect(within(log).getByText('graph: Neo4j up')).toBeInTheDocument();
    expect(within(log).getByText('streamed from the run · secrets scrubbed')).toBeInTheDocument();
    expect(planHeading()).toBeNull();
    expect(screen.queryByText('Time in steps')).toBeNull();
    expect(screen.queryByRole('region', { name: 'Pre-flight' })).toBeNull();
  });

  it('says a terminal-launched run was started from a terminal', async () => {
    mockApi({
      [PIPELINE]: pipelineBase(),
      '/api/run/current': { state: 'running', run: runInfo({ launched_from: 'terminal', run_id: null }) } satisfies RunCurrent,
      '/api/meta': META,
    });
    renderApp(<PipelineTab season={2026} week={5} />);
    expect(await screen.findByText(/It was started from a terminal\./)).toBeInTheDocument();
  });

  it('shows the pipeline as its records stand while a terminal run has not written events yet', async () => {
    mockApi({
      [PIPELINE]: pipelineBase(),
      '/api/run/current': { state: 'running', run: runInfo({ launched_from: 'terminal', run_id: null, has_events: false }) } satisfies RunCurrent,
      '/api/meta': META,
    });
    renderApp(<PipelineTab season={2026} week={5} />);
    expect(await screen.findByText(/A run of this week is in progress\./)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Run progress' })).toBeNull();
    expect(es.all()).toHaveLength(0);
  });

  it('switching the view mid-run keeps the run, and does not reopen the stream', async () => {
    mockApi({ [PIPELINE]: pipelineBase(), '/api/run/current': RUNNING, '/api/meta': META });
    renderApp(<PipelineTab season={2026} week={5} />);
    tick(130);
    await screen.findByText(/A run of week 5 is in progress\./);
    send(upTo(weekly, 'graphHalf'));
    await nowBar().findByText('Step 6 of 9');
    fireEvent.click(screen.getByRole('button', { name: 'Pipeline map' }));
    expect(screen.getByText('Pipeline map · live')).toBeInTheDocument();
    expect(document.querySelector('[data-node="graph"] .node-prog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Timeline' }));
    expect(document.querySelector('[data-step="graph"]')).toHaveClass('running');
    expect(nowBar().getByText('Step 6 of 9')).toBeInTheDocument();
    expect(es.all()).toHaveLength(1);
  });

  it('ignores a run of another week, and a Saturday injury update (WeekPage shows that one)', async () => {
    mockApi({
      [PIPELINE]: PLAN,
      '/api/run/current': { state: 'running', run: runInfo({ week: 6 }) } satisfies RunCurrent,
      '/api/meta': META,
    });
    const first = renderApp(<PipelineTab season={2026} week={5} />);
    expect(await screen.findByRole('heading', { name: 'The plan for this run' })).toBeInTheDocument();
    expect(es.all()).toHaveLength(0);
    first.unmount();
    mockApi({
      [PIPELINE]: PLAN,
      '/api/run/current': { state: 'running', run: runInfo({ kind: 'injury_update' }) } satisfies RunCurrent,
      '/api/meta': META,
    });
    renderApp(<PipelineTab season={2026} week={5} />);
    expect(await screen.findByRole('heading', { name: 'The plan for this run' })).toBeInTheDocument();
    expect(es.all()).toHaveLength(0);
  });
});

describe('PipelineTab: the end of a run', () => {
  it('keeps the final model on screen until the refetched week shows the result: no flash back to the plan', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-06T14:15:00Z'));
    let phase: 'running' | 'over' | 'caught-up' = 'running';
    let release: () => void = () => {};
    const held = new Promise<void>((r) => (release = r));
    const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === '/api/meta') return ok(META);
        if (url === '/api/run/current') {
          return ok(phase === 'running' ? RUNNING : { state: 'finished', run: runInfo({ finished: '2026-10-06T10:14:39-04:00', status: 'ok', published: true }) });
        }
        if (url === PIPELINE) {
          if (phase === 'running') return ok(PLAN); // the week has not caught up with the run yet
          if (phase === 'over') {
            await held; // the refetch is slow...
            phase = 'caught-up';
            return ok(PLAN); // ...and its first answer is still the plan
          }
          return ok(finishedWeek());
        }
        return new Response('{}', { status: 404 });
      }),
    );
    renderApp(<PipelineTab season={2026} week={5} />);
    await screen.findByText(/A run of week 5 is in progress\./);
    send(weekly.events);
    await nowBar().findByText('Published');

    phase = 'over';
    act(() => es.last().emitEnd(runInfo({ finished: '2026-10-06T10:14:39-04:00', status: 'ok', exit_code: 0, published: true })));
    // run/current says finished and the week still says plan: the page is still the run
    await waitFor(() => expect(screen.getByText(/Week 5 is published\./)).toBeInTheDocument());
    expect(planHeading()).toBeNull();
    expect(nowBar().getByText('Published')).toBeInTheDocument();
    release();
    // the first refetch answers "plan" (stale): still not shown. It asks again, and then the week has caught up.
    await waitFor(() => expect(phase).toBe('caught-up'));
    expect(planHeading()).toBeNull();
    expect(nowBar().getByText('Published')).toBeInTheDocument();
    vi.setSystemTime(new Date(Date.now() + 3000));
    tick(3);
    await waitFor(() => expect(screen.getByText(/real step times from run_summary.json/)).toBeInTheDocument());
    expect(planHeading()).toBeNull();
    expect(screen.queryByRole('region', { name: 'Run progress' })).toBeNull();
    expect(screen.getByText('Time in steps')).toBeInTheDocument(); // CR01's finished view
    expect(screen.getByText('Published')).toBeInTheDocument(); // the Status tile
  });

  it('a failed run: one failure banner (with the step and why), the fumble, then the week\'s own failed view', async () => {
    const run = failingRun();
    const failedWeek = pipelineBase(
      {},
      { state: 'failed', source: 'run_summary', failed_step: 'player', error: 'player failed: refit failed for sacks-edge', log_source: 'events' },
    );
    failedWeek.steps = failedWeek.steps.map((s) =>
      s.step === 'player'
        ? { ...s, status: 'failed', seconds: 30, detail: 'refit failed for sacks-edge: calibration seed missing' }
        : s.step === 'digest'
          ? { ...s, status: 'skipped' }
          : { ...s, status: 'ok', seconds: 5 },
    );
    let over = false;
    const api = mockApi({
      [PIPELINE]: () => (over ? failedWeek : pipelineBase()),
      '/api/run/current': () => (over ? { state: 'finished', run: runInfo({ status: 'failed', exit_code: 1, failed_step: 'player' }) } : RUNNING),
      '/api/meta': META,
    });
    renderApp(<PipelineTab season={2026} week={5} />);
    await screen.findByText(/A run of week 5 is in progress\./);
    send(run.events);
    expect(await nowBar().findByText('Failed · player')).toBeInTheDocument();
    expect(screen.getAllByText('The player step failed.')).toHaveLength(1);
    expect(screen.getByText('FUMBLE')).toBeInTheDocument();
    over = true;
    act(() => es.last().emitEnd(runInfo({ status: 'failed', exit_code: 1, failed_step: 'player', finished: '2026-10-06T10:04:23-04:00' })));
    await waitFor(() => expect(screen.getByText(/real step times from run_summary.json/)).toBeInTheDocument());
    expect(screen.getAllByText('The player step failed.')).toHaveLength(1); // the archive's banner took over
    expect(screen.queryByRole('region', { name: 'Run progress' })).toBeNull();
    expect(api.mock.calls.filter((c) => c[0] === PIPELINE).length).toBeGreaterThan(1);
  });
});

describe('PipelineTab: rehearsal mode', () => {
  const REHEARSAL_META = { ...META, mode: { dev: false, rehearsal: true } };
  const run = rehearsalRun();
  const info = runInfo({ kind: 'rehearsal', run_id: run.events[0].run_id });

  it('shows the pre-flight about the rehearsed week above the week\'s archive, even for a published week', async () => {
    mockApi({
      [PIPELINE]: finishedWeek(),
      '/api/run/current': IDLE,
      '/api/meta': REHEARSAL_META,
      '/api/preflight': { ...PREFLIGHT, mode: 'rehearsal' } satisfies PreflightResponse,
    });
    renderApp(<PipelineTab season={2026} week={5} />);
    const panel = await screen.findByRole('region', { name: 'Pre-flight' });
    expect(within(panel).getByRole('button', { name: 'Rehearse week 5' })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'The run' })).toBeInTheDocument();
  });

  it('follows a rehearsal that is running, with its four steps', async () => {
    mockApi({
      [PIPELINE]: finishedWeek(),
      '/api/run/current': { state: 'running', run: info } satisfies RunCurrent,
      '/api/meta': REHEARSAL_META,
      '/api/preflight': { ...PREFLIGHT, mode: 'rehearsal' } satisfies PreflightResponse,
    });
    renderApp(<PipelineTab season={2026} week={5} />);
    expect(await screen.findByText(/A rehearsal of week 5 is in progress\./)).toBeInTheDocument();
    tick(50);
    send(upTo(run, 'playerHalf'));
    expect(await nowBar().findByText('Step 3 of 4')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'The rehearsal' })).toBeInTheDocument();
    expect(await screen.findByText('Run in progress')).toBeInTheDocument(); // the pre-flight panel's own chip
    expect(screen.queryByText('Time in steps')).toBeNull(); // the archive waits until it is over
  });

  it('shows a finished rehearsal from its replay in its own card, with the archive\'s view below', async () => {
    mockApi({
      [PIPELINE]: finishedWeek(),
      '/api/run/current': { state: 'finished', run: { ...info, finished: '2026-10-06T12:05:11-04:00', status: 'ok' } } satisfies RunCurrent,
      '/api/meta': REHEARSAL_META,
      '/api/preflight': { ...PREFLIGHT, mode: 'rehearsal' } satisfies PreflightResponse,
    });
    renderApp(<PipelineTab season={2026} week={5} />);
    await waitFor(() => expect(es.all()).toHaveLength(1)); // the replay
    send(run.events);
    act(() => es.last().emitEnd({ ...info, finished: '2026-10-06T12:05:11-04:00', status: 'ok' }));
    expect(await nowBar().findByText('Rehearsal done')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'The rehearsal' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Rehearsal log' })).toBeInTheDocument();
    // and the archive's own run below it
    expect(await screen.findByRole('heading', { name: 'The run' })).toBeInTheDocument();
    expect(screen.getByText(/real step times from run_summary.json/)).toBeInTheDocument();
    expect(screen.getByText('Time in steps')).toBeInTheDocument();
  });

  it('is not a rehearsal page outside rehearsal mode: a finished rehearsal is not replayed', async () => {
    mockApi({
      [PIPELINE]: finishedWeek(),
      '/api/run/current': { state: 'finished', run: { ...info, finished: '2026-10-06T12:05:11-04:00', status: 'ok' } } satisfies RunCurrent,
      '/api/meta': META,
    });
    renderApp(<PipelineTab season={2026} week={5} />);
    expect(await screen.findByRole('heading', { name: 'The run' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'The rehearsal' })).toBeNull();
    expect(es.all()).toHaveLength(0);
  });
});
