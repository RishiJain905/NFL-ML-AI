import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PreflightResponse, RunCurrent } from '../../api/types';
import { RunUiProvider } from '../../components/run/RunUiProvider';
import {
  cdfOf,
  centreOf,
  chanceAbove,
  chanceBetween,
  chartDomain,
  clearBest,
  defaultAbove,
  markWords,
  niceTicks,
  oneIn,
  pctText,
  simpleCdf,
  spanText,
  byProjection,
} from '../../lib/forecastlab';
import { META, WEEKS, mockApi, renderApp } from '../../test/utils';
import {
  BACK,
  CURVE_BACK,
  CURVE_END,
  CURVE_RECEIVER,
  CURVE_RECEIVER_GRADED,
  LAB_UP_TO_DATE,
  OUTCOMES_CARRIES,
  OUTCOMES_GRADED,
  OUTCOMES_OK,
  RECEIVER,
} from './forecastlab/labFixtures';
import { OutcomesTab } from './OutcomesTab';

const IDLE: RunCurrent = { state: 'idle', run: null };
const W5 = { season: 2026, week: 5, isCurrent: false, lastPublishedWeek: 4 };
const BASE = '/api/weeks/2026/5/outcomes';
const curveUrl = (pid: string, target: string) => `${BASE}/curve?player=${pid}&target=${target}`;

function api(paths: Record<string, unknown>) {
  return mockApi({
    '/api/meta': META,
    '/api/weeks': WEEKS,
    '/api/preflight': { forecastlab: LAB_UP_TO_DATE } as unknown as PreflightResponse,
    '/api/run/current': IDLE,
    [BASE]: OUTCOMES_OK,
    [`${BASE}?target=rec_yds-wrte`]: OUTCOMES_OK,
    [`${BASE}?target=carries-rb`]: OUTCOMES_CARRIES,
    [curveUrl(RECEIVER.player_id, 'rec_yds-wrte')]: CURVE_RECEIVER,
    [curveUrl(CURVE_END.player_id, 'rec_yds-wrte')]: CURVE_END,
    [curveUrl(BACK.player_id, 'carries-rb')]: CURVE_BACK,
    ...paths,
  });
}
const render = (el: ReactNode, route = '/week/2026/5/outcomes') =>
  renderApp(<RunUiProvider>{el}</RunUiProvider>, { route, path: '/week/:season/:week/:tab' });
/** The curve panel once the whole curve has come back (its chart is drawn). */
const panel = async (name: RegExp | string = /'s curve$/) => {
  const p = await screen.findByRole('region', { name });
  await within(p).findByRole('img');
  return p;
};

afterEach(() => vi.unstubAllGlobals());

