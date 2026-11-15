import { QueryClient, useQueryClient } from '@tanstack/react-query';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useEffect } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { invalidateAfterRun } from '../../api/client';
import type { ForecastLabGate, PreflightResponse, RunCurrent, RunEvent, RunInfo } from '../../api/types';
import { LAB_COMMAND, LAB_STEPS, LAB_UP_TO_DATE, labGate } from '../../pages/week/forecastlab/labFixtures';
import { installFakeEventSource } from '../../test/fakeEventSource';
import { META, WEEKS, mockApi, renderApp } from '../../test/utils';
import { ForecastLabUpdateCard } from './ForecastLabUpdate';
import { labChainToast, labEndToast, labResult, starterOf } from './forecastLabRunModel';
import { InjuryUpdateButton } from './InjuryUpdate';
import { resetNotifyForTests } from './notify';
import { RunConfirm } from './RunConfirm';
import { RunUiProvider } from './RunUiProvider';
import { RunWatcher } from './RunWatcher';
import { runEndToast } from './runUi';

// ---- small synthetic fixtures (D158) ----------------------------------------------------------

function preflight(gate: ForecastLabGate = labGate(), over: Partial<PreflightResponse> = {}): PreflightResponse {
  return {
    checked_at: '2026-10-07T14:00:00+00:00',
    mode: 'live',
    season: 2026,
    week: 5,
    checks: [],
    run: { allowed: false, reason: 'Week 5 is already published.', command: 'nfl weekly run --auto --expect-week 5', retry_after: null },
    confirm: { last_week: 'week 4 · 16/16 final', deadline: null, hours_to_deadline: null, retry_until: null, steps: [], publishes: [] },
    resume: { allowed: false, reason: null, command: null, step: null },
    injury_update: {
      allowed: true,
      reason: null,
      command: 'nfl weekly injury-update --auto',
      opens: null,
      closes: '2026-10-12T00:15:00+00:00',
      material_last: null,
    },
    playcalling: { allowed: false, reason: 'Everything is up to date.', command: null, steps: [] },
    forecastlab: gate,
    chain: true,
    dry_run_log: [],
    ...over,
  };
}

const RUN_ID = '20261007T140100Z-fl01';
const WEEKLY_ID = '20261006T140012Z-a1b2';

function runInfo(over: Partial<RunInfo> = {}): RunInfo {
  return {
    run_id: RUN_ID,
    kind: 'forecastlab_update',
    launched_from: 'app',
    command: LAB_COMMAND,
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
    follows: null,
    ...over,
  };
}

let seq = 0;
const base = (over: object) => ({ seq: ++seq, t: '2026-10-07T14:01:00+00:00', run_id: RUN_ID, ...over }) as RunEvent;
const START = () =>
  base({
    type: 'run_start',
    kind: 'forecastlab_update',
    command: LAB_COMMAND,
    season: 2026,
    week: 5,
    steps: ['availability', 'curves'],
    from_step: null,
    earlier: {},
    launched_by: 'rishi',
    via: 'control-room',
    plan: [],
  });
const stepStart = (step: string) => base({ type: 'step_start', step });
const stepEnd = (step: string, status: string, detail: string) => base({ type: 'step_end', step, status, seconds: 12, detail });
const log = (text: string) => base({ type: 'log', step: null, text, cls: null });

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
const card = () =>
  renderApp(
    <RunUiProvider>
      <GrabClient />
      <RunWatcher />
      <ForecastLabUpdateCard season={2026} week={5} />
    </RunUiProvider>,
  );
const LABEL = 'Update availability and outcomes';
const cardEl = async () => (await screen.findByRole('heading', { name: LABEL })).closest('.card') as HTMLElement;
const stepItem = (root: HTMLElement, label: RegExp) => within(root).getByRole('listitem', { name: label });
const refetchCurrent = async () => {
  await act(async () => {
    await client?.invalidateQueries({ queryKey: ['run-current'] });
  });
};

beforeEach(() => {
  resetNotifyForTests();
  seq = 0;
});
afterEach(() => {
  vi.unstubAllGlobals();
  client = null;
});

