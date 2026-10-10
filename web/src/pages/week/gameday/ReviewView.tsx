// The decision review of a finished week (LD03; mockup: documentation/live-decisions/mockup/
// review.js reviewView / rvHead): a "This week / Season" switch inside the Game day tab (no
// sidebar page), a stepper to the previous / next week (weeks before go-live and past seasons
// aren't in the sidebar), the models behind it, and the states: loading (the first open of a
// season scores it), no plays yet, no models. Read-only: curated plays + the models, never ESPN.

import { type ReactNode, useState } from 'react';
import { Link } from 'react-router-dom';
import { useLiveReview, useLiveSeasonReview } from '../../../api/client';
import type { LiveReviewResponse, LiveSeasonReviewResponse, ReviewModel } from '../../../api/types';
import { Card, EmptyState, Notice, Segmented } from '../../../components/ui';
import { ReviewSeason } from './ReviewSeason';
import { ReviewWeek } from './ReviewWeek';
import { stepWeeks, throughText, weekName } from './review';
import './review.css';

type View = 'week' | 'season';

const SOURCE: Record<string, string> = {
  file: "from nfl live review's file",
  cache: "from the app's cache",
  computed: 'computed on first open',
  memory: 'computed this session',
};

function Meta({ model, source }: { model: ReviewModel | null; source: string | null }) {
  return (
    <div className="rv-meta">
      {model?.version ? (
        <span>
          Decision models <span className="mono">{model.version}</span>
        </span>
      ) : null}
      <span>nflverse play-by-play, not ESPN</span>
      {model && model.in_sample === false && model.trained_seasons ? (
        <span>
          <b>Out of sample</b>: trained on {model.trained_seasons[0]}–{model.trained_seasons[1]}
        </span>
      ) : null}
      {source && SOURCE[source] ? <span>{SOURCE[source]}</span> : null}
    </div>
  );
}

function InSample({ model, season }: { model: ReviewModel; season: number }) {
  const span = model.trained_seasons ? `${model.trained_seasons[0]}–${model.trained_seasons[1]}` : 'seasons that include this one';
  return (
    <div className="notice warn" role="note">
      <span className="ic warn" aria-hidden="true">
        !
      </span>
      <div>
        <b>In-sample season.</b> The decision models were trained on {span}, {season} included: they have seen these plays, so
        the calibration below flatters the bot and its calls agree with what happened more than they would live. 2026 is the
        first season the models haven&apos;t seen.
      </div>
    </div>
  );
}

function Loading({ season }: { season: boolean }) {
  return (
    <Card>
      <div className="skel" aria-hidden="true">
        <i style={{ width: '30%', height: 34 }} />
        <i style={{ width: '92%' }} />
        <i style={{ width: '80%' }} />
        <i style={{ width: '86%' }} />
      </div>
      <p className="muted rv-loading" role="status">
        <span className="spin" aria-hidden="true" />
        {season
          ? "Building the season review: the first open of a week or season the server hasn't reviewed yet can take up to half a minute. It's kept after that."
          : "Scoring the week's 4th downs: the first open of a week or season the server hasn't reviewed yet can take up to half a minute. It's kept after that."}
      </p>
    </Card>
  );
}

function Failed({ message, onRetry, retrying }: { message: string; onRetry: () => void; retrying: boolean }) {
  return (
    <Notice icon="!" tone="err">
      <b>Couldn&apos;t load the review.</b> {message}{' '}
      <button type="button" className="btn sm" onClick={onRetry} disabled={retrying}>
        {retrying ? 'Retrying…' : 'Retry'}
      </button>
    </Notice>
  );
}

function NoModels({ message }: { message: string | null }) {
  return (
    <div className="notice warn" role="status">
      <span className="ic warn" aria-hidden="true">
        !
      </span>
      <div>
        <b>{message ?? "The decision models aren't available."}</b> The review scores every 4th down with the decision models, so
        it appears once a bundle is promoted.
      </div>
    </div>
  );
}

