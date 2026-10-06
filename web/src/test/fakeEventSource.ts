// A stand-in for the browser's EventSource (jsdom has none), for tests of the live run.
//
//   const es = installFakeEventSource();
//   renderApp(...);
//   act(() => es.last().emitRun(event));     // one `run` message (data = the event JSON)
//   act(() => es.last().emitEnd(runInfo));   // the final `end` message
//
// Remove it with `vi.unstubAllGlobals()` (or let the test file's afterEach do it).

import { vi } from 'vitest';
import type { RunEvent, RunInfo } from '../api/types';

type Listener = (msg: MessageEvent<string>) => void;

export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  readyState = 0;
  closed = false;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  private listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }

  removeEventListener(type: string, fn: Listener) {
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter((f) => f !== fn),
    );
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  open() {
    this.readyState = 1;
    this.onopen?.(new Event('open'));
  }

  emit(type: string, data: unknown, id?: string) {
    if (this.closed) return;
    const msg = new MessageEvent<string>(type, { data: JSON.stringify(data), lastEventId: id ?? '' });
    for (const fn of this.listeners.get(type) ?? []) fn(msg);
  }

  emitRun(ev: RunEvent) {
    this.emit('run', ev, String(ev.seq));
  }

  emitRuns(events: RunEvent[]) {
    for (const ev of events) this.emitRun(ev);
  }

  emitEnd(info: RunInfo) {
    this.emit('end', info);
  }
}

export function installFakeEventSource() {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  return {
    all: () => FakeEventSource.instances,
    last: () => {
      const es = FakeEventSource.instances[FakeEventSource.instances.length - 1];
      if (!es) throw new Error('no EventSource was opened');
      return es;
    },
  };
}
