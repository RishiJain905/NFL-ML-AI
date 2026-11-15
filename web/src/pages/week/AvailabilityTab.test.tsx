import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PreflightResponse, RunCurrent } from '../../api/types';
import { RunUiProvider } from '../../components/run/RunUiProvider';
import { inheritByPlayer, ptsText, shareText, whereText } from '../../lib/forecastlab';
import { META, WEEKS, mockApi, renderApp } from '../../test/utils';
import { AvailabilityTab } from './AvailabilityTab';
import { AR_QUESTIONABLE, AVAIL_GRADED, AVAIL_OK, AVAIL_TEAM_TB, AVAIL_TUESDAY, LAB_UP_TO_DATE } from './forecastlab/labFixtures';

const IDLE: RunCurrent = { state: 'idle', run: null };
const W5 = { season: 2026, week: 5, isCurrent: false, lastPublishedWeek: 4 };
const BASE = '/api/weeks/2026/5/availability';

function api(paths: Record<string, unknown>) {
  return mockApi({
    '/api/meta': META,
    '/api/weeks': WEEKS,
    '/api/preflight': { forecastlab: LAB_UP_TO_DATE } as unknown as PreflightResponse,
    '/api/run/current': IDLE,
    [BASE]: AVAIL_OK,
    [`${BASE}?team=TB`]: AVAIL_TEAM_TB,
    ...paths,
  });
}
const render = (el: ReactNode, route = '/week/2026/5/availability') =>
  renderApp(<RunUiProvider>{el}</RunUiProvider>, { route, path: '/week/:season/:week/:tab' });
const section = (name: string) => screen.getByRole('region', { name });
const rowOf = (root: HTMLElement, player: string) => within(root).getByText(player).closest('li') as HTMLElement;

afterEach(() => vi.unstubAllGlobals());

describe('availability words (lib/forecastlab)', () => {
  it('shares are whole percents (100% is real), changes are points', () => {
    expect([shareText(1), shareText(0.474), shareText(null)]).toEqual(['100%', '47%', '—']);
    expect([ptsText(0.16), ptsText(-0.272), ptsText(0.01), ptsText(null)]).toEqual(['+16 points', '−27 points', '+1 point', '—']);
    expect(whereText({ team: 'MIN', opponent: 'GB', home: false })).toBe('MIN at GB');
    expect(whereText({ team: 'DET', opponent: 'NE', home: true })).toBe('DET vs NE');
  });

  it('joins one teammate\'s gains', () => {
    const joined = inheritByPlayer(AR_QUESTIONABLE.inherit);
    expect(joined).toHaveLength(1);
    expect(joined[0].gains.map((g) => g.kind)).toEqual(['snaps', 'targets']);
  });
});