describe('Forecast lab update: ready', () => {
  it('lists the three steps with why, the button and the command', async () => {
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': IDLE });
    card();
    const c = await cardEl();
    expect(stepItem(c, /^Availability for week 5 \(Tuesday view\): will run/)).toHaveTextContent('no Tuesday view yet');
    expect(stepItem(c, /^Outcome curves for week 5: will run/)).toBeInTheDocument();
    expect(stepItem(c, /^Grade week 4: not due/)).toHaveTextContent('week 4 has nothing to grade');
    expect(within(c).getByText('2 steps to run')).toBeInTheDocument();
    expect(within(c).getByText(LAB_COMMAND)).toBeInTheDocument();
    expect(within(c).getByText(/runs these by itself after Run week 5 and the injury update/)).toBeInTheDocument();
    expect(within(c).getByRole('button', { name: LABEL })).toBeEnabled();
  });

  it('nothing due: one quiet line, "Everything is up to date", no button; the checks behind a disclosure', async () => {
    mockApi({ ...apiBase, '/api/preflight': preflight(LAB_UP_TO_DATE), '/api/run/current': IDLE });
    card();
    const line = await screen.findByRole('status', { name: 'Availability and outcomes update' });
    expect(line).toHaveTextContent('Everything is up to date.');
    expect(screen.queryByRole('heading', { name: LABEL })).toBeNull();
    expect(screen.queryByRole('button', { name: LABEL })).toBeNull();
    fireEvent.click(within(line).getByText('What it checked'));
    expect(within(line).getByRole('listitem', { name: /^Grade week 4: not due/ })).toBeInTheDocument();
  });

  it('nothing due because nothing is promoted: the quiet line says so, without the tick', async () => {
    const reason = 'Nothing to do until the models are promoted.';
    mockApi({ ...apiBase, '/api/preflight': preflight({ ...LAB_UP_TO_DATE, reason }), '/api/run/current': IDLE });
    card();
    const line = await screen.findByRole('status', { name: 'Availability and outcomes update' });
    expect(line).toHaveTextContent(reason);
    expect(line).not.toHaveTextContent('✓');
    expect(screen.queryByRole('button', { name: LABEL })).toBeNull();
  });

  it.each([["Needs this week's Tuesday run first (Run week 5)."], ['A run is in progress.']])("greys the button with the gate's reason: %s", async (reason) => {
    mockApi({ ...apiBase, '/api/preflight': preflight(labGate({ allowed: false, reason })), '/api/run/current': IDLE });
    card();
    const c = await cardEl();
    expect(within(c).getByRole('button', { name: LABEL })).toBeDisabled();
    expect(within(c).getByText(reason)).toBeInTheDocument();
  });

  it('shows no card when pre-flight is about another week', async () => {
    mockApi({ ...apiBase, '/api/preflight': { ...preflight(), week: 6 }, '/api/run/current': IDLE });
    const { container } = card();
    await waitFor(() => expect(client?.getQueryData(['preflight'])).toBeTruthy());
    expect(container.querySelector('.flupd')).toBeNull();
    expect(container.querySelector('.flquiet')).toBeNull();
  });

  it("another run going: says which, the button greyed, nothing of this card's own running", async () => {
    mockApi({
      ...apiBase,
      '/api/preflight': preflight(labGate({ allowed: false, reason: 'A run is in progress.' })),
      '/api/run/current': { state: 'running', run: runInfo({ kind: 'injury_update', run_id: 'inj1', command: 'nfl weekly injury-update --auto' }) },
    });
    card();
    const c = await cardEl();
    expect(await within(c).findByText('The week 5 injury update is running: this refreshes after it')).toBeInTheDocument();
    expect(within(c).getByRole('button', { name: LABEL })).toBeDisabled();
    expect(within(c).queryByText('Running')).toBeNull();
  });

  it("confirm lists what will run and what's skipped, then posts only the kind and the server's week", async () => {
    const f = mockApi({
      ...apiBase,
      '/api/preflight': preflight(),
      '/api/run/current': IDLE,
      'POST /api/run': { run_id: RUN_ID, kind: 'forecastlab_update', command: LAB_COMMAND, season: 2026, week: 5 },
    });
    card();
    const c = await cardEl();
    fireEvent.click(within(c).getByRole('button', { name: LABEL }));
    const dialog = screen.getByRole('dialog', { name: "Update week 5's availability and outcomes?" });
    expect(within(dialog).getAllByRole('listitem')).toHaveLength(2);
    expect(within(dialog).getByText('Grade week 4')).toBeInTheDocument();
    expect(within(dialog).getByText(LAB_COMMAND)).toBeInTheDocument();
    expect(dialog).toHaveTextContent('never touches the published week');
    fireEvent.click(within(dialog).getByRole('button', { name: LABEL }));
    await waitFor(() => expect(posts(f)).toEqual([{ kind: 'forecastlab_update', expect_week: 5 }]));
    expect(await screen.findByText('Week 5: availability and outcomes update started')).toBeInTheDocument();
  });
});

