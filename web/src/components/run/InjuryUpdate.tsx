// The Saturday injury update (CR02): the header's button with its own confirm dialog, and the
// live log card WeekPage shows while an update runs (or just ran) for that week. The server
// decides when it's open (Saturday ET once the Tuesday run is published, until the week's last
// kickoff); the browser only sends the kind and the server's week back.

import { useEffect, useState } from 'react';
import { usePreflight, useRunCurrent, useStartRun } from '../../api/client';
import type { LogLine, RunEndEvent } from '../../api/types';
import { eventsToLog, useRunStream } from '../../api/stream';
import { fmtET, kickoffLabel } from '../../lib/format';
import { useNow } from '../../lib/useNow';
import { ConfirmDialog } from '../ConfirmDialog';
import { LogPanel } from '../pipeline/LogPanel';
import { Chip, Notice } from '../ui';
import { notify } from './notify';
import { followed, injuryEndToast, runKey, startErrorToast, useRunUi } from './runUi';
import './run.css';

/** A finished update's log stays on the week page this long (unless hidden). */
const RECENT_MS = 3 * 3600_000;

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
  else if (running?.kind === 'injury_update') reason = 'Running now: its log is below';
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

/** The injury update's live log on its week's page, and its end toast (with `material`). */
export function InjuryUpdateLog({ season, week }: { season: number; week: number }) {
  const cur = useRunCurrent();
  const ui = useRunUi();
  const now = useNow();
  const [hidden, setHidden] = useState<string | null>(null);
  const run = cur.data?.run ?? null;
  const mine =
    run != null && run.kind === 'injury_update' && run.season === season && run.week === week;
  const key = mine ? runKey(run) : null;
  const running = mine && cur.data?.state === 'running';
  const finishedAt = run?.finished ? Date.parse(run.finished) : NaN;
  const recent = !Number.isNaN(finishedAt) && now - finishedAt < RECENT_MS;
  const following = mine && (running || followed(ui, run));
  const show = mine && key !== hidden && (running || following || recent);
  const stream = useRunStream(Boolean(show && (running || run?.has_events)), show ? key : null);
  const endEvent = stream.events.find((e): e is RunEndEvent => e.type === 'run_end') ?? null;
  const ended = endEvent != null || stream.end != null;

  // while this card follows the run, it (not the watcher) announces the end: it knows `material`
  useEffect(() => {
    if (!following || !key) return undefined;
    ui.claimed.add(key);
    return () => {
      ui.claimed.delete(key);
    };
  }, [following, key, ui]);

  useEffect(() => {
    if (!following || !key || !ended || ui.announced.has(key)) return;
    ui.announced.add(key);
    const t = injuryEndToast(stream.end ?? run, endEvent);
    ui.toast(t);
    notify(t.title, t.text);
  }, [following, key, ended, endEvent, stream.end, run, ui]);

  if (!show || !run) return null;
  let lines: LogLine[] = eventsToLog(stream.events);
  if (lines.length === 0 && !running)
    lines = run.output_tail.map((text) => ({ ts: '', text, cls: '' }));
  const material = endEvent?.material ?? null;
  const live = running && !ended;

  return (
    <div className="injurylog">
      <Notice tone="accent" icon={live ? '●' : 'i'}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <b>Saturday injury update</b>
          {live ? (
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
          {!live ? (
            <button
              type="button"
              className="btn sm"
              style={{ marginLeft: 'auto' }}
              onClick={() => setHidden(key)}
            >
              Hide
            </button>
          ) : null}
        </div>
        <div style={{ marginTop: 4 }}>
          {live
            ? "Re-pulling injuries, news and lines and re-predicting the games that haven't started. Closing this tab doesn't stop it."
            : material === true
              ? 'Addendum published: it shows under the digest on the Digest tab.'
              : material === false
                ? 'Nothing material changed: no addendum.'
                : (run.message ?? 'The update has ended.')}
        </div>
      </Notice>
      <LogPanel lines={lines} source="none" live={live} />
    </div>
  );
}
