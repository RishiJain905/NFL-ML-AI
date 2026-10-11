import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { META, WEEKS, mockApi, renderApp, withStatus } from '../../../test/utils';
import { WeekPage } from '../../WeekPage';
import { GameDayTab } from '../GameDayTab';
import { TEAM_INFO } from '../weekFixtures';
import {
  CALL_BEHIND,
  CALL_FOURTH,
  CALL_NONE,
  CALL_REPEAT,
  CALL_STALE,
  CALL_THIRD,
  CALL_TOSS,
  CONTEXT,
  GAMES_BEFORE,
  GAMES_BETWEEN,
  GAMES_FEED_ERROR,
  GAMES_FINAL,
  GAMES_FUTURE,
  GAMES_LIVE,
  GAMES_NO_MODELS,
  GAMES_PAST,
  GAMES_REPLAY,
  PATH,
} from './gamedayFixtures';
import { REVIEW_NO_PLAYS } from './reviewFixtures';

const W4 = { season: 2026, week: 4, isCurrent: false, lastPublishedWeek: 4 };

function api(extra: Record<string, unknown> = {}) {
  return mockApi({ '/api/team-info': TEAM_INFO, [PATH.games]: GAMES_LIVE, [PATH.context('100', 'BBB')]: CONTEXT, ...extra });
}
const calledPaths = (f: ReturnType<typeof mockApi>) => f.mock.calls.map(([input]) => String(input));
const liveCalls = (f: ReturnType<typeof mockApi>) => calledPaths(f).filter((p) => /\/(call|context)/.test(p));
const checkButton = () => screen.findByRole('button', { name: /Check (this play|again)|Try again/ });

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('GameDayTab: the board', () => {
  it('lists the games by state and opens on the first 3rd / 4th down, fetching nothing else', async () => {
    const f = api({ [PATH.call('100')]: CALL_FOURTH });
    renderApp(<GameDayTab {...W4} />);
    const onNow = await screen.findByRole('group', { name: 'On now' });
    expect(within(onNow).getAllByRole('button')).toHaveLength(2);
    expect(screen.getByRole('group', { name: 'Later' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Final' })).toBeInTheDocument();
    const card = screen.getByRole('button', { name: /AAA 17 at BBB 20, Q4 2:11, 4th & 5 at AAA 34/ });
    expect(card).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Our pre-game pick')).toBeInTheDocument();
    expect(screen.getByText('AAA 55%')).toBeInTheDocument();
    expect(await checkButton()).toHaveTextContent('Check this play');
    expect(liveCalls(f)).toEqual([]); // no /call or /context until the click
  });

  it('keeps the game it opened on when the next refresh moves past its 4th down', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const f = api();
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByRole('button', { name: /AAA 17 at BBB 20/ })).toHaveAttribute('aria-pressed', 'true');
    const moved = { ...GAMES_LIVE, games: GAMES_LIVE.games.map((g, i) => (i === 0 ? { ...g, decision_down: false, down: 1, situation: '1st & 10 at AAA 25' } : { ...g, decision_down: i === 1 })) };
    const real = f.getMockImplementation()!;
    f.mockImplementation(async (input, init) => (String(input) === PATH.games ? new Response(JSON.stringify(moved)) : real(input, init)));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    expect(await screen.findByText('1st & 10 at AAA 25')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /AAA 17 at BBB 20/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('renders ESPN text as text, never as HTML', async () => {
    api();
    const { container } = renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByText('(Shotgun) <b>Q.Back</b> pass incomplete short left.')).toBeInTheDocument();
    expect(container.querySelector('.lastplay b')).toBeNull();
  });

  it('checks a 4th down on the click: the call, three honest bars, the odds and the team', async () => {
    const f = api({ [PATH.call('100')]: CALL_FOURTH });
    const { container } = renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await checkButton());
    expect(await screen.findByText('Go for it', { selector: '.big' })).toBeInTheDocument();
    expect(screen.getByText('Worth 1.4 more points of win chance than the field goal')).toBeInTheDocument();
    expect(screen.getByText('The call held in 20 of 20 re-checks of the models.')).toBeInTheDocument();
    expect(['73.1%', '71.7%', '64.0%'].every((t) => screen.getByText(t))).toBe(true);
    const fills = [...container.querySelectorAll<HTMLElement>('.opt .fill')].map((e) => e.style.width);
    expect(fills).toEqual(['73.1%', '71.7%', '64%']); // a 0-100% axis
    expect(container.querySelectorAll('.opt.best')).toHaveLength(1);
    expect(container.querySelector('.opt .now')).toBeNull(); // wp_now isn't drawn on a 4th down
    expect(container).not.toHaveTextContent('81.0%');
    expect(screen.getByText('to make it from 52 yards')).toBeInTheDocument();
    expect(screen.getByText('Own 11')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Is BBB good at this?' })).toBeInTheDocument();
    expect(screen.getByText('this kick: 52')).toBeInTheDocument();
    expect(screen.getByText('career long 55 (2024)')).toBeInTheDocument();
    expect(screen.getByText('this play')).toBeInTheDocument(); // 4th & 5: the 3-5 band
    expect(screen.getByRole('button', { name: 'Check again' })).toBeInTheDocument();
    expect(liveCalls(f)).toEqual([PATH.call('100'), PATH.context('100', 'BBB')]);
  });

  it('says Toss-up big, with the leading option under it, and no single full accent', async () => {
    api({ [PATH.call('100')]: CALL_TOSS });
    const { container } = renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await checkButton());
    expect(await screen.findByText('Toss-up', { selector: '.big' })).toBeInTheDocument();
    expect(screen.getByText('Field goal by 0.2 points over going for it')).toBeInTheDocument();
    expect(screen.getByText(/Under 1 point apart: either choice is defensible/)).toBeInTheDocument();
    expect(container.querySelectorAll('.opt.best')).toHaveLength(0);
    expect(container.querySelectorAll('.opt.tied')).toHaveLength(2);
    expect(screen.getByRole('img', { name: /How sure the bot is: Confident.*Lean.*Toss-up/ })).toBeInTheDocument();
  });

  it('shows the 3rd-down chances and the if-stopped table with the no-gain row marked', async () => {
    api({ [PATH.call('100')]: CALL_THIRD });
    const { container } = renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await checkButton());
    expect(await screen.findByText('chance they convert')).toBeInTheDocument();
    expect(screen.getByText('52%')).toBeInTheDocument();
    expect(screen.getByText('69%')).toBeInTheDocument(); // wp_now is shown on a 3rd down
    const rows = container.querySelectorAll('.iftbl tbody tr');
    expect(rows).toHaveLength(4);
    expect(rows[2]).toHaveClass('nogain');
    expect(rows[2]).toHaveTextContent('4th & 3 at BBB 46');
    expect(rows[3]).toHaveTextContent('Punt');
    expect(rows[3]).toHaveTextContent('Toss-up');
  });

  it('when ESPN is behind, says so and marks the no-gain row as the TV likely shows it', async () => {
    api({ [PATH.call('100')]: CALL_BEHIND });
    const { container } = renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await checkButton());
    expect(await screen.findByText(/ESPN still shows 3rd & 3 at BBB 46/)).toBeInTheDocument();
    expect(container.querySelector('.iftbl tr.yours')).toHaveTextContent('your TV, likely');
  });

  it('a stale answer and a repeat check say so; assumptions are listed', async () => {
    api({ [PATH.call('100')]: CALL_STALE });
    renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await checkButton());
    expect(await screen.findByText(/ESPN didn.t answer \(HTTP 503\)/)).toBeInTheDocument();
    expect(screen.getAllByText(/Last good answer/, { selector: '.chip' })).toHaveLength(2); // the panel's and the card's stamps
    vi.unstubAllGlobals();

    api({ [PATH.call('100')]: CALL_REPEAT });
    renderApp(<GameDayTab {...W4} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Check this play' }))[0]);
    expect(await screen.findByText('No new play since your last check (10 s ago).')).toBeInTheDocument();
    expect(screen.getByText('Assumed: no pre-game line: spread 0 assumed')).toBeInTheDocument();
  });

  it('a 1st or 2nd down says why there is no call, and fetches no team context', async () => {
    const f = api({ [PATH.call('101')]: CALL_NONE });
    renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await screen.findByRole('button', { name: /CCC 17 at DDD 20/ }));
    fireEvent.click(await checkButton());
    expect(await screen.findByText('2nd & 5: checks are for 3rd and 4th downs.')).toBeInTheDocument();
    expect(liveCalls(f)).toEqual([PATH.call('101')]);
  });

  it('shows the loading state, then the error with Try again', async () => {
    let release: (r: Response) => void = () => {};
    const f = api({ [PATH.call('100')]: withStatus(503, { error: { code: 'espn_unavailable', message: "ESPN didn't answer (timeout)." } }) });
    const real = f.getMockImplementation()!;
    f.mockImplementation(async (input, init) => {
      if (String(input) === PATH.call('100') && f.mock.calls.filter(([i]) => String(i) === PATH.call('100')).length === 1)
        return new Promise<Response>((r) => {
          release = r;
        });
      return real(input, init);
    });
    const { container } = renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await checkButton());
    expect(await screen.findByRole('button', { name: 'Checking…' })).toHaveAttribute('aria-busy', 'true');
    expect(container.querySelector('.skel')).not.toBeNull();
    await act(async () => {
      release(new Response(JSON.stringify({ error: { code: 'espn_unavailable', message: "ESPN didn't answer (timeout)." } }), { status: 503 }));
    });
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't check this play. ESPN didn't answer (timeout).");
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(container.querySelector('.skel')).toBeNull();
  });

  it("shows the server's message for any API error", async () => {
    const text = 'ESPN sent something unexpected for this game. Try again shortly.';
    api({ [PATH.call('100')]: withStatus(502, { error: { code: 'espn_unexpected', message: text } }) });
    renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await checkButton());
    expect(await screen.findByRole('alert')).toHaveTextContent(`Couldn't check this play. ${text}`);
    vi.unstubAllGlobals();

    api({ [PATH.games]: withStatus(403, { error: { code: 'not_allowed', message: 'Cross-site requests are not allowed.' } }) });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByText('Cross-site requests are not allowed.', { exact: false })).toBeInTheDocument();
  });

  it('the Refresh button re-asks the server with ?refresh=1', async () => {
    const f = api({ [PATH.refresh]: GAMES_LIVE });
    renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(calledPaths(f)).toContain(PATH.refresh));
  });
});

