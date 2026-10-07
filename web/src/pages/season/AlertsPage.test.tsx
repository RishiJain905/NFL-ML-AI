import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { mockApi, renderApp } from '../../test/utils';
import { AlertsPage } from './AlertsPage';
import { ALERTS_EMPTY, ALERTS_WITH } from './seasonFixtures';

describe('AlertsPage', () => {
  it('shows the empty card, the signal timeline, the example alert and the drift by run', async () => {
    mockApi({ '/api/alerts': ALERTS_EMPTY });
    const { container } = renderApp(<AlertsPage />);
    expect(await screen.findByText('No alerts in 2026 yet')).toBeInTheDocument();
    expect(screen.getByText('2026 · none yet')).toBeInTheDocument();
    // the timeline: one focusable mark per signal, "this week" at week 5
    const marks = screen.getAllByRole('img', { name: /needs/ });
    expect(marks).toHaveLength(5);
    expect(container.querySelector('.sigline .now-l')).toHaveTextContent('this week');
    const elo = screen.getByRole('img', { name: /^game_vs_elo/ });
    fireEvent.focus(elo);
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent("First fires: week 10's run");
    expect(tip).toHaveTextContent('Needs 6 graded weeks');
    expect(tip).toHaveTextContent('Now: not enough data yet');
    fireEvent.blur(elo);
    // the example (D75)
    const example = screen
      .getByRole('heading', { name: 'What an alert looks like' })
      .closest('.card') as HTMLElement;
    expect(within(example).getByText('example · 2025 week-10 simulation')).toBeInTheDocument();
    expect(example).toHaveTextContent('ECE 0.053 over 135 graded games');
    expect(example).toHaveTextContent('Investigation:');
    expect(example).toHaveTextContent('D75');
    // drift by run
    expect(screen.getByRole('link', { name: 'Week 5' })).toHaveAttribute(
      'href',
      '/week/2026/5/mlops',
    );
    expect(screen.getAllByText('not enough data yet').length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('shows each alert with its level, notes and links', async () => {
    mockApi({ '/api/alerts': ALERTS_WITH });
    const { container } = renderApp(<AlertsPage />);
    expect(
      await screen.findByRole('heading', { name: 'Calibration drifting' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No alerts in/)).toBeNull();
    expect(screen.getByText('2026 · 2 alerts')).toBeInTheDocument();
    const cal = screen
      .getByRole('heading', { name: 'Calibration drifting' })
      .closest('.card') as HTMLElement;
    expect(within(cal).getByText('warn')).toBeInTheDocument();
    expect(cal).toHaveTextContent('Mostly the 60–70% bin');
    expect(within(cal).getByRole('link', { name: /Open the run in W&B/ })).toHaveAttribute(
      'href',
      ALERTS_WITH.alerts[0].run_url,
    );
    expect(within(cal).getByRole('link', { name: "Week 8's pipeline" })).toHaveAttribute(
      'href',
      '/week/2026/8/pipeline',
    );
    const graph = screen
      .getByRole('heading', { name: 'The graph step failed' })
      .closest('.card') as HTMLElement;
    expect(within(graph).getByText('error')).toBeInTheDocument();
    expect(graph).toHaveTextContent('No investigation notes yet');
    expect(within(graph).queryByRole('link', { name: /W&B/ })).toBeNull();
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });
});
