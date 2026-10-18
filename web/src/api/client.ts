import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  AlertsResponse,
  ApiErrorBody,
  AskExamplesResponse,
  AskRequest,
  AskResponse,
  DigestResponse,
  GamesResponse,
  GraphResponse,
  HealthResponse,
  LiveCallResponse,
  LiveContextResponse,
  LiveGamesResponse,
  LiveReviewResponse,
  LiveSeasonReviewResponse,
  Meta,
  MlopsArtifactsResponse,
  MlopsHealthResponse,
  MlopsWandbResponse,
  ModelCardResponse,
  ModelsResponse,
  PipelineResponse,
  PlayCallsResponse,
  PlaycallHistoryResponse,
  PlaycallPlaysResponse,
  PlayDiagramResponse,
  RealClipResponse,
  PlaycallSide,
  PlaycallTeamResponse,
  PlaycallTeamsResponse,
  PlayersResponse,
  PreflightResponse,
  ResultsResponse,
  RunCurrent,
  RunRequest,
  RunStarted,
  ScorecardResponse,
  TeamInfoResponse,
  TeamsResponse,
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

function useWeekQuery<T>(season: number, week: number, tab?: string, query = '', refetchMs = 60_000) {
  const valid = Number.isInteger(season) && Number.isInteger(week);
  return useQuery({
    queryKey: ['week', season, week, tab ?? 'header', query],
    queryFn: () => apiGet<T>(weekPath(season, week, tab) + query),
    enabled: valid,
    staleTime: Math.min(60_000, refetchMs),
    refetchInterval: refetchMs,
  });
}

export const useWeekDetail = (season: number, week: number) =>
  useWeekQuery<WeekDetail>(season, week);
/** `refetchMs`: the Pipeline tab re-reads its log every 2 s while an injury update runs. */
export const usePipeline = (season: number, week: number, refetchMs?: number) =>
  useWeekQuery<PipelineResponse>(season, week, 'pipeline', '', refetchMs);
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
/** PC01: the week's matchups of tendencies (descriptive; PC02 adds the forecast). */
export const usePlayCalls = (season: number, week: number) =>
  useWeekQuery<PlayCallsResponse>(season, week, 'play-calls');

// ---- CR02: run control. The server builds the command; the browser sends a kind and its
// expected week (a cross-check). POSTs carry the launch token (security.py).

/** `signal` (AE01): the Ask page's Cancel stops waiting for the answer (the server finishes it). */
export async function apiPost<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
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
      signal,
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
  // ['playcalling']: the Explore pages after a play-calling update (D132); the week tabs are ['week']
  for (const key of [['run-current'], ['preflight'], ['meta'], ['weeks'], ['week'], ['season'], ['playcalling']]) {
    void qc.invalidateQueries({ queryKey: key });
  }
}

// ---- CR03: MLOps and the season pages. W&B-backed answers are cached on the server (about
// 10 minutes; longer for finished weeks); `refresh` asks the server to skip its cache once
// (the "Refresh from W&B" button: call `refetch` after `refreshWandb`).

export type MlopsSection = 'health' | 'wandb' | 'artifacts';

let refreshOnce = new Set<string>();
/** The next fetch of `path` asks the server to skip its W&B cache (`?refresh=1`). */
export function refreshWandb(path: string): void {
  refreshOnce.add(path);
}
/** Tests only. */
export function resetRefreshes(): void {
  refreshOnce = new Set();
}
function withRefresh(path: string): string {
  if (!refreshOnce.has(path)) return path;
  refreshOnce.delete(path);
  return path + (path.includes('?') ? '&' : '?') + 'refresh=1';
}

export const mlopsPath = (season: number, week: number, section: MlopsSection) =>
  `/api/weeks/${season}/${week}/mlops/${section}`;

