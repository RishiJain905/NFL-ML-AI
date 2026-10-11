// The Play calls tab (PC01; mockup: weekView): the week's matchups of tendencies, descriptive for
// now (PC02 adds the forecast). The week's biggest matchup shifts (the server's sentences), then
// each game with two columns: each offense against the other defense, one row per metric: what
// the offense does, what the defense allows (or, for a defense's call, calls), the league.

import { Link } from 'react-router-dom';
import { usePlayCalls } from '../../api/client';
import type { PlayCallsGame, PlayCallsMatchup, PlayCallsResponse, PlaycallMetric } from '../../api/types';
import { MatchStrip } from '../../charts/MatchStrip';
import { TipTarget } from '../../charts/TipTarget';
import { TeamChip } from '../../components/TeamChip';
import { Card, CardHeader, Notice } from '../../components/ui';
import { kickoffLabel } from '../../lib/format';
import { ALLOWED_NOTE, fmtRate, perWord } from '../../lib/playcall';
import { PlaycallEmpty } from '../explore/common';
import { TabError, TabLoading, WarnNotice } from './WeekEmpty';

const MINUS = '−';
const sd = (x: number) => `${x > 0 ? '+' : x < 0 ? MINUS : ''}${Math.abs(x).toFixed(1)} SD`;

