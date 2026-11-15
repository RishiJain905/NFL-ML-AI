// The Availability tab's parts (FL02, D158; mockup: documentation/forecast-lab/mockup/
// availability.js, published at https://claude.ai/artifact/NiZt8wAJn6jqL1RWFwxDZU): the might-sit
// list in three groups, the biggest role changes, every player of one team, how it did after the
// games, and the backtest's "how good is this".

import { useState } from 'react';
import type { AvailabilityAtRisk, AvailabilityMover, AvailabilityPlayer, AvailabilityResponse, AvailabilityScoreRow } from '../../../api/types';
import { Chip } from '../../../components/ui';
import { PRACTICE_WORDS, ROSTER_WORDS, SHARE_KIND, inheritByPlayer, pctText, ptsText, shareText, whereText } from '../../../lib/forecastlab';

const STATUS_TONE: Record<string, 'err' | 'warn'> = { Out: 'err', Doubtful: 'warn', Questionable: 'warn' };
/** Rows of the regulars' group shown before "Show all". */
const LIMIT = 15;

function StatusChips({ r }: { r: AvailabilityPlayer }) {
  const chips = [];
  if (r.report_status) {
    chips.push(
      <Chip key="r" tone={STATUS_TONE[r.report_status]}>
        {r.report_status}
      </Chip>,
    );
  }
  if (r.practice_status) {
    chips.push(
      <Chip key="p" tone="flat">
        {PRACTICE_WORDS[r.practice_status] ?? r.practice_status}
      </Chip>,
    );
  }
  if (r.prev_status) {
    chips.push(
      <Chip key="l" tone="ghost">
        {r.prev_status} last week
      </Chip>,
    );
  }
  if (r.roster_status && ROSTER_WORDS[r.roster_status]) {
    chips.push(
      <Chip key="s" tone="ghost">
        {ROSTER_WORDS[r.roster_status]}
      </Chip>,
    );
  }
  return chips.length ? <div className="av-chips">{chips}</div> : null;
}

/** "Played: 61% of the snaps" / "Didn't play", once his game is over. */
function After({ r }: { r: AvailabilityPlayer }) {
  if (r.played == null) return null;
  return (
    <div className="av-after">
      {r.played ? (
        <>
          <b>Played</b>
          {r.actual_snap != null ? `: ${shareText(r.actual_snap)} of the snaps` : ''}
        </>
      ) : (
        <b>Didn&apos;t play</b>
      )}
    </div>
  );
}

function RiskRow({ r }: { r: AvailabilityAtRisk }) {
  const p = r.p_play ?? r.base_p_play;
  const moved = r.view === 'saturday' && r.p_play_tuesday != null;
  const sits = inheritByPlayer(r.inherit);
  return (
    <li className="av-row">
      <div className="who">
        <b>{r.player ?? r.player_id}</b>
        <small>
          {r.position} · {whereText(r)}
        </small>
        <StatusChips r={r} />
      </div>
      <div className="av-chance" aria-label={`Chance he plays ${pctText(p)}`}>
        <span className="big">
          {pctText(p)}
          <small>to play · usual for his status {pctText(r.base_p_play)}</small>
        </span>
        <span className="av-meter" aria-hidden="true">
          <b style={{ width: `${(p ?? 0) * 100}%` }} />
          <i style={{ left: `${(r.base_p_play ?? 0) * 100}%` }} />
        </span>
        {moved ? (
          <span className="move">
            Tuesday {pctText(r.p_play_tuesday)} → Saturday {pctText(r.p_play)}
          </span>
        ) : null}
      </div>
      <div className="av-role">
        {r.snap_p50 != null ? (
          <>
            If he plays: <b>{shareText(r.snap_p50)}</b> of the snaps{' '}
            <span className="muted">
              (80%: {shareText(r.snap_p10)}–{shareText(r.snap_p90)})
            </span>{' '}
            · usually {shareText(r.base_snap)}
          </>
        ) : (
          <>Usually {shareText(r.base_snap)} of the snaps</>
        )}
      </div>
      {sits.length ? (
        <div className="av-inh">
          <span className="lbl">If he sits:</span>
          {sits.map(({ first, gains }) => (
            <span key={first.player_id}>
              <b>{first.player}</b> {first.position} {gains.map((g) => `${ptsText(g.delta)} ${SHARE_KIND[g.kind] ?? g.kind}`).join(', ')}
            </span>
          ))}
        </div>
      ) : null}
      <After r={r} />
    </li>
  );
}

