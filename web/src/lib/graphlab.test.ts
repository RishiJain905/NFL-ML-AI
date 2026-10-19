import { describe, expect, it } from 'vitest';
import {
  algName,
  asMatch,
  int,
  ms,
  msTick,
  pct,
  personSub,
  plural,
  seasonText,
  span,
  splitCommand,
} from './graphlab';

describe('graphlab formatting', () => {
  it('formats milliseconds for chips and axis ticks', () => {
    expect(ms(null)).toBe('—');
    expect(ms(0.35)).toBe('<1 ms');
    expect(ms(79)).toBe('79 ms');
    expect(ms(1234)).toBe('1.23 s');
    expect(ms(31_875)).toBe('31.9 s');
    expect(ms(108_400)).toBe('1.8 min');
    expect(msTick(0.1)).toBe('0.1 ms');
    expect(msTick(100)).toBe('100 ms');
    expect(msTick(1000)).toBe('1 s');
    expect(msTick(100_000)).toBe('100 s');
  });

  it('formats counts, shares, spans and plurals', () => {
    expect(int(8372)).toBe('8,372');
    expect(int(null)).toBe('—');
    expect(pct(0.7129, 1)).toBe('71.3%');
    expect(span(2023, 2023)).toBe('2023');
    expect(span(2018, 2021)).toBe('2018–2021');
    expect(plural(1, 'link')).toBe('1 link');
    expect(plural(3, 'coach', 'coaches')).toBe('3 coaches');
  });

  it('describes a person: position or "coach", team, seasons (one season once)', () => {
    expect(personSub({ kind: 'player', position: 'QB', team: 'KC', seasons: '2018-2026' })).toBe(
      'QB · KC',
    );
    expect(
      personSub({ kind: 'player', position: 'QB', team: 'KC', seasons: '2018-2026' }, true),
    ).toBe('QB · KC · 2018-2026');
    expect(personSub({ kind: 'coach', position: null, team: 'NE', seasons: null })).toBe(
      'coach · NE',
    );
    expect(seasonText('2018-2018')).toBe('2018');
    expect(seasonText('2018-2019')).toBe('2018-2019');
    expect(
      asMatch({
        kind: 'coach',
        id: 'andy-reid',
        name: 'Andy Reid',
        position: 'coach',
        team: 'KC',
        seasons: '2018-2026',
        links: 272,
      }).id,
    ).toBe('coach:andy-reid');
  });

  it('pulls the command out of a server message', () => {
    expect(splitCommand('No families results yet: run `uv run nfl history-graph lab`.')).toEqual({
      text: 'No families results yet: run uv run nfl history-graph lab.',
      command: 'uv run nfl history-graph lab',
    });
    expect(splitCommand('Pick two different people.').command).toBeNull();
  });

  it('names the benchmark algorithms and keeps an unknown slug', () => {
    expect(algName('cypher-shortest')).toBe('Cypher SHORTEST (two-way BFS)');
    expect(algName('new-thing')).toBe('new-thing');
  });
});
