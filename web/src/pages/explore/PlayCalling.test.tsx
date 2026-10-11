import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Sidebar } from '../../components/Sidebar';
import { mockApi, renderApp, withStatus } from '../../test/utils';
import { SEASON_TEAM_INFO } from '../season/seasonFixtures';
import { PlayCallTeamPage } from './PlayCallTeamPage';
import { PlayCallingPage } from './PlayCallingPage';
import { HISTORY, TEAM, TEAMS, TEAMS_NOT_BUILT, TEAM_DEFENSE, TEAM_NOT_BUILT, cell } from './playcallFixtures';

const GRID = '/api/playcalling/teams';
const KC_OFF = '/api/playcalling/teams/KC?side=offense';
const KC_DEF = '/api/playcalling/teams/KC?side=defense';
const KC_HIST = '/api/playcalling/teams/KC/history?side=offense';

const grid = (route = '/explore/play-calling') => renderApp(<PlayCallingPage />, { route, path: '/explore/play-calling' });
const teamPage = (route = '/explore/play-calling/KC') => renderApp(<PlayCallTeamPage />, { route, path: '/explore/play-calling/:team' });
const calls = (fetchMock: ReturnType<typeof mockApi>) => fetchMock.mock.calls.map((c) => String(c[0]));

describe('Explore → Play calling: the teams grid', () => {
  it('shows 32 tiles with the signature numbers, n and the as-of week', async () => {
    mockApi({ [GRID]: TEAMS, '/api/team-info': SEASON_TEAM_INFO });
    const { container } = grid();
    await screen.findByText('32 teams');
    expect(container.querySelectorAll('.ptile')).toHaveLength(32);
    expect(screen.getByText('2026 · as of week 5 · weeks 1–4 played')).toBeInTheDocument();
    expect(screen.getByText('FTN waiting: 1')).toHaveAttribute('title', expect.stringContaining('ATL at NO, week 4'));
    const ari = container.querySelectorAll('.ptile')[0];
    expect(within(ari as HTMLElement).getByRole('link', { name: /open its play-calling page/ })).toHaveAttribute('href', '/explore/play-calling/ARI');
    const sigs = ari.querySelectorAll('.psig');
    expect(sigs).toHaveLength(2);
    expect(sigs[0]).toHaveTextContent('OffensePROE');
    expect(sigs[0]).toHaveTextContent('−10.0'); // PROE x100, signed
    expect(sigs[0]).toHaveTextContent('lg −1.9');
    expect(sigs[0]).toHaveTextContent('n 140');
    expect(sigs[1]).toHaveTextContent('DefenseBlitz');
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('sorts by any column, and shows a non-signature column on each tile', async () => {
    mockApi({ [GRID]: TEAMS });
    const { container } = grid();
    await screen.findByText('32 teams');
    const first = () => container.querySelector('.ptile .pt-h a')?.textContent;
    expect(first()).toBe('ARI');
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'offense.proe' } });
    expect(first()).toBe('WAS'); // highest first
    fireEvent.click(screen.getByRole('button', { name: 'Highest first' }));
    expect(first()).toBe('ARI');
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'offense.play_action_rate' } });
    expect(container.querySelector('.ptile .psort')).toHaveTextContent('Offense · Play-action');
  });

  it('greys a small sample: the table hatches its cell, keeps n and explains it on focus', async () => {
    mockApi({ [GRID]: TEAMS });
    const { container } = grid();
    await screen.findByText('32 teams');
    fireEvent.click(screen.getByRole('button', { name: 'Table' }));
    const link = screen.getByRole('link', { name: 'MIN: open its play-calling page' });
    expect(link).toHaveAttribute('href', '/explore/play-calling/MIN');
    const min = link.closest('tr') as HTMLTableRowElement;
    const pa = min.querySelectorAll('td')[2];
    expect(pa).toHaveClass('small');
    expect(pa).toHaveTextContent('n 12');
    expect(pa.getAttribute('style')).toBeNull();
    expect(pa).not.toHaveAttribute('title'); // a focusable tooltip, not a native title (Sol review)
    const target = within(pa).getByRole('img');
    expect(target).toHaveAttribute('tabindex', '0');
    fireEvent.focus(target);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Small sample: under 20 dropbacks');
    fireEvent.blur(target);
    expect(container.querySelector('tr.lgrow')).toHaveTextContent('−1.9');
    // sortable headers
    fireEvent.click(screen.getByRole('button', { name: /Sort by Defense: Blitz/ }));
    expect(container.querySelectorAll('tbody tr')[1]).toHaveTextContent('ARI'); // ARI blitzes most (row 1: after the league row)
  });

  it('switches the season through ?season=', async () => {
    const fetchMock = mockApi({ [GRID]: TEAMS, [`${GRID}?season=2025`]: { ...TEAMS, season: 2025 } });
    grid();
    await screen.findByText('32 teams');
    fireEvent.click(screen.getByRole('button', { name: '2025' }));
    await waitFor(() => expect(calls(fetchMock)).toContain(`${GRID}?season=2025`));
  });

  it('says how to build the tables when they are missing', async () => {
    mockApi({ [GRID]: TEAMS_NOT_BUILT });
    grid();
    expect(await screen.findByText('No play-calling tables for 2026')).toBeInTheDocument();
    expect(screen.getByText('uv run nfl playcalling build --season 2026', { selector: '.cmd' })).toBeInTheDocument();
  });

  it('shows a loading block, then an error notice when the API fails', async () => {
    mockApi({ [GRID]: withStatus(500, { error: { code: 'server_error', message: 'boom' } }) });
    grid();
    expect(screen.getByRole('status', { name: 'Loading the teams' })).toBeInTheDocument();
    expect(await screen.findByText("Couldn't load the play-calling teams.")).toBeInTheDocument();
  });
});