function MatchupRow({ m, row, mu }: { m: PlaycallMetric; row: PlayCallsMatchup['rows'][number]; mu: PlayCallsMatchup }) {
  const defCall = m.caller === 'defense';
  const o = row.offense;
  const d = row.defense;
  const solid = o != null && d != null && !o.small && !d.small;
  const cellText = (c: typeof o) => (c ? ` (${c.n} ${perWord(m.per, c.n)}${c.small ? ', small' : ''})` : '');
  const tip = [
    m.label,
    `${mu.offense} ${defCall ? 'has faced' : 'does'}: ${fmtRate(m, o?.value)}${cellText(o)}`,
    `${mu.defense} ${defCall ? 'calls' : 'allows'}: ${fmtRate(m, d?.value)}${cellText(d)}`,
    `League: ${fmtRate(m, row.league)}`,
    // the interpretive lines only when both rates rest on enough plays (Sol review, PC01)
    ...(solid && row.shift != null ? [`${mu.defense} sits ${sd(row.shift)} from the league's defenses`] : []),
    ...(solid && row.same_way != null ? [row.same_way ? 'Both sit on the same side of the league' : 'They pull opposite ways'] : []),
    ...(defCall ? [] : [ALLOWED_NOTE]),
  ];
  // marked: 2+ SD from the league's defenses, and never on a small sample
  const big = solid && row.shift != null && Math.abs(row.shift) >= 2;
  return (
    <tr className={big ? 'bigshift' : undefined}>
      <th scope="row">
        {/* the row's tooltip target at every width (the strip hides on a phone) */}
        <TipTarget as="div" className="mname" lines={tip} label={[m.short, ...tip].join(', ')}>
          {m.short}
          {defCall ? <small>defense's call</small> : null}
        </TipTarget>
      </th>
      <td className={`r num${o?.small ? ' smallv' : ''}`}>
        {fmtRate(m, o?.value)}
        <small>{o ? `n ${o.n}` : ''}</small>
      </td>
      <td className={`r num${d?.small ? ' smallv' : ''}`}>
        {fmtRate(m, d?.value)}
        <small>{d ? `n ${d.n}` : ''}</small>
      </td>
      <td className="r num muted">{fmtRate(m, row.league)}</td>
      <td className="mviz">
        <MatchStrip metric={m} offense={o} defense={d} league={row.league} tip={tip} />
      </td>
    </tr>
  );
}

function Matchup({ mu, metrics }: { mu: PlayCallsMatchup; metrics: Map<string, PlaycallMetric> }) {
  return (
    <section aria-label={`${mu.offense} offense against ${mu.defense} defense`}>
      <h3 className="mh">
        <TeamChip team={mu.offense} /> offense <span className="muted">vs</span> <TeamChip team={mu.defense} /> defense
      </h3>
      <div className="tablewrap">
        <table className="tbl mtbl">
          <thead>
            <tr>
              <th />
              <th className="r">{mu.offense} does</th>
              <th className="r">{mu.defense} allows</th>
              <th className="r">League</th>
              <th className="mviz" />
            </tr>
          </thead>
          <tbody>
            {mu.rows.map((row) => {
              const m = metrics.get(row.metric);
              return m ? <MatchupRow key={row.metric} m={m} row={row} mu={mu} /> : null;
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Game({ g, metrics, season }: { g: PlayCallsGame; metrics: Map<string, PlaycallMetric>; season: number }) {
  const page = (t: string) => ({ pathname: `/explore/play-calling/${t}`, search: `?season=${season}` });
  return (
    <Card className="pcgame">
      <CardHeader
        title={
          <>
            <TeamChip team={g.away} full /> <span className="at">at</span> <TeamChip team={g.home} full />
          </>
        }
        sub={g.kickoff ? kickoffLabel(g.kickoff) : undefined}
        right={
          <>
            <Link className="btn sm" to={page(g.away)}>
              {g.away} page
            </Link>
            <Link className="btn sm" to={page(g.home)}>
              {g.home} page
            </Link>
          </>
        }
      />
      <div className="pcmatch">
        {g.matchups.map((mu) => (
          <Matchup key={mu.offense} mu={mu} metrics={metrics} />
        ))}
      </div>
    </Card>
  );
}

function Calls({ r }: { r: PlayCallsResponse }) {
  const metrics = new Map(r.metrics.map((m) => [m.metric, m]));
  return (
    <>
      <Notice tone="accent">{r.note}</Notice>
      {r.ftn_waiting.length ? (
        <WarnNotice>
          <b>FTN hasn't charted {r.ftn_waiting.length === 1 ? 'one game' : `${r.ftn_waiting.length} games`} yet:</b> {r.ftn_waiting.join(' · ')}.
          Play-action, screens, RPO, motion, blitz and box rates wait for it.
        </WarnNotice>
      ) : null}
      {r.shifts.length ? (
        <Card>
          <CardHeader title="Biggest matchup shifts" sub="where a defense allows or calls something far from the league · in standard deviations of the 32 defenses" />
          <ol className="shifts">
            {r.shifts.map((s) => (
              <li key={`${s.game_id}-${s.offense}-${s.metric}`}>
                <span className="shm" aria-hidden="true">
                  <span className="shb" style={{ width: `${Math.min(100, (Math.abs(s.shift) / 3) * 100).toFixed(0)}%` }} />
                </span>
                <span>{s.text}</span>
                <span className="num muted shv">{sd(s.shift)}</span>
              </li>
            ))}
          </ol>
        </Card>
      ) : null}
      <div className="sec-h">
        <h2>Every game</h2>
        <p>
          {r.window === 'season' ? `${r.season} season before week ${r.week}` : `${r.season - 1} season`} · each offense against the other defense
        </p>
        <div className="right">
          <span className="legend">
            <span>
              <i style={{ background: 'var(--s1)' }} />
              offense does
            </span>
            <span>
              <i style={{ background: 'var(--s2)' }} />
              defense allows
            </span>
            <span>
              <i className="line" style={{ background: 'var(--ink-2)' }} />
              league
            </span>
          </span>
        </div>
      </div>
      <div className="pcgames">
        {r.games.map((g) => (
          <Game key={g.game_id} g={g} metrics={metrics} season={r.season} />
        ))}
      </div>
    </>
  );
}

export function PlayCallsTab({ season, week }: { season: number; week: number; isCurrent?: boolean; lastPublishedWeek?: number | null }) {
  const q = usePlayCalls(season, week);
  if (q.isLoading) return <TabLoading />;
  if (q.error) return <TabError error={q.error} />;
  const r = q.data;
  if (!r) return null;
  if (r.status === 'not_built') return <PlaycallEmpty meta={r} title="No play-calling tables yet" />;
  if (r.status === 'not_yet') return <PlaycallEmpty meta={r} title={`Week ${week}'s matchups aren't ready yet`} />;
  // built, but the schedule has no games this week (the server says so in `message`)
  if (!r.games.length) return <PlaycallEmpty meta={r} title={`No week-${week} games`} />;
  return <Calls r={r} />;
}
