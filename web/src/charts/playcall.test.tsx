import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ADOT, PLAY_ACTION, PROE, TEAM, cell } from '../pages/explore/playcallFixtures';
import { DirectionStrip, FieldZones } from './FieldZones';
import { HeatTable } from './HeatTable';
import { MatchStrip } from './MatchStrip';
import { PctMeter, RateBar, RateRow } from './RateBar';
import { RunLanes } from './RunLanes';
import { Sparkline } from './Sparkline';

const tipOf = (el: Element) => {
  fireEvent.focus(el);
  return screen.getByRole('tooltip');
};

describe('RateBar', () => {
  it('draws a share from 0 with the league tick on the same scale', () => {
    const { container } = render(<RateBar metric={PLAY_ACTION} cell={cell(0.3, 0.2, 100)} domain={{ top: 0.4 }} />);
    expect((container.querySelector('.rb') as HTMLElement).style.width).toBe('75%');
    expect((container.querySelector('.lgm') as HTMLElement).style.left).toBe('50%');
  });

  it('draws PROE from the league tick, not from 0', () => {
    const { container } = render(<RateBar metric={PROE} cell={cell(0.04, -0.02, 140)} domain={{ span: 0.12 }} />);
    const bar = container.querySelector('.rb') as HTMLElement;
    expect(container.querySelector('.lgm')).toHaveStyle({ left: '50%' });
    expect(bar.style.left).toBe('50%');
    expect(bar.style.width).toBe('25%'); // +6 points over the league on a ±12 scale
    expect(bar).toHaveClass('up');
  });

  it('hatches a small sample and shows an empty track without a value', () => {
    const { container, rerender } = render(<RateBar metric={PLAY_ACTION} cell={cell(0.5, 0.24, 4)} />);
    expect(container.querySelector('.rbar')).toHaveClass('small');
    rerender(<RateBar metric={PLAY_ACTION} cell={null} />);
    expect(container.querySelector('.rbar')).toHaveClass('empty');
    expect(container.querySelector('.rb')).toBeNull();
  });

  it('writes the percentile as an ordinal with a dot', () => {
    const { container } = render(<PctMeter pct={88.4} />);
    expect(screen.getByText('88th')).toBeInTheDocument();
    expect((container.querySelector('.pd') as HTMLElement).style.left).toBe('88%');
  });
});

describe('RateRow', () => {
  it('shows the value, n, the league and the percentile, and a tooltip on focus', () => {
    render(<RateRow metric={PROE} cell={cell(0.037, -0.019, 140, 88)} tip={['Pass rate over expected', 'KC: +3.7 (140 plays)']} />);
    const row = screen.getByRole('img', { name: /Pass rate over expected/ });
    expect(row).toHaveTextContent('+3.7');
    expect(row).toHaveTextContent('n 140');
    expect(row).toHaveTextContent('−1.9');
    expect(row).toHaveTextContent('88th');
    expect(tipOf(row)).toHaveTextContent('KC: +3.7 (140 plays)');
  });

  it('greys a small sample and says so', () => {
    render(<RateRow metric={PLAY_ACTION} cell={cell(0.5, 0.24, 6)} tip={['Play-action']} />);
    const row = screen.getByRole('img', { name: 'Play-action' });
    expect(row).toHaveClass('small');
    expect(row).toHaveTextContent('n 6 · small');
  });

  it('keeps a mean in its own unit', () => {
    render(<RateRow metric={ADOT} cell={cell(7.7, 8.02, 120)} tip={['aDOT']} />);
    expect(screen.getByRole('img', { name: 'aDOT' })).toHaveTextContent('7.7');
  });
});

