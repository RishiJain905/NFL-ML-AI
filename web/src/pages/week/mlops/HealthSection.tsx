// MLOps → Health (mockup: mlHealth): did the run go cleanly, and was the data fresh? Tiles, step
// timings, what ran, data freshness, this week vs last week, ingest, quality checks and drift.
// Everything here comes from local files (run_summary.json and friends); no W&B.

import { Fragment } from 'react';
import { Link } from 'react-router-dom';
import { useMlopsHealth } from '../../../api/client';
import type { FreshnessRow, MlopsHealthResponse } from '../../../api/types';
import { HBars } from '../../../charts/HBars';
import { Sparkline } from '../../../charts/Sparkline';
import { TipTarget } from '../../../charts/TipTarget';
import { Card, CardHeader, Chip, EmptyState, Notice, Tile } from '../../../components/ui';
import { comma, duration, fmtET } from '../../../lib/format';
import { TabError, TabLoading, WarnNotice } from '../WeekEmpty';
import { StatusChip } from './parts';
import { deltaText, fmtMeasure, groupDrift, statusWord, unique, valueText, type DriftGroup } from './util';

type Health = MlopsHealthResponse;

// ---- tiles ---------------------------------------------------------------------------------

function statusTone(status: string | null): string {
  if (status === 'ok') return 'ml-ok';
  if (!status) return '';
  return /fail|error|crash/.test(status) ? 'ml-err' : 'ml-warn';
}

function Tiles({ h }: { h: Health }) {
  const { run, data, quality, drift, projections } = h.tiles;
  const runDetail =
    run.steps_total > 0
      ? `${run.steps_ok}/${run.steps_total} steps ok · ${run.degraded} degraded${run.failed_step ? ` · failed at ${run.failed_step}` : ''}`
      : 'no steps recorded';
  const qualityDetail =
    quality.blocking == null
      ? undefined
      : quality.blocking_failed
        ? `${quality.blocking_failed} of ${quality.blocking} blocking failed`
        : `${quality.blocking} blocking, all pass`;
  // counted from the rows, by signal: the tile's own `insufficient` counts rows, `signals` names
  const signals = groupDrift(h.drift);
  const waiting = signals.filter((g) => g.status === 'insufficient_data').length;
  const driftDetail =
    signals.length === 0
      ? drift.signals === 0
        ? 'no signals recorded'
        : `${drift.signals} signals`
      : waiting === signals.length
        ? `${signals.length} signals · not enough weeks yet`
        : `${signals.length} signals${waiting ? ` · ${waiting} need more weeks` : ''}`;
  const written = projections.graph_written;
  return (
    <div className="tiles">
      <Tile
        k="Run status"
        v={<span className={statusTone(run.status)}>{run.status ? statusWord(run.status) : '—'}</span>}
        d={runDetail}
      />
      <Tile
        k="Data"
        v={
          data.datasets == null ? (
            '—'
          ) : (
            <>
              {comma(data.datasets)}
              <small>datasets</small>
            </>
          )
        }
        d={data.datasets == null ? undefined : `${data.failures ?? 0} failures · snapshot ${data.snapshot_date ?? '—'}`}
      />
      <Tile
        k="Quality checks"
        v={quality.total == null ? '—' : `${quality.passed ?? 0}/${quality.total}`}
        d={qualityDetail}
      />
      <Tile k="Drift alerts" v={drift.alerts == null ? '—' : String(drift.alerts)} d={driftDetail} />
      <Tile
        k="Projections"
        v={comma(projections.players)}
        d={
          projections.players == null
            ? undefined
            : [
                projections.teams != null ? `${comma(projections.teams)} team rows` : null,
                written == null ? null : written === projections.players ? 'all written to the graph' : `${comma(written)} written to the graph`,
              ]
                .filter(Boolean)
                .join(' · ') || undefined
        }
      />
    </div>
  );
}

// ---- step timings and what ran --------------------------------------------------------------

