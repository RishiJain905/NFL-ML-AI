// MLOps → Artifacts (mockup: mlArtifacts): every model, graph and digest version with its
// aliases, where `production` points, and the lineage behind the week's digest. Versions, aliases
// and lineage exist only in W&B; when it can't be reached the banner says so and the page shows
// the model versions the run itself recorded.

import { Fragment, useState } from 'react';
import { mlopsPath, refreshWandb, useMlopsArtifacts } from '../../../api/client';
import type { ArtifactCollection, MlopsArtifactsResponse } from '../../../api/types';
import { Card, CardHeader, Chip, Tile } from '../../../components/ui';
import { WandbBanner } from '../../../components/WandbBanner';
import { fmtET } from '../../../lib/format';
import { TabError, TabLoading } from '../WeekEmpty';
import { EmptyBlock, ExtLink } from './parts';
import { bytes, projectBase } from './util';

type Artifacts = MlopsArtifactsResponse;

const FIRST_VERSIONS = 6;
const PRODUCTION_TILES = ['game-model', 'player-model', 'team-model'];

/** The response's project URL, or the one its version and collection links imply (it can come back null). */
function projectOf(d: Artifacts): string | null {
  return projectBase(d.wandb.project_url, d.collections.flatMap((c) => [c.url, ...c.versions.map((v) => v.logged_by_url)]));
}

function ProductionTile({ name, d }: { name: string; d: Artifacts }) {
  const prod = d.production.find((p) => p.name === name);
  const logged = d.collections.find((c) => c.name === name)?.versions.find((v) => v.version === prod?.version)?.logged_by_url;
  const project = projectOf(d);
  const runHref = logged ?? (prod?.run_id && project ? `${project}/runs/${prod.run_id}` : null);
  return (
    <Tile
      k={`${name} · production`}
      v={prod?.version ?? '—'}
      d={
        prod?.version ? (
          <>
            {prod.week_alias ?? ''}
            {prod.week_alias && prod.run_id ? ' · ' : ''}
            {prod.run_id ? (
              <ExtLink className="mono" href={runHref} label={`${prod.run_id} in W&B`}>
                {prod.run_id}
              </ExtLink>
            ) : null}
          </>
        ) : d.wandb.available ? (
          'no production version yet'
        ) : (
          'needs W&B'
        )
      }
    />
  );
}

/** The lineage note, with the pipeline run's id in it turned into a link (or the link added when
 *  the note doesn't name it). */
function NoteWithRun({ note, runId, base }: { note: string | null; runId: string | null; base: string | null }) {
  const href = runId && base ? `${base}/runs/${runId}` : null;
  const at = note && runId ? note.indexOf(runId) : -1;
  if (note && runId && at >= 0) {
    return (
      <>
        {note.slice(0, at)}
        <ExtLink className="mono" href={href}>
          {runId}
        </ExtLink>
        {note.slice(at + runId.length)}
      </>
    );
  }
  return (
    <>
      {note}
      {runId ? (
        <>
          {note ? ' ' : ''}
          Pipeline run{' '}
          <ExtLink className="mono" href={href}>
            {runId}
          </ExtLink>
          .
        </>
      ) : null}
    </>
  );
}

function Lineage({ d }: { d: Artifacts }) {
  const l = d.lineage;
  return (
    <Card>
      <CardHeader
        title={`What the week-${d.week} digest was built from`}
        sub={l.source === 'pipeline_run' ? "lineage · from the run's used artifacts" : l.source === 'aliases' ? 'lineage · from the week aliases' : 'lineage'}
      />
      <div className="card-b ml-stack">
        {l.items.length ? (
          <div className="lineage" role="list" aria-label="Lineage">
            {l.items.map((it, i) => (
              <Fragment key={`${it.role}-${it.name}`}>
                {i > 0 ? (
                  <span className="arr" aria-hidden="true">
                    →
                  </span>
                ) : null}
                <span className={`art${it.role === 'published' ? ' pub' : ''}`} role="listitem">
                  <b>{it.role}</b>
                  {it.name}
                  {it.version ? `:${it.version}` : ''}
                </span>
              </Fragment>
            ))}
          </div>
        ) : (
          <span className="muted">
            {d.wandb.available ? 'No lineage was recorded for this week.' : "The lineage lives in W&B, which isn't available right now."}
          </span>
        )}
        {l.note || l.pipeline_run ? (
          <span className="ml-note tight">
            <NoteWithRun note={l.note} runId={l.pipeline_run} base={projectOf(d)} />
          </span>
        ) : null}
      </div>
    </Card>
  );
}

