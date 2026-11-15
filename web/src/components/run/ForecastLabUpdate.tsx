// "Update availability and outcomes" (D158): the Availability and Outcomes tabs' card for the
// current week. The app starts `nfl forecastlab update --auto --expect-week N` by itself after
// Run week N, a resume or the injury update ends well; this card follows that run live (its steps
// and last lines from /api/run/stream) and is the fallback when the follow-up didn't run (a run
// from a terminal, the app closed mid-run, a failed step). Ready: pre-flight's `forecastlab` gate
// (each step due or not, and why). Nothing due: one quiet line. The server decides; the browser
// sends only the kind and the server's week back. Its events never go to the Pipeline tab.

import { useState } from 'react';
import { usePreflight, useRunCurrent, useStartRun } from '../../api/client';
import { eventClock, useRunStream } from '../../api/stream';
import type { ForecastLabUpdateStep, RunInfo } from '../../api/types';
import { useNow } from '../../lib/useNow';
import { ConfirmDialog } from '../ConfirmDialog';
import { Card, CardHeader, Chip, Notice } from '../ui';
import { LAB_KIND, STARTER_TEXT, UPDATE_LABEL, UP_TO_DATE, labResult, starterOf } from './forecastLabRunModel';
import { STATE_ICON, STATE_WORD, foldUpdate, type UpdateStepView } from './playcallUpdateModel';
import { followed, runKey, startErrorToast, useRunUi } from './runUi';
import './run.css';

/** A finished update's result stays on the tab this long when this page didn't follow it. */
const RECENT_MS = 30 * 60_000;

function StepList({ steps, compact }: { steps: UpdateStepView<ForecastLabUpdateStep>[]; compact?: boolean }) {
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

/** This week's Forecast lab update, if it's the current run: running, or finished and followed
 *  here (or recent). */
function useLabRun(season: number, week: number) {
  const cur = useRunCurrent();
  const ui = useRunUi();
  const now = useNow();
  const run = cur.data?.run ?? null;
  const state = cur.data?.state ?? 'idle';
  const mine = run != null && run.kind === LAB_KIND && run.season === season && run.week === week;
  const running = mine && state === 'running';
  const finishedAt = run?.finished ? Date.parse(run.finished) : NaN;
  const recent = !Number.isNaN(finishedAt) && now - finishedAt < RECENT_MS;
  const show = mine && (running || followed(ui, run) || recent) && !ui.hidden.has(runKey(run));
  const other = state === 'running' && run != null && !mine ? run : null;
  return { run: show ? run : null, running, other };
}

function otherRunText(run: RunInfo): string {
  if (run.kind === LAB_KIND) return `Another availability and outcomes update is running (week ${run.week ?? '—'})`;
  if (run.kind === 'injury_update') return `The week ${run.week ?? '—'} injury update is running: this refreshes after it`;
  if (run.kind === 'playcalling_update') return 'The play-calling update is running';
  return `Another run is in progress${run.week != null ? ` (week ${run.week})` : ''}`;
}

export function ForecastLabUpdateCard({ season, week }: { season: number; week: number }) {
  const pfq = usePreflight();
  const start = useStartRun();
  const ui = useRunUi();
  const [open, setOpen] = useState(false);
  const [, rerender] = useState(0);
  const { run, running, other } = useLabRun(season, week);
  const stream = useRunStream(run != null && (run.launched_from === 'app' || run.has_events), run ? runKey(run) : null);
  const pf = pfq.data;
  const gate = pf && pf.season === season && pf.week === week ? pf.forecastlab : null;
  if (!gate) return null; // pre-flight is about another week (or hasn't answered): no card

  const plan = gate.steps;
  const fold = foldUpdate(plan, run ? stream.events : []);
  const due = plan.filter((s) => s.due);
  const finished = run != null && !running;
  const result = finished ? labResult(run) : null;
  const starter = run ? starterOf(run, ui.launched) : null;

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
      { kind: LAB_KIND, expect_week: pf.week },
      {
        onSuccess: (r) => {
          ui.launched.add(r.run_id);
          ui.toast({ title: `Week ${r.week}: availability and outcomes update started`, text: r.command });
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

  // Nothing due, nothing running, no result to show: one quiet line with the gate's reason ("Everything
  // is up to date.", "Nothing to do until the models are promoted.", ...), the steps behind a disclosure
  if (!run && !other && !gate.allowed && plan.every((s) => !s.due)) {
    const upToDate = gate.reason === UP_TO_DATE;
    return (
      <div className="flquiet" role="status" aria-label="Availability and outcomes update">
        <span className={upToDate ? 'ok' : 'muted'} aria-hidden="true">
          {upToDate ? '✓' : 'i'}
        </span>
        <span>
          <b>{gate.reason ?? UP_TO_DATE}</b>{' '}
          <span className="muted">The app refreshes these after Run week {week} and the injury update.</span>
        </span>
        {plan.length ? (
          <details>
            <summary>What it checked</summary>
            <StepList steps={foldUpdate(plan, []).steps} compact />
          </details>
        ) : null}
      </div>
    );
  }

  // during a run, and after it while its result shows, the steps are the run's; otherwise the plan's
  const steps = run && fold.started ? fold.steps : foldUpdate(plan, []).steps;
  const tail = fold.log.slice(-5);

  return (
    <Card className={`pcupd flupd${running ? ' live' : ''}`}>
      <CardHeader
        title={UPDATE_LABEL}
        sub={
          running && starter
            ? `${STARTER_TEXT[starter]} · its steps and last lines follow the run · closing this tab doesn't stop it`
            : `availability, the outcome curves, last week's grades · the app runs these by itself after Run week ${week} and the injury update`
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
                {UPDATE_LABEL}
              </button>
              {reason ? <span className="reason">{reason}</span> : null}
            </span>
          )
        }
      />
      <div className="card-b">
        {run && result ? (
          <Notice tone={result.tone === 'err' ? 'err' : result.tone === 'warn' ? 'accent' : undefined} icon={result.tone === 'ok' ? '✓' : '!'}>
            <b>{result.title}.</b> {starter && starter !== 'here' ? `${STARTER_TEXT[starter]}. ` : ''}
            {run.message ?? ''}
            {run.status === 'ok' ? ' The tab below is refreshed.' : ''}
            {result.tone === 'err'
              ? ` The published week isn't touched. Fix it and press ${UPDATE_LABEL}: it runs only what's still due.`
              : ''}{' '}
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
            Runs <span className="mono">{gate.command}</span> · holds the weekly lock while it runs · only the steps marked &quot;will run&quot; ·
            never touches the published week
          </div>
        ) : null}
      </div>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        eyebrow="Confirm the update"
        title={`Update week ${week}'s availability and outcomes?`}
        confirmLabel={UPDATE_LABEL}
        onConfirm={confirm}
      >
        <span>Runs the steps that are due, in order. Each one stands alone: a failed step lets the rest run.</span>
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
          It holds the weekly lock while it runs: Run week {week} and the injury update wait for it. It never touches the published week,
          and its log stays on the Availability and Outcomes tabs, not the Pipeline tab.
        </span>
      </ConfirmDialog>
    </Card>
  );
}