export function RiskSections({ r }: { r: AvailabilityResponse }) {
  const [all, setAll] = useState(false);
  const t = r.tiles;
  const group = (g: AvailabilityAtRisk['risk_group']) => r.at_risk.filter((x) => x.risk_group === g);
  const report = group('report');
  const uncertain = group('uncertain');
  const reserve = group('reserve');
  const shown = all ? uncertain : uncertain.slice(0, LIMIT);
  // the list is capped by the reader: a group may count more than it lists
  const n = (g: AvailabilityAtRisk['risk_group'], listed: number) => t?.by_group[g] ?? listed;
  return (
    <>
      <section className="card av-sec" aria-label="On this week's injury report">
        <div className="card-h">
          <h2>On this week&apos;s injury report</h2>
          <span className="n">{n('report', report.length)}</span>
          <span className="muted">Out, Doubtful or Questionable, the most playing time at stake first</span>
        </div>
        {report.length ? (
          <ul className="av-rows">
            {report.map((x) => (
              <RiskRow key={`${x.player_id}-${x.game_id}`} r={x} />
            ))}
          </ul>
        ) : (
          <div className="av-empty">
            {t?.view === 'tuesday'
              ? "No report yet: the first one comes out Wednesday, and Saturday's view reads the final one."
              : "Nobody with a regular role is on this week's report."}
          </div>
        )}
      </section>
      <section className="card av-sec" aria-label="Regulars with a low chance">
        <div className="card-h">
          <h2>Regulars with a low chance</h2>
          <span className="n">{n('uncertain', uncertain.length)}</span>
          <span className="muted">
            played at least {shareText(t?.role_floor ?? 0.3)} of the snaps lately · the most playing time at stake first
          </span>
        </div>
        {uncertain.length ? (
          <ul className="av-rows">
            {shown.map((x) => (
              <RiskRow key={`${x.player_id}-${x.game_id}`} r={x} />
            ))}
          </ul>
        ) : (
          <div className="av-empty">Nobody this week.</div>
        )}
        {shown.length < uncertain.length ? (
          <div className="av-more">
            <button type="button" className="btn sm" onClick={() => setAll(true)}>
              Show all {uncertain.length}
            </button>
          </div>
        ) : null}
      </section>
      {reserve.length ? (
        <details className="card av-sec">
          <summary>
            <div className="card-h">
              <h2>On a reserve list</h2>
              <span className="n">{n('reserve', reserve.length)}</span>
              <span className="muted">not expected back this week · kept for who took their work</span>
            </div>
          </summary>
          <ul className="av-rows">
            {reserve.map((x) => (
              <RiskRow key={`${x.player_id}-${x.game_id}`} r={x} />
            ))}
          </ul>
        </details>
      ) : null}
    </>
  );
}

function MoverBox({
  title,
  list,
  model,
  base,
}: {
  title: string;
  list: AvailabilityMover[];
  model: keyof AvailabilityPlayer;
  base: keyof AvailabilityPlayer;
}) {
  return (
    <div className="card pad av-mv">
      <h3>{title}</h3>
      <ol>
        {list.slice(0, 6).map((x) => (
          <li key={`${x.player_id}-${x.game_id}`}>
            <span>
              <b>{x.player}</b>
              <small>
                {x.position} · {x.team}
              </small>
            </span>
            <span className="ft">
              {shareText(x[base] as number | null)} → {shareText(x[model] as number | null)}
            </span>
            <span className="d">{ptsText(x.change)}</span>
          </li>
        ))}
        {list.length === 0 ? <li className="muted">Nobody this week</li> : null}
      </ol>
    </div>
  );
}

