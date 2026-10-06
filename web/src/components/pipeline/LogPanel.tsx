// The Log card (mockup: the "Log" card and its #console). Finished weeks show lines rebuilt from
// the step details (or the run's own events), the current week before its run shows the dry run,
// and a live run (CR02) streams lines in. The console follows the newest line unless the reader
// scrolled up to look at an earlier one.

import { memo, useEffect, useRef } from 'react';
import type { LogLine } from '../../api/types';

export type LogSource = 'rebuilt' | 'calendar' | 'dryrun' | 'events' | 'none';

const SUB: Record<LogSource, string> = {
  rebuilt: 'rebuilt from the step details · secrets scrubbed',
  calendar: "from the calendar's plan · secrets scrubbed",
  dryrun: 'the dry run (read-only) · secrets scrubbed',
  events: 'streamed from the run · secrets scrubbed',
  none: 'no log for this week · secrets scrubbed',
};

/** Within this many pixels of the bottom counts as "at the end": new lines keep it there. */
const AT_END = 24;

// memo: a live run re-renders its page every second; the log only when its lines change
export const LogPanel = memo(function LogPanel({
  lines,
  source,
  live,
  title = 'Log',
}: {
  lines: LogLine[];
  source: LogSource;
  live?: boolean;
  title?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  const onScroll = () => {
    const el = box.current;
    if (el) follow.current = el.scrollHeight - el.scrollTop - el.clientHeight <= AT_END;
  };

  return (
    <section className="card" aria-label={title}>
      <div className="card-h">
        <h2>{title}</h2>
        <span className="muted">{live ? SUB.events : SUB[source]}</span>
      </div>
      <div className="card-b">
        {/* focusable so the keyboard can scroll it */}
        <div className="console" role="log" aria-live="polite" aria-label="Run log" tabIndex={0} ref={box} onScroll={onScroll}>
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
});
