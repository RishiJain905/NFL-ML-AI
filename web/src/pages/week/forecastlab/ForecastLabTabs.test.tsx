import { screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PreflightResponse, RunCurrent } from '../../../api/types';
import { RunUiProvider } from '../../../components/run/RunUiProvider';
import { splitCmd } from '../../../lib/forecastlab';
import { META, WEEKS, mockApi, renderApp } from '../../../test/utils';
import { WeekPage } from '../../WeekPage';
import { AvailabilityTab } from '../AvailabilityTab';
import { OutcomesTab } from '../OutcomesTab';
import {
  AVAIL_NO_MODEL,
  AVAIL_NONE,
  AVAIL_NONE_W3,
  AVAIL_OK,
  LAB_UP_TO_DATE,
  OUTCOMES_NO_MODEL,
  OUTCOMES_NONE,
  OUTCOMES_OK,
  labGate,
} from './labFixtures';

const W5 = { season: 2026, week: 5, isCurrent: true, lastPublishedWeek: 4 };
const W3 = { season: 2026, week: 3, isCurrent: false, lastPublishedWeek: 4 };
const IDLE: RunCurrent = { state: 'idle', run: null };

function preflight(gate = labGate()): PreflightResponse {
  return {
    checked_at: '2026-10-07T14:00:00+00:00',
    mode: 'live',
    season: 2026,
    week: 5,
    checks: [],
    run: { allowed: false, reason: 'Week 5 is already published.', command: null, retry_after: null },
    confirm: { last_week: 'week 4', deadline: null, hours_to_deadline: null, retry_until: null, steps: [], publishes: [] },
    resume: { allowed: false, reason: null, command: null, step: null },
    injury_update: { allowed: false, reason: 'Opens Saturday.', command: null, opens: null, closes: null, material_last: null },
    playcalling: { allowed: false, reason: 'Everything is up to date.', command: null, steps: [] },
    forecastlab: gate,
    chain: true,
    dry_run_log: [],
  };
}

const api = (paths: Record<string, unknown>, gate = labGate()) =>
  mockApi({ '/api/meta': META, '/api/weeks': WEEKS, '/api/preflight': preflight(gate), '/api/run/current': IDLE, ...paths });

const tab = (el: ReactNode) => renderApp(<RunUiProvider>{el}</RunUiProvider>);

afterEach(() => vi.unstubAllGlobals());

describe('the two new week tabs', () => {
  it('sit after Play calls, in that order, as route links', () => {
    mockApi();
    renderApp(<WeekPage />, { route: '/week/2026/5/availability', path: '/week/:season/:week/:tab' });
    const nav = screen.getByRole('navigation', { name: 'Week sections' });
    const names = within(nav).getAllByRole('link').map((t) => t.textContent);
    expect(names.slice(-3)).toEqual(['Play calls', 'Availability', 'Outcomes']);
    expect(within(nav).getByRole('link', { name: 'Availability' })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getByRole('link', { name: 'Outcomes' })).toHaveAttribute('href', '/week/2026/5/outcomes');
  });
});

