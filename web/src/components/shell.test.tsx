import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WeekPage } from '../pages/WeekPage';
import { META, WEEKS, mockApi, renderApp } from '../test/utils';
import { ConfirmDialog } from './ConfirmDialog';
import { Sidebar } from './Sidebar';
import { WeekHeader } from './WeekHeader';

describe('Sidebar', () => {
  it('lists the real weeks with their status and the before-go-live row', async () => {
    mockApi();
    renderApp(<Sidebar />);
    const w5 = await screen.findByRole('button', { name: /Week 5/ });
    expect(w5).toHaveTextContent('Ready');
    expect(w5.querySelector('.chip')).toHaveAttribute('title', 'Ready to run');
    expect(w5).toHaveTextContent('This week · Thu, Oct 8');
    const w4 = screen.getByRole('button', { name: /Week 4/ });
    expect(w4).toHaveTextContent('Published');
    expect(w4).toHaveTextContent('Checks passed after a rewrite');
    expect(screen.getByRole('button', { name: /Before go-live/ })).toBeDisabled();
  });

  it('shows the lock, Neo4j and data drive in the footer', async () => {
    mockApi();
    renderApp(<Sidebar />);
    const foot = await screen.findByLabelText('Status');
    await waitFor(() => expect(foot).toHaveTextContent('475 GB free'));
    expect(foot).toHaveTextContent('Run lock');
    expect(foot).toHaveTextContent('free');
    expect(foot).toHaveTextContent('down');
  });

  it('switches theme and mode from the gear, and remembers them', async () => {
    mockApi();
    renderApp(<Sidebar />);
    fireEvent.click(screen.getByRole('button', { name: 'Appearance' }));
    fireEvent.click(await screen.findByRole('button', { name: /Playbook/ }));
    expect(document.documentElement.dataset.pal).toBe('playbook');
    fireEvent.click(screen.getByRole('button', { name: 'Light' }));
    expect(document.documentElement.dataset.mode).toBe('light');
    expect(JSON.parse(localStorage.getItem('cr.appearance') ?? '{}')).toEqual({ palette: 'playbook', mode: 'light' });
  });
});

describe('WeekHeader', () => {
  it('counts down to the first kickoff and greys out the Saturday update', () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-06T14:00:00Z'));
    try {
      renderApp(<WeekHeader season={2026} week={5} entry={WEEKS.weeks[0]} calendar={META.calendar} />);
      expect(screen.getByRole('heading', { name: 'Week 5' })).toBeInTheDocument();
      expect(screen.getByText('58 h 15 m')).toBeInTheDocument();
      expect(screen.getByText(/Thu, Oct 8, 8:15 PM ET/)).toBeInTheDocument();
      expect(screen.getByText('Byes: CAR, KC')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Saturday injury update' })).toBeDisabled();
      expect(screen.getByText("Needs this week's Tuesday run first")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows when a past week was published', () => {
    renderApp(<WeekHeader season={2026} week={4} entry={WEEKS.weeks[1]} calendar={META.calendar} />);
    expect(screen.getByText('Published', { selector: '.small' })).toBeInTheDocument();
    expect(screen.getByText('Sun 5:02 AM')).toBeInTheDocument();
    expect(screen.getByText('No run records')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Saturday injury update' })).toBeNull();
  });
});

describe('WeekPage', () => {
  it('has the eight tabs and the MLOps switcher', async () => {
    mockApi();
    renderApp(<WeekPage />, { route: '/week/2026/4/mlops', path: '/week/:season/:week/:tab' });
    const tabs = screen.getByRole('navigation', { name: 'Week sections' });
    expect(within(tabs).getAllByRole('link').map((t) => t.textContent)).toEqual([
      'Pipeline', 'Digest', 'Games', 'Players', 'Results', 'MLOps', 'Graph', 'Game day',
    ]);
    expect(within(tabs).getByRole('link', { name: 'MLOps' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('group', { name: 'MLOps sections' })).toBeInTheDocument();
    expect(await screen.findByText('No run records')).toBeInTheDocument(); // week 4: before P07
  });
});

describe('ConfirmDialog', () => {
  it('confirms or cancels', () => {
    const onConfirm = vi.fn();
    const onOpenChange = vi.fn();
    renderApp(
      <ConfirmDialog open onOpenChange={onOpenChange} title="Run 2026 week 5?" confirmLabel="Run week 5" onConfirm={onConfirm}>
        <span>nfl weekly run --auto --expect-week 5</span>
      </ConfirmDialog>,
    );
    expect(screen.getByRole('dialog', { name: 'Run 2026 week 5?' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Run week 5' }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
