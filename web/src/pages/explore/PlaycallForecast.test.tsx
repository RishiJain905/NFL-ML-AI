import { fireEvent, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { mockApi, renderApp } from '../../test/utils';
import { PlayCallsTab } from '../week/PlayCallsTab';
import { PlayCallTeamPage } from './PlayCallTeamPage';
import { HISTORY } from './playcallFixtures';
import {
  PLAY_CALLS_FORECAST,
  PLAY_CALLS_GRADED,
  PLAY_CALLS_HINT,
  PLAY_CALLS_WEEK1,
  TEAM_DEFENSE_HINT,
  TEAM_FORECAST,
} from './playcallForecastFixtures';

const tab = (week: number) => renderApp(<PlayCallsTab season={2026} week={week} isCurrent lastPublishedWeek={4} />);
const url = (week: number) => `/api/weeks/2026/${week}/play-calls`;
const teamPage = () => renderApp(<PlayCallTeamPage />, { route: '/explore/play-calling/KC', path: '/explore/play-calling/:team' });
const KC_OFF = '/api/playcalling/teams/KC?side=offense';
const KC_DEF = '/api/playcalling/teams/KC?side=defense';

const kcRegion = () => screen.findByRole('region', { name: 'KC offense against JAX defense' });
const row = (region: HTMLElement, name: RegExp) => within(region).getByRole('row', { name });

describe('Play calls tab: the forecast (PC02)', () => {
  it('shows each forecast with its 80% range, the strip and the rate so far, offense calls then defense calls', async () => {
    mockApi({ [url(5)]: PLAY_CALLS_FORECAST });
    const { container } = tab(5);
    const region = await kcRegion();
    const groups = within(region).getAllByRole('columnheader', { name: /'s calls$/ });
    expect(groups.map((g) => g.textContent)).toEqual(["KC's calls", "JAX's calls"]);
    const proe = row(region, /^PROE/);
    expect(proe).toHaveTextContent('+3.1'); // PROE x100, signed points
    expect(proe).toHaveTextContent('−8.0 to +14.2'); // the 80% range
    expect(proe).toHaveTextContent('+4.5'); // so far
    expect(proe).toHaveTextContent('−1.4 pts'); // the change
    const db = row(region, /^Dropback rate/);
    expect(db).toHaveTextContent('61.2%');
    expect(db).toHaveTextContent('49.0% to 73.0%');
    const rush = row(region, /^Pass rushers/);
    expect(rush).toHaveTextContent('4.31'); // rushers with 2 decimals
    expect(rush).toHaveTextContent('+0.11');
    // the strip: a band, the forecast, the ring, the league tick; no actual before grading
    const strip = proe.querySelector('.fs') as HTMLElement;
    expect(strip.querySelector('.fs-band.o')).not.toBeNull();
    expect(strip.querySelector('.fs-f.o')).not.toBeNull();
    expect(strip.querySelector('.fs-sd')).not.toBeNull();
    expect(strip.querySelector('.fs-lg')).not.toBeNull();
    expect(strip.querySelector('.fs-a')).toBeNull();
    expect(row(region, /^Blitz/).querySelector('.fs-band.d')).not.toBeNull(); // a defense's call in --s2
    // waiting chips before the game
    expect(within(proe).getByText('Not played yet')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it("keeps each row's tooltip on its focusable name, the pieces included (the strip is hover-only)", async () => {
    mockApi({ [url(5)]: PLAY_CALLS_FORECAST });
    const { container } = tab(5);
    const region = await kcRegion();
    const strip = container.querySelector('.fs') as HTMLElement;
    expect(strip).toHaveAttribute('aria-hidden', 'true');
    expect(strip).not.toHaveAttribute('tabindex');
    fireEvent.focus(row(region, /^Runs up the middle/).querySelector('.fname') as HTMLElement);
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent('Forecast: 31.0% (80% range 12.0% to 50.0%)');
    expect(tip).toHaveTextContent("the home team's stats crew (JAX) +10.0 pts");
    expect(tip).toHaveTextContent('small sample');
    expect(tip).not.toHaveTextContent('game script'); // only PROE and dropback rate
  });

  it('shows the note, the made-after-kickoff badge with its games and the biggest expected moves', async () => {
    mockApi({ [url(5)]: PLAY_CALLS_FORECAST });
    tab(5);
    expect(await screen.findByText(/a point or two is inside the noise/)).toBeInTheDocument();
    expect(screen.getByText('Made after kickoff (a backfill).')).toBeInTheDocument();
    expect(screen.getByText('The game').closest('details')).toHaveTextContent('KC at JAX, week 5');
    const moves = screen.getByRole('heading', { name: 'Biggest expected moves' }).closest('.card') as HTMLElement;
    expect(within(moves).getAllByRole('listitem')).toHaveLength(2);
    expect(moves).toHaveTextContent('+13.0 pts');
    expect(moves).toHaveTextContent('1.37× half-range');
    expect(moves).toHaveTextContent('+0.11'); // rushers' change in rushers
    expect(screen.queryByRole('heading', { name: 'Biggest matchup shifts' })).toBeNull();
    // nothing graded yet: a waiting note, not a grade card
    expect(screen.getByText(/Waiting for the games:/).parentElement).toHaveTextContent('7 not played yet');
    expect(screen.queryByRole('heading', { name: /How the forecast did/ })).toBeNull();
  });

  it('after the games: the actual on the strip, in range and beat marks, the week grade, FTN chips', async () => {
    mockApi({ [url(4)]: PLAY_CALLS_GRADED });
    const { container } = tab(4);
    const region = await kcRegion();
    const proe = row(region, /^PROE/);
    expect(proe).toHaveTextContent('+9.0'); // the actual
    expect(proe).toHaveTextContent('✓ in range');
    expect(proe).toHaveTextContent('✗ so far closer');
    expect(proe.querySelector('.fs-a')).not.toBeNull();
    expect(row(region, /^Runs up the middle/)).toHaveTextContent('✓ beat so far');
    expect(within(row(region, /^Blitz/)).getByText('FTN not in yet')).toBeInTheDocument();
    expect(row(within(await screen.findByRole('region', { name: 'JAX offense against KC defense' })).getByRole('table'), /^Blitz/).querySelector('.fs-a.out')).not.toBeNull(); // 50% outside 24-48%
    expect(screen.queryByText('Made after kickoff (a backfill).')).toBeNull();
    const grade = screen.getByRole('heading', { name: 'How the forecast did · week 4' }).closest('.card') as HTMLElement;
    expect(grade).toHaveTextContent('6 graded · 1 waiting (1 waiting for FTN)');
    expect(grade).toHaveTextContent('4 of 6');
    expect(grade).toHaveTextContent('83%'); // in range
    expect(grade).toHaveTextContent('7.3 vs 7.6'); // overall MAE in points
    const t = within(grade).getByRole('table', { name: "Week 4's grade by rate" });
    const proeRow = within(t).getByRole('row', { name: /^PROE/ });
    expect(proeRow).toHaveTextContent('5.7 pts');
    expect(proeRow.querySelectorAll('td')[1]).toHaveClass('win'); // the smaller miss in bold
    const rushRow = within(t).getByRole('row', { name: /^Pass rushers/ });
    expect(rushRow).toHaveTextContent('0.16 rushers');
    const runRow = within(t).getByRole('row', { name: /^Runs up the middle/ });
    expect(runRow.querySelectorAll('.win')).toHaveLength(0); // 10.6 vs 10.6: a tie, neither bold
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it("week 1: the baseline column is last season's, and the grade hint names the command", async () => {
    mockApi({ [url(1)]: PLAY_CALLS_WEEK1 });
    tab(1);
    const region = await kcRegion();
    expect(within(region).getByRole('columnheader', { name: /Last season/ })).toBeInTheDocument();
    fireEvent.focus(row(region, /^PROE/).querySelector('.fname') as HTMLElement);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Last season: +4.5');
    expect(screen.getByRole('tooltip')).toHaveTextContent('Change from last season');
    expect(screen.getByText('uv run nfl playcalling grade --season 2026 --week 1')).toBeInTheDocument();
  });

  it('switches to the tendencies so far (PC01) and back', async () => {
    mockApi({ [url(5)]: PLAY_CALLS_FORECAST });
    const { container } = tab(5);
    await kcRegion();
    fireEvent.click(screen.getByRole('button', { name: 'Tendencies so far' }));
    expect(container.querySelector('.mtbl')).not.toBeNull();
    expect(container.querySelector('.ftbl')).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Biggest matchup shifts' })).toBeNull(); // the shifts are the forecast's
    fireEvent.click(screen.getByRole('button', { name: 'Forecast' }));
    expect(container.querySelector('.ftbl')).not.toBeNull();
  });

  it('without a forecast: the PC01 view, its shifts and the command that makes the forecast', async () => {
    mockApi({ [url(5)]: PLAY_CALLS_HINT });
    const { container } = tab(5);
    expect(await screen.findByText(/The forecast arrives in PC02/)).toBeInTheDocument();
    expect(screen.getByText('No forecast for week 5 yet.')).toBeInTheDocument();
    expect(screen.getByText('uv run nfl playcalling forecast --season 2026 --week 5')).toBeInTheDocument();
    expect(screen.getByText("after the week's nfl playcalling build")).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Biggest matchup shifts' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tendencies so far' })).toBeNull();
    expect(container.querySelector('.ftbl')).toBeNull();
  });
});

describe('A team page: the forecast (PC02)', () => {
  beforeEach(() => {
    try {
      localStorage.clear();
    } catch {
      /* fine */
    }
  });

  it("shows the Next game card: this team's calls and the opponent's, with the pieces and a link to the tab", async () => {
    mockApi({ [KC_OFF]: TEAM_FORECAST, '/api/playcalling/teams/KC/history?side=offense': HISTORY });
    const { container } = teamPage();
    const card = (await screen.findByRole('heading', { name: /Next game/ })).closest('.card') as HTMLElement;
    expect(card).toHaveTextContent('week 5');
    expect(within(card).getByRole('link', { name: 'Week 5 · Play calls →' })).toHaveAttribute('href', '/week/2026/5/play-calls');
    const own = within(card).getByRole('table', { name: "KC's offense calls: the forecast" });
    expect(within(own).getAllByRole('row')).toHaveLength(4); // the header + 3
    expect(within(own).getByRole('row', { name: /^PROE/ })).toHaveTextContent('KC +4.0 · opp −0.4 pts · script −0.5 pts');
    expect(within(own).getByRole('row', { name: /^Runs up the middle/ })).toHaveTextContent('KC 21.0% · crew JAX +10.0 pts');
    const opp = within(card).getByRole('table', { name: "JAX's defense calls: the forecast" });
    expect(within(opp).getByRole('row', { name: /^Pass rushers/ })).toHaveTextContent('4.31');
    expect(card).toHaveTextContent('made after kickoff');
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('shows the season card: per rate, games, both misses, closer in N and the in-range share', async () => {
    mockApi({ [KC_OFF]: TEAM_FORECAST });
    teamPage();
    const card = (await screen.findByRole('heading', { name: 'How the forecast has done for KC' })).closest('.card') as HTMLElement;
    expect(card).toHaveTextContent('its offense calls · weeks 1–4');
    expect(card).toHaveTextContent('8 of 12');
    expect(card).toHaveTextContent('6.7 vs 8.0');
    const run = within(card).getByRole('row', { name: /^Runs up the middle/ });
    expect(run).toHaveTextContent('7.3 pts');
    expect(run).toHaveTextContent('11.7 pts');
    expect(run).toHaveTextContent('2 of 4');
    expect(run).toHaveTextContent('100%');
    expect(card).toHaveTextContent('The backtest (every team, 2019–2025) is the fairer test');
  });

  it('a bye: the next game has no forecast yet, so the card names the command; rushers in rushers', async () => {
    try {
      localStorage.setItem('cr.playcallSide', 'defense');
    } catch {
      /* fine */
    }
    mockApi({ [KC_DEF]: TEAM_DEFENSE_HINT });
    teamPage();
    const card = (await screen.findByRole('heading', { name: /Next game/ })).closest('.card') as HTMLElement;
    expect(card).toHaveTextContent('week 6');
    expect(card).toHaveTextContent('No forecast for week 6 yet. The forecast for KC vs LAC appears here once it');
    expect(within(card).getByText('uv run nfl playcalling forecast --season 2026 --week 6')).toBeInTheDocument();
    expect(within(card).queryByRole('table')).toBeNull();
    const rec = screen.getByRole('heading', { name: 'How the forecast has done for KC' }).closest('.card') as HTMLElement;
    expect(rec).toHaveTextContent('its defense calls');
    expect(within(rec).getByRole('row', { name: /^Pass rushers/ })).toHaveTextContent('0.21 rushers');
  });

  it('shows neither card before any forecast or grade (PC01 pages)', async () => {
    const { TEAM } = await import('./playcallFixtures');
    mockApi({ [KC_OFF]: TEAM });
    teamPage();
    expect(await screen.findByRole('heading', { name: /Next game/ })).toBeInTheDocument(); // the game, no forecast
    expect(screen.queryByRole('heading', { name: /How the forecast has done/ })).toBeNull();
  });
});
