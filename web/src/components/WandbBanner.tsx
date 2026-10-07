// The W&B state above a W&B-backed section (CR03). W&B is read on the server and cached; when it
// can't be reached the banner says why and the page still shows everything local. Nothing is
// rendered while W&B answered with fresh data.

import type { WandbState } from '../api/types';
import { fmtET } from '../lib/format';

export function WandbBanner({
  state,
  onRefresh,
  refreshing,
}: {
  state: WandbState | undefined;
  onRefresh?: () => void;
  refreshing?: boolean;
}) {
  if (!state || (state.available && !state.stale)) return null;
  const when = state.fetched_at
    ? fmtET(state.fetched_at, { weekday: 'short', hour: 'numeric', minute: '2-digit' })
    : null;
  return (
    <div className="notice wandb-banner" role="status">
      <span className="ic" aria-hidden="true">
        {state.available ? 'i' : '!'}
      </span>
      <div>
        {state.available ? (
          <>
            <b>W&amp;B can&apos;t be reached right now</b>: showing what it said
            {when ? ` at ${when}` : ' earlier'}.{' '}
          </>
        ) : (
          <>
            <b>W&amp;B isn&apos;t available</b>: links, versions and aliases are missing; everything
            from the local files is still here.{' '}
          </>
        )}
        {state.reason ? <span className="muted">{state.reason}</span> : null}
      </div>
      {onRefresh ? (
        <button
          type="button"
          className="btn sm"
          style={{ marginLeft: 'auto', alignSelf: 'center' }}
          onClick={onRefresh}
          disabled={refreshing}
        >
          {refreshing ? 'Trying…' : 'Try again'}
        </button>
      ) : null}
    </div>
  );
}
