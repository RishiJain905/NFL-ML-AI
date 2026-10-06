import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';
import type { Meta, WeeksResponse } from '../api/types';
import { AppearanceProvider } from '../theme/AppearanceProvider';

export const META: Meta = {
  app: 'control-room',
  version: '0.1.0',
  now: '2026-10-06T14:00:00+00:00',
  mode: { dev: false, rehearsal: false },
  data_root: { found: true, free_gb: 475 },
  lock: { held: false, command: null, started: null },
  neo4j: { status: 'down', reason: 'not reachable; the weekly run starts it', checked_at: null },
  calendar: {
    season: 2026,
    week: 5,
    phase: 'regular',
    previous_week: 4,
    previous_games: 16,
    previous_final: 16,
    previous_complete: true,
    deadline: '2026-10-09T00:15:00+00:00',
    hours_to_deadline: 58.25,
    deadline_passed: false,
    retry_until: '2026-10-07T22:00:00+00:00',
    started_games: 0,
    games: 15,
    game_type: 'REG',
    days: ['thu', 'sun', 'mon'],
    special: ['thursday', 'morning_kickoff', 'byes'],
    byes: ['CAR', 'KC'],
    neutral_sites: [],
    international: [],
    notes: [],
  },
  current_season: 2026,
};

export const WEEKS: WeeksResponse = {
  season: 2026,
  current_week: 5,
  phase: 'regular',
  go_live_week: 4,
  weeks: [
    {
      season: 2026,
      week: 5,
      status: 'ready',
      label: 'Ready to run',
      detail: 'Week 4 is final (16/16)',
      is_current: true,
      has_run: false,
      has_run_summary: false,
      published_at: null,
      on_time: null,
      steps: {},
    },
    {
      season: 2026,
      week: 4,
      status: 'published',
      label: 'Published',
      detail: 'Checks passed after a rewrite',
      is_current: false,
      has_run: true,
      has_run_summary: false,
      published_at: '2026-10-04T05:02:13-04:00',
      on_time: null,
      steps: { digest: 'ok' },
    },
  ],
};

/** A mocked answer with a status other than 200 (`mockApi({'POST /api/run': withStatus(409, ...)})`). */
export interface MockReply {
  __status: number;
  body: unknown;
}
export const withStatus = (status: number, body: unknown): MockReply => ({ __status: status, body });

/** Stub fetch for the API paths the shell calls. Keys are paths for GETs and `'POST <path>'` for
 *  POSTs; a value may be a function of the request body (POSTs) and/or a `withStatus` reply. */
export function mockApi(responses: Record<string, unknown> = { '/api/meta': META, '/api/weeks': WEEKS }) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === 'string' ? input : input.toString();
    const method = (init?.method ?? 'GET').toUpperCase();
    const key = method === 'GET' ? path : `${method} ${path}`;
    if (key in responses) {
      let value = responses[key];
      if (typeof value === 'function') {
        const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
        value = (value as (b: unknown) => unknown)(body);
      }
      const reply = value as MockReply;
      if (reply && typeof reply === 'object' && '__status' in reply) {
        return new Response(JSON.stringify(reply.body), { status: reply.__status });
      }
      return new Response(JSON.stringify(value), { status: 200 });
    }
    return new Response(JSON.stringify({ error: { code: 'not_found', message: path } }), { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

export function renderApp(ui: ReactNode, { route = '/', path = '*' }: { route?: string; path?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AppearanceProvider>
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route path={path} element={ui} />
          </Routes>
        </MemoryRouter>
      </AppearanceProvider>
    </QueryClientProvider>,
  );
}
