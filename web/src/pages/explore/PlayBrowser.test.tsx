import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { mockApi, renderApp } from '../../test/utils';
import { PlayBrowser } from './PlayBrowser';
import { PlayCallTeamPage } from './PlayCallTeamPage';
import { TEAM } from './playcallFixtures';
import {
  CLIP,
  CLIP_2,
  CLIP_NO_LIBRARY,
  DIAGRAM_RES,
  DIAGRAM_RUN,
  DIAGRAM_WITH_REAL,
  LABEL,
  PLAYS,
  PLAYS_DEFENSE,
  PLAYS_LIBRARY,
  PLAYS_NOT_BUILT,
  PLAYS_NO_WEEK,
  PLAYS_RUNS,
  PLAYS_WEEK1,
  PLAYS_ZERO,
  REAL_LABEL,
} from './playbrowserFixtures';

const LIST = '/api/playcalling/teams/KC/plays?side=offense&season=2026';
const DIAG = (id: number, game = '2026_02_IND_KC') => `/api/playcalling/plays/${game}/${id}/diagram`;
const CLIP_URL = (i: number) => `/api/playcalling/concepts/play_action_deep?example=${i}`;

const calls = (f: ReturnType<typeof mockApi>) => f.mock.calls.map((c) => String(c[0]));
const browser = (side: 'offense' | 'defense' = 'offense') => renderApp(<PlayBrowser team="KC" season={2026} side={side} />);
const count = () => document.querySelector('.pbcount')?.textContent ?? '';
const listed = () => waitFor(() => expect(count()).toMatch(/^Showing/));
const open = () => fireEvent.click(screen.getByRole('button', { name: 'Show plays' }));
const rows = () => screen.getAllByRole('button', { pressed: undefined }).filter((b) => b.classList.contains('prow'));

