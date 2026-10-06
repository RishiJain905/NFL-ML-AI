// A team's colour dot + abbreviation or nickname (mockup: tchip). Falls back to the
// abbreviation and a neutral colour while the team list loads or for an unknown code.

import { useTeamInfo } from '../api/client';

const NEUTRAL = '#888888';

export function TeamChip({ team, full }: { team: string; full?: boolean }) {
  const info = useTeamInfo().data?.teams[team];
  return (
    <span className="team" title={info?.name}>
      <i style={{ background: info?.color ?? NEUTRAL }} aria-hidden="true" />
      <b>{full ? (info?.nick ?? team) : team}</b>
    </span>
  );
}

/** Just the nickname as text ("Commanders"), for sentences. */
export function TeamNick({ team }: { team: string }) {
  const info = useTeamInfo().data?.teams[team];
  return <>{info?.nick ?? team}</>;
}
