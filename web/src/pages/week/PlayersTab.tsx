// The Players tab (mockup: w4Players): the watch-list cards (projection, 80% range with the
// baseline mark, main driver, injury), tough spots, and every projection in a table with group
// filters and mini range bars; "Show every stat" switches to the all-stats endpoint.

import { useState } from 'react';
import { usePlayers, useTeamInfo } from '../../api/client';
import type { PlayersResponse, ProjectionRow, WatchPick } from '../../api/types';
import { RangeBar } from '../../charts/RangeBar';
import { TeamChip } from '../../components/TeamChip';
import { Card, CardHeader, Chip } from '../../components/ui';
import { comma, fixed, pct } from '../../lib/format';
import { TabError, TabLoading, WeekEmpty } from './WeekEmpty';
import { deltaText, projText, statNum } from './weekUtil';
import './week.css';

const FIRST_ROWS = 40;

const diff = (a: number | null, b: number | null) => (a == null || b == null ? null : a - b);

function deltaClass(v: number | null): string {
  return (v ?? 0) >= 0 ? 'delta-up' : 'delta-down';
}

function WatchCard({ r, nick }: { r: WatchPick; nick: (t: string) => string }) {
  return (
    <div className="wcard">
      <div className="wh">
        <span className="wn">{r.player}</span>
        {r.rank != null ? <Chip>#{r.rank}</Chip> : null}
      </div>
      <span className="wm">
        <TeamChip team={r.team} /> {r.position} · {r.home ? 'vs' : 'at'} {nick(r.opponent)}
        {r.injury ? (
          <>
            {' · '}
            <span style={{ color: 'var(--warn)', fontWeight: 600 }}>{r.injury}</span>
          </>
        ) : null}
      </span>
      <span className="wp">
        {projText(r)}
        <small>{r.target_label}</small>
      </span>
      <RangeBar who={r.player} p10={r.p10} p90={r.p90} projection={r.projection} baseline={r.baseline} />
      <span className="why">
        <b className={deltaClass(r.vs_baseline)}>{deltaText(r.vs_baseline)}</b> vs his baseline {fixed(r.baseline, 1)} ·{' '}
        {r.driver ?? 'no single factor stands out'}
      </span>
    </div>
  );
}

function WatchList({ d, nick }: { d: PlayersResponse; nick: (t: string) => string }) {
  const sided = d.watch.some((w) => w.side);
  const grid = (picks: WatchPick[]) => (
    <div className="watch">
      {picks.map((r) => (
        <WatchCard key={`${r.player_id}-${r.target}`} r={r} nick={nick} />
      ))}
    </div>
  );
  if (!sided) return grid(d.watch);
  return (
    <>
      {(['offense', 'defense'] as const).map((side) => {
        const picks = d.watch.filter((w) => w.side === side);
        if (!picks.length) return null;
        return (
          <section key={side} className="wk-stack" style={{ gap: 10 }} aria-label={side === 'offense' ? 'Offense' : 'Defense'}>
            <h3 className="sub-h">{side === 'offense' ? 'Offense' : 'Defense'}</h3>
            {grid(picks)}
          </section>
        );
      })}
    </>
  );
}

function ToughSpots({ d, nick }: { d: PlayersResponse; nick: (t: string) => string }) {
  if (!d.tough_spots.length) return null;
  return (
    <Card>
      <CardHeader title="Tough spots" sub="projected well below their own baseline" />
      <div className="card-b">
        <div className="grid g3">
          {d.tough_spots.map((t) => (
            <div key={`${t.player_id}-${t.target_label}`}>
              <b>{t.player}</b>{' '}
              <span className="muted">
                {t.position} · <TeamChip team={t.team} /> vs {nick(t.opponent)}
              </span>
              <div style={{ marginTop: 4, fontSize: 13 }} title={`range ${t.display.range} · ${t.display.vs_baseline}`}>
                {t.display.projection} projected · baseline {statNum(t.baseline)}{' '}
                <b className={deltaClass(diff(t.projection, t.baseline))}>{deltaText(diff(t.projection, t.baseline))}</b>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function ProjectionTable({
  d,
  stats,
  setStats,
  loading,
}: {
  d: PlayersResponse;
  stats: 'main' | 'all';
  setStats: (s: 'main' | 'all') => void;
  loading: boolean;
}) {
  const [group, setGroup] = useState('All');
  const [showAll, setShowAll] = useState(false);
  const rows = d.rows
    .filter((r) => group === 'All' || r.group === group)
    .sort((a, b) => (b.vs_baseline ?? -Infinity) - (a.vs_baseline ?? -Infinity));
  const shown = showAll ? rows : rows.slice(0, FIRST_ROWS);
  const hasChance = rows.some((r) => r.chance != null);
  const pickGroup = (g: string) => {
    setGroup(g);
    setShowAll(false);
  };

  return (
    <Card>
      <CardHeader
        title="All projections"
        sub={`sorted by how far above his baseline · ${stats === 'all' ? 'every stat' : 'main stat per group'}${loading ? ' · loading every stat…' : ''}`}
        right={
          <div className="filters" role="group" aria-label="Filter by group">
            {['All', ...d.groups].map((g) => (
              <button key={g} type="button" className="fchip" aria-pressed={group === g} onClick={() => pickGroup(g)}>
                {g}
              </button>
            ))}
            <button
              type="button"
              className="fchip"
              aria-pressed={stats === 'all'}
              onClick={() => setStats(stats === 'all' ? 'main' : 'all')}
            >
              Show every stat
            </button>
          </div>
        }
      />
      <div className="tablewrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Player</th>
              <th>Team</th>
              <th>Stat</th>
              <th className="r">Proj.</th>
              <th>Range</th>
              <th className="r">Baseline</th>
              <th className="r">vs base</th>
              {hasChance ? <th className="r">Chance</th> : null}
              <th>Conf.</th>
              <th>Injury</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r: ProjectionRow) => (
              <tr key={`${r.player_id}-${r.target}`}>
                <td>
                  <b>{r.player}</b> <span className="muted">{r.position}</span>
                </td>
                <td>
                  <TeamChip team={r.team} />{' '}
                  <span className="muted">
                    {r.home ? 'vs' : 'at'} {r.opponent}
                  </span>
                </td>
                <td>{r.target_label}</td>
                <td className="r num">
                  <b>{projText(r)}</b>
                </td>
                <td>
                  <RangeBar mini who={r.player} p10={r.p10} p90={r.p90} projection={r.projection} baseline={r.baseline} />
                </td>
                <td className="r num">{fixed(r.baseline, 1)}</td>
                <td className="r num">
                  <span className={deltaClass(r.vs_baseline)}>{deltaText(r.vs_baseline)}</span>
                </td>
                {hasChance ? <td className="r num">{r.chance == null ? '' : pct(r.chance)}</td> : null}
                <td>{r.confidence ?? ''}</td>
                <td>{r.injury ? <Chip tone="warn">{r.injury}</Chip> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > FIRST_ROWS ? (
        <div className="wk-more">
          <button type="button" className="btn sm" onClick={() => setShowAll(!showAll)}>
            {showAll ? `Show the first ${FIRST_ROWS}` : `Show all ${comma(rows.length)}`}
          </button>
        </div>
      ) : null}
    </Card>
  );
}

export function PlayersTab({
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
  const [stats, setStats] = useState<'main' | 'all'>('main');
  // the main stats carry the page; with every stat on, the table switches once those arrive
  // (both hooks share one query while stats is 'main')
  const q = usePlayers(season, week, 'main');
  const qStats = usePlayers(season, week, stats);
  const info = useTeamInfo().data?.teams;
  const nick = (t: string) => info?.[t]?.nick ?? t;
  if (q.isPending) return <TabLoading />;
  if (q.isError) return <TabError error={q.error} />;
  if (qStats.isError) return <TabError error={qStats.error} />;
  const d = q.data;
  const table = qStats.data ?? d;
  if (d.status === 'none') {
    return <WeekEmpty tab="players" season={season} week={week} isCurrent={isCurrent} lastPublishedWeek={lastPublishedWeek} />;
  }
  const c = d.counts;

  return (
    <div className="wk-stack">
      <div className="sec-h">
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 22 }}>Players to watch</h2>
        <p>
          The {d.watch.length} model picks from the digest · {comma(c.total)} projections in all
        </p>
      </div>
      <div className="legend">
        <span>
          <i style={{ background: 'color-mix(in srgb,var(--s1) 30%,transparent)', borderRadius: 2 }} />
          80% range (10th–90th percentile)
        </span>
        <span>
          <i className="line" style={{ background: 'var(--s1)', width: 3, height: 12 }} />
          Projection
        </span>
        <span>
          <i className="line" style={{ borderLeft: '2px dotted var(--ink-2)', background: 'none', width: 2, height: 12 }} />
          His rolling baseline
        </span>
      </div>
      <WatchList d={d} nick={nick} />
      <ToughSpots d={d} nick={nick} />
      <ProjectionTable d={table} stats={stats} setStats={setStats} loading={qStats.isPending} />
      <div className="foot">
        Week {d.week} has {comma(c.total)} projections: {c.stats} stats{c.teams ? `, ${comma(c.teams)} team totals` : ''}
        {d.model?.version ? ` · ${d.model.version}` : ''}
        {d.model?.trained_through ? `, trained through ${d.model.trained_through}` : ''}.
      </div>
    </div>
  );
}
