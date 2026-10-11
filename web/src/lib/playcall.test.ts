import { afterEach, describe, expect, it, vi } from 'vitest';
import { cell, PLAY_ACTION, PROE, ADOT } from '../pages/explore/playcallFixtures';
import { cellTip, divTint, fmtDiff, fmtRate, niceTop, ordinal, perWord, rateDomain, readSide, writeSide } from './playcall';

describe('play-calling formats', () => {
  it('writes shares as %, PROE as signed points and means in their unit', () => {
    expect(fmtRate(PLAY_ACTION, 0.366)).toBe('36.6%');
    expect(fmtRate(PROE, 0.037)).toBe('+3.7');
    expect(fmtRate(PROE, -0.019)).toBe('−1.9');
    expect(fmtRate(PROE, 0)).toBe('0.0');
    expect(fmtRate(ADOT, 7.71)).toBe('7.7');
    expect(fmtRate(PROE, null)).toBe('—');
    expect(fmtDiff(PLAY_ACTION, 0.125)).toBe('+12.5 pts');
    expect(fmtDiff(ADOT, -0.32)).toBe('−0.3');
  });

  it('writes ordinals and plural words', () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 88.4, 100].map(ordinal)).toEqual(['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '88th', '100th']);
    expect(perWord('dropback', 1)).toBe('dropback');
    expect(perWord('designed run', 3)).toBe('designed runs');
  });

  it('rounds a scale top and shares one scale per column', () => {
    expect(niceTop(0.27)).toBe(0.3);
    expect(niceTop(7.7)).toBe(8);
    expect(rateDomain(PLAY_ACTION, [cell(0.36, 0.24, 100), cell(0.1, 0.24, 100), null]).top).toBeCloseTo(0.4);
    expect(rateDomain(PROE, [cell(0.08, -0.02, 100), cell(-0.12, -0.02, 100)]).span).toBeCloseTo(0.108);
  });

  it('tints by the difference from the league, never a small sample', () => {
    expect(divTint(PLAY_ACTION, cell(0.36, 0.24, 100))?.background).toBe('color-mix(in srgb, var(--s1) 62%, var(--panel))');
    expect(divTint(PLAY_ACTION, cell(0.18, 0.24, 100))?.background).toBe('color-mix(in srgb, var(--s2) 31%, var(--panel))');
    expect(divTint(PLAY_ACTION, cell(0.245, 0.24, 100))).toBeUndefined();
    expect(divTint(PLAY_ACTION, cell(0.9, 0.24, 5))).toBeUndefined();
  });

  it('explains a cell: n, the league, the percentile as "more of it", a small sample', () => {
    const lines = cellTip(PLAY_ACTION, cell(0.5, 0.24, 8, 99), 'KC', 'Season', 20, 'a note');
    expect(lines).toEqual([
      'Play-action per dropback · Season',
      'KC: 50.0% (8 dropbacks, 4 games)',
      'League: 24.0% · +26.0 pts',
      '99th percentile: more of it than 99% of teams (not "better")',
      'Small sample: under 20 dropbacks, not a tendency yet',
      'a note',
      'The QB fakes a handoff before throwing.',
    ]);
    expect(cellTip(PROE, null, 'KC', null, 20)).toEqual(['Pass rate over expected (neutral)', 'KC: no plays yet']);
  });
});

describe('the remembered side', () => {
  afterEach(() => vi.restoreAllMocks());

  it('reads and writes offense / defense in this browser', () => {
    expect(readSide()).toBe('offense');
    writeSide('defense');
    expect(localStorage.getItem('cr.playcallSide')).toBe('defense');
    expect(readSide()).toBe('defense');
  });

  it('falls back to offense when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => writeSide('defense')).not.toThrow();
    expect(readSide()).toBe('offense');
  });
});
