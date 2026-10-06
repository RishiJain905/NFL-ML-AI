// Shared state for the run controls (CR02): the toaster, and what this page has seen of the
// runs (launched here, seen running, already announced), so the end of a run is announced once
// and old runs are never announced on page load. RunUiProvider (in Shell) holds it.

import { createContext, useContext } from 'react';
import { ApiError } from '../../api/client';
import type { RunEndEvent, RunInfo } from '../../api/types';

export type ToastTone = 'ok' | 'warn' | 'err';
export interface ToastInput {
  title: string;
  text?: string;
  tone?: ToastTone;
}

export interface RunUi {
  toast: (t: ToastInput) => void;
  /** run ids this page started (POST /api/run) */
  launched: Set<string>;
  /** runs (by runKey) this page saw running */
  seenRunning: Set<string>;
  /** runs whose end was already toasted */
  announced: Set<string>;
  /** injury updates whose end the week page's log card announces (it has `material`) */
  claimed: Set<string>;
}

export function newRunUi(toast: RunUi['toast'] = () => {}): RunUi {
  return {
    toast,
    launched: new Set(),
    seenRunning: new Set(),
    announced: new Set(),
    claimed: new Set(),
  };
}

export const RunUiContext = createContext<RunUi>(newRunUi());

export function useRunUi(): RunUi {
  return useContext(RunUiContext);
}

/** A run's identity: its run_id, or its start time for a terminal run without events. */
export function runKey(run: RunInfo): string {
  return run.run_id ?? run.started ?? '';
}

/** Remember a run seen running, by both its ids (a terminal run gets its run_id later). */
export function markSeen(ui: RunUi, run: RunInfo): void {
  for (const id of [run.run_id, run.started]) if (id) ui.seenRunning.add(id);
}

/** Whether this page followed the run (so its end is news, not history). */
export function followed(ui: RunUi, run: RunInfo): boolean {
  return [run.run_id, run.started].some(
    (id) => id != null && (ui.seenRunning.has(id) || ui.launched.has(id)),
  );
}

/** The toast for a refused or failed POST /api/run. */
export function startErrorToast(err: unknown): ToastInput {
  const message = err instanceof Error ? err.message : String(err);
  if (err instanceof ApiError && err.status === 409) {
    return {
      // `running`: the app's own run is going; `locked`: another process (a terminal) holds it
      title: err.code === 'running' ? 'A run is already going' : 'Another run holds the lock',
      text: `${message} Nothing new was started.`,
      tone: 'err',
    };
  }
  if (err instanceof ApiError && err.status === 403) {
    return {
      title: "The server didn't allow the run",
      text: `${message} Pre-flight was checked again.`,
      tone: 'err',
    };
  }
  return { title: "The run didn't start", text: message, tone: 'err' };
}

/** The toast (and browser notification) for an injury update that ended. */
export function injuryEndToast(run: RunInfo | null, end?: RunEndEvent | null): ToastInput {
  const status = end?.status ?? run?.status ?? '';
  const message = end?.message ?? run?.message ?? undefined;
  const code = end?.exit_code ?? run?.exit_code ?? null;
  if (status === 'failed' || status === 'interrupted' || code === 1) {
    return { title: 'The injury update failed', text: message, tone: 'err' };
  }
  if (code === 4 || status === 'locked')
    return { title: 'Another run holds the lock: nothing ran', text: message, tone: 'warn' };
  // run_end's `material`, copied onto the final RunInfo by the server
  const material = end?.material ?? run?.material ?? null;
  if (material === true) return { title: 'Addendum published', text: message, tone: 'ok' };
  if (material === false)
    return { title: 'Nothing material changed: no addendum', text: message, tone: 'ok' };
  return { title: 'Injury update finished', text: message, tone: 'ok' };
}

/** The toast (and browser notification) when a run ends (mockup: `loop`'s toasts). */
export function runEndToast(run: RunInfo): ToastInput {
  if (run.kind === 'injury_update') return injuryEndToast(run);
  const w = run.week != null ? `Week ${run.week}` : 'The run';
  const text = run.message ?? undefined;
  const status = run.status ?? '';
  const code = run.exit_code;
  if (status === 'week_mismatch' || code === 6) {
    return { title: "The calendar's week changed: nothing ran", text, tone: 'warn' };
  }
  if (status === 'not_ready' || code === 3) {
    const last = run.week != null && run.week > 1 ? `Week ${run.week - 1}` : 'Last week';
    return { title: `${last} isn't final yet: try again later`, text, tone: 'warn' };
  }
  if (status === 'locked' || code === 4)
    return { title: 'Another run holds the lock: nothing ran', text, tone: 'warn' };
  if (status === 'no_data_root' || code === 5)
    return { title: "The data drive wasn't found: nothing ran", text, tone: 'err' };
  if (status === 'usage' || code === 2)
    return { title: `${w}: the command was refused (usage)`, text, tone: 'err' };
  if (status === 'already_done')
    return { title: `${w} was already published: nothing ran`, text, tone: 'ok' };
  if (status === 'unknown') {
    return {
      title: `${w}: the run ended while the app wasn't watching`,
      text: text ?? 'It left no events; see the Pipeline tab for its records.',
      tone: 'warn',
    };
  }
  if (status === 'interrupted') {
    return { title: `${w} stopped: the run was interrupted`, text, tone: 'err' };
  }
  if (status === 'failed' || code === 1) {
    const step = run.failed_step;
    return {
      title: step ? `${w} failed at the ${step} step` : `${w} failed`,
      text:
        text ??
        (step
          ? `The run stopped and wrote its records. Use "Resume from ${step}" once it's fixed.`
          : 'The run stopped and wrote its records.'),
      tone: 'err',
    };
  }
  if (run.kind === 'rehearsal')
    return { title: `${w} rehearsed`, text, tone: status === 'degraded' ? 'warn' : 'ok' };
  if (run.published) {
    return {
      title: `${w} published`,
      text: text ?? 'The digest is out and the tabs have refreshed.',
      tone: status === 'degraded' ? 'warn' : 'ok',
    };
  }
  return {
    title: `${w}: the run ended (${status || 'no status'})`,
    text,
    tone: code === 0 ? 'ok' : 'warn',
  };
}
