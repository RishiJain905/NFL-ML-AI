// PC03: the diagram player's clock and the reduced-motion setting (hooks only, so the player's file
// exports components only).

import { useCallback, useEffect, useRef, useState } from 'react';
import { prefersReducedMotion } from '../lib/playdiagram';

/** The OS's reduced-motion setting, kept current. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);
  useEffect(() => {
    let mq: MediaQueryList | null = null;
    try {
      mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    } catch {
      return;
    }
    const on = () => setReduced(!!mq?.matches);
    mq?.addEventListener?.('change', on);
    return () => mq?.removeEventListener?.('change', on);
  }, []);
  return reduced;
}

/** The diagram's clock: t in seconds, advanced by animation frames while playing. */
export function usePlayClock(duration: number, start: number, autoplay: boolean) {
  const [t, setT] = useState(start);
  const [playing, setPlaying] = useState(autoplay);
  const tRef = useRef(start);
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.1, Math.max(0, (now - last) / 1000));
      last = now;
      const next = Math.min(duration, tRef.current + dt);
      tRef.current = next;
      setT(next);
      if (next >= duration) {
        setPlaying(false);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, duration]);
  const seek = useCallback((x: number) => {
    const v = Math.max(0, Math.min(duration, x));
    tRef.current = v;
    setT(v);
  }, [duration]);
  const play = useCallback(() => {
    if (tRef.current >= duration - 1e-3) seek(0);
    setPlaying(true);
  }, [duration, seek]);
  const pause = useCallback(() => setPlaying(false), []);
  return { t, playing, play, pause, seek };
}
