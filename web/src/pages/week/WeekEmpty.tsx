// What a week tab shows without data: the current week before its run gets the mockup's empty
// states (week5View → E) with links to the pipeline and to the last published week; a past week
// says plainly that there's nothing. Also the tabs' shared loading, error and warning blocks.

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Card, EmptyState, Notice } from '../../components/ui';
import './week.css';

export type DataTab = 'digest' | 'games' | 'players' | 'results' | 'graph';

const TAB_NAME: Record<DataTab, string> = {
  digest: 'Digest',
  games: 'Games',
  players: 'Players',
  results: 'Results',
  graph: 'Graph',
};

function currentText(tab: DataTab, week: number): { glyph: string; title: string; body: string } {
  switch (tab) {
    case 'digest':
      return {
        glyph: 'MD',
        title: 'The digest appears here when the run finishes',
        body: "Rendered from the week's digest file, with its checks, the GLM's provider, time and cost beside it. If Saturday's injury update publishes an addendum, it shows up under the digest.",
      };
    case 'games':
      return {
        glyph: 'GM',
        title: 'Win chances and scores arrive with the game step',
        body: "Each game's win %, predicted score, the model-only, Elo and market chances, and the QBs. The slate shows here once the schedule has it.",
      };
    case 'players':
      return {
        glyph: 'PL',
        title: 'Player projections arrive with the player step',
        body: 'Every projection with its 80% range and baseline, team totals, the watch list and the tough spots.',
      };
    case 'results':
      return {
        glyph: `W${week + 1}`,
        title: `Week ${week}'s results are graded by week ${week + 1}'s run`,
        body: "Next week's Tuesday run grades this week: each game against the final score, each projection against the box score, the watch list against its baselines.",
      };
    case 'graph':
      return {
        glyph: 'KG',
        title: 'The graph is rebuilt during the run',
        body: 'Node and relationship counts, the insights the digest used and the ones it skipped, GDS status, and a link to Neo4j Browser.',
      };
  }
}

const PAST: Record<DataTab, { glyph: string; title: (w: number) => string; body: (w: number) => string }> = {
  digest: { glyph: 'MD', title: (w) => `No digest for week ${w}`, body: (w) => `Week ${w} has no digest file: its run didn't reach the digest step.` },
  games: { glyph: 'GM', title: (w) => `No games for week ${w}`, body: (w) => `Week ${w} has no saved predictions and no slate.` },
  players: { glyph: 'PL', title: (w) => `No player projections for week ${w}`, body: (w) => `Week ${w} has no saved player projections.` },
  results: { glyph: 'RS', title: (w) => `No results for week ${w}`, body: (w) => `Week ${w} has nothing graded.` },
  graph: { glyph: 'KG', title: () => 'No graph for this week', body: (w) => `Week ${w} has no saved graph results: the graph step didn't run or didn't finish.` },
};

export function WeekEmpty({
  tab,
  season,
  week,
  isCurrent,
  lastPublishedWeek,
}: {
  tab: DataTab;
  season: number;
  week: number;
  isCurrent: boolean;
  lastPublishedWeek: number | null;
}) {
  if (!isCurrent) {
    const p = PAST[tab];
    return (
      <EmptyState glyph={p.glyph} title={p.title(week)}>
        {p.body(week)}
      </EmptyState>
    );
  }
  const e = currentText(tab, week);
  return (
    <EmptyState
      glyph={e.glyph}
      title={e.title}
      actions={
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}>
          <Link className="btn sm" to={`/week/${season}/${week}/pipeline`}>
            Go to the pipeline
          </Link>
          {lastPublishedWeek != null && lastPublishedWeek !== week ? (
            <Link className="btn sm" to={`/week/${season}/${lastPublishedWeek}/${tab}`}>
              See week {lastPublishedWeek}'s {TAB_NAME[tab]}
            </Link>
          ) : null}
        </div>
      }
    >
      {e.body}
    </EmptyState>
  );
}

export function TabLoading() {
  return (
    <Card>
      <div className="card-b muted" role="status">
        Loading…
      </div>
    </Card>
  );
}

export function TabError({ error }: { error: Error }) {
  return (
    <Notice tone="err" icon="✕">
      <b>Couldn't load this tab.</b> {error.message}
    </Notice>
  );
}

/** The mockup's warning notice (warn colour, "!" icon). */
export function WarnNotice({ children }: { children: ReactNode }) {
  return (
    <div className="notice warn" role="note">
      <span className="ic" aria-hidden="true">
        !
      </span>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}
