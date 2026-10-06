// Shapes of the control room's JSON API (src/nflengine/app/). Times are ISO strings.

export interface Calendar {
  season: number;
  week: number | null;
  phase: 'preseason' | 'regular' | 'playoffs' | 'offseason' | string;
  previous_week: number | null;
  previous_games: number;
  previous_final: number;
  previous_complete: boolean;
  deadline: string | null;
  hours_to_deadline: number | null;
  deadline_passed: boolean;
  retry_until: string | null;
  started_games: number;
  games: number;
  game_type: string | null;
  days: string[];
  special: string[];
  byes: string[];
  neutral_sites: string[];
  international: string[];
  notes: string[];
}

export interface Meta {
  app: string;
  version: string;
  now: string;
  mode: { dev: boolean; rehearsal: boolean };
  data_root: { found: boolean; free_gb?: number; message?: string };
  lock: { held: boolean; command: string | null; started: string | null };
  neo4j: { status: 'up' | 'down' | 'checking' | 'config_problem'; reason: string; checked_at: string | null };
  calendar: Calendar | null;
  current_season?: number;
}

export type WeekStatus =
  | 'published'
  | 'running'
  | 'failed'
  | 'partial'
  | 'ready'
  | 'waiting'
  | 'not_run';

export interface WeekEntry {
  season: number;
  week: number;
  status: WeekStatus;
  label: string;
  detail: string | null;
  is_current: boolean;
  has_run: boolean;
  has_run_summary: boolean;
  published_at: string | null;
  on_time: boolean | null;
  steps: Record<string, string>;
}

export interface WeeksResponse {
  season: number;
  current_week: number | null;
  phase: string | null;
  go_live_week: number | null;
  weeks: WeekEntry[];
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}