function useMlops<T>(season: number, week: number, section: MlopsSection) {
  const valid = Number.isInteger(season) && Number.isInteger(week);
  const path = mlopsPath(season, week, section);
  return useQuery({
    queryKey: ['week', season, week, 'mlops', section],
    queryFn: () => apiGet<T>(withRefresh(path)),
    enabled: valid,
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
}
export const useMlopsHealth = (season: number, week: number) =>
  useMlops<MlopsHealthResponse>(season, week, 'health');
export const useMlopsWandb = (season: number, week: number) =>
  useMlops<MlopsWandbResponse>(season, week, 'wandb');
export const useMlopsArtifacts = (season: number, week: number) =>
  useMlops<MlopsArtifactsResponse>(season, week, 'artifacts');

export const scorecardPath = (season: number, source: 'live' | 'backtest') =>
  `/api/season/${season}/scorecard?source=${source}`;
export function useScorecard(season: number, source: 'live' | 'backtest') {
  return useQuery({
    queryKey: ['season', season, 'scorecard', source],
    queryFn: () => apiGet<ScorecardResponse>(scorecardPath(season, source)),
    enabled: Number.isInteger(season),
    staleTime: 60_000,
  });
}

export function useTeams(season?: number, week?: number) {
  const q = new URLSearchParams();
  if (season != null) q.set('season', String(season));
  if (week != null) q.set('week', String(week));
  const qs = q.toString();
  const path = `/api/teams${qs ? `?${qs}` : ''}`;
  return useQuery({
    queryKey: ['season', 'teams', season ?? null, week ?? null],
    queryFn: () => apiGet<TeamsResponse>(path),
    staleTime: 60_000,
  });
}

export const MODELS_PATH = '/api/models';
export function useModels() {
  return useQuery({
    queryKey: ['season', 'models'],
    queryFn: () => apiGet<ModelsResponse>(withRefresh(MODELS_PATH)),
    staleTime: 60_000,
  });
}
export function useModelCard(id: string | null) {
  return useQuery({
    queryKey: ['model-card', id],
    queryFn: () => apiGet<ModelCardResponse>(`/api/models/card/${encodeURIComponent(id ?? '')}`),
    enabled: Boolean(id),
    staleTime: Infinity,
  });
}

export function useAlerts(season?: number) {
  const path = `/api/alerts${season != null ? `?season=${season}` : ''}`;
  return useQuery({
    queryKey: ['season', 'alerts', season ?? null],
    queryFn: () => apiGet<AlertsResponse>(path),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
}

export const HEALTH_PATH = '/api/health';
/** System health. The slow checks run in the background on the server; while `checking` the
 *  page polls every 3 s. "Check again" = `refreshWandb(HEALTH_PATH)` + `refetch()`. */
export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => apiGet<HealthResponse>(withRefresh(HEALTH_PATH)),
    refetchInterval: (q) => (q.state.data?.checking ? 3_000 : 60_000),
  });
}

// ---- LD02: Game day. Nothing is fetched until a click, except the list: every 30 s while the
// tab is open and the browser tab is visible (TanStack pauses intervals in the background), and
// only for the week being played. The server keeps ESPN's answers (30 s list, 2 s per game).

const livePath = (season: number, week: number, rest = '') =>
  `/api/live/${season}/${week}/games${rest}`;

export function useLiveGames(season: number, week: number) {
  const valid = Number.isInteger(season) && Number.isInteger(week);
  return useQuery({
    queryKey: ['live', season, week, 'games'],
    queryFn: () => apiGet<LiveGamesResponse>(livePath(season, week)),
    enabled: valid,
    // the week being played: every refresh_s; a future week: every 5 minutes, so an open tab
    // turns live when its week starts (the server never asks ESPN for it); a failed first
    // load: every 30 s (Sol review, LD02)
    refetchInterval: (q) => {
      const d = q.state.data;
      if (!d) return q.state.status === 'error' ? 30_000 : false;
      if (d.is_current && d.phase !== 'final') return d.refresh_s * 1000;
      return d.phase === 'future' ? 300_000 : false;
    },
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/** "Check this play": never runs by itself (`enabled: false`); the button calls `refetch()`. */
export function useLiveCall(season: number, week: number, event: string | null) {
  return useQuery({
    queryKey: ['live', season, week, 'call', event],
    queryFn: () =>
      apiGet<LiveCallResponse>(livePath(season, week, `/${encodeURIComponent(event ?? '')}/call`)),
    enabled: false,
    retry: false,
    gcTime: 10 * 60_000,
  });
}

/** "Is this team good at this?": the offense's record as of the week's start (one per week). */
export function useLiveContext(
  season: number,
  week: number,
  event: string | null,
  offense: string | null,
) {
  return useQuery({
    queryKey: ['live', season, week, 'context', offense],
    queryFn: () =>
      apiGet<LiveContextResponse>(
        livePath(
          season,
          week,
          `/${encodeURIComponent(event ?? '')}/context?offense=${encodeURIComponent(offense ?? '')}`,
        ),
      ),
    enabled: Boolean(event && offense),
    staleTime: Infinity,
    retry: false,
  });
}

// ---- LD03: the decision review of a finished week and the season beside it. Read-only on the
// server (curated plays + the models, never ESPN); a week without a stored review is computed
// once (a second or two; the first open of a season also reads its plays from D:).

/** A finished week's decision review. */
export function useLiveReview(season: number, week: number, enabled = true) {
  const valid = Number.isInteger(season) && Number.isInteger(week);
  return useQuery({
    queryKey: ['live', season, week, 'review'],
    queryFn: () => apiGet<LiveReviewResponse>(`/api/live/${season}/${week}/review`),
    enabled: valid && enabled,
    staleTime: 10 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/** The season through `through` (the week the tab shows): leaderboard, calibration, trend. */
export function useLiveSeasonReview(season: number, through: number, enabled = true) {
  const valid = Number.isInteger(season) && Number.isInteger(through);
  return useQuery({
    queryKey: ['live', season, 'season-review', through],
    queryFn: () =>
      apiGet<LiveSeasonReviewResponse>(`/api/live/${season}/season-review?through=${through}`),
    enabled: valid && enabled,
    staleTime: 10 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

// ---- PC01: Explore -> Play calling. The tables change at most twice a week (Tuesday's build
// and Wednesday's FTN refresh), so answers are kept for ten minutes.

const PLAYCALL_STALE_MS = 10 * 60_000;

/** The teams grid; no season = the newest built season. */
export function usePlaycallTeams(season?: number) {
  const q = season ? `?season=${season}` : '';
  return useQuery({
    queryKey: ['playcalling', 'teams', season ?? 'newest'],
    queryFn: () => apiGet<PlaycallTeamsResponse>(`/api/playcalling/teams${q}`),
    staleTime: PLAYCALL_STALE_MS,
    refetchOnWindowFocus: false,
  });
}

/** One team's page for a side; no season = the newest built season. */
export function usePlaycallTeam(team: string, side: PlaycallSide, season?: number) {
  const q = `?side=${side}${season ? `&season=${season}` : ''}`;
  return useQuery({
    queryKey: ['playcalling', 'team', team, side, season ?? 'newest'],
    queryFn: () =>
      apiGet<PlaycallTeamResponse>(`/api/playcalling/teams/${encodeURIComponent(team)}${q}`),
    enabled: /^[A-Z]{2,3}$/.test(team),
    staleTime: PLAYCALL_STALE_MS,
    refetchOnWindowFocus: false,
  });
}

export interface PlaycallPlaysQuery {
  season?: number;
  week?: number | null; // null / undefined: the newest week with plays
  down?: number | null;
  type?: string | null;
  result?: string | null;
}

/** PC03: the play browser's list for one week; fetched only when the browser is open. */
export function usePlaycallPlays(
  team: string,
  side: PlaycallSide,
  query: PlaycallPlaysQuery = {},
  enabled = true,
) {
  const params = new URLSearchParams({ side });
  if (query.season) params.set('season', String(query.season));
  if (query.week) params.set('week', String(query.week));
  if (query.down) params.set('down', String(query.down));
  if (query.type) params.set('type', query.type);
  if (query.result) params.set('result', query.result);
  return useQuery({
    queryKey: [
      'playcalling',
      'plays',
      team,
      side,
      query.season ?? 'newest',
      query.week ?? 'newest',
      query.down ?? null,
      query.type ?? null,
      query.result ?? null,
    ],
    queryFn: () =>
      apiGet<PlaycallPlaysResponse>(
        `/api/playcalling/teams/${encodeURIComponent(team)}/plays?${params.toString()}`,
      ),
    enabled: enabled && /^[A-Z]{2,3}$/.test(team),
    staleTime: PLAYCALL_STALE_MS,
    refetchOnWindowFocus: false,
  });
}

/** PC03: one play's reconstruction (a play's diagram never changes once built). */
export function usePlayDiagram(gameId: string | null, playId: number | null) {
  const valid = !!gameId && /^\d{4}_\d{2}_[A-Z]{2,3}_[A-Z]{2,3}$/.test(gameId) && !!playId;
  return useQuery({
    queryKey: ['playcalling', 'diagram', gameId, playId],
    queryFn: () =>
      apiGet<PlayDiagramResponse>(
        `/api/playcalling/plays/${encodeURIComponent(gameId ?? '')}/${playId}/diagram`,
      ),
    enabled: valid,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/** PC03: a real Big Data Bowl example of a concept ("See a real one"); only when asked for. */
export function usePlaycallRealClip(concept: string | null, index: number, enabled = true) {
  return useQuery({
    queryKey: ['playcalling', 'real', concept, index],
    queryFn: () =>
      apiGet<RealClipResponse>(
        `/api/playcalling/concepts/${encodeURIComponent(concept ?? '')}?example=${index}`,
      ),
    enabled: enabled && !!concept && /^[a-z0-9_]{2,30}$/.test(concept),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/** History, 2023-2025 (research data): fetched only when its section is opened. */
export function usePlaycallHistory(team: string, side: PlaycallSide, enabled = true) {
  return useQuery({
    queryKey: ['playcalling', 'history', team, side],
    queryFn: () =>
      apiGet<PlaycallHistoryResponse>(
        `/api/playcalling/teams/${encodeURIComponent(team)}/history?side=${side}`,
      ),
    enabled: enabled && /^[A-Z]{2,3}$/.test(team),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

// ---- AE01: Explore -> Ask the Engine. The examples and the sources' status are refetched every
// minute (the graph's week and Neo4j's state change); a question is a POST (`askQuestion`) whose
// answer the page keeps in its session list (localStorage), never in the query cache.

export function useAskExamples() {
  return useQuery({
    queryKey: ['ask', 'examples'],
    queryFn: () => apiGet<AskExamplesResponse>('/api/ask/examples'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** One question. Rejects with `ApiError` (409 busy, 429 rate_limited, 422 bad_request, 503
 *  ask_unavailable, ...) or an `AbortError` when `signal` aborts (Cancel). */
export function askQuestion(req: AskRequest, signal?: AbortSignal): Promise<AskResponse> {
  return apiPost<AskResponse>('/api/ask', req, signal);
}
