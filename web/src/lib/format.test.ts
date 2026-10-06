import { describe, expect, it } from 'vitest';
import { countdown, daysRange, kickoffLabel, specialChips, weekTitle } from './format';

describe('format', () => {
  it('shows kickoffs in US Eastern with daylight saving', () => {
    expect(kickoffLabel('2026-10-09T00:15:00+00:00')).toBe('Thu, Oct 8, 8:15 PM ET');
    expect(kickoffLabel('2026-11-24T01:15:00+00:00')).toBe('Mon, Nov 23, 8:15 PM ET'); // EST
  });

  it('counts down to the first kickoff', () => {
    const now = Date.parse('2026-10-06T14:00:00Z');
    expect(countdown(now, '2026-10-09T00:15:00+00:00')).toEqual({ text: '58 h 15 m', passed: false });
    expect(countdown(now, '2026-10-06T14:42:00+00:00')).toEqual({ text: '42 m', passed: false });
    expect(countdown(now, '2026-10-06T13:00:00+00:00').passed).toBe(true);
    expect(countdown(now, null).text).toBe('—');
  });

  it('names the slate', () => {
    expect(daysRange(['thu', 'sun', 'mon'])).toBe('Thu–Mon');
    expect(daysRange(['sun'])).toBe('Sun');
    expect(specialChips(['thursday', 'byes', 'morning_kickoff'])).toEqual(['Thursday game', 'Morning kickoff']);
    expect(weekTitle(5, 'REG')).toBe('Week 5');
    expect(weekTitle(19, 'WC')).toBe('Wild Card round');
  });
});
