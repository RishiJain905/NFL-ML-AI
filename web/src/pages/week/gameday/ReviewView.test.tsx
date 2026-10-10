import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockApi, renderApp } from '../../../test/utils';
import { GameDayTab } from '../GameDayTab';
import { TEAM_INFO } from '../weekFixtures';
import { GAMES_FINAL, GAMES_PAST, PATH } from './gamedayFixtures';
import {
  BOLD,
  REVIEW,
  REVIEW_2025,
  REVIEW_NO_MODELS,
  REVIEW_NO_PLAYS,
  REVIEW_PATH,
  SEASON,
  SEASON_2025,
} from './reviewFixtures';
import { playsByGame, signedPts, smallSampleText, stepWeeks, throughText, weekName, weekShort } from './review';

const W4 = { season: 2026, week: 4, isCurrent: false, lastPublishedWeek: 4 };
// week 4 once Game day has moved on to week 5: a finished week
const PAST_W4 = { ...GAMES_PAST, week: 4, live_week: { season: 2026, week: 5 } };

function api(extra: Record<string, unknown> = {}) {
  return mockApi({
    '/api/team-info': TEAM_INFO,
    [PATH.games]: PAST_W4,
    [REVIEW_PATH.week(2026, 4)]: REVIEW,
    [REVIEW_PATH.season(2026, 4)]: SEASON,
    ...extra,
  });
}
const paths = (f: ReturnType<typeof mockApi>) => f.mock.calls.map(([input]) => String(input));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('review helpers', () => {
  it('names weeks, playoff rounds and signed edges', () => {
    expect(weekName(2026, 4)).toBe('Week 4');
    expect(weekName(2025, 22)).toBe('Super Bowl');
    expect(weekShort(2025, 19)).toBe('WC');
    expect(weekName(2020, 21)).toBe('Super Bowl'); // 17 regular-season weeks then
    expect(throughText(2025, 22)).toBe('the Super Bowl');
    expect(throughText(2026, 4)).toBe('week 4');
    expect(signedPts(-0.0493)).toBe('−4.9');
    expect(signedPts(0.051)).toBe('+5.1');
    expect(signedPts(null)).toBe('—');
  });

  it('steps one week back, and forward only up to Game day’s week', () => {
    expect(stepWeeks(2026, 4, { season: 2026, week: 5 })).toEqual({ prev: 3, next: 5 });
    expect(stepWeeks(2026, 5, { season: 2026, week: 5 })).toEqual({ prev: 4, next: null });
    expect(stepWeeks(2026, 1, { season: 2026, week: 5 })).toEqual({ prev: null, next: 2 });
    expect(stepWeeks(2025, 22, { season: 2026, week: 5 })).toEqual({ prev: 21, next: null });
    expect(stepWeeks(2025, 18, { season: 2026, week: 5 })).toEqual({ prev: 17, next: 19 });
  });

  it('groups plays by game in kickoff order and says how small the early season is', () => {
    const g = playsByGame(REVIEW.games, REVIEW.plays);
    expect(g.map((x) => [x.game.game_id, x.plays.length])).toEqual([
      ['2026_04_AAA_BBB', 2],
      ['2026_04_CCC_DDD', 2],
    ]);
    expect(smallSampleText(2, SEASON.leaderboard)).toMatch(/4 to 12 go spots, so one call moves his rate by 8 to 25 points/);
    expect(smallSampleText(8, SEASON.leaderboard)).toBeNull();
  });
});

