// The Game day tab (LD02; mockup: documentation/live-decisions/mockup/, boardView, beforeView,
// pastView). The week's games refresh every 30 s while this tab is open in the week being played;
// nothing else is fetched until "Check this play".

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { apiGet, useLiveGames } from '../../api/client';
import type { LiveGame, LiveGamesResponse } from '../../api/types';
import { TeamChip } from '../../components/TeamChip';
import { Card, EmptyState, Notice } from '../../components/ui';
import { dayLabel } from '../../lib/format';
import { GameCard, GameList } from './gameday/GameList';
import { GamePanel } from './gameday/GamePanel';
import { defaultPick, groupGames, liveGamesKey, pregamePick, tKick, tMin, tSec } from './gameday/model';
import './week.css';
import './gameday/gameday.css';

/** The list failed to refresh but React Query kept the last answer: say so, keep showing it. */
function RefreshError({ message, at, onRetry, retrying }: { message: string; at: string | null; onRetry: () => void; retrying: boolean }) {
  return (
    <div className="notice warn" role="alert">
      <span className="ic warn" aria-hidden="true">
        !
      </span>
      <div>
        <b>Couldn&apos;t refresh the games.</b> {message}
        {at ? ` Showing the list from ${tSec(at)}.` : ''}{' '}
        <button type="button" className="btn sm" onClick={onRetry} disabled={retrying}>
          {retrying ? 'Retrying…' : 'Retry'}
        </button>
      </div>
    </div>
  );
}

function LiveWeekLink({ d }: { d: LiveGamesResponse }) {
  const lw = d.live_week;
  if (!lw || (lw.season === d.season && lw.week === d.week)) return null;
  return (
    <Link className="btn sm" to={`/week/${lw.season}/${lw.week}/game-day`}>
      Game day is on week {lw.week}
    </Link>
  );
}

function ReplayBanner({ d }: { d: LiveGamesResponse }) {
  if (!d.replay) return null;
  const r = d.replay;
  return (
    <Notice icon="R" tone="accent">
      <b>Replay.</b> ESPN&apos;s play log for game {r.event} as if live, from {tSec(r.at)} ET
      {r.speed !== 1 ? ` at ${r.speed}×` : ''}, with ESPN {r.lag_s} s behind the snap (
      <code>nfl app --live-replay</code>). Nothing is fetched from ESPN.
    </Notice>
  );
}

