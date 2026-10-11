// The Saturday injury update (CR02): the header's button with its own confirm dialog, and the
// notice the Pipeline tab shows while an update runs (or just ran) for that week; its lines go
// into that tab's one Log card. The server decides when it's open (Saturday ET once the Tuesday
// run is published, until the week's last kickoff); the browser only sends the kind and the
// server's week back.

import { useState } from 'react';
import { usePreflight, useRunCurrent, useStartRun } from '../../api/client';
import { fmtET, kickoffLabel } from '../../lib/format';
import { ConfirmDialog } from '../ConfirmDialog';
import { Chip, Notice } from '../ui';
import { runKey, startErrorToast, useInjuryUpdateRun, useRunUi } from './runUi';
import './run.css';

export function InjuryUpdateButton({
  season,
  week,
  fallbackReason,
}: {
  season: number;
  week: number;
  fallbackReason: string; // when pre-flight isn't about this week (or hasn't answered)
}) {
  const pfq = usePreflight();
  const cur = useRunCurrent();
  const start = useStartRun();
  const ui = useRunUi();
  const [open, setOpen] = useState(false);
  const pf = pfq.data;
  const gate = pf && pf.season === season && pf.week === week ? pf.injury_update : null;
  const running = cur.data?.state === 'running' ? cur.data.run : null;

  let allowed = false;
  let reason = fallbackReason;
  if (pf?.mode === 'rehearsal') reason = gate?.reason ?? 'Not offered in rehearsal mode';
  else if (running?.kind === 'injury_update') reason = 'Running now: its log is on the Pipeline tab';
  else if (running) reason = 'Another run is in progress';
  else if (gate) {
    allowed = gate.allowed && !start.isPending;
    reason = gate.reason ?? fallbackReason;
  }
  const last =
    gate?.material_last == null
      ? ''
      : gate.material_last
        ? ' · last update: addendum published'
        : ' · last update: nothing material';
  const note = allowed
    ? `${gate?.closes ? `Open until ${kickoffLabel(gate.closes)}` : 'Open now'}${last}`
    : reason;

  const confirm = () => {
    if (!pf || pf.week == null) return;
    start.mutate(
      { kind: 'injury_update', expect_week: pf.week },
      {
        onSuccess: (r) => {
          ui.launched.add(r.run_id);
          ui.toast({ title: `Week ${r.week} injury update started`, text: r.command });
        },
        onError: (e) => {
          ui.toast(startErrorToast(e));
          void pfq.refetch();
        },
      },
    );
  };

  return (
    <div className="saturday">
      <button
        type="button"
        className="btn sm"
        disabled={!allowed}
        aria-disabled={!allowed}
        title={
          allowed
            ? "Re-pulls injuries, news and lines and re-predicts the games that haven't started"
            : reason
        }
        onClick={() => setOpen(true)}
      >
        Saturday injury update
      </button>
      <span className="reason">{note}</span>
      {gate ? (
        <ConfirmDialog
          open={open}
          onOpenChange={setOpen}
          eyebrow="Confirm the update"
          title={`Run week ${week}'s injury update?`}
          confirmLabel="Run injury update"
          onConfirm={confirm}
        >
          <span>
            Re-pulls injuries, news and lines, re-predicts the games that haven&apos;t started, and
            publishes a short addendum to the digest only if something material changed.
          </span>
          <dl className="kv">
            <dt>Week</dt>
            <dd>
              <b>{week}</b> · from the calendar
            </dd>
            <dt>Open until</dt>
            <dd>{gate.closes ? kickoffLabel(gate.closes) : '—'}</dd>
            {gate.material_last != null ? (
              <>
                <dt>Last update</dt>
                <dd>{gate.material_last ? 'addendum published' : 'nothing material changed'}</dd>
              </>
            ) : null}
          </dl>
          {gate.command ? <div className="cmd">{gate.command}</div> : null}
        </ConfirmDialog>
      ) : null}
    </div>
  );
}

/** The injury update's status above the Pipeline tab: running, or how it ended. Its lines are
 * in the tab's one Log card (the week's events files, re-read every 2 s while it runs); the end
 * toast, with `material`, comes from the RunWatcher. */
export function InjuryUpdateNotice({ season, week }: { season: number; week: number }) {
  const { run, running } = useInjuryUpdateRun(season, week);
  const ui = useRunUi();
  const [, rerender] = useState(0);
  if (!run || ui.hidden.has(runKey(run))) return null;
  const hide = () => {
    ui.hidden.add(runKey(run));
    rerender((n) => n + 1);
  };
  const failed = run.status === 'failed' || run.status === 'interrupted' || run.exit_code === 1;

  return (
    <Notice tone="accent" icon={running ? '●' : failed ? '!' : 'i'}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <b>Saturday injury update</b>
        {running ? (
          <Chip tone="run" pulse>
            Running
          </Chip>
        ) : (
          <span className="muted">
            {run.finished
              ? `finished ${fmtET(run.finished, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET`
              : 'finished'}
          </span>
        )}
        {!running ? (
          <button
            type="button"
            className="btn sm"
            style={{ marginLeft: 'auto' }}
            onClick={hide}
          >
            Hide
          </button>
        ) : null}
      </div>
      <div style={{ marginTop: 4 }}>
        {running
          ? "Re-pulling injuries, news and lines and re-predicting the games that haven't started: its lines follow the run's in the Log below. Closing this tab doesn't stop it."
          : failed
            ? (
                <>
                  {run.message ?? 'The update failed.'}
                  <div className="muted">Its lines are at the end of the Log below.</div>
                </>
              )
            : run.material === true
              ? 'Addendum published: it shows under the digest on the Digest tab.'
              : run.material === false
                ? 'Nothing material changed: no addendum.'
                : (run.message ?? 'The update has ended.')}
      </div>
      {/* a process that died without its run_end: its last output isn't in the events file */}
      {!running && run.output_tail.length > 0 ? (
        <div className="mono muted" style={{ marginTop: 6, fontSize: 11.5, whiteSpace: 'pre-wrap' }}>
          {run.output_tail.join('\n')}
        </div>
      ) : null}
    </Notice>
  );
}
