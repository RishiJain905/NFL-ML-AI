// The live run's event stream (CR02): GET /api/run/stream, server-sent events.
//
// The server sends one `run` message per line of the run's events file (`id` = `<run id>:<seq>`),
// then a single `end` message with the final RunInfo, and closes. EventSource reconnects by
// itself after a dropped connection and sends `Last-Event-ID`, so the server resumes after the
// last event this page saw (or replays from the start if another run is current by then).
// Events are kept for **one run only** (the key's run id, or the first one seen) and
// de-duplicated by `seq` within it, so two runs never mix (Sol review, CR02).

import { useEffect, useRef, useState } from 'react';
import type { LogLine, RunEvent, RunInfo } from './types';

export interface RunStream {
  /** Every event of the run so far, in `seq` order. */
  events: RunEvent[];
  /** The final RunInfo once the server said the run is over (then the stream is closed). */
  end: RunInfo | null;
  connected: boolean;
}

const EMPTY: RunStream = { events: [], end: null, connected: false };
const RUN_ID = /^\d{8}T\d{6}Z(?:-[a-z0-9]{4,8})?$/;

/**
 * Follow the current run's events while `active`. `runKey` identifies the run (its run_id, or
 * its start time for a terminal run): a new key starts from an empty list.
 */
export function useRunStream(active: boolean, runKey: string | null): RunStream {
  // the state carries the key it belongs to: a new run (or switching off) shows EMPTY at once
  const key = active ? (runKey ?? '') : null;
  const [state, setState] = useState<RunStream & { key: string | null }>({ ...EMPTY, key: null });
  const buf = useRef<RunEvent[]>([]);
  const lastSeq = useRef(0);
  const runId = useRef<string | null>(null);

  useEffect(() => {
    buf.current = [];
    lastSeq.current = 0;
    // an app run's key is its run id; a terminal run's (its start time) locks on the first one seen
    runId.current = key !== null && RUN_ID.test(key) ? key : null;
    if (key === null || typeof EventSource === 'undefined') return undefined;
    const es = new EventSource('/api/run/stream');
    let pending: RunEvent[] = [];
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    // Replays arrive in bursts (hundreds of log lines): batch them into one render.
    const flush = () => {
      timer = null;
      if (closed || pending.length === 0) return;
      buf.current = [...buf.current, ...pending];
      pending = [];
      const events = buf.current;
      // a state left by another key (a finished earlier run) never carries over (Sol review)
      setState((s) => ({ ...(s.key === key ? s : EMPTY), key, events }));
    };
    const schedule = () => {
      if (timer === null) timer = setTimeout(flush, 50);
    };

    es.onopen = () =>
      setState((s) =>
        s.key === key && s.connected ? s : { ...(s.key === key ? s : EMPTY), key, connected: true },
      );
    es.onerror = () => setState((s) => (s.connected ? { ...s, connected: false } : s));
    es.addEventListener('run', (msg) => {
      let ev: RunEvent;
      try {
        ev = JSON.parse((msg as MessageEvent<string>).data) as RunEvent;
      } catch {
        return;
      }
      if (typeof ev.seq !== 'number') return;
      if (runId.current === null) runId.current = ev.run_id;
      if (ev.run_id !== runId.current || ev.seq <= lastSeq.current) return; // another run, or seen
      lastSeq.current = ev.seq;
      pending.push(ev);
      schedule();
    });
    es.addEventListener('end', (msg) => {
      let info: RunInfo | null;
      try {
        info = JSON.parse((msg as MessageEvent<string>).data) as RunInfo;
      } catch {
        info = null;
      }
      flush();
      es.close();
      closed = true;
      // another run's end (the run this page followed was replaced) never completes this view:
      // the page moves on with /api/run/current (Sol review)
      const mine = !info || !info.run_id || runId.current === null || info.run_id === runId.current;
      setState({ key, events: buf.current, end: mine ? info : null, connected: false });
    });
    return () => {
      closed = true;
      if (timer !== null) clearTimeout(timer);
      es.close();
    };
  }, [key]);

  if (state.key !== key || key === null) return EMPTY;
  return { events: state.events, end: state.end, connected: state.connected };
}

const CLOCK = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
  timeZone: 'America/New_York',
});

/** "10:00:12" (US Eastern, the schedule's clock) for an event time. */
export function eventClock(t: string): string {
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? '' : CLOCK.format(d);
}

/** The run's console, as the Log panel shows it: its `log` events, in order. */
export function eventsToLog(events: RunEvent[]): LogLine[] {
  const out: LogLine[] = [];
  for (const e of events) {
    if (e.type === 'log') out.push({ ts: eventClock(e.t), text: e.text, cls: e.cls ?? '' });
  }
  return out;
}
