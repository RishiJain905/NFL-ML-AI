// The forecast strip (PC02; mockup: fStrip): on one track, the 80% range as a band in the caller's
// colour (--s1 offense, --s2 defense), the forecast as a bar, the team's rate so far as a ring, the
// league as a tick, and after the game the actual as an ink dot (ringed when outside the range).
// Hover-only (TipTarget focusable={false}): the row's name cell is the focusable target with the
// same lines, visible at every width (the strip hides on a phone, PC01's rule).

import type { PlaycallForecastValue } from '../api/types';
import type { StripDomain } from '../lib/playcallForecast';
import { TipTarget } from './TipTarget';
import './playcall.css';

export function ForecastStrip({ v, domain, tip }: { v: PlaycallForecastValue; domain: StripDomain | undefined; tip: string[] }) {
  if (v.forecast == null || !domain || !(domain.hi > domain.lo)) return null;
  const X = (x: number) => Math.max(0, Math.min(100, ((x - domain.lo) / (domain.hi - domain.lo)) * 100));
  const pc = (x: number) => `${X(x).toFixed(1)}%`;
  const side = v.caller === 'defense' ? 'd' : 'o';
  return (
    <TipTarget as="div" className="fs" lines={tip} focusable={false}>
      <span className="fs-t" />
      {v.lo != null && v.hi != null ? (
        <span className={`fs-band ${side}`} style={{ left: pc(v.lo), width: `${Math.max(0.5, X(v.hi) - X(v.lo)).toFixed(1)}%` }} />
      ) : null}
      {v.league != null ? <span className="fs-lg" style={{ left: pc(v.league) }} /> : null}
      {v.season_to_date != null ? <span className={`fs-sd${v.small ? ' small' : ''}`} style={{ left: pc(v.season_to_date) }} /> : null}
      <span className={`fs-f ${side}`} style={{ left: pc(v.forecast) }} />
      {v.status === 'graded' && v.actual != null ? <span className={`fs-a${v.in_range ? '' : ' out'}`} style={{ left: pc(v.actual) }} /> : null}
    </TipTarget>
  );
}

/** The strip's legend (one per view). */
export function ForecastLegend() {
  return (
    <span className="legend fleg">
      <span>
        <i className="lk band" />
        80% range
      </span>
      <span>
        <i className="lk fbar" />
        forecast
      </span>
      <span>
        <i className="lk ring" />
        season so far
      </span>
      <span>
        <i className="lk tick" />
        league
      </span>
      <span>
        <i className="lk adot" />
        actual
      </span>
    </span>
  );
}
