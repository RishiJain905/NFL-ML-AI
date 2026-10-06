import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { LogLine, PipelineResponse, PipelineStep, StepName } from '../../api/types';
import { DriveChart } from './DriveChart';
import {
  MID_Y,
  RING_C,
  ballYard,
  clip,
  goalpostColor,
  isTouchdown,
  scrimmageLines,
  timelineLayout,
  xAt,
} from './geometry';
import { LogPanel } from './LogPanel';
import { STEP_DEFS, toModel, type PipelineModel } from './model';
import { PipelineMap } from './PipelineMap';
import { PipelineView } from './PipelineView';
import { Timeline } from './Timeline';
import { ViewPicker, useViewChoice } from './ViewPicker';
import { pickOtherView } from './views';

// ---- fixtures ------------------------------------------------------------------------------

const NAMES = STEP_DEFS.map((d) => d.name);
const EXPECTED = Object.fromEntries(STEP_DEFS.map((d) => [d.name, d.defaultSeconds])) as Record<StepName, number>;

function step(name: StepName, over: Partial<PipelineStep> = {}): PipelineStep {
  return {
    step: name,
    status: 'ok',
    seconds: 10,
    expected_seconds: EXPECTED[name],
    started: null,
    finished: null,
    detail: `${name} detail`,
    this_run: null,
    wandb_url: null,
    ...over,
  };
}

function resp(over: Partial<PipelineResponse>, steps: PipelineStep[]): PipelineResponse {
  return {
    season: 2026,
    week: 5,
    state: 'finished',
    source: 'run_summary',
    steps,
    sittings: 1,
    step_seconds: null,
    expected_from: null,
    failed_step: null,
    error: null,
    log: [],
    log_source: 'rebuilt',
    tiles: { checks_passed: null, checks_total: null, first_time: null, llm_cost: null, llm_calls: 0, llm_provider: null, digest_share: null },
    no_run_records: false,
    ...over,
  };
}

// Week 4 as it really ran: real seconds, three sittings, no run records.
const W4_SECONDS: Record<string, number> = { ingest: 25, ready: 0, curate: 16, ratings: 4, game: 15, graph: 147, player: 74, digest: 2329 };

function week4(): PipelineModel {
  return toModel(
    resp({ week: 4, source: 'weekly_run', sittings: 3, no_run_records: true }, [
      ...NAMES.slice(0, 8).map((n) => step(n, { seconds: W4_SECONDS[n], detail: `${n} finished` })),
      step('records', { status: 'none', seconds: null, detail: null }),
    ]),
  );
}

function plan(): PipelineModel {
  return toModel(
    resp({ state: 'plan', source: 'plan' }, NAMES.map((n) => step(n, { status: 'pending', seconds: null, detail: null }))),
  );
}

function failed(): PipelineModel {
  return toModel(
    resp({ state: 'failed', failed_step: 'player' }, [
      ...NAMES.slice(0, 6).map((n) => step(n, { seconds: EXPECTED[n] })),
      step('player', { status: 'failed', seconds: 40, detail: 'refit failed for sacks-edge' }),
      step('digest', { status: 'skipped', seconds: null, detail: null }),
      step('records', { seconds: 20 }),
    ]),
  );
}

function stopped(): PipelineModel {
  return toModel(
    resp({ state: 'partial' }, [
      ...NAMES.slice(0, 7).map((n) => step(n, { seconds: EXPECTED[n] })),
      step('digest', { status: 'pending', seconds: null, detail: null }),
      step('records', { status: 'pending', seconds: null, detail: null }),
    ]),
  );
}

/** CR02's shape: the knowledge graph is half way through. */
function live(): PipelineModel {
  const base = plan();
  return {
    ...base,
    outcome: 'live',
    idx: 5,
    idleLabel: null,
    steps: base.steps.map((s, i) =>
      i < 5
        ? { ...s, status: 'ok', seconds: s.expected, progress: 1, detail: 'done' }
        : i === 5
          ? { ...s, status: 'running', seconds: 75, progress: 0.5, detail: 'loading nodes & relationships 50%' }
          : s,
    ),
  };
}

/** What CR01 shows while a run holds the lock: weekly_run.json's states, the running step at progress 0. */
function running(): PipelineModel {
  return toModel(
    resp({ state: 'running' }, [
      ...NAMES.slice(0, 5).map((n) => step(n, { seconds: EXPECTED[n] })),
      step('graph', { status: 'running', seconds: null, detail: null }),
      ...NAMES.slice(6).map((n) => step(n, { status: 'pending', seconds: null, detail: null })),
    ]),
  );
}

// ---- geometry ------------------------------------------------------------------------------

