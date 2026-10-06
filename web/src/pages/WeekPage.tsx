// One week: header + tabs (mockup: renderTop / renderTabs). In CR00 every tab shows a
// designed empty state that says what will appear and which phase brings it.

import { NavLink, Navigate, useParams } from 'react-router-dom';
import { useMeta, useWeeks } from '../api/client';
import { WeekHeader } from '../components/WeekHeader';
import { EmptyState } from '../components/ui';
import { TABS, type TabKey } from './tabs';

const COMING: Record<TabKey, { glyph: string; title: string; body: string }> = {
  pipeline: {
    glyph: 'CR01',
    title: "The week's run, step by step",
    body: 'Finished runs with their real step times, in three views (drive chart, pipeline map, timeline) picked per week, come in CR01. The Run button, pre-flight checks and the live view come in CR02.',
  },
  digest: {
    glyph: 'MD',
    title: 'The digest, with its checks and writer',
    body: "The rendered digest, its checks, words per section, the GLM's route, time and cost, and Saturday's addendum. Coming in CR01.",
  },
  games: {
    glyph: 'CR01',
    title: 'Every game: win %, score, model vs market',
    body: 'Game cards with the model, model-only, Elo and market chances, predicted scores, QB changes and finals once played. Coming in CR01.',
  },
  players: {
    glyph: 'CR01',
    title: 'Projections and the watch list',
    body: 'Watch-list cards with ranges and baselines, tough spots, and every projection with filters. Coming in CR01.',
  },
  results: {
    glyph: 'CR01',
    title: "How this week's calls did",
    body: "Once the next week's run grades this week: picks, Brier vs Elo and market, game by game, player accuracy and the watch list. Coming in CR01.",
  },
  mlops: {
    glyph: 'ML',
    title: 'Health · W&B runs · Artifacts',
    body: "Run health, data freshness, ingest and quality checks, drift; the week's W&B runs with their charts redrawn; artifact versions and where production points. Coming in CR03.",
  },
  graph: {
    glyph: 'KG',
    title: 'The knowledge graph behind the digest',
    body: 'Counts, build time, the insights the digest used and the ones it skipped, rows per query. Coming in CR01.',
  },
};

export function WeekPage() {
  const params = useParams();
  const season = Number(params.season);
  const week = Number(params.week);
  const tab = params.tab as TabKey;
  const weeks = useWeeks();
  const meta = useMeta();

  if (!TABS.some(([k]) => k === tab)) return <Navigate to={`/week/${season}/${week}/pipeline`} replace />;
  const entry = weeks.data?.season === season ? weeks.data.weeks.find((w) => w.week === week) : undefined;
  const c = COMING[tab];

  return (
    <>
      <WeekHeader season={season} week={week} entry={entry} calendar={meta.data?.calendar} />
      {/* plain route links (NavLink sets aria-current="page"), not ARIA tabs (Sol review, CR00) */}
      <nav className="tabs" aria-label="Week sections">
        {TABS.map(([k, label]) => (
          <NavLink key={k} to={`/week/${season}/${week}/${k}`} className="tab">
            {label}
          </NavLink>
        ))}
      </nav>
      <section className="view" aria-label={TABS.find(([k]) => k === tab)?.[1]}>
        <EmptyState glyph={c.glyph} title={c.title}>
          {c.body}
        </EmptyState>
      </section>
    </>
  );
}
