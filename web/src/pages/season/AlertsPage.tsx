// Alerts (mockup: alertsView): the season's alerts with their investigation notes (or the empty
// card), when each drift signal can first fire, what an alert looks like (the 2025 week-10
// calibration example, D75), and every run's drift states.

import { Link } from 'react-router-dom';
import { useAlerts } from '../../api/client';
import type { AlertItem, AlertsResponse } from '../../api/types';
import { TipTarget } from '../../charts/TipTarget';
import { Card, CardHeader, Chip, type ChipTone } from '../../components/ui';
import { Page } from '../Pages';
import { ExternalLink, PageError, PageLoading } from './common';

const LEVEL: Record<string, { tone: ChipTone; icon: string }> = {
  warn: { tone: 'warn', icon: '!' },
  error: { tone: 'err', icon: '✕' },
};

function LevelChip({ level }: { level: string }) {
  const l = LEVEL[level] ?? { tone: 'flat' as ChipTone, icon: 'i' };
  return (
    <Chip tone={l.tone} icon={l.icon}>
      {level}
    </Chip>
  );
}

const DRIFT: Record<string, { tone: ChipTone; icon: string; text: string }> = {
  ok: { tone: 'ok', icon: '✓', text: 'ok' },
  alert: { tone: 'warn', icon: '!', text: 'alert' },
  insufficient_data: { tone: 'ghost', icon: '…', text: 'not enough data yet' },
};

function DriftChip({ status }: { status: string | null }) {
  const d = status ? DRIFT[status] : undefined;
  if (!d) return <Chip tone="ghost">{status ? status.replace(/_/g, ' ') : '—'}</Chip>;
  return (
    <Chip tone={d.tone} icon={d.icon}>
      {d.text}
    </Chip>
  );
}

