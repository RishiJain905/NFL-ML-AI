// The Run button's confirm dialog (mockup: confirmRun). Every fact comes from the server's
// pre-flight: the week is the calendar's, never one from the page.

import type { PreflightResponse } from '../../api/types';
import { kickoffLabel } from '../../lib/format';
import { ConfirmDialog } from '../ConfirmDialog';

export function RunConfirm({
  pf,
  open,
  onOpenChange,
  onConfirm,
}: {
  pf: PreflightResponse;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const rehearsal = pf.mode === 'rehearsal';
  const c = pf.confirm;
  const hours = c.hours_to_deadline != null ? ` · ${Math.round(c.hours_to_deadline)} h` : '';
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      eyebrow={rehearsal ? 'Confirm the rehearsal' : 'Confirm the week'}
      title={`${rehearsal ? 'Rehearse' : 'Run'} ${pf.season} week ${pf.week}?`}
      confirmLabel={`${rehearsal ? 'Rehearse' : 'Run'} week ${pf.week}`}
      onConfirm={onConfirm}
    >
      <dl className="kv">
        <dt>Week</dt>
        <dd>
          <b>{pf.week}</b> · {rehearsal ? 'the rehearsed week' : 'from the calendar'}
        </dd>
        <dt>Last week</dt>
        <dd>{c.last_week}</dd>
        <dt>Deadline</dt>
        <dd>{c.deadline ? `${kickoffLabel(c.deadline)}${hours}` : '—'}</dd>
        <dt>Steps</dt>
        <dd>{c.steps.join(' → ')}</dd>
        <dt>{rehearsal ? 'Writes' : 'Publishes'}</dt>
        <dd>{c.publishes.length ? c.publishes.join(' · ') : 'nothing'}</dd>
      </dl>
      {pf.run.command ? <div className="cmd">{pf.run.command}</div> : null}
      {rehearsal ? (
        <span>
          The rehearsal writes only to a scratch folder: no published digest, no W&amp;B, no graph.
        </span>
      ) : (
        <span>
          If the calendar&apos;s week has changed by the time the run starts, it stops before
          touching anything (<code>--expect-week</code>).
        </span>
      )}
    </ConfirmDialog>
  );
}
