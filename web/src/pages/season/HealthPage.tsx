// System → Health (mockup: healthView): services (Neo4j and its plugins, Docker, W&B, OpenRouter,
// the data drive, the run lock), variables by name with set / not set (never a value: the
// server only sends names and two booleans), and the most recent runs.

import { HEALTH_PATH, refreshWandb, useHealth } from '../../api/client';
import type { HealthResponse } from '../../api/types';
import { Card, CardHeader, Chip, Notice, type ChipTone } from '../../components/ui';
import { duration, fmtET } from '../../lib/format';
import { Page } from '../Pages';
import { PageError, PageLoading, RunStatusChip } from './common';

const SERVICE: Record<
  HealthResponse['services'][number]['status'],
  { tone: ChipTone; icon: string; text: string }
> = {
  ok: { tone: 'ok', icon: '✓', text: 'ok' },
  warn: { tone: 'warn', icon: '!', text: 'warn' },
  fail: { tone: 'err', icon: '✕', text: 'fail' },
  busy: { tone: 'run', icon: '●', text: 'busy' },
  checking: { tone: 'ghost', icon: '…', text: 'checking' },
};

const when = (iso: string | null) =>
  iso
    ? `${fmtET(iso, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} ET`
    : '—';

function Services({
  h,
  onCheck,
  fetching,
}: {
  h: HealthResponse;
  onCheck: () => void;
  fetching: boolean;
}) {
  return (
    <Card>
      <CardHeader
        title="Services"
        sub={h.checked_at ? `checked ${when(h.checked_at)}` : 'not checked yet'}
        right={
          <button
            type="button"
            className="btn sm"
            onClick={onCheck}
            disabled={h.checking || fetching}
          >
            {h.checking ? 'Checking…' : 'Check again'}
          </button>
        }
      />
      <div
        className="checking"
        role="status"
        aria-live="polite"
        style={{ padding: h.checking ? '8px 18px 0' : 0 }}
      >
        {h.checking ? 'Checking W&B, OpenRouter and Neo4j… this page updates by itself.' : ''}
      </div>
      <div className="tablewrap">
        <table className="tbl">
          <tbody>
            {h.services.map((s) => {
              const c = SERVICE[s.status] ?? SERVICE.checking;
              return (
                <tr key={s.name}>
                  <td>
                    <b>{s.name}</b>
                  </td>
                  <td className="muted">{s.detail}</td>
                  <td className="c">
                    <Chip tone={c.tone} icon={c.icon}>
                      {c.text}
                    </Chip>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Variables({ vars }: { vars: HealthResponse['variables'] }) {
  return (
    <Card>
      <CardHeader title="Variables" sub="names only" />
      <div className="tablewrap">
        <table className="tbl">
          <tbody>
            {vars.map((v) => (
              <tr key={v.name}>
                <td className="mono">{v.name}</td>
                <td className="c">
                  {v.set ? (
                    <Chip tone="ok" icon="✓">
                      set
                    </Chip>
                  ) : v.required ? (
                    <Chip tone="err" icon="✕">
                      not set (required)
                    </Chip>
                  ) : (
                    <Chip tone="ghost">not set · optional</Chip>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function RecentRuns({ runs }: { runs: HealthResponse['recent_runs'] }) {
  return (
    <Card>
      <CardHeader title="Recent runs" sub="pipeline history and the control room's launches" />
      {runs.length ? (
        <div className="tablewrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>When (ET)</th>
                <th>Week</th>
                <th>Command</th>
                <th>Status</th>
                <th>Launched by</th>
                <th className="r">Took</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r, i) => (
                <tr key={r.run_id ?? `${r.started}-${i}`}>
                  <td className="num" style={{ whiteSpace: 'nowrap' }}>
                    {r.started
                      ? fmtET(r.started, {
                          weekday: 'short',
                          month: 'short',
                          day: 'numeric',
                          hour: 'numeric',
                          minute: '2-digit',
                        })
                      : '—'}
                  </td>
                  <td className="num">
                    {r.season} · {r.week}
                  </td>
                  <td className="mono">{r.command ?? r.kind}</td>
                  <td>
                    <RunStatusChip status={r.status} />
                  </td>
                  <td>
                    {r.launched_by ?? '—'}
                    {r.via === 'control-room' ? (
                      <span className="muted"> · via control room</span>
                    ) : null}
                  </td>
                  <td className="r num">{duration(r.seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card-b muted">No runs recorded yet.</div>
      )}
    </Card>
  );
}

export function HealthPage() {
  const q = useHealth();
  const h = q.data;
  const problems = h
    ? h.services.filter((s) => s.status === 'fail').length +
      h.variables.filter((v) => v.required && !v.set).length
    : 0;
  return (
    <Page eyebrow="System" title="Health" sub="Services, variables and the latest runs">
      {q.isPending ? (
        <PageLoading />
      ) : q.isError ? (
        <PageError error={q.error} what="the health checks" />
      ) : (
        <>
          <Notice>
            From the same checks as <span className="mono">nfl doctor</span>:{' '}
            <i>set / not set / connection OK</i>. Values stay on the server and never reach the
            browser.
          </Notice>
          {problems ? (
            <Notice tone="err" icon="✕">
              <b>
                {problems} problem{problems === 1 ? '' : 's'}
              </b>{' '}
              below: a service that failed its check or a required variable that isn't set.
            </Notice>
          ) : null}
          <div className="grid g2">
            <Services
              h={q.data}
              fetching={q.isFetching}
              onCheck={() => {
                refreshWandb(HEALTH_PATH);
                void q.refetch();
              }}
            />
            <Variables vars={q.data.variables} />
          </div>
          <RecentRuns runs={q.data.recent_runs} />
        </>
      )}
    </Page>
  );
}
