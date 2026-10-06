// The Graph tab (mockup: w4Graph): the build's counts and time, the insights the digest used
// (section, query, strength, confidence), rows per query, what was found but not used and why,
// nodes by label, GDS, and a link to Neo4j Browser on this machine.

import { useState } from 'react';
import { useGraph } from '../../api/client';
import type { GraphResponse, Insight } from '../../api/types';
import { HBars } from '../../charts/HBars';
import { Card, CardHeader, Chip, Notice, Tile } from '../../components/ui';
import { comma, duration } from '../../lib/format';
import { TabError, TabLoading, WeekEmpty } from './WeekEmpty';
import { SECTION_LABEL, queryLabel } from './weekUtil';
import './week.css';

const FIRST_UNUSED = 15;

const secs = (s: number) => `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;

function InsightRow({ s }: { s: Insight }) {
  return (
    <div className="insight">
      <div className="ih">
        <span className="im">{s.matchup ?? 'League-wide'}</span>
        <Chip tone="run">{SECTION_LABEL[s.section] ?? s.section}</Chip>
        <span className="chip flat mono">{s.query}</span>
        <span className="meter">
          strength{' '}
          <i aria-hidden="true">
            <b style={{ width: `${Math.max(0, Math.min(1, s.strength)) * 100}%` }} />
          </i>{' '}
          {s.strength.toFixed(2)}
        </span>
        <Chip>{s.confidence} confidence</Chip>
      </div>
      <p>{s.brief}</p>
      {s.note ? (
        <p className="muted" style={{ fontSize: 12.5 }}>
          {s.note}
        </p>
      ) : null}
    </div>
  );
}

function NotUsed({ g }: { g: GraphResponse }) {
  const [all, setAll] = useState(false);
  if (!g.not_used.length) return null;
  const rows = all ? g.not_used : g.not_used.slice(0, FIRST_UNUSED);
  return (
    <Card>
      <CardHeader title="Found but not used" sub="the next strongest candidates · the digest has room for a few" />
      <div className="tablewrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Game</th>
              <th>Kind</th>
              <th>Query</th>
              <th className="r">Strength</th>
              <th>Why not</th>
              <th>Finding</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id}>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <b>{c.matchup ?? 'League-wide'}</b>
                </td>
                <td>{c.type.replace(/_/g, ' ')}</td>
                <td className="mono">{c.query}</td>
                <td className="r num">{c.strength.toFixed(2)}</td>
                <td className="dim">{c.reason}</td>
                <td style={{ minWidth: 320 }}>{c.brief}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {g.not_used.length > FIRST_UNUSED ? (
        <div className="wk-more">
          <button type="button" className="btn sm" onClick={() => setAll(!all)}>
            {all ? `Show the first ${FIRST_UNUSED}` : `Show all ${g.not_used.length}`}
          </button>
        </div>
      ) : null}
    </Card>
  );
}

function GraphView({ g }: { g: GraphResponse }) {
  const stage = (name: string) => g.stages.find((s) => s.stage === name)?.seconds;
  const buildParts = (['wipe', 'load', 'queries'] as const)
    .map((k) => (stage(k) == null ? null : `${k} ${secs(stage(k) as number)}`))
    .filter(Boolean)
    .join(' · ');
  const errors = g.queries.filter((q) => q.error);

  return (
    <div className="wk-stack">
      {g.status !== 'ok' ? (
        <Notice tone="err" icon="✕">
          <b>The graph build {g.status === 'failed' ? 'failed' : `ended as "${g.status}"`}.</b> What it saved is shown
          below; the digest's graph sections may be missing.
        </Notice>
      ) : null}
      <div className="tiles">
        <Tile k="Nodes" v={comma(g.totals.nodes)} d={`${g.nodes_by_label.length} labels`} />
        <Tile k="Relationships" v={comma(g.totals.rels)} d={`${g.totals.mismatches} count mismatches`} />
        <Tile
          k="Build"
          v={
            g.total_seconds == null ? (
              '—'
            ) : (
              <>
                {Math.round(g.total_seconds)}
                <small>s</small>
              </>
            )
          }
          d={buildParts || undefined}
        />
        <Tile
          k="Insights used"
          v={
            <>
              {g.used.length}
              <small>of {g.candidates}</small>
            </>
          }
          d={`${g.skipped.started} skipped: game already started`}
        />
      </div>
      <div className="split">
        <Card>
          <CardHeader
            title="Used in the digest"
            right={
              <a className="btn sm" href={g.browser_url} target="_blank" rel="noopener noreferrer" title="Opens on your machine">
                Neo4j Browser ↗
              </a>
            }
          />
          <div className="card-b">
            {g.used.length ? (
              g.used.map((s) => <InsightRow key={s.id} s={s} />)
            ) : (
              <span className="muted">The digest used no graph insights this week.</span>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Query results" sub="rows per query" />
          <div className="card-b" style={{ display: 'grid', gap: 12 }}>
            <HBars
              label="Rows per query"
              color="var(--s3)"
              rows={g.queries.map((q) => ({
                label: queryLabel(q.name),
                value: q.rows,
                tip: [q.name, `${comma(q.rows)} rows${q.seconds != null ? ` · ${secs(q.seconds)}` : ''}`, ...(q.error ? [`error: ${q.error}`] : [])].join(
                  '\n',
                ),
              }))}
              format={(v) => comma(v)}
            />
            {errors.map((q) => (
              <div key={q.name} style={{ color: 'var(--err)', fontSize: 12.5 }}>
                ✕ <span className="mono">{q.name}</span>: {q.error}
              </div>
            ))}
          </div>
        </Card>
      </div>
      <NotUsed g={g} />
      <Card>
        <CardHeader title="Nodes by label" />
        <div className="card-b">
          <HBars
            label="Nodes by label"
            color="var(--s2)"
            rows={g.nodes_by_label.map((n) => ({ label: n.label, value: n.count, tip: `${n.label}\n${comma(n.count)} nodes` }))}
            format={(v) => comma(v)}
          />
        </div>
      </Card>
      {g.gds ? (
        <div className="foot">
          Graph Data Science: {g.gds.status}
          {g.gds.version ? ` · version ${g.gds.version}` : ''}
          {g.gds.seconds != null ? ` · ${duration(g.gds.seconds)}` : ''}
          {g.gds.jobs.length
            ? ` · ${g.gds.jobs.map((j) => `${j.name} ${j.status}${j.seconds != null ? ` (${secs(j.seconds)})` : ''}`).join(', ')}`
            : ''}
        </div>
      ) : null}
    </div>
  );
}

export function GraphTab({
  season,
  week,
  isCurrent,
  lastPublishedWeek,
}: {
  season: number;
  week: number;
  isCurrent: boolean;
  lastPublishedWeek: number | null;
}) {
  const q = useGraph(season, week);
  if (q.isPending) return <TabLoading />;
  if (q.isError) return <TabError error={q.error} />;
  if (q.data.status === 'none') {
    return <WeekEmpty tab="graph" season={season} week={week} isCurrent={isCurrent} lastPublishedWeek={lastPublishedWeek} />;
  }
  return <GraphView g={q.data} />;
}
