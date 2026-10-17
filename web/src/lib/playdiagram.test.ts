import { describe, expect, it } from 'vitest';
import type { DiagramKey } from '../api/types';
import { DIAGRAM } from '../pages/explore/playbrowserFixtures';
import {
  MIN_SPAN,
  ballAt,
  endTime,
  epaText,
  hasBall,
  lerpKeys,
  marksAt,
  nextPhaseT,
  partialPath,
  pathLength,
  phaseAt,
  positionAt,
  restTime,
  stepTime,
  throwArc,
  trailOf,
  viewBox,
  viewRange,
  yardNumber,
} from './playdiagram';

const KEYS: DiagramKey[] = [
  [0, 10, 20],
  [1, 20, 30],
  [3, 20, 10],
];

describe('playdiagram: interpolation', () => {
  it('holds the first key before it, interpolates linearly, holds the last key after it', () => {
    expect(positionAt(KEYS, -1)).toEqual([10, 20]);
    expect(positionAt(KEYS, 0)).toEqual([10, 20]);
    expect(positionAt(KEYS, 0.5)).toEqual([15, 25]);
    expect(positionAt(KEYS, 2)).toEqual([20, 20]);
    expect(positionAt(KEYS, 3)).toEqual([20, 10]);
    expect(positionAt(KEYS, 9)).toEqual([20, 10]);
  });

  it('a single key never moves; no keys give null', () => {
    expect(positionAt([[0, 5, 6]], 2)).toEqual([5, 6]);
    expect(lerpKeys([], 1)).toBeNull();
  });

  it('two keys at the same time jump to the later one', () => {
    expect(positionAt([[0, 0, 0], [1, 0, 0], [1, 10, 0]], 1)).toEqual([10, 0]);
  });

  it('the ball carries its height', () => {
    expect(ballAt(DIAGRAM.ball, 2)).toEqual([32, 28, 6]);
    const mid = ballAt(DIAGRAM.ball, 1.8);
    expect(mid?.[2]).toBeCloseTo(3.5);
    expect(ballAt([], 1)).toBeNull();
  });
});

describe('playdiagram: phases and steps', () => {
  it('phaseAt is the last phase that has started', () => {
    expect(phaseAt(DIAGRAM.phases, 0)?.name).toBe('presnap');
    expect(phaseAt(DIAGRAM.phases, 0.79)?.name).toBe('presnap');
    expect(phaseAt(DIAGRAM.phases, 0.8)?.name).toBe('snap');
    expect(phaseAt(DIAGRAM.phases, 3.4)?.name).toBe('end');
    expect(phaseAt([], 1)).toBeNull();
  });

  it('steps go one phase at a time; forward past the last phase is the end, back before the first is 0', () => {
    expect(stepTime(DIAGRAM, 0, 1)).toBe(0.8);
    expect(stepTime(DIAGRAM, 1.0, 1)).toBe(1.6); // mid-snap -> the throw
    expect(stepTime(DIAGRAM, 2.8, 1)).toBe(3.4); // the last phase -> the end
    expect(stepTime(DIAGRAM, 1.7, -1)).toBe(0.8); // in the throw -> the snap
    expect(stepTime(DIAGRAM, 0.5, -1)).toBe(0);
    expect(restTime(DIAGRAM)).toBe(2.8);
    expect(restTime({ phases: [], duration: 3 })).toBe(3);
  });

  it('marks appear at their time and stay', () => {
    expect(marksAt(DIAGRAM.marks, 0.9).map((m) => m.kind)).toEqual([]);
    expect(marksAt(DIAGRAM.marks, 1.6).map((m) => m.kind)).toEqual(['fake', 'throw']);
    expect(marksAt(DIAGRAM.marks, 9).map((m) => m.kind)).toEqual(['fake', 'throw', 'end']);
    expect(endTime(DIAGRAM)).toBe(2.8);
    expect(nextPhaseT(DIAGRAM, 1.0)).toBe(1.6);
    expect(nextPhaseT(DIAGRAM, 2.8)).toBe(3.4);
  });

  it('the carrier has the ball once it is on the ground at his spot, after the snap', () => {
    expect(hasBall(DIAGRAM, 47, 26.67, 2.4)).toBe(true);
    expect(hasBall(DIAGRAM, 47, 26.67, 2.0)).toBe(false); // in the air
    expect(hasBall(DIAGRAM, 25, 29.75, 0.5)).toBe(false); // before the snap
  });
});

describe('playdiagram: the view and the paths', () => {
  it('widens a narrow view to MIN_SPAN around its centre and keeps it inside -10..110', () => {
    expect(viewRange({ view: [13, 45] })).toEqual([4, 54]);
    expect(viewRange({ view: [0, 80] })).toEqual([0, 80]);
    const [a, b] = viewRange({ view: [84, 110] });
    expect(b).toBe(110);
    expect(b - a).toBe(MIN_SPAN);
    expect(viewRange({ view: [-10, 10] })[0]).toBe(-10);
    const vb = viewBox({ view: [13, 45], width: 53.33 });
    expect(vb.x).toBe(4);
    expect(vb.w).toBe(50);
    expect(vb.y).toBeLessThan(0);
    expect(vb.h).toBeGreaterThan(53.33);
  });

  it('a trail is the keys passed plus where the player is now', () => {
    expect(trailOf(KEYS, 0.5)).toEqual([
      [10, 20],
      [15, 25],
    ]);
    expect(pathLength([[0, 0], [3, 4]])).toBe(5);
  });

  it('partialPath cuts a polyline by length', () => {
    const pts: [number, number][] = [
      [0, 0],
      [10, 0],
      [10, 10],
    ];
    expect(partialPath(pts, 0.25)).toEqual([
      [0, 0],
      [5, 0],
    ]);
    expect(partialPath(pts, 0.75)).toEqual([
      [0, 0],
      [10, 0],
      [10, 5],
    ]);
    expect(partialPath(pts, 1)).toEqual(pts);
  });

  it('the throw arc runs from the release to the ball, lifted by its height, done at the catch', () => {
    const m = DIAGRAM.marks[1];
    const early = throwArc(DIAGRAM, m, 2.0);
    expect(early.done).toBe(false);
    expect(early.pts[0]).toEqual([18, 29.75]);
    expect(early.pts[early.pts.length - 1][1]).toBeLessThan(28); // lifted above the ground spot
    const done = throwArc(DIAGRAM, m, 3);
    expect(done.done).toBe(true);
    expect(done.pts[done.pts.length - 1]).toEqual([47, 26.67]);
  });

  it('writes the yard numbers and EPA', () => {
    expect(yardNumber(30)).toBe(30);
    expect(yardNumber(70)).toBe(30);
    expect(epaText(1.734)).toBe('+1.73');
    expect(epaText(-0.004)).toBe('0.00');
    expect(epaText(-0.57)).toBe('−0.57');
    expect(epaText(null)).toBe('—');
  });
});
