import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CLIP, DIAGRAM, DIAGRAM_RUN, LABEL, REAL_LABEL } from '../pages/explore/playbrowserFixtures';
import { PlayDiagramSvg } from './PlayDiagram';
import { PlayDiagramPlayer } from './PlayDiagramPlayer';

const phase = () => screen.getByTestId('diagram-phase').textContent ?? '';
const label = () => screen.getByTestId('diagram-label');
const time = () => Number((screen.getByRole('slider', { name: 'Time in the animation' }) as HTMLInputElement).value);

/** Pretend the OS asks for reduced motion (setup.ts answers `false` for every non-dark query). */
function reduceMotion() {
  const orig = window.matchMedia;
  vi.spyOn(window, 'matchMedia').mockImplementation((q: string) => {
    const mq = orig(q);
    return { ...mq, matches: q.includes('reduce') ? true : mq.matches } as MediaQueryList;
  });
}

afterEach(() => {
  vi.useRealTimers();
});

describe('PlayDiagramPlayer', () => {
  it('starts paused on the pre-snap picture, the label on', () => {
    render(<PlayDiagramPlayer diagram={DIAGRAM} />);
    expect(label()).toHaveTextContent(LABEL);
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
    expect(phase()).toContain('1/5');
    expect(phase()).toContain('Before the snap');
    expect(time()).toBe(0);
    expect(screen.getByRole('img', { name: new RegExp(LABEL) })).toBeInTheDocument(); // the SVG's name says it too
  });

  it('plays and pauses: the clock advances on animation frames, the label stays', () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance'] });
    render(<PlayDiagramPlayer diagram={DIAGRAM} />);
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    const t1 = time();
    expect(t1).toBeGreaterThan(0.8);
    expect(phase()).toContain('Snap');
    expect(label()).toHaveTextContent(LABEL);
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(time()).toBe(t1);
    // play to the end: it stops there, on the last phase
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(time()).toBe(DIAGRAM.duration);
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
    expect(phase()).toContain('Complete, +24');
    expect(label()).toHaveTextContent(LABEL);
  });

  it('autoplays a play the viewer picked', () => {
    render(<PlayDiagramPlayer diagram={DIAGRAM} autoplay />);
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();
  });

  it('steps phase by phase and restarts', () => {
    render(<PlayDiagramPlayer diagram={DIAGRAM} />);
    const fwd = screen.getByRole('button', { name: 'Step forward a phase' });
    fireEvent.click(fwd);
    expect(phase()).toContain('2/5');
    fireEvent.click(fwd);
    expect(phase()).toContain('Throw: 22 air yards');
    expect(time()).toBe(1.6);
    fireEvent.click(screen.getByRole('button', { name: 'Step back a phase' }));
    expect(phase()).toContain('Snap');
    fireEvent.click(screen.getByRole('button', { name: /Jump to 5:/ }));
    expect(phase()).toContain('5/5');
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    expect(time()).toBe(0);
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument(); // restart plays from the top
    expect(label()).toHaveTextContent(LABEL);
  });

  it('the scrubber seeks and pauses', () => {
    render(<PlayDiagramPlayer diagram={DIAGRAM} autoplay />);
    fireEvent.change(screen.getByRole('slider', { name: 'Time in the animation' }), { target: { value: '2.5' } });
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
    expect(phase()).toContain('Caught at the KC 47');
  });

  it('reduced motion: no autoplay, no play button, the finished card with its paths; stepping still works', () => {
    reduceMotion();
    const { container } = render(<PlayDiagramPlayer diagram={DIAGRAM} autoplay />);
    expect(screen.queryByRole('button', { name: /Play|Pause/ })).toBeNull();
    expect(time()).toBe(2.8); // the last phase
    expect(phase()).toContain('reduced motion');
    expect(container.querySelector('[data-mark="throw"]')).not.toBeNull();
    expect(container.querySelector('[data-mark="end"]')).toHaveTextContent('Complete, +24');
    expect(label()).toHaveTextContent(LABEL);
    fireEvent.click(screen.getByRole('button', { name: 'Step back a phase' }));
    expect(phase()).toContain('Caught at the KC 47');
    fireEvent.click(screen.getByRole('button', { name: 'Step back a phase' }));
    expect(phase()).toContain('Throw');
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    expect(time()).toBe(0);
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull(); // restart doesn't play either
    expect(label()).toHaveTextContent(LABEL);
  });

  it('a real clip carries its own label and drops the approximate legend', () => {
    render(<PlayDiagramPlayer diagram={CLIP.diagram!} />);
    expect(label()).toHaveTextContent(REAL_LABEL);
    expect(label()).toHaveClass('real');
    expect(screen.queryByText('generic spot (approximate)')).toBeNull();
    expect(screen.getByText('REAL PLAY · TRACKING')).toBeInTheDocument();
  });
});

describe('PlayDiagramSvg', () => {
  it('draws approximate players hollow and measured ones filled, rushers ringed', () => {
    const { container } = render(<PlayDiagramSvg diagram={DIAGRAM} t={0} />);
    const r1 = container.querySelector('[data-player="o_r1"]')!;
    expect(r1).toHaveAttribute('data-approx', 'true');
    expect(r1.querySelector('circle.pdp')).toHaveClass('ap');
    const qb = container.querySelector('[data-player="o_qb"]')!;
    expect(qb).toHaveAttribute('data-approx', 'false');
    expect(qb.querySelector('circle.pdp')).not.toHaveClass('ap');
    expect(container.querySelector('[data-player="d_dl1"] .pdrush')).not.toBeNull();
    expect(container.querySelector('[data-player="o_r1"] .pdtgt')).not.toBeNull(); // the target
    expect(screen.getByText('RECONSTRUCTED · NOT TRACKING')).toBeInTheDocument();
    expect(screen.getByTestId('first-down-line')).toBeInTheDocument();
    expect(container.querySelector('svg')).toHaveAttribute('viewBox', expect.stringMatching(/^4 -3\.2 50 /));
  });

  it('marks appear at their time; the carrier is ringed once he has the ball', () => {
    const { container, rerender } = render(<PlayDiagramSvg diagram={DIAGRAM} t={0.5} />);
    expect(container.querySelector('[data-mark]')).toBeNull();
    rerender(<PlayDiagramSvg diagram={DIAGRAM} t={2.5} />);
    expect(container.querySelector('[data-mark="fake"]')).not.toBeNull();
    expect(container.querySelector('[data-mark="throw"]')).not.toBeNull();
    expect(container.querySelector('[data-mark="end"]')).toBeNull();
    expect(container.querySelector('[data-player="o_r1"] .pdcar')).not.toBeNull();
  });

  it('a run: the lane at the line, the approximate path dashed, goal to go has no first-down line', () => {
    const { container } = render(<PlayDiagramSvg diagram={DIAGRAM_RUN.diagram} t={2.8} />);
    expect(container.querySelector('[data-mark="lane"]')).toHaveTextContent('up the middle');
    const run = container.querySelector('[data-mark="run"]')!;
    expect(run).toHaveClass('ap');
    expect(screen.queryByTestId('first-down-line')).toBeNull();
    expect(screen.getByText('GOAL TO GO')).toBeInTheDocument();
    expect(screen.getByText(/hash not charted/)).toBeInTheDocument();
  });
});