describe('Forecast lab update: running and finished', () => {
  it('a chained run: says the app started it, follows its steps and lines, then toasts the end', async () => {
    const es = installFakeEventSource();
    let current: RunCurrent = { state: 'running', run: runInfo({ follows: WEEKLY_ID }) };
    mockApi({ ...apiBase, '/api/preflight': preflight(labGate({ allowed: false, reason: 'A run is in progress.' })), '/api/run/current': () => current });
    card();
    const c = await cardEl();
    expect(await within(c).findByText('Running')).toBeInTheDocument();
    expect(within(c).getByText(/Started by the app after the week's run ended/)).toBeInTheDocument();
    expect(within(c).queryByRole('button', { name: LABEL })).toBeNull();
    await waitFor(() => expect(es.all().length).toBeGreaterThan(0));
    act(() => es.last().emitRuns([START(), stepStart('availability'), log('availability: tuesday view'), stepEnd('availability', 'ok', 'tuesday view: 1400 players'), stepStart('curves')]));
    await waitFor(() => expect(stepItem(c, /^Availability for week 5 \(Tuesday view\): done/)).toHaveTextContent('tuesday view: 1400 players'));
    expect(stepItem(c, /^Outcome curves for week 5: running/)).toBeInTheDocument();
    expect(stepItem(c, /^Grade week 4: not due/)).toHaveClass('off');
    expect(within(c).getByRole('log', { name: "The update's last lines" })).toHaveTextContent('availability: tuesday view');

    act(() =>
      es.last().emitRuns([
        stepEnd('curves', 'ok', '2310 curves'),
        base({ type: 'run_end', status: 'ok', exit_code: 0, message: 'done: availability, curves', published: false, failed_step: null, material: null }),
      ]),
    );
    current = { state: 'finished', run: runInfo({ follows: WEEKLY_ID, finished: '2026-10-07T14:03:00+00:00', status: 'ok', exit_code: 0, message: 'done: availability, curves' }) };
    await refetchCurrent();
    expect(await within(c).findByText('Availability and outcomes updated.')).toBeInTheDocument();
    expect(c).toHaveTextContent('The tab below is refreshed.');
    expect(await screen.findByRole('status', { name: 'Notifications' })).toHaveTextContent('Availability and outcomes updated');
    fireEvent.click(within(c).getByRole('button', { name: 'Hide' }));
    expect(within(c).queryByText('Availability and outcomes updated.')).toBeNull();
  });

  it('a press from this page says so; one from elsewhere says "the Update button"', async () => {
    let current: RunCurrent = IDLE;
    mockApi({
      ...apiBase,
      '/api/preflight': preflight(),
      '/api/run/current': () => current,
      'POST /api/run': { run_id: RUN_ID, kind: 'forecastlab_update', command: LAB_COMMAND, season: 2026, week: 5 },
    });
    installFakeEventSource();
    card();
    const c = await cardEl();
    fireEvent.click(within(c).getByRole('button', { name: LABEL }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: LABEL }));
    await screen.findByText('Week 5: availability and outcomes update started');
    current = { state: 'running', run: runInfo() };
    await refetchCurrent();
    expect(await within(c).findByText(/You pressed Update/)).toBeInTheDocument();
    current = { state: 'running', run: runInfo({ run_id: 'other-tab' }) };
    await refetchCurrent();
    expect(await within(c).findByText(/Started with the Update button/)).toBeInTheDocument();
  });

  it('a failed step: the step with its detail, the published week untouched, the button again', async () => {
    const es = installFakeEventSource();
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': { state: 'running', run: runInfo() } satisfies RunCurrent });
    card();
    const c = await cardEl();
    await waitFor(() => expect(es.all().length).toBeGreaterThan(0));
    act(() =>
      es.last().emitRuns([
        START(),
        stepStart('availability'),
        stepEnd('availability', 'failed', 'availability.parquet is open in another program'),
        stepStart('curves'),
        stepEnd('curves', 'ok', '2310 curves'),
        base({ type: 'run_end', status: 'failed', exit_code: 1, message: 'done: curves; failed: availability', published: false, failed_step: null, material: null }),
      ]),
    );
    mockApi({
      ...apiBase,
      '/api/preflight': preflight(),
      '/api/run/current': {
        state: 'finished',
        run: runInfo({ finished: '2026-10-07T14:02:30+00:00', status: 'failed', exit_code: 1, message: 'done: curves; failed: availability' }),
      } satisfies RunCurrent,
    });
    await refetchCurrent();
    expect(await within(c).findByText('The availability and outcomes update failed at availability.')).toBeInTheDocument();
    expect(c).toHaveTextContent("The published week isn't touched.");
    await waitFor(() => expect(stepItem(c, /^Availability for week 5 \(Tuesday view\): failed/)).toHaveTextContent('open in another program'));
    expect(stepItem(c, /^Outcome curves for week 5: done/)).toBeInTheDocument();
    expect(within(c).getByRole('button', { name: LABEL })).toBeEnabled();
    expect(await screen.findByRole('status', { name: 'Notifications' })).toHaveTextContent('The availability and outcomes update failed at availability');
  });

  it("doesn't show another week's update as this week's", async () => {
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': { state: 'running', run: runInfo({ week: 4 }) } satisfies RunCurrent });
    card();
    const c = await cardEl();
    expect(await within(c).findByText('Another availability and outcomes update is running (week 4)')).toBeInTheDocument();
    expect(within(c).queryByText('Running')).toBeNull();
  });

  it('a refused run shows its reason, never "updated"', async () => {
    const refused = runInfo({ finished: new Date().toISOString(), status: 'locked', exit_code: 4, has_events: false });
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': { state: 'finished', run: refused } satisfies RunCurrent });
    card();
    const c = await cardEl();
    expect(await within(c).findByText('Another run holds the lock: nothing ran.')).toBeInTheDocument();
    expect(within(c).queryByText(/outcomes updated/)).toBeNull();
  });
});

describe('Forecast lab update: the chain is visible', () => {
  it('the weekly run this page followed ends, the app starts the update: one "refreshing" toast, then the end toast', async () => {
    let current: RunCurrent = { state: 'running', run: runInfo({ kind: 'weekly', run_id: WEEKLY_ID, command: 'nfl weekly run --auto --expect-week 5' }) };
    mockApi({ ...apiBase, '/api/preflight': preflight(LAB_UP_TO_DATE), '/api/run/current': () => current });
    installFakeEventSource();
    card();
    await waitFor(() => expect(client?.getQueryData(['run-current'])).toBeTruthy());
    current = { state: 'running', run: runInfo({ follows: WEEKLY_ID }) };
    await refetchCurrent();
    const toasts = await screen.findByRole('status', { name: 'Notifications' });
    await waitFor(() => expect(toasts).toHaveTextContent('Week 5: refreshing availability and outcomes'));
    await refetchCurrent(); // seen again: announced once only
    expect(toasts.textContent?.match(/refreshing availability and outcomes/g)).toHaveLength(1);
  });

  it("a chained run after a run this page didn't follow (opened mid-way) isn't announced at its start", async () => {
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': { state: 'running', run: runInfo({ follows: WEEKLY_ID }) } satisfies RunCurrent });
    installFakeEventSource();
    card();
    await cardEl();
    await waitFor(() => expect(client?.getQueryData(['run-current'])).toBeTruthy());
    expect(screen.queryByText(/refreshing availability and outcomes/)).toBeNull();
  });

  it('Run week N\'s confirm says the curves refresh afterwards (only when the chain is on, never in rehearsal)', () => {
    const { unmount } = renderApp(<RunConfirm pf={preflight()} open onOpenChange={() => {}} onConfirm={() => {}} />);
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveTextContent('Once the week is published, the app refreshes availability and outcome curves');
    expect(dlg).toHaveTextContent('A failure there never touches the published week.');
    unmount();
    const off = renderApp(<RunConfirm pf={preflight(labGate(), { chain: false })} open onOpenChange={() => {}} onConfirm={() => {}} />);
    expect(screen.getByRole('dialog')).not.toHaveTextContent('refreshes availability');
    off.unmount();
    renderApp(<RunConfirm pf={preflight(labGate(), { mode: 'rehearsal' })} open onOpenChange={() => {}} onConfirm={() => {}} />);
    expect(screen.getByRole('dialog')).not.toHaveTextContent('refreshes availability');
  });

  it("the injury update's confirm says the Saturday view refreshes afterwards", async () => {
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': IDLE });
    renderApp(
      <RunUiProvider>
        <InjuryUpdateButton season={2026} week={5} fallbackReason="—" />
      </RunUiProvider>,
    );
    const btn = await screen.findByRole('button', { name: 'Saturday injury update' });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    const dlg = await screen.findByRole('dialog');
    expect(dlg).toHaveTextContent('Afterwards the app makes the Saturday availability view');
    expect(dlg).toHaveTextContent('A failure there never touches the published week.');
  });

  it('the injury button names the running Forecast lab update', async () => {
    mockApi({ ...apiBase, '/api/preflight': preflight(), '/api/run/current': { state: 'running', run: runInfo({ follows: WEEKLY_ID }) } satisfies RunCurrent });
    renderApp(
      <RunUiProvider>
        <InjuryUpdateButton season={2026} week={5} fallbackReason="—" />
      </RunUiProvider>,
    );
    expect(await screen.findByText('Availability and outcomes are updating: open again when that ends')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Saturday injury update' })).toBeDisabled();
  });
});

describe('Forecast lab update: the model and the toasts', () => {
  it('who started it', () => {
    const none = new Set<string>();
    expect(starterOf(runInfo({ follows: WEEKLY_ID }), none)).toBe('chain');
    expect(starterOf(runInfo(), new Set([RUN_ID]))).toBe('here');
    expect(starterOf(runInfo(), none)).toBe('button');
    expect(starterOf(runInfo({ launched_from: 'terminal' }), none)).toBe('terminal');
  });

  it('only a confirmed end reads as success (the D132 rule)', () => {
    const outcome = (status: string | null, exit_code: number | null) => labResult(runInfo({ status, exit_code }));
    expect(outcome('ok', 0)).toEqual({ tone: 'ok', title: 'Availability and outcomes updated' });
    expect(outcome('idle', 0)).toEqual({ tone: 'ok', title: 'Nothing was due: nothing ran' });
    expect(outcome('locked', 4).tone).toBe('warn');
    expect(outcome('week_mismatch', 6).tone).toBe('warn');
    expect(outcome('no_data_root', 5).tone).toBe('err');
    expect(outcome('usage', 2).tone).toBe('err');
    expect(outcome('unknown', null).tone).toBe('warn');
    expect(labResult(runInfo({ status: 'failed', exit_code: 1, message: 'done: availability; failed: grade' })).title).toBe(
      "The availability and outcomes update failed at last week's grading",
    );
  });

  it("runEndToast never calls the update 'Week N published' (even when the week is published)", () => {
    const t = runEndToast(runInfo({ status: 'ok', exit_code: 0, published: true, message: 'done: curves', finished: '2026-10-07T14:03:00+00:00' }));
    expect(t).toEqual({ title: 'Availability and outcomes updated', text: 'done: curves', tone: 'ok' });
    expect(labEndToast(runInfo({ status: 'failed', exit_code: 1, message: 'failed: curves' }))).toMatchObject({
      tone: 'err',
      text: "failed: curves The published week isn't touched.",
    });
    expect(labChainToast(runInfo({ follows: WEEKLY_ID })).title).toBe('Week 5: refreshing availability and outcomes');
  });

  it('the tabs refetch when an update ends (every week query)', () => {
    const qc = new QueryClient();
    qc.setQueryData(['week', 2026, 5, 'availability', ''], { ok: true });
    qc.setQueryData(['week', 2026, 5, 'outcomes', ''], { ok: true });
    invalidateAfterRun(qc);
    expect(qc.getQueryState(['week', 2026, 5, 'availability', ''])?.isInvalidated).toBe(true);
    expect(qc.getQueryState(['week', 2026, 5, 'outcomes', ''])?.isInvalidated).toBe(true);
  });

  it('the fixtures use every step kind', () => {
    expect(LAB_STEPS.map((s) => s.step)).toEqual(['availability', 'curves', 'grade']);
  });
});