function StepTimings({ h }: { h: Health }) {
  const timed = h.steps.flatMap((s) => (s.seconds == null ? [] : [{ ...s, seconds: s.seconds }]));
  const untimed = h.steps.filter((s) => s.seconds == null);
  const pole = h.long_pole;
  // the share is a fraction (0.54); a percentage that slipped through is kept as is
  const fraction = pole ? (pole.share > 1 ? pole.share / 100 : pole.share) : 0;
  const share = pole ? Math.round(fraction * 100) : null;
  // the run's own total (the records step isn't in it), so the sentence's numbers agree
  const total = pole && fraction > 0 ? pole.seconds / fraction : timed.reduce((a, s) => a + s.seconds, 0);
  return (
    <Card>
      <CardHeader title="Step timings" sub="seconds per step" />
      <div className="card-b">
        {timed.length ? (
          <HBars
            label="Seconds per step"
            color="var(--s2)"
            max={Math.max(...timed.map((s) => s.seconds))}
            format={(v) => duration(v)}
            rows={timed.map((s) => ({
              label: s.status === 'ok' ? s.step : `${s.step} · ${statusWord(s.status)}`,
              value: s.seconds,
              tip: `${s.step}\n${duration(s.seconds)}${s.status === 'ok' ? '' : ` · ${statusWord(s.status)}`}`,
            }))}
          />
        ) : (
          <span className="muted">No step times were recorded.</span>
        )}
        {pole && share != null ? (
          <p className="ml-note">
            The {pole.step} step is the long pole ({share}% of the time in steps): {duration(pole.seconds)} of {duration(total)}.
          </p>
        ) : null}
        {untimed.length ? <p className="ml-note tight">No time recorded for {untimed.map((s) => s.step).join(', ')}.</p> : null}
      </div>
    </Card>
  );
}

function WhatRan({ h }: { h: Health }) {
  const w = h.what_ran;
  const g = w.graph;
  const rows: [string, string | null, boolean][] = [
    ['Game model', w.game_model, true],
    ['Trained through', w.trained_through, false],
    ['Player model', w.player_model ? `${w.player_model}${w.player_stats != null ? ` · ${w.player_stats} stats` : ''}` : null, true],
    ['Team model', w.team_model, true],
    [
      'Graph build',
      g
        ? [
            g.built_at ? `built ${fmtET(g.built_at, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET` : null,
            g.nodes != null ? `${comma(g.nodes)} nodes` : null,
            g.relationships != null ? `${comma(g.relationships)} relationships` : null,
            g.status && g.status !== 'ok' ? g.status : null,
          ]
            .filter(Boolean)
            .join(' · ') || null
        : null,
      false,
    ],
    ['Digest writer', w.writer?.model ?? null, true],
    [
      'Route',
      w.writer
        ? `${w.writer.route ?? '—'}${w.writer.providers.length ? ` → ${w.writer.providers.join(', ')}` : ''} · ${w.writer.calls} ${w.writer.calls === 1 ? 'call' : 'calls'}`
        : null,
      false,
    ],
    ['Prompt', w.prompt_id, true],
    ['Promoted', w.promoted.length ? `${w.promoted.join(', ')} → production` : h.records === 'full' ? 'nothing promoted' : null, false],
    ['Command', w.command, true],
    ['Launched by', w.launched_by ? `${w.launched_by}${w.via === 'control-room' ? ' · via control room' : ''}` : null, false],
  ];
  const shown = rows.filter(([, v]) => v != null);
  return (
    <Card>
      <CardHeader title="What ran" sub="versions and settings behind this week" />
      <div className="card-b">
        {shown.length ? (
          <dl className="kv">
            {shown.map(([k, v, mono]) => (
              <Fragment key={k}>
                <dt>{k}</dt>
                <dd className={mono ? 'mono' : undefined}>{v}</dd>
              </Fragment>
            ))}
          </dl>
        ) : (
          <span className="muted">No versions or settings were recorded for this week.</span>
        )}
      </div>
    </Card>
  );
}

// ---- data freshness -------------------------------------------------------------------------

const SOURCE_LABEL: Record<string, string> = {
  nflverse: 'nflverse',
  espn: 'ESPN',
  ngs_site: 'Next Gen Stats site',
  open_meteo: 'Open-Meteo weather',
  odds_api: 'The Odds API',
  content: 'Content coverage',
};

/** The run writes "last ingest: ok" / "current" on every healthy row: nothing worth a line. */
const BORING_NOTE = /^(last ingest: ok|current)$/i;

/** 0 = failed, 1 = stale or unknown, 2 = fresh: the order rows are listed in. */
function badness(r: FreshnessRow): number {
  if (r.status === 'failed' || r.status === 'missing') return 0;
  if (r.stale || r.status !== 'ok') return 1;
  return 2;
}

function FreshChip({ r }: { r: FreshnessRow }) {
  if (r.status === 'failed' || r.status === 'missing') return <Chip tone="err" icon="✕">{statusWord(r.status)}</Chip>;
  if (r.stale) return <Chip tone="warn" icon="!">stale</Chip>;
  if (r.status !== 'ok') return <Chip tone="warn" icon="!">{statusWord(r.status)}</Chip>;
  return <Chip tone="ok" icon="✓">fresh</Chip>;
}