describe('pipeline geometry', () => {
  it('moves the ball from the 20 to the end zone, part-way for a running or failed step', () => {
    expect(ballYard(plan())).toBe(20);
    expect(ballYard(week4())).toBe(100);
    expect(ballYard(failed())).toBeCloseTo(78.5); // half way from the 72 to the 85
    expect(ballYard(live())).toBeCloseTo(66); // half way from the 60 to the 72
  });

  it('puts the line of scrimmage where the run is and the first-down line where the step ends', () => {
    expect(scrimmageLines(plan())).toEqual({ from: 20, target: 30 });
    expect(scrimmageLines(live())).toEqual({ from: 60, target: 72 });
  });

  it('only a finished run that nothing stopped scores', () => {
    expect(isTouchdown(week4())).toBe(true);
    expect(isTouchdown(plan())).toBe(false);
    expect(isTouchdown(failed())).toBe(false);
    expect(isTouchdown(stopped())).toBe(false);
    expect(isTouchdown(live())).toBe(false);
  });

  it('greys the goalposts when the run has no records', () => {
    expect(goalpostColor('none')).toBe('var(--line-2)');
    expect(goalpostColor('ok')).toBe('var(--ok)');
    expect(goalpostColor('running')).toBe('var(--accent)');
  });

  it('lays the timeline out without NaN, with min-width bars for zero seconds', () => {
    const l = timelineLayout(week4());
    expect(l.total).toBe(2610);
    expect(l.rows).toHaveLength(9);
    for (const r of l.rows) {
      expect(Number.isFinite(r.left)).toBe(true);
      expect(Number.isFinite(r.width)).toBe(true);
    }
    expect(l.rows[1].width).toBe(0); // ready: 0 s (the CSS gives the bar its 3 px)
    expect(l.rows[8].hasBar).toBe(false); // records: never recorded
    expect(l.digestShare).toBe(89);
    expect(l.ticks.map((t) => t.label)).toEqual(['0m', '10m', '20m', '30m', '40m']);
    expect(l.grid).toHaveLength(4);
  });

  it('keeps every bar on the axis even when skipped steps are drawn at their expected time', () => {
    const l = timelineLayout(failed());
    for (const r of l.rows) expect(r.left + r.width).toBeLessThanOrEqual(100.0001);
  });

  it('copes with a week that has no steps at all', () => {
    const none = toModel(resp({ state: 'none', source: 'none' }, []));
    const l = timelineLayout(none);
    expect(l.total).toBe(1);
    expect(l.rows.every((r) => !r.hasBar && Number.isFinite(r.left) && Number.isFinite(r.width))).toBe(true);
    expect(l.digestShare).toBeNull();
  });

  it('cuts a long running sub-line at 34 characters with an ellipsis', () => {
    expect(clip('x'.repeat(40))).toBe(`${'x'.repeat(33)}…`);
    expect(clip('short')).toBe('short');
  });
});

// ---- drive chart ---------------------------------------------------------------------------

