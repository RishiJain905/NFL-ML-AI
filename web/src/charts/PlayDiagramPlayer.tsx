// PC03: the diagram player (mockup: playerPanel, pdControls, pdLegend and the clock in
// documentation/play-calling/mockup/playcalling.js). The label comes first and is part of the
// component: `diagram.label` ("Reconstructed from charting data, not player tracking", or a real
// clip's "Real play: 2022 week 6, Big Data Bowl data") shows in every state and at every width;
// there is no prop to hide it. Controls: restart, step back / forward a phase, play / pause, a
// time scrubber with a button per phase, the phase's text. Reduced motion: no autoplay and no
// play button; the player rests on the last phase with every path drawn and steps between phases.
// Remount it (a `key`) for a new diagram: its clock starts over.

import { useEffect, type ReactNode } from 'react';
import type { PlayDiagram } from '../api/types';
import { phaseAt, restTime, stepTime } from '../lib/playdiagram';
import { usePlayClock, useReducedMotion } from './PlayDiagramClock';
import { PlayDiagramSvg } from './PlayDiagram';
import './playdiagram.css';

const ICON = {
  restart: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <path d="M3 8a5 5 0 1 0 1.6-3.7M3 2.5v3h3" />
    </svg>
  ),
  back: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M3 3h1.8v10H3zM13 3v10L5.6 8z" />
    </svg>
  ),
  fwd: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M11.2 3H13v10h-1.8zM3 3v10l7.4-5z" />
    </svg>
  ),
  play: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M4 2.5v11l9.5-5.5z" />
    </svg>
  ),
  pause: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M3.5 2.5h3v11h-3zM9.5 2.5h3v11h-3z" />
    </svg>
  ),
};

/** The label: always on, the first thing over the field. */
export function DiagramLabel({ diagram }: { diagram: Pick<PlayDiagram, 'kind' | 'label'> }) {
  const real = diagram.kind === 'real';
  return (
    <div className={`pdlabel${real ? ' real' : ''}`} role="note" data-testid="diagram-label">
      <span className="ic" aria-hidden="true">
        {real ? '●' : '◇'}
      </span>
      <b>{diagram.label}</b>
    </div>
  );
}

function Legend({ real }: { real: boolean }) {
  return (
    <div className="pdlegend" role="list" aria-label="Legend">
      <span role="listitem">
        <i className="lg-dot o" aria-hidden="true" />
        offense
      </span>
      <span role="listitem">
        <i className="lg-dot d" aria-hidden="true" />
        defense
      </span>
      {real ? null : (
        <span role="listitem">
          <i className="lg-dot ap" aria-hidden="true" />
          generic spot (approximate)
        </span>
      )}
      <span role="listitem">
        <i className="lg-ring rush" aria-hidden="true" />
        rusher
      </span>
      <span role="listitem">
        <i className="lg-ring tgt" aria-hidden="true" />
        target
      </span>
      <span role="listitem">
        <i className="lg-ring car" aria-hidden="true" />
        ball carrier
      </span>
      <span role="listitem">
        <i className="lg-line" aria-hidden="true" />
        line of scrimmage
      </span>
      <span role="listitem">
        <i className="lg-line fd" aria-hidden="true" />
        line to gain
      </span>
      {real ? null : (
        <span role="listitem">
          <i className="lg-line ap" aria-hidden="true" />
          approximate path
        </span>
      )}
    </div>
  );
}

export function PlayDiagramPlayer({
  diagram: dg,
  autoplay = false,
  top,
  children,
}: {
  diagram: PlayDiagram;
  /** Start playing at once (a play the viewer just picked); never under reduced motion. */
  autoplay?: boolean;
  /** Above the label (the "See a real one" bar, a real clip's navigation). */
  top?: ReactNode;
  /** Under the legend (the play's header, its description, Known / Approximate / Not in the data). */
  children?: ReactNode;
}) {
  const reduced = useReducedMotion();
  const clock = usePlayClock(dg.duration, reduced ? restTime(dg) : 0, autoplay && !reduced);
  const { t, playing, play, pause, seek } = clock;
  // the OS setting flipped while a play was running: stop, show the finished card
  useEffect(() => {
    if (reduced) pause();
  }, [reduced, pause]);
  const ph = phaseAt(dg.phases, t);
  const idx = ph ? dg.phases.indexOf(ph) : -1;
  const step = (dir: 1 | -1) => {
    pause();
    seek(stepTime(dg, t, dir));
  };
  return (
    <div className={`card pdcard${dg.kind === 'real' ? ' real' : ''}`}>
      {top}
      <DiagramLabel diagram={dg} />
      <div className="pdfield">
        <PlayDiagramSvg diagram={dg} t={t} />
      </div>
      <div className="pdctl">
        <div className="pdbtns">
          <button
            type="button"
            className="iconbtn"
            aria-label="Restart"
            onClick={() => {
              pause();
              seek(0);
              if (!reduced) play();
            }}
          >
            {ICON.restart}
          </button>
          <button type="button" className="iconbtn" aria-label="Step back a phase" onClick={() => step(-1)}>
            {ICON.back}
          </button>
          {reduced ? null : (
            <button type="button" className="iconbtn play" aria-label={playing ? 'Pause' : 'Play'} onClick={() => (playing ? pause() : play())}>
              {playing ? ICON.pause : ICON.play}
            </button>
          )}
          <button type="button" className="iconbtn" aria-label="Step forward a phase" onClick={() => step(1)}>
            {ICON.fwd}
          </button>
        </div>
        <div className="pdscrub">
          <input
            type="range"
            min={0}
            max={dg.duration}
            step={0.02}
            value={t}
            aria-label="Time in the animation"
            aria-valuetext={ph?.text ?? ''}
            onChange={(e) => {
              pause();
              seek(Number(e.target.value));
            }}
          />
          <span className="pdticks">
            {dg.phases.map((p, i) => (
              <button
                key={`${p.name}-${i}`}
                type="button"
                className={`pdtick${p === ph ? ' on' : ''}`}
                style={{ left: `${((p.t / dg.duration) * 100).toFixed(1)}%` }}
                aria-label={`Jump to ${i + 1}: ${p.text}`}
                title={p.text}
                onClick={() => {
                  pause();
                  seek(p.t);
                }}
              />
            ))}
          </span>
        </div>
        <span className="pdtime num">
          {t.toFixed(1)} / {dg.duration.toFixed(1)} s
        </span>
      </div>
      <div className="pdphase" aria-live="polite" data-testid="diagram-phase">
        <span className="pdpn num">
          {idx + 1}/{dg.phases.length}
        </span>
        <span>{ph?.text ?? ''}</span>
        {reduced ? <span className="chip ghost">reduced motion: step through the phases</span> : null}
      </div>
      <Legend real={dg.kind === 'real'} />
      {children}
    </div>
  );
}