describe('Availability tab', () => {
  it('no model promoted: the designed empty state with its command, and the Update card below', async () => {
    api({ '/api/weeks/2026/5/availability': AVAIL_NO_MODEL });
    const { container } = tab(<AvailabilityTab {...W5} />);
    expect(await screen.findByRole('heading', { name: 'No availability model yet' })).toBeInTheDocument();
    expect(screen.getByText(/who might sit this week/)).toHaveTextContent("This model hasn't been promoted yet: its backtest decides what ships.");
    expect(screen.getByText('nfl forecastlab backtest --model availability --promote', { selector: '.cmd' })).toBeInTheDocument();
    const card = await screen.findByRole('heading', { name: 'Update availability and outcomes' });
    // the Update card comes after the empty state
    const empty = container.querySelector('.empty') as HTMLElement;
    expect(empty.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("nothing for the current week yet: the server's sentence and a link to the last published week", async () => {
    api({ '/api/weeks/2026/5/availability': AVAIL_NONE });
    tab(<AvailabilityTab {...W5} />);
    expect(await screen.findByRole('heading', { name: "Week 5's availability isn't made yet" })).toBeInTheDocument();
    expect(screen.getByText(/It's made right after Run week 5/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: "See week 4's Availability" })).toHaveAttribute('href', '/week/2026/4/availability');
  });

  it('a past week without a file: plain words, no Update card, no "press Update"', async () => {
    api({ '/api/weeks/2026/3/availability': AVAIL_NONE_W3 });
    tab(<AvailabilityTab {...W3} />);
    expect(await screen.findByRole('heading', { name: 'No availability for week 3' })).toBeInTheDocument();
    expect(screen.queryByText(/press Update/)).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Update availability and outcomes' })).toBeNull();
  });

  it('with data: when each view was made, the model, the counts', async () => {
    api({ '/api/weeks/2026/5/availability': AVAIL_OK }, LAB_UP_TO_DATE);
    tab(<AvailabilityTab {...W5} />);
    const strip = await screen.findByRole('note', { name: 'When this was made' });
    expect(strip).toHaveTextContent('Tuesday view Tue, Oct 6, 10:40 AM ET');
    expect(strip).toHaveTextContent('Saturday view Sat, Oct 10, 11:05 AM ET');
    expect(strip).toHaveTextContent('showing the Saturday view');
    expect(strip).toHaveTextContent('model avail-v1');
    expect(strip).toHaveTextContent('trained through 2026-w04');
    expect(strip).toHaveTextContent('1 game made after kickoff (TB at ATL)');
    expect(screen.getByText('Might sit').closest('.tile')).toHaveTextContent('4');
    expect(await screen.findByText('Everything is up to date.')).toBeInTheDocument();
  });
});

describe('Outcomes tab', () => {
  it('no model promoted: the designed empty state with its command', async () => {
    api({ '/api/weeks/2026/5/outcomes': OUTCOMES_NO_MODEL });
    tab(<OutcomesTab {...W5} />);
    expect(await screen.findByRole('heading', { name: 'No outcome-curves model yet' })).toBeInTheDocument();
    expect(screen.getByText('nfl forecastlab backtest --model curves --promote', { selector: '.cmd' })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Update availability and outcomes' })).toBeInTheDocument();
  });

  it('nothing for this week yet', async () => {
    api({ '/api/weeks/2026/5/outcomes': OUTCOMES_NONE });
    tab(<OutcomesTab {...W5} />);
    expect(await screen.findByRole('heading', { name: "Week 5's outcome curves aren't made yet" })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: "See week 4's Outcomes" })).toHaveAttribute('href', '/week/2026/4/outcomes');
  });

  it('with data: when made, from which models, the counts (the full tab: OutcomesTab.test.tsx)', async () => {
    api({ '/api/weeks/2026/5/outcomes': OUTCOMES_OK });
    tab(<OutcomesTab {...W5} />);
    const strip = await screen.findByRole('note', { name: 'When this was made' });
    expect(strip).toHaveTextContent('Made Tue, Oct 6, 10:45 AM ET');
    expect(strip).toHaveTextContent('curves model curves-v1');
    expect(strip).toHaveTextContent('built on player model player-v12');
    expect(screen.getByText('Curves').closest('.tile')).toHaveTextContent('1,381');
    expect(screen.getByText('Curves').closest('.tile')).toHaveTextContent('631 players · 2 stats');
    expect(screen.getByText('This stat').closest('.tile')).toHaveTextContent('WR/TE receiving yards');
  });
});

describe('helpers', () => {
  it('splitCmd takes the command out of a hint', () => {
    expect(splitCmd(AVAIL_NO_MODEL.hint)).toEqual({
      text: "This model hasn't been promoted yet: its backtest decides what ships.",
      cmd: 'nfl forecastlab backtest --model availability --promote',
    });
    expect(splitCmd(null)).toEqual({ text: '', cmd: null });
    expect(splitCmd('No file: `nfl x`')).toEqual({ text: 'No file.', cmd: 'nfl x' });
  });
});
