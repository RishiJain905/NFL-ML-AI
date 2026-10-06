import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { HBars } from './HBars';
import { LineChart } from './LineChart';
import { nearestIndex, niceTicks } from './scale';
import { Sparkline } from './Sparkline';

const SERIES = [
  { name: 'Model', color: 'var(--s1)', values: [0.22, 0.215, 0.211] },
  { name: 'Elo', color: 'var(--s2)', values: [0.23, 0.225, 0.222] },
];

describe('chart scales', () => {
  it('makes round ticks', () => {
    expect(niceTicks(0.205, 0.226, 4)).toEqual([0.2, 0.21, 0.22, 0.23]);
    expect(niceTicks(0, 100, 4)).toEqual([0, 25, 50, 75, 100]);
    expect(niceTicks(5, 5).length).toBeGreaterThan(1);
  });

  it('finds the nearest point', () => {
    expect(nearestIndex(50, 50, 552, 18)).toBe(0);
    expect(nearestIndex(602, 50, 552, 18)).toBe(17);
    expect(nearestIndex(10_000, 50, 552, 18)).toBe(17);
    expect(nearestIndex(NaN, 50, 552, 18)).toBe(0);
  });
});

describe('LineChart', () => {
  it('has a legend for two or more series, none for one', () => {
    const { container, rerender } = render(
      <LineChart label="Brier" xs={[1, 2, 3]} series={SERIES} format={(v) => v.toFixed(3)} />,
    );
    expect(container.querySelectorAll('.legend span')).toHaveLength(2);
    rerender(<LineChart label="Brier" xs={[1, 2, 3]} series={[SERIES[0]]} format={(v) => v.toFixed(3)} />);
    expect(container.querySelector('.legend')).toBeNull();
    expect(screen.getByRole('img', { name: 'Brier' })).toBeInTheDocument();
  });

  it('shows a crosshair tooltip with every series value on hover', () => {
    render(
      <LineChart
        label="Brier"
        xs={[1, 2, 3]}
        series={SERIES}
        format={(v) => v.toFixed(3)}
        xFormat={(x) => `Week ${x}`}
      />,
    );
    const box = { left: 0, top: 0, width: 620, height: 230, right: 620, bottom: 230, x: 0, y: 0, toJSON: () => ({}) };
    const svg = screen.getByRole('img', { name: 'Brier' });
    svg.getBoundingClientRect = () => box;
    (svg.parentElement as HTMLElement).getBoundingClientRect = () => box;
    fireEvent.pointerMove(screen.getByTestId('chart-hover'), { clientX: 602, clientY: 50 });
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent('Week 3');
    expect(tip.style.right).toBe('32px'); // right half: opens to the left (Sol review, CR00)
    expect(tip.style.left).toBe('');
    expect(tip).toHaveTextContent('0.211');
    expect(tip).toHaveTextContent('0.222');
    fireEvent.pointerMove(screen.getByTestId('chart-hover'), { clientX: 60, clientY: 50 });
    expect(screen.getByRole('tooltip').style.left).toBe('74px');
    fireEvent.pointerLeave(screen.getByTestId('chart-hover'));
    expect(screen.queryByRole('tooltip')).toBeNull();
  });
});

describe('HBars', () => {
  it('labels each bar and shows a tooltip on focus', () => {
    render(
      <HBars
        label="Step times"
        rows={[
          { label: 'graph', value: 147, tip: 'graph\n2m 27s' },
          { label: 'digest', value: 2329 },
        ]}
        format={(v) => `${v} s`}
      />,
    );
    expect(screen.getByText('2329 s')).toBeInTheDocument();
    fireEvent.focus(screen.getAllByRole('listitem')[0]);
    expect(screen.getByRole('tooltip')).toHaveTextContent('2m 27s');
  });
});

describe('Sparkline', () => {
  it('draws a line and an end dot', () => {
    const { container } = render(<Sparkline label="Elo" values={[1500, 1512, 1498, 1530]} />);
    expect(container.querySelector('polyline')).not.toBeNull();
    expect(container.querySelector('circle')).not.toBeNull();
  });
});