describe('the curve arithmetic (lib/forecastlab)', () => {
  const c = { grid_lo: 0, cdf: [0.1, 0.3, 0.6] }; // the list stops before 1.0: past its end is 1
  const F = cdfOf(c);

  it('reads a whole-number curve, 0 below it and 1 past its end', () => {
    expect([F(-1), F(0), F(1), F(2), F(3), F(99)]).toEqual([0, 0.1, 0.3, 0.6, 1, 1]);
    expect(F(1.7)).toBe(0.3); // whole numbers
  });

  it('"more than x" for yardage, "x or more" for counts, "between a and b" with both ends in', () => {
    expect(chanceAbove(F, 'amount', 1)).toBeCloseTo(0.7);
    expect(chanceAbove(F, 'amount', 1.5)).toBeCloseTo(0.7);
    expect(chanceAbove(F, 'count', 2)).toBeCloseTo(0.7); // 2 or more = 1 - P(1 or fewer)
    expect(chanceAbove(F, 'count', 1.5)).toBeCloseTo(0.7); // marks are x.5: "2 or more"
    expect(chanceBetween(F, 1, 2)).toBeCloseTo(0.5);
    expect(chanceBetween(F, 0, 0)).toBeCloseTo(0.1);
  });

  it("the simple curve matches the reader's (curves.py bell_cdf) on real week-10 rows", () => {
    // Jaxon Smith-Njigba, receiving yards: P10 15.6, P50 81.6, P90 147.1; the reader's simple
    // curve gives 0.734 to more than 49.5 and 0.116 to 75-89
    const jsn = { kind: 'amount', p10: 15.6, p50: 81.6, p90: 147.1, mean: 81.59 };
    const S = simpleCdf(jsn, -15);
    expect(chanceAbove(S, 'amount', 49.5)).toBeCloseTo(0.7342, 3);
    expect(chanceBetween(S, 75, 89)).toBeCloseTo(0.1162, 3);
    expect(centreOf(jsn)).toBe(82);
    // James Cook, carries: a Poisson at 19.63; the reader gives 0.423 to 18-22
    const cook = { kind: 'count', p10: 10, p50: 19, p90: 31, mean: 19.63 };
    expect(centreOf(cook)).toBe(20);
    expect(chanceBetween(simpleCdf(cook, 0), 18, 22)).toBeCloseTo(0.4226, 3);
  });

  it('words: percentages, "1 in N", marks, the opening number', () => {
    expect([pctText(0.004), pctText(0.115), pctText(0.996), pctText(null)]).toEqual(['<1%', '12%', '>99%', '—']);
    expect([oneIn(0.115), oneIn(0.77), oneIn(0.3), oneIn(0.97), oneIn(0)]).toEqual(['about 1 in 9', 'about 8 in 10', 'about 1 in 3', 'almost always', '']);
    expect([markWords('amount', 49.5), markWords('count', 4.5)]).toEqual(['more than 49.5', '5 or more']);
    expect(defaultAbove({ kind: 'amount', marks: [24.5, 49.5, 74.5] })).toBe(49.5);
    expect(defaultAbove({ kind: 'count', marks: [9.5, 12.5, 14.5] })).toBe(13);
    // with a projection: the mark nearest it (a low-projection tight end, a star receiver)
    expect(defaultAbove({ kind: 'amount', marks: [14.5, 24.5, 49.5, 74.5, 99.5] }, 2.3)).toBe(14.5);
    expect(defaultAbove({ kind: 'amount', marks: [14.5, 24.5, 49.5, 74.5, 99.5] }, 81.6)).toBe(74.5);
    expect(defaultAbove({ kind: 'count', marks: [9.5, 12.5, 14.5] }, 18.4)).toBe(15);
    expect([spanText(51, 65), spanText(-5, 9)]).toEqual(['51–65', '−5 to 9']);
  });

  it('the chart: yardage starts at 0, ticks are round', () => {
    expect(chartDomain({ grid_lo: -15, cdf: [...Array(15).fill(0), 0.2, 0.5, 0.9, 1] }, 'amount')[0]).toBe(0);
    expect(chartDomain({ grid_lo: -15, cdf: [...Array(30).fill(0), 0.2, 0.5, 0.9, 1] }, 'amount')[0]).toBe(15); // far from 0: from where it starts
    expect(chartDomain(CURVE_BACK, 'count')[0]).toBeGreaterThan(0);
    expect(niceTicks(0, 160)).toEqual([0, 50, 100, 150]);
  });

  it('bold only a clear winner: a 0.1-yard gap is nothing, a 0.7-yard one is', () => {
    const keys = ['a', 'b', 'c'] as const;
    expect(clearBest({ a: 47.9, b: 48.0, c: 52.3 }, [...keys])).toBeNull();
    expect(clearBest({ a: 15.2, b: 15.9, c: 16.8 }, [...keys])).toBe('a');
    expect(clearBest({ a: null, b: 2.4, c: 2.6 }, [...keys])).toBe('b');
  });
});

