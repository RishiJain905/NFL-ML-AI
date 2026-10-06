// The Pipeline tab (mockup: w4Pipeline / week5Pipeline / pipelineBlock): tiles and notices for
// the run, the run (or the plan) in the chosen view, and the log. A run of this week that is
// going (CR02) is drawn live from its event stream (LiveRun); the current week before its run
// shows the pre-flight panel above the plan; a finished or failed week is read from its files.

import { useCallback, useState, type ReactNode } from 'react';
import { useMeta, usePipeline, usePreflight, useRunCurrent } from '../../api/client';
import type { LogLine, PipelineResponse, WeekDetail } from '../../api/types';
import { LiveRun } from '../../components/pipeline/LiveRun';
import { runStreamKey } from '../../components/pipeline/live';
import { LogPanel } from '../../components/pipeline/LogPanel';
import { toModel } from '../../components/pipeline/model';
import { PipelineView } from '../../components/pipeline/PipelineView';
import { useViewChoice, ViewPicker } from '../../components/pipeline/ViewPicker';
import { VIEWS } from '../../components/pipeline/views';
import { FailureBanner } from '../../components/run/FailureBanner';
import { PreflightPanel } from '../../components/run/PreflightPanel';
import { Card, CardHeader, EmptyState, Notice, Tile } from '../../components/ui';
import { duration, fmtET } from '../../lib/format';
import { TabError, TabLoading } from './WeekEmpty';

const SITTINGS = ['', 'one sitting', 'two sittings', 'three sittings', 'four sittings'];

function Tiles({ p, detail }: { p: PipelineResponse; detail: WeekDetail | undefined }) {
  const ran = p.steps.filter((s) => s.step !== 'records' && s.status !== 'none' && s.status !== 'pending');
  const ok = ran.filter((s) => s.status === 'ok' || s.status === 'degraded').length;
  const t = p.tiles;
  const resumes = Math.max(0, p.sittings - 1);
  const published = p.state === 'finished';
  return (
    <div className="tiles">
      <Tile
        k="Status"
        v={
          <span style={{ color: published ? 'var(--ok)' : p.state === 'failed' ? 'var(--err)' : undefined }}>
            {published ? 'Published' : p.state === 'failed' ? 'Failed' : p.state === 'running' ? 'Running' : 'Stopped'}
          </span>
        }
        d={
          detail?.published_at
            ? `${fmtET(detail.published_at, { weekday: 'short', month: 'short', day: 'numeric' })}, ${fmtET(detail.published_at, { hour: 'numeric', minute: '2-digit' })} ET`
            : p.failed_step
              ? `at the ${p.failed_step} step`
              : undefined
        }
      />
      <Tile
        k="Steps"
        v={
          <>
            {ok}/{ran.length} <small>ok</small>
          </>
        }
        d={p.sittings > 1 ? `run in ${SITTINGS[p.sittings] ?? `${p.sittings} sittings`} (${resumes} resume${resumes === 1 ? '' : 's'})` : 'one sitting'}
      />
      <Tile
        k="Time in steps"
        v={duration(p.step_seconds)}
        d={t.digest_share != null ? `digest ${Math.round(t.digest_share * 100)}% of it` : undefined}
      />
      {t.checks_total != null ? (
        <Tile
          k="Digest checks"
          v={`${t.checks_passed}/${t.checks_total}`}
          d={t.first_time ? 'passed first time' : t.first_time === false ? 'after one regeneration' : undefined}
        />
      ) : null}
      {t.llm_cost != null ? (
        <Tile
          k="LLM cost"
          v={`$${t.llm_cost.toFixed(3)}`}
          d={`${t.llm_calls} call${t.llm_calls === 1 ? '' : 's'}${t.llm_provider ? ` · ${t.llm_provider}` : ''}`}
        />
      ) : null}
    </div>
  );
}

function RunNotice({ p }: { p: PipelineResponse }) {
  if (p.state === 'failed') {
    const step = p.steps.find((s) => s.step === p.failed_step);
    return <FailureBanner season={p.season} week={p.week} failedStep={p.failed_step} detail={step?.detail ?? null} error={p.error} />;
  }
  if (p.state === 'partial') {
    return (
      <Notice icon="!">
        <b>The last run stopped before the digest.</b> Nothing was published for this week.
      </Notice>
    );
  }
  if (p.state === 'running') {
    // a run holds the lock but the app has no event stream to follow (a terminal run that hasn't written events yet)
    return (
      <Notice tone="accent" icon="●">
        <b>A run of this week is in progress.</b> The steps below are as its records stand; the live view starts when the
        run&apos;s events appear. Closing this tab doesn&apos;t stop a run.
      </Notice>
    );
  }
  if (p.no_run_records) {
    return (
      <Notice>
        Week {p.week} ran before P07 added run records, so it has no <code>run_summary.json</code> and no W&amp;B
        pipeline run. The steps and times below come from <code>weekly_run.json</code>. From week 5 every run has the
        full record.
      </Notice>
    );
  }
  return null;
}