function WeekBody({ r, onSeason, empty }: { r: LiveReviewResponse; onSeason: () => void; empty?: ReactNode }) {
  if (r.status === 'no_models') return <NoModels message={r.message} />;
  if (r.status === 'no_plays')
    return (
      <>
        <EmptyState
          glyph="—"
          title={`${weekName(r.season, r.week)}'s review isn't ready yet`}
          actions={
            <button type="button" className="btn sm" onClick={onSeason}>
              See the season so far
            </button>
          }
        >
          {r.message}
        </EmptyState>
        {empty}
      </>
    );
  return <ReviewWeek r={r} />;
}

function SeasonBody({ sv, week }: { sv: LiveSeasonReviewResponse; week: number }) {
  if (sv.status === 'no_models') return <NoModels message={sv.message} />;
  if (sv.status === 'no_plays')
    return (
      <EmptyState glyph="—" title="No reviewed week yet">
        {sv.message}
      </EmptyState>
    );
  return <ReviewSeason sv={sv} week={week} />;
}

export function ReviewView({
  season,
  week,
  liveWeek,
  actions,
  empty,
  onBack,
}: {
  season: number;
  week: number;
  liveWeek: { season: number; week: number } | null;
  actions?: ReactNode; // more buttons in the header (the link to Game day's week)
  empty?: ReactNode; // shown under "isn't ready yet" (the week's finals)
  onBack?: () => void; // the current week's final phase: back to the games
}) {
  const [view, setView] = useState<View>('week');
  const rq = useLiveReview(season, week);
  const sq = useLiveSeasonReview(season, week, view === 'season');
  const r = rq.data;
  const sv = sq.data;
  const model = (view === 'season' ? sv?.model : r?.model) ?? r?.model ?? sv?.model ?? null;
  const source = view === 'season' ? (sv?.source ?? null) : (r?.source ?? null);
  const through = sv?.through_week ?? null;
  const title =
    view === 'season'
      ? `The ${season} season${through != null ? ` through ${throughText(season, through)}` : ''}`
      : "Every 4th down: the coach's call next to the bot's";
  const { prev, next } = stepWeeks(season, week, liveWeek);
  const step = (w: number, text: string) => (
    <Link className="btn sm" to={`/week/${season}/${w}/game-day`}>
      {text}
    </Link>
  );

  let body: ReactNode;
  if (view === 'week') {
    if (rq.isPending) body = <Loading season={false} />;
    else if (!r) body = <Failed message={rq.error?.message ?? ''} onRetry={() => void rq.refetch()} retrying={rq.isFetching} />;
    else body = <WeekBody r={r} onSeason={() => setView('season')} empty={empty} />;
  } else if (sq.isPending) body = <Loading season />;
  else if (!sv) body = <Failed message={sq.error?.message ?? ''} onRetry={() => void sq.refetch()} retrying={sq.isFetching} />;
  else body = <SeasonBody sv={sv} week={week} />;

  return (
    <div className="rv">
      {onBack ? (
        <div>
          <button type="button" className="btn sm" onClick={onBack}>
            ← The week&apos;s games
          </button>
        </div>
      ) : null}
      <div className="rv-h">
        <div className="ttl">
          <span className="eyebrow">
            Decision review · {season} {weekName(season, week)}
          </span>
          <h2>{title}</h2>
          <Meta model={model} source={source} />
        </div>
        <div className="right">
          {view === 'week' ? (
            <span className="wkstep">
              {prev ? step(prev, `‹ ${weekName(season, prev)}`) : null}
              {next ? step(next, `${weekName(season, next)} ›`) : null}
            </span>
          ) : null}
          {actions}
          <Segmented
            label="Review of"
            value={view}
            onChange={setView}
            options={[
              { value: 'week', label: 'This week' },
              { value: 'season', label: 'Season' },
            ]}
          />
        </div>
      </div>
      {model?.in_sample ? <InSample model={model} season={season} /> : null}
      {body}
    </div>
  );
}
