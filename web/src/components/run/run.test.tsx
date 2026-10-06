import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useEffect, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PreflightResponse, RunCurrent, RunEvent, RunInfo } from '../../api/types';
import { installFakeEventSource } from '../../test/fakeEventSource';
import { META, WEEKS, mockApi, renderApp, withStatus } from '../../test/utils';
import { Shell } from '../Shell';
import { WeekHeader } from '../WeekHeader';
import { FailureBanner } from './FailureBanner';
import { InjuryUpdateLog } from './InjuryUpdate';
import { resetNotifyForTests } from './notify';
import { PreflightPanel } from './PreflightPanel';
import { RunUiProvider } from './RunUiProvider';
import { RunWatcher } from './RunWatcher';
import { runEndToast } from './runUi';

// ---- small synthetic fixtures ---------------------------------------------------------------

function preflight(over: Partial<PreflightResponse> = {}): PreflightResponse {
  return {
    checked_at: '2026-10-06T14:00:00+00:00',
    mode: 'live',
    season: 2026,
    week: 5,
    checks: [
      {
        id: 'calendar',
        label: 'Calendar',
        status: 'ok',
        value: '2026 week 5 · 15 games',
        reason: null,
        blocking: true,
      },
      {
        id: 'last_week_final',
        label: 'Week 4 is final',
        status: 'ok',
        value: '16/16 final',
        reason: null,
        blocking: true,
      },
      {
        id: 'neo4j',
        label: 'Neo4j',
        status: 'warn',
        value: 'down',
        reason: 'the run starts it',
        blocking: false,
      },
    ],
    run: {
      allowed: true,
      reason: null,
      command: 'nfl weekly run --auto --expect-week 5',
      retry_after: null,
    },
    confirm: {
      last_week: 'week 4 · 16/16 final',
      deadline: '2026-10-09T00:15:00+00:00',
      hours_to_deadline: 58.25,
      retry_until: '2026-10-07T22:00:00+00:00',
      steps: ['ingest', 'ready', 'curate', 'ratings', 'game', 'graph', 'player', 'digest'],
      publishes: ['week05-digest.md', 'production alias'],
    },
    resume: { allowed: false, reason: null, command: null, step: null },
    injury_update: {
      allowed: false,
      reason: 'Opens Sat Oct 10',
      command: 'nfl weekly injury-update --auto',
      opens: '2026-10-10T04:00:00+00:00',
      closes: '2026-10-13T00:15:00+00:00',
      material_last: null,
    },
    dry_run_log: [],
    ...over,
  };
}

const BLOCKED = preflight({
  checks: [
    {
      id: 'last_week_final',
      label: 'Week 4 is final',
      status: 'fail',
      value: '15/16 final',
      reason: 'Falcons at Saints not in play-by-play yet',
      blocking: true,
    },
  ],
  run: {
    allowed: false,
    reason: "Week 4 isn't final in the data yet. Try again after 6:00 AM ET.",
    command: 'nfl weekly run --auto --expect-week 5',
    retry_after: null,
  },
});

function runInfo(over: Partial<RunInfo> = {}): RunInfo {
  return {
    run_id: 'r1',
    kind: 'weekly',
    launched_from: 'app',
    command: 'nfl weekly run --auto --expect-week 5',
    season: 2026,
    week: 5,
    material: null,
    started: '2026-10-06T14:00:12+00:00',
    finished: null,
    status: null,
    exit_code: null,
    message: null,
    published: null,
    failed_step: null,
    has_events: true,
    output_tail: [],
    ...over,
  };
}
const IDLE: RunCurrent = { state: 'idle', run: null };

const base = { '/api/meta': META, '/api/weeks': WEEKS, '/api/session': { token: 'test-launch' } };

function withUi(ui: ReactNode) {
  return <RunUiProvider>{ui}</RunUiProvider>;
}

const toasts = () => screen.getByRole('status', { name: 'Notifications' });
const preflightCalls = (f: ReturnType<typeof mockApi>) =>
  f.mock.calls.filter(([p, init]) => String(p) === '/api/preflight' && !init?.method).length;
const posts = (f: ReturnType<typeof mockApi>) =>
  f.mock.calls
    .filter(([p, init]) => String(p) === '/api/run' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)) as unknown);

