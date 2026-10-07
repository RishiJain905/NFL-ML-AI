import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { mockApi, renderApp } from '../../test/utils';
import { ScorecardPage } from './ScorecardPage';
import { SCORECARD_BACKTEST, SCORECARD_EMPTY, SCORECARD_LIVE } from './seasonFixtures';

const LIVE = '/api/season/2026/scorecard?source=live';
const BACKTEST = '/api/season/2025/scorecard?source=backtest';
const ROUTE = { route: '/season/2026/scorecard', path: '/season/:season/scorecard' };

describe('ScorecardPage', () => {
  beforeEach(() => window.localStorage.clear());

  it('renders the live season: tiles, empty line charts with one graded week, pipeline health', async () => {
    mockApi({ [LIVE]: SCORECARD_LIVE, [BACKTEST]: SCORECARD_BACKTEST });
    const { container } = renderApp(<ScorecardPage />, ROUTE);
    expect(await screen.findByText('0.2246')).toBeInTheDocument();
    expect(screen.getByText('Elo better by 0.0132')).toBeInTheDocument();
    expect(screen.getByText('market better by 0.0074')).toBeInTheDocument();
    expect(screen.getByText('60.0%')).toBeInTheDocument();
    expect(screen.getByText('2 of 2 digests')).toBeInTheDocument();
    expect(screen.getByText('$0.027')).toBeInTheDocument();
    expect(screen.getAllByText(/Starts once two weeks are graded/)).toHaveLength(2);
    expect(screen.queryByRole('img', { name: /Season-to-date Brier/ })).toBeNull();
    // the calibration chart and the player bars still draw
    expect(screen.getByRole('group', { name: /Calibration curve, 2026 live/ })).toBeInTheDocument();
    expect(
      screen.getByRole('list', { name: 'Player model vs baseline by group' }),
    ).toHaveTextContent('+8.7%');
    // pipeline health
    expect(screen.getByText('on time · 47.5 h before')).toBeInTheDocument();
    expect(screen.getByText('late')).toBeInTheDocument();
    expect(screen.getByText('passed after a rewrite')).toBeInTheDocument();
    expect(screen.getByText('16m 29s')).toBeInTheDocument();
    expect(screen.getByText('rishi')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /2026 Season Dashboard/ })).toHaveAttribute(
      'href',
      SCORECARD_LIVE.dashboard.url,
    );
    expect(screen.getByText("Week 5 is graded by week 6's run.")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('switches to the backtest example (season − 1), draws the lines and remembers the choice', async () => {
    const fetchMock = mockApi({ [LIVE]: SCORECARD_LIVE, [BACKTEST]: SCORECARD_BACKTEST });
    const { container } = renderApp(<ScorecardPage />, ROUTE);
    await screen.findByText('0.2246');
    const live = screen.getByRole('button', { name: '2026 live' });
    const backtest = screen.getByRole('button', { name: '2025 backtest (example)' });
    expect(live).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(backtest);
    expect(
      await screen.findByRole('img', { name: /Season-to-date Brier score by week, 2025 backtest/ }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(BACKTEST, expect.anything());
    expect(backtest).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('img', { name: /pick accuracy, 2025 backtest/ })).toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: /Predicted .* games/ })).toHaveLength(10);
    expect(screen.queryByText('Pipeline health')).toBeNull();
    expect(screen.queryByText('LLM spend')).toBeNull();
    expect(window.localStorage.getItem('cr.scorecardSource')).toBe('backtest');
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('opens on the remembered source', async () => {
    window.localStorage.setItem('cr.scorecardSource', 'backtest');
    mockApi({ [LIVE]: SCORECARD_LIVE, [BACKTEST]: SCORECARD_BACKTEST });
    renderApp(<ScorecardPage />, ROUTE);
    expect(
      await screen.findByRole('img', { name: /Season-to-date Brier score by week/ }),
    ).toBeInTheDocument();
  });

  it('puts the "no pipeline row" note under the pipeline table, other notes above the charts', async () => {
    const note = '1 published week(s) ran before run records (P07) and have no pipeline row.';
    mockApi({
      [LIVE]: {
        ...SCORECARD_LIVE,
        pipeline: SCORECARD_LIVE.pipeline.filter((p) => p.week === 5),
        notes: [...SCORECARD_LIVE.notes, note],
      },
    });
    renderApp(<ScorecardPage />, ROUTE);
    const under = await screen.findByText(note);
    expect(under.closest('.card')).toHaveTextContent('Pipeline health');
    expect(under).not.toHaveAttribute('role', 'note');
    expect(screen.getAllByText(note)).toHaveLength(1);
    expect(
      screen.getByText("Week 5 is graded by week 6's run.").closest('[role="note"]'),
    ).not.toBeNull();
  });

  it('renders an empty season cleanly', async () => {
    mockApi({ [LIVE]: SCORECARD_EMPTY });
    const { container } = renderApp(<ScorecardPage />, ROUTE);
    expect(await screen.findByText('Nothing graded in 2026 live yet')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('says so when the scorecard fails to load', async () => {
    mockApi({});
    renderApp(<ScorecardPage />, ROUTE);
    await waitFor(() =>
      expect(screen.getByText("Couldn't load the scorecard.")).toBeInTheDocument(),
    );
  });
});