function AliasChips({ aliases }: { aliases: string[] }) {
  // production first, then the week alias and the rest, then latest
  const plain = aliases.filter((a) => a !== 'latest').sort((a, b) => Number(b === 'production') - Number(a === 'production'));
  return (
    <>
      {plain.map((a) => (
        <Fragment key={a}>
          <Chip tone={a === 'production' ? 'run' : 'flat'}>{a}</Chip>{' '}
        </Fragment>
      ))}
      {aliases.includes('latest') ? <Chip tone="ghost">latest</Chip> : null}
    </>
  );
}

function CollectionCard({ c }: { c: ArtifactCollection }) {
  const [all, setAll] = useState(false);
  const shown = all ? c.versions : c.versions.slice(0, FIRST_VERSIONS);
  const older = Math.max(c.total, c.versions.length) - FIRST_VERSIONS;
  const loadedOlder = c.versions.length - FIRST_VERSIONS;
  return (
    <Card>
      <CardHeader
        title={<span className="ml-title-mono">{c.name}</span>}
        sub={
          <>
            <Chip>{c.type}</Chip> {c.note}
          </>
        }
        right={
          c.url ? (
            <ExtLink className="btn sm" href={c.url}>
              W&amp;B ↗
            </ExtLink>
          ) : undefined
        }
      />
      {c.versions.length === 0 ? (
        <EmptyBlock glyph="—">No versions yet. {c.first_note ?? ''}</EmptyBlock>
      ) : (
        <>
          <div className="tablewrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Created (ET)</th>
                  <th>Aliases</th>
                  <th>Logged by</th>
                  <th className="r">Size</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((v) => (
                  <tr key={v.version}>
                    <td className="mono">
                      <b>{v.version}</b>
                    </td>
                    <td className="num">{fmtET(v.created_at, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</td>
                    <td>
                      <AliasChips aliases={v.aliases} />
                    </td>
                    <td>
                      {v.logged_by ? (
                        <ExtLink className="mono" href={v.logged_by_url} label={`${v.logged_by} in W&B`}>
                          {v.logged_by} ↗
                        </ExtLink>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="r num">{bytes(v.size)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {older > 0 ? (
            <div className="wk-more">
              {loadedOlder > 0 ? (
                <button type="button" className="btn sm" aria-expanded={all} onClick={() => setAll(!all)}>
                  {all ? `Show the newest ${FIRST_VERSIONS}` : `${older} older ${older === 1 ? 'version' : 'versions'}`}
                </button>
              ) : (
                <span className="muted">
                  {older} older {older === 1 ? 'version' : 'versions'}
                </span>
              )}
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}

const LOCAL_NAME: Record<string, string> = { game: 'game-model', player: 'player-model', team: 'team-model' };

function LocalModels({ d }: { d: Artifacts }) {
  const entries = Object.entries(d.local_models ?? {});
  return (
    <Card>
      <CardHeader title="Recorded by this week's run" sub="from run_summary.json, without W&B" />
      <div className="card-b">
        {entries.length ? (
          <dl className="kv">
            {entries.map(([name, versions]) => (
              <Fragment key={name}>
                <dt className="mono">{LOCAL_NAME[name] ?? name}</dt>
                <dd className="mono">{versions.length ? versions.join(', ') : '—'}</dd>
              </Fragment>
            ))}
          </dl>
        ) : (
          <span className="muted">The run didn&apos;t record any model versions, and W&amp;B isn&apos;t available to ask.</span>
        )}
        <p className="ml-note">Aliases, creation times, sizes and the lineage come back when W&amp;B does.</p>
      </div>
    </Card>
  );
}

export function ArtifactsSection({ season, week }: { season: number; week: number }) {
  const q = useMlopsArtifacts(season, week);
  if (q.isPending) return <TabLoading />;
  if (q.isError) return <TabError error={q.error} />;
  const d = q.data;
  return (
    <div className="wk-stack">
      <WandbBanner
        state={d.wandb}
        refreshing={q.isFetching}
        onRefresh={() => {
          refreshWandb(mlopsPath(season, week, 'artifacts'));
          void q.refetch();
        }}
      />
      <div className="tiles">
        {PRODUCTION_TILES.map((n) => (
          <ProductionTile key={n} name={n} d={d} />
        ))}
        <Tile
          k="Versions in all"
          v={d.total_versions ?? '—'}
          d={d.collections_count != null ? `across ${d.collections_count} live artifacts` : undefined}
        />
      </div>
      <Lineage d={d} />
      {d.wandb.available ? (
        <div className="grid g2">
          {d.collections.map((c) => (
            <CollectionCard key={c.name} c={c} />
          ))}
        </div>
      ) : (
        <LocalModels d={d} />
      )}
      <div className="foot">Read from the W&amp;B API on the server (read-only, cached). The W&amp;B key never reaches the browser.</div>
    </div>
  );
}