describe('DriveChart', () => {
  it('draws a finished week 4 as a touchdown with the real times', () => {
    const { container } = render(<DriveChart model={week4()} />);
    expect(screen.getByRole('img', { name: /football drive/ })).toBeInTheDocument();
    const bug = container.querySelector('.scorebug') as HTMLElement;
    expect(bug).toHaveTextContent('2026WK 4');
    expect(bug).toHaveTextContent('8 plays · 80 yds');
    expect(bug).toHaveTextContent('FinalTouchdown · digest published');
    expect(bug).toHaveTextContent('43m 30s');
    expect(screen.getByText('TOUCHDOWN')).toBeInTheDocument();
    expect(screen.queryByText('FUMBLE')).toBeNull();
    // no live lines once the run is done; the ball sits in the end zone
    expect(screen.queryByTestId('los')).toBeNull();
    expect(screen.queryByTestId('fdl')).toBeNull();
    expect(screen.getByTestId('ball')).toHaveAttribute('transform', `translate(${xAt(100)} ${MID_Y}) rotate(-12)`);
    // no run records before P07: grey goalposts
    expect(screen.getByTestId('goalposts')).toHaveAttribute('data-status', 'none');
  });

  it('writes the drive log as a list with down and distance, glyphs, details and times', () => {
    render(<DriveChart model={week4()} />);
    const rows = within(screen.getByRole('list', { name: 'Drive log' })).getAllByRole('listitem');
    expect(rows).toHaveLength(9);
    expect(rows[0]).toHaveTextContent('1st & 10 at OWN 20');
    expect(rows[0]).toHaveTextContent('✓ Ingest');
    expect(rows[0]).toHaveTextContent('25 s');
    expect(rows[1]).toHaveTextContent('0 s'); // a 0 s step still gets a row
    expect(rows[5]).toHaveTextContent('1st & 12 at OPP 40'); // graph: 60 → 72
    expect(rows[5]).toHaveTextContent('2m 27s');
    expect(rows[7]).toHaveTextContent('1st & Goal at OPP 15');
    expect(rows[7]).toHaveTextContent('38m 49s');
    expect(rows[8]).toHaveTextContent('PAT');
    expect(rows[8]).toHaveTextContent('Run records');
    expect(rows[8]).toHaveTextContent('not recorded');
    expect(rows[8]).toHaveTextContent('—');
    expect(rows[8]).toHaveClass('pending');
  });

  it('shows the plan with the ball at the 20, both lines, and the waiting scorebug', () => {
    const { container } = render(<DriveChart model={plan()} />);
    const bug = container.querySelector('.scorebug') as HTMLElement;
    expect(bug).toHaveTextContent('WaitingKickoff when the run starts');
    expect(bug).toHaveTextContent('0 plays · 0 yds');
    expect(screen.getByTestId('ball')).toHaveAttribute('transform', `translate(${xAt(20)} ${MID_Y}) rotate(-12)`);
    expect(screen.getByTestId('los')).toHaveAttribute('x1', String(xAt(20)));
    expect(screen.getByTestId('fdl')).toHaveAttribute('x1', String(xAt(30)));
    expect(screen.queryByText('TOUCHDOWN')).toBeNull();
    const rows = within(screen.getByRole('list', { name: 'Drive log' })).getAllByRole('listitem');
    expect(rows[0]).toHaveTextContent('expected ~25 s');
    expect(rows[5]).toHaveTextContent('expected ~2m 30s');
    expect(rows.every((r) => r.classList.contains('pending'))).toBe(true);
    // the extra point hasn't been tried: gold posts, not grey
    expect(screen.getByTestId('goalposts')).toHaveAttribute('data-status', 'pending');
  });

  it('marks a failed step as a fumble and a turnover', () => {
    const { container } = render(<DriveChart model={failed()} />);
    const bug = container.querySelector('.scorebug') as HTMLElement;
    expect(bug).toHaveTextContent('TurnoverFumble at the player step');
    expect(screen.getByText('FUMBLE')).toBeInTheDocument();
    expect(screen.queryByText('TOUCHDOWN')).toBeNull();
    const rows = within(screen.getByRole('list', { name: 'Drive log' })).getAllByRole('listitem');
    expect(rows[6]).toHaveTextContent('✕ Player model');
    expect(rows[6]).toHaveClass('failed');
    expect(rows[6]).toHaveTextContent('refit failed for sacks-edge');
    expect(rows[7]).toHaveTextContent('skipped (an earlier step failed)');
    expect(rows[7]).toHaveClass('pending');
    expect(rows[8]).toHaveTextContent('✓ Run records'); // the records are still written after a failure
    expect(screen.getByTestId('goalposts')).toHaveAttribute('data-status', 'ok');
    expect(screen.getByTestId('ball')).toHaveAttribute('transform', `translate(${xAt(78.5)} ${MID_Y}) rotate(-12)`);
  });

  it('treats a run that stopped before the digest as a drive that ended short', () => {
    const { container } = render(<DriveChart model={stopped()} />);
    expect((container.querySelector('.scorebug') as HTMLElement)).toHaveTextContent('StoppedStopped before the digest');
    expect(screen.queryByText('TOUCHDOWN')).toBeNull();
    expect(screen.queryByText('FUMBLE')).toBeNull();
    expect(screen.queryByTestId('los')).toBeNull();
  });

  it('shows a running step on the field and in the scorebug', () => {
    const { container } = render(<DriveChart model={live()} />);
    expect((container.querySelector('.scorebug') as HTMLElement)).toHaveTextContent('NowKnowledge graph — loading nodes & relationships 50%');
    expect(screen.getByTestId('los')).toHaveAttribute('x1', String(xAt(60)));
    expect(screen.getByTestId('fdl')).toHaveAttribute('x1', String(xAt(72)));
    expect(screen.getByTestId('run-arc')).toBeInTheDocument();
    const row = container.querySelector('[data-row="graph"]') as HTMLElement;
    expect(row).toHaveClass('running');
    expect(row).toHaveTextContent('● Knowledge graph');
  });

  it('updates in place when the model changes (no remount)', () => {
    const { rerender } = render(<DriveChart model={plan()} />);
    const svg = screen.getByRole('img', { name: /football drive/ });
    const ball = screen.getByTestId('ball');
    rerender(<DriveChart model={live()} />);
    expect(screen.getByRole('img', { name: /football drive/ })).toBe(svg);
    expect(screen.getByTestId('ball')).toBe(ball);
    expect(ball).toHaveAttribute('transform', `translate(${xAt(66)} ${MID_Y}) rotate(-12)`);
    rerender(<DriveChart model={week4()} />);
    expect(screen.getByRole('img', { name: /football drive/ })).toBe(svg);
    expect(screen.getByText('TOUCHDOWN')).toBeInTheDocument();
    expect(screen.queryByTestId('los')).toBeNull();
  });
});

