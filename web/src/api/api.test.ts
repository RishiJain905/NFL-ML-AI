// CR02 Sol review: the launch token is fetched again after a restart (bad_token), and the event
// stream never mixes two runs' events.

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installFakeEventSource } from '../test/fakeEventSource';
import { apiPost, launchToken, resetLaunchToken } from './client';
import { useRunStream } from './stream';
import type { RunEvent, RunInfo } from './types';

describe('launch token', () => {
  beforeEach(() => resetLaunchToken());
  afterEach(() => vi.unstubAllGlobals());

  it('fetches a fresh token once after bad_token, then succeeds', async () => {
    let token = 'old';
    const posts: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === '/api/session')
          return new Response(JSON.stringify({ token }), { status: 200 });
        const sent = (init?.headers as Record<string, string>)['x-cr-token'];
        posts.push(sent);
        if (sent !== 'new') {
          return new Response(JSON.stringify({ error: { code: 'bad_token', message: 'x' } }), {
            status: 403,
          });
        }
        return new Response(JSON.stringify({ run_id: 'r' }), { status: 202 });
      }),
    );
    await launchToken(); // cached "old"
    token = 'new'; // the app restarted
    await expect(apiPost('/api/run', { kind: 'weekly', expect_week: 5 })).resolves.toEqual({
      run_id: 'r',
    });
    expect(posts).toEqual(['old', 'new']);
  });

  it('does not retry other refusals, and a failed token fetch is not kept', async () => {
    let fail = true;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/session') {
        if (fail) return new Response('nope', { status: 500 });
        return new Response(JSON.stringify({ token: 't' }), { status: 200 });
      }
      return new Response(JSON.stringify({ error: { code: 'not_allowed', message: 'closed' } }), {
        status: 403,
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    await expect(launchToken()).rejects.toBeTruthy();
    fail = false;
    await expect(launchToken()).resolves.toBe('t'); // fetched again, not the cached failure
    await expect(apiPost('/api/run', { kind: 'weekly', expect_week: 5 })).rejects.toMatchObject({
      code: 'not_allowed',
    });
    expect(fetchMock.mock.calls.filter((c) => String(c[0]) === '/api/run')).toHaveLength(1);
  });
});

describe('useRunStream keeps one run', () => {
  afterEach(() => vi.unstubAllGlobals());

  const ev = (run_id: string, seq: number): RunEvent =>
    ({
      type: 'log',
      seq,
      t: '2026-10-06T10:00:00-04:00',
      run_id,
      step: null,
      text: `${run_id} ${seq}`,
      cls: '',
    }) as RunEvent;

  it('ignores another run on the same connection (an app run keyed by its id)', async () => {
    vi.useFakeTimers();
    const es = installFakeEventSource();
    const { result } = renderHook(() => useRunStream(true, '20261006T140000Z-aaaa'));
    act(() => {
      es.last().emitRuns([
        ev('20261006T140000Z-aaaa', 1),
        ev('20261006T150000Z-bbbb', 2),
        ev('20261006T140000Z-aaaa', 2),
      ]);
      vi.advanceTimersByTime(100);
    });
    expect(result.current.events.map((e) => `${e.run_id}:${e.seq}`)).toEqual([
      '20261006T140000Z-aaaa:1',
      '20261006T140000Z-aaaa:2',
    ]);
    vi.useRealTimers();
  });

  it('a terminal run (keyed by its start time) locks on the first run id it sees', () => {
    vi.useFakeTimers();
    const es = installFakeEventSource();
    const { result } = renderHook(() => useRunStream(true, '2026-10-06T14:00:00+00:00'));
    act(() => {
      es.last().emitRuns([
        ev('20261006T140000Z', 1),
        ev('20261006T150000Z', 5),
        ev('20261006T140000Z', 2),
      ]);
      vi.advanceTimersByTime(100);
    });
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 2]);
    vi.useRealTimers();
  });
});

describe('useRunStream across runs (Sol verification)', () => {
  afterEach(() => vi.unstubAllGlobals());

  const ev = (run_id: string, seq: number): RunEvent =>
    ({
      type: 'log',
      seq,
      t: '2026-10-06T10:00:00-04:00',
      run_id,
      step: null,
      text: `${run_id} ${seq}`,
      cls: '',
    }) as RunEvent;
  const info = (run_id: string) => ({ run_id, status: 'ok' }) as unknown as RunInfo;

  it("ignores another run's end message", () => {
    vi.useFakeTimers();
    const es = installFakeEventSource();
    const { result } = renderHook(() => useRunStream(true, '20261006T140000Z-aaaa'));
    act(() => {
      es.last().emitRuns([ev('20261006T140000Z-aaaa', 1)]);
      es.last().emitEnd(info('20261006T150000Z-bbbb'));
      vi.advanceTimersByTime(100);
    });
    expect(result.current.events).toHaveLength(1);
    expect(result.current.end).toBeNull();
    vi.useRealTimers();
  });

  it('a new run key starts empty: run A finished, then run B opens in the same component', () => {
    vi.useFakeTimers();
    const es = installFakeEventSource();
    const { result, rerender } = renderHook(({ k }) => useRunStream(true, k), {
      initialProps: { k: '20261010T140000Z-aaaa' },
    });
    act(() => {
      es.last().emitRuns([ev('20261010T140000Z-aaaa', 1)]);
      es.last().emitEnd(info('20261010T140000Z-aaaa'));
      vi.advanceTimersByTime(100);
    });
    expect(result.current.end?.run_id).toBe('20261010T140000Z-aaaa');
    rerender({ k: '20261010T150000Z-bbbb' });
    act(() => {
      es.last().open();
      es.last().emitRuns([ev('20261010T150000Z-bbbb', 1)]);
      vi.advanceTimersByTime(100);
    });
    expect(result.current.end).toBeNull(); // A's end didn't carry over
    expect(result.current.events.map((e) => e.run_id)).toEqual(['20261010T150000Z-bbbb']);
    vi.useRealTimers();
  });
});