describe('GameDayTab: a finished week is its decision review', () => {
  it('shows the week: numbers, boldest and costliest, the five costliest and every 4th down', async () => {
    const f = api();
    const { container } = renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByText('Boldest call')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: "Every 4th down: the coach's call next to the bot's" })).toBeInTheDocument();
    expect(screen.getByText('Decision review · 2026 Week 4')).toBeInTheDocument();
    expect(screen.getByText('test-bundle')).toBeInTheDocument();
    expect(screen.getByText("from nfl live review's file")).toBeInTheDocument();
    // the numbers
    expect(screen.getByText('2 games · 1 wiped out by a penalty · 1 fake')).toBeInTheDocument();
    expect(screen.getByText('2 of 4 · 1 toss-ups')).toBeInTheDocument();
    const lost = screen.getByText('Expected wins given up').closest('.tile') as HTMLElement;
    expect(within(lost).getByText('0.05')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Coaches: Go for it 2' })).toBeInTheDocument();
    // the highlights
    const bold = screen.getByText('Boldest call').closest('.hl') as HTMLElement;
    expect(within(bold).getByText('4th & 13 at CCC 10')).toBeInTheDocument();
    expect(within(bold).getByText('26%')).toBeInTheDocument();
    expect(within(bold).getByText('the bot agreed (+2.6)')).toBeInTheDocument();
    const cost = screen.getByText('Costliest call').closest('.hl') as HTMLElement;
    expect(within(cost).getByText('4.9')).toBeInTheDocument();
    expect(within(cost).getByText('BBB ball at own 14')).toBeInTheDocument();
    const top = screen.getByRole('heading', { name: 'The five costliest' }).closest('.card') as HTMLElement;
    expect(within(top).getAllByRole('button')).toHaveLength(2);
    // every 4th down, by game, with the edge and the tags
    expect(screen.getByRole('region', { name: 'AAA at BBB' })).toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: 'cost 4.9 points against the bot' }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('img', { name: 'gained 2.6 points over the next best' }).length).toBeGreaterThan(0);
    expect(container.querySelectorAll('details.rv-row')).toHaveLength(4);
    expect(screen.getByText('fake')).toBeInTheDocument();
    expect(screen.getAllByText('wiped').length).toBeGreaterThan(0);
    // the season isn't asked for until the switch
    expect(paths(f)).toContain(REVIEW_PATH.week(2026, 4));
    expect(paths(f).some((p) => p.includes('season-review'))).toBe(false);
  });

  it("renders nflverse's play text as text, never HTML", async () => {
    api();
    const { container } = renderApp(<GameDayTab {...W4} />);
    await screen.findByText('Boldest call');
    expect(screen.getAllByText(BOLD.desc, { exact: false }).length).toBeGreaterThan(0);
    expect(container.querySelector('.desc b')).toBeNull();
  });

  it('filters to the disagreements and opens a row from the highlights', async () => {
    api();
    const { container } = renderApp(<GameDayTab {...W4} />);
    await screen.findByText('Boldest call');
    fireEvent.click(screen.getByRole('button', { name: 'Disagreements only 2' }));
    expect(container.querySelectorAll('details.rv-row')).toHaveLength(2);
    expect(screen.queryByRole('region', { name: 'CCC at DDD' })).toBeNull();
    // the boldest call agreed with the bot: jumping to it brings every row back and opens it
    const bold = screen.getByText('Boldest call').closest('.hl') as HTMLElement;
    fireEvent.click(within(bold).getByRole('button', { name: 'Show it in the list' }));
    await waitFor(() => expect(container.querySelectorAll('details.rv-row')).toHaveLength(4));
    await waitFor(() => expect((container.querySelector('#p-2026_04_CCC_DDD-4319') as HTMLDetailsElement).open).toBe(true));
  });

  it('a row opens to the three options, the odds and the coach', async () => {
    api();
    const { container } = renderApp(<GameDayTab {...W4} />);
    await screen.findByText('Boldest call');
    const row = container.querySelector('#p-2026_04_AAA_BBB-317') as HTMLDetailsElement;
    const opts = within(row).getByRole('list', { name: 'AAA win chance after each option' });
    expect(within(opts).getAllByRole('listitem')).toHaveLength(3);
    expect(within(opts).getByText('67.8%')).toBeInTheDocument();
    expect(within(row).getByText('45% from 62 yards')).toBeInTheDocument();
    const w = container.querySelector('#p-2026_04_CCC_DDD-4319') as HTMLDetailsElement;
    expect(within(w).getByText('not an option')).toBeInTheDocument();
    expect(within(w).getByText('out of range (107 yards)')).toBeInTheDocument();
  });

  it('steps to the previous and next weeks and links to Game day’s week', async () => {
    api();
    renderApp(<GameDayTab {...W4} />);
    await screen.findByText('Boldest call');
    expect(screen.getByRole('link', { name: '‹ Week 3' })).toHaveAttribute('href', '/week/2026/3/game-day');
    expect(screen.getByRole('link', { name: 'Week 5 ›' })).toHaveAttribute('href', '/week/2026/5/game-day');
    expect(screen.getByRole('link', { name: 'Game day is on week 5' })).toBeInTheDocument();
  });

  it('the season switch: leaderboard, calibration, conversion, field goals, the trend', async () => {
    const f = api();
    renderApp(<GameDayTab {...W4} />);
    await screen.findByText('Boldest call');
    fireEvent.click(screen.getByRole('button', { name: 'Season' }));
    expect(await screen.findByRole('heading', { name: 'Coach aggressiveness' })).toBeInTheDocument();
    expect(paths(f)).toContain(REVIEW_PATH.season(2026, 4));
    expect(screen.getByRole('heading', { name: 'The 2026 season through week 4' })).toBeInTheDocument();
    expect(screen.getByText('Early-season samples are small.')).toBeInTheDocument();
    const table = within(screen.getByRole('heading', { name: 'Coach aggressiveness' }).closest('.card') as HTMLElement).getByRole('table');
    const rows = within(table).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('Alex Alpha');
    expect(within(table).getByText('League')).toBeInTheDocument();
    // sort by wins given up: Blake Beta (0.30) first
    fireEvent.click(within(table).getByRole('button', { name: 'Wins given up' }));
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('Blake Beta');
    // the charts
    expect(screen.getByRole('group', { name: "Calibration of the bot's win probability, 2026" })).toBeInTheDocument();
    expect(screen.getByText('0.168')).toBeInTheDocument();
    expect(screen.getByText('Tries convert more often than the bot says.')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Third-down conversion: predicted vs actual by distance' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Fourth-down conversion: predicted vs actual by distance' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Field goals: made vs predicted' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Week by week' })).toBeInTheDocument();
    // a bin's tooltip on keyboard focus
    const bin = screen.getAllByRole('img', { name: /^Said 30–40%/ })[0];
    fireEvent.focus(bin);
    expect(screen.getByRole('tooltip')).toHaveTextContent('The bot: said 35.0%, won 36.0% (103 snaps)');
    fireEvent.blur(bin);
    const col = screen.getByRole('img', { name: /^4th & 3, The bot said 58%, Converted 60% \(3 tries\)/ });
    fireEvent.focus(col);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Fewer than 10: read loosely');
    fireEvent.blur(col);
    // small 4th-down samples are dashed
    expect(document.querySelectorAll('[data-small="true"]').length).toBe(8);
  });

  it("pairs a bin with vegas_wp's same bin even when the lists skip different empty bins", async () => {
    const wp = SEASON.calibration!.wp!;
    // vegas_wp has no snaps under 20%: by index its 30-40% row would be the bot's 50-60% (Sol review)
    const sparse = { ...SEASON, calibration: { ...SEASON.calibration!, wp: { ...wp, vegas: wp.vegas.slice(2) } } };
    api({ [REVIEW_PATH.season(2026, 4)]: sparse });
    renderApp(<GameDayTab {...W4} />);
    await screen.findByText('Boldest call');
    fireEvent.click(screen.getByRole('button', { name: 'Season' }));
    await screen.findByRole('heading', { name: 'Coach aggressiveness' });
    const v = wp.vegas.find((b) => Math.abs(b.bin_lo - 0.3) < 1e-9)!;
    const bin = screen.getAllByRole('img', { name: /^Said 30–40%/ })[0];
    fireEvent.focus(bin);
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      `vegas_wp: said ${((v.mean_pred ?? 0) * 100).toFixed(1)}%, won ${((v.mean_outcome ?? 0) * 100).toFixed(1)}%`,
    );
    fireEvent.blur(bin);
  });

  it('2025 is in-sample: the warning on both views', async () => {
    mockApi({
      '/api/team-info': TEAM_INFO,
      '/api/live/2025/22/games': { ...PAST_W4, season: 2025, week: 22 },
      [REVIEW_PATH.week(2025, 22)]: REVIEW_2025,
      [REVIEW_PATH.season(2025, 22)]: SEASON_2025,
    });
    renderApp(<GameDayTab season={2025} week={22} isCurrent={false} lastPublishedWeek={4} />);
    expect(await screen.findByText('In-sample season.')).toBeInTheDocument();
    expect(screen.getByText('Decision review · 2025 Super Bowl')).toBeInTheDocument();
    expect(screen.getByText('No long shot this week: no go-for-it in a game still in doubt when kicking was a real option.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /›$/ })).toBeNull(); // nothing after the Super Bowl
    fireEvent.click(screen.getByRole('button', { name: 'Season' }));
    expect(await screen.findByRole('heading', { name: 'The 2025 season through the Super Bowl' })).toBeInTheDocument();
    expect(screen.getByText('In-sample season.')).toBeInTheDocument();
    expect(screen.queryByText('Early-season samples are small.')).toBeNull();
  });

  it("a week without plays: the reader's message, the finals and the season so far", async () => {
    api({ [REVIEW_PATH.week(2026, 4)]: REVIEW_NO_PLAYS, [REVIEW_PATH.season(2026, 4)]: { ...SEASON, through_week: 3 } });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByRole('heading', { name: "Week 4's review isn't ready yet" })).toBeInTheDocument();
    expect(screen.getByText(/come in with the Tuesday run/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Week 4 finals' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'See the season so far' }));
    expect(await screen.findByText("Week 4's plays aren't in yet:")).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'The 2026 season through week 3' })).toBeInTheDocument();
  });

  it('without the models: the reader’s message as text', async () => {
    api({ [REVIEW_PATH.week(2026, 4)]: REVIEW_NO_MODELS });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByText("The decision models aren't trained yet (LD00): run `uv run nfl live train --promote`.")).toBeInTheDocument();
    expect(screen.queryByText('Boldest call')).toBeNull();
  });

  it('says the review is being built while it loads', async () => {
    // a review fetch that never settles: the loading card stays
    const f = mockApi({ '/api/team-info': TEAM_INFO, [PATH.games]: PAST_W4 });
    const real = f.getMockImplementation()!;
    f.mockImplementation((input, init) => (String(input).endsWith('/review') ? new Promise(() => {}) : real(input, init)));
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByText(/Scoring the week's 4th downs/)).toBeInTheDocument();
  });
});

