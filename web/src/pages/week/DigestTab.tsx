// The Digest tab (mockup: w4Digest): the rendered digest, and beside it the checks, words per
// section, the writer (model, route, each call's time, reasoning and cost), the files and
// Saturday's addendum. Markdown is rendered without raw HTML; tables sit in scroll boxes.

import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useDigest } from '../../api/client';
import type { CheckRound, DigestResponse } from '../../api/types';
import { HBars } from '../../charts/HBars';
import { Card, CardHeader, Chip } from '../../components/ui';
import { comma, duration, kickoffLabel } from '../../lib/format';
import { TabError, TabLoading, WarnNotice, WeekEmpty } from './WeekEmpty';
import './week.css';

const MD_COMPONENTS: Components = {
  table: ({ children }) => (
    <div className="tablewrap">
      <table>{children}</table>
    </div>
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  // the app stays offline: an image shows its alt text instead of loading
  img: ({ alt }) => <span className="muted">{alt ? `[${alt}]` : ''}</span>,
};

function Md({ src }: { src: string }) {
  return (
    <Markdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
      {src}
    </Markdown>
  );
}

function resultText(c: NonNullable<DigestResponse['checks']>): string {
  if (c.passed) return c.regenerated ? 'passed after one regeneration' : 'all passed first time';
  return c.regenerated ? 'failed after one regeneration' : 'failed';
}

function Checklist({ round }: { round: CheckRound }) {
  return (
    <ul className="checklist" aria-label="Final checks">
      {round.checks.map((x) => (
        <li key={x.name} style={{ flexDirection: 'column', gap: 2 }}>
          <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <span className="mono">{x.name}</span>
            <span>
              {x.passed ? (
                <span className="p" role="img" aria-label="passed">
                  ✓
                </span>
              ) : (
                <span className="x" role="img" aria-label="failed">
                  ✕
                </span>
              )}{' '}
              <span className="muted">{x.level}</span>
            </span>
          </span>
          {!x.passed && x.issues.length ? (
            <ul className="issues">
              {x.issues.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function ChecksCard({ checks }: { checks: NonNullable<DigestResponse['checks']> }) {
  const round = checks.final;
  return (
    <Card>
      <CardHeader
        title="Checks"
        right={
          <>
            {checks.banner ? (
              <Chip tone="warn" icon="!">
                Banner
              </Chip>
            ) : null}
            {checks.passed ? (
              <Chip tone="ok" icon="✓">
                Passed
              </Chip>
            ) : (
              <Chip tone="err" icon="✕">
                Failed
              </Chip>
            )}
          </>
        }
      />
      <div className="card-b" style={{ display: 'grid', gap: 12 }}>
        <dl className="kv">
          <dt>Result</dt>
          <dd>{resultText(checks)}</dd>
          <dt>Warning banner</dt>
          <dd>{checks.banner ? 'yes' : 'none'}</dd>
          <dt>Writers</dt>
          <dd>{[...new Set(checks.writers)].join(', ') || '—'}</dd>
        </dl>
        {round ? <Checklist round={round} /> : null}
        {checks.fallback_notes.length ? (
          <ul className="issues" style={{ margin: 0 }}>
            {checks.fallback_notes.map((t, i) => (
              <li key={i} className="muted">
                {t}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Card>
  );
}

function WriterCard({ d }: { d: DigestResponse }) {
  const w = d.writer;
  if (!w) return null;
  const route = w.calls[0]?.provider ? `${w.provider} → ${w.calls[0].provider}` : w.provider;
  return (
    <Card>
      <CardHeader
        title="The writer"
        right={
          d.wandb_url ? (
            <a className="btn sm" href={d.wandb_url} target="_blank" rel="noopener noreferrer">
              digest run ↗
            </a>
          ) : undefined
        }
      />
      <div className="card-b" style={{ display: 'grid', gap: 12 }}>
        <dl className="kv">
          <dt>Model</dt>
          <dd className="mono">{w.model}</dd>
          <dt>Route</dt>
          <dd>{route}</dd>
          <dt>Prompt</dt>
          <dd className="mono">{w.prompt ?? '—'}</dd>
          <dt>Total time</dt>
          <dd>{duration(w.total_seconds)}</dd>
          <dt>Total cost</dt>
          <dd>{w.total_cost == null ? '—' : `$${w.total_cost.toFixed(4)}`}</dd>
        </dl>
        {w.calls.length ? (
          <div className="tablewrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Call</th>
                  <th className="r">Time</th>
                  <th className="r">Reasoning</th>
                  <th className="r">Cost</th>
                </tr>
              </thead>
              <tbody>
                {w.calls.map((c, i) => (
                  <tr key={i}>
                    <td>{i === 0 ? 'first draft' : c.regeneration ? 'rewrite' : `call ${i + 1}`}</td>
                    <td className="r num">{duration(c.latency_s)}</td>
                    <td className="r num">{comma(c.usage.reasoning)}</td>
                    <td className="r num">{c.cost == null ? '—' : `$${c.cost.toFixed(4)}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <span className="muted">No LLM calls ({w.provider} writer).</span>
        )}
      </div>
    </Card>
  );
}

const isSaturday = (label: string) => label.toLowerCase() === 'saturday';

function FilesCard({ d }: { d: DigestResponse }) {
  const a = d.addendum;
  const saturday = a?.markdown
    ? `addendum below${a.at ? ` · ${kickoffLabel(a.at)}` : ''}`
    : a && a.material === false
      ? 'no material changes'
      : 'no addendum';
  // the reader lists the addendum's file as "Saturday"; until it exists the row says why
  const satFile = d.files.find((f) => isSaturday(f.label));
  return (
    <Card>
      <CardHeader title="Files" />
      <div className="card-b">
        <dl className="kv mono" style={{ fontSize: 11.5 }}>
          {d.files
            .filter((f) => !isSaturday(f.label) || f.exists)
            .map((f) => (
              <FileRow key={f.label} label={f.label} path={f.path} exists={f.exists} />
            ))}
          {!satFile?.exists ? (
            <>
              <dt>Saturday</dt>
              <dd>{saturday}</dd>
            </>
          ) : null}
        </dl>
      </div>
    </Card>
  );
}

function FileRow({ label, path, exists }: { label: string; path: string; exists: boolean }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={exists ? undefined : 'muted'} title={exists ? undefined : 'not found'}>
        {path}
        {exists ? '' : ' · missing'}
      </dd>
    </>
  );
}

export function DigestTab({
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
  const q = useDigest(season, week);
  if (q.isPending) return <TabLoading />;
  if (q.isError) return <TabError error={q.error} />;
  const d = q.data;
  if (d.status === 'none' || !d.markdown) {
    return <WeekEmpty tab="digest" season={season} week={week} isCurrent={isCurrent} lastPublishedWeek={lastPublishedWeek} />;
  }
  const wc = Object.entries(d.checks?.final?.word_counts ?? {});

  return (
    <div className="wk-stack">
      {d.status === 'unpublished' ? (
        <WarnNotice>
          <b>Not published: the run's draft.</b> This is the run folder's digest, from a run that didn't publish
          it. The published digest replaces it when a run finishes.
        </WarnNotice>
      ) : null}
      <div className="split">
        <div className="wk-stack">
          <div className="card pad">
            <article className="digest">
              <Md src={d.markdown} />
            </article>
          </div>
          {d.addendum?.markdown ? (
            <div className="card pad" aria-label="Saturday's addendum">
              <span className="eyebrow">Saturday's injury update</span>
              <article className="digest">
                <Md src={d.addendum.markdown} />
              </article>
            </div>
          ) : null}
        </div>
        <div className="rail">
          {d.checks ? <ChecksCard checks={d.checks} /> : null}
          {wc.length ? (
            <Card>
              <CardHeader title="Words per section" />
              <div className="card-b">
                <HBars
                  label="Words per section"
                  rows={wc.map(([k, v]) => {
                    const name = k.replace(/_/g, ' ');
                    return { label: name, value: v, tip: `${name}\n${v} words` };
                  })}
                  format={(v) => String(v)}
                />
              </div>
            </Card>
          ) : null}
          <WriterCard d={d} />
          <FilesCard d={d} />
        </div>
      </div>
    </div>
  );
}
