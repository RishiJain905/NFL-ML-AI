// The Pipeline tab (mockup: w4Pipeline / week5Pipeline / pipelineBlock): tiles and notices for
// the run, the run (or the plan) in the chosen view, and the log. CR01 is read-only: the
// pre-flight checks, the Run button and live runs come with CR02.

import { usePipeline } from '../../api/client';
import type { PipelineResponse, WeekDetail } from '../../api/types';
import { LogPanel } from '../../components/pipeline/LogPanel';
import { toModel } from '../../components/pipeline/model';
import { PipelineView } from '../../components/pipeline/PipelineView';
import { useViewChoice, ViewPicker } from '../../components/pipeline/ViewPicker';
import { VIEWS } from '../../components/pipeline/views';
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
    return (
      <Notice tone="err" icon="✕">
        <b>The {p.failed_step} step failed.</b> {step?.detail ?? p.error ?? ''} Steps before it are saved; the digest
        didn't run, so nothing was published. Resuming from the app comes with CR02; from a terminal:{' '}
        <span className="cmd">
          nfl weekly run --season {p.season} --week {p.week} --from-step {p.failed_step}
        </span>
      </Notice>
    );
  }
  if (p.state === 'partial') {
    return (
      <Notice icon="!">
        <b>The last run stopped before the digest.</b> Nothing was published for this week.
      </Notice>
    );
  }
  if (p.state === 'running') {
    return (
      <Notice tone="accent" icon="●">
        <b>A run of this week is in progress.</b> The steps below are as its records stand; the live view comes with
        CR02. Closing this tab doesn't stop a run.
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

export function PipelineTab({ season, week, detail }: { season: number; week: number; detail?: WeekDetail }) {
  const q = usePipeline(season, week);
  const choice = useViewChoice(season, week);
  if (q.isLoading) return <TabLoading />;
  if (q.isError) return <TabError error={q.error as Error} />;
  const p = q.data;
  if (!p) return <TabLoading />;
  if (p.state === 'none') {
    return (
      <EmptyState glyph="—" title={`No run for week ${week}`}>
        There's no <code>weekly_run.json</code> for {season} week {week}: the weekly runs started later this season.
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
          <b>Week {week} hasn't run yet.</b> Below is the plan, with each step's expected time. The pre-flight checks
          and the Run button come with CR02; until then the run starts from a terminal:{' '}
          <span className="cmd">uv run nfl weekly run --auto</span>
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
      <LogPanel lines={p.log} source={p.log_source} live={p.state === 'running'} />
    </>
  );
}
