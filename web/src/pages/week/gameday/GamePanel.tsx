// The picked game (mockup: panel): score, clock, possession, timeouts, ESPN's situation and last
// play, our pre-game pick, the market line, ESPN's win %; then "Check this play" and its answer.
// The call is fetched on the click only (`useLiveCall` never runs by itself); the team context
// once a call names an offense on a 3rd or 4th down.

import { useLiveCall, useLiveContext, useTeamInfo } from '../../../api/client';
import type { LiveFeed, LiveGame, LiveModels } from '../../../api/types';
import { TeamChip } from '../../../components/TeamChip';
import { dayLabel } from '../../../lib/format';
import { useNow } from '../../../lib/useNow';
import { CallNotices, FourthCard, NoneCard, ThirdCard } from './CallCards';
import { ContextCard } from './ContextCard';
import { ago, lastPlayAge, newer, p0, pregamePick, quarter, tMin, tSec } from './model';
import { AsOf, Ball, Timeouts } from './parts';

function Side({ g, team, home }: { g: LiveGame; team: string; home?: boolean }) {
  const score = team === g.home ? g.home_score : g.away_score;
  return (
    <div className={`side-t${home ? ' home' : ''}`}>
      <TeamSwatch team={team} />
      <span className="tn">
        <b>
          {team}
          {g.state === 'in' && g.possession === team ? <Ball large /> : null}
        </b>
        <small>
          <TeamChipNick team={team} />
          {home ? ' · home' : ''}
        </small>
        {g.state === 'in' ? <Timeouts n={home ? g.home_timeouts : g.away_timeouts} /> : null}
      </span>
      <span className="sc num">{g.state === 'pre' ? '' : score}</span>
    </div>
  );
}

// the team colour bar and nickname come from the same team list TeamChip reads
function TeamSwatch({ team }: { team: string }) {
  const c = useTeamInfo().data?.teams[team]?.color ?? '#888888';
  return <span className="swatchbar" style={{ background: c }} aria-hidden="true" />;
}
function TeamChipNick({ team }: { team: string }) {
  return <>{useTeamInfo().data?.teams[team]?.nick ?? team}</>;
}

