// "Update play calling" (D132; mockup: updCard in documentation/play-calling/mockup/playcalling.js):
// the current week's Play calls tab runs every post-run play-calling step from the app
// (`nfl playcalling update --auto --expect-week N`): Monday night's FTN, the tables, last week's
// decision review and forecast grade, this week's forecast. Ready: pre-flight's `playcalling`
// gate (each step due or not, and why). Running / finished: the run's own events from
// /api/run/stream (its steps and the last lines of its log). The server decides; the browser
// sends only the kind and the server's week back. Its events never go to the Pipeline tab.

import { useState } from 'react';
import { usePreflight, useRunCurrent, useStartRun } from '../../api/client';
import { eventClock, useRunStream } from '../../api/stream';
import type { PlaycallUpdateStep, RunInfo } from '../../api/types';
import { useNow } from '../../lib/useNow';
import { ConfirmDialog } from '../ConfirmDialog';
import { Card, CardHeader, Chip, Notice } from '../ui';
import { STATE_ICON, STATE_WORD, foldUpdate, updateResult, type UpdateStepView } from './playcallUpdateModel';
import { followed, runKey, startErrorToast, useRunUi } from './runUi';
import './run.css';

/** A finished update's result stays on the tab this long when this page didn't follow it. */
const RECENT_MS = 30 * 60_000;

function StepList({ steps, compact }: { steps: UpdateStepView[]; compact?: boolean }) {
  return (
    <ol className={`usteps${compact ? ' compact' : ''}`}>
      {steps.map((s) => (
        <li key={s.step} className={`ust ${s.state}`} aria-label={`${s.label}: ${STATE_WORD[s.state]}`}>
          <span className="ui" aria-hidden="true">
            {STATE_ICON[s.state]}
          </span>
          <span className="ul">
            <b>{s.label}</b>
            <small>{s.detail ?? s.reason}</small>
          </span>
          {compact ? null : <span className="uw">{STATE_WORD[s.state]}</span>}
        </li>
      ))}
    </ol>
  );
}

/** This week's play-calling update, if it's the current run: running, or finished and followed
 *  here (or recent). */
function useUpdateRun(season: number, week: number) {
  const cur = useRunCurrent();
  const ui = useRunUi();
  const now = useNow();
  const run = cur.data?.run ?? null;
  const state = cur.data?.state ?? 'idle';
  const mine = run != null && run.kind === 'playcalling_update' && run.season === season && run.week === week;
  const running = mine && state === 'running';
  const finishedAt = run?.finished ? Date.parse(run.finished) : NaN;
  const recent = !Number.isNaN(finishedAt) && now - finishedAt < RECENT_MS;
  const show = mine && (running || followed(ui, run) || recent) && !ui.hidden.has(runKey(run));
  const other = state === 'running' && run != null && !mine ? run : null;
  return { run: show ? run : null, running, other };
}

function otherRunText(run: RunInfo): string {
  if (run.kind === 'playcalling_update') return `Another play-calling update is running (week ${run.week ?? '—'})`;
  if (run.kind === 'injury_update') return `The week ${run.week ?? '—'} injury update is running`;
  return `Another run is in progress${run.week != null ? ` (week ${run.week})` : ''}`;
}

