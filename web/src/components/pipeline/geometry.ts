// Geometry and layout the three pipeline views share (mockup: X, posLabel, ballYd, MAP,
// timelineHTML). Pure functions of the step model, so every view is a function of its props
// (CR02 changes the model and the views follow in place) and the numbers are easy to test.

import type { StepStatus } from '../../api/types';
import { STEP_DEFS, STEP_INDEX, type PipelineModel } from './model';

/** A step that has run to the end (a degraded step still finished). */
export const isFinished = (status: StepStatus): boolean => status === 'ok' || status === 'degraded';

/** The mockup's state marks: never colour alone. */
export function glyphOf(status: StepStatus): string {
  return status === 'ok' ? '✓' : status === 'failed' ? '✕' : status === 'running' ? '●' : status === 'degraded' ? '!' : '';
}

const num = (v: number): number => (Number.isFinite(v) ? v : 0);

// ---- drive chart ---------------------------------------------------------------------------

export const FX0 = 40;
export const FW = 920;
export const FY0 = 54;
export const FH = 226;
export const PX = FW / 120;
export const MID_Y = FY0 + FH / 2;
export const START_YD = 20;

/** x for a yard line (0 = the left goal line; the end zones sit at -10..0 and 100..110). */
export const xAt = (yd: number): number => FX0 + 10 * PX + yd * PX;

export const posLabel = (yd: number): string => (yd === 50 ? '50' : yd < 50 ? `OWN ${yd}` : `OPP ${100 - yd}`);

/** The yard line a step starts from (the previous step's line; the drive starts at the 20). */
export const startYd = (i: number): number => (i === 0 ? START_YD : STEP_DEFS[i - 1].yd);

const RECORDS = STEP_INDEX.records;

/** The ball: each finished step advances it to its yard line; a running or failed one part-way. */
export function ballYard(model: PipelineModel): number {
  let yd = START_YD;
  model.steps.forEach((st, i) => {
    if (i === RECORDS) return;
    const to = STEP_DEFS[i].yd;
    if (isFinished(st.status)) yd = to;
    else if (st.status === 'running' || st.status === 'failed') {
      yd = startYd(i) + (to - startYd(i)) * Math.min(1, Math.max(0, num(st.progress)));
    }
  });
  return yd;
}

/** Plays run so far (the records step is the extra point, not a play). */
export function playCount(model: PipelineModel): number {
  return model.steps.filter((st, i) => i !== RECORDS && isFinished(st.status)).length;
}

/** The drive ended in the end zone: the run finished and nothing failed or stopped short. */
export function isTouchdown(model: PipelineModel): boolean {
  return model.done && !model.failed && model.outcome !== 'stopped' && model.outcome !== 'plan';
}

/** Line of scrimmage (where the run is) and first-down line (where this step ends). */
export function scrimmageLines(model: PipelineModel): { from: number; target: number } {
  if (model.idx >= 0) return { from: startYd(model.idx), target: STEP_DEFS[model.idx].yd };
  let last = -1;
  model.steps.forEach((st, i) => {
    if (i !== RECORDS && isFinished(st.status)) last = i;
  });
  if (last >= 0 && last < STEP_DEFS.length - 1) return { from: STEP_DEFS[last].yd, target: STEP_DEFS[last + 1].yd };
  return { from: START_YD, target: STEP_DEFS[0].yd };
}

/** The scorebug's "now" box: its small label and its line. */
export function scorebugNow(model: PipelineModel): { k: string; v: string } {
  if (model.outcome === 'plan') return { k: 'Waiting', v: model.idleLabel ?? 'Kickoff when the run starts' };
  if (model.done) {
    if (model.failed) {
      const f = model.steps.find((s) => s.status === 'failed');
      return { k: 'Turnover', v: f ? `Fumble at the ${f.key} step` : 'Fumble' };
    }
    if (model.outcome === 'stopped') return { k: 'Stopped', v: 'Stopped before the digest' };
    return { k: 'Final', v: 'Touchdown · digest published' };
  }
  const running = model.idx >= 0 ? model.steps[model.idx] : undefined;
  return { k: 'Now', v: running ? (running.detail ? `${running.label} — ${running.detail}` : running.label) : 'Between steps' };
}

const GOLD = '#E8C547'; // the mockup's goalpost yellow, before the extra point is tried

/** The goalposts show the run records (the extra point). */
export function goalpostColor(status: StepStatus): string {
  switch (status) {
    case 'ok':
      return 'var(--ok)';
    case 'degraded':
      return 'var(--warn)';
    case 'failed':
      return 'var(--err)';
    case 'running':
      return 'var(--accent)';
    case 'none':
      return 'var(--line-2)';
    default:
      return GOLD;
  }
}

// ---- pipeline map --------------------------------------------------------------------------

/** The snake: sources and eight steps along the top, back along the bottom to Published. */
export const MAP: Record<string, [number, number]> = {
  sources: [95, 112],
  ingest: [245, 112],
  ready: [395, 112],
  curate: [545, 112],
  ratings: [695, 112],
  game: [855, 112],
  graph: [855, 286],
  player: [675, 286],
  digest: [495, 286],
  records: [315, 286],
  published: [125, 286],
};
export const RING_R = 33;
export const RING_C = 2 * Math.PI * RING_R;

/** The sub-line under a node while a step runs: the mockup cuts it at 34 characters. */
export const clip = (text: string, max = 34): string => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

// ---- timeline ------------------------------------------------------------------------------

export interface TimelineRow {
  key: string;
  label: string;
  status: StepStatus;
  /** dashed bar: pending or skipped, drawn at its expected time */
  planned: boolean;
  /** no bar at all (run records the week never had) */
  hasBar: boolean;
  /** seconds the bar stands for (real, or expected when planned) */
  seconds: number;
  /** left edge and width, % of the axis */
  left: number;
  width: number;
}

export interface TimelineLayout {
  total: number;
  rows: TimelineRow[];
  /** axis labels, every `step` seconds: { minutes label, % from the left } */
  ticks: { label: string; left: number }[];
  /** vertical grid lines (% from the left) */
  grid: number[];
  /** the digest's share of the drawn time, whole %, or null when it has none */
  digestShare: number | null;
}

export function timelineLayout(model: PipelineModel): TimelineLayout {
  let start = 0;
  const rows: TimelineRow[] = model.steps.map((st) => {
    const planned = st.status === 'pending' || st.status === 'skipped';
    const hasBar = st.status !== 'none';
    const seconds = hasBar ? num(planned ? st.expected : st.seconds) : 0;
    const row = { key: st.key, label: st.label, status: st.status, planned, hasBar, seconds, left: start, width: seconds };
    start += seconds;
    return row;
  });
  // A skipped step is drawn at its expected time but isn't in `remaining`: the axis covers every bar.
  const total = Math.max(num(model.elapsed) + num(model.remaining), start, 1);
  const step = total > 1800 ? 600 : total > 900 ? 180 : 120;
  const ticks: TimelineLayout['ticks'] = [];
  for (let t = 0; t <= total; t += step) ticks.push({ label: `${Math.round(t / 60)}m`, left: (t / total) * 100 });
  const grid: number[] = [];
  for (let t = step; t < total; t += step) grid.push((t / total) * 100);
  const placed = rows.map((r) => ({ ...r, left: (r.left / total) * 100, width: (r.seconds / total) * 100 }));
  const digest = rows[STEP_INDEX.digest];
  const digestShare =
    digest && digest.hasBar && digest.status !== 'skipped' && start > 0 ? Math.round((digest.seconds / start) * 100) : null;
  return { total, rows: placed, ticks, grid, digestShare };
}
