import { QueryClient, useQueryClient } from '@tanstack/react-query';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useEffect } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { invalidateAfterRun } from '../../api/client';
import type { PlaycallUpdateStep, PreflightResponse, RunCurrent, RunEvent, RunInfo } from '../../api/types';
import { installFakeEventSource } from '../../test/fakeEventSource';
import { META, WEEKS, mockApi, renderApp } from '../../test/utils';
import { PlayCallsTab } from '../../pages/week/PlayCallsTab';
import { PLAY_CALLS } from '../../pages/explore/playcallFixtures';
import { resetNotifyForTests } from './notify';
import { PlaycallUpdateCard } from './PlaycallUpdate';
import { STATE_WORD, failedStepOf, foldUpdate, playcallEndToast, updateFailed, updateResult } from './playcallUpdateModel';
import { RunUiProvider } from './RunUiProvider';
import { RunWatcher } from './RunWatcher';
import { runEndToast } from './runUi';

// ---- small synthetic fixtures (D132) ----------------------------------------------------------

const STEPS: PlaycallUpdateStep[] = [
  { step: 'ftn', label: 'Refresh FTN charting', due: true, reason: 'ATL at NO should have FTN by now', week: null },
  { step: 'build', label: 'Build the play-calling tables', due: true, reason: 'after the FTN refresh', week: null },
  { step: 'review', label: "Week 4's 4th-down decision review", due: false, reason: 'week 4 is already reviewed', week: 4 },
  { step: 'grade', label: "Grade week 4's forecast", due: true, reason: "12 of week 4's rows were still waiting", week: 4 },
  { step: 'forecast', label: 'Forecast week 5', due: false, reason: "week 5's forecast is current", week: 5 },
];
const COMMAND = 'nfl playcalling update --auto --expect-week 5';

function preflight(gate: Partial<PreflightResponse['playcalling']> = {}): PreflightResponse {
  return {
    checked_at: '2026-10-07T14:00:00+00:00',
    mode: 'live',
    season: 2026,
    week: 5,
    checks: [],
    run: { allowed: false, reason: 'Week 5 is already published.', command: null, retry_after: null },
    confirm: { last_week: 'week 4 · 16/16 final', deadline: null, hours_to_deadline: null, retry_until: null, steps: [], publishes: [] },
    resume: { allowed: false, reason: null, command: null, step: null },
    injury_update: { allowed: false, reason: 'Opens Saturday.', command: null, opens: null, closes: null, material_last: null },
    playcalling: { allowed: true, reason: null, command: COMMAND, steps: STEPS, ...gate },
    dry_run_log: [],
  };
}