describe('GameDayTab: the week states', () => {
  it('before the first kickoff: the slate', async () => {
    mockApi({ '/api/team-info': TEAM_INFO, '/api/live/2026/5/games': GAMES_BEFORE });
    renderApp(<GameDayTab {...W4} week={5} />);
    expect(await screen.findByRole('heading', { name: 'Game day opens at the first kickoff' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Week 5 slate' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Check this play/ })).toBeNull();
  });

  it('a future week: the slate and a link to the week being played', async () => {
    mockApi({ '/api/team-info': TEAM_INFO, '/api/live/2026/6/games': GAMES_FUTURE });
    renderApp(<GameDayTab {...W4} week={6} />);
    expect(await screen.findByRole('link', { name: 'Game day is on week 4' })).toHaveAttribute('href', '/week/2026/4/game-day');
  });

  it('a past week: its decision review (LD03; ReviewView.test.tsx has the rest), never ESPN', async () => {
    const f = mockApi({ '/api/team-info': TEAM_INFO, '/api/live/2026/3/games': GAMES_PAST, '/api/live/2026/3/review': REVIEW_NO_PLAYS });
    renderApp(<GameDayTab {...W4} week={3} />);
    expect(await screen.findByText('Decision review · 2026 Week 3')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Week 3 finals' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Game day is on week 4' })).toBeInTheDocument();
    expect(liveCalls(f)).toEqual([]);
  });

  it('between windows and when every game is final', async () => {
    api({ [PATH.games]: GAMES_BETWEEN });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByRole('heading', { name: 'No game on right now' })).toBeInTheDocument();
    expect(screen.getByText(/Next: EEE at FFF/)).toBeInTheDocument();
    vi.unstubAllGlobals();
    api({ [PATH.games]: GAMES_FINAL });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByRole('heading', { name: 'Every week-4 game is final' })).toBeInTheDocument();
  });

  it('without the models, the list works and the button stays off', async () => {
    api({ [PATH.games]: GAMES_NO_MODELS });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByText("The decision models aren't trained yet (LD00).", { selector: 'b' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check this play' })).toHaveAttribute('aria-disabled', 'true');
  });

  it("ESPN down with no earlier answer: the schedule's games and a notice", async () => {
    api({ [PATH.games]: GAMES_FEED_ERROR });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByText(/ESPN didn.t answer \(timeout\)\./)).toBeInTheDocument();
  });

  it('a replay says so', async () => {
    api({ [PATH.games]: GAMES_REPLAY });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByText('Replay.')).toBeInTheDocument();
    expect(screen.getByText(/Nothing is fetched from ESPN/)).toBeInTheDocument();
  });
});

