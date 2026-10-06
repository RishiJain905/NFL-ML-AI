// The Log card (mockup: the "Log" card and its #console). Finished weeks show lines rebuilt from
// the step details, the current week before its run shows the calendar's dry-run summary, and a
// live run (CR02) streams lines in. The console scrolls to the newest line.

import { useEffect, useRef } from 'react';
import type { LogLine } from '../../api/types';

const SUB: Record<'rebuilt' | 'calendar' | 'none', string> = {
  rebuilt: 'rebuilt from the step details · secrets scrubbed',
  calendar: 'from the calendar: the dry run comes with CR02 · secrets scrubbed',
  none: 'no log for this week · secrets scrubbed',
};

export function LogPanel({
  lines,
  source,
  live,
}: {
  lines: LogLine[];
  source: 'rebuilt' | 'calendar' | 'none';
  live?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <section className="card" aria-label="Log">
      <div className="card-h">
        <h2>Log</h2>
        <span className="muted">{live ? 'streamed from the run · secrets scrubbed' : SUB[source]}</span>
      </div>
      <div className="card-b">
        {/* focusable so the keyboard can scroll it */}
        <div className="console" role="log" aria-live="polite" aria-label="Run log" tabIndex={0} ref={box}>
          {lines.map((l, i) => (
            <div className="ln" key={i}>
              <span className="ts">{l.ts}</span>
              <span className={l.cls}>{l.text}</span>
            </div>
          ))}
          {live ? <span className="cursor" aria-hidden="true" /> : null}
        </div>
      </div>
    </section>
  );
}