describe('GameDayTab: the week being played, every game final', () => {
  it('opens the decision review when it is ready, and goes back to the games', async () => {
    mockApi({ '/api/team-info': TEAM_INFO, [PATH.games]: GAMES_FINAL, [REVIEW_PATH.week(2026, 4)]: REVIEW });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByRole('heading', { name: 'Every week-4 game is final' })).toBeInTheDocument();
    expect(await screen.findByText(/The decision review is ready: 4 4th downs/)).toBeInTheDocument();
    const open = screen.getByRole('button', { name: 'Open the decision review' });
    expect(open).toBeEnabled();
    fireEvent.click(open);
    expect(await screen.findByText('Boldest call')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /›$/ })).toBeNull(); // week 4 is Game day's week: no next
    fireEvent.click(screen.getByRole('button', { name: "← The week's games" }));
    expect(await screen.findByRole('heading', { name: 'Every week-4 game is final' })).toBeInTheDocument();
  });

  it("keeps the button off until Tuesday's run brings the plays", async () => {
    mockApi({ '/api/team-info': TEAM_INFO, [PATH.games]: GAMES_FINAL, [REVIEW_PATH.week(2026, 4)]: REVIEW_NO_PLAYS });
    renderApp(<GameDayTab {...W4} />);
    expect(await screen.findByText(/Ready after Tuesday's run/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open the decision review' })).toBeDisabled();
    expect(screen.getByText(/aren't in the curated data yet/)).toBeInTheDocument();
  });
});