describe('GameDayTab: the list refresh', () => {
  it('refetches every refresh_s only for the week being played', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const f = api();
    renderApp(<GameDayTab {...W4} />);
    await screen.findByRole('group', { name: 'On now' });
    const n = () => calledPaths(f).filter((p) => p === PATH.games).length;
    expect(n()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    expect(n()).toBe(2);
    vi.unstubAllGlobals();

    const g = mockApi({ '/api/team-info': TEAM_INFO, '/api/live/2026/3/games': GAMES_PAST, '/api/live/2026/3/review': REVIEW_NO_PLAYS });
    renderApp(<GameDayTab {...W4} week={3} />);
    await screen.findByRole('heading', { name: 'Week 3 finals' });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(65_000);
    });
    expect(g.mock.calls.filter(([i]) => String(i) === '/api/live/2026/3/games')).toHaveLength(1);
  });
});

describe('WeekPage: the Game day tab', () => {
  it('comes after Graph; the live count comes from the cached list only', async () => {
    const f = mockApi({ '/api/meta': META, '/api/weeks': WEEKS, '/api/team-info': TEAM_INFO, [PATH.games]: GAMES_LIVE });
    renderApp(<WeekPage />, { route: '/week/2026/4/graph', path: '/week/:season/:week/:tab' });
    const tabs = screen.getByRole('navigation', { name: 'Week sections' });
    const links = within(tabs).getAllByRole('link');
    expect(links.map((t) => t.textContent)).toEqual(['Pipeline', 'Digest', 'Games', 'Players', 'Results', 'MLOps', 'Graph', 'Game day', 'Play calls']);
    expect(links[7]).toHaveAttribute('href', '/week/2026/4/game-day');
    await waitFor(() => expect(calledPaths(f)).toContain('/api/weeks'));
    expect(calledPaths(f).some((p) => p.startsWith('/api/live/'))).toBe(false); // another tab never asks
  });

  it('shows "N live" on the tab once Game day has the list', async () => {
    mockApi({ '/api/meta': META, '/api/weeks': WEEKS, '/api/team-info': TEAM_INFO, [PATH.games]: GAMES_LIVE });
    renderApp(<WeekPage />, { route: '/week/2026/4/game-day', path: '/week/:season/:week/:tab' });
    const tabs = screen.getByRole('navigation', { name: 'Week sections' });
    expect(await within(tabs).findByText('2 live')).toBeInTheDocument();
  });
});

