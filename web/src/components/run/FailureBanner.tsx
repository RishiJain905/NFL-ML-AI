// The failure banner with "Resume week N from <step>" (mockup: week5Pipeline's failed state).
// PipelineTab renders it for a failed run. The resume button appears only when the server's
// pre-flight offers a resume for this week from this very step; otherwise the banner gives the
// terminal command (an older week, or the server says no).

import { useState } from 'react';
import { usePreflight, useStartRun } from '../../api/client';
import type { StepName } from '../../api/types';
import { ConfirmDialog } from '../ConfirmDialog';
import { Notice } from '../ui';
import { startErrorToast, useRunUi } from './runUi';
import './run.css';

const sentence = (s: string) => (/[.!?]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`);

export function FailureBanner({
  season,
  week,
  failedStep,
  detail,
  error,
}: {
  season: number;
  week: number;
  failedStep: StepName | null;
  detail: string | null;
  error: string | null;
}) {
  const pfq = usePreflight();
  const start = useStartRun();
  const ui = useRunUi();
  const [open, setOpen] = useState(false);
  const pf = pfq.data;
  const about = pf != null && pf.season === season && pf.week === week;
  const resume = about ? pf.resume : null;
  const canResume = Boolean(resume?.allowed && failedStep && resume.step === failedStep);
  const rehearsal = pf?.mode === 'rehearsal';
  const why = detail ?? error;
  const terminal = `nfl weekly run --season ${season} --week ${week} --from-step ${failedStep ?? '<step>'}`;

  const confirm = () => {
    if (!pf || pf.week == null) return;
    start.mutate(
      { kind: 'resume', expect_week: pf.week },
      {
        onSuccess: (r) => {
          ui.launched.add(r.run_id);
          ui.toast({ title: `Week ${r.week} resumed from ${failedStep}`, text: r.command });
        },
        onError: (e) => {
          ui.toast(startErrorToast(e));
          void pfq.refetch();
        },
      },
    );
  };

  return (
    <Notice tone="err" icon="✕">
      <b>{failedStep ? `The ${failedStep} step failed.` : 'The run failed.'}</b>{' '}
      {why ? `${sentence(why)} ` : ''}
      Steps before it are saved; the digest didn&apos;t run, so nothing was published. Fix the
      cause, then resume. The app only offers a resume for this week, from the step that failed.
      {canResume && resume ? (
        <div className="runrow">
          <button
            type="button"
            className="btn primary"
            disabled={start.isPending}
            aria-disabled={start.isPending}
            onClick={() => setOpen(true)}
          >
            Resume week {week} from {failedStep}
          </button>
          {resume.command ? <span className="cmd">{resume.command}</span> : null}
        </div>
      ) : failedStep ? (
        <div className="runrow">
          <span>From a terminal:</span>
          <span className="cmd">{terminal}</span>
          {resume?.reason ? <span className="reason">{resume.reason}</span> : null}
        </div>
      ) : null}
      {canResume ? (
        <ConfirmDialog
          open={open}
          onOpenChange={setOpen}
          eyebrow="Confirm the resume"
          title={`Resume ${season} week ${week} from ${failedStep}?`}
          confirmLabel={`Resume from ${failedStep}`}
          onConfirm={confirm}
        >
          <dl className="kv">
            <dt>Week</dt>
            <dd>
              <b>{week}</b> · {rehearsal ? 'the rehearsed week' : 'from the calendar'}
            </dd>
            <dt>From step</dt>
            <dd>{failedStep}</dd>
          </dl>
          {resume?.command ? <div className="cmd">{resume.command}</div> : null}
          {rehearsal ? (
            <span>
              Steps before {failedStep} are kept; the rehearsal continues from there, into the
              rehearsal folder only.
            </span>
          ) : (
            <span>
              Steps before {failedStep} are kept; the run continues from there and publishes as the
              Tuesday run would.
            </span>
          )}
        </ConfirmDialog>
      ) : null}
    </Notice>
  );
}
