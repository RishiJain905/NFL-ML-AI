// MLOps → W&B runs (mockup: mlWandb): this week's runs, the season-dashboard card, and one card
// per main chart, redrawn here from the local files the run wrote and linked to the W&B run that
// logged it. W&B is read on the server and cached; when it can't be reached the banner says so,
// the runs list falls back to the links in the steps' log lines, and the charts still draw.

import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { mlopsPath, refreshWandb, useMlopsWandb } from '../../../api/client';
import type { ChartCardMeta, MlopsWandbResponse, WandbRun } from '../../../api/types';
import { Dumbbell } from '../../../charts/Dumbbell';
import { HBars } from '../../../charts/HBars';
import { Card, CardHeader, Chip, Notice } from '../../../components/ui';
import { WandbBanner } from '../../../components/WandbBanner';
import { comma, fmtET, signed } from '../../../lib/format';
import { TabError, TabLoading } from '../WeekEmpty';
import { EmptyBlock, ExtLink, StatusChip } from './parts';
import { JOB_LABEL, groupDrift, projectBase, statusWord } from './util';

type Wandb = MlopsWandbResponse;

/** The response's project URL, or the one its run links imply (it can come back null). */
function projectOf(d: Wandb): string | null {
  return projectBase(d.wandb.project_url, [
    ...d.runs.map((r) => r.url),
    ...d.local_links.map((l) => l.url),
    ...Object.values(d.cards).map((c) => c.url),
  ]);
}