describe('GameDayTab: failures and phase changes (Sol review)', () => {
  const fail = (status: number, message: string) => withStatus(status, { error: { code: 'x', message } });

  it('a failed refresh keeps the board and the open call card, with a Retry', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let list: unknown = GAMES_LIVE;
    const f = api({ [PATH.games]: () => list, [PATH.call('100')]: CALL_FOURTH });
    renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await checkButton());
    expect(await screen.findByText('Go for it', { selector: '.big' })).toBeInTheDocument();
    list = fail(503, 'The server is busy.');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    expect(await screen.findByText("Couldn't refresh the games.")).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('The server is busy. Showing the list from');
    expect(screen.getByRole('group', { name: 'On now' })).toBeInTheDocument();
    expect(screen.getByText('Go for it', { selector: '.big' })).toBeInTheDocument();
    list = GAMES_LIVE;
    const before = calledPaths(f).filter((p) => p === PATH.games).length;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText("Couldn't refresh the games.")).toBeNull());
    expect(calledPaths(f).filter((p) => p === PATH.games).length).toBe(before + 1);
  });

  it('a failed manual Refresh says so and keeps the list', async () => {
    api({ [PATH.refresh]: fail(502, 'ESPN sent something unexpected for this game. Try again shortly.') });
    renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't refresh the games. ESPN sent something unexpected");
    expect(screen.getByRole('group', { name: 'On now' })).toBeInTheDocument();
  });

  it('the first-load error shows Retry, and Retry refetches', async () => {
    let list: unknown = fail(503, 'The data drive is missing.');
    api({ [PATH.games]: () => list });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByText("Couldn't load the games.")).toBeInTheDocument();
    expect(screen.getByText(/The data drive is missing\./)).toBeInTheDocument();
    list = GAMES_LIVE;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('group', { name: 'On now' })).toBeInTheDocument();
  });

  it('a future week turns live in place when its week starts', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const path = '/api/live/2026/6/games';
    let list: unknown = GAMES_FUTURE;
    mockApi({ '/api/team-info': TEAM_INFO, [path]: () => list });
    renderApp(<GameDayTab {...W4} week={6} />);
    expect(await screen.findByRole('heading', { name: 'Game day opens at the first kickoff' })).toBeInTheDocument();
    list = { ...GAMES_LIVE, week: 6 };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300_000);
    });
    expect(await screen.findByRole('group', { name: 'On now' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Game day opens at the first kickoff' })).toBeNull();
  });

  it("the if-stopped table's confidence chips carry the label for screen readers", async () => {
    api({ [PATH.call('100')]: CALL_THIRD });
    const { container } = renderApp(<GameDayTab {...W4} />);
    fireEvent.click(await checkButton());
    await screen.findByText('chance they convert');
    const table = container.querySelector('.iftbl') as HTMLElement;
    expect(within(table).getAllByRole('img', { name: 'Confident' })).toHaveLength(3);
    expect(within(table).getByRole('img', { name: 'Toss-up' })).toBeInTheDocument();
  });
});