describe('HeatTable', () => {
  const s = TEAM.situations!;
  const table = () =>
    render(
      <HeatTable
        label="KC offense by down"
        metrics={s.metrics}
        baseline={s.baseline}
        rows={s.families[0].rows}
        win="season"
        who="KC"
        minN={20}
        side="offense"
      />,
    );

  it('puts the All plays baseline first, then the buckets, a column per metric', () => {
    table();
    const t = screen.getByRole('table', { name: 'KC offense by down' });
    const rows = within(t).getAllByRole('row');
    expect(rows.map((r) => r.querySelector('th')?.textContent)).toEqual(['Situation', 'All playsthe row to compare with', '1st down', '4th down']);
    expect(within(t).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Situation', 'PROE', 'Play-action']);
  });

  it('tints a cell by its difference from the league and never tints a small one', () => {
    const { container } = table();
    const cells = Array.from(container.querySelectorAll<HTMLTableCellElement>('td.hc'));
    const first = cells[2]; // 1st down, PROE: +7 points over the league
    expect(first.getAttribute('style')).toMatch(/color-mix\(in srgb, var\(--s1\)/);
    const fourth = cells[4]; // 4th down, n 4
    expect(fourth).toHaveClass('small');
    expect(fourth.getAttribute('style')).toBeNull();
    expect(fourth).toHaveTextContent('n 4');
  });

  it('explains a cell on focus, with the percentile as "more of it"', () => {
    const { container } = table();
    const target = container.querySelectorAll('td.hc .hct')[4];
    const tip = tipOf(target);
    expect(tip).toHaveTextContent('Pass rate over expected (neutral) · 4th down');
    expect(tip).toHaveTextContent('Small sample: under 20 plays');
    expect(tip).toHaveTextContent('not "better"');
  });
});

describe('FieldZones and RunLanes', () => {
  it('draws six zones with a focusable box each, the share and the league', () => {
    const { container } = render(<FieldZones zones={TEAM.field!.zones} win="season" who="KC" minN={20} side="offense" />);
    const hits = container.querySelectorAll('.hit');
    expect(hits).toHaveLength(6);
    expect(container.querySelectorAll('path.route')).toHaveLength(6);
    expect(container.textContent).toContain('24%');
    expect(container.textContent).toContain('lg 25%');
    expect(tipOf(hits[0])).toHaveTextContent('Short left passes');
    expect(screen.getByText(/110 attempts with a depth and direction/)).toBeInTheDocument();
  });

  it("notes on a defense's page that what offenses do against it depends on who it faced", () => {
    const { container } = render(<FieldZones zones={TEAM.field!.zones} win="season" who="vs KC" minN={20} side="defense" />);
    expect(tipOf(container.querySelectorAll('.hit')[3])).toHaveTextContent('depends on the offenses it faced');
  });

  it('draws the directions strip', () => {
    render(<DirectionStrip directions={TEAM.field!.directions} win="season" who="KC" minN={20} side="offense" />);
    expect(screen.getAllByRole('img').map((e) => e.textContent)).toEqual(['left36%lg 37%', 'middle25%lg 26%', 'right39%lg 37%']);
  });

  it('draws the line with seven lanes, one path each, and a tooltip per lane', () => {
    const { container } = render(<RunLanes lanes={TEAM.runs} win="season" who="KC" minN={20} side="offense" />);
    expect(container.querySelectorAll('.hit')).toHaveLength(7);
    expect(container.querySelectorAll('path.route')).toHaveLength(7);
    expect(container.querySelectorAll('rect.pl')).toHaveLength(1); // the center, a square
    expect(container.querySelectorAll('circle.pl')).toHaveLength(8); // 4 linemen, 2 ends, QB, RB
    expect(tipOf(container.querySelectorAll('.hit')[0])).toHaveTextContent('Runs: left end');
  });

  it('hollows a lane on a small sample', () => {
    const lanes = TEAM.runs.map((l) => ({ ...l, windows: { ...l.windows, season: cell(0.2, 0.11, 8) } }));
    const { container } = render(<RunLanes lanes={lanes} win="season" who="KC" minN={20} side="offense" />);
    expect(container.querySelectorAll('g.zbox.small')).toHaveLength(7);
    expect(container.querySelectorAll('path.route.small')).toHaveLength(7);
    expect(screen.getByText(/small sample/)).toBeInTheDocument();
  });
});

describe('Sparkline with a reference line and points', () => {
  it('draws the league line and a tooltip per point, hollow for small samples', () => {
    const { container } = render(
      <Sparkline
        values={[0.02, 0.06, -0.01]}
        reference={-0.019}
        label="PROE by week"
        width={200}
        height={60}
        points={[
          { tip: ['Week 1 · vs LAC'], xLabel: '1' },
          { tip: ['Week 2 · at PHI'], xLabel: '2' },
          { tip: ['Week 3 · vs NYG', 'Small sample'], xLabel: '3', hollow: true },
        ]}
      />,
    );
    expect(container.querySelector('line.refline')).not.toBeNull();
    expect(container.querySelectorAll('circle')).toHaveLength(3);
    expect(container.querySelectorAll('circle')[2].getAttribute('style')).toMatch(/fill: var\(--panel\)/);
    const hits = container.querySelectorAll('.spark-hit');
    expect(hits).toHaveLength(3);
    expect(tipOf(hits[2])).toHaveTextContent('Small sample');
    fireEvent.blur(hits[2]);
    fireEvent.pointerMove(hits[0], { clientX: 10, clientY: 10 });
    expect(screen.getByRole('tooltip')).toHaveTextContent('Week 1 · vs LAC');
  });

  it('stays the plain sparkline without them', () => {
    const { container } = render(<Sparkline values={[1, 2, 3]} label="Elo" />);
    expect(container.querySelector('.spark')).toBeNull();
    expect(container.querySelectorAll('circle')).toHaveLength(1);
  });
});

describe('MatchStrip', () => {
  it('places the offense, the defense and the league on one track, with a hover-only tooltip', () => {
    const { container } = render(
      <MatchStrip metric={PLAY_ACTION} offense={cell(0.36, 0.24, 130)} defense={cell(0.3, 0.24, 8)} league={0.24} tip={['Play-action', 'KC does: 36.0%']} />,
    );
    expect(container.querySelector('.md.o')).not.toBeNull();
    expect(container.querySelector('.md.d')).toHaveClass('small');
    expect(container.querySelector('.ml')).not.toBeNull();
    // the row's name cell is the focusable target (PlayCallsTab); the strip is no extra tab stop
    const strip = container.querySelector('.mv') as HTMLElement;
    expect(strip).toHaveAttribute('aria-hidden', 'true');
    expect(strip).not.toHaveAttribute('tabindex');
    expect(screen.queryByRole('img')).toBeNull();
    fireEvent.pointerMove(strip, { clientX: 10, clientY: 10 });
    expect(screen.getByRole('tooltip')).toHaveTextContent('KC does: 36.0%');
  });
});