const secs = (s: number) => `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;

// ---- runs this week -------------------------------------------------------------------------

function RunState({ state }: { state: string }) {
  if (state === 'finished') return null;
  const tone = state === 'running' ? 'run' : /crash|fail/.test(state) ? 'err' : 'ghost';
  return (
    <span className="state">
      <Chip tone={tone} pulse={state === 'running'} icon={tone === 'err' ? '✕' : undefined}>
        {state}
      </Chip>
    </span>
  );
}

function RunItem({ r, rerun }: { r: WandbRun; rerun?: boolean }) {
  return (
    <li className={rerun ? 'rerun' : undefined}>
      <span className="job">{JOB_LABEL[r.job] ?? r.job}</span>
      <span>
        <b>{r.name}</b> <span className="muted">{r.group} / {r.job_type}</span>
        <RunState state={r.state} />
        {rerun ? (
          <>
            {' '}
            <Chip>re-run</Chip>
          </>
        ) : null}
      </span>
      <ExtLink href={r.url} label={`${r.name} (${r.id}) in W&B`}>
        {r.id} ↗
      </ExtLink>
    </li>
  );
}

function RunsCard({ d, onRefresh, refreshing }: { d: Wandb; onRefresh: () => void; refreshing: boolean }) {
  const [showEarlier, setShowEarlier] = useState(false);
  const project = projectOf(d);
  const current = d.runs.filter((r) => r.current);
  const earlier = d.runs.filter((r) => !r.current);
  const fetched = d.wandb.fetched_at ? fmtET(d.wandb.fetched_at, { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : null;
  return (
    <Card>
      <CardHeader
        title="Runs this week"
        sub={fetched && d.wandb.available ? `from W&B · ${fetched} ET` : undefined}
        right={
          <>
            {/* the banner has its own "Try again" while W&B can't be reached */}
            {d.wandb.available && !d.wandb.stale ? (
              <button type="button" className="btn sm" onClick={onRefresh} disabled={refreshing} title="Ask W&B again, skipping the server's cache">
                {refreshing ? 'Refreshing…' : 'Refresh'}
              </button>
            ) : null}
            {project ? (
              <ExtLink className="btn sm" href={project}>
                project ↗
              </ExtLink>
            ) : null}
          </>
        }
      />
      <div className="card-b">
        {d.runs.length ? (
          <>
            <ul className={`runs${showEarlier && earlier.length > 6 ? ' ml-scroll' : ''}`} aria-label="W&B runs this week">
              {current.map((r) => (
                <RunItem key={r.id} r={r} />
              ))}
              {showEarlier ? earlier.map((r) => <RunItem key={r.id} r={r} rerun />) : null}
            </ul>
            {earlier.length ? (
              <div className="wk-more" style={{ borderTop: 0, paddingBottom: 0 }}>
                <button type="button" className="btn sm" aria-expanded={showEarlier} onClick={() => setShowEarlier(!showEarlier)}>
                  {showEarlier ? 'Hide earlier runs' : `Show ${earlier.length} earlier ${earlier.length === 1 ? 'run' : 'runs'} (re-runs)`}
                </button>
              </div>
            ) : null}
          </>
        ) : d.local_links.length ? (
          <>
            <ul className="runs" aria-label="W&B run links from the steps' logs">
              {d.local_links.map((l) => (
                <li key={`${l.job}-${l.run_id}`}>
                  <span className="job">{JOB_LABEL[l.job] ?? l.job}</span>
                  <span className="muted">from the step&apos;s log line</span>
                  <ExtLink href={l.url}>{l.run_id} ↗</ExtLink>
                </li>
              ))}
            </ul>
            <p className="ml-note">
              W&amp;B didn&apos;t send its run list, so these are the run links the steps wrote in their own logs.
            </p>
          </>
        ) : (
          <span className="muted">No W&amp;B runs for this week yet. They appear as the run&apos;s steps finish.</span>
        )}
      </div>
    </Card>
  );
}

function DashboardCard({ d }: { d: Wandb }) {
  const db = d.dashboard;
  const project = projectOf(d);
  const runLink = (id: string) => d.runs.find((r) => r.id === id)?.url ?? (project ? `${project}/runs/${id}` : null);
  return (
    <Card>
      <CardHeader
        title="Season dashboard"
        right={
          db.url ? (
            <ExtLink className="btn sm" href={db.url} title={db.title ?? undefined}>
              report ↗
            </ExtLink>
          ) : undefined
        }
      />
      <div className="card-b ml-stack">
        <p style={{ margin: 0 }}>
          The W&amp;B Report reads the newest <span className="mono">season-dashboard</span> run (tag <span className="mono">dashboard-current</span>)
          {db.current_run_id ? (
            <>
              , now{' '}
              <ExtLink className="mono" href={runLink(db.current_run_id)}>
                {db.current_run_id}
              </ExtLink>
              {db.current_run_week != null ? ` (week ${db.current_run_week})` : ''}
            </>
          ) : null}
          . Its charts are redrawn on <Link to={`/season/${d.season}/scorecard`}>Season → Scorecard</Link>.
        </p>
        {db.week_run_id ? (
          <p className="ml-note tight">
            This week&apos;s dashboard run:{' '}
            <ExtLink className="mono" href={runLink(db.week_run_id)}>
              {db.week_run_id} ↗
            </ExtLink>
          </p>
        ) : (
          <p className="ml-note tight">
            {d.wandb.available ? 'This week has no dashboard run of its own yet.' : "The dashboard run ids come from W&B, which isn't available right now."}
          </p>
        )}
      </div>
    </Card>
  );
}

// ---- chart cards ----------------------------------------------------------------------------

function ChartCard({
  title,
  meta,
  projectUrl,
  empty,
  emptyGlyph,
  emptyText,
  children,
}: {
  title: string;
  meta: ChartCardMeta;
  projectUrl: string | null;
  empty: boolean;
  emptyGlyph: string;
  emptyText: string;
  children: ReactNode;
}) {
  const href = meta.url ?? (meta.run_id && projectUrl ? `${projectUrl}/runs/${meta.run_id}` : null);
  return (
    <Card>
      <CardHeader
        title={title}
        sub={<span className="mono ml-meta">{meta.run_name} · {meta.metrics}</span>}
        right={
          meta.run_id ? (
            href ? (
              <ExtLink className="btn sm" href={href} label={`${meta.run_name} (${meta.run_id}) in W&B`}>
                {meta.run_id} ↗
              </ExtLink>
            ) : (
              <Chip tone="ghost">{meta.run_id}</Chip>
            )
          ) : undefined
        }
      />
      <div className="card-b">
        {empty ? (
          <EmptyBlock glyph={emptyGlyph}>{meta.note ?? emptyText}</EmptyBlock>
        ) : (
          <>
            {children}
            {meta.note ? <p className="ml-note">{meta.note}</p> : null}
          </>
        )}
        <p className="ml-source">
          Drawn from <span className="mono">{meta.source}</span>
        </p>
      </div>
    </Card>
  );
}

function GameFit({ d }: { d: Wandb }) {
  const c = d.cards.game_fit;
  const both = c.games.filter((g) => g.p_model_only != null && g.p_market != null);
  return (
    <ChartCard
      title="Game fit: model only vs market"
      meta={c}
      projectUrl={projectOf(d)}
      empty={both.length === 0}
      emptyGlyph="GM"
      emptyText="No game has both a model-only chance and a market line yet."
    >
      <Dumbbell games={c.games} label={`Model-only versus market home win chance for each week-${d.week} game`} />
      {both.length < c.games.length ? (
        <p className="ml-note">{c.games.length - both.length} game(s) have no market line yet and are left out.</p>
      ) : null}
    </ChartCard>
  );
}

function Scoreboard({ d }: { d: Wandb }) {
  const c = d.cards.player_scoreboard;
  const scored = c.groups.flatMap((g) => (g.improvement == null ? [] : [{ ...g, improvement: g.improvement }])).sort((a, b) => b.improvement - a.improvement);
  const unscored = c.groups.filter((g) => g.improvement == null);
  const lead =
    c.mode === 'live'
      ? `Graded by this run: week ${c.scored_week ?? '—'}'s results, live. MAE improvement over the rolling baseline, by position group.`
      : c.mode === 'backtest'
        ? `${c.scored_week != null ? `Week ${c.scored_week}'s walk-forward rows` : 'Walk-forward rows'}${c.note ? '' : ' (no live week has been graded yet)'}. MAE improvement over the rolling baseline, by position group.`
        : 'MAE improvement over the rolling baseline, by position group.';
  return (
    <ChartCard
      title="Player scoreboard: vs the baseline"
      meta={c}
      projectUrl={projectOf(d)}
      empty={scored.length === 0}
      emptyGlyph="PL"
      emptyText="No player group has been scored against its baseline yet."
    >
      <p className="ml-note tight" style={{ marginBottom: 10 }}>
        {lead}
      </p>
      <HBars
        label="Player model improvement over the baseline, by group"
        color="var(--s1)"
        format={(v) => `${signed(v, 1)}%`}
        rows={scored.map((g) => ({
          label: g.group,
          value: g.improvement,
          tip: `${g.group}\n${Math.abs(g.improvement).toFixed(1)}% ${g.improvement < 0 ? 'worse' : 'better'} than the baseline\n${comma(g.scored)} projections scored`,
        }))}
      />
      {unscored.length ? <p className="ml-note">Not scored yet: {unscored.map((g) => g.group).join(', ')}.</p> : null}
    </ChartCard>
  );
}