/** The week as its files have it (CR01): tiles, the run or the plan, and the log. */
function Archive({
  p,
  detail,
  choice,
  dryRun,
}: {
  p: PipelineResponse;
  detail: WeekDetail | undefined;
  choice: ReturnType<typeof useViewChoice>;
  /** the calendar's dry run for the plan (pre-flight), when it has come back */
  dryRun: LogLine[] | null;
}) {
  const { season, week } = p;
  if (p.state === 'none') {
    return (
      <EmptyState glyph="—" title={`No run for week ${week}`}>
        There&apos;s no <code>weekly_run.json</code> for {season} week {week}: the weekly runs started later this season.
      </EmptyState>
    );
  }
  const plan = p.state === 'plan';
  const model = toModel(p);
  const viewLabel = VIEWS.find((v) => v.kind === choice.view)?.label ?? '';
  const timesFrom = plan
    ? p.expected_from
      ? `expected times from week ${p.expected_from.week}'s run`
      : 'expected times (estimates until a week has run)'
    : `real step times from ${p.source === 'run_summary' ? 'run_summary.json' : 'weekly_run.json'}`;

  return (
    <>
      {plan ? (
        <Notice tone="accent">
          <b>Week {week} hasn&apos;t run yet.</b> Below is the plan, with each step&apos;s expected time.
        </Notice>
      ) : (
        <>
          <Tiles p={p} detail={detail} />
          <RunNotice p={p} />
        </>
      )}
      <Card>
        <CardHeader
          title={plan ? 'The plan for this run' : 'The run'}
          sub={`${viewLabel} · ${timesFrom}`}
          right={<ViewPicker season={season} week={week} value={choice.view} surprised={choice.surprised} onChoose={choice.choose} />}
        />
        <div className="card-b">
          <PipelineView model={model} view={choice.view} sittings={p.sittings} />
        </div>
      </Card>
      {plan && dryRun ? <LogPanel lines={dryRun} source="dryrun" /> : <LogPanel lines={p.log} source={p.log_source} />}
    </>
  );
}

export function PipelineTab({ season, week, detail }: { season: number; week: number; detail?: WeekDetail }) {
  const q = usePipeline(season, week);
  const choice = useViewChoice(season, week);
  const cur = useRunCurrent();
  const meta = useMeta();
  const p = q.data;
  const rehearsalMode = Boolean(meta.data?.mode.rehearsal);

  // ---- which run is this page about
  const run = cur.data?.run ?? null;
  const mine = run != null && run.season === season && run.week === week && run.kind !== 'injury_update';
  const running = mine && cur.data?.state === 'running';
  // a terminal run that hasn't written events has nothing to stream yet: the archive shows it as it stands
  const streamable = run != null && (run.launched_from === 'app' || run.has_events);
  const key = run ? runStreamKey(run) : '';

  // The run this page followed to its end stays on screen until the week's records show its result
  // (LiveRun says when), so the page never flashes back to the plan in between.
  const [follow, setFollow] = useState<{ key: string; released: boolean } | null>(null);
  if (running && streamable && follow?.key !== key) setFollow({ key, released: false });
  const holding = mine && follow != null && !follow.released && follow.key === key;
  const settle = useCallback(() => setFollow((f) => (f && !f.released ? { ...f, released: true } : f)), []);
  const liveMode = (running && streamable) || holding;
  // rehearsal mode: a finished rehearsal of this week is shown from its recorded events (its files
  // are in the rehearsal folder, not the archive)
  const replay =
    Boolean(!liveMode && rehearsalMode && mine && cur.data?.state === 'finished' && run?.kind === 'rehearsal' && run.has_events);

  // the pre-flight panel: the current week before its run (or stopped before the digest), and
  // whatever the rehearsal is about in rehearsal mode
  const showPreflight = rehearsalMode || p?.state === 'plan' || p?.state === 'partial';
  const preflight = usePreflight(showPreflight);
  const pf = preflight.data;
  const dryRun = pf && pf.season === season && pf.week === week && pf.dry_run_log.length > 0 ? pf.dry_run_log : null;

  let archive: ReactNode = null;
  if (!liveMode) {
    if (q.isLoading) archive = <TabLoading />;
    else if (q.isError) archive = <TabError error={q.error as Error} />;
    else if (p) archive = <Archive p={p} detail={detail} choice={choice} dryRun={dryRun} />;
    else archive = <TabLoading />;
  }

  return (
    <>
      {showPreflight ? <PreflightPanel season={season} week={week} /> : null}
      {run && (liveMode || replay) ? (
        <LiveRun
          key={key}
          season={season}
          week={week}
          info={run}
          base={p ?? null}
          choice={choice}
          replay={replay}
          onSettled={settle}
        />
      ) : null}
      {archive}
    </>
  );
}