describe('PC03: the play browser', () => {
  it('fetches nothing until opened, then lists the week with counts and draws the first play', async () => {
    const f = mockApi({ [LIST]: PLAYS, [DIAG(100)]: DIAGRAM_RES });
    browser();
    expect(calls(f).some((c) => c.includes('/plays'))).toBe(false);
    open();
    await listed();
    expect(count()).toBe('Showing 3 of 3 plays');
    expect(rows()).toHaveLength(3);
    const first = rows()[0];
    expect(first).toHaveAttribute('aria-pressed', 'true');
    expect(first).toHaveTextContent('1st & 10');
    expect(first).toHaveTextContent('KC 25');
    expect(first).toHaveTextContent('PA');
    expect(first).toHaveTextContent('Complete, +24 (22 air yards), first down');
    expect(first).toHaveTextContent('+1.80');
    expect(first).toHaveTextContent('15-A.Passer pass deep middle');
    expect(rows()[2]).toHaveTextContent('not charted');
    expect((screen.getByRole('combobox', { name: 'Game' }) as HTMLSelectElement).value).toBe('2');
    // the player for the selected play, labelled, not playing (nobody picked it yet)
    expect(await screen.findByTestId('diagram-label')).toHaveTextContent(LABEL);
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Known' }) ?? null).toBeTruthy();
    // no library on this computer: a quiet line, no button
    expect(screen.getByText(/need the Big Data Bowl library on this computer/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /See a real one/ })).toBeNull();
  });

  it('picking a play draws it and plays it; arrow keys move through the list', async () => {
    const f = mockApi({ [LIST]: PLAYS, [DIAG(100)]: DIAGRAM_RES, [DIAG(120)]: DIAGRAM_RUN, [DIAG(140)]: DIAGRAM_RES });
    browser();
    open();
    await listed();
    fireEvent.click(rows()[1]);
    expect(rows()[1]).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(calls(f)).toContain(DIAG(120)));
    await screen.findByRole('button', { name: 'Pause' }); // autoplay on a pick
    expect(screen.getByTestId('diagram-label')).toHaveTextContent(LABEL);
    fireEvent.keyDown(rows()[1], { key: 'ArrowDown' });
    expect(rows()[2]).toHaveAttribute('aria-pressed', 'true');
    expect(document.activeElement).toBe(rows()[2]);
    await waitFor(() => expect(calls(f)).toContain(DIAG(140)));
    fireEvent.keyDown(rows()[2], { key: 'Home' });
    expect(rows()[0]).toHaveAttribute('aria-pressed', 'true');
  });

  it('the game picker and the filters change the query', async () => {
    const f = mockApi({
      [LIST]: PLAYS,
      [`${LIST}&week=1`]: PLAYS_WEEK1,
      [`${LIST}&type=run`]: PLAYS_RUNS,
      [DIAG(100)]: DIAGRAM_RES,
      [DIAG(120)]: DIAGRAM_RUN,
      [DIAG(55, '2026_01_KC_DEN')]: DIAGRAM_RES,
    });
    browser();
    open();
    await listed();
    fireEvent.click(screen.getByRole('button', { name: 'Runs' }));
    await waitFor(() => expect(calls(f)).toContain(`${LIST}&type=run`));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(count()).toBe('Showing 1 of 3 plays');
    expect(screen.getByRole('button', { name: 'Runs' })).toHaveAttribute('aria-pressed', 'true');
    // the same chip again clears it
    fireEvent.click(screen.getByRole('button', { name: 'Runs' }));
    await waitFor(() => expect(rows()).toHaveLength(3));
    fireEvent.change(screen.getByRole('combobox', { name: 'Game' }), { target: { value: '1' } });
    await waitFor(() => expect(calls(f)).toContain(`${LIST}&week=1`));
    await waitFor(() => expect(rows()[0]).toHaveTextContent('Week one pass.'));
    expect(screen.getByRole('button', { name: 'Previous game' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Next game' }));
    await waitFor(() => expect(calls(f)).toContain(`${LIST}&week=2`));
  });

  it('a filter with no plays says so and offers to clear', async () => {
    mockApi({ [LIST]: PLAYS, [`${LIST}&down=4`]: PLAYS_ZERO, [DIAG(100)]: DIAGRAM_RES });
    browser();
    open();
    await listed();
    fireEvent.click(screen.getByRole('button', { name: '4th' }));
    expect(await screen.findByText('No plays match these filters')).toBeInTheDocument();
    expect(screen.getByText(/none is on 4th down/)).toBeInTheDocument();
    expect(screen.queryByTestId('diagram-label')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0]);
    await waitFor(() => expect(rows()).toHaveLength(3));
  });

  it('not built and no week: the reader messages, with the command and the game picker', async () => {
    mockApi({ [LIST]: PLAYS_NOT_BUILT });
    const { unmount } = browser();
    open();
    expect(await screen.findByText('No play-by-play tables for 2026')).toBeInTheDocument();
    expect(screen.getByText('uv run nfl playcalling build --season 2026')).toBeInTheDocument();
    unmount();
    mockApi({ [LIST]: PLAYS_NO_WEEK });
    browser();
    open();
    expect(await screen.findByText('No plays that week')).toBeInTheDocument();
    expect(screen.getByText(/a bye, or not played yet/)).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Game' })).toBeInTheDocument();
  });

  it('on defense it asks for the plays run against the team', async () => {
    const f = mockApi({
      '/api/playcalling/teams/KC/plays?side=defense&season=2026': PLAYS_DEFENSE,
      [DIAG(300)]: { ...DIAGRAM_RES, diagram: { ...DIAGRAM_RES.diagram, play: { ...DIAGRAM_RES.diagram.play, offense: 'IND', defense: 'KC' } } },
    });
    browser('defense');
    expect(screen.getByText("The plays run against KC's defense, one game at a time")).toBeInTheDocument();
    open();
    await listed();
    expect(calls(f)).toContain('/api/playcalling/teams/KC/plays?side=defense&season=2026');
    expect(rows()[0]).toHaveTextContent('IND pass against KC.');
    expect(await screen.findByTestId('diagram-label')).toHaveTextContent(LABEL);
  });

  it('"See a real one" plays the clip in the same player with its own label, steps examples, and goes back', async () => {
    const f = mockApi({ [LIST]: PLAYS_LIBRARY, [DIAG(100)]: DIAGRAM_WITH_REAL, [CLIP_URL(0)]: CLIP, [CLIP_URL(1)]: CLIP_2 });
    browser();
    open();
    await listed();
    expect(screen.queryByText(/need the Big Data Bowl library/)).toBeNull();
    const btn = await screen.findByRole('button', { name: 'See a real one: Play-action deep shot' });
    expect(screen.getByText(/matches a play-action throw 20\+ air yards · 2 real examples/)).toBeInTheDocument();
    expect(calls(f)).not.toContain(CLIP_URL(0)); // only when asked
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByTestId('diagram-label')).toHaveTextContent(REAL_LABEL));
    expect(screen.getByText(CLIP.note)).toBeInTheDocument();
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next example' }));
    await waitFor(() => expect(screen.getByTestId('diagram-label')).toHaveTextContent('Real play: 2022 week 7'));
    fireEvent.click(screen.getByRole('button', { name: /Back to the reconstruction/ }));
    expect(await screen.findByTestId('diagram-label')).toHaveTextContent(LABEL);
  });

  it('a clip without the library shows the reader message in the player', async () => {
    mockApi({ [LIST]: PLAYS_LIBRARY, [DIAG(100)]: DIAGRAM_WITH_REAL, [CLIP_URL(0)]: CLIP_NO_LIBRARY });
    browser();
    open();
    fireEvent.click(await screen.findByRole('button', { name: /See a real one/ }));
    expect(await screen.findByText('No Big Data Bowl library here')).toBeInTheDocument();
    expect(screen.getByText('uv run nfl playcalling library')).toBeInTheDocument();
  });

  it('a play that cannot be drawn says so', async () => {
    mockApi({ [LIST]: PLAYS });
    browser();
    open();
    expect(await screen.findByText("This play can't be drawn")).toBeInTheDocument();
  });

  it('sits on the team page before History, closed', async () => {
    const f = mockApi({ '/api/playcalling/teams/KC?side=offense': TEAM });
    const { container } = renderApp(<PlayCallTeamPage />, { route: '/explore/play-calling/KC', path: '/explore/play-calling/:team' });
    const card = (await screen.findByRole('button', { name: 'Show plays' })).closest('.card') as HTMLElement;
    expect(within(card).getByText('Plays')).toBeInTheDocument();
    const cards = [...container.querySelectorAll('.card')];
    expect(cards.indexOf(card)).toBeLessThan(cards.findIndex((c) => c.classList.contains('hist')));
    expect(calls(f).some((c) => c.includes('/plays'))).toBe(false);
  });
});