let client: QueryClient | null = null;
function GrabClient() {
  const qc = useQueryClient();
  useEffect(() => {
    client = qc;
  }, [qc]);
  return null;
}
/** Wait until the page has its first /api/run/current answer (and the watcher has seen it). */
async function firstAnswer() {
  await waitFor(() => expect(client?.getQueryData(['run-current'])).toBeTruthy());
  await act(async () => {});
}

beforeEach(() => {
  resetNotifyForTests();
});
afterEach(() => {
  vi.unstubAllGlobals();
  client = null;
});

// ---- pre-flight and the Run button -----------------------------------------------------------

describe('PreflightPanel', () => {
  it('lists the checks and offers Run week N with its command', async () => {
    mockApi({ ...base, '/api/preflight': preflight(), '/api/run/current': IDLE });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    const run = await screen.findByRole('button', { name: 'Run week 5' });
    expect(run).toBeEnabled();
    expect(screen.getByText('Week 4 is final')).toBeInTheDocument();
    expect(screen.getByText('16/16 final')).toBeInTheDocument();
    expect(screen.getByText('the run starts it')).toBeInTheDocument(); // a warn's reason
    expect(screen.getByText('Tuesday run')).toBeInTheDocument();
    expect(screen.getByText('nfl weekly run --auto --expect-week 5')).toHaveClass('cmd');
    expect(screen.getByText(/Closing this tab doesn't stop a run/)).toBeInTheDocument();
  });

  it('renders nothing for a week pre-flight is not about', async () => {
    const f = mockApi({ ...base, '/api/preflight': preflight(), '/api/run/current': IDLE });
    renderApp(withUi(<PreflightPanel season={2026} week={4} />));
    await waitFor(() => expect(preflightCalls(f)).toBe(1));
    expect(screen.queryByRole('region', { name: 'Pre-flight' })).toBeNull();
  });

  it('greys Run out with the reason, and Check again refetches', async () => {
    let answer = BLOCKED;
    const f = mockApi({ ...base, '/api/preflight': () => answer, '/api/run/current': IDLE });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    const run = await screen.findByRole('button', { name: 'Run week 5' });
    expect(run).toBeDisabled();
    expect(run).toHaveAttribute('aria-disabled', 'true');
    expect(run).toHaveAttribute('title', BLOCKED.run.reason);
    expect(run).toHaveAccessibleDescription(BLOCKED.run.reason as string);
    expect(screen.getByText(BLOCKED.run.reason as string)).toBeInTheDocument();
    expect(screen.getByText('Falcons at Saints not in play-by-play yet')).toBeInTheDocument();
    expect(screen.getByLabelText('fail')).toHaveTextContent('✕');

    answer = preflight();
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run week 5' })).toBeEnabled());
    expect(preflightCalls(f)).toBe(2);
  });

  it('shows "Run in progress" instead of the button while a run is going', async () => {
    mockApi({
      ...base,
      '/api/preflight': preflight(),
      '/api/run/current': { state: 'running', run: runInfo() },
    });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    expect(await screen.findByText('Run in progress')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Run week 5' })).toBeNull();
  });

  it("confirms the server's week and posts exactly {kind, expect_week}", async () => {
    const f = mockApi({
      ...base,
      '/api/preflight': preflight(),
      '/api/run/current': IDLE,
      'POST /api/run': {
        run_id: 'r1',
        kind: 'weekly',
        command: 'nfl weekly run --auto --expect-week 5',
        season: 2026,
        week: 5,
      },
    });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    fireEvent.click(await screen.findByRole('button', { name: 'Run week 5' }));
    const dialog = screen.getByRole('dialog', { name: 'Run 2026 week 5?' });
    expect(within(dialog).getByText('Confirm the week')).toBeInTheDocument();
    expect(within(dialog).getByText('week 4 · 16/16 final')).toBeInTheDocument();
    expect(within(dialog).getByText('Thu, Oct 8, 8:15 PM ET · 58 h')).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        'ingest → ready → curate → ratings → game → graph → player → digest',
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('week05-digest.md · production alias')).toBeInTheDocument();
    expect(within(dialog).getByText(/stops before touching anything/)).toBeInTheDocument();
    const go = within(dialog).getByRole('button', { name: 'Run week 5' });
    expect(go).toHaveFocus();

    fireEvent.click(go);
    await waitFor(() => expect(posts(f)).toEqual([{ kind: 'weekly', expect_week: 5 }]));
    expect(await within(toasts()).findByText('Week 5 started')).toBeInTheDocument();
  });

  it('Escape cancels the dialog without posting', async () => {
    const f = mockApi({ ...base, '/api/preflight': preflight(), '/api/run/current': IDLE });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    fireEvent.click(await screen.findByRole('button', { name: 'Run week 5' }));
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(posts(f)).toEqual([]);
  });

  it('a 409 (lock held) shows the server message in red and checks pre-flight again', async () => {
    const f = mockApi({
      ...base,
      '/api/preflight': preflight(),
      '/api/run/current': IDLE,
      'POST /api/run': withStatus(409, {
        error: { code: 'locked', message: 'A run started from a terminal holds the lock.' },
      }),
    });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    fireEvent.click(await screen.findByRole('button', { name: 'Run week 5' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run week 5' }));
    const title = await within(toasts()).findByText('Another run holds the lock');
    expect(title.closest('.toast')).toHaveClass('err');
    expect(
      within(toasts()).getByText(/A run started from a terminal holds the lock/),
    ).toBeInTheDocument();
    await waitFor(() => expect(preflightCalls(f)).toBeGreaterThanOrEqual(2));
  });

  it('a 409 (the app already runs one) says a run is going', async () => {
    mockApi({
      ...base,
      '/api/preflight': preflight(),
      '/api/run/current': IDLE,
      'POST /api/run': withStatus(409, {
        error: { code: 'running', message: 'Week 5 is already running.' },
      }),
    });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    fireEvent.click(await screen.findByRole('button', { name: 'Run week 5' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run week 5' }));
    const title = await within(toasts()).findByText('A run is already going');
    expect(title.closest('.toast')).toHaveClass('err');
  });

  it("a 403 (the week moved) shows the server's reason", async () => {
    mockApi({
      ...base,
      '/api/preflight': preflight(),
      '/api/run/current': IDLE,
      'POST /api/run': withStatus(403, {
        error: { code: 'not_allowed', message: "The calendar's week is 6 now." },
      }),
    });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    fireEvent.click(await screen.findByRole('button', { name: 'Run week 5' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run week 5' }));
    expect(
      await within(toasts()).findByText("The server didn't allow the run"),
    ).toBeInTheDocument();
    expect(within(toasts()).getByText(/The calendar's week is 6 now/)).toBeInTheDocument();
  });

  it('asks for notification permission once, on the first Run click', async () => {
    const requestPermission = vi.fn(() => Promise.resolve('granted' as NotificationPermission));
    vi.stubGlobal(
      'Notification',
      Object.assign(function Notification() {}, { permission: 'default', requestPermission }),
    );
    mockApi({ ...base, '/api/preflight': preflight(), '/api/run/current': IDLE });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    const run = await screen.findByRole('button', { name: 'Run week 5' });
    expect(requestPermission).not.toHaveBeenCalled();
    fireEvent.click(run);
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Run week 5' }));
    expect(requestPermission).toHaveBeenCalledTimes(1);
  });

  it('rehearsal mode says what it rehearses', async () => {
    mockApi({
      ...base,
      '/api/preflight': preflight({ mode: 'rehearsal' }),
      '/api/run/current': IDLE,
    });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    expect(await screen.findByRole('button', { name: 'Rehearse week 5' })).toBeEnabled();
    expect(screen.getByText('Rehearsal')).toBeInTheDocument();
    expect(screen.getByText(/nothing live is touched/)).toBeInTheDocument();
    expect(
      screen.getByText(/The rehearsed week is the newest published one\. A rehearsal takes/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/never from a picker/)).toBeNull();
  });

  it('live mode keeps the calendar note under the button', async () => {
    mockApi({ ...base, '/api/preflight': preflight(), '/api/run/current': IDLE });
    renderApp(withUi(<PreflightPanel season={2026} week={5} />));
    expect(await screen.findByText(/never from a picker/)).toBeInTheDocument();
    expect(screen.queryByText(/The rehearsed week is the newest/)).toBeNull();
  });
});

// ---- failure and resume -----------------------------------------------------------------------

describe('FailureBanner', () => {
  const resumable = preflight({
    run: { allowed: false, reason: 'Week 5 failed: resume it', command: null, retry_after: null },
    resume: {
      allowed: true,
      reason: null,
      command: 'nfl weekly run --season 2026 --week 5 --from-step player',
      step: 'player',
    },
  });

  it('offers Resume from the failed step, with its own confirm, and posts kind resume', async () => {
    const f = mockApi({
      ...base,
      '/api/preflight': resumable,
      '/api/run/current': IDLE,
      'POST /api/run': {
        run_id: 'r2',
        kind: 'resume',
        command: 'nfl weekly run --season 2026 --week 5 --from-step player',
        season: 2026,
        week: 5,
      },
    });
    renderApp(
      withUi(
        <FailureBanner
          season={2026}
          week={5}
          failedStep="player"
          detail="refit rec_yds-wrte failed"
          error={null}
        />,
      ),
    );
    expect(screen.getByText('The player step failed.')).toBeInTheDocument();
    expect(screen.getByText(/refit rec_yds-wrte failed\./)).toBeInTheDocument();
    expect(screen.getByText(/nothing was published/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Resume week 5 from player' }));
    const dialog = screen.getByRole('dialog', { name: 'Resume 2026 week 5 from player?' });
    expect(within(dialog).getByText(/Steps before player are kept/)).toBeInTheDocument();
    expect(within(dialog).getByText(/publishes as the Tuesday run would/)).toBeInTheDocument();
    expect(within(dialog).getByText(/from the calendar/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume from player' }));
    await waitFor(() => expect(posts(f)).toEqual([{ kind: 'resume', expect_week: 5 }]));
  });

  it('rehearsal mode: the resume confirm speaks of the rehearsed week and folder', async () => {
    mockApi({
      ...base,
      '/api/preflight': { ...resumable, mode: 'rehearsal' },
      '/api/run/current': IDLE,
    });
    renderApp(
      withUi(
        <FailureBanner season={2026} week={5} failedStep="player" detail={null} error={null} />,
      ),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Resume week 5 from player' }));
    const dialog = screen.getByRole('dialog', { name: 'Resume 2026 week 5 from player?' });
    expect(within(dialog).getByText(/the rehearsed week/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/from the calendar/)).toBeNull();
    expect(
      within(dialog).getByText(
        'Steps before player are kept; the rehearsal continues from there, into the rehearsal folder only.',
      ),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/Tuesday run/)).toBeNull();
  });

  it('gives only the terminal command when the server offers no resume for this step', async () => {
    const f = mockApi({
      ...base,
      '/api/preflight': preflight({
        resume: { allowed: true, reason: null, command: 'x', step: 'graph' }, // a different step
      }),
      '/api/run/current': IDLE,
    });
    renderApp(
      withUi(
        <FailureBanner season={2026} week={5} failedStep="player" detail={null} error="boom" />,
      ),
    );
    await waitFor(() => expect(preflightCalls(f)).toBe(1));
    expect(screen.queryByRole('button', { name: /Resume/ })).toBeNull();
    expect(
      screen.getByText('nfl weekly run --season 2026 --week 5 --from-step player'),
    ).toBeInTheDocument();
  });

  it("shows the server's reason for an older week", async () => {
    mockApi({
      ...base,
      '/api/preflight': preflight({
        resume: {
          allowed: false,
          reason: 'Only the current week resumes here',
          command: null,
          step: null,
        },
      }),
      '/api/run/current': IDLE,
    });
    renderApp(
      withUi(<FailureBanner season={2026} week={5} failedStep="game" detail={null} error={null} />),
    );
    expect(await screen.findByText('Only the current week resumes here')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Resume/ })).toBeNull();
  });
});

// ---- the Saturday injury update -------------------------------------------------------------

describe('Saturday injury update', () => {
  const open = preflight({
    injury_update: {
      allowed: true,
      reason: null,
      command: 'nfl weekly injury-update --auto',
      opens: '2026-10-10T04:00:00+00:00',
      closes: '2026-10-13T00:15:00+00:00',
      material_last: null,
    },
  });
  const published = { ...WEEKS.weeks[0], status: 'published' as const, label: 'Published' };

  it('is greyed out with the server reason when closed', async () => {
    mockApi({ ...base, '/api/preflight': preflight(), '/api/run/current': IDLE });
    renderApp(
      withUi(<WeekHeader season={2026} week={5} entry={published} calendar={META.calendar} />),
    );
    expect(await screen.findByText('Opens Sat Oct 10')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Saturday injury update' })).toBeDisabled();
  });

  it('is enabled when open, confirms, and posts kind injury_update', async () => {
    const f = mockApi({
      ...base,
      '/api/preflight': open,
      '/api/run/current': IDLE,
      'POST /api/run': {
        run_id: 'r3',
        kind: 'injury_update',
        command: 'nfl weekly injury-update --auto',
        season: 2026,
        week: 5,
      },
    });
    renderApp(
      withUi(<WeekHeader season={2026} week={5} entry={published} calendar={META.calendar} />),
    );
    const btn = await screen.findByRole('button', { name: 'Saturday injury update' });
    await waitFor(() => expect(btn).toBeEnabled());
    expect(screen.getByText(/Open until Mon, Oct 12, 8:15 PM ET/)).toBeInTheDocument();
    fireEvent.click(btn);
    const dialog = screen.getByRole('dialog', { name: "Run week 5's injury update?" });
    expect(within(dialog).getByText(/only if something material changed/)).toBeInTheDocument();
    expect(within(dialog).getByText('nfl weekly injury-update --auto')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Run injury update' }));
    await waitFor(() => expect(posts(f)).toEqual([{ kind: 'injury_update', expect_week: 5 }]));
  });

  it('streams the live log and toasts whether the update was material', async () => {
    const es = installFakeEventSource();
    let current: RunCurrent = {
      state: 'running',
      run: runInfo({
        run_id: 'inj1',
        kind: 'injury_update',
        command: 'nfl weekly injury-update --auto',
      }),
    };
    mockApi({ ...base, '/api/preflight': preflight(), '/api/run/current': () => current });
    renderApp(
      withUi(
        <>
          <GrabClient />
          <RunWatcher />
          <InjuryUpdateLog season={2026} week={5} />
        </>,
      ),
    );
    expect(await screen.findByText('Saturday injury update')).toBeInTheDocument();
    const t = '2026-10-10T14:00:00+00:00';
    const events: RunEvent[] = [
      {
        seq: 1,
        t,
        run_id: 'inj1',
        type: 'log',
        step: null,
        text: 'pulling injuries and news',
        cls: '',
      },
      {
        seq: 2,
        t,
        run_id: 'inj1',
        type: 'log',
        step: null,
        text: 'Ravens QB downgraded to out',
        cls: 'warn',
      },
    ];
    await waitFor(() => expect(es.all().length).toBe(1));
    act(() => es.last().emitRuns(events));
    expect(await screen.findByText('Ravens QB downgraded to out')).toBeInTheDocument();

    act(() =>
      es.last().emitRun({
        seq: 3,
        t,
        run_id: 'inj1',
        type: 'run_end',
        status: 'ok',
        exit_code: 0,
        message: 'Addendum written for 2 games',
        published: true,
        failed_step: null,
        material: true,
      }),
    );
    expect(await within(toasts()).findByText('Addendum published')).toBeInTheDocument();
    expect(screen.getByText(/it shows under the digest/)).toBeInTheDocument();

    // the watcher sees it finish too, but the card already announced it: one toast only
    current = {
      state: 'finished',
      run: { ...current.run!, finished: t, status: 'ok', exit_code: 0 },
    };
    await act(async () => {
      await client?.invalidateQueries({ queryKey: ['run-current'] });
    });
    expect(within(toasts()).getAllByText('Addendum published')).toHaveLength(1);
  });
});

// ---- the watcher, toasts and the rehearsal banner -------------------------------------------

describe('RunWatcher', () => {
  it('toasts when a run it saw running finishes', async () => {
    let current: RunCurrent = { state: 'running', run: runInfo() };
    mockApi({ ...base, '/api/run/current': () => current });
    renderApp(
      withUi(
        <>
          <GrabClient />
          <RunWatcher />
        </>,
      ),
    );
    await firstAnswer();
    current = {
      state: 'finished',
      run: runInfo({
        finished: '2026-10-06T14:40:00+00:00',
        status: 'ok',
        exit_code: 0,
        published: true,
        message: 'Checks passed first time · 57.8 h before the deadline',
      }),
    };
    await act(async () => {
      await client?.invalidateQueries({ queryKey: ['run-current'] });
    });
    expect(await within(toasts()).findByText('Week 5 published')).toBeInTheDocument();
    expect(within(toasts()).getByText(/Checks passed first time/)).toBeInTheDocument();
  });

  it('does not toast a run that had already ended when the page opened', async () => {
    mockApi({
      ...base,
      '/api/run/current': {
        state: 'finished',
        run: runInfo({ finished: '2026-10-06T14:40:00+00:00', status: 'ok', published: true }),
      },
    });
    renderApp(
      withUi(
        <>
          <GrabClient />
          <RunWatcher />
        </>,
      ),
    );
    await firstAnswer();
    await act(async () => {
      await client?.invalidateQueries({ queryKey: ['run-current'] });
    });
    expect(toasts()).toBeEmptyDOMElement();
  });

  it('toasts a run launched here that ended before it was seen running (exit 6)', async () => {
    let current: RunCurrent = IDLE;
    mockApi({
      ...base,
      '/api/preflight': preflight(),
      '/api/run/current': () => current,
      'POST /api/run': () => {
        current = {
          state: 'finished',
          run: runInfo({ finished: 'x', status: 'week_mismatch', exit_code: 6, published: false }),
        };
        return { run_id: 'r1', kind: 'weekly', command: 'c', season: 2026, week: 5 };
      },
    });
    renderApp(
      withUi(
        <>
          <RunWatcher />
          <PreflightPanel season={2026} week={5} />
        </>,
      ),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Run week 5' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run week 5' }));
    const title = await within(toasts()).findByText("The calendar's week changed: nothing ran");
    expect(title.closest('.toast')).toHaveClass('warn');
  });

  it('notifies the browser when permission was granted', async () => {
    const shown: string[] = [];
    vi.stubGlobal(
      'Notification',
      Object.assign(
        function Notification(title: string) {
          shown.push(title);
        },
        { permission: 'granted', requestPermission: vi.fn() },
      ),
    );
    let current: RunCurrent = { state: 'running', run: runInfo() };
    mockApi({ ...base, '/api/run/current': () => current });
    renderApp(
      withUi(
        <>
          <GrabClient />
          <RunWatcher />
        </>,
      ),
    );
    await firstAnswer();
    current = {
      state: 'finished',
      run: runInfo({ finished: 'x', status: 'failed', exit_code: 1, failed_step: 'player' }),
    };
    await act(async () => {
      await client?.invalidateQueries({ queryKey: ['run-current'] });
    });
    const title = await within(toasts()).findByText('Week 5 failed at the player step');
    expect(title.closest('.toast')).toHaveClass('err');
    expect(shown).toEqual(['Week 5 failed at the player step']);
  });
});

describe('runEndToast', () => {
  it('says what happened for each kind of ending', () => {
    expect(runEndToast(runInfo({ status: 'not_ready', exit_code: 3 })).title).toBe(
      "Week 4 isn't final yet: try again later",
    );
    expect(runEndToast(runInfo({ status: 'week_mismatch', exit_code: 6 })).title).toBe(
      "The calendar's week changed: nothing ran",
    );
    expect(
      runEndToast(runInfo({ status: 'failed', exit_code: 1, failed_step: 'digest' })),
    ).toMatchObject({
      title: 'Week 5 failed at the digest step',
      tone: 'err',
    });
    expect(runEndToast(runInfo({ kind: 'rehearsal', status: 'ok', exit_code: 0 })).title).toBe(
      'Week 5 rehearsed',
    );
    expect(runEndToast(runInfo({ kind: 'injury_update', status: 'ok', exit_code: 0 })).title).toBe(
      'Injury update finished',
    );
    expect(runEndToast(runInfo({ status: 'ok', exit_code: 0, published: true }))).toMatchObject({
      title: 'Week 5 published',
      tone: 'ok',
    });
    expect(runEndToast(runInfo({ status: 'locked', exit_code: 4 })).title).toBe(
      'Another run holds the lock: nothing ran',
    );
    expect(runEndToast(runInfo({ status: 'interrupted' })).tone).toBe('err');
    expect(runEndToast(runInfo({ status: 'already_done', exit_code: 0 })).title).toBe(
      'Week 5 was already published: nothing ran',
    );
    expect(runEndToast(runInfo({ status: 'unknown' })).tone).toBe('warn');
  });
});

describe('Shell', () => {
  it('shows the rehearsal banner with the rehearsed week', async () => {
    mockApi({
      ...base,
      '/api/meta': { ...META, mode: { dev: false, rehearsal: true } },
      '/api/preflight': preflight({ mode: 'rehearsal', week: 3 }),
      '/api/run/current': IDLE,
    });
    renderApp(<Shell />);
    expect(await screen.findByText(/rehearses week 3/)).toBeInTheDocument();
    expect(screen.getByText(/nothing live is touched, no W&B, no graph/)).toBeInTheDocument();
    expect(screen.getByText('rehearsals/control-room')).toBeInTheDocument();
  });
});
