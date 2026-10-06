// The pre-flight panel with the Run button (mockup: preflight / confirmRun). PipelineTab renders
// it on the week pre-flight is about. The server decides the week and the command; the button
// only sends a kind and the server's own week back as a cross-check (README §5.3).

import { useId, useState } from 'react';
import { usePreflight, useRunCurrent, useStartRun } from '../../api/client';
import type { CheckState, PreflightCheck } from '../../api/types';
import { fmtET } from '../../lib/format';
import { Chip } from '../ui';
import { askNotifyOnce } from './notify';
import { RunConfirm } from './RunConfirm';
import { startErrorToast, useRunUi } from './runUi';
import './run.css';

const MARK: Record<CheckState, [string, string]> = {
  ok: ['ok', '✓'],
  warn: ['warn', '!'],
  fail: ['err', '✕'],
};

function Check({ c }: { c: PreflightCheck }) {
  const [cls, mark] = MARK[c.status] ?? ['warn', '?'];
  return (
    <li>
      <span className={`st ${cls}`} aria-label={c.status}>
        {mark}
      </span>
      <span>{c.label}</span>
      <span className="v">{c.value}</span>
      {c.status !== 'ok' && c.reason ? <span className="why">{c.reason}</span> : null}
    </li>
  );
}

export function PreflightPanel({ season, week }: { season: number; week: number }) {
  const pfq = usePreflight();
  const cur = useRunCurrent();
  const start = useStartRun();
  const ui = useRunUi();
  const [open, setOpen] = useState(false);
  const [checking, setChecking] = useState(false);
  const reasonId = useId();
  const pf = pfq.data;
  if (!pf || pf.season !== season || pf.week !== week) return null;

  const rehearsal = pf.mode === 'rehearsal';
  const running = cur.data?.state === 'running' ? cur.data.run : null;
  const blocked = !pf.run.allowed;
  const reason = pf.run.reason ?? 'Pre-flight says the run can’t start yet.';

  const recheck = () => {
    setChecking(true);
    void pfq.refetch().finally(() => setChecking(false));
  };
  const onRun = () => {
    askNotifyOnce();
    setOpen(true);
  };
  const confirm = () => {
    start.mutate(
      // the server's week, never the page's (the server re-checks it)
      { kind: 'weekly', expect_week: pf.week as number },
      {
        onSuccess: (r) => {
          ui.launched.add(r.run_id);
          ui.toast({
            title: `Week ${r.week} ${r.kind === 'rehearsal' ? 'rehearsal ' : ''}started`,
            text: `${r.command}. Closing this tab doesn't stop it.`,
          });
        },
        onError: (e) => {
          ui.toast(startErrorToast(e));
          void pfq.refetch();
        },
      },
    );
  };

  return (
    <section className="card runpanel" aria-label="Pre-flight">
      <div>
        <div className="sec-h">
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 22 }}>Pre-flight</h2>
          <p>
            Checked when the page opens and again before the run starts
            <span className="checked">
              {' '}
              ·{' '}
              {checking
                ? 'checking…'
                : `checked ${fmtET(pf.checked_at, { hour: 'numeric', minute: '2-digit' })} ET`}
            </span>
          </p>
        </div>
        <ul className="checks">
          {pf.checks.map((c) => (
            <Check key={c.id} c={c} />
          ))}
        </ul>
      </div>
      <div>
        <span className="eyebrow">{rehearsal ? 'Rehearsal' : 'Tuesday run'}</span>
        {running ? (
          <>
            <Chip tone="run" pulse>
              Run in progress
            </Chip>
            <div className="reason">
              <span>
                {running.command}
                {running.started
                  ? ` · started ${fmtET(running.started, { hour: 'numeric', minute: '2-digit' })} ET`
                  : ''}
                . The views below follow it live; closing this tab doesn&apos;t stop it.
              </span>
            </div>
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn primary big"
              disabled={blocked || start.isPending}
              aria-disabled={blocked || start.isPending}
              title={blocked ? reason : undefined}
              aria-describedby={blocked ? reasonId : undefined}
              onClick={onRun}
            >
              {start.isPending ? 'Starting…' : `${rehearsal ? 'Rehearse' : 'Run'} week ${pf.week}`}
            </button>
            {blocked ? (
              <div className="reason">
                <span aria-hidden="true">⏳</span>
                <span id={reasonId}>
                  {reason}
                  {pf.run.retry_after
                    ? ` Run opens again at ${fmtET(pf.run.retry_after, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET.`
                    : ''}
                </span>
              </div>
            ) : (
              <div className="reason">
                {rehearsal
                  ? 'The rehearsed week is the newest published one. A rehearsal takes a few minutes: the player refits dominate, and the digest uses the templates.'
                  : 'The week comes from the calendar, never from a picker. Most of the time goes to the GLM writing the digest: about 10–40 minutes in all.'}
              </div>
            )}
            <div className="actions">
              <button
                type="button"
                className="btn sm"
                onClick={recheck}
                disabled={checking}
                aria-disabled={checking}
              >
                {checking ? 'Checking…' : 'Check again'}
              </button>
            </div>
          </>
        )}
        {pf.run.command ? <div className="cmd">{pf.run.command}</div> : null}
        <div className="reason">
          {rehearsal ? (
            <span>Rehearses week {pf.week} into a scratch folder; nothing live is touched.</span>
          ) : (
            <span>
              Publishes the digest and moves W&amp;B&apos;s <b>production</b> alias. Closing this
              tab doesn&apos;t stop a run.
            </span>
          )}
        </div>
      </div>
      <RunConfirm pf={pf} open={open} onOpenChange={setOpen} onConfirm={confirm} />
    </section>
  );
}