// ---- pipeline map --------------------------------------------------------------------------

function node(container: HTMLElement, name: string): HTMLElement {
  return container.querySelector(`[data-node="${name}"]`) as HTMLElement;
}
const chipOpacity = (n: HTMLElement) => (n.querySelector('.artifact-chip') as HTMLElement).style.opacity;

describe('PipelineMap', () => {
  it('draws a finished week 4: times, chips, and a records node that was never recorded', () => {
    const { container } = render(<PipelineMap model={week4()} />);
    expect(screen.getByRole('img', { name: /Pipeline map/ })).toBeInTheDocument();
    expect(node(container, 'ingest')).toHaveTextContent('✓');
    expect(node(container, 'ingest')).toHaveTextContent('25 s');
    expect(node(container, 'ready')).toHaveTextContent('0 s');
    expect(node(container, 'graph')).toHaveTextContent('2m 27s');
    expect(node(container, 'digest')).toHaveTextContent('38m 49s');
    // the records node: a dash and "not recorded", no chip
    expect(node(container, 'records')).toHaveTextContent('–');
    expect(node(container, 'records')).toHaveTextContent('not recorded');
    expect(chipOpacity(node(container, 'records'))).toBe('0');
    // finished steps show their file or artifact (week 4's ready check was on week 3)
    expect(node(container, 'curate')).toHaveTextContent('nfl.duckdb');
    expect(chipOpacity(node(container, 'curate'))).toBe('1');
    expect(node(container, 'ready')).toHaveTextContent('week 3 final');
    expect(node(container, 'published')).toHaveTextContent('LIVE');
    expect(node(container, 'published')).toHaveTextContent('week04-digest.md');
    expect(container.querySelectorAll('.flow-edge')).toHaveLength(10);
    expect(container.querySelectorAll('.flow-edge.done')).toHaveLength(9);
    expect(container.querySelector('.node-prog')).toBeNull();
    expect(container.querySelector('.legend')).toHaveTextContent('W&B artifact or run');
  });

  it('draws the plan all grey with expected times and numbers', () => {
    const { container } = render(<PipelineMap model={plan()} />);
    expect(node(container, 'graph')).toHaveTextContent('~2m 30s');
    expect(node(container, 'ingest')).toHaveTextContent('~25 s');
    expect(node(container, 'ready')).toHaveTextContent('~1 s');
    expect(node(container, 'ingest')).toHaveTextContent('1');
    expect(node(container, 'records')).toHaveTextContent('9');
    expect(node(container, 'published')).toHaveTextContent('DIGEST');
    expect(node(container, 'published')).toHaveTextContent('not yet');
    expect(container.querySelectorAll('.flow-edge.done, .flow-edge.active')).toHaveLength(0);
    for (const d of STEP_DEFS) expect(chipOpacity(node(container, d.name))).toBe('0');
  });

  it('flags a failed step with ! and skips what follows', () => {
    const { container } = render(<PipelineMap model={failed()} />);
    expect(node(container, 'player')).toHaveTextContent('!');
    expect(node(container, 'player')).toHaveTextContent('failed');
    expect(node(container, 'digest')).toHaveTextContent('skipped');
    expect(node(container, 'records')).toHaveTextContent('✓');
    expect(node(container, 'published')).toHaveTextContent('not yet');
  });

  it('fills the ring and flows the edge into the running step', () => {
    const { container } = render(<PipelineMap model={live()} />);
    const ring = container.querySelector('.node-prog') as SVGElement;
    expect(ring).toBeInTheDocument();
    expect(Number(ring.getAttribute('stroke-dashoffset'))).toBeCloseTo(RING_C / 2);
    expect(container.querySelectorAll('.flow-edge.active')).toHaveLength(1);
    expect(node(container, 'graph')).toHaveTextContent('loading nodes & relationships 50%');
  });

  it("starts the corner edge below the Game model label and its time, not through them", () => {
    const { container } = render(<PipelineMap model={week4()} />);
    const down = [...container.querySelectorAll('.flow-edge')].find((e) => e.getAttribute('d')?.includes('V'));
    expect(down?.getAttribute('d')).toBe('M855 190 V250'); // from 112 + 78 (the sub-line ends near 112 + 72) to the graph ring's top
  });

  it('updates in place when the model changes', () => {
    const { container, rerender } = render(<PipelineMap model={plan()} />);
    const svg = screen.getByRole('img', { name: /Pipeline map/ });
    const ingest = node(container, 'ingest');
    rerender(<PipelineMap model={week4()} />);
    expect(screen.getByRole('img', { name: /Pipeline map/ })).toBe(svg);
    expect(node(container, 'ingest')).toBe(ingest);
    expect(ingest).toHaveTextContent('25 s');
    expect(chipOpacity(ingest)).toBe('1');
  });
});

