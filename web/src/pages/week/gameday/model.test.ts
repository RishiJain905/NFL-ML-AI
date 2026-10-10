import { describe, expect, it } from 'vitest';
import { CALL_FOURTH, CALL_TOSS, game } from './gamedayFixtures';
import {
  ago,
  defaultPick,
  distanceBand,
  kickBand,
  kickerLong,
  labelLine,
  lastPlayAge,
  leadText,
  newer,
  optionTone,
  pregamePick,
  verdict,
} from './model';

describe('Game day wording', () => {
  it('says the label in plain words', () => {
    expect(labelLine({ label: 'Confident', gap: 0.014, boot_share: 0.95 })).toBe('The call held in 19 of 20 re-checks of the models.');
    expect(labelLine({ label: 'Confident', gap: 0.07, boot_share: null })).toMatch(/More than 5 points clear/);
    expect(labelLine({ label: 'Lean', gap: 0.02, boot_share: 0.7 })).toBe('The call held in 14 of 20 re-checks: likely right, not certain.');
    expect(labelLine({ label: 'Toss-up', gap: 0.004, boot_share: 0.9 })).toMatch(/^Under 1 point apart/);
    expect(labelLine({ label: 'Toss-up', gap: 0.02, boot_share: 0.5 })).toBe("It flipped in 10 of 20 re-checks: the data can't separate them.");
  });

  it('puts the call or TOSS-UP on the big line', () => {
    expect(verdict(CALL_FOURTH.fourth!)).toEqual({
      big: 'Go for it',
      sub: 'Worth 1.4 more points of win chance than the field goal',
      toss: false,
    });
    expect(verdict(CALL_TOSS.fourth!)).toEqual({ big: 'Toss-up', sub: 'Field goal by 0.2 points over going for it', toss: true });
  });

  it('gives the accent to the best option only, and none in full on a toss-up', () => {
    expect(['go', 'fg', 'punt'].map((c) => optionTone(CALL_FOURTH.fourth!, c as 'go'))).toEqual(['best', '', '']);
    expect(['go', 'fg', 'punt'].map((c) => optionTone(CALL_TOSS.fourth!, c as 'go'))).toEqual(['tied', 'tied', '']);
  });

  it('formats the game facts', () => {
    const g = game();
    expect(leadText(g, 'BBB')).toBe('BBB up 3');
    expect(leadText(g, 'AAA')).toBe('AAA down 3');
    expect(pregamePick(g)).toBe('AAA 55%');
    expect(ago(35)).toBe('35 s ago');
    expect(ago(78)).toBe('1 min ago');
    expect(lastPlayAge(35, Date.parse('2026-10-10T18:00:00Z'), Date.parse('2026-10-10T18:00:10Z'))).toBe(45);
    expect(lastPlayAge(35, null, Date.now())).toBe(35);
    expect(newer('2026-10-04T19:57:50Z', '2026-10-04T19:57:45Z')).toBe(true);
    expect(newer(null, '2026-10-04T19:57:45Z')).toBe(false);
  });

  it('bands distances and reads the kicker long', () => {
    expect([29, 30, 49, 52].map(kickBand)).toEqual(['<30', '30-39', '40-49', '50+']);
    expect([2, 3, 5, 6].map(distanceBand)).toEqual(['1-2', '3-5', '3-5', '6+']);
    expect(kickerLong(61, 2023)).toBe('career long 61 (2023)');
    expect(kickerLong(58, 52)).toBe('long 58 (career) · 52 this season');
  });

  it('opens on the first 3rd / 4th down that is on', () => {
    const a = game({ event: '1', decision_down: false });
    const b = game({ event: '2', decision_down: true });
    expect(defaultPick([a, b])).toBe('2');
    expect(defaultPick([a])).toBe('1');
    expect(defaultPick([game({ state: 'pre' })])).toBeNull();
  });
});