export function PlaycallUpdateCard({ season, week }: { season: number; week: number }) {
  const pfq = usePreflight();
  const start = useStartRun();
  const ui = useRunUi();
  const [open, setOpen] = useState(false);
  const [, rerender] = useState(0);
  const { run, running, other } = useUpdateRun(season, week);
  const stream = useRunStream(run != null && (run.launched_from === 'app' || run.has_events), run ? runKey(run) : null);
  const pf = pfq.data;
  const gate = pf && pf.season === season && pf.week === week ? pf.playcalling : null;
  if (!gate) return null; // pre-flight is about another week (or hasn't answered): no card

  const plan: PlaycallUpdateStep[] = gate.steps;
  const fold = foldUpdate(plan, run ? stream.events : []);
  const due = plan.filter((s) => s.due);
  const finished = run != null && !running;
  const result = finished ? updateResult(run) : null;
  const rehearsal = pf?.mode === 'rehearsal';

  let allowed = false;
  let reason: string | null = gate.reason;
  if (other) reason = otherRunText(other);
  else if (!running) {
    allowed = gate.allowed && !start.isPending;
    if (allowed) reason = `${due.length} step${due.length === 1 ? '' : 's'} to run`;
  }

  const confirm = () => {
    if (!pf || pf.week == null) return;
    start.mutate(
      { kind: 'playcalling_update', expect_week: pf.week },
      {
        onSuccess: (r) => {
          ui.launched.add(r.run_id);
          ui.toast({ title: `Week ${r.week} play-calling update started`, text: r.command });
        },
        onError: (e) => {
          ui.toast(startErrorToast(e));
          void pfq.refetch();
        },
      },
    );
  };
  const hide = () => {
    if (run) ui.hidden.add(runKey(run));
    rerender((n) => n + 1);
  };

  // during a run, and after it while its result shows, the steps are the run's; otherwise the plan's
  const steps = run && fold.started ? fold.steps : foldUpdate(plan, []).steps;
  const tail = fold.log.slice(-5);

  return (
    <Card className={`pcupd${running ? ' live' : ''}`}>
      <CardHeader
        title="Update play calling"
        sub={
          running
            ? "its steps and last lines follow the run · closing this tab doesn't stop it"
            : "the steps after Tuesday's run: Monday night's FTN, the tables, last week's review and grade, this week's forecast"
        }
        right={
          running ? (
            <Chip tone="run" pulse>
              Running
            </Chip>
          ) : (
            <span className="uright">
              <button
                type="button"
                className="btn sm primary"
                disabled={!allowed}
                aria-disabled={!allowed}
                title={allowed ? (gate.command ?? undefined) : (reason ?? undefined)}
                onClick={() => setOpen(true)}
              >
                Update play calling
              </button>
              {reason ? <span className="reason">{reason}</span> : null}
            </span>
          )
        }
      />
      <div className="card-b">
        {run && result ? (
          <Notice tone={result.tone === 'err' ? 'err' : result.tone === 'warn' ? 'accent' : undefined} icon={result.tone === 'ok' ? '✓' : '!'}>
            <b>{result.title}.</b> {run.message ?? ''}
            {run.status === 'ok' ? ' The forecasts and grades below are refreshed.' : ''}
            {result.tone === 'err' ? " Fix it and press Update play calling again: it runs only what's still due." : ''}{' '}
            <button type="button" className="btn sm" onClick={hide}>
              Hide
            </button>
            {result.tone !== 'ok' && run.output_tail.length ? <div className="mono muted utail">{run.output_tail.join('\n')}</div> : null}
          </Notice>
        ) : null}
        <StepList steps={steps} />
        {run && tail.length ? (
          <div className="ulog mono" role="log" aria-label="The update's last lines">
            {tail.map((l) => (
              <div key={l.seq} className={l.cls ?? undefined}>
                <span className="muted">{eventClock(l.t)}</span> {l.text}
              </div>
            ))}
          </div>
        ) : null}
        {!run && gate.command ? (
          <div className="ufoot muted">
            Runs <span className="mono">{gate.command}</span> · holds the weekly lock while it runs · only the steps marked &quot;will run&quot;
            {rehearsal ? ' · rehearsal mode: the server decides whether it runs' : ''}
          </div>
        ) : null}
      </div>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        eyebrow="Confirm the update"
        title={`Update week ${week}'s play calling?`}
        confirmLabel="Update play calling"
        onConfirm={confirm}
      >
        <span>
          Runs the steps that are due, in order. A failed FTN refresh or build skips the steps that need it; any other failure lets the
          rest run:
        </span>
        <StepList steps={foldUpdate(due, []).steps} compact />
        <dl className="kv">
          <dt>Week</dt>
          <dd>
            <b>{week}</b> · from the calendar
          </dd>
          <dt>Skipped</dt>
          <dd>
            {plan
              .filter((s) => !s.due)
              .map((s) => s.label)
              .join(' · ') || 'nothing'}
          </dd>
        </dl>
        {gate.command ? <div className="cmd">{gate.command}</div> : null}
        <span>
          It holds the weekly lock while it runs: Run week {week} and the injury update wait for it. Its log stays on this tab, not the Pipeline
          tab.
        </span>
      </ConfirmDialog>
    </Card>
  );
}
