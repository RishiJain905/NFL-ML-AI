// The banner under `nfl app --rehearsal` (CR02): the Run button rehearses a past week into a
// scratch folder instead of running the live week.

import { usePreflight } from '../../api/client';
import { Notice } from '../ui';

export function RehearsalBanner() {
  const pf = usePreflight().data;
  const week = pf?.mode === 'rehearsal' && pf.week != null ? `week ${pf.week}` : 'a past week';
  return (
    <Notice icon="R" tone="accent">
      <b>Rehearsal mode.</b> The Run button rehearses {week} (<code>nfl weekly rehearse</code>) into
      a scratch folder (<code>rehearsals/control-room</code>); nothing live is touched, no W&amp;B,
      no graph.
    </Notice>
  );
}