// ---- timeline ------------------------------------------------------------------------------

const bars = (c: HTMLElement) => [...c.querySelectorAll<HTMLElement>('.bar')];

describe('Timeline', () => {
  it('draws a finished week 4: a bar per recorded step, none for the missing records', () => {
    const { container } = render(<Timeline model={week4()} sittings={3} />);
    expect(container.querySelectorAll('.gname')).toHaveLength(9);
    expect(bars(container)).toHaveLength(8);
    expect(container.innerHTML).not.toContain('NaN');
    expect(container.querySelector('.gaxis')).toHaveTextContent('0m10m20m30m40m');
    expect(container.querySelectorAll('.gtrack')[0].querySelectorAll('.gl')).toHaveLength(4);
    const digest = container.querySelector('[data-step="digest"]') as HTMLElement;
    expect(parseFloat(digest.style.width)).toBeCloseTo((2329 / 2610) * 100);
    expect((container.querySelector('[data-step="ready"]') as HTMLElement).style.width).toBe('0%');
    const durs = [...container.querySelectorAll('.gdur')].map((d) => d.textContent);
    expect(durs).toEqual(['25 s', '0 s', '16 s', '4 s', '15 s', '2m 27s', '1m 14s', '38m 49s', '—']);
    expect(container.querySelector('.sicon.ok')).toHaveTextContent('✓');
    expect(bars(container).every((b) => b.className === 'bar')).toBe(true);
  });

  it('says how much of the run the digest took, and mentions sittings only when there were several', () => {
    const { container, rerender } = render(<Timeline model={week4()} sittings={3} />);
    const note = container.querySelector('p.muted') as HTMLElement;
    expect(note).toHaveTextContent(
      "The digest is about 89% of the run's time (this week ran in 3 sittings: the gaps between them are left out). Striped = running, dashed = expected.",
    );
    rerender(<Timeline model={week4()} sittings={1} />);
    expect(container.querySelector('p.muted')).toHaveTextContent("The digest is about 89% of the run's time. Striped = running, dashed = expected.");
    expect(container.querySelector('p.muted')).not.toHaveTextContent('sittings');
  });

  it('draws the plan as dashed bars on the expected axis', () => {
    const { container } = render(<Timeline model={plan()} sittings={1} />);
    expect(bars(container)).toHaveLength(9);
    expect(bars(container).every((b) => b.classList.contains('planned'))).toBe(true);
    const durs = [...container.querySelectorAll('.gdur')].map((d) => d.textContent);
    expect(durs[0]).toBe('~25 s');
    expect(durs[5]).toBe('~2m 30s');
    expect(container.querySelector('.gaxis')).toHaveTextContent('0m2m4m');
    expect(container.querySelector('p.muted')).toHaveTextContent("The digest is about 62% of the run's time");
  });

  it('draws a failed step red, the steps after it dashed, and says the digest was skipped', () => {
    const { container } = render(<Timeline model={failed()} sittings={1} />);
    expect(container.querySelectorAll('.bar.failed')).toHaveLength(1);
    expect((container.querySelector('[data-step="digest"]') as HTMLElement)).toHaveClass('planned');
    const durs = [...container.querySelectorAll('.gdur')].map((d) => d.textContent);
    expect(durs[6]).toBe('40 s');
    expect(durs[7]).toBe('skipped');
    expect(container.querySelector('.sicon.failed')).toHaveTextContent('✕');
    expect(container.querySelector('p.muted')).toHaveTextContent('The digest was skipped because an earlier step failed');
  });

  it('stripes the running bar and grows it in place', () => {
    const { container, rerender } = render(<Timeline model={live()} sittings={1} />);
    const running = container.querySelector('.bar.running') as HTMLElement;
    expect(running).toBeInTheDocument();
    expect(container.querySelector('.sicon.running')).toHaveTextContent('●');
    rerender(<Timeline model={week4()} sittings={3} />);
    expect(container.querySelector('[data-step="graph"]')).toBe(running);
    expect(running.className).toBe('bar');
  });

  it('shows a tooltip on hover and on keyboard focus', () => {
    const { container } = render(<Timeline model={week4()} sittings={3} />);
    const graph = container.querySelector('[data-step="graph"]') as HTMLElement;
    expect(graph).toHaveAttribute('tabindex', '0');
    expect(graph).toHaveAttribute('aria-label', 'Knowledge graph: 2m 27s');
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.focus(graph);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Knowledge graph2m 27s');
    fireEvent.blur(graph);
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.pointerEnter(graph);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.pointerLeave(graph);
    expect(screen.queryByRole('tooltip')).toBeNull();
    const planned = render(<Timeline model={plan()} sittings={1} />).container.querySelector('[data-step="digest"]') as HTMLElement;
    fireEvent.focus(planned);
    expect(screen.getByRole('tooltip')).toHaveTextContent('expected ~9m 00s');
  });
});