function Slate({ d }: { d: LiveGamesResponse }) {
  const first = d.games[0];
  return (
    <>
      <EmptyState glyph="GD" title="Game day opens at the first kickoff" actions={<LiveWeekLink d={d} />}>
        {d.first_kickoff && first ? (
          <>
            {dayLabel(d.first_kickoff)}, {tMin(d.first_kickoff)} ET: <TeamChip team={first.away} full /> at{' '}
            <TeamChip team={first.home} full />. From then on this tab lists the games that are on, and checks a 3rd or 4th
            down when you ask.
          </>
        ) : (
          'This tab lists the games that are on, and checks a 3rd or 4th down when you ask.'
        )}
      </EmptyState>
      <Card className="slate">
        <div className="card-h">
          <h2>Week {d.week} slate</h2>
          <span className="muted">{d.games.length} games · times ET · our pick from this week&apos;s run</span>
        </div>
        <div className="tablewrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Kickoff</th>
                <th>Game</th>
                <th className="r">Market</th>
                <th className="r">Total</th>
                <th className="r">Our pick</th>
              </tr>
            </thead>
            <tbody>
              {d.games.map((g) => (
                <tr key={g.event}>
                  <td className="num">{tKick(g.kickoff)}</td>
                  <td>
                    <TeamChip team={g.away} full /> <span className="muted">at</span> <TeamChip team={g.home} full />
                  </td>
                  <td className="r num">{g.pregame.spread_text ?? '—'}</td>
                  <td className="r num">{g.pregame.total ?? '—'}</td>
                  <td className="r num pick">{pregamePick(g)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

function Past({ d }: { d: LiveGamesResponse }) {
  return (
    <>
      <EmptyState glyph="LD03" title="Decision review: coming in LD03" actions={<LiveWeekLink d={d} />}>
        For a finished week this tab will show every 4th down, the bot&apos;s call next to the coach&apos;s, and the win
        chance each choice cost or gained. Live checks only run during the week being played.
      </EmptyState>
      <Card>
        <div className="card-h">
          <h2>Week {d.week} finals</h2>
          <span className="muted">{d.games.length} games</span>
        </div>
        <div className="card-b">
          <div className="games" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
            {d.games.map((g) => (
              <GameCard key={g.event} game={g} />
            ))}
          </div>
        </div>
      </Card>
    </>
  );
}

function Board({ d, season, week, receivedAt }: { d: LiveGamesResponse; season: number; week: number; receivedAt: number | null }) {
  const qc = useQueryClient();
  const [sel, setSel] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const { live, later, final } = groupGames(d.games);
  const pickedEvent =
    sel && d.games.some((g) => g.event === sel) ? sel : d.phase === 'live' ? defaultPick(d.games) : null;
  const picked: LiveGame | null = d.games.find((g) => g.event === pickedEvent) ?? null;
  // keep the game the board opened on: the next list refresh mustn't move the panel to another game
  // (state adjusted while rendering, React's pattern for state derived from props)
  if (!sel && pickedEvent) setSel(pickedEvent);
  const counts = [live.length && `${live.length} on now`, later.length && `${later.length} later`, final.length && `${final.length} final`]
    .filter(Boolean)
    .join(' · ');
  const next = later[0];
  const refresh = async () => {
    setRefreshing(true);
    try {
      const fresh = await apiGet<LiveGamesResponse>(`/api/live/${season}/${week}/games?refresh=1`);
      qc.setQueryData(liveGamesKey(season, week), fresh);
      setRefreshError(null);
    } catch (e) {
      // the list keeps its last answer (the next 30 s refresh tries again); say so
      setRefreshError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  };
  const now = d.feed?.as_of ?? null;
  return (
    <>
      <div className="gd-head">
        <h2>Game day</h2>
        <span className="muted">
          {now ? `${dayLabel(now)} · ` : ''}
          {counts}
        </span>
        <div className="right">
          {d.is_current && d.phase !== 'final' ? (
            <span>
              Updates every {d.refresh_s} s while this tab is open
              {d.feed ? (
                <>
                  {' · '}
                  <span className="num">{tSec(d.feed.as_of)}</span>
                </>
              ) : null}
            </span>
          ) : null}
          <button type="button" className="btn sm" onClick={() => void refresh()} aria-disabled={refreshing || undefined} disabled={refreshing}>
            {refreshing ? (
              <>
                <span className="spin" aria-hidden="true" />
                Refreshing
              </>
            ) : (
              'Refresh'
            )}
          </button>
        </div>
      </div>
      {refreshError ? (
        <RefreshError message={refreshError} at={d.feed?.as_of ?? null} onRetry={() => void refresh()} retrying={refreshing} />
      ) : null}
      <div className="gd">
        <GameList games={d.games} picked={pickedEvent} onPick={setSel} />
        <div className="gd-main">
          {!d.models.available ? (
            <div className="notice warn" role="status">
              <span className="ic warn" aria-hidden="true">
                !
              </span>
              <div>
                <b>{d.models.message ?? "The decision models aren't available."}</b> The game list still works;{' '}
                <b>Check this play</b> stays off until <code className="mono">nfl live train</code> has promoted a bundle.
              </div>
            </div>
          ) : null}
          {d.phase === 'between' && !picked ? (
            <EmptyState glyph="—" title="No game on right now">
              {next ? `Next: ${next.away} at ${next.home}, ${tMin(next.kickoff)} ET. ` : ''}The list keeps updating every{' '}
              {d.refresh_s} s while this tab is open.
            </EmptyState>
          ) : null}
          {d.phase === 'final' && !picked ? (
            <EmptyState
              glyph="F"
              title={`Every week-${d.week} game is final`}
              actions={
                <button type="button" className="btn sm" aria-disabled="true" title="Coming in LD03">
                  Open the decision review · LD03
                </button>
              }
            >
              The week&apos;s decision review (each 4th down, the bot&apos;s call next to the coach&apos;s, and what it cost
              or gained) comes in LD03.
            </EmptyState>
          ) : null}
          {picked ? (
            <GamePanel
              key={picked.event}
              season={season}
              week={week}
              game={picked}
              feed={d.feed}
              listReceivedAt={receivedAt}
              models={d.models}
            />
          ) : null}
        </div>
      </div>
    </>
  );
}

export function GameDayTab({ season, week }: { season: number; week: number; isCurrent: boolean; lastPublishedWeek: number | null }) {
  const q = useLiveGames(season, week);
  const d = q.data;
  // the phase can change while the tab is open (a future week turns live): the body follows it
  let body;
  if (q.isPending) body = <Card className="pad muted">Loading the week&apos;s games…</Card>;
  else if (!d)
    body = (
      <Notice icon="!" tone="err">
        <b>Couldn&apos;t load the games.</b> {q.error?.message}{' '}
        <button type="button" className="btn sm" onClick={() => void q.refetch()} disabled={q.isFetching}>
          {q.isFetching ? 'Retrying…' : 'Retry'}
        </button>
      </Notice>
    );
  else if (d.phase === 'before' || d.phase === 'future') body = <Slate d={d} />;
  else if (d.phase === 'past') body = <Past d={d} />;
  else body = <Board d={d} season={season} week={week} receivedAt={q.dataUpdatedAt || null} />;
  return (
    <div className="gameday wk-stack">
      {d ? <ReplayBanner d={d} /> : null}
      {d && q.isError ? (
        <RefreshError message={q.error.message} at={d.feed?.as_of ?? null} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : null}
      {d?.feed_error ? (
        <div className="notice warn" role="status">
          <span className="ic warn" aria-hidden="true">
            !
          </span>
          <div>
            <b>ESPN didn&apos;t answer ({d.feed_error}).</b> These are the schedule&apos;s games; scores and situations
            appear once ESPN answers.
          </div>
        </div>
      ) : null}
      {body}
    </div>
  );
}
