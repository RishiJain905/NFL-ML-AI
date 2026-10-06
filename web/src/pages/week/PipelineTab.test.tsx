import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { PipelineResponse, PipelineStep, WeekDetail } from '../../api/types';
import { WeekHeader } from '../../components/WeekHeader';
import { META, WEEKS, mockApi, renderApp } from '../../test/utils';
import { WeekPage } from '../WeekPage';
import { PipelineTab } from './PipelineTab';

const NAMES = ['ingest', 'ready', 'curate', 'ratings', 'game', 'graph', 'player', 'digest'] as const;
const SECONDS = [25, 0, 16, 4, 15, 147, 74, 2329];

function step(name: PipelineStep['step'], status: PipelineStep['status'], seconds: number | null): PipelineStep {
  return {
    step: name,
    status,
    seconds,
    expected_seconds: seconds ?? 20,
    started: null,
    finished: null,
    detail: status === 'ok' ? `${name} detail` : null,
    this_run: null,
    wandb_url: null,
  };
}

const WEEK4: PipelineResponse = {
  season: 2026,
  week: 4,
  state: 'finished',
  source: 'weekly_run',
  steps: [...NAMES.map((n, i) => step(n, 'ok', SECONDS[i])), step('records', 'none', null)],
  sittings: 3,
  step_seconds: 2610,
  expected_from: null,
  failed_step: null,
  error: null,
  log: [
    { ts: '03:18:17', text: '$ nfl weekly run --season 2026 --week 4', cls: 'acc' },
    { ts: '05:02:13', text: 'published reports/2026/week04-digest.md', cls: 'ok' },
  ],
  log_source: 'rebuilt',
  tiles: {
    checks_passed: 11,
    checks_total: 11,
    first_time: false,
    llm_cost: 0.0256,
    llm_calls: 2,
    llm_provider: 'DeepInfra',
    digest_share: 0.8923,
  },
  no_run_records: true,
};

const PLAN: PipelineResponse = {
  ...WEEK4,
  week: 5,
  state: 'plan',
  source: 'plan',
  steps: [...NAMES, 'records' as const].map((n) => ({ ...step(n, 'pending', null), expected_seconds: 30 })),
  sittings: 0,
  step_seconds: null,
  expected_from: { season: 2026, week: 4 },
  log: [{ ts: '10:00:00', text: "the calendar's plan for this week (the dry run comes with CR02)", cls: 'acc' }],
  log_source: 'calendar',
  no_run_records: false,
};

const FAILED: PipelineResponse = {
  ...WEEK4,
  week: 3,
  state: 'failed',
  source: 'run_summary',
  failed_step: 'player',
  steps: [
    ...NAMES.slice(0, 6).map((n, i) => step(n, 'ok', SECONDS[i])),
    { ...step('player', 'failed', 40), detail: 'refit failed for sacks-edge' },
    step('digest', 'skipped', null),
    step('records', 'ok', 20),
  ],
  no_run_records: false,
};

const DETAIL4: WeekDetail = {
  season: 2026,
  week: 4,
  title: 'Week 4',
  entry: WEEKS.weeks[1],
  plan: {
    ...META.calendar!,
    week: 4,
    games: 16,
    special: ['thursday', 'neutral_site', 'international', 'morning_kickoff'],
    international: ['IND@WAS'],
    neutral_sites: ['IND@WAS'],
    byes: [],
    started_games: 16,
  },
  is_current: false,
  published_at: '2026-10-04T05:02:13-04:00',
  on_time: false,
  on_time_source: 'deadline',
  first_live_week: true,
  wandb_url: 'https://wandb.ai/ent/proj/runs/0eh6h6ll',
  tab_counts: { games: 16, players: 1860, graph: 3 },
  last_published_week: 4,
};

const TEAMS = {
  teams: {
    IND: { nick: 'Colts', name: 'Indianapolis Colts', color: '#002C5F', conf: 'AFC', div: 'AFC South' },
    WAS: { nick: 'Commanders', name: 'Washington Commanders', color: '#5A1414', conf: 'NFC', div: 'NFC East' },
  },
};