export function GamePanel({
  season,
  week,
  game: listGame,
  feed,
  listReceivedAt,
  models,
  stadium,
}: {
  season: number;
  week: number;
  game: LiveGame;
  feed: LiveFeed | null;
  listReceivedAt: number | null; // when this browser got the list (its dataUpdatedAt)
  models: LiveModels;
  stadium?: string | null;
}) {
  const call = useLiveCall(season, week, listGame.event);
  const answer = call.data && call.data.event === listGame.event ? call.data : null;
  const ctxOn = answer && answer.kind !== 'none' && answer.offense ? answer : null;
  const context = useLiveContext(season, week, ctxOn ? ctxOn.event : null, ctxOn ? ctxOn.offense : null);
  // show whichever copy of the game is newer: the list's (every 30 s) or the last check's
  const g = answer && newer(answer.feed.as_of, feed?.as_of) ? answer.game : listGame;
  const fromCall = Boolean(answer && g === answer.game);
  const shownFeed = fromCall && answer ? answer.feed : feed;
  const receivedAt = fromCall ? call.dataUpdatedAt || null : listReceivedAt;
  const off = g.possession;
  const espn = g.espn_home_wp == null ? null : off === g.away ? 1 - g.espn_home_wp : g.espn_home_wp;
  const espnTeam = off === g.away ? g.away : g.home;
  const lp = g.last_play;
  const now = useNow(5_000);
  const loading = call.isFetching;
  const failed = call.isError && !loading;

  let mid;
  if (g.state === 'in')
    mid = (
      <>
        <span className="q">{quarter(g.period)}</span>
        <span className="clk">{g.clock ?? ''}</span>
        {g.situation ? <span className="dd">{g.situation}</span> : null}
        {g.red_zone ? <span className="chip flat">Red zone</span> : null}
      </>
    );
  else if (g.state === 'pre')
    mid = (
      <>
        <span className="q">Kickoff</span>
        <span className="clk">{tMin(g.kickoff)}</span>
        <span className="dd">{dayLabel(g.kickoff)}</span>
      </>
    );
  else
    mid = (
      <>
        <span className="q">{g.detail ?? 'Final'}</span>
        <span className="clk">Final</span>
      </>
    );

  let act;
  if (g.state === 'pre')
    act = (
      <>
        <button type="button" className="btn primary big" aria-disabled="true">
          Check this play
        </button>
        <span className="reason">Opens at kickoff ({tMin(g.kickoff)} ET).</span>
      </>
    );
  else if (g.state === 'post')
    act = (
      <>
        <button type="button" className="btn primary big" aria-disabled="true">
          Check this play
        </button>
        <span className="reason">The game is over. Its 4th downs go in the decision review (LD03).</span>
      </>
    );
  else if (!models.available)
    act = (
      <>
        <button type="button" className="btn primary big" aria-disabled="true">
          Check this play
        </button>
        <span className="reason">{models.message ?? "The decision models aren't available."}</span>
      </>
    );
  else
    act = (
      <>
        <button
          type="button"
          className="btn primary big"
          aria-busy={loading || undefined}
          aria-disabled={loading || undefined}
          onClick={() => {
            if (!loading) void call.refetch();
          }}
        >
          {loading ? (
            <>
              <span className="spin" aria-hidden="true" />
              Checking…
            </>
          ) : failed ? (
            'Try again'
          ) : answer ? (
            'Check again'
          ) : (
            'Check this play'
          )}
        </button>
        <span className="reason">
          {loading
            ? 'Asking ESPN for this game, then running the bot.'
            : `${answer ? `Last check ${tSec(answer.feed.as_of)}. ` : ''}One ESPN call for this game, then the bot: about a second. Nothing is fetched until you press it.`}
        </span>
      </>
    );

  const live = g.state === 'in' && models.available;
  return (
    <>
      <div className="card gp">
        <div className="gp-h">
          <h2>
            <TeamChip team={g.away} full />{' '}
            <span className="muted" style={{ fontWeight: 500 }}>
              at
            </span>{' '}
            <TeamChip team={g.home} full />
          </h2>
          {stadium ? <span className="muted">{stadium}</span> : null}
          {g.state === 'in' && shownFeed ? (
            <div className="right">
              <AsOf feed={shownFeed} game={g} receivedAt={receivedAt} />
            </div>
          ) : null}
        </div>
        <div className="board">
          <Side g={g} team={g.away} />
          <div className="mid">{mid}</div>
          <Side g={g} team={g.home} home />
        </div>
        <div className="facts">
          <span>
            Our pre-game pick <b>{pregamePick(g)}</b>
          </span>
          <span>
            Market <b>{g.pregame.spread_text ?? '—'}</b>
            {g.pregame.total != null ? (
              <>
                {' '}
                · total <b>{g.pregame.total}</b>
              </>
            ) : null}
          </span>
          {espn != null ? (
            <span>
              ESPN&apos;s win % now{' '}
              <b>
                {espnTeam} {p0(espn)}
              </b>
            </span>
          ) : null}
        </div>
        {g.state === 'in' && lp?.text ? (
          <div className="lastplay">
            <span className="eyebrow">Last play · ESPN</span>
            <span className="muted">{lp.age_s != null ? ago(lastPlayAge(lp.age_s, receivedAt, now)) : ''}</span>
            <p>{lp.text}</p>
          </div>
        ) : null}
        <div className="actbar">{act}</div>
      </div>
      {live && loading && !answer ? (
        <div className="card callcard" aria-live="polite" aria-busy="true">
          <div className="ch">
            <div className="what">
              <span className="eyebrow">Checking</span>
              <b>{g.situation}</b>
            </div>
          </div>
          <div className="skel" aria-hidden="true">
            <i style={{ width: '40%', height: 44 }} />
            <i style={{ width: '92%' }} />
            <i style={{ width: '84%' }} />
            <i style={{ width: '70%' }} />
          </div>
        </div>
      ) : null}
      {live && failed ? (
        <div className="notice err" role="alert">
          <span className="ic err" aria-hidden="true">
            !
          </span>
          <div>
            <b>Couldn&apos;t check this play.</b> {call.error?.message}
            {answer ? ' The last answer is below.' : ' Try again in a few seconds; the app waits at least 2 s between calls for one game.'}
          </div>
        </div>
      ) : null}
      {live && answer ? (
        <div className="callblock" aria-live="polite">
          <CallNotices call={answer} />
          {answer.kind === 'fourth' && answer.fourth ? <FourthCard call={answer} f={answer.fourth} receivedAt={call.dataUpdatedAt || null} /> : null}
          {answer.kind === 'third' && answer.third ? <ThirdCard call={answer} t={answer.third} receivedAt={call.dataUpdatedAt || null} /> : null}
          {answer.kind === 'none' ? <NoneCard call={answer} receivedAt={call.dataUpdatedAt || null} /> : null}
          {context.data && answer.kind !== 'none' ? <ContextCard ctx={context.data} call={answer} /> : null}
          {context.isError ? (
            <div className="notice">
              <span className="ic" aria-hidden="true">
                i
              </span>
              <div>&quot;Is this team good at this?&quot; isn&apos;t available: {context.error.message}</div>
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