function runInfo(over: Partial<RunInfo> = {}): RunInfo {
  return {
    run_id: '20261007T140100Z-pc01',
    kind: 'playcalling_update',
    launched_from: 'app',
    command: COMMAND,
    season: 2026,
    week: 5,
    started: '2026-10-07T14:01:00+00:00',
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

let seq = 0;
const base = (over: object) => ({ seq: ++seq, t: '2026-10-07T14:01:00+00:00', run_id: '20261007T140100Z-pc01', ...over }) as RunEvent;
const START = () =>
  base({
    type: 'run_start',
    kind: 'playcalling_update',
    command: COMMAND,
    season: 2026,
    week: 5,
    steps: ['ftn', 'build', 'grade'],
    from_step: null,
    earlier: {},
    launched_by: 'rishi',
    via: 'control-room',
    plan: STEPS.map((s) => `${s.step}: ${s.reason}`),
  });
const stepStart = (step: string) => base({ type: 'step_start', step });
const stepEnd = (step: string, status: string, detail: string) => base({ type: 'step_end', step, status, seconds: 12, detail });
const log = (text: string, cls: string | null = null) => base({ type: 'log', step: null, text, cls });

const IDLE: RunCurrent = { state: 'idle', run: null };
const apiBase = { '/api/meta': META, '/api/weeks': WEEKS, '/api/session': { token: 'test-launch' } };
const posts = (f: ReturnType<typeof mockApi>) =>
  f.mock.calls.filter(([p, init]) => String(p) === '/api/run' && init?.method === 'POST').map(([, init]) => JSON.parse(String(init?.body)) as unknown);

let client: QueryClient | null = null;
function GrabClient() {
  const qc = useQueryClient();
  useEffect(() => {
    client = qc;
  }, [qc]);
  return null;
}
const card = () => renderApp(
  <RunUiProvider>
    <GrabClient />
    <RunWatcher />
    <PlaycallUpdateCard season={2026} week={5} />
  </RunUiProvider>,
);
const cardEl = async () => (await screen.findByRole('heading', { name: 'Update play calling' })).closest('.card') as HTMLElement;
const stepItem = (root: HTMLElement, label: RegExp) => within(root).getByRole('listitem', { name: label });

beforeEach(() => {
  resetNotifyForTests();
  seq = 0;
});
afterEach(() => {
  vi.unstubAllGlobals();
  client = null;
});

describe('Update play calling: ready', () => {
  it('lists the five steps: the due ones "will run" with why, the others "not due" with why not', async () => {
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': IDLE });
    card();
    const c = await cardEl();
    expect(stepItem(c, /^Refresh FTN charting: will run/)).toHaveTextContent('ATL at NO should have FTN by now');
    expect(stepItem(c, /^Week 4's 4th-down decision review: not due/)).toHaveTextContent('week 4 is already reviewed');
    expect(stepItem(c, /^Grade week 4's forecast: will run/)).toBeInTheDocument();
    expect(stepItem(c, /^Forecast week 5: not due/)).toHaveClass('off');
    expect(within(c).getByText('3 steps to run')).toBeInTheDocument();
    expect(within(c).getByText(COMMAND)).toBeInTheDocument();
    expect(within(c).getByRole('button', { name: 'Update play calling' })).toBeEnabled();
  });

  it.each([
    ["Needs this week's Tuesday run first (Run week 5).", false],
    ['A run is in progress.', false],
    ['Everything is up to date.', false],
  ])('greys the button with the gate\'s reason: %s', async (reason) => {
    mockApi({ ...apiBase, '/api/preflight': preflight({ allowed: false, reason }), '/api/run/current': IDLE });
    card();
    const c = await cardEl();
    expect(within(c).getByRole('button', { name: 'Update play calling' })).toBeDisabled();
    expect(within(c).getByText(reason)).toBeInTheDocument();
  });

  it('shows no card when pre-flight is about another week', async () => {
    mockApi({ ...apiBase, '/api/preflight': { ...preflight(), week: 6 }, '/api/run/current': IDLE });
    const { container } = card();
    await waitFor(() => expect(client?.getQueryData(['preflight'])).toBeTruthy());
    expect(container.querySelector('.pcupd')).toBeNull();
  });

  it('a run of another kind: "another run is in progress", the button greyed', async () => {
    mockApi({
      ...apiBase,
      '/api/preflight': preflight({ allowed: false, reason: 'A run is in progress.' }),
      '/api/run/current': { state: 'running', run: runInfo({ kind: 'weekly', run_id: 'w1', command: 'nfl weekly run --auto --expect-week 5' }) },
    });
    card();
    const c = await cardEl();
    expect(await within(c).findByText('Another run is in progress (week 5)')).toBeInTheDocument();
    expect(within(c).getByRole('button', { name: 'Update play calling' })).toBeDisabled();
    expect(within(c).queryByText('Running')).toBeNull();
  });

  it('confirm lists what will run and what is skipped, then posts only the kind and the server\'s week', async () => {
    const f = mockApi({
      ...apiBase,
      '/api/preflight': preflight(),
      '/api/run/current': IDLE,
      'POST /api/run': { run_id: '20261007T140100Z-pc01', kind: 'playcalling_update', command: COMMAND, season: 2026, week: 5 },
    });
    card();
    const c = await cardEl();
    fireEvent.click(within(c).getByRole('button', { name: 'Update play calling' }));
    const dialog = screen.getByRole('dialog', { name: "Update week 5's play calling?" });
    expect(within(dialog).getAllByRole('listitem')).toHaveLength(3);
    expect(within(dialog).getByText("Week 4's 4th-down decision review · Forecast week 5")).toBeInTheDocument();
    expect(within(dialog).getByText(COMMAND)).toBeInTheDocument();
    expect(within(dialog).getByText(/holds the weekly lock/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update play calling' }));
    await waitFor(() => expect(posts(f)).toEqual([{ kind: 'playcalling_update', expect_week: 5 }]));
    expect(await screen.findByText('Week 5 play-calling update started')).toBeInTheDocument();
  });
});

describe('Update play calling: running and finished', () => {
  it('follows the run: steps pending → running → done from its events, the last log lines; then the end toast', async () => {
    const es = installFakeEventSource();
    let current: RunCurrent = { state: 'running', run: runInfo() };
    mockApi({ ...apiBase, '/api/preflight': preflight({ allowed: false, reason: 'A run is in progress.' }), '/api/run/current': () => current });
    card();
    const c = await cardEl();
    expect(await within(c).findByText('Running')).toBeInTheDocument();
    expect(within(c).queryByRole('button', { name: 'Update play calling' })).toBeNull();
    await waitFor(() => expect(es.all().length).toBeGreaterThan(0));
    act(() => es.last().emitRuns([START(), stepStart('ftn'), log('nflverse ftn_charting: 1 new snapshot'), stepEnd('ftn', 'ok', 'FTN: ATL at NO charted'), stepStart('build')]));
    await waitFor(() => expect(stepItem(c, /^Refresh FTN charting: done/)).toHaveTextContent('FTN: ATL at NO charted'));
    expect(stepItem(c, /^Build the play-calling tables: running/)).toBeInTheDocument();
    expect(stepItem(c, /^Grade week 4's forecast: waiting/)).toBeInTheDocument();
    expect(stepItem(c, /^Week 4's 4th-down decision review: not due/)).toHaveClass('off');
    expect(within(c).getByRole('log', { name: "The update's last lines" })).toHaveTextContent('nflverse ftn_charting: 1 new snapshot');

    act(() => es.last().emitRuns([stepEnd('build', 'ok', 'tables as of week 5'), stepStart('grade'), stepEnd('grade', 'ok', '352 of 352 graded'), base({ type: 'run_end', status: 'ok', exit_code: 0, message: 'done: ftn, build, grade', published: false, failed_step: null, material: null })]));
    current = { state: 'finished', run: runInfo({ finished: '2026-10-07T14:02:30+00:00', status: 'ok', exit_code: 0, message: 'done: ftn, build, grade' }) };
    await act(async () => {
      await client?.invalidateQueries({ queryKey: ['run-current'] });
    });
    expect(await within(c).findByText('Play calling updated.')).toBeInTheDocument();
    await waitFor(() => expect(stepItem(c, /^Grade week 4's forecast: done/)).toHaveTextContent('352 of 352 graded'));
    expect(await screen.findByRole('status', { name: 'Notifications' })).toHaveTextContent('Play calling updated');
    fireEvent.click(within(c).getByRole('button', { name: 'Hide' }));
    expect(within(c).queryByText('Play calling updated.')).toBeNull();
  });

  it('a failed update: the failed step with its detail, the message, and the button again', async () => {
    const es = installFakeEventSource();
    const failedRun = runInfo({
      finished: '2026-10-07T14:02:30+00:00',
      status: 'failed',
      exit_code: 1,
      message: 'done: ftn, build; failed: grade',
    });
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': { state: 'running', run: runInfo() } satisfies RunCurrent });
    card();
    const c = await cardEl();
    await waitFor(() => expect(es.all().length).toBeGreaterThan(0));
    act(() =>
      es.last().emitRuns([
        START(),
        stepStart('ftn'),
        stepEnd('ftn', 'ok', 'FTN in'),
        stepStart('build'),
        stepEnd('build', 'ok', 'built'),
        stepStart('grade'),
        stepEnd('grade', 'failed', 'scoreboard.parquet is open in another program'),
        base({ type: 'run_end', status: 'failed', exit_code: 1, message: 'done: ftn, build; failed: grade', published: false, failed_step: null, material: null }),
      ]),
    );
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': { state: 'finished', run: failedRun } satisfies RunCurrent });
    await act(async () => {
      await client?.invalidateQueries({ queryKey: ['run-current'] });
    });
    expect(await within(c).findByText('The play-calling update failed at grade.')).toBeInTheDocument();
    await waitFor(() => expect(stepItem(c, /^Grade week 4's forecast: failed/)).toHaveTextContent('scoreboard.parquet is open in another program'));
    expect(within(c).getByRole('button', { name: 'Update play calling' })).toBeEnabled();
    expect(await screen.findByRole('status', { name: 'Notifications' })).toHaveTextContent('The play-calling update failed at grade');
  });

  it("doesn't show another week's update as this week's", async () => {
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': { state: 'running', run: runInfo({ week: 4 }) } satisfies RunCurrent });
    card();
    const c = await cardEl();
    expect(await within(c).findByText('Another play-calling update is running (week 4)')).toBeInTheDocument();
    expect(within(c).queryByText('Running')).toBeNull();
  });
});

describe('Update play calling: on the Play calls tab', () => {
  it('shows only on the current week, above the tab', async () => {
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': IDLE, '/api/weeks/2026/5/play-calls': PLAY_CALLS });
    const { unmount } = renderApp(
      <RunUiProvider>
        <PlayCallsTab season={2026} week={5} isCurrent />
      </RunUiProvider>,
    );
    expect(await screen.findByRole('heading', { name: 'Update play calling' })).toBeInTheDocument();
    unmount();
    renderApp(
      <RunUiProvider>
        <GrabClient />
        <PlayCallsTab season={2026} week={5} isCurrent={false} />
      </RunUiProvider>,
    );
    expect(await screen.findByRole('region', { name: 'KC offense against JAX defense' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Update play calling' })).toBeNull();
  });
});

describe('Update play calling: the model and the toast', () => {
  it("invalidateAfterRun refreshes the Explore play-calling pages too (['playcalling'])", () => {
    const qc = new QueryClient();
    qc.setQueryData(['playcalling', 'teams', 'newest'], { ok: true });
    qc.setQueryData(['week', 2026, 5, 'play-calls'], { ok: true });
    invalidateAfterRun(qc);
    expect(qc.getQueryState(['playcalling', 'teams', 'newest'])?.isInvalidated).toBe(true);
    expect(qc.getQueryState(['week', 2026, 5, 'play-calls'])?.isInvalidated).toBe(true);
  });

  it('folds the events onto the plan (before a run: will / off)', () => {
    expect(foldUpdate(STEPS, []).steps.map((s) => s.state)).toEqual(['will', 'will', 'off', 'will', 'off']);
    const f = foldUpdate(STEPS, [START(), stepStart('ftn'), stepEnd('ftn', 'skipped', 'nothing new')]);
    // skipped = ended without running (an earlier step failed); off = not in this run
    expect(f.steps.map((s) => s.state)).toEqual(['skipped', 'pending', 'off', 'pending', 'off']);
    expect(STATE_WORD.skipped).toBe('skipped');
    expect(f.steps[0].detail).toBe('nothing new');
    expect(f.end).toBeNull();
  });

  it('names the failed step from failed_step or the message', () => {
    expect(failedStepOf({ failed_step: null, message: 'done: ftn; failed: build' })).toBe('build');
    expect(failedStepOf({ failed_step: 'grade', message: null })).toBe('grade');
    expect(failedStepOf({ failed_step: null, message: 'done: ftn, build' })).toBeNull();
  });

  it('toasts the end: updated, failed at <step>, locked', () => {
    expect(runEndToast(runInfo({ status: 'ok', exit_code: 0, message: 'done: build' }))).toEqual({ title: 'Play calling updated', text: 'done: build', tone: 'ok' });
    expect(playcallEndToast(runInfo({ status: 'failed', exit_code: 1, message: 'done: ftn; failed: build' }))).toMatchObject({
      title: 'The play-calling update failed at build',
      tone: 'err',
    });
    expect(playcallEndToast(runInfo({ status: 'interrupted', exit_code: null }))).toMatchObject({ title: 'The play-calling update failed', tone: 'err' });
    expect(playcallEndToast(runInfo({ status: 'locked', exit_code: 4 }))).toMatchObject({ tone: 'warn' });
  });

  it('only a confirmed end reads as success: refusals, a missing drive and an unseen end never do (Sol review)', () => {
    const outcome = (status: string | null, exit_code: number | null) => updateResult(runInfo({ status, exit_code }));
    expect(outcome('ok', 0)).toEqual({ tone: 'ok', title: 'Play calling updated' });
    expect(outcome('idle', 0)).toEqual({ tone: 'ok', title: 'Nothing was due: nothing ran' });
    expect(outcome('locked', 4)).toEqual({ tone: 'warn', title: 'Another run holds the lock: nothing ran' });
    expect(outcome('week_mismatch', 6)).toEqual({ tone: 'warn', title: "The calendar's week changed: nothing ran" });
    expect(outcome('no_data_root', 5)).toEqual({ tone: 'err', title: "The data drive isn't there: nothing ran" });
    expect(outcome('usage', 2)).toEqual({ tone: 'err', title: 'The update command was refused: nothing ran' });
    expect(outcome('failed', 1)).toEqual({ tone: 'err', title: 'The play-calling update failed' });
    expect(outcome('unknown', null)).toEqual({ tone: 'warn', title: 'The play-calling update ended without a result' });
    for (const [status, code] of [['locked', 4], ['week_mismatch', 6], ['no_data_root', 5], ['usage', 2], ['unknown', null]] as const) {
      expect(updateFailed(runInfo({ status, exit_code: code }))).toBe(true);
      expect(playcallEndToast(runInfo({ status, exit_code: code })).title).not.toBe('Play calling updated');
    }
    expect(updateFailed(runInfo({ status: 'ok', exit_code: 0 }))).toBe(false);
  });

  it('a refused run shows its reason on the card, never "Play calling updated"', async () => {
    const refused = runInfo({ finished: new Date().toISOString(), status: 'locked', exit_code: 4, has_events: false });
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': { state: 'finished', run: refused } satisfies RunCurrent });
    card();
    const c = await cardEl();
    expect(await within(c).findByText('Another run holds the lock: nothing ran.')).toBeInTheDocument();
    expect(within(c).queryByText(/Play calling updated/)).toBeNull();
    expect(within(c).queryByText(/below are refreshed/)).toBeNull();
  });

  it('a missing drive is an error on the card', async () => {
    const gone = runInfo({ finished: new Date().toISOString(), status: 'no_data_root', exit_code: 5, has_events: false, output_tail: ['NFL_DATA_ROOT is not set'] });
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': { state: 'finished', run: gone } satisfies RunCurrent });
    card();
    const c = await cardEl();
    expect(await within(c).findByText("The data drive isn't there: nothing ran.")).toBeInTheDocument();
    expect(within(c).getByText(/press Update play calling again/)).toBeInTheDocument();
    expect(within(c).getByText('NFL_DATA_ROOT is not set')).toBeInTheDocument();
  });

  it("the confirm says a failed FTN refresh or build skips what needs it, any other failure doesn't", async () => {
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': IDLE });
    card();
    const c = await cardEl();
    fireEvent.click(await within(c).findByRole('button', { name: 'Update play calling' }));
    const dlg = await screen.findByRole('dialog');
    expect(dlg).toHaveTextContent('A failed FTN refresh or build skips the steps that need it; any other failure lets the rest run');
    expect(dlg).not.toHaveTextContent('stops at the first failure');
  });
});