function PlayerFit({ d }: { d: Wandb }) {
  const c = d.cards.player_fit;
  const seen = new Map<string, number>();
  const rows = [...c.stats]
    .sort((a, b) => b.count - a.count)
    .map((s) => {
      const base = `${s.label} · ${s.group}`;
      const n = (seen.get(base) ?? 0) + 1;
      seen.set(base, n);
      return { label: n > 1 ? `${base} (${s.target})` : base, value: s.count };
    });
  const total = rows.reduce((a, r) => a + r.value, 0);
  return (
    <ChartCard
      title="Player fit: projections per stat"
      meta={c}
      projectUrl={projectOf(d)}
      empty={rows.length === 0}
      emptyGlyph="PL"
      emptyText="The player run hasn't logged its projections per stat."
    >
      <HBars
        label="Projections per stat"
        color="var(--s2)"
        format={(v) => comma(v)}
        rows={rows.map((r) => ({ ...r, tip: `${r.label}\n${comma(r.value)} projections` }))}
      />
      <p className="ml-note">
        {comma(total)} projections across {rows.length} stats.
      </p>
    </ChartCard>
  );
}

function GraphBuild({ d }: { d: Wandb }) {
  const c = d.cards.graph_build;
  const stages = [...c.stages].sort((a, b) => b.seconds - a.seconds);
  const parts = [
    c.nodes != null ? `${comma(c.nodes)} nodes` : null,
    c.relationships != null ? `${comma(c.relationships)} relationships` : null,
    c.mismatches != null ? `${c.mismatches} count mismatches` : null,
    c.total_seconds != null ? `${Math.round(c.total_seconds)} s in all` : null,
  ].filter(Boolean);
  return (
    <ChartCard
      title="Graph build: time by stage"
      meta={c}
      projectUrl={projectOf(d)}
      empty={stages.length === 0}
      emptyGlyph="KG"
      emptyText="The graph build hasn't logged its stage times."
    >
      <HBars
        label="Graph build seconds by stage"
        color="var(--s3)"
        format={secs}
        rows={stages.map((s) => ({ label: s.stage, value: s.seconds, tip: `${s.stage}\n${secs(s.seconds)}` }))}
      />
      {parts.length ? <p className="ml-note">{parts.join(', ')}.</p> : null}
    </ChartCard>
  );
}

