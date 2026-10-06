// The three views and the now-bar, drawn from a model built from a run's events (live.ts).
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { RunEvent } from '../../api/types';
import { DriveChart } from './DriveChart';
import { MID_Y, RING_C, xAt } from './geometry';
import { describeNow, liveModel, type LiveModel } from './live';
import { clockAt, failingRun, rehearsalRun, resumeRun, upTo, weeklyRun } from './liveFixtures';
import { NowBar } from './NowBar';
import { PipelineMap } from './PipelineMap';
import { PipelineView } from './PipelineView';
import { Timeline } from './Timeline';

const model = (events: RunEvent[], sec: number): LiveModel => liveModel({ season: 2026, week: 5, events, nowMs: clockAt(sec) });
const weekly = weeklyRun();
const node = (c: HTMLElement, name: string) => c.querySelector(`[data-node="${name}"]`) as HTMLElement;
const row = (c: HTMLElement, name: string) => c.querySelector(`[data-row="${name}"]`) as HTMLElement;

describe('drive chart, live', () => {
  it('puts the ball part-way along the running step by its progress, with both lines and the dashed run arc', () => {
    const m = model(upTo(weekly, 'graphHalf'), 130); // graph reported 0.5; the clock says 0.41
    const { container } = render(<DriveChart model={m} />);
    expect(screen.getByTestId('ball')).toHaveAttribute('transform', `translate(${xAt(66)} ${MID_Y}) rotate(-12)`);
    expect(screen.getByTestId('los')).toHaveAttribute('x1', String(xAt(60)));
    expect(screen.getByTestId('fdl')).toHaveAttribute('x1', String(xAt(72)));
    expect(screen.getByTestId('run-arc').getAttribute('d')).toContain(`M${xAt(60)} ${MID_Y}`);
    const bug = container.querySelector('.scorebug') as HTMLElement;
    expect(bug).toHaveTextContent('NowKnowledge graph — loading 12/23 · node:Player');
    expect(bug).toHaveTextContent('5 plays · 46 yds');
    expect(bug).toHaveTextContent('2m 10s');
    expect(row(container, 'graph')).toHaveClass('running');
    expect(row(container, 'graph')).toHaveTextContent('loading 12/23 · node:Player');
    expect(row(container, 'player')).toHaveTextContent('expected ~1m 35s');
  });

  it('moves the ball every second by the clock even when the step reports nothing (the GLM wait)', () => {
    const wait = upTo(weekly, 'glmWait');
    const ball = (sec: number) => {
      const { unmount } = render(<DriveChart model={model(wait, sec)} />);
      const t = screen.getByTestId('ball').getAttribute('transform') ?? '';
      unmount();
      return Number(/translate\(([\d.]+)/.exec(t)?.[1]);
    };
    const [a, b, c] = [ball(400), ball(500), ball(600)];
    expect(a).toBeGreaterThan(xAt(85));
    expect(b).toBeGreaterThan(a);
    expect(c).toBeGreaterThan(b);
    expect(c).toBeLessThan(xAt(100)); // never reaches the end zone before the step ends
    const { container } = render(<DriveChart model={model(wait, 502)} />);
    expect(row(container, 'digest')).toHaveTextContent('GLM writing · waiting for the model · 3m 12s · route baseten/fp8 → novita/fp8 → relace');
  });

  it('scores a touchdown when the run ends, and puts the failed step as a fumble where it stopped', () => {
    const done = render(<DriveChart model={model(weekly.events, 9999)} />);
    expect(screen.getByText('TOUCHDOWN')).toBeInTheDocument();
    expect(screen.getByTestId('goalposts')).toHaveAttribute('data-status', 'ok');
    expect(screen.queryByTestId('los')).toBeNull();
    done.unmount();

    const { container } = render(<DriveChart model={model(failingRun().events, 9999)} />);
    expect(screen.getByText('FUMBLE')).toBeInTheDocument();
    expect(screen.queryByText('TOUCHDOWN')).toBeNull();
    expect((container.querySelector('.scorebug') as HTMLElement)).toHaveTextContent('TurnoverFumble at the player step');
    expect(screen.getByTestId('ball')).toHaveAttribute('transform', `translate(${xAt(78.5)} ${MID_Y}) rotate(-12)`);
    expect(row(container, 'player')).toHaveClass('failed');
    expect(row(container, 'player')).toHaveTextContent('refit failed for sacks-edge: calibration seed missing');
    expect(row(container, 'digest')).toHaveTextContent('skipped (an earlier step failed)');
    expect(row(container, 'records')).toHaveTextContent('✓ Run records'); // the extra point is still tried
  });

  it('a resume draws the earlier steps as finished plays and starts the lines where it resumes', () => {
    const run = resumeRun();
    const { container } = render(<DriveChart model={model(upTo(run, 'playerHalf'), 40)} />);
    expect(container.querySelectorAll('[data-play]')).toHaveLength(6); // ingest .. graph, as finished arcs
    expect(row(container, 'graph')).toHaveTextContent('✓ Knowledge graph');
    expect(row(container, 'game')).toHaveTextContent('! Game model'); // degraded in the earlier sitting
    expect(screen.getByTestId('los')).toHaveAttribute('x1', String(xAt(72)));
    expect((container.querySelector('.scorebug') as HTMLElement)).toHaveTextContent('NowPlayer model');
  });

  it('a finished rehearsal scores, but its digest is written, not published', () => {
    const { container } = render(<DriveChart model={model(rehearsalRun().events, 9999)} />);
    expect(screen.getByText('TOUCHDOWN')).toBeInTheDocument();
    expect(container.querySelector('.scorebug') as HTMLElement).toHaveTextContent('FinalTouchdown · digest written (rehearsal)');
    expect(container.textContent).not.toContain('digest published');
  });

  it('a weekly run still says the digest is published', () => {
    const { container } = render(<DriveChart model={model(weekly.events, 9999)} />);
    expect(container.querySelector('.scorebug') as HTMLElement).toHaveTextContent('FinalTouchdown · digest published');
  });

  it('a rehearsal skips five steps and plays the four it has', () => {
    const run = rehearsalRun();
    const { container } = render(<DriveChart model={model(upTo(run, 'playerHalf'), 50)} />);
    expect(row(container, 'ingest')).toHaveTextContent('not in a rehearsal');
    expect(row(container, 'ingest')).toHaveClass('pending');
    expect(row(container, 'records')).toHaveTextContent('not in a rehearsal');
    expect(row(container, 'ready')).toHaveTextContent('✓ Ready check');
    expect(row(container, 'player')).toHaveClass('running');
    expect(screen.getByTestId('goalposts')).toHaveAttribute('data-status', 'skipped');
  });
});

describe('pipeline map, live', () => {
  it('fills the running ring by its progress, flows the edge into it, and shows the GLM wait in its sub-line', () => {
    const m = model(upTo(weekly, 'glmWait'), 502);
    const { container } = render(<PipelineMap model={m} />);
    const ring = node(container, 'digest').querySelector('.node-prog') as SVGElement;
    const progress = m.steps[7].progress;
    expect(progress).toBeCloseTo(0.9 * (502 - 306) / 540, 3);
    expect(Number(ring.getAttribute('stroke-dashoffset'))).toBeCloseTo(RING_C * (1 - progress), 3);
    expect(container.querySelectorAll('.flow-edge.active')).toHaveLength(1);
    expect(node(container, 'digest')).toHaveTextContent('GLM writing · 3m 12s'); // the short form fits the node
    expect(node(container, 'digest')).not.toHaveTextContent('route');
    expect(node(container, 'graph')).toHaveTextContent('2m 30s');
    expect(node(container, 'published')).toHaveTextContent('not yet');
  });

  it('the ring and the text advance with the clock, in place', () => {
    const wait = upTo(weekly, 'glmWait');
    const { container, rerender } = render(<PipelineMap model={model(wait, 400)} />);
    const ring = node(container, 'digest').querySelector('.node-prog') as SVGElement;
    const first = Number(ring.getAttribute('stroke-dashoffset'));
    rerender(<PipelineMap model={model(wait, 500)} />);
    expect(node(container, 'digest').querySelector('.node-prog')).toBe(ring); // the same element
    expect(Number(ring.getAttribute('stroke-dashoffset'))).toBeLessThan(first);
    expect(node(container, 'digest')).toHaveTextContent('GLM writing · 3m 10s');
  });

  it('turns the ring red for a failure, marks skipped steps, and lights Published only after the digest', () => {
    const failed = render(<PipelineMap model={model(failingRun().events, 9999)} />);
    expect(node(failed.container, 'player')).toHaveTextContent('failed');
    expect(node(failed.container, 'digest')).toHaveTextContent('skipped');
    expect(node(failed.container, 'published')).toHaveTextContent('not yet');
    failed.unmount();
    const ok = render(<PipelineMap model={model(weekly.events, 9999)} />);
    expect(node(ok.container, 'published')).toHaveTextContent('LIVE');
    expect(ok.container.querySelector('.node-prog')).toBeNull();
  });

  it('shows a resume\'s earlier steps as finished, with their seconds', () => {
    const run = resumeRun();
    const { container } = render(<PipelineMap model={model(upTo(run, 'playerHalf'), 40)} />);
    expect(node(container, 'graph')).toHaveTextContent('2m 30s');
    expect(node(container, 'graph')).toHaveTextContent('✓');
    expect(node(container, 'game')).toHaveTextContent('!');
    expect(node(container, 'player').querySelector('.node-prog')).toBeInTheDocument();
  });

  it('a finished rehearsal\'s last node says the digest was written, not that it is live or published', () => {
    const { container } = render(<PipelineMap model={model(rehearsalRun().events, 9999)} />);
    const done = node(container, 'published');
    expect(done).toHaveTextContent('DIGEST');
    expect(done).toHaveTextContent('Rehearsal digest');
    expect(done).toHaveTextContent('written, not published');
    expect(done).not.toHaveTextContent('LIVE');
    expect(done).not.toHaveTextContent('week05-digest.md');
  });

  it('a rehearsal\'s skipped nodes say skipped', () => {
    const run = rehearsalRun();
    const { container } = render(<PipelineMap model={model(upTo(run, 'playerHalf'), 50)} />);
    expect(node(container, 'ingest')).toHaveTextContent('skipped');
    expect(node(container, 'graph')).toHaveTextContent('skipped');
    expect(node(container, 'player').querySelector('.node-prog')).toBeInTheDocument();
  });
});

describe('timeline, live', () => {
  const widthOf = (c: HTMLElement, step: string) => parseFloat((c.querySelector(`[data-step="${step}"]`) as HTMLElement).style.width);

  it('grows the striped bar of the running step in place as the clock moves', () => {
    const events = upTo(weekly, 'graphHalf');
    const { container, rerender } = render(<Timeline model={model(events, 130)} sittings={1} />);
    const bar = container.querySelector('[data-step="graph"]') as HTMLElement;
    expect(bar).toHaveClass('running');
    const a = widthOf(container, 'graph');
    expect(a).toBeCloseTo((69 / 866) * 100, 1);
    rerender(<Timeline model={model(events, 160)} sittings={1} />);
    expect(container.querySelector('[data-step="graph"]')).toBe(bar);
    expect(widthOf(container, 'graph')).toBeGreaterThan(a);
    expect(widthOf(container, 'graph')).toBeCloseTo((99 / 866) * 100, 1);
    // the steps still to come are dashed at their expected time; the finished ones are solid
    expect(container.querySelector('[data-step="player"]')).toHaveClass('planned');
    expect(container.querySelector('[data-step="ingest"]')?.className).toBe('bar');
    expect(container.innerHTML).not.toContain('NaN');
    expect(container.querySelectorAll('.gdur')[5]).toHaveTextContent('1m 39s');
  });

  it('the axis grows once a step runs longer than expected, and no bar leaves it', () => {
    const wait = upTo(weekly, 'glmWait');
    const { container } = render(<Timeline model={model(wait, 310 + 2000)} sittings={1} />);
    const bars = [...container.querySelectorAll<HTMLElement>('.bar')];
    for (const b of bars) expect(parseFloat(b.style.left) + parseFloat(b.style.width)).toBeLessThanOrEqual(100.0001);
    expect(container.querySelector('[data-step="digest"]')).toHaveClass('running');
  });

  it('a rehearsal draws bars for its four steps only; the other five say skipped', () => {
    const run = rehearsalRun();
    const { container } = render(<Timeline model={model(upTo(run, 'playerHalf'), 50)} sittings={1} />);
    expect([...container.querySelectorAll('.bar')].map((b) => b.getAttribute('data-step'))).toEqual(['ready', 'game', 'player', 'digest']);
    const durs = [...container.querySelectorAll('.gdur')].map((d) => d.textContent);
    expect(durs[0]).toBe('skipped'); // ingest
    expect(durs[8]).toBe('skipped'); // records
    expect(container.querySelector('[data-step="player"]')).toHaveClass('running');
  });

  it('draws a failure red, with the steps after it dashed, and a resume\'s earlier steps solid', () => {
    const failed = render(<Timeline model={model(failingRun().events, 9999)} sittings={1} />);
    expect(failed.container.querySelectorAll('.bar.failed')).toHaveLength(1);
    expect(failed.container.querySelector('[data-step="digest"]')).toHaveClass('planned');
    failed.unmount();
    const run = resumeRun();
    const { container } = render(<Timeline model={model(upTo(run, 'playerHalf'), 40)} sittings={2} />);
    expect(container.querySelector('[data-step="graph"]')?.className).toBe('bar');
    expect(container.querySelector('p.muted')).toHaveTextContent('this week ran in 2 sittings');
  });
});

describe('switching views mid-run', () => {
  it('keeps the run\'s state in every view, and each view updates in place as the model changes', () => {
    const events = upTo(weekly, 'graphHalf');
    const { container, rerender } = render(<PipelineView model={model(events, 130)} view="drive" sittings={1} />);
    expect(screen.getByTestId('ball')).toBeInTheDocument();
    rerender(<PipelineView model={model(events, 130)} view="map" sittings={1} />);
    expect(node(container, 'graph').querySelector('.node-prog')).toBeInTheDocument();
    expect(node(container, 'graph')).toHaveTextContent('loading 12/23 · node:Player');
    rerender(<PipelineView model={model(events, 130)} view="timeline" sittings={1} />);
    expect(container.querySelector('[data-step="graph"]')).toHaveClass('running');
    rerender(<PipelineView model={model(events, 130)} view="drive" sittings={1} />);
    expect(screen.getByTestId('ball')).toHaveAttribute('transform', `translate(${xAt(66)} ${MID_Y}) rotate(-12)`);
    // a second later: the same svg, the same ball element, the ball further on
    const svg = screen.getByRole('img', { name: /football drive/ });
    const ball = screen.getByTestId('ball');
    rerender(<PipelineView model={model(events, 160)} view="drive" sittings={1} />);
    expect(screen.getByRole('img', { name: /football drive/ })).toBe(svg);
    expect(screen.getByTestId('ball')).toBe(ball);
  });
});

describe('NowBar', () => {
  it('shows the step, its words, the bar, the time spent and the time left', () => {
    render(<NowBar now={describeNow(model(upTo(weekly, 'graphHalf'), 130))} />);
    expect(screen.getByText('Step 6 of 9')).toBeInTheDocument();
    expect(screen.getByText('Knowledge graph')).toBeInTheDocument();
    expect(screen.getByText('loading 12/23 · node:Player')).toBeInTheDocument();
    expect(screen.getByText('2m 10s')).toBeInTheDocument();
    expect(screen.getByText('about 12m 16s left')).toBeInTheDocument();
    const bar = screen.getByRole('progressbar', { name: 'Run progress' });
    expect(bar).toHaveAttribute('aria-valuenow', '15');
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('15%');
    expect((bar.firstElementChild as HTMLElement).style.background).toBe('');
  });

  it('keeps moving through the GLM wait: the bar, the clock and the wait all change each second', () => {
    const wait = upTo(weekly, 'glmWait');
    const read = (sec: number) => {
      const { unmount, container } = render(<NowBar now={describeNow(model(wait, sec))} />);
      const out = {
        pct: Number(screen.getByRole('progressbar').getAttribute('aria-valuenow')),
        text: container.textContent ?? '',
        width: parseFloat(((screen.getByRole('progressbar').firstElementChild as HTMLElement).style.width)),
      };
      unmount();
      return out;
    };
    const a = read(400);
    const b = read(401);
    const c = read(600);
    expect(a.text).toContain('Digest (GLM)');
    expect(a.text).toContain('GLM writing · waiting for the model · 1m 30s');
    expect(b.text).toContain('1m 31s');
    expect(b.text).not.toBe(a.text);
    expect(b.width).toBeGreaterThan(a.width);
    expect(c.width).toBeGreaterThan(b.width);
  });

  it('says Published at the end, with the exit code and that the records were written', () => {
    render(<NowBar now={describeNow(model(weekly.events, 9999))} />);
    expect(screen.getByText('Final')).toBeInTheDocument();
    expect(screen.getByText('Published')).toBeInTheDocument();
    expect(screen.getByText('exit 0 · records written')).toBeInTheDocument();
    expect(screen.getByText('published week 5')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
  });

  it('says Failed · step in red, with a red bar, the reason and the exit code', () => {
    render(<NowBar now={describeNow(model(failingRun().events, 9999))} />);
    expect(screen.getByText('Stopped')).toBeInTheDocument();
    const name = screen.getByText('Failed · player');
    expect(name.style.color).toBe('var(--err)');
    expect(screen.getByText('refit failed for sacks-edge: calibration seed missing')).toBeInTheDocument();
    expect(screen.getByText('exit 1 · records written')).toBeInTheDocument();
    expect(((screen.getByRole('progressbar').firstElementChild as HTMLElement).style.background)).toBe('var(--err)');
  });

  it('pulses the bar when a step runs longer than it usually does, and says so', () => {
    render(<NowBar now={describeNow(model(upTo(weekly, 'glmWait'), 310 + 2000))} />);
    expect(screen.getByText('longer than usual · expected ~9m 00s')).toBeInTheDocument();
    expect(screen.getByRole('progressbar').firstElementChild).toHaveClass('overrun');
  });

  it('survives a bad number', () => {
    render(<NowBar now={{ eyebrow: 'x', name: 'y', sub: 'z', pct: Number.NaN, elapsed: '0 s', small: '', tone: 'run', overrun: false }} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
  });
});
