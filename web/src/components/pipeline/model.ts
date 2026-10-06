// The step model the three pipeline views share (mockup: STEPS, finalModel, simModel).
// CR01 builds it from a finished run or the plan (`toModel`); CR02's live stream updates the same
// shape step by step (`liveModel`, live.ts), so the views take a model and never re-mount to animate.

import type { PipelineResponse, StepName, StepStatus } from '../../api/types';
import { duration } from '../../lib/format';

export interface StepDef {
  name: StepName;
  label: string;
  /** Drive chart: the yard line the step ends at (the digest is the touchdown). */
  yd: number;
  /** Pipeline map: the file or W&B artifact that appears when the step finishes. */
  chip: (season: number, week: number) => string;
  /** The chip is a W&B artifact or run (highlighted). */
  wb: boolean;
  /** Expected seconds when no published week has real times yet (the mockup's numbers). */
  defaultSeconds: number;
}

const wk = (week: number) => String(week).padStart(2, '0');

export const STEP_DEFS: StepDef[] = [
  { name: 'ingest', label: 'Ingest', yd: 30, chip: () => 'raw snapshots', wb: false, defaultSeconds: 25 },
  { name: 'ready', label: 'Ready check', yd: 35, chip: (_s, w) => `week ${w - 1} final`, wb: false, defaultSeconds: 1 },
  { name: 'curate', label: 'Curate', yd: 45, chip: () => 'nfl.duckdb', wb: false, defaultSeconds: 16 },
  { name: 'ratings', label: 'Ratings & Elo', yd: 50, chip: () => 'team_elo.parquet', wb: false, defaultSeconds: 4 },
  { name: 'game', label: 'Game model', yd: 60, chip: () => 'W&B · game-model', wb: true, defaultSeconds: 15 },
  { name: 'graph', label: 'Knowledge graph', yd: 72, chip: () => 'Neo4j · graph-results', wb: true, defaultSeconds: 150 },
  { name: 'player', label: 'Player model', yd: 85, chip: () => 'W&B · player-model', wb: true, defaultSeconds: 95 },
  { name: 'digest', label: 'Digest (GLM)', yd: 100, chip: () => 'digest.md · W&B digest', wb: true, defaultSeconds: 540 },
  { name: 'records', label: 'Run records', yd: 100, chip: (s, w) => `pipeline-${s}-w${wk(w)}`, wb: true, defaultSeconds: 20 },
];

export const STEP_INDEX: Record<StepName, number> = Object.fromEntries(
  STEP_DEFS.map((d, i) => [d.name, i]),
) as Record<StepName, number>;

export interface ViewStep {
  key: StepName;
  label: string;
  status: StepStatus;
  /** Real seconds so far (finished: the step's time; running: elapsed; else 0). */
  seconds: number;
  /** Expected seconds (the plan's estimate). */
  expected: number;
  /** One line to print with the step: its detail, "expected ~2m 30s", "skipped", ... */
  detail: string;
  /** 0–1: 1 when finished, 0 when pending; part-way while running (CR02) or where it failed. */
  progress: number;
  wandbUrl: string | null;
  /** Live mode: a short form of `detail` for the pipeline map's sub-line (it cuts at 34 characters). */
  brief?: string;
}

export type Outcome = 'published' | 'failed' | 'stopped' | 'plan' | 'live';

export interface PipelineModel {
  season: number;
  week: number;
  /** published / failed / stopped: a past run · plan: the current week before its run · live: a run in progress (CR02) */
  outcome: Outcome;
  steps: ViewStep[]; // always 9, in STEP_DEFS order
  /** The running step's index; -1 when nothing runs. */
  idx: number;
  done: boolean;
  failed: boolean;
  /** Seconds spent in steps (finished ones, plus the running one's elapsed). */
  elapsed: number;
  /** Expected seconds left (pending steps' expected, plus the running one's remainder). */
  remaining: number;
  /** Plan mode: the scorebug's "Waiting" line. */
  idleLabel: string | null;
  /** A rehearsal's run (CR02): its digest is written to the rehearsal folder, never published. */
  rehearsal?: boolean;
}

function stepDetail(status: StepStatus, detail: string | null, expected: number): string {
  if (status === 'pending') return `expected ~${duration(expected)}`;
  if (status === 'skipped') return 'skipped (an earlier step failed)';
  if (status === 'none') return 'not recorded (this week ran before run records existed)';
  return detail ?? '';
}

export function toModel(resp: PipelineResponse): PipelineModel {
  const byName = new Map(resp.steps.map((s) => [s.step, s]));
  const steps: ViewStep[] = STEP_DEFS.map((d) => {
    const s = byName.get(d.name);
    const status: StepStatus = s?.status ?? (resp.state === 'plan' ? 'pending' : 'none');
    const expected = s?.expected_seconds ?? d.defaultSeconds;
    const seconds = status === 'pending' || status === 'none' || status === 'skipped' ? 0 : (s?.seconds ?? 0);
    const progress = status === 'ok' || status === 'degraded' ? 1 : status === 'failed' ? 0.5 : 0;
    return {
      key: d.name,
      label: d.label,
      status,
      seconds,
      expected,
      detail: stepDetail(status, s?.detail ?? null, expected),
      progress,
      wandbUrl: s?.wandb_url ?? null,
    };
  });
  const outcome: Outcome =
    resp.state === 'plan' || resp.state === 'none'
      ? 'plan'
      : resp.state === 'running'
        ? 'live'
        : resp.state === 'failed'
          ? 'failed'
          : resp.state === 'partial'
            ? 'stopped'
            : 'published';
  const elapsed = steps.reduce((a, s) => a + s.seconds, 0);
  const remaining = steps.reduce((a, s) => a + (s.status === 'pending' ? s.expected : 0), 0);
  return {
    season: resp.season,
    week: resp.week,
    outcome,
    steps,
    idx: steps.findIndex((s) => s.status === 'running'),
    done: outcome !== 'plan' && outcome !== 'live',
    failed: outcome === 'failed',
    elapsed,
    remaining,
    idleLabel: outcome === 'plan' ? 'Kickoff when the run starts' : null,
  };
}