function newestText(r: FreshnessRow): string {
  const parts = [
    r.snapshot_date ? `snapshot ${r.snapshot_date}` : null,
    r.newest_week != null ? `through week ${r.newest_week}` : null,
    r.stale && r.expected_week != null && r.expected_week !== r.newest_week ? `expected week ${r.expected_week}` : null,
    r.age_days != null ? (Math.round(r.age_days) === 0 ? 'today' : `${Math.round(r.age_days)} d old`) : null,
  ].filter(Boolean);
  return parts.join(' · ') || '—';
}

// a stable sort: within a badness the run's own order is kept
const byBadness = (a: FreshnessRow, b: FreshnessRow) => badness(a) - badness(b);
const FLAT_LIMIT = 8;

function FreshRow({ r }: { r: FreshnessRow }) {
  return (
    <tr>
      <td className="mono">{r.dataset}</td>
      <td className="muted">
        {newestText(r)}
        {r.note && (badness(r) < 2 || !BORING_NOTE.test(r.note)) ? <div className="ml-small">{r.note}</div> : null}
      </td>
      <td className="c">
        <FreshChip r={r} />
      </td>
    </tr>
  );
}

function Freshness({ rows }: { rows: FreshnessRow[] }) {
  const bad = rows.filter((r) => badness(r) < 2).length;
  const grouped = rows.length > FLAT_LIMIT;
  const sources = unique(rows.map((r) => r.source));
  // sources with a problem first, then the run's order
  const order = [...sources.filter((s) => rows.some((r) => r.source === s && badness(r) < 2)), ...sources.filter((s) => !rows.some((r) => r.source === s && badness(r) < 2))];
  return (
    <Card>
      <CardHeader
        title="Data freshness"
        sub={rows.length ? `${rows.length} sources · ${bad ? `${bad} stale or failed` : 'none stale'}` : 'what the run read'}
      />
      {rows.length ? (
        <div className="tablewrap ml-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Newest</th>
                <th className="c">State</th>
              </tr>
            </thead>
            <tbody>
              {grouped
                ? order.map((src) => {
                    const mine = rows.filter((r) => r.source === src).sort(byBadness);
                    const stale = mine.filter((r) => badness(r) < 2).length;
                    return [
                      <tr key={`g-${src}`} className="grp">
                        <td colSpan={3}>
                          <span className="sub-h">{SOURCE_LABEL[src] ?? src}</span>
                          <span className="muted">
                            {mine.length} checked{stale ? ` · ${stale} stale or failed` : ''}
                          </span>
                        </td>
                      </tr>,
                      ...mine.map((r) => <FreshRow key={`${src}-${r.dataset}`} r={r} />),
                    ];
                  })
                : [...rows].sort(byBadness).map((r) => <FreshRow key={`${r.source}-${r.dataset}`} r={r} />)}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card-b muted">This week has no freshness table: the run summary isn&apos;t there.</div>
      )}
    </Card>
  );
}

// ---- this week vs last week -----------------------------------------------------------------