describe('a past week with no run at all', () => {
  it('renders in every view without errors or NaN', () => {
    const none = toModel(resp({ state: 'none', source: 'none' }, []));
    for (const view of ['drive', 'map', 'timeline'] as const) {
      const { container, unmount } = render(<PipelineView model={none} view={view} sittings={0} />);
      expect(container.innerHTML).not.toContain('NaN');
      expect(container.innerHTML).not.toContain('undefined');
      unmount();
    }
  });
});

describe('PipelineView', () => {
  it('renders the chosen view', () => {
    const { container, rerender } = render(<PipelineView model={week4()} view="drive" sittings={3} />);
    expect(screen.getByRole('img', { name: /football drive/ })).toBeInTheDocument();
    rerender(<PipelineView model={week4()} view="map" sittings={3} />);
    expect(screen.getByRole('img', { name: /Pipeline map/ })).toBeInTheDocument();
    rerender(<PipelineView model={week4()} view="timeline" sittings={3} />);
    expect(container.querySelector('.gantt')).toBeInTheDocument();
  });
});

// ---- view picker ---------------------------------------------------------------------------

function Harness({ season = 2026, week = 4 }: { season?: number; week?: number }) {
  const { view, surprised, choose } = useViewChoice(season, week);
  return (
    <div>
      <span data-testid="view">{view}</span>
      <ViewPicker season={season} week={week} value={view} surprised={surprised} onChoose={choose} />
    </div>
  );
}
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const current = () => screen.getByTestId('view').textContent;
const stored = () => JSON.parse(localStorage.getItem('cr.vizByWeek') ?? '{}') as Record<string, string>;

describe('ViewPicker', () => {
  it('defaults to the drive chart and marks the pressed button', () => {
    render(<Harness />);
    expect(current()).toBe('drive');
    expect(screen.getByRole('button', { name: 'Drive chart' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Pipeline map' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Timeline' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Surprise me' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Pipeline view for 2026 week 4' })).toBeInTheDocument();
    expect(screen.queryByText('surprise pick')).toBeNull();
  });

  it('remembers the choice per week across a re-mount, keyed by season and week', () => {
    const first = render(<Harness week={4} />);
    click('Pipeline map');
    expect(current()).toBe('map');
    expect(stored()).toEqual({ '2026-4': 'map' });
    first.unmount();

    const second = render(<Harness week={4} />);
    expect(current()).toBe('map');
    expect(screen.getByRole('button', { name: 'Pipeline map' })).toHaveAttribute('aria-pressed', 'true');
    second.unmount();

    // another week keeps its own choice, and doesn't disturb week 4's
    const other = render(<Harness week={5} />);
    expect(current()).toBe('drive');
    click('Timeline');
    expect(stored()).toEqual({ '2026-4': 'map', '2026-5': 'timeline' });
    other.unmount();
    render(<Harness week={4} />);
    expect(current()).toBe('map');
  });

  it('follows the week when the same picker moves to another week', () => {
    localStorage.setItem('cr.vizByWeek', JSON.stringify({ '2026-4': 'map', '2026-5': 'timeline' }));
    const { rerender } = render(<Harness week={4} />);
    expect(current()).toBe('map');
    rerender(<Harness week={5} />);
    expect(current()).toBe('timeline');
    rerender(<Harness season={2025} week={5} />);
    expect(current()).toBe('drive');
  });

  it('ignores a saved value it does not know, or storage that is not JSON', () => {
    localStorage.setItem('cr.vizByWeek', JSON.stringify({ '2026-4': 'sparkles' }));
    const first = render(<Harness />);
    expect(current()).toBe('drive');
    first.unmount();
    localStorage.setItem('cr.vizByWeek', 'not json {');
    const second = render(<Harness />);
    expect(current()).toBe('drive');
    click('Timeline'); // and a bad value is simply overwritten
    expect(stored()).toEqual({ '2026-4': 'timeline' });
    second.unmount();
    localStorage.setItem('cr.vizByWeek', '[1,2]');
    render(<Harness />);
    expect(current()).toBe('drive');
  });

  it('"Surprise me" never picks the current view, whatever the random number', () => {
    for (const start of ['drive', 'map', 'timeline']) {
      for (const r of [0, 0.25, 0.49, 0.5, 0.75, 0.999, 1, NaN]) {
        localStorage.setItem('cr.vizByWeek', JSON.stringify({ '2026-4': start }));
        vi.spyOn(Math, 'random').mockReturnValue(r);
        const { unmount } = render(<Harness />);
        expect(current()).toBe(start);
        click('Surprise me');
        expect(current()).not.toBe(start);
        expect(['drive', 'map', 'timeline']).toContain(current());
        expect(screen.getByText('surprise pick')).toBeInTheDocument();
        expect(stored()['2026-4']).toBe(current());
        unmount();
        vi.restoreAllMocks();
      }
    }
  });

  it('"Surprise me" can land on each of the other two views', () => {
    const seen = new Set<string>();
    for (const r of [0, 0.99]) {
      vi.spyOn(Math, 'random').mockReturnValue(r);
      const { unmount } = render(<Harness />);
      click('Surprise me');
      seen.add(current() ?? '');
      unmount();
      vi.restoreAllMocks();
    }
    expect([...seen].sort()).toEqual(['map', 'timeline']);
    expect(pickOtherView('map', () => 0)).toBe('drive');
    expect(pickOtherView('map', () => 0.99)).toBe('timeline');
  });

  it('keeps asking for a different view when surprising again, and a direct choice clears the chip', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0);
    render(<Harness />);
    click('Surprise me');
    expect(current()).toBe('map');
    click('Surprise me');
    expect(current()).toBe('drive'); // not 'map' again
    expect(screen.getByText('surprise pick')).toBeInTheDocument();
    click('Timeline');
    expect(current()).toBe('timeline');
    expect(screen.queryByText('surprise pick')).toBeNull();
  });

  it('works when localStorage is blocked: no crash, and the choice lasts for the page', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    render(<Harness />);
    expect(current()).toBe('drive');
    click('Pipeline map');
    expect(current()).toBe('map');
    vi.spyOn(Math, 'random').mockReturnValue(0.99);
    click('Surprise me');
    expect(current()).toBe('timeline'); // the others of 'map' are drive and timeline; 0.99 → the second
    expect(screen.getByText('surprise pick')).toBeInTheDocument();
  });
});

