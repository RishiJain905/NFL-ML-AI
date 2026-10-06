// The now-bar above a live run (mockup: nowbar): which step of how many, what it is doing, a
// progress bar, the time spent and about how long is left. At the end it says Published, Failed ·
// <step> or Stopped. Pure: LiveRun computes the words (live.ts: describeNow) and ticks the clock.

import type { NowView } from './live';
import './live.css';

export function NowBar({ now }: { now: NowView }) {
  const err = now.tone === 'err';
  const pct = Math.min(100, Math.max(0, Number.isFinite(now.pct) ? now.pct : 0));
  return (
    <section className="card" aria-label="Run progress">
      <div className="nowbar" data-tone={now.tone}>
        <div>
          <span className="eyebrow">{now.eyebrow}</span>
          <div className="stepname" style={err ? { color: 'var(--err)' } : undefined}>
            {now.name}
          </div>
        </div>
        <div className="nowmid">
          <span className="sub">{now.sub}</span>
          <div
            className="progress"
            role="progressbar"
            aria-label="Run progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(pct)}
          >
            <b
              className={now.overrun ? 'overrun' : undefined}
              style={{ width: `${pct.toFixed(1)}%`, ...(err ? { background: 'var(--err)' } : null) }}
            />
          </div>
        </div>
        <div className="stat">
          <div className="big">{now.elapsed}</div>
          <div className="small">{now.small}</div>
        </div>
      </div>
    </section>
  );
}