function VsLast({ h, week }: { h: Health; week: number }) {
  // the run before this one: the newest week in any trend below this week (normally week − 1)
  const earlier = h.vs_last.flatMap((r) => r.trend.map((t) => t.week)).filter((w) => w < week);
  const lastWeek = earlier.length ? Math.max(...earlier) : week - 1;
  return (
    <Card>
      <CardHeader title="This week vs last week" sub={h.vs_last.length ? 'from every run so far' : undefined} />
      {h.vs_last.length ? (
        <div className="tablewrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Measure</th>
                <th className="r">Week {week}</th>
                <th className="r">Week {lastWeek}</th>
                <th className="r">Change</th>
                <th>Season</th>
              </tr>
            </thead>
            <tbody>
              {h.vs_last.map((r) => {
                const pts = r.trend.flatMap((t) => (t.value == null ? [] : [{ week: t.week, value: t.value }]));
                return (
                  <tr key={r.measure}>
                    <td>{r.measure}</td>
                    <td className="r num">
                      <b>{fmtMeasure(r.unit, r.this_week)}</b>
                    </td>
                    <td className={`r num${r.last_week == null ? ' muted' : ''}`}>{r.last_week == null ? 'no run' : fmtMeasure(r.unit, r.last_week)}</td>
                    <td className="r num">{deltaText(r.unit, r.this_week, r.last_week)}</td>
                    <td>
                      {pts.length ? (
                        <TipTarget
                          className="ml-spark"
                          label={`${r.measure} by week: ${pts.map((p) => `week ${p.week} ${fmtMeasure(r.unit, p.value)}`).join(', ')}`}
                          lines={[r.measure, ...pts.slice(-8).map((p) => `Week ${p.week}: ${fmtMeasure(r.unit, p.value)}`)]}
                        >
                          <Sparkline values={pts.map((p) => p.value)} label={`${r.measure} trend`} />
                        </TipTarget>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card-b">
          <span className="muted ml-note tight">
            Each row gets the change and a season trend line from <span className="mono">pipeline_history.parquet</span> once this week has run records.
          </span>
        </div>
      )}
    </Card>
  );
}

// ---- ingest and quality ---------------------------------------------------------------------

function Ingest({ ingest }: { ingest: Health['ingest'] }) {
  return (
    <Card>
      <CardHeader
        title="Ingest, dataset by dataset"
        sub={[ingest.manifest, ingest.snapshot_date].filter(Boolean).join(' · ') || 'raw/_runs manifest'}
      />
      {ingest.rows.length ? (
        <div className="tablewrap ml-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>Source</th>
                <th>Dataset</th>
                <th className="r">Rows</th>
                <th className="c">Status</th>
              </tr>
            </thead>
            <tbody>
              {ingest.rows.map((r) => (
                <tr key={`${r.source}-${r.dataset}`}>
                  <td className="muted">{r.source}</td>
                  <td>
                    <span className="mono">{r.dataset}</span>
                    {r.detail ? <div className="ml-small">{r.detail}</div> : null}
                  </td>
                  <td className="r num">{comma(r.rows)}</td>
                  <td className="c">
                    {r.status === 'ok' ? <Chip tone="ok" icon="✓">ok</Chip> : <Chip tone="err" icon="✕">{statusWord(r.status)}</Chip>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card-b muted">No ingest manifest was found for this run.</div>
      )}
    </Card>
  );
}

function Quality({ h }: { h: Health }) {
  const q = h.quality;
  const t = h.tiles.quality;
  return (
    <Card>
      <CardHeader title="Quality checks" sub="curate · block = stops the run, warn = flags it" />
      {q.match === 'later' ? (
        <div className="ml-pad">
          <Notice>
            <b>
              This file holds a later curate run{q.run_at ? ` (${fmtET(q.run_at, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET)` : ''}.
            </b>{' '}
            {q.checks.length
              ? `Only the counts in this run's summary${t.total != null ? ` (${t.passed ?? 0} of ${t.total} passed)` : ''} are this run's; the checks below are the later run's.`
              : "This run's own checks weren't kept, so none are listed."}
          </Notice>
        </div>
      ) : null}
      {q.failed.length ? (
        <div className="ml-pad ml-chips" style={{ marginTop: 0 }}>
          <span className="lead">Failed in this run:</span>
          {q.failed.map((n) => (
            <Chip key={n} tone="err" icon="✕">
              {n}
            </Chip>
          ))}
        </div>
      ) : null}
      {q.checks.length ? (
        <div className="tablewrap ml-scroll" style={q.match === 'later' || q.failed.length ? { marginTop: 12 } : undefined}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Check</th>
                <th>Level</th>
                <th className="c">Result</th>
              </tr>
            </thead>
            <tbody>
              {q.checks.map((c) => (
                <tr key={c.name}>
                  <td>
                    <span>{c.name}</span>
                    {c.detail ? <div className="ml-small">{c.detail}</div> : null}
                  </td>
                  <td>
                    <Chip>{c.level}</Chip>
                  </td>
                  <td className="c">
                    {c.passed ? (
                      <Chip tone="ok" icon="✓">pass</Chip>
                    ) : c.level === 'warn' ? (
                      <Chip tone="warn" icon="!">flagged</Chip>
                    ) : (
                      <Chip tone="err" icon="✕">fail</Chip>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : q.match === 'later' && t.total == null ? null : (
        <div className="card-b muted">
          {q.match === 'none' ? 'No quality file was saved for this run.' : 'No checks were listed.'}
          {t.total != null ? ` The run summary counts ${t.passed ?? 0} of ${t.total} passed.` : ''}
        </div>
      )}
    </Card>
  );
}

// ---- drift ----------------------------------------------------------------------------------

/** The run writes "QB: 4 scored week(s) so far; ..." per group: without the prefix they match. */
const bare = (r: { group: string | null; detail: string }) =>
  r.group && r.detail.startsWith(`${r.group}: `) ? r.detail.slice(r.group.length + 2) : r.detail;

function DriftBlock({ g }: { g: DriftGroup }) {
  const single = g.rows.length === 1 && g.rows[0].group == null;
  const details = unique(g.rows.map(bare).filter(Boolean));
  const responses = unique(g.rows.map((r) => r.response).filter(Boolean));
  const statuses = unique(g.rows.map((r) => r.status));
  return (
    <div className="signal">
      <b>
        {g.name}
        {single ? null : <span className="muted"> · {g.rows.length} groups</span>}
      </b>
      <StatusChip status={g.status} />
      {single ? (
        <p>
          {g.rows[0].detail}
          {valueText(g.rows[0]) ? <span className="muted"> · {valueText(g.rows[0])}</span> : null}
        </p>
      ) : (
        <>
          <div className="dr-lines">
            {statuses.map((s) => (
              <div key={s} className="dr-line">
                <StatusChip status={s} />
                <span>
                  {g.rows
                    .filter((r) => r.status === s)
                    .map((r) => [r.group ?? '—', valueText(r)].filter(Boolean).join(' '))
                    .join(', ')}
                </span>
              </div>
            ))}
          </div>
          {details.length === 1 ? (
            <p>{details[0]}</p>
          ) : (
            <ul className="dr-list">
              {g.rows.map((r) => (
                <li key={r.group ?? r.detail}>
                  <b>{r.group ?? '—'}</b> {bare(r)}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {responses.map((r) => (
        <p key={r} className="resp">
          Response: {r}
        </p>
      ))}
    </div>
  );
}

function Drift({ rows }: { rows: Health['drift'] }) {
  const groups = groupDrift(rows);
  const alerts = rows.filter((r) => r.status === 'alert').length;
  const allWaiting = rows.length > 0 && rows.every((r) => r.status === 'insufficient_data');
  return (
    <Card>
      <CardHeader
        title="Drift signals"
        sub="they ask a person to look; they never retune anything"
        right={
          alerts ? (
            <Chip tone="err" icon="!">
              {alerts} {alerts === 1 ? 'alert' : 'alerts'}
            </Chip>
          ) : allWaiting ? (
            <Chip tone="ghost">not enough weeks yet</Chip>
          ) : rows.length ? (
            <Chip tone="ok" icon="✓">
              no alerts
            </Chip>
          ) : undefined
        }
      />
      <div className="card-b">
        {groups.length ? (
          groups.map((g) => <DriftBlock key={g.name} g={g} />)
        ) : (
          <span className="muted">This week has no drift record: the run summary isn&apos;t there.</span>
        )}
      </div>
    </Card>
  );
}

// ---- the section ----------------------------------------------------------------------------

function HealthView({ h, week }: { h: Health; week: number }) {
  return (
    <div className="wk-stack">
      {h.notice ? h.records === 'partial' ? <WarnNotice>{h.notice}</WarnNotice> : <Notice>{h.notice}</Notice> : null}
      <Tiles h={h} />
      <div className="grid g2">
        <StepTimings h={h} />
        <WhatRan h={h} />
      </div>
      <div className="grid g2">
        <Freshness rows={h.freshness} />
        <VsLast h={h} week={week} />
      </div>
      <div className="grid g2">
        <Ingest ingest={h.ingest} />
        <Quality h={h} />
      </div>
      <Drift rows={h.drift} />
    </div>
  );
}

export function HealthSection({
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
  const q = useMlopsHealth(season, week);
  if (q.isPending) return <TabLoading />;
  if (q.isError) return <TabError error={q.error} />;
  const h = q.data;
  if (h.records === 'none') {
    return (
      <EmptyState
        glyph="ML"
        title={isCurrent ? 'Run health appears when the run starts' : `No run records for week ${week}`}
        actions={
          isCurrent ? (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}>
              <Link className="btn sm" to={`/week/${season}/${week}/pipeline`}>
                Go to the pipeline
              </Link>
              {lastPublishedWeek != null && lastPublishedWeek !== week ? (
                <Link className="btn sm" to={`/week/${season}/${lastPublishedWeek}/mlops`}>
                  See week {lastPublishedWeek}&apos;s MLOps
                </Link>
              ) : null}
            </div>
          ) : undefined
        }
      >
        {h.notice ??
          (isCurrent
            ? 'Step timings, what ran, data freshness, ingest and quality checks and drift fill in as the weekly run goes.'
            : `Week ${week} has no weekly run record, so there's nothing to show for run health.`)}
      </EmptyState>
    );
  }
  return <HealthView h={h} week={week} />;
}