function DigestCard({ d }: { d: Wandb }) {
  const c = d.cards.digest;
  const words = [...c.words].sort((a, b) => b.words - a.words);
  return (
    <ChartCard
      title="Digest: words per section"
      meta={c}
      projectUrl={projectOf(d)}
      empty={words.length === 0}
      emptyGlyph="MD"
      emptyText="The digest run hasn't logged its words per section."
    >
      <HBars
        label="Words per section"
        color="var(--s1)"
        format={(v) => comma(v)}
        rows={words.map((w) => ({
          label: w.section.replace(/_/g, ' '),
          value: w.words,
          tip: [
            w.section.replace(/_/g, ' '),
            `${comma(w.words)} words`,
            ...(w.budget != null ? [`budget ${comma(w.budget)}${w.words > w.budget ? ` (over by ${comma(w.words - w.budget)})` : ''}`] : []),
          ].join('\n'),
        }))}
      />
      {c.checks.length ? (
        <div className="ml-chips">
          <span className="lead">Check issues:</span>
          {c.checks.map((k) => (
            <Chip key={k.name} tone={k.issues === 0 ? 'ok' : k.level === 'fail' ? 'err' : 'warn'} icon={k.issues === 0 ? '✓' : k.level === 'fail' ? '✕' : '!'} title={`${k.name} (${k.level})`}>
              {k.name} {k.issues}
            </Chip>
          ))}
        </div>
      ) : null}
    </ChartCard>
  );
}

function PipelineCard({ d }: { d: Wandb }) {
  const c = d.cards.pipeline;
  const steps = c.steps.flatMap((s) => (s.seconds == null ? [] : [{ ...s, seconds: s.seconds }]));
  const drift = groupDrift(c.drift);
  return (
    <ChartCard
      title="Pipeline run: time per step"
      meta={c}
      projectUrl={projectOf(d)}
      empty={steps.length === 0}
      emptyGlyph="PR"
      emptyText="No pipeline run was recorded for this week."
    >
      <HBars
        label="Pipeline seconds per step"
        color="var(--s2)"
        format={(v) => secs(v)}
        rows={steps.map((s) => ({
          label: s.status === 'ok' ? s.step : `${s.step} · ${statusWord(s.status)}`,
          value: s.seconds,
          tip: `${s.step}\n${secs(s.seconds)}${s.status === 'ok' ? '' : ` · ${statusWord(s.status)}`}`,
        }))}
      />
      {c.stale_sources != null ? (
        <div className="ml-chips">
          <span className="lead">Data freshness:</span>
          {c.stale_sources === 0 ? (
            <Chip tone="ok" icon="✓">no stale sources</Chip>
          ) : (
            <Chip tone="warn" icon="!">{c.stale_sources} stale {c.stale_sources === 1 ? 'source' : 'sources'}</Chip>
          )}
        </div>
      ) : null}
      {drift.length ? (
        <div className="ml-chips" role="list" aria-label="Drift signals">
          <span className="lead">Drift:</span>
          {drift.map((g) => (
            <span key={g.name} role="listitem" className="chip-pair" title={`${g.name}: ${statusWord(g.status)}`}>
              <span className="mono">{g.name}</span> <StatusChip status={g.status} />
            </span>
          ))}
        </div>
      ) : null}
    </ChartCard>
  );
}

// ---- the section ----------------------------------------------------------------------------

export function WandbSection({ season, week }: { season: number; week: number }) {
  const q = useMlopsWandb(season, week);
  if (q.isPending) return <TabLoading />;
  if (q.isError) return <TabError error={q.error} />;
  const d = q.data;
  const refresh = () => {
    refreshWandb(mlopsPath(season, week, 'wandb'));
    void q.refetch();
  };
  return (
    <div className="wk-stack">
      <WandbBanner state={d.wandb} onRefresh={refresh} refreshing={q.isFetching} />
      <div className="grid g2">
        <RunsCard d={d} onRefresh={refresh} refreshing={q.isFetching} />
        <DashboardCard d={d} />
      </div>
      <div className="grid g2">
        <GameFit d={d} />
        <Scoreboard d={d} />
        <PlayerFit d={d} />
        <GraphBuild d={d} />
        <DigestCard d={d} />
        <PipelineCard d={d} />
      </div>
      {!d.wandb.available ? (
        <Notice>
          The charts above are drawn from this week&apos;s local files, so they are complete without W&amp;B; only the run list, links and the dashboard
          card need it.
        </Notice>
      ) : null}
    </div>
  );
}