describe('Explore → Play calling: a team page', () => {
  it('shows the summary, the identity with n and the league, the situations, the field, the line and the weeks', async () => {
    mockApi({ [KC_OFF]: TEAM, '/api/team-info': SEASON_TEAM_INFO });
    const { container } = teamPage();
    await screen.findByText(/KC passes 5.6 points more/);
    expect(await screen.findByRole('heading', { name: 'Kansas City Chiefs', level: 1 })).toBeInTheDocument();
    expect(screen.getByText('Chiefs offense · in five seconds')).toBeInTheDocument();
    const proe = screen.getByRole('img', { name: /^Pass rate over expected \(neutral\) · Season/ });
    expect(proe).toHaveTextContent('+3.7');
    expect(proe).toHaveTextContent('n 140');
    expect(proe).toHaveTextContent('88th');
    expect(screen.getByRole('table', { name: 'KC offense by down & distance' })).toBeInTheDocument();
    expect(container.querySelectorAll('.fieldbox')).toHaveLength(2);
    expect(screen.getByText('Week by week')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Next: at JAX · week 5/ })).toHaveAttribute('href', '/week/2026/5/play-calls');
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('greys small samples: the last-4 window and the 4th-down cells', async () => {
    mockApi({ [KC_OFF]: TEAM });
    const { container } = teamPage();
    await screen.findByText(/KC passes 5.6 points more/);
    fireEvent.click(screen.getByRole('button', { name: 'Last 4' }));
    const pa = screen.getByRole('img', { name: /^Play-action per dropback · Last 4/ });
    expect(pa).toHaveClass('small');
    expect(pa).toHaveTextContent('n 12 · small');
    const fourth = screen.getByRole('row', { name: /4th down/ });
    expect(within(fourth).getAllByRole('cell').every((c) => c.classList.contains('small'))).toBe(true);
    // the window switch only offers the windows the answer has
    expect(screen.getByRole('button', { name: 'Last season · 2025' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Field zone' }));
    expect(screen.getByRole('table', { name: 'KC offense by field zone' })).toHaveTextContent('Red zone');
    expect(container.querySelector('.wintool')).toHaveTextContent('The last four games');
  });

  it('offers no Last 4 while a team has four games or fewer (it equals the season)', async () => {
    mockApi({ [KC_OFF]: { ...TEAM, games: 4 } });
    const { container } = teamPage();
    await screen.findByText(/KC passes 5.6 points more/);
    expect(screen.queryByRole('button', { name: 'Last 4' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Season · 4 g' })).toHaveAttribute('aria-pressed', 'true');
    expect(container.querySelector('.wintool')).toHaveTextContent('Last 4 is the same until a fifth game.');
  });

  it('says so when no rate is solid enough for a summary yet', async () => {
    mockApi({ [KC_OFF]: { ...TEAM, summary: [] } });
    teamPage();
    expect(await screen.findByText(/No clear tendencies yet/)).toBeInTheDocument();
  });

  it('remembers the Offense / Defense switch in this browser', async () => {
    const fetchMock = mockApi({ [KC_OFF]: TEAM, [KC_DEF]: TEAM_DEFENSE });
    const first = teamPage();
    await screen.findByText(/KC passes 5.6 points more/);
    fireEvent.click(screen.getByRole('button', { name: 'Defense' }));
    expect(await screen.findByText(/KC blitzes on 39.0%/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Defense' })).toHaveAttribute('aria-pressed', 'true');
    expect(calls(fetchMock)).toContain(KC_DEF);
    expect(localStorage.getItem('cr.playcallSide')).toBe('defense');
    expect(screen.getByText(/the second depends on the offenses it faced/)).toBeInTheDocument();
    first.unmount();
    teamPage();
    expect(await screen.findByText(/KC blitzes on 39.0%/)).toBeInTheDocument();
  });

  it('loads History only when its section is opened', async () => {
    const fetchMock = mockApi({ [KC_OFF]: TEAM, [KC_HIST]: HISTORY });
    teamPage();
    await screen.findByText(/KC passes 5.6 points more/);
    expect(calls(fetchMock)).not.toContain(KC_HIST);
    const btn = screen.getByRole('button', { name: 'Show history' });
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(btn);
    expect(await screen.findByText(/research data\): 2026 arrives about February 2027/)).toBeInTheDocument();
    expect(calls(fetchMock)).toContain(KC_HIST);
    expect(screen.getByText('History, 2023–2025')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getAllByRole('img', { name: /12 personnel · 2025/ })[0]).toHaveTextContent('31.0%');
  });

  it("draws History's bars for a row on one scale, so the seasons keep their order", async () => {
    // league 10% then 30%; the team 20% then 30%: scaled per cell, the first bar drew longer (80% vs 75%)
    const row = HISTORY.groups[0].rows[0];
    const hist = {
      ...HISTORY,
      seasons: [2024, 2025],
      groups: [{ ...HISTORY.groups[0], rows: [{ ...row, seasons: { '2024': cell(0.2, 0.1, 900), '2025': cell(0.3, 0.3, 900) } }] }],
    };
    mockApi({ [KC_OFF]: TEAM, [KC_HIST]: hist });
    const { container } = teamPage();
    await screen.findByText(/KC passes 5.6 points more/);
    expect(screen.getByText(/is published after each season; this shows the newest three seasons from 2023/)).toBeInTheDocument();
    expect(screen.queryByText(/isn't out yet/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show history' }));
    await screen.findByText('History, 2024–2025');
    const widths = Array.from(container.querySelectorAll<HTMLElement>('.htbl .rb')).map((b) => parseFloat(b.style.width));
    expect(widths).toHaveLength(2);
    expect(widths[0]).toBeLessThan(widths[1]);
    expect(widths[1]).toBe(100); // the shared top is the row's largest value (30%)
  });

  it('shows the not-built state, a bad team and an error', async () => {
    mockApi({ [KC_OFF]: TEAM_NOT_BUILT, '/api/playcalling/teams/XX?side=offense': withStatus(404, { error: { code: 'not_found', message: 'no team' } }) });
    const a = teamPage();
    expect(await screen.findByText('No play-calling tables for 2026')).toBeInTheDocument();
    a.unmount();
    const b = teamPage('/explore/play-calling/k1');
    expect(screen.getByText('No such team')).toBeInTheDocument();
    b.unmount();
    teamPage('/explore/play-calling/XX');
    expect(await screen.findByText("Couldn't load XX's play calling.")).toBeInTheDocument();
  });
});

describe('the sidebar', () => {
  it('has Explore → Play calling between Season and System', () => {
    mockApi();
    renderApp(<Sidebar />);
    const navs = screen.getAllByRole('navigation').map((n) => n.getAttribute('aria-label'));
    expect(navs.slice(-3)).toEqual(['Season', 'Explore', 'System']);
    expect(within(screen.getByRole('navigation', { name: 'Explore' })).getByRole('link', { name: 'Play calling' })).toHaveAttribute(
      'href',
      '/explore/play-calling',
    );
  });
});