describe('Outcomes tab', () => {
  it('opens on the server\'s stat and its first player: the two boxes, the chart, the facts', async () => {
    api({});
    render(<OutcomesTab {...W5} />);
    const p = await panel();
    expect(within(p).getByRole('heading', { name: 'A. Receiver' })).toBeInTheDocument();
    expect(p).toHaveTextContent('Projection 58 yards · 80% range 22–104');
    expect(p).toHaveTextContent('if he plays');
    const F = cdfOf(CURVE_RECEIVER);
    const S = simpleCdf(RECEIVER, CURVE_RECEIVER.grid_lo);
    // the number box opens on the stat's middle mark; the band box on ±band around the projection
    const above = within(p).getByLabelText(/Chance of more than/);
    expect(above).toHaveValue(49.5);
    expect(p).toHaveTextContent(`${pctText(chanceAbove(F, 'amount', 49.5))}${oneIn(chanceAbove(F, 'amount', 49.5))}`);
    expect(p).toHaveTextContent(`the simple curve: ${pctText(chanceAbove(S, 'amount', 49.5))}`);
    expect(within(p).getByLabelText(/Within ±/)).toHaveValue(7);
    expect(p).toHaveTextContent(`51–65 yards · the simple curve: ${pctText(chanceBetween(S, 51, 65))}`);
    expect(within(p).getByRole('img', { name: /chance of more than each number of receiving yards/ })).toBeInTheDocument();
    expect(p).toHaveTextContent('Built from 900 past projections like this one');
    // the note that says why ranges are wide, once
    expect(screen.getAllByText(/That's the sport, not the model/)).toHaveLength(1);
  });

  it('typing a number or a band recomputes; a blank box asks for a number', async () => {
    api({});
    render(<OutcomesTab {...W5} />);
    const p = await panel();
    const F = cdfOf(CURVE_RECEIVER);
    fireEvent.change(within(p).getByLabelText(/Chance of more than/), { target: { value: '100' } });
    expect(p).toHaveTextContent(pctText(chanceAbove(F, 'amount', 100)));
    fireEvent.change(within(p).getByLabelText(/Chance of more than/), { target: { value: '' } });
    expect(within(p).getByText('Type a number.')).toBeInTheDocument();
    fireEvent.change(within(p).getByLabelText(/Within ±/), { target: { value: '20' } });
    expect(p).toHaveTextContent(`38–78 yards`);
    expect(p).toHaveTextContent(pctText(chanceBetween(F, 38, 78)));
    fireEvent.click(within(p).getByRole('button', { name: 'Between' }));
    fireEvent.change(within(p).getByLabelText('Between'), { target: { value: '60' } });
    fireEvent.change(within(p).getByLabelText('and'), { target: { value: '90' } });
    expect(p).toHaveTextContent(`60–90 yards · the simple curve`);
    expect(p).toHaveTextContent(pctText(chanceBetween(F, 60, 90)));
    fireEvent.change(within(p).getByLabelText('and'), { target: { value: '10' } });
    expect(within(p).getByText('Type two numbers, the smaller first.')).toBeInTheDocument();
  });

  it('a stat from the picker asks the server for that stat; counts read "15 or more" and draw bars', async () => {
    const f = api({});
    render(<OutcomesTab {...W5} />);
    await panel();
    const picker = screen.getByRole('group', { name: 'Stat' });
    expect(within(picker).getByRole('button', { name: 'WR/TE receiving yards' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(picker).getByRole('button', { name: 'RB carries' }));
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u) === `${BASE}?target=carries-rb`)).toBe(true));
    const p = await panel("C. Back's curve");
    expect(within(p).getByLabelText(/Chance of/)).toHaveValue(15); // the mark nearest his 18.4: 14.5
    expect(p).toHaveTextContent('carries or more');
    expect(within(p).getByLabelText(/Within ±/)).toHaveValue(2);
    expect(p).toHaveTextContent('carries of 18');
    expect(p).not.toHaveTextContent('No carries at all'); // "no yards" is for yardage only
    expect(within(p).getByRole('img', { name: /Bars: the chance of each number of carries/ })).toBeInTheDocument();
    const list = screen.getByRole('region', { name: 'Every curve for RB carries' });
    expect(within(list).getByRole('columnheader', { name: /13 or more/ })).toBeInTheDocument();
  });

  it('picking a row opens his curve; the list filters and switches its mark', async () => {
    api({});
    render(<OutcomesTab {...W5} />);
    await panel();
    const list = screen.getByRole('region', { name: 'Every curve for WR/TE receiving yards' });
    fireEvent.click(within(list).getByRole('button', { name: 'B. End' }));
    expect(await panel("B. End's curve")).toBeInTheDocument();
    expect(within(list).getByRole('button', { name: 'B. End' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.change(within(list).getByRole('combobox', { name: 'Chance of' }), { target: { value: '2' } });
    expect(within(list).getByRole('columnheader', { name: /more than 74.5/ })).toBeInTheDocument();
    expect(list).toHaveTextContent('33%30%');
    fireEvent.change(within(list).getByRole('searchbox', { name: 'Find a player or team' }), { target: { value: 'zzz' } });
    expect(within(list).getByText(/No player matches/)).toBeInTheDocument();
  });

  it('rows are sorted by the projection shown (counts: the mean)', () => {
    const rows = [
      { kind: 'count', p10: 4, p50: 8, p90: 13, mean: 8.1, id: 'a' },
      { kind: 'count', p10: 4, p50: 8, p90: 13, mean: 8.5, id: 'b' },
      { kind: 'count', p10: 4, p50: 8, p90: 13, mean: 8.1, id: 'c' },
    ];
    expect(byProjection(rows).map((x) => x.id)).toEqual(['b', 'a', 'c']);
  });

  it('a long stat shows its first 40 rows, then "Show all"', async () => {
    const many = { ...OUTCOMES_OK, rows: Array.from({ length: 45 }, (_, i) => ({ ...RECEIVER, player_id: `00-00${String(i).padStart(5, '0')}`, player: `Player ${i}` })) };
    api({ [BASE]: many, [curveUrl('00-0000000', 'rec_yds-wrte')]: { ...CURVE_RECEIVER, player_id: '00-0000000', player: 'Player 0' } });
    render(<OutcomesTab {...W5} />);
    await panel();
    const list = screen.getByRole('region', { name: 'Every curve for WR/TE receiving yards' });
    expect(within(list).getAllByRole('button', { name: /^Player \d+$/ })).toHaveLength(40);
    fireEvent.click(within(list).getByRole('button', { name: 'Show all 45' }));
    expect(within(list).getAllByRole('button', { name: /^Player \d+$/ })).toHaveLength(45);
  });

  it('the stat and the player come from the URL (a shared link lands on the same curve)', async () => {
    api({});
    render(<OutcomesTab {...W5} />, `/week/2026/5/outcomes?stat=rec_yds-wrte&player=${CURVE_END.player_id}`);
    expect(await screen.findByRole('region', { name: "B. End's curve" })).toBeInTheDocument();
  });

  it('where the two curves disagree: a sentence per row and Show', async () => {
    api({});
    render(<OutcomesTab {...W5} />);
    await panel();
    const gaps = screen.getByRole('region', { name: 'Where the curve and the simple curve disagree most' });
    expect(gaps).toHaveTextContent('A. Receiver TB · WR/TE receiving yards, projected 58: chance of more than 24.5');
    expect(gaps).toHaveTextContent('curve 86% · simple 80%');
    fireEvent.click(within(gaps).getByRole('button', { name: "Show B. End's receiving yards" }));
    expect(await screen.findByRole('region', { name: "B. End's curve" })).toBeInTheDocument();
  });

  it('after the games: the actual on the list and the panel, "didn\'t play", and how the curves did', async () => {
    api({ [BASE]: OUTCOMES_GRADED, [curveUrl(RECEIVER.player_id, 'rec_yds-wrte')]: CURVE_RECEIVER_GRADED });
    render(<OutcomesTab {...W5} />);
    const p = await panel();
    expect(p).toHaveTextContent('He had 94 yards');
    expect(within(p).getByText('what happened')).toBeInTheDocument();
    const list = screen.getByRole('region', { name: 'Every curve for WR/TE receiving yards' });
    expect(within(list).getByRole('columnheader', { name: 'Actual' })).toBeInTheDocument();
    expect(within(list).getByText("didn't play")).toBeInTheDocument();
    const score = screen.getByRole('region', { name: 'How the curves did' });
    const rows = within(score).getAllByRole('row');
    const rec = rows.find((r) => r.textContent?.startsWith('WR/TE receiving yards'))!;
    const pass = rows.find((r) => r.textContent?.startsWith('pass_yds-qb'))!; // a stat the week's list doesn't name: its key
    expect(within(rec).getByText('15.2')).toHaveClass('best');
    expect(within(pass).getByText('47.9')).not.toHaveClass('best'); // 0.1 below the simple curve: no emphasis
    fireEvent.click(within(score).getByRole('button', { name: 'Season to date' }));
    expect(within(score).getByText('470')).toBeInTheDocument();
  });

  it('a player who didn\'t play says so on his curve', async () => {
    api({ [BASE]: OUTCOMES_GRADED, [curveUrl(CURVE_END.player_id, 'rec_yds-wrte')]: { ...CURVE_END, played: false } });
    render(<OutcomesTab {...W5} />, `/week/2026/5/outcomes?player=${CURVE_END.player_id}`);
    const p = await panel("B. End's curve");
    expect(await within(p).findByText(/He didn't play: a curve is "if he plays"/)).toBeInTheDocument();
  });

  it('no betting words anywhere on the tab', async () => {
    api({ [BASE]: OUTCOMES_GRADED, [curveUrl(RECEIVER.player_id, 'rec_yds-wrte')]: CURVE_RECEIVER_GRADED });
    const { container } = render(<OutcomesTab {...W5} />);
    await panel();
    expect(container.textContent).not.toMatch(/\b(odds|over\/under|over-under|wager|bet|betting|pick|line)s?\b/i);
  });
});
