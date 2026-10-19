// The Graph lab's shared bits (AE03): a view's "Ran …" line (the W&B link, the seed, the amber
// "made on older person links"), the not-run-yet card (404 `not_built`, the command to run) and
// the loading / error blocks.

import { ApiError } from '../../../api/client';
import type { LabAsOf } from '../../../api/types';
import { TipTarget } from '../../../charts/TipTarget';
import { splitCommand, when } from '../../../lib/graphlab';

const paramLine = ([k, v]: [string, unknown]) =>
  `${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`;

export function LabAsOfLine({ asof, job }: { asof: LabAsOf | null | undefined; job: string }) {
  if (!asof) return null;
  const params = Object.entries(asof.params ?? {});
  const seed = asof.params && 'seed' in asof.params ? String(asof.params.seed) : null;
  return (
    <>
      <div className="gl-asof">
        <TipTarget
          className="chip flat"
          lines={[
            'This view as of',
            `Ran ${when(asof.ran_at)}${asof.derive_id ? ` on the person links ${asof.derive_id}` : ''}`,
            ...params.map(paramLine),
          ]}
          label={`Ran ${when(asof.ran_at)}`}
        >
          <span className="k" style={{ color: 'var(--ink-3)' }}>
            Ran
          </span>
          &nbsp;{when(asof.ran_at)}
        </TipTarget>
        {asof.wandb_url ? (
          <a href={asof.wandb_url} target="_blank" rel="noopener noreferrer">
            W&amp;B run ↗
          </a>
        ) : null}
        {seed != null ? <span>seed {seed}</span> : null}
      </div>
      {!asof.current ? (
        <div className="notice ask-banner warn" role="status">
          <span className="ic" aria-hidden="true">
            !
          </span>
          <div>
            <b>Made on older person links.</b> The links were made again after this {job} run;
            re-run <span className="gl-cmd">uv run nfl history-graph lab</span> to bring it up to
            date.
          </div>
        </div>
      ) : null}
    </>
  );
}

/** A lab view that hasn't run (404 `not_built`) or any other error. */
export function LabError({ error, what }: { error: Error; what: string }) {
  const e = error instanceof ApiError ? error : null;
  if (e && e.status === 404 && e.code === 'not_built') {
    const { text, command } = splitCommand(e.message);
    return (
      <div className="card">
        <div className="empty">
          <div className="glyph" aria-hidden="true">
            {what === 'benchmark' ? 'ms' : 'lab'}
          </div>
          <h3>{what === 'benchmark' ? 'The benchmark hasn’t run yet' : 'Not run yet'}</h3>
          <p>{text}</p>
          {command ? <div className="cmd">{command}</div> : null}
        </div>
      </div>
    );
  }
  return (
    <div className="notice ask-banner err" role="alert">
      <span className="ic" aria-hidden="true">
        !
      </span>
      <div>
        <b>Couldn’t load the {what}.</b>{' '}
        {e ? e.message : 'The app didn’t answer: is `nfl app` still running?'}
        {e ? <span className="muted"> (HTTP {e.status})</span> : null}
      </div>
    </div>
  );
}

export function LabLoading({ what }: { what: string }) {
  return (
    <div className="card pad sk">
      <div role="status" aria-busy="true" aria-label={`Loading ${what}`}>
        <span className="skl w40" />
        <span className="skl w70" />
        <span className="skl w55" />
      </div>
    </div>
  );
}
