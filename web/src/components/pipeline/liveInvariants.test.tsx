// Invariants every run's events must keep, whatever the run: checked over the synthetic runs here and
// over every recorded run dropped into `src/test/fixtures/*.jsonl` (a real events file, one JSON
// object per line; `events/<run-id>.jsonl` under the run folder). To add one: copy the file there.
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { RunEvent } from '../../api/types';
import { ballYard } from './geometry';
import { describeNow, foldRun, projectRun } from './live';
import { failingRun, parseEventsJsonl, rehearsalRun, resumeRun, weeklyRun } from './liveFixtures';
import { PipelineView } from './PipelineView';

const recorded = import.meta.glob('../../test/fixtures/*.jsonl', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

const runs: [string, RunEvent[]][] = [
  ['a weekly run', weeklyRun().events],
  ['a failing run', failingRun().events],
  ['a resume', resumeRun().events],
  ['a rehearsal', rehearsalRun().events],
  ...Object.entries(recorded).map(([path, text]): [string, RunEvent[]] => [path.split('/').pop() ?? path, parseEventsJsonl(text)]),
];

const ms = (e: RunEvent) => Date.parse(e.t);
const TERMINAL = ['ok', 'degraded', 'failed', 'skipped'];

describe.each(runs)('events of %s', (_name, events) => {
  const first = events[0];
  const season = first?.type === 'run_start' ? first.season : 2026;
  const week = first?.type === 'run_start' ? first.week : 5;
  const project = (list: RunEvent[], nowMs: number) => projectRun(foldRun(list), { season, week, nowMs });
  const last = events[events.length - 1];
  const over = events.some((e) => e.type === 'run_end');

  it('are in order, from one run, and start with run_start', () => {
    expect(events.length).toBeGreaterThan(0);
    expect(first.type).toBe('run_start');
    events.forEach((e, i) => {
      expect(e.seq).toBe(i + 1);
      expect(e.run_id).toBe(first.run_id);
      expect(Number.isFinite(ms(e))).toBe(true);
    });
  });

  it('fold into a finished model with no NaN: every step in a final state, the clock stopped', () => {
    const m = project(events, ms(last) + 3_600_000);
    expect(m.steps).toHaveLength(9);
    if (over) {
      expect(m.done).toBe(true);
      expect(m.idx).toBe(-1);
      for (const s of m.steps) expect(TERMINAL).toContain(s.status);
    }
    for (const s of m.steps) {
      expect(Number.isFinite(s.seconds)).toBe(true);
      expect(s.progress).toBeGreaterThanOrEqual(0);
      expect(s.progress).toBeLessThanOrEqual(1);
    }
    expect(project(events, ms(last) + 7_200_000).elapsed).toBe(m.elapsed); // a later clock changes nothing
    const now = describeNow(m);
    expect(JSON.stringify(now)).not.toMatch(/NaN|undefined|null/);
  });

  it('only ever move forward while the run goes: the ball, the time spent and the steps done', () => {
    let yard = 0;
    let elapsed = 0;
    let done = 0;
    const stride = Math.max(1, Math.floor(events.length / 80));
    for (let n = 1; n <= events.length; n += stride) {
      const slice = events.slice(0, n);
      const m = project(slice, ms(slice[slice.length - 1]));
      const y = ballYard(m);
      const finished = m.steps.filter((s) => s.status === 'ok' || s.status === 'degraded').length;
      expect(y).toBeGreaterThanOrEqual(yard - 0.5); // a failed step may settle a hair behind its last estimate
      expect(m.elapsed).toBeGreaterThanOrEqual(elapsed - 1);
      expect(finished).toBeGreaterThanOrEqual(done);
      expect(m.remaining).toBeGreaterThanOrEqual(0);
      yard = y;
      elapsed = m.elapsed;
      done = finished;
    }
  });

  it('draw in every view at every stage without NaN or undefined', () => {
    const stride = Math.max(1, Math.floor(events.length / 12));
    for (let n = 1; n <= events.length; n += stride) {
      const slice = events.slice(0, n);
      const m = project(slice, ms(slice[slice.length - 1]) + 5_000);
      for (const view of ['drive', 'map', 'timeline'] as const) {
        const { container, unmount } = render(<PipelineView model={m} view={view} sittings={1} />);
        expect(container.innerHTML).not.toContain('NaN');
        expect(container.innerHTML).not.toContain('undefined');
        unmount();
      }
    }
  });
});

describe('recorded runs', () => {
  it(`found ${Object.keys(recorded).length} in src/test/fixtures (drop an events .jsonl there to check it)`, () => {
    expect(Array.isArray(Object.keys(recorded))).toBe(true);
  });
});
