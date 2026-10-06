// The Games tab (mockup: w4Games, and slateCard for the current week before its run): tiles,
// where the model disagrees with the market, then a card per game with the win %, predicted
// score, the dot strip, QBs (⚠ on a QB change), the market line and the final once played.

import { useGames, useTeamInfo } from '../../api/client';
import type { GameRow, GamesResponse } from '../../api/types';
import { DotStrip } from '../../charts/DotStrip';
import { TeamChip, TeamNick } from '../../components/TeamChip';
import { Card, CardHeader, Chip, Tile, type ChipTone } from '../../components/ui';
import { fixed, pct, signed } from '../../lib/format';
import { TabError, TabLoading, WeekEmpty } from './WeekEmpty';
import { kickShort, marketLine } from './weekUtil';
import './week.css';

const CONF_TONE: Record<string, ChipTone> = { strong: 'ok', solid: 'run' };

function Qb({ name, change }: { name: string | null; change: string | null }) {
  if (!name) return null;
  return (
    <small>
      {name}
      {change ? (
        <span role="img" aria-label={`QB change: ${change}`} title={change}>
          {' '}
          ⚠
        </span>
      ) : null}
    </small>
  );
}

function ResultChip({ g, week }: { g: GameRow; week: number }) {
  if (g.final) {
    if (g.hit === true)
      return (
        <Chip tone="ok" icon="✓">
          hit · final
        </Chip>
      );
    if (g.hit === false)
      return (
        <Chip tone="err" icon="✕">
          miss · final
        </Chip>
      );
    return (
      <Chip title={g.predicted_after_kickoff ? 'Predicted after kickoff, so the report card leaves it out' : undefined}>
        {g.predicted_after_kickoff ? 'final · not graded' : 'final'}
      </Chip>
    );
  }
  return <span className="muted">result after week {week + 1}'s ingest</span>;
}

function GameCard({ g, week, nick }: { g: GameRow; week: number; nick: (t: string) => string }) {
  const favHome = (g.p_home ?? 0.5) >= 0.5;
  const pAway = g.p_home == null ? null : 1 - g.p_home;
  const dim = { color: 'var(--ink-3)' };
  return (
    <div className="game" aria-label={`${nick(g.away)} at ${nick(g.home)}`} role="group">
      <div className="gh">
        <span>
          {kickShort(g.kickoff)}
          {g.neutral ? ' · neutral site' : ''}
          {g.predicted_after_kickoff ? ' · predicted after kickoff' : ''}
        </span>
        {g.confidence ? <Chip tone={CONF_TONE[g.confidence] ?? 'flat'}>{g.confidence}</Chip> : null}
      </div>
      <div className="rows">
        <span className="tn">
          <TeamChip team={g.away} full />
          <Qb name={g.qb_away} change={g.qb_change_away} />
        </span>
        <span className="pct" style={favHome ? dim : undefined}>
          {pct(pAway)}
        </span>
        <span className="pts">{g.final ? <b>{g.final.away}</b> : g.pts_away == null ? '—' : Math.round(g.pts_away)}</span>
        <span className="tn">
          <TeamChip team={g.home} full />
          <Qb name={g.qb_home} change={g.qb_change_home} />
        </span>
        <span className="pct" style={favHome ? undefined : dim}>
          {pct(g.p_home)}
        </span>
        <span className="pts">{g.final ? <b>{g.final.home}</b> : g.pts_home == null ? '—' : Math.round(g.pts_home)}</span>
      </div>
      <DotStrip
        home={nick(g.home)}
        away={nick(g.away)}
        pHome={g.p_home}
        pModelOnly={g.p_home_model_only}
        pElo={g.p_elo}
        pMarket={g.p_market}
      />
      <div className="gf">
        <span>
          {g.away} ← → {g.home} · market {marketLine(g.spread, g.home, g.away)} · total {fixed(g.total)}
        </span>
        <span>
          <ResultChip g={g} week={week} />
        </span>
      </div>
    </div>
  );
}