describe('Availability tab', () => {
  it("Saturday's view: the report first, with the chance he plays, Tuesday's beside it, his chips and who takes the work", async () => {
    api({});
    render(<AvailabilityTab {...W5} />);
    const report = await screen.findByRole('region', { name: "On this week's injury report" });
    expect(within(report).getByText('2')).toBeInTheDocument();
    const q = rowOf(report, 'Q. Receiver');
    expect(q).toHaveTextContent('65%to play · usual for his status 34%');
    expect(q).toHaveTextContent('Tuesday 39% → Saturday 65%');
    expect(within(q).getByText('Questionable')).toHaveClass('chip', 'warn');
    expect(within(q).getByText('limited practice')).toBeInTheDocument();
    expect(within(q).getByText('Questionable last week')).toBeInTheDocument();
    expect(q).toHaveTextContent('If he plays: 74% of the snaps (80%: 37%–93%) · usually 84%');
    // one teammate's gains on one line
    expect(q).toHaveTextContent('If he sits:K. Slot WR +10 points of the snaps, +1 point of the targets');
    const out = rowOf(report, 'D. Corner');
    expect(within(out).getByText('Out')).toHaveClass('err');
    expect(out).toHaveTextContent('<1%');
    expect(out).toHaveTextContent('Tuesday 38% → Saturday <1%');
    // the note: Saturday's view, and shares are "if he plays"
    expect(screen.getByText(/This is Saturday's view/).closest('.notice')).toHaveTextContent('Snap, target and carry shares are if he plays');
  });

  it("Tuesday's view: no report yet, said plainly; regulars and the reserve list (collapsed)", async () => {
    api({ [BASE]: AVAIL_TUESDAY });
    render(<AvailabilityTab {...W5} />);
    const report = await screen.findByRole('region', { name: "On this week's injury report" });
    expect(report).toHaveTextContent("No report yet: the first one comes out Wednesday, and Saturday's view reads the final one.");
    expect(screen.getByText(/This is Tuesday's view/).closest('.notice')).toHaveTextContent('leans on last week');
    const regulars = section('Regulars with a low chance');
    const m = rowOf(regulars, 'M. Passer');
    expect(m).toHaveTextContent('Out last week');
    expect(m).toHaveTextContent('If he sits:B. Backup QB +45 points of the snaps');
    expect(m).toHaveTextContent("Didn't play"); // his game is over
    expect(m).not.toHaveTextContent('→ Saturday');
    expect(regulars).toHaveTextContent('played at least 30% of the snaps lately');
    const reserve = screen.getByText('On a reserve list').closest('details') as HTMLDetailsElement;
    expect(reserve.open).toBe(false);
    expect(within(reserve).getByText('reserve list')).toBeInTheDocument();
    expect(screen.getByText('This week\'s report').closest('.tile')).toHaveTextContent('not out yet on Tuesday');
  });

  it('the strip: each view, the model, the game made after kickoff', async () => {
    api({});
    render(<AvailabilityTab {...W5} />);
    const strip = await screen.findByRole('note', { name: 'When this was made' });
    expect(strip).toHaveTextContent('showing the Saturday view');
    expect(strip).toHaveTextContent('1 game made after kickoff (TB at ATL)');
  });

  it('the biggest role changes, in points', async () => {
    api({});
    render(<AvailabilityTab {...W5} />);
    const box = (await screen.findByRole('heading', { name: 'More of the carries' })).closest('.av-mv') as HTMLElement;
    expect(box).toHaveTextContent('C. UpRB · TB31% → 47%+16 points');
    const down = screen.getByRole('heading', { name: 'Fewer snaps' }).closest('.av-mv') as HTMLElement;
    expect(down).toHaveTextContent('50% → 22%−28 points');
  });

  it('every player: pick a team, the server sends that team, offense then defense', async () => {
    const f = api({});
    render(<AvailabilityTab {...W5} />);
    const every = await screen.findByRole('region', { name: 'Every player' });
    expect(every).toHaveTextContent('Pick a team to see every player');
    fireEvent.click(within(every).getByRole('button', { name: 'TB' }));
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u) === `${BASE}?team=TB`)).toBe(true));
    expect(await within(every).findByText('T. Quarterback')).toBeInTheDocument();
    const rows = within(every).getAllByRole('row').map((r) => r.textContent ?? '');
    expect(rows.findIndex((t) => t === 'Offense')).toBeLessThan(rows.findIndex((t) => t === 'Defense'));
    expect(within(every).getByRole('button', { name: 'TB' })).toHaveAttribute('aria-pressed', 'true');
    // a game that's over: what happened
    expect(within(every).getByRole('columnheader', { name: 'Played' })).toBeInTheDocument();
    expect(within(every).getByText("didn't play")).toBeInTheDocument();
    expect(within(every).getByText('Q. Receiver').closest('tr')).toHaveClass('risk');
  });

  it('the team comes from the URL', async () => {
    api({});
    render(<AvailabilityTab {...W5} />, '/week/2026/5/availability?team=TB');
    expect(await screen.findByText('T. Quarterback')).toBeInTheDocument();
  });

  it("a team that isn't playing this week is ignored (no request for it)", async () => {
    const f = api({});
    render(<AvailabilityTab {...W5} />, '/week/2026/5/availability?team=ZZZ');
    expect(await screen.findByRole('region', { name: 'Every player' })).toHaveTextContent('Pick a team');
    expect(f.mock.calls.some(([u]) => String(u).includes('team=ZZZ'))).toBe(false);
  });

  it('after the games: how it did, bold only when clearly better; the backtest note always', async () => {
    api({ [BASE]: AVAIL_GRADED });
    render(<AvailabilityTab {...W5} />);
    const score = await screen.findByRole('region', { name: 'How it did' });
    const play = within(score).getByText('Will he play').closest('tr') as HTMLElement;
    expect(within(play).getByText('0.101')).toHaveClass('best');
    const tgt = within(score).getByText('Target share').closest('tr') as HTMLElement;
    expect(within(tgt).getAllByText('5.0 pts')[0]).not.toHaveClass('best');
    fireEvent.click(within(score).getByRole('button', { name: 'Season to date' }));
    expect(within(score).getByText('9,000')).toBeInTheDocument();
    expect(section('How good is this')).toHaveTextContent('0.107 vs 0.149');
  });

  it('no betting words anywhere on the tab', async () => {
    api({ [BASE]: AVAIL_GRADED });
    const { container } = render(<AvailabilityTab {...W5} />);
    await screen.findByRole('region', { name: 'How it did' });
    expect(container.textContent).not.toMatch(/\b(odds|over\/under|wager|bet|betting|lines?)\b/i);
  });
});
