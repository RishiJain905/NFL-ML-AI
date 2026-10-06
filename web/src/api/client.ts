import { useQuery } from '@tanstack/react-query';
import type {
  ApiErrorBody,
  DigestResponse,
  GamesResponse,
  GraphResponse,
  Meta,
  PipelineResponse,
  PlayersResponse,
  ResultsResponse,
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
    throw new ApiError(res.status, err?.code ?? `http_${res.status}`, err?.message ?? res.statusText);
  }
  return body as T;
}

let tokenPromise: Promise<string> | null = null;

/** The per-launch token every state-changing request needs (README §5.2). */
export function launchToken(): Promise<string> {
  tokenPromise ??= apiGet<{ token: string }>('/api/session').then((r) => r.token);
  return tokenPromise;
}

export function useMeta() {
  return useQuery({ queryKey: ['meta'], queryFn: () => apiGet<Meta>('/api/meta'), refetchInterval: 30_000 });
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

export const useWeekDetail = (season: number, week: number) => useWeekQuery<WeekDetail>(season, week);
export const usePipeline = (season: number, week: number) =>
  useWeekQuery<PipelineResponse>(season, week, 'pipeline');
export const useDigest = (season: number, week: number) => useWeekQuery<DigestResponse>(season, week, 'digest');
export const useGames = (season: number, week: number) => useWeekQuery<GamesResponse>(season, week, 'games');
export const usePlayers = (season: number, week: number, stats: 'main' | 'all' = 'main') =>
  useWeekQuery<PlayersResponse>(season, week, 'players', stats === 'all' ? '?stats=all' : '');
export const useResults = (season: number, week: number) =>
  useWeekQuery<ResultsResponse>(season, week, 'results');
export const useGraph = (season: number, week: number) => useWeekQuery<GraphResponse>(season, week, 'graph');
