// The Forecast lab update (D158): `nfl forecastlab update --auto --expect-week N`, which the app
// starts by itself after Run week N / a resume / the injury update ends well, and which the
// Availability and Outcomes tabs' Update button runs too. Who started it, how it ended, and the
// toasts. (Not `forecastLabUpdate.ts`: Windows' case-insensitive names would clash with the .tsx.)

import type { RunInfo } from '../../api/types';
import { failedStepOf } from './playcallUpdateModel';
import type { ToastInput, ToastTone } from './runUi';

export const LAB_KIND = 'forecastlab_update';

/** The button's label (both tabs). */
export const UPDATE_LABEL = 'Update availability and outcomes';

/** The gate's reason when nothing is due: the card then shrinks to one quiet line. */
export const UP_TO_DATE = 'Everything is up to date.';

/** The steps' names in plain words (the run's `failed_step` is one of these). */
const STEP_WORD: Record<string, string> = {
  availability: 'availability',
  curves: 'the outcome curves',
  grade: "last week's grading",
};

export type LabStarter = 'chain' | 'here' | 'button' | 'terminal';

/** Who started the update: the app after a run (`follows`), this page's press, another page's
 *  press (the Update button elsewhere), or a terminal. */
export function starterOf(run: Pick<RunInfo, 'run_id' | 'launched_from' | 'follows'>, launchedHere: Set<string>): LabStarter {
  if (run.launched_from === 'terminal') return 'terminal';
  if (run.follows) return 'chain';
  if (run.run_id != null && launchedHere.has(run.run_id)) return 'here';
  return 'button';
}

export const STARTER_TEXT: Record<LabStarter, string> = {
  chain: "Started by the app after the week's run ended",
  here: 'You pressed Update',
  button: 'Started with the Update button',
  terminal: 'Started from a terminal',
};

/** What an ended update did (the play-calling rule, D132: only `ok` from run_end, or exit 0 with
 *  nothing due, reads as success; a refusal, a missing drive or an unseen end never does). */
export function labResult(
  run: Pick<RunInfo, 'status' | 'exit_code' | 'message'> & { failed_step: string | null },
): { tone: ToastTone; title: string } {
  switch (run.status) {
    case 'ok':
      return { tone: 'ok', title: 'Availability and outcomes updated' };
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
      return {
        tone: 'err',
        title: step ? `The availability and outcomes update failed at ${STEP_WORD[step] ?? step}` : 'The availability and outcomes update failed',
      };
    }
    default: // unknown: it ended while the app wasn't watching and left no events
      return { tone: 'warn', title: 'The availability and outcomes update ended without a result' };
  }
}

/** The toast (and browser notification) when an update ends. A failure never touches the
 *  published week, so the text says so. */
export function labEndToast(run: RunInfo): ToastInput {
  const r = labResult(run);
  const text = [run.message, r.tone === 'err' ? "The published week isn't touched." : null].filter(Boolean).join(' ');
  return { title: r.title, text: text || undefined, tone: r.tone };
}

/** The toast when the app starts the update by itself after a run this page followed. */
export function labChainToast(run: RunInfo): ToastInput {
  return {
    title: `Week ${run.week ?? '—'}: refreshing availability and outcomes`,
    text: 'Started by the app after the run. Its progress is on the Availability and Outcomes tabs.',
  };
}