function AlertCard({
  a,
  example,
  note,
}: {
  a: AlertItem;
  example?: boolean;
  note?: string | null;
}) {
  return (
    <Card className="alert-card">
      <CardHeader
        title={example ? 'What an alert looks like' : a.title}
        right={
          example ? (
            <Chip tone="warn">{`example · ${a.season} week-${a.week} simulation`}</Chip>
          ) : undefined
        }
      />
      <div className="card-b">
        <div className="alert-head">
          <LevelChip level={a.level} />
          <b className="mono">{a.source}</b>
          <span className="muted">
            {a.season} week {a.week}
            {example ? ' · simulation' : ''}
          </span>
        </div>
        {example ? <b>{a.title}</b> : null}
        <p>{a.text}</p>
        {a.notes.length ? (
          <div className="notice" style={{ fontSize: 12.5 }}>
            <span className="ic" aria-hidden="true">
              →
            </span>
            <div>
              <b>Investigation:</b>{' '}
              {a.notes.map((n, i) => (
                <span key={i}>
                  {i ? ' ' : ''}
                  {n}
                </span>
              ))}
            </div>
          </div>
        ) : !example ? (
          <p className="muted" style={{ fontSize: 12.5 }}>
            No investigation notes yet: they're written in PROGRESS's Season log under week {a.week}
            .
          </p>
        ) : null}
        {note ? (
          <p className="muted" style={{ fontSize: 12.5 }}>
            {note}
          </p>
        ) : null}
        {!example ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {a.run_url ? (
              <ExternalLink href={a.run_url}>Open the run in W&amp;B</ExternalLink>
            ) : null}
            <Link className="btn sm" to={`/week/${a.season}/${a.week}/pipeline`}>
              Week {a.week}'s pipeline
            </Link>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/** Tooltip lines don't wrap (they're `nowrap`), so long sentences are split at word boundaries. */
function wrap(text: string, width = 52): string[] {
  const out: string[] = [];
  let line = '';
  for (const word of text.split(/\s+/)) {
    if (line && line.length + 1 + word.length > width) {
      out.push(line);
      line = word;
    } else {
      line = line ? `${line} ${word}` : word;
    }
  }
  return line ? [...out, line] : out;
}

/** One entry per signal and status, with its position groups (a run has a row per group). */
function bySignal(drift: AlertsResponse['runs'][number]['drift']) {
  const out: { name: string; status: string; groups: string[] }[] = [];
  for (const d of drift) {
    let e = out.find((x) => x.name === d.name && x.status === d.status);
    if (!e) {
      e = { name: d.name, status: d.status, groups: [] };
      out.push(e);
    }
    if (d.group) e.groups.push(d.group);
  }
  return out;
}

const LO = 4;
const HI = 18;
const X = (w: number) => ((Math.min(HI, Math.max(LO, w)) - LO) / (HI - LO)) * 100;
const AXIS = [4, 6, 8, 10, 12, 14, 16, 18];

function Timeline({ r }: { r: AlertsResponse }) {
  const now = r.current_week;
  return (
    <Card>
      <CardHeader title="When each signal can first fire" sub={`${r.season} season`} />
      <div className="card-b">
        <div className="sigline">
          <div className="sig axis" aria-hidden="true">
            <span />
            <div className="axis-row">
              {AXIS.map((w) => (
                <span key={w} className="tick" style={{ left: `${X(w)}%` }}>
                  W{w}
                </span>
              ))}
              {now != null && now >= LO && now <= HI ? (
                <>
                  <span className="now" style={{ left: `${X(now)}%` }} />
                  <span
                    className="now-l"
                    style={
                      now > 14
                        ? { right: `calc(${100 - X(now)}% + 6px)` }
                        : { left: `calc(${X(now)}% + 6px)` }
                    }
                  >
                    this week
                  </span>
                </>
              ) : null}
            </div>
          </div>
          {r.signals.map((s) => {
            const from = X(s.first_week ?? LO);
            const lines = [
              s.name,
              ...wrap(s.label),
              ...wrap(`First fires: ${s.first_text}`),
              `Needs ${s.needs}`,
              `Now: ${s.status_now ? (DRIFT[s.status_now]?.text ?? s.status_now.replace(/_/g, ' ')) : 'no run yet'}`,
              ...(s.detail_now ? wrap(s.detail_now) : []),
            ];
            return (
              <div key={s.name} className="sig">
                <span className="mono" title={s.label}>
                  {s.name}
                </span>
                <div className="sig-track">
                  <span className="sig-bar" style={{ left: `${from}%` }} />
                  <span className="sig-dot" style={{ left: `${from}%` }} />
                  <TipTarget
                    className="sig-hit"
                    lines={lines}
                    label={`${s.name}: from ${s.first_text}, needs ${s.needs}`}
                    style={{ left: `calc(${from}% - 8px)` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
        <p className="muted" style={{ margin: '14px 0 0', fontSize: 12.5 }}>
          Until then a signal reports "not enough data yet", which isn't an error.
        </p>
      </div>
    </Card>
  );
}

function RunsTable({ season, runs }: { season: number; runs: AlertsResponse['runs'] }) {
  return (
    <Card>
      <CardHeader title="Drift by run" sub="every weekly run's signals" />
      {runs.length ? (
        <div className="tablewrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Week</th>
                <th className="r">Alerts</th>
                <th>Signals</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.week}>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <Link to={`/week/${season}/${run.week}/mlops`}>
                      <b>Week {run.week}</b>
                    </Link>
                  </td>
                  <td className="r num">{run.alerts}</td>
                  <td>
                    <div style={{ display: 'flex', gap: '6px 14px', flexWrap: 'wrap' }}>
                      {bySignal(run.drift).map((d) => (
                        <span
                          key={`${d.name}-${d.status}`}
                          style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}
                          title={d.groups.length ? d.groups.join(', ') : undefined}
                        >
                          <span className="mono" style={{ fontSize: 12 }}>
                            {d.name}
                          </span>
                          <DriftChip status={d.status} />
                          {d.groups.length ? (
                            <span className="muted" style={{ fontSize: 12 }}>
                              {d.groups.length === 1 ? d.groups[0] : `${d.groups.length} groups`}
                            </span>
                          ) : null}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card-b muted">No weekly runs with drift checks yet.</div>
      )}
    </Card>
  );
}

function AlertsBody({ r }: { r: AlertsResponse }) {
  return (
    <>
      {r.alerts.length ? (
        <div className="grid g2">
          {r.alerts.map((a, i) => (
            <AlertCard key={`${a.week}-${a.source}-${i}`} a={a} />
          ))}
        </div>
      ) : (
        <Card>
          <div className="empty">
            <div className="glyph" aria-hidden="true">
              0
            </div>
            <h3>No alerts in {r.season} yet</h3>
            <p>
              Alerts come from a failed step, a "not ready" past the retry window, a degraded step,
              a digest that failed its checks, a lock left behind, and the five drift signals. They
              also go to W&amp;B's alerts (email or app).
            </p>
          </div>
        </Card>
      )}
      <div className="grid g2">
        <Timeline r={r} />
        {r.example ? <AlertCard a={r.example} example note={r.example_note} /> : null}
      </div>
      <RunsTable season={r.season} runs={r.runs} />
    </>
  );
}

export function AlertsPage() {
  const q = useAlerts();
  const n = q.data?.alerts.length;
  return (
    <Page
      eyebrow="Operations"
      title="Alerts"
      sub={
        q.data
          ? `${q.data.season} · ${n === 0 ? 'none yet' : `${n} alert${n === 1 ? '' : 's'}`}`
          : 'Drift signals, failed steps and stale data'
      }
    >
      {q.isPending ? (
        <PageLoading />
      ) : q.isError ? (
        <PageError error={q.error} what="the alerts" />
      ) : (
        <AlertsBody r={q.data} />
      )}
    </Page>
  );
}
