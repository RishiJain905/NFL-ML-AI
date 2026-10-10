// The week's games as cards (mockup: gameCard): on now, later, final. A card is a button that
// picks the game for the panel; a past week's cards are plain (nothing to pick).

import type { LiveGame } from '../../../api/types';
import { TeamChip } from '../../../components/TeamChip';
import { dayLabel } from '../../../lib/format';
import { cardSituation, gameLabel, groupGames, quarter, tKick } from './model';
import { Ball } from './parts';

export function GameCard({
  game: g,
  picked,
  onPick,
}: {
  game: LiveGame;
  picked?: boolean;
  onPick?: (event: string) => void;
}) {
  const won = (t: string) =>
    g.state !== 'post' || (t === g.home ? g.home_score >= g.away_score : g.away_score >= g.home_score);
  const row = (t: string, score: number) => (
    <>
      <span className={`gt${won(t) ? '' : ' lost'}`}>
        <TeamChip team={t} />
        {g.state === 'in' && g.possession === t ? <Ball /> : null}
      </span>
      <span className={`gs${g.state === 'pre' ? ' pre' : won(t) ? '' : ' lost'}`}>
        {g.state === 'pre' ? '' : score}
      </span>
    </>
  );
  let top;
  if (g.state === 'in') {
    top = (
      <>
        <span className="live">
          <span className="dot pulse" aria-hidden="true" />
          {quarter(g.period)} {g.clock ?? ''}
        </span>
        <span className={`gsit${g.decision_down ? ' dd' : ''}`}>{cardSituation(g.situation) || g.detail}</span>
      </>
    );
  } else if (g.state === 'pre') {
    top = (
      <>
        <span>{tKick(g.kickoff)}</span>
        <span className="gsit">{g.pregame.spread_text ?? ''}</span>
      </>
    );
  } else {
    top = (
      <>
        <span>{g.detail ?? 'Final'}</span>
        <span className="gsit">{dayLabel(g.kickoff)}</span>
      </>
    );
  }
  const body = (
    <>
      <span className="gtop">{top}</span>
      <span className="grow2">
        {row(g.away, g.away_score)}
        {row(g.home, g.home_score)}
      </span>
    </>
  );
  if (!onPick) return <div className="gcard">{body}</div>;
  return (
    <button
      type="button"
      className="gcard"
      aria-pressed={Boolean(picked)}
      aria-label={gameLabel(g, tKick(g.kickoff))}
      onClick={() => onPick(g.event)}
    >
      {body}
    </button>
  );
}

export function GameList({
  games,
  picked,
  onPick,
}: {
  games: LiveGame[];
  picked: string | null;
  onPick: (event: string) => void;
}) {
  const { live, later, final } = groupGames(games);
  const grp = (label: string, gs: LiveGame[]) =>
    gs.length ? (
      <div className="grp" role="group" aria-label={label}>
        <div className="eyebrow">
          <span>{label}</span>
          <span>{gs.length}</span>
        </div>
        {gs.map((g) => (
          <GameCard key={g.event} game={g} picked={g.event === picked} onPick={onPick} />
        ))}
      </div>
    ) : null;
  return (
    <div className="glist" aria-label="This week's games">
      {grp('On now', live)}
      {grp('Later', later)}
      {grp('Final', final)}
    </div>
  );
}