function Predicted({ d }: { d: GamesResponse }) {
  const info = useTeamInfo().data?.teams;
  const nick = (t: string) => info?.[t]?.nick ?? t;
  const n = d.games.length;
  const kept = d.games.filter((g) => g.predicted_after_kickoff).length;
  const byId = new Map(d.games.map((g) => [g.game_id, g]));
  const gaps = d.gaps.flatMap((x) => {
    const g = byId.get(x.game_id);
    return g ? [{ g, gap: x.gap }] : [];
  });

  return (
    <div className="wk-stack">
      <div className="grid g3">
        <Tile
          k="Games predicted"
          v={n}
          d={kept ? `${n - kept} before kickoff + ${kept} after kickoff (not graded)` : 'all before kickoff'}
        />
        <Tile k="Lines used" v={`${d.lines_used}/${n}`} d="market-informed rows shown in the digest" />
        <Tile
          k="Model"
          v={<span style={{ fontSize: 20 }}>{d.model?.version ?? '—'}</span>}
          d={d.model?.trained_through ? `trained through ${d.model.trained_through}` : undefined}
        />
      </div>
      {gaps.length ? (
        <Card>
          <CardHeader
            title="Where the model disagrees with the market"
            sub="model-only win chance vs the market's, biggest gaps"
          />
          <div className="card-b">
            <div className="grid g3">
              {gaps.map(({ g, gap }) => (
                <div key={g.game_id}>
                  <div style={{ fontWeight: 700 }}>
                    <TeamChip team={g.away} full /> at <TeamChip team={g.home} full />
                  </div>
                  <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
                    Model only gives <TeamNick team={g.home} />{' '}
                    <b style={{ color: 'var(--ink)' }}>{pct(g.p_home_model_only)}</b>, market {pct(g.p_market)} (
                    {signed(gap * 100, 0)} pts)
                  </div>
                </div>
              ))}
            </div>
          </div>
        </Card>
      ) : null}
      <div className="sec-h">
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20 }}>All games</h2>
        <div className="right legend">
          <span>
            <i style={{ background: 'var(--s1)' }} />
            Model (shown)
          </span>
          <span>
            <i style={{ background: 'var(--panel)', boxShadow: 'inset 0 0 0 2.5px var(--s1)' }} />
            Model only
          </span>
          <span>
            <i style={{ background: 'var(--s2)' }} />
            Elo
          </span>
          <span>
            <i style={{ background: 'var(--s3)' }} />
            Market
          </span>
          <span className="muted">dots: home team's win chance, away ← 50% → home</span>
        </div>
      </div>
      <div className="games">
        {d.games.map((g) => (
          <GameCard key={g.game_id} g={g} week={d.week} nick={nick} />
        ))}
      </div>
      <div className="foot">
        {d.finals === 0
          ? `No finals yet. Week ${d.week + 1}'s run grades these games.`
          : d.finals >= n
            ? `All ${n} games are final. Week ${d.week + 1}'s run grades them.`
            : `${d.finals} of ${n} games are final so far. The rest are graded by week ${d.week + 1}'s run.`}
      </div>
    </div>
  );
}

function Slate({ d }: { d: GamesResponse }) {
  const n = d.games.length;
  const sub = [
    `${n} games from the schedule${d.source.snapshot_date ? ` (snapshot ${d.source.snapshot_date})` : ''}`,
    d.byes.length ? `byes: ${d.byes.join(', ')}` : null,
  ]
    .filter(Boolean)
    .join(' · ');
  return (
    <Card>
      <CardHeader title={`Week ${d.week} slate`} sub={sub} />
      <div className="tablewrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Kickoff (ET)</th>
              <th>Game</th>
              <th>Stadium</th>
              <th className="r">Market</th>
              <th className="r">Total</th>
              <th className="c">Win %</th>
              <th className="c">Predicted score</th>
            </tr>
          </thead>
          <tbody>
            {d.games.map((g) => (
              <tr key={g.game_id}>
                <td className="num">{kickShort(g.kickoff).replace(/ ET$/, '')}</td>
                <td>
                  <TeamChip team={g.away} full /> <span className="muted">at</span> <TeamChip team={g.home} full />
                  {g.neutral ? <span className="muted"> · neutral site</span> : null}
                </td>
                <td className="muted">{g.stadium ?? ''}</td>
                <td className="r num">{marketLine(g.spread, g.home, g.away)}</td>
                <td className="r num">{fixed(g.total)}</td>
                <td className="c">
                  <Chip tone="ghost">after the run</Chip>
                </td>
                <td className="c">
                  <Chip tone="ghost">after the run</Chip>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function GamesTab({
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
  const q = useGames(season, week);
  if (q.isPending) return <TabLoading />;
  if (q.isError) return <TabError error={q.error} />;
  const d = q.data;
  if (d.status === 'slate' && d.games.length) return <Slate d={d} />;
  if (d.status === 'predicted' && d.games.length) return <Predicted d={d} />;
  return <WeekEmpty tab="games" season={season} week={week} isCurrent={isCurrent} lastPublishedWeek={lastPublishedWeek} />;
}