export function Movers({ r }: { r: AvailabilityResponse }) {
  return (
    <section aria-label="Biggest role changes" className="wk-stack" style={{ gap: 12 }}>
      <div className="sec-h">
        <h2>Biggest role changes</h2>
        <p>the share if he plays against his usual (his last two games for snaps, his rolling share for targets and carries)</p>
      </div>
      <div className="av-movers">
        <MoverBox title="More snaps" list={r.movers.snap_up} model="snap_p50" base="base_snap" />
        <MoverBox title="Fewer snaps" list={r.movers.snap_down} model="snap_p50" base="base_snap" />
        <MoverBox title="More of the targets" list={r.movers.targets_up} model="tgt_share" base="base_tgt_share" />
        <MoverBox title="More of the carries" list={r.movers.carries_up} model="carry_share" base="base_carry_share" />
      </div>
    </section>
  );
}

export function EveryPlayer({
  teams,
  team,
  players,
  loading,
  onTeam,
}: {
  teams: string[];
  team: string | null;
  players: AvailabilityPlayer[];
  loading: boolean;
  onTeam: (t: string) => void;
}) {
  const played = players.some((x) => x.played != null);
  const cols = played ? 7 : 6;
  const side = (s: string, label: string) => {
    const rows = players.filter((x) => x.side === s);
    if (!rows.length) return null;
    return (
      <>
        <tr className="av-side">
          <td colSpan={cols}>{label}</td>
        </tr>
        {rows.map((x) => (
          <tr key={`${x.player_id}-${x.game_id}`} className={x.at_risk ? 'risk' : undefined}>
            <td>
              <b>{x.player}</b>
              <small>{x.position}</small>
              {x.at_risk ? <span className="sr-only"> (might sit)</span> : null}
            </td>
            <td className="r num">
              {pctText(x.p_play)}
              <small>{pctText(x.base_p_play)}</small>
            </td>
            <td className="r num">
              {shareText(x.snap_p50)}
              <small className="wide">
                {shareText(x.snap_p10)}–{shareText(x.snap_p90)}
              </small>
            </td>
            <td className="r num">{shareText(x.base_snap)}</td>
            <td className="r num">
              {x.tgt_share != null ? (
                <>
                  {shareText(x.tgt_share)}
                  <small>{shareText(x.base_tgt_share)}</small>
                </>
              ) : (
                '—'
              )}
            </td>
            <td className="r num">
              {x.carry_share != null ? (
                <>
                  {shareText(x.carry_share)}
                  <small>{shareText(x.base_carry_share)}</small>
                </>
              ) : (
                '—'
              )}
            </td>
            {played ? (
              <td className="r num">{x.played == null ? '—' : x.played ? shareText(x.actual_snap) : "didn't play"}</td>
            ) : null}
          </tr>
        ))}
      </>
    );
  };
  return (
    <section className="card" aria-label="Every player">
      <div className="card-h">
        <h2>Every player</h2>
        <span className="muted">{team ? `${team} · the marked rows might sit` : 'one team at a time'}</span>
      </div>
      <div className="card-b">
        <div className="av-teams" role="group" aria-label="Team">
          {teams.map((x) => (
            <button key={x} type="button" className="av-team" aria-pressed={x === team} onClick={() => onTeam(x)}>
              {x}
            </button>
          ))}
        </div>
      </div>
      {!team ? (
        <div className="card-b muted">Pick a team to see every player: the chance he plays and his shares against his usual.</div>
      ) : loading && !players.length ? (
        <div className="card-b muted" role="status">
          Loading {team}…
        </div>
      ) : (
        <div className="tablewrap">
          <table className="tbl av-tbl">
            <thead>
              <tr>
                <th>Player</th>
                <th className="r">
                  Plays
                  <br />
                  <small className="muted">model · usual</small>
                </th>
                <th className="r">
                  Snaps if he plays
                  <br />
                  <small className="muted">and 80% range</small>
                </th>
                <th className="r">Usually</th>
                <th className="r">
                  Targets
                  <br />
                  <small className="muted">model · usual</small>
                </th>
                <th className="r">
                  Carries
                  <br />
                  <small className="muted">model · usual</small>
                </th>
                {played ? <th className="r">Played</th> : null}
              </tr>
            </thead>
            <tbody>
              {side('off', 'Offense')}
              {side('def', 'Defense')}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const OUTPUT_WORDS: Record<string, string> = { play: 'Will he play', snap: 'Snap share', tgt_share: 'Target share', carry_share: 'Carry share' };

export function AvailScoreCard({ r }: { r: AvailabilityResponse }) {
  const [period, setPeriod] = useState<'week' | 'season' | null>(null);
  const { week, season } = r.scoreboard;
  if (!week.length && !season.length) return null;
  const pick = period ?? (week.length ? 'week' : 'season');
  const rows = (pick === 'week' ? week : season).filter((x) => x.group === 'ALL');
  const fmt = (x: AvailabilityScoreRow, v: number | null) =>
    v == null ? '—' : x.output === 'play' ? v.toFixed(3) : `${(v * 100).toFixed(1)} pts`;
  return (
    <section className="card av-score" aria-label="How it did">
      <div className="card-h">
        <h2>How it did</h2>
        <span className="muted">lower is better · in bold when one is clearly lower</span>
        <div className="right">
          <span className="seg" role="group" aria-label="Period">
            <button type="button" aria-pressed={pick === 'week'} onClick={() => setPeriod('week')}>
              This week
            </button>
            <button type="button" aria-pressed={pick === 'season'} onClick={() => setPeriod('season')}>
              Season to date
            </button>
          </span>
        </div>
      </div>
      <div className="tablewrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Output</th>
              <th>View</th>
              <th className="r">Players</th>
              <th className="r">Model</th>
              <th className="r">Baseline</th>
              <th className="r">80% range held</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => {
              const m = x.output === 'play' ? x.brier_model : x.mae_model;
              const b = x.output === 'play' ? x.brier_baseline : x.mae_baseline;
              const best = m != null && b != null && m <= b * 0.98;
              return (
                <tr key={`${x.view}-${x.output}`}>
                  <td>{OUTPUT_WORDS[x.output] ?? x.output}</td>
                  <td>{x.view === 'saturday' ? 'Saturday' : 'Tuesday'}</td>
                  <td className="r num">{x.n.toLocaleString('en-US')}</td>
                  <td className={`r num${best ? ' best' : ''}`}>{fmt(x, m)}</td>
                  <td className="r num">{fmt(x, b)}</td>
                  <td className="r num">{x.coverage_80 == null ? '—' : pctText(x.coverage_80)}</td>
                </tr>
              );
            })}
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="muted">
                  Nothing graded {pick === 'week' ? 'this week yet' : 'yet this season'}.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <div className="card-b muted" style={{ fontSize: 12 }}>
        Will he play: the Brier score against the usual rate for his status. Shares: how far off, in points, against his usual share.
      </div>
    </section>
  );
}

export function HowGood() {
  return (
    <section className="card" aria-label="How good is this">
      <div className="card-h">
        <h2>How good is this</h2>
        <span className="muted">the backtest, 2019–2025: the model beat its baseline in every season</span>
      </div>
      <div className="card-b av-good">
        <div>
          <b>0.107 vs 0.149</b>Will he play, Tuesday: the error (Brier) against the usual rate for his status; lower is better
        </div>
        <div>
          <b>0.069 vs 0.116</b>The same on Saturday, with the final report
        </div>
        <div>
          <b>12.0 vs 13.1 points</b>Snap share if he plays: off by, against his last two games
        </div>
        <div>
          <b>80–81%</b>of the time the snap share landed in its 80% range
        </div>
      </div>
    </section>
  );
}
