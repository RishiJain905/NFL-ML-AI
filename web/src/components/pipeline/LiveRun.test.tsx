// LiveRun on a fake event stream: the now-bar, the views, the log and the end of the run. The clock
// (`useNow`) is replaced by one the test sets, so a second passes when the test says so.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RunEvent } from '../../api/types';
import { installFakeEventSource } from '../../test/fakeEventSource';
import { mockApi, renderApp } from '../../test/utils';
import { clockAt, failingRun, pipelineBase, rehearsalRun, resumeRun, runInfo, tape, upTo, weeklyRun } from './liveFixtures';
import { LiveRun, type ViewChoice } from './LiveRun';

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

const PIPELINE_URL = '/api/weeks/2026/5/pipeline';
const weekly = weeklyRun();

let es: ReturnType<typeof installFakeEventSource>;
beforeEach(() => {
  es = installFakeEventSource();
  clock.set(clockAt(0));
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const choice = (view: ViewChoice['view'] = 'drive'): ViewChoice => ({ view, surprised: false, choose: vi.fn() });
const pipelineCalls = (f: ReturnType<typeof mockApi>) => f.mock.calls.filter((c) => c[0] === PIPELINE_URL).length;
const send = (events: RunEvent[]) => act(() => es.last().emitRuns(events));
const tick = (sec: number) => act(() => clock.set(clockAt(sec)));
const nowBar = () => within(screen.getByRole('region', { name: 'Run progress' }));
/** The stream batches events for 50 ms before rendering them. */
const settled = async (text: string | RegExp) => nowBar().findByText(text);

/** The week's pipeline answer: while the run holds the lock it says running, once the run is over it says finished. */
let runOver = false;
function renderRun(props: Partial<React.ComponentProps<typeof LiveRun>> = {}) {
  runOver = false;
  const api = mockApi({ [PIPELINE_URL]: () => pipelineBase({}, { state: runOver ? 'finished' : 'running' }) });
  const onSettled = vi.fn();
  const view = renderApp(
    <LiveRun season={2026} week={5} info={runInfo()} base={pipelineBase()} choice={choice()} onSettled={onSettled} {...props} />,
  );
  return { api, onSettled, ...view };
}

describe('LiveRun following a run', () => {
  it('starts from nothing: "Starting", all steps pending, the stream opened', async () => {
    renderRun();
    expect(es.all()).toHaveLength(1);
    expect(es.last().url).toBe('/api/run/stream');
    expect(await settled('Starting the run')).toBeInTheDocument();
    expect(screen.getByText(/A run of week 5 is in progress\./)).toBeInTheDocument();
    expect(screen.getByText(/Closing this tab doesn.t stop a run/)).toBeInTheDocument();
    expect(screen.getByText('streamed from the run · secrets scrubbed')).toBeInTheDocument();
    expect(screen.getByText('Between steps')).toBeInTheDocument(); // the scorebug: nothing has started
  });

  it('follows the run: the now-bar, the view and the log all move with the events', async () => {
    const { container } = renderRun();
    tick(130);
    send(upTo(weekly, 'graphHalf'));
    expect(await settled('Step 6 of 9')).toBeInTheDocument();
    expect(nowBar().getByText('Knowledge graph')).toBeInTheDocument();
    expect(nowBar().getByText('loading 12/23 · node:Player')).toBeInTheDocument();
    expect(nowBar().getByText('2m 10s')).toBeInTheDocument();
    // the drive chart: the running row and the log lines (US Eastern, as the schedule's clock)
    expect(container.querySelector('[data-row="graph"]')).toHaveClass('running');
    const log = screen.getByRole('log');
    expect(within(log).getByText('graph: Neo4j up')).toHaveClass('dim');
    expect(within(log).getByText('10:00:12')).toBeInTheDocument();
    expect(log.querySelector('.cursor')).toBeInTheDocument(); // blinking while live
    // a later batch appends
    send([...weekly.events.filter((e) => e.seq > weekly.marks.graphHalf && e.seq <= weekly.marks.playerRefit)]);
    tick(260);
    expect(await settled('Step 7 of 9')).toBeInTheDocument();
    expect(nowBar().getByText('refitting 7/23 · rec_yds-wrte')).toBeInTheDocument();
    expect(within(log).getByText('graph: Neo4j up')).toBeInTheDocument(); // still there
  });

  it('the GLM wait keeps counting up each second, with the route, and then fills in what the call cost', async () => {
    renderRun();
    tick(502);
    send(upTo(weekly, 'glmWait'));
    const wait = 'GLM writing · waiting for the model · 3m 12s · route baseten/fp8 → novita/fp8 → relace';
    expect(await settled(wait)).toBeInTheDocument();
    tick(503);
    expect(await settled(wait.replace('3m 12s', '3m 13s'))).toBeInTheDocument();
    expect(nowBar().getByText('Digest (GLM)')).toBeInTheDocument();
    tick(840);
    send(weekly.events.filter((e) => e.seq > weekly.marks.glmWait && e.seq <= weekly.marks.glmDone));
    expect(await settled('GLM done on BaseTen · 8m 41s · 41,200 reasoning · $0.012')).toBeInTheDocument();
  });

  it('ignores an event it has already seen (a reconnect replays from the last id)', async () => {
    renderRun();
    send(upTo(weekly, 'graphHalf'));
    await settled('Step 6 of 9');
    const lines = () => screen.getByRole('log').querySelectorAll('.ln').length;
    const before = lines();
    send(upTo(weekly, 'graphHalf'));
    expect(lines()).toBe(before);
  });

  it('keeps the same elements when the view is switched in the middle of the run', async () => {
    mockApi({ [PIPELINE_URL]: pipelineBase() });
    function Switcher() {
      const [view, setView] = useState<ViewChoice['view']>('drive');
      return (
        <>
          {(['drive', 'map', 'timeline'] as const).map((v) => (
            <button key={v} type="button" onClick={() => setView(v)}>
              show {v}
            </button>
          ))}
          <LiveRun season={2026} week={5} info={runInfo()} base={null} choice={choice(view)} onSettled={vi.fn()} />
        </>
      );
    }
    renderApp(<Switcher />);
    tick(130);
    send(upTo(weekly, 'graphHalf'));
    await settled('Step 6 of 9');
    expect(screen.getByRole('img', { name: /football drive/ })).toBeInTheDocument();
    const bar = screen.getByRole('progressbar');
    fireEvent.click(screen.getByRole('button', { name: 'show map' }));
    expect(screen.getByRole('img', { name: /Pipeline map/ })).toBeInTheDocument();
    expect(document.querySelector('[data-node="graph"]')).toHaveTextContent('loading 12/23 · node:Player');
    expect(screen.getByRole('progressbar')).toBe(bar); // the now-bar was not re-mounted
    fireEvent.click(screen.getByRole('button', { name: 'show timeline' }));
    expect(document.querySelector('[data-step="graph"]')).toHaveClass('running');
    expect(es.all()).toHaveLength(1); // and the stream was not reopened
  });

  it('says a run started from a terminal was started from a terminal', async () => {
    renderRun({ info: runInfo({ launched_from: 'terminal', run_id: null }) });
    expect(screen.getByText(/It was started from a terminal\./)).toBeInTheDocument();
  });

  it('draws a resume from the earlier sitting and counts nine steps', async () => {
    const run = resumeRun();
    renderRun({ info: runInfo({ kind: 'resume', run_id: run.events[0].run_id }), choice: choice('map') });
    tick(40);
    send(upTo(run, 'playerHalf'));
    expect(await settled('Step 7 of 9')).toBeInTheDocument();
    expect(screen.getByText(/A resume of week 5 is in progress/)).toBeInTheDocument();
    const graph = document.querySelector('[data-node="graph"]') as HTMLElement;
    expect(graph).toHaveTextContent('✓');
    expect(graph).toHaveTextContent('2m 30s'); // the earlier sitting's seconds
  });

  it('draws a rehearsal with its four steps, and the other five as not in a rehearsal', async () => {
    const run = rehearsalRun();
    renderRun({ info: runInfo({ kind: 'rehearsal', run_id: run.events[0].run_id }) });
    tick(50);
    send(upTo(run, 'playerHalf'));
    expect(await settled('Step 3 of 4')).toBeInTheDocument();
    expect(screen.getByText(/A rehearsal of week 5 is in progress\./)).toBeInTheDocument();
    expect(document.querySelector('[data-row="ingest"]')).toHaveTextContent('not in a rehearsal');
    expect(screen.getByRole('heading', { name: 'The rehearsal' })).toBeInTheDocument();
  });
});

describe('LiveRun at the end of a run', () => {
  it('shows Published, then refetches the week and hands the page back once its records catch up', async () => {
    const { api, onSettled } = renderRun();
    send(weekly.events);
    expect(await settled('Published')).toBeInTheDocument();
    expect(nowBar().getByText('exit 0 · records written')).toBeInTheDocument();
    expect(screen.getByText(/Week 5 is published\./)).toBeInTheDocument();
    // the run's records are only refetched once the server says the stream is over
    const calls = pipelineCalls(api);
    expect(onSettled).not.toHaveBeenCalled();
    runOver = true;
    act(() => es.last().emitEnd(runInfo({ finished: '2026-10-06T10:14:39-04:00', status: 'ok', exit_code: 0, published: true })));
    await waitFor(() => expect(pipelineCalls(api)).toBeGreaterThan(calls));
    await waitFor(() => expect(onSettled).toHaveBeenCalled());
    expect(es.last().closed).toBe(true);
  });

  it('gives up waiting for the records after 20 seconds', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-06T14:15:00Z'));
    // the first answer comes back; the refetch after the run never does
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        if (String(input) !== PIPELINE_URL) return Promise.resolve(new Response('{}', { status: 404 }));
        calls += 1;
        return calls === 1 ? Promise.resolve(new Response(JSON.stringify(pipelineBase()))) : new Promise<Response>(() => {});
      }),
    );
    const onSettled = vi.fn();
    renderApp(<LiveRun season={2026} week={5} info={runInfo()} base={null} choice={choice()} onSettled={onSettled} />);
    await waitFor(() => expect(calls).toBe(1));
    send(weekly.events);
    await settled('Published');
    act(() => es.last().emitEnd(runInfo({ finished: '2026-10-06T10:14:39-04:00', status: 'ok', published: true })));
    await waitFor(() => expect(calls).toBe(2));
    expect(onSettled).not.toHaveBeenCalled();
    vi.setSystemTime(new Date('2026-10-06T14:15:21Z'));
    tick(9999); // the clock moves (the page ticks every second)
    await waitFor(() => expect(onSettled).toHaveBeenCalled());
  });

  it('a failure: the banner names the step and why, the now-bar goes red, the failed step is a fumble', async () => {
    const run = failingRun();
    renderRun();
    send(run.events);
    expect(await settled('Failed · player')).toBeInTheDocument();
    expect(nowBar().getByText('exit 1 · records written')).toBeInTheDocument();
    expect(screen.getByText('The player step failed.')).toBeInTheDocument();
    expect(screen.getByText(/refit failed for sacks-edge: calibration seed missing\./)).toBeInTheDocument();
    expect(screen.getByText(/the digest didn.t run, so nothing was published/)).toBeInTheDocument();
    expect(screen.getByText('FUMBLE')).toBeInTheDocument();
    expect(screen.getByText('step failed → digest skipped; run records still written')).toHaveClass('warn');
    expect(screen.getByRole('log').querySelector('.cursor')).toBeNull(); // no cursor once it is over
  });

  it('a run that stopped before the digest says nothing was published', async () => {
    const t = tape()
      .start(0)
      .stepStart(0, 'ready')
      .stepEnd(2, 'ready', 2, 'week 4: 15/16 final', 'failed')
      .end(3, { status: 'not_ready', exit_code: 3, message: 'week 4 is not final yet', published: false, failed_step: 'ready' });
    renderRun();
    send(t.events);
    expect(await settled('Not ready')).toBeInTheDocument();
    expect(screen.getByText('The run stopped before the digest.')).toBeInTheDocument();
    expect(screen.queryByText(/The ready step failed\./)).toBeNull(); // not a failure: no resume offered
  });

  it('a rehearsal that stopped before the digest says so, without "published"', async () => {
    const t = tape('r4')
      .start(0, { kind: 'rehearsal', steps: ['ready', 'game', 'player', 'digest'] })
      .stepStart(0, 'ready')
      .stepEnd(2, 'ready', 2, 'week 4 is not final', 'failed')
      .end(3, { status: 'not_ready', exit_code: 3, message: 'week 4 is not final yet', published: false, failed_step: 'ready' });
    renderRun({ info: runInfo({ kind: 'rehearsal', run_id: 'r4' }) });
    send(t.events);
    expect(await settled('Not ready')).toBeInTheDocument();
    expect(screen.getByText('The rehearsal stopped before the digest.')).toBeInTheDocument();
    expect(screen.getByText(/Nothing live was touched\./)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing was published/)).toBeNull();
  });

  it('a finished rehearsal replayed from its events: its own card and log, no picker, nothing refetched', async () => {
    const run = rehearsalRun();
    const { api, onSettled } = renderRun({
      info: runInfo({ kind: 'rehearsal', run_id: run.events[0].run_id, finished: '2026-10-06T12:05:11-04:00', status: 'ok' }),
      replay: true,
    });
    const calls = pipelineCalls(api);
    send(run.events);
    act(() => es.last().emitEnd(runInfo({ kind: 'rehearsal', finished: '2026-10-06T12:05:11-04:00', status: 'ok' })));
    expect(await settled('Rehearsal done')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'The rehearsal' })).toBeInTheDocument();
    expect(screen.getByText(/replayed from its events/)).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Rehearsal log' })).toBeInTheDocument();
    expect(screen.getByText(/The rehearsal of week 5 finished\./)).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /Pipeline view/ })).toBeNull();
    expect(pipelineCalls(api)).toBe(calls);
    expect(onSettled).not.toHaveBeenCalled();
  });
});
