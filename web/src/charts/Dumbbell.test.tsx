import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Dumbbell, type DumbbellGame } from './Dumbbell';

const GAMES: DumbbellGame[] = [
  { game_id: 'a', away: 'BAL', home: 'KC', p_model_only: 0.58, p_market: 0.51 },
  { game_id: 'b', away: 'BUF', home: 'MIA', p_model_only: 0.64, p_market: 0.7 },
  { game_id: 'c', away: 'NYJ', home: 'NE', p_model_only: 0.46, p_market: 0.38 },
  { game_id: 'd', away: 'CHI', home: 'MIN', p_model_only: 0.49, p_market: null },
];

describe('Dumbbell', () => {
  it('sorts rows by the gap, leaves out games without a market and writes the gap out', () => {
    const { container } = render(<Dumbbell games={GAMES} label="Model-only versus market" />);
    expect([...container.querySelectorAll('.db-label')].map((n) => n.textContent)).toEqual(['NYJ at NE', 'BAL at KC', 'BUF at MIA']);
    expect([...container.querySelectorAll('.db-gap')].map((n) => n.textContent)).toEqual(['+8 pts', '+7 pts', '−6 pts']);
    expect(screen.getByRole('group', { name: 'Model-only versus market' })).toBeInTheDocument();
  });

  it('has a legend naming both marks', () => {
    const { container } = render(<Dumbbell games={GAMES} label="x" />);
    const legend = container.querySelector('.legend') as HTMLElement;
    expect(legend).toHaveTextContent('Model only');
    expect(legend).toHaveTextContent('Market');
    expect(legend.querySelectorAll('i')).toHaveLength(2);
  });

  it('puts the two dots at the two chances', () => {
    const { container } = render(<Dumbbell games={[GAMES[2]]} label="x" />);
    const row = container.querySelector('.db-row') as HTMLElement;
    expect((row.querySelector('.db-dot.model') as HTMLElement).style.left).toBe('46%');
    expect((row.querySelector('.db-dot.market') as HTMLElement).style.left).toBe('38%');
    const link = row.querySelector('.db-link') as HTMLElement;
    expect(link.style.left).toBe('38%');
    expect(parseFloat(link.style.width)).toBeCloseTo(8);
  });

  it('has a named, focusable row with a tooltip on focus and on hover', () => {
    render(<Dumbbell games={GAMES} label="x" />);
    const row = screen.getByRole('img', { name: 'NYJ at NE: model only NE 46%, market NE 38%, gap +8 pts' });
    expect(row).toHaveAttribute('tabindex', '0');
    fireEvent.focus(row);
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent('NYJ at NE');
    expect(tip).toHaveTextContent('model only: NE 46%');
    expect(tip).toHaveTextContent('market: NE 38%');
    expect(tip).toHaveTextContent('gap +8 pts');
    fireEvent.blur(row);
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.pointerMove(row, { clientX: 20, clientY: 20 });
    expect(screen.getByRole('tooltip')).toHaveTextContent('gap +8 pts');
  });

  it('rounds the gap before signing it: no "−0 pts"', () => {
    const { container } = render(
      <Dumbbell games={[{ game_id: 'z', away: 'IND', home: 'PIT', p_model_only: 0.496, p_market: 0.5 }]} label="x" />,
    );
    expect(container.querySelector('.db-gap')).toHaveTextContent(/^0 pts$/);
  });

  it('renders nothing when no game has both chances', () => {
    const { container } = render(<Dumbbell games={[GAMES[3]]} label="x" />);
    expect(container).toBeEmptyDOMElement();
  });
});