describe('PipelineTab', () => {
  it('shows a week without run records: tiles, the notice, the drive and the rebuilt log', async () => {
    mockApi({ '/api/weeks/2026/4/pipeline': WEEK4 });
    renderApp(<PipelineTab season={2026} week={4} detail={DETAIL4} />);
    expect(await screen.findByText('8/8')).toBeInTheDocument();
    expect(screen.getByText('run in three sittings (2 resumes)')).toBeInTheDocument();
    expect(screen.getAllByText('43m 30s').length).toBeGreaterThanOrEqual(2); // the tile and the scorebug clock
    expect(screen.getByText('digest 89% of it')).toBeInTheDocument();
    expect(screen.getByText('11/11')).toBeInTheDocument();
    expect(screen.getByText('$0.026')).toBeInTheDocument();
    expect(screen.getByText(/ran before P07 added run records/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'The run' })).toBeInTheDocument();
    expect(screen.getByText(/real step times from weekly_run.json/)).toBeInTheDocument();
    const log = screen.getByRole('region', { name: 'Log' });
    expect(within(log).getByText('$ nfl weekly run --season 2026 --week 4')).toBeInTheDocument();
  });

  it('switches views and remembers the choice for that week only', async () => {
    mockApi({ '/api/weeks/2026/4/pipeline': WEEK4 });
    const { unmount } = renderApp(<PipelineTab season={2026} week={4} detail={DETAIL4} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Timeline' }));
    expect(screen.getByText(/Timeline · real step times/)).toBeInTheDocument();
    unmount();
    renderApp(<PipelineTab season={2026} week={4} detail={DETAIL4} />);
    expect(await screen.findByRole('button', { name: 'Timeline' })).toHaveAttribute('aria-pressed', 'true');
    expect(JSON.parse(localStorage.getItem('cr.vizByWeek') ?? '{}')).toEqual({ '2026-4': 'timeline' });
  });

  it('shows the plan for the current week, with where the expected times come from', async () => {
    mockApi({ '/api/weeks/2026/5/pipeline': PLAN });
    renderApp(<PipelineTab season={2026} week={5} />);
    expect(await screen.findByRole('heading', { name: 'The plan for this run' })).toBeInTheDocument();
    expect(screen.getByText(/Week 5 hasn't run yet/)).toBeInTheDocument();
    expect(screen.getByText(/expected times from week 4's run/)).toBeInTheDocument();
    expect(screen.getByText(/from the calendar/)).toBeInTheDocument();
    expect(screen.queryByText('Status')).toBeNull(); // no tiles before a run
  });

  it('names the failed step and the resume command', async () => {
    mockApi({ '/api/weeks/2026/3/pipeline': FAILED });
    renderApp(<PipelineTab season={2026} week={3} />);
    expect(await screen.findByText('The player step failed.')).toBeInTheDocument();
    expect(screen.getByText('nfl weekly run --season 2026 --week 3 --from-step player')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('has an empty state for a week with no run', async () => {
    mockApi({ '/api/weeks/2026/2/pipeline': { ...PLAN, week: 2, state: 'none', source: 'none', log: [] } });
    renderApp(<PipelineTab season={2026} week={2} />);
    expect(await screen.findByText('No run for week 2')).toBeInTheDocument();
  });
});

describe('WeekHeader for a past week', () => {
  it('shows the late chip, the slate tags, the publish time and the W&B link', async () => {
    mockApi({ '/api/team-info': TEAMS });
    renderApp(<WeekHeader season={2026} week={4} entry={WEEKS.weeks[1]} calendar={META.calendar} detail={DETAIL4} />);
    expect(screen.getByText('Late run · first live week')).toBeInTheDocument();
    expect(screen.getByText('16 games · Thu–Mon')).toBeInTheDocument();
    expect(screen.getByText('Morning kickoff')).toBeInTheDocument();
    expect(await screen.findByText('Colts at Commanders abroad')).toBeInTheDocument();
    expect(screen.getByText('Sun 5:02 AM')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open week 4 in W&B/ })).toHaveAttribute(
      'href',
      'https://wandb.ai/ent/proj/runs/0eh6h6ll',
    );
    expect(screen.queryByRole('button', { name: 'Saturday injury update' })).toBeNull();
  });
});

describe('WeekPage with CR01 data', () => {
  it('puts the counts on the tabs and renders the Pipeline tab', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-06T14:00:00Z'));
    try {
      mockApi({
        '/api/meta': META,
        '/api/weeks': WEEKS,
        '/api/team-info': TEAMS,
        '/api/weeks/2026/4': DETAIL4,
        '/api/weeks/2026/4/pipeline': WEEK4,
      });
      renderApp(<WeekPage />, { route: '/week/2026/4/pipeline', path: '/week/:season/:week/:tab' });
      const tabs = screen.getByRole('navigation', { name: 'Week sections' });
      expect(await within(tabs).findByText('1,860')).toBeInTheDocument();
      expect(within(tabs).getByRole('link', { name: /Games/ })).toHaveTextContent('Games16');
      expect(await screen.findByText('8/8')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