// ---- log panel -----------------------------------------------------------------------------

const LINES: LogLine[] = [
  { ts: '03:18:17', text: '$ nfl weekly run --season 2026 --week 4 --promote', cls: 'acc' },
  { ts: '03:18:42', text: 'ingest: 30 datasets (0 optional failures)', cls: 'ok' },
  { ts: '03:25:10', text: 'graph: slow query', cls: 'warn' },
  { ts: '', text: 'player: refit failed', cls: 'err' },
  { ts: '05:02:13', text: 'plain line', cls: '' },
];

describe('LogPanel', () => {
  it('renders each line with its timestamp and class', () => {
    const { container } = render(<LogPanel lines={LINES} source="rebuilt" />);
    expect(screen.getByRole('heading', { name: 'Log' })).toBeInTheDocument();
    expect(container.textContent).toContain('rebuilt from the step details · secrets scrubbed');
    expect(container.querySelectorAll('.console .ln')).toHaveLength(5);
    expect(screen.getByText('ingest: 30 datasets (0 optional failures)')).toHaveClass('ok');
    expect(screen.getByText('graph: slow query')).toHaveClass('warn');
    expect(screen.getByText('player: refit failed')).toHaveClass('err');
    expect(screen.getByText('$ nfl weekly run --season 2026 --week 4 --promote')).toHaveClass('acc');
    expect(screen.getByText('03:18:42')).toHaveClass('ts');
    expect(container.querySelector('.cursor')).toBeNull();
  });

  it('is a polite live region the keyboard can scroll', () => {
    render(<LogPanel lines={LINES} source="rebuilt" />);
    const log = screen.getByRole('log');
    expect(log).toHaveAttribute('aria-live', 'polite');
    expect(log).toHaveAttribute('tabindex', '0');
    expect(log).toHaveClass('console');
  });

  it('says where the lines came from', () => {
    const { container, rerender } = render(<LogPanel lines={[]} source="calendar" />);
    expect(container.textContent).toContain("from the calendar's plan · secrets scrubbed");
    rerender(<LogPanel lines={[]} source="dryrun" />);
    expect(container.textContent).toContain('the dry run (read-only) · secrets scrubbed');
    rerender(<LogPanel lines={[]} source="none" />);
    expect(container.textContent).toContain('no log for this week');
    rerender(<LogPanel lines={LINES} source="events" />);
    expect(container.textContent).toContain('streamed from the run · secrets scrubbed');
    rerender(<LogPanel lines={LINES} source="rebuilt" live />);
    expect(container.textContent).toContain('streamed from the run · secrets scrubbed');
  });

  it('can carry another title, for a second log on the page', () => {
    render(<LogPanel lines={[]} source="events" title="Rehearsal log" />);
    expect(screen.getByRole('region', { name: 'Rehearsal log' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Rehearsal log' })).toBeInTheDocument();
  });

  it('follows the newest line until the reader scrolls up, and again once they scroll back to the end', () => {
    vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockImplementation(function (this: Element) {
      return this.querySelectorAll('.ln').length * 20;
    });
    const more = (n: number): LogLine[] => Array.from({ length: n }, (_, i) => ({ ts: '', text: `line ${i}`, cls: '' as const }));
    const { rerender } = render(<LogPanel lines={more(10)} source="events" live />);
    const log = screen.getByRole('log');
    expect(log.scrollTop).toBe(200);
    // the reader scrolls up to an earlier line: new lines no longer move the view
    log.scrollTop = 40;
    fireEvent.scroll(log);
    rerender(<LogPanel lines={more(12)} source="events" live />);
    expect(log.scrollTop).toBe(40);
    // ...and back to the end (clientHeight is 0 in jsdom, so "the end" is scrollTop = scrollHeight)
    log.scrollTop = 240;
    fireEvent.scroll(log);
    rerender(<LogPanel lines={more(14)} source="events" live />);
    expect(log.scrollTop).toBe(280);
  });

  it('shows the blinking cursor only while live', () => {
    const { container, rerender } = render(<LogPanel lines={LINES} source="rebuilt" live />);
    expect(container.querySelector('.console .cursor')).toBeInTheDocument();
    rerender(<LogPanel lines={LINES} source="rebuilt" />);
    expect(container.querySelector('.cursor')).toBeNull();
  });

  it('scrolls to the newest line on mount and when lines are added', () => {
    vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockImplementation(function (this: Element) {
      return this.querySelectorAll('.ln').length * 20;
    });
    const { rerender } = render(<LogPanel lines={LINES} source="rebuilt" />);
    const log = screen.getByRole('log');
    expect(log.scrollTop).toBe(100);
    rerender(<LogPanel lines={[...LINES, { ts: '05:03:00', text: 'one more', cls: '' }]} source="rebuilt" live />);
    expect(log.scrollTop).toBe(120);
  });
});

// ---- a run in progress (state 'running': CR01 shows the step states, CR02 animates them) ----

describe('a model with one running step at progress 0', () => {
  it('is live, not done, and points at the running step', () => {
    const m = running();
    expect(m.outcome).toBe('live');
    expect(m.done).toBe(false);
    expect(m.idx).toBe(5);
    expect(m.steps[5].progress).toBe(0);
    expect(ballYard(m)).toBe(60); // the start of the graph step
    expect(scrimmageLines(m)).toEqual({ from: 60, target: 72 });
    expect(isTouchdown(m)).toBe(false);
  });

  it('drive chart: the ball at the step start, both lines, and "Now" in the scorebug', () => {
    const { container } = render(<DriveChart model={running()} />);
    const bug = container.querySelector('.scorebug') as HTMLElement;
    expect(bug).toHaveTextContent('NowKnowledge graph');
    expect(bug).not.toHaveTextContent('—');
    expect(bug).toHaveTextContent('5 plays · 40 yds');
    expect(screen.getByTestId('ball')).toHaveAttribute('transform', `translate(${xAt(60)} ${MID_Y}) rotate(-12)`);
    expect(screen.getByTestId('los')).toHaveAttribute('x1', String(xAt(60)));
    expect(screen.getByTestId('fdl')).toHaveAttribute('x1', String(xAt(72)));
    expect(screen.queryByTestId('run-arc')).toBeNull(); // nothing gained yet
    expect(container.querySelector('[data-row="graph"]')).toHaveClass('running');
    expect(container.querySelector('[data-row="graph"]')).toHaveTextContent('● Knowledge graph');
    expect(container.querySelector('[data-row="player"]')).toHaveTextContent('expected ~1m 35s');
  });

  it('map: the progress ring at zero and the edge flowing in', () => {
    const { container } = render(<PipelineMap model={running()} />);
    const ring = container.querySelector('.node-prog') as SVGElement;
    expect(ring).toBeInTheDocument();
    expect(Number(ring.getAttribute('stroke-dashoffset'))).toBeCloseTo(RING_C);
    expect(container.querySelectorAll('.flow-edge.active')).toHaveLength(1);
    expect(chipOpacity(node(container, 'graph'))).toBe('0');
    expect(container.innerHTML).not.toContain('NaN');
  });

  it('timeline: a striped bar for the running step, no NaN', () => {
    const { container } = render(<Timeline model={running()} sittings={1} />);
    const bar = container.querySelector('[data-step="graph"]') as HTMLElement;
    expect(bar).toHaveClass('running');
    expect(container.querySelector('.sicon.running')).toHaveTextContent('●');
    expect(container.innerHTML).not.toContain('NaN');
    expect(container.querySelectorAll('.bar.planned')).toHaveLength(3); // player, digest, records still expected
  });
});
