import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CalibrationChart } from './CalibrationChart';

const BINS = [
  { predicted: 0.45, observed: 0.43, games: 60 },
  { predicted: 0.15, observed: 0.18, games: 5 },
  { predicted: 0.85, observed: 0.86, games: 22 },
];

describe('CalibrationChart', () => {
  it('draws one point per bin sized by games, the diagonal and a legend', () => {
    const { container } = render(<CalibrationChart label="Calibration, 2025" bins={BINS} />);
    expect(screen.getByRole('group', { name: 'Calibration, 2025' })).toBeInTheDocument();
    const dots = screen.getAllByRole('img');
    expect(dots).toHaveLength(3);
    const r = (name: RegExp) => Number(screen.getByRole('img', { name }).getAttribute('r'));
    expect(r(/60 games/)).toBeGreaterThan(r(/22 games/));
    expect(r(/22 games/)).toBeGreaterThan(r(/5 games/));
    expect(container.querySelector('line[stroke-dasharray]')).not.toBeNull();
    expect(container.querySelector('.legend')).toHaveTextContent('Perfect calibration');
    expect(container.querySelector('polyline')).not.toBeNull();
  });

  it('shows a tooltip on keyboard focus and on hover', () => {
    render(<CalibrationChart label="Calibration" bins={BINS} />);
    const dot = screen.getByRole('img', { name: /60 games/ });
    fireEvent.focus(dot);
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent('Predicted 45.0%');
    expect(tip).toHaveTextContent('Happened 43.0%');
    expect(tip).toHaveTextContent('60 games');
    fireEvent.blur(dot);
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.pointerEnter(screen.getByRole('img', { name: /5 games/ }));
    expect(screen.getByRole('tooltip')).toHaveTextContent('Predicted 15.0%');
  });

  it('Escape hides the tooltip and keeps focus on the point (Sol review, CR03)', () => {
    render(<CalibrationChart label="Calibration" bins={BINS} />);
    const dot = screen.getByRole('img', { name: /60 games/ });
    dot.focus();
    fireEvent.focus(dot);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.keyDown(dot, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(document.activeElement).toBe(dot);
  });

  it('copes with a single bin and with no bins', () => {
    const { container, rerender } = render(<CalibrationChart label="One" bins={[BINS[0]]} />);
    expect(container.querySelector('polyline')).toBeNull();
    expect(screen.getAllByRole('img')).toHaveLength(1);
    rerender(<CalibrationChart label="None" bins={[]} />);
    expect(screen.queryAllByRole('img')).toHaveLength(0);
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });
});
