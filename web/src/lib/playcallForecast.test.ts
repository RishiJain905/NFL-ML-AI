import { describe, expect, it } from 'vitest';
import { JAX_RUSHERS, KC_PROE, KC_RUN_MIDDLE, T_DROPBACK, T_PROE, T_RUN_MIDDLE, T_RUSHERS } from '../pages/explore/playcallForecastFixtures';
import {
  contextName,
  contextShort,
  fmtChange,
  fmtErr,
  forecastDomains,
  forecastTip,
  pctText,
  soFarLabel,
  soFarName,
  splitHint,
  statusCounts,
} from './playcallForecast';

describe('playcallForecast: units', () => {
  it('writes a change in points for shares and PROE, in rushers for rushers (2 decimals)', () => {
    expect(fmtChange(T_DROPBACK, 0.0271)).toBe('+2.7 pts');
    expect(fmtChange(T_PROE, -0.064)).toBe('−6.4 pts');
    expect(fmtChange(T_RUSHERS, -0.2346)).toBe('−0.23');
    expect(fmtChange(T_RUSHERS, 0)).toBe('0.00');
    // a change that rounds to zero has no sign (real data: MIN's PROE, −0.0003)
    expect(fmtChange(T_PROE, -0.0003)).toBe('0.0 pts');
    expect(fmtChange(T_RUSHERS, -0.004)).toBe('0.00');
    expect(fmtChange(T_PROE, null)).toBe('—');
  });

  it('writes a miss in points (shares, PROE) or rushers', () => {
    expect(fmtErr(T_PROE, 0.0568)).toBe('5.7 pts');
    expect(fmtErr(T_RUSHERS, 0.1603)).toBe('0.16 rushers');
    expect(fmtErr(T_DROPBACK, null)).toBe('—');
    expect(pctText(0.8059)).toBe('81%');
    expect(pctText(null)).toBe('—');
  });
});

describe('playcallForecast: names and the tooltip', () => {
  it('calls the baseline "last season" before a team has played', () => {
    expect(soFarName(KC_PROE)).toBe('so far');
    expect(soFarLabel({ games_so_far: 0 })).toBe('Last season');
    expect(soFarName({ games_so_far: 0 })).toBe('last season');
  });

  it("names the second piece the opponent, or the home team's stats crew for run direction", () => {
    expect(contextName(T_PROE, KC_PROE)).toBe('the opponent (JAX)');
    expect(contextName(T_RUN_MIDDLE, KC_RUN_MIDDLE)).toBe("the home team's stats crew (JAX)");
    expect(contextShort(T_PROE, KC_PROE)).toBe('opp');
    expect(contextShort(T_RUN_MIDDLE, KC_RUN_MIDDLE)).toBe('crew JAX');
  });

  it('lists the forecast, its range, the baseline, the league and the pieces (script only for PROE / dropback)', () => {
    const tip = forecastTip(T_PROE, KC_PROE);
    expect(tip[0]).toBe('Pass rate over expected (neutral) · KC vs JAX');
    expect(tip).toContain('Forecast: +3.1 (80% range −8.0 to +14.2)');
    expect(tip).toContain('Season so far: +4.5 (140 plays, 4 games)');
    expect(tip).toContain('Change from so far: −1.4 pts');
    expect(tip).toContain('League: −1.9');
    expect(tip.find((l) => l.startsWith('Pieces'))).toBe(
      "Pieces: KC's rate shrunk toward last season and the league +4.0, the opponent (JAX) −0.4 pts, the game script −0.5 pts",
    );
    const rush = forecastTip(T_RUSHERS, JAX_RUSHERS);
    expect(rush).toContain('Forecast: 4.31 (80% range 4.08 to 4.55)');
    expect(rush.find((l) => l.startsWith('Pieces'))).not.toMatch(/script/);
  });

  it('adds the grade once graded, and the status before', () => {
    const g = forecastTip(T_PROE, { ...KC_PROE, status: 'graded', actual: 0.2, n: 30, in_range: false, beat_season_to_date: false });
    expect(g).toContain('Actual: +20.0 (30 plays) · outside the range');
    expect(g).toContain('Season so far was closer (or as close)');
    expect(forecastTip(T_PROE, { ...KC_PROE, status: 'waiting_ftn' }).join('\n')).toMatch(/FTN not in yet/);
  });
});

describe('playcallForecast: scales, counts, hints', () => {
  it('puts every value of a target on one padded scale', () => {
    const d = forecastDomains([KC_PROE, { ...KC_PROE, team: 'JAX', lo: -0.3, hi: 0.01 }]);
    expect(d.proe.lo).toBeLessThan(-0.3);
    expect(d.proe.hi).toBeGreaterThan(0.142);
    expect(Object.keys(d)).toEqual(['proe']);
  });

  it('counts the waiting statuses in words', () => {
    expect(statusCounts([{ status: 'waiting_ftn' }, { status: 'not_played' }, { status: 'not_played' }, { status: 'graded' }, { status: null }])).toBe(
      '1 waiting for FTN · 2 not played yet',
    );
  });

  it('splits a hint into its text, its command and its tail', () => {
    expect(splitHint("No forecast for week 10 yet: `uv run nfl playcalling forecast --season 2025 --week 10` (after the week's `nfl playcalling build`).")).toEqual({
      text: 'No forecast for week 10 yet.',
      cmd: 'uv run nfl playcalling forecast --season 2025 --week 10',
      tail: "after the week's nfl playcalling build",
    });
    expect(splitHint('No forecast for week 6 yet: `uv run nfl playcalling forecast --season 2026 --week 6`.')).toEqual({
      text: 'No forecast for week 6 yet.',
      cmd: 'uv run nfl playcalling forecast --season 2026 --week 6',
      tail: null,
    });
    expect(splitHint('Plain text.')).toEqual({ text: 'Plain text.', cmd: null, tail: null });
  });
});
