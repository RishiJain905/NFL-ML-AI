// One week: header + tabs (mockup: renderTop / renderTabs / week4View / week5View). Every tab
// reads its own endpoint (CR01; the MLOps tab's three sections are CR03).

import { NavLink, Navigate, useParams } from 'react-router-dom';
import { useMeta, useWeekDetail, useWeeks } from '../api/client';
import { InjuryUpdateLog } from '../components/run/InjuryUpdate';
import { WeekHeader } from '../components/WeekHeader';
import { comma } from '../lib/format';
import { DigestTab } from './week/DigestTab';
import { GameDayTab } from './week/GameDayTab';
import { useCachedLiveCount } from './week/gameday/useCachedLive';
import { GamesTab } from './week/GamesTab';
import { GraphTab } from './week/GraphTab';
import { MlopsTab } from './week/MlopsTab';
import { PipelineTab } from './week/PipelineTab';
import { PlayCallsTab } from './week/PlayCallsTab';
import { PlayersTab } from './week/PlayersTab';
import { ResultsTab } from './week/ResultsTab';
import { TABS, type TabKey } from './tabs';

export function WeekPage() {
  const params = useParams();
  const season = Number(params.season);
  const week = Number(params.week);
  const tab = params.tab as TabKey;
  const weeks = useWeeks();
  const meta = useMeta();
  const detail = useWeekDetail(season, week);
  const liveNow = useCachedLiveCount(season, week); // Game day's list, from the cache only (LD02)

  if (!TABS.some(([k]) => k === tab))
    return <Navigate to={`/week/${season}/${week}/pipeline`} replace />;
  const entry =
    weeks.data?.season === season ? weeks.data.weeks.find((w) => w.week === week) : undefined;
  const d = detail.data;
  const isCurrent = Boolean(d?.is_current ?? entry?.is_current);
  const lastPublishedWeek = d?.last_published_week ?? null;
  const counts: Partial<Record<TabKey, string>> = {
    games: d?.tab_counts.games != null ? String(d.tab_counts.games) : undefined,
    players: d?.tab_counts.players != null ? comma(d.tab_counts.players) : undefined,
    graph: d?.tab_counts.graph != null ? String(d.tab_counts.graph) : undefined,
  };
  const props = { season, week, isCurrent, lastPublishedWeek };

  return (
    <>
      <WeekHeader
        season={season}
        week={week}
        entry={entry}
        calendar={meta.data?.calendar}
        detail={d}
      />
      {/* plain route links (NavLink sets aria-current="page"), not ARIA tabs (Sol review, CR00) */}
      <nav className="tabs" aria-label="Week sections">
        {TABS.map(([k, label]) => (
          <NavLink key={k} to={`/week/${season}/${week}/${k}`} className="tab">
            {label}
            {counts[k] ? (
              <span className="count" aria-label={`(${counts[k]})`}>
                {counts[k]}
              </span>
            ) : null}
            {k === 'game-day' && liveNow > 0 ? (
              <>
                <span className="dot pulse" style={{ color: 'var(--accent)' }} aria-hidden="true" />
                <span className="count" aria-label={`(${liveNow} live)`}>
                  {liveNow} live
                </span>
              </>
            ) : null}
          </NavLink>
        ))}
      </nav>
      <section className="view" aria-label={TABS.find(([k]) => k === tab)?.[1]}>
        {/* the Saturday injury update's live log, on every tab of its week (CR02) */}
        <InjuryUpdateLog season={season} week={week} />
        {tab === 'pipeline' ? <PipelineTab season={season} week={week} detail={d} /> : null}
        {tab === 'digest' ? <DigestTab {...props} /> : null}
        {tab === 'games' ? <GamesTab {...props} /> : null}
        {tab === 'players' ? <PlayersTab {...props} /> : null}
        {tab === 'results' ? <ResultsTab {...props} /> : null}
        {tab === 'graph' ? <GraphTab {...props} /> : null}
        {tab === 'mlops' ? <MlopsTab {...props} /> : null}
        {tab === 'game-day' ? <GameDayTab {...props} /> : null}
        {tab === 'play-calls' ? <PlayCallsTab {...props} /> : null}
      </section>
    </>
  );
}
