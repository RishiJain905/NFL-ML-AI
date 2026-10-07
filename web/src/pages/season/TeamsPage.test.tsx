import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { TeamsResponse } from '../../api/types';
import { mockApi, renderApp } from '../../test/utils';
import { TeamsPage } from './TeamsPage';
import { SEASON_TEAM_INFO, TEAMS } from './seasonFixtures';

function api(teams: TeamsResponse = TEAMS) {
  return mockApi({ '/api/teams': teams, '/api/team-info': SEASON_TEAM_INFO });
}
const rows = (c: HTMLElement) => Array.from(c.querySelectorAll<HTMLTableRowElement>('tr.teamrow'));

describe('TeamsPage', () => {
  it('renders the movers and all 32 teams in rank order with sparklines and signed EPA', async () => {
    api();
    const { container } = renderApp(<TeamsPage />);
    await screen.findByText('Power rankings');
    expect(screen.getByText('2026 · Elo and ratings entering week 5')).toBeInTheDocument();
    expect(rows(container)).toHaveLength(32);
    const first = rows(container)[0];
    expect(within(first).getByText('1')).toBeInTheDocument();
    expect(within(first).getByText(String(Math.round(TEAMS.teams[0].elo)))).toBeInTheDocument();
    expect(within(first).getByText('+0.180')).toBeInTheDocument();
    expect(within(first).getByRole('img', { name: /Elo by week: week 1/ })).toBeInTheDocument();
    const risers = await screen.findByRole('list', { name: 'Biggest Elo risers' });
    expect(within(risers).getAllByRole('listitem')).toHaveLength(5);
    const fallers = screen.getByRole('list', { name: 'Biggest Elo fallers' });
    expect(fallers.textContent).toMatch(/−\d+\.\d/);
    // the move column: ▲ / ▼ / — for each row, matching prev_rank − rank
    const moved = TEAMS.teams.find((t) => t.prev_rank != null && t.prev_rank > t.rank)!;
    const movedRow = rows(container).find((r) =>
      r.getAttribute('aria-label')?.startsWith(`${moved.team},`),
    )!;
    expect(within(movedRow).getByText(`▲ ${moved.prev_rank! - moved.rank}`)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('filters by conference', async () => {
    api();
    const { container } = renderApp(<TeamsPage />);
    await screen.findByText('Power rankings');
    fireEvent.click(screen.getByRole('button', { name: 'AFC' }));
    expect(screen.getByRole('button', { name: 'AFC' })).toHaveAttribute('aria-pressed', 'true');
    expect(rows(container)).toHaveLength(16);
    expect(container.querySelector('tbody')).not.toHaveTextContent('NFC East');
    fireEvent.click(screen.getByRole('button', { name: 'NFC' }));
    expect(rows(container)).toHaveLength(16);
    expect(container.querySelector('tbody')).not.toHaveTextContent('AFC West');
    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    expect(rows(container)).toHaveLength(32);
  });

  it('opens a team detail row by click and by keyboard', async () => {
    api();
    const { container } = renderApp(<TeamsPage />);
    await screen.findByText('Power rankings');
    const buf = rows(container).find((r) => r.getAttribute('aria-label')?.startsWith('BUF,'))!;
    expect(buf).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(buf);
    expect(buf).toHaveAttribute('aria-expanded', 'true');
    const detail = container.querySelector('#team-detail-BUF') as HTMLElement;
    expect(detail).toHaveTextContent('Elo, weeks 1–5');
    expect(detail).toHaveTextContent('Pass vs rush (net EPA/play)');
    expect(detail).toHaveTextContent('model gave Bills 71%');
    expect(detail).toHaveTextContent('Week 6');
    fireEvent.click(buf);
    expect(container.querySelector('#team-detail-BUF')).toBeNull();

    const kc = rows(container).find((r) => r.getAttribute('aria-label')?.startsWith('KC,'))!;
    kc.focus();
    fireEvent.keyDown(kc, { key: 'Enter' });
    expect(kc).toHaveAttribute('aria-expanded', 'true');
    expect(container.querySelector('#team-detail-KC')).toHaveTextContent('This week');
    expect(container.querySelector('#team-detail-KC')).toHaveTextContent('bye');
    fireEvent.keyDown(kc, { key: ' ' });
    expect(kc).toHaveAttribute('aria-expanded', 'false');
  });

  it('shows an empty state for a season without ratings', async () => {
    api({ season: 2027, week: null, weeks: [], teams: [], risers: [], fallers: [] });
    renderApp(<TeamsPage />);
    expect(await screen.findByText('No ratings for 2027 yet')).toBeInTheDocument();
  });
});
