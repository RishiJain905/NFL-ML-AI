import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  ApiErrorBody,
  DigestResponse,
  GamesResponse,
  GraphResponse,
  Meta,
  PipelineResponse,
  PlayersResponse,
  PreflightResponse,
  ResultsResponse,
  RunCurrent,
  RunRequest,
  RunStarted,
  TeamInfoResponse,
  WeekDetail,
  WeeksResponse,
} from './types';

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { accept: 'application/json' } });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (body as ApiErrorBody | null)?.error;
    throw new ApiError(
      res.status,
      err?.code ?? `http_${res.status}`,
      err?.message ?? res.statusText,
    );
  }
  return body as T;
}

let tokenPromise: Promise<string> | null = null;

/** The per-launch token every state-changing request needs (README §5.2). Fetched once; a
 *  failed fetch isn't kept, and `fresh` fetches it again (after `nfl app` restarted with a new
 *  token; Sol review, CR02). */
export function launchToken(fresh = false): Promise<string> {
  if (fresh) tokenPromise = null;
  tokenPromise ??= apiGet<{ token: string }>('/api/session')
    .then((r) => r.token)
    .catch((e: unknown) => {
      tokenPromise = null;
      throw e;
    });
  return tokenPromise;
}

export function useMeta() {
  return useQuery({
    queryKey: ['meta'],
    queryFn: () => apiGet<Meta>('/api/meta'),
    refetchInterval: 30_000,
  });
}

export function useWeeks() {
  return useQuery({
    queryKey: ['weeks'],
    queryFn: () => apiGet<WeeksResponse>('/api/weeks'),
    refetchInterval: 30_000,
  });
}

// ---- CR01: one query per week tab. Finished weeks don't change, so they're cached for a
// minute; the current week's are refetched with the sidebar (a run may have finished).

export function useTeamInfo() {
  return useQuery({
    queryKey: ['team-info'],
    queryFn: () => apiGet<TeamInfoResponse>('/api/team-info'),
    staleTime: Infinity,
  });
}

const weekPath = (season: number, week: number, tab?: string) =>
  `/api/weeks/${season}/${week}${tab ? `/${tab}` : ''}`;

function useWeekQuery<T>(season: number, week: number, tab?: string, query = '') {
  const valid = Number.isInteger(season) && Number.isInteger(week);
  return useQuery({
    queryKey: ['week', season, week, tab ?? 'header', query],
    queryFn: () => apiGet<T>(weekPath(season, week, tab) + query),
    enabled: valid,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}

export const useWeekDetail = (season: number, week: number) =>
  useWeekQuery<WeekDetail>(season, week);
export const usePipeline = (season: number, week: number) =>
  useWeekQuery<PipelineResponse>(season, week, 'pipeline');
export const useDigest = (season: number, week: number) =>
  useWeekQuery<DigestResponse>(season, week, 'digest');
export const useGames = (season: number, week: number) =>
  useWeekQuery<GamesResponse>(season, week, 'games');
export const usePlayers = (season: number, week: number, stats: 'main' | 'all' = 'main') =>
  useWeekQuery<PlayersResponse>(season, week, 'players', stats === 'all' ? '?stats=all' : '');
export const useResults = (season: number, week: number) =>
  useWeekQuery<ResultsResponse>(season, week, 'results');
export const useGraph = (season: number, week: number) =>
  useWeekQuery<GraphResponse>(season, week, 'graph');

// ---- CR02: run control. The server builds the command; the browser sends a kind and its
// expected week (a cross-check). POSTs carry the launch token (security.py).

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    const token = await launchToken(attempt > 0);
    const res = await fetch(path, {
      method: 'POST',
      headers: {
        accept: 'application/json',
        'content-type': 'application/json',
        'x-cr-token': token,
      },
      body: JSON.stringify(body),
    });
    const data: unknown = await res.json().catch(() => null);
    if (res.ok) return data as T;
    const err = (data as ApiErrorBody | null)?.error;
    // the app was restarted with a new token: fetch it once more and retry once
    if (attempt === 0 && res.status === 403 && err?.code === 'bad_token') continue;
    throw new ApiError(
      res.status,
      err?.code ?? `http_${res.status}`,
      err?.message ?? res.statusText,
    );
  }
}

/** Tests only: forget the cached launch token. */
export function resetLaunchToken(): void {
  tokenPromise = null;
}

/** Pre-flight: on open, every 60 s, and on "Check again" (`refetch`). */
export function usePreflight(enabled = true) {
  return useQuery({
    queryKey: ['preflight'],
    queryFn: () => apiGet<PreflightResponse>('/api/preflight'),
    enabled,
    refetchInterval: 60_000,
  });
}

/** The run in progress (or the last one the app launched). Polled every 5 s while a run is
 *  going (the stream carries the detail), every 30 s otherwise. */
export function useRunCurrent() {
  return useQuery({
    queryKey: ['run-current'],
    queryFn: () => apiGet<RunCurrent>('/api/run/current'),
    refetchInterval: (q) => (q.state.data?.state === 'running' ? 5_000 : 30_000),
  });
}

export function useStartRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: RunRequest) => apiPost<RunStarted>('/api/run', req),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ['run-current'] });
      void qc.invalidateQueries({ queryKey: ['preflight'] });
      void qc.invalidateQueries({ queryKey: ['meta'] });
      void qc.invalidateQueries({ queryKey: ['weeks'] });
    },
  });
}

/** After a run ends (or a step finishes), everything the run may have written is refetched. */
export function invalidateAfterRun(qc: ReturnType<typeof useQueryClient>) {
  for (const key of [['run-current'], ['preflight'], ['meta'], ['weeks'], ['week']]) {
    void qc.invalidateQueries({ queryKey: key });
  }
}
