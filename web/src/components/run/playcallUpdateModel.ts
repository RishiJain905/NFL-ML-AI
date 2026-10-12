// "Update play calling" (D132): the steps of `nfl playcalling update --auto --expect-week N`
// folded from pre-flight's plan and the run's events (mockup: updFold in
// documentation/play-calling/mockup/playcalling.js), and the toast when the update ends.

import type { PlaycallUpdateStep, RunEvent, RunInfo } from '../../api/types';
import type { ToastInput } from './runUi';

/** A step's state on the card: before a run `will` (due) or `off` (not due); during and after it
 *  `pending` → `running` → `ok` / `failed` / `skipped` (an earlier step failed), and `off` for a
 *  step that isn't in this run. */
export type UpdateStepState = 'will' | 'off' | 'pending' | 'running' | 'ok' | 'failed' | 'skipped';

export interface UpdateStepView extends PlaycallUpdateStep {
  state: UpdateStepState;
  detail: string | null; // step_end's detail once the step ended
}

export interface UpdateFold {
  steps: UpdateStepView[];
  log: Extract<RunEvent, { type: 'log' }>[];
  end: Extract<RunEvent, { type: 'run_end' }> | null;
  started: boolean; // the run's run_start has been seen
}

export const STATE_WORD: Record<UpdateStepState, string> = {
  will: 'will run',
  off: 'not due',
  pending: 'waiting',
  running: 'running',
  ok: 'done',
  failed: 'failed',
  skipped: 'skipped',
};

export const STATE_ICON: Record<UpdateStepState, string> = {
  will: '●',
  off: '–',
  pending: '○',
  running: '◐',
  ok: '✓',
  failed: '✗',
  skipped: '–',
};

/** The plan's steps with the run's progress on them (the events of one update run). */
export function foldUpdate(plan: PlaycallUpdateStep[], events: RunEvent[]): UpdateFold {
  const start = events.find((e): e is Extract<RunEvent, { type: 'run_start' }> => e.type === 'run_start') ?? null;
  const byStep = new Map<string, { state: UpdateStepState; detail: string | null }>();
  for (const s of plan) {
    const inRun = start ? start.steps.includes(s.step) : null;
    byStep.set(s.step, { state: inRun === null ? (s.due ? 'will' : 'off') : inRun ? 'pending' : 'off', detail: null });
  }
  for (const e of events) {
    if (e.type === 'step_start') {
      const x = byStep.get(e.step);
      if (x) x.state = 'running';
    } else if (e.type === 'step_end') {
      const x = byStep.get(e.step);
      if (!x) continue;
      // the update's step_end status is ok | failed | skipped (the weekly run's adds degraded)
      const st = String(e.status);
      x.state = st === 'failed' ? 'failed' : st === 'skipped' ? 'skipped' : 'ok';
      x.detail = e.detail || null;
    }
  }
  return {
    steps: plan.map((s) => ({ ...s, ...(byStep.get(s.step) ?? { state: 'off' as const, detail: null }) })),
    log: events.filter((e): e is Extract<RunEvent, { type: 'log' }> => e.type === 'log'),
    end: events.find((e): e is Extract<RunEvent, { type: 'run_end' }> => e.type === 'run_end') ?? null,
    started: start != null,
  };
}

/** The failed step of an ended update: run_end's / RunInfo's `failed_step`, else its message
 *  ("done: ftn, build; failed: grade"). */
export function failedStepOf(run: { failed_step: string | null; message: string | null } | null): string | null {
  if (!run) return null;
  if (run.failed_step) return run.failed_step;
  return /failed:\s*([a-z_]+)/.exec(run.message ?? '')?.[1] ?? null;
}

export type UpdateTone = 'ok' | 'warn' | 'err';

/** What an ended update did, from the runner's status (run_end's, else the exit code's). Only a
 *  run that ended with run_end "ok" (or exit 0 with nothing due: "idle") reads as success; a
 *  refusal, a missing drive or an end the app didn't see never does (Sol review, D132). */
export function updateResult(
  run: Pick<RunInfo, 'status' | 'exit_code' | 'message'> & { failed_step: string | null },
): { tone: UpdateTone; title: string } {
  switch (run.status) {
    case 'ok':
      return { tone: 'ok', title: 'Play calling updated' };
    case 'idle':
      return { tone: 'ok', title: 'Nothing was due: nothing ran' };
    case 'locked':
      return { tone: 'warn', title: 'Another run holds the lock: nothing ran' };
    case 'week_mismatch':
      return { tone: 'warn', title: "The calendar's week changed: nothing ran" };
    case 'no_data_root':
      return { tone: 'err', title: "The data drive isn't there: nothing ran" };
    case 'usage':
      return { tone: 'err', title: 'The update command was refused: nothing ran' };
    case 'failed':
    case 'interrupted': {
      const step = failedStepOf(run);
      return { tone: 'err', title: step ? `The play-calling update failed at ${step}` : 'The play-calling update failed' };
    }
    default: // unknown: it ended while the app wasn't watching and left no events
      return { tone: 'warn', title: 'The play-calling update ended without a result' };
  }
}

/** Did the update end without doing its job (anything but a confirmed success)? */
export function updateFailed(run: Pick<RunInfo, 'status' | 'exit_code' | 'message' | 'failed_step'>): boolean {
  return updateResult(run).tone !== 'ok';
}

/** The toast (and browser notification) for an update that ended. */
export function playcallEndToast(run: RunInfo): ToastInput {
  const r = updateResult(run);
  return { title: r.title, text: run.message ?? undefined, tone: r.tone };
}
