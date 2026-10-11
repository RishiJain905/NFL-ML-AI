import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { mockApi, renderApp, withStatus } from '../../test/utils';
import { PLAY_CALLS, PLAY_CALLS_NOT_BUILT, PLAY_CALLS_NOT_YET } from '../explore/playcallFixtures';
import { WeekPage } from '../WeekPage';
import { PlayCallsTab } from './PlayCallsTab';

const URL5 = '/api/weeks/2026/5/play-calls';
const tab = (week = 5) => renderApp(<PlayCallsTab season={2026} week={week} isCurrent lastPublishedWeek={4} />);

describe('PlayCallsTab', () => {
  it('shows the note, the biggest shifts and each game with both matchups', async () => {
    mockApi({ [URL5]: PLAY_CALLS });
    const { container } = tab();
    expect(await screen.findByText(/The forecast arrives in PC02/)).toBeInTheDocument();
    expect(screen.getByText(/KC's play-action meets JAX/)).toBeInTheDocument();
    expect(screen.getByText('+2.1 SD')).toBeInTheDocument();
    const kc = screen.getByRole('region', { name: 'KC offense against JAX defense' });
    const pa = within(kc).getByRole('row', { name: /Play-action/ });
    expect(pa).toHaveTextContent('36.6%');
    expect(pa).toHaveTextContent('33.0%');
    expect(pa).toHaveTextContent('24.1%');
    expect(pa.querySelectorAll('td')[1]).toHaveClass('smallv'); // JAX's play-action allowed: 12 dropbacks
    const blitz = within(kc).getByRole('row', { name: /Blitz/ });
    expect(blitz).toHaveTextContent("defense's call");
    fireEvent.focus(within(blitz).getByRole('img'));
    expect(screen.getByRole('tooltip')).toHaveTextContent('JAX calls: 18.0%');
    expect(screen.getByRole('tooltip')).toHaveTextContent("JAX sits −2.4 SD from the league's defenses");
    expect(screen.getByRole('region', { name: 'JAX offense against KC defense' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'KC page' })).toHaveAttribute('href', '/explore/play-calling/KC?season=2026');
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('marks a 2+ SD shift only when neither rate is a small sample, and says so only then', async () => {
    mockApi({ [URL5]: PLAY_CALLS });
    tab();
    const region = await screen.findByRole('region', { name: 'KC offense against JAX defense' });
    // play-action: +2.1 SD, but JAX's allowed rate rests on 12 dropbacks: no accent, no "sits" line
    const pa = within(region).getByRole('row', { name: /Play-action/ });
    expect(pa).not.toHaveClass('bigshift');
    fireEvent.focus(within(pa).getByRole('img'));
    expect(screen.getByRole('tooltip')).not.toHaveTextContent('sits');
    expect(screen.getByRole('tooltip')).not.toHaveTextContent('same side');
    fireEvent.blur(within(pa).getByRole('img'));
    // blitz: −2.4 SD on two solid samples: the accent
    expect(within(region).getByRole('row', { name: /Blitz/ })).toHaveClass('bigshift');
    // PROE: 0.8 SD: no accent
    expect(within(region).getByRole('row', { name: /^PROE/ })).not.toHaveClass('bigshift');
  });

  it("keeps each row's tooltip on its focusable name cell, with the strip gone (a phone hides it)", async () => {
    mockApi({ [URL5]: PLAY_CALLS });
    const { container } = tab();
    const region = await screen.findByRole('region', { name: 'KC offense against JAX defense' });
    // the strip is a hover-only mark: no tab stop, hidden from assistive tech
    const strip = container.querySelector('.mv') as HTMLElement;
    expect(strip).toHaveAttribute('aria-hidden', 'true');
    expect(strip).not.toHaveAttribute('tabindex');
    container.querySelectorAll('td.mviz').forEach((td) => td.remove());
    const name = within(within(region).getByRole('row', { name: /Blitz/ })).getByRole('img');
    expect(name).toHaveAttribute('tabindex', '0');
    expect(name.closest('th')).not.toBeNull();
    fireEvent.focus(name);
    expect(screen.getByRole('tooltip')).toHaveTextContent('KC has faced: 30.0%');
  });

  it("explains an offense's allowed rate in the tooltip", async () => {
    mockApi({ [URL5]: PLAY_CALLS });
    tab();
    const kc = await screen.findByRole('region', { name: 'KC offense against JAX defense' });
    fireEvent.focus(within(within(kc).getByRole('row', { name: /PROE/ })).getByRole('img'));
    expect(screen.getByRole('tooltip')).toHaveTextContent('depends on the offenses it faced');
  });

  it('shows the not-built and not-yet states with the server message', async () => {
    mockApi({ [URL5]: PLAY_CALLS_NOT_BUILT, '/api/weeks/2026/7/play-calls': PLAY_CALLS_NOT_YET });
    const a = tab();
    expect(await screen.findByText('No play-calling tables yet')).toBeInTheDocument();
    expect(screen.getByText('uv run nfl playcalling build --season 2026', { selector: '.cmd' })).toBeInTheDocument();
    a.unmount();
    tab(7);
    expect(await screen.findByText("Week 7's matchups aren't ready yet")).toBeInTheDocument();
    expect(screen.getByText(/Week 7 is counted once week 6 is played/)).toBeInTheDocument();
  });

  it("says so when a built week has no games", async () => {
    mockApi({ [URL5]: { ...PLAY_CALLS, games: [], shifts: [], message: 'No week-5 games in the schedule.' } });
    tab();
    expect(await screen.findByText('No week-5 games')).toBeInTheDocument();
    expect(screen.getByText('No week-5 games in the schedule.')).toBeInTheDocument();
  });

  it('shows loading, then an error', async () => {
    mockApi({ [URL5]: withStatus(500, { error: { code: 'server_error', message: 'boom' } }) });
    tab();
    expect(screen.getByRole('status')).toHaveTextContent('Loading');
    expect(await screen.findByText(/Couldn't load this tab/)).toBeInTheDocument();
  });

  it('is the tab after Game day in the week page', async () => {
    mockApi({ [URL5]: PLAY_CALLS });
    renderApp(<WeekPage />, { route: '/week/2026/5/play-calls', path: '/week/:season/:week/:tab' });
    const tabs = screen.getByRole('navigation', { name: 'Week sections' });
    const names = within(tabs).getAllByRole('link').map((t) => t.textContent);
    expect(names.slice(-2)).toEqual(['Game day', 'Play calls']);
    expect(within(tabs).getByRole('link', { name: 'Play calls' })).toHaveAttribute('aria-current', 'page');
    expect(await screen.findByText(/The forecast arrives in PC02/)).toBeInTheDocument();
  });
});
