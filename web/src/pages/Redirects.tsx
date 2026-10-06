import { Navigate, useParams } from 'react-router-dom';
import { useMeta, useWeeks } from '../api/client';
import { EmptyState } from '../components/ui';

/** `/` → the calendar's current week, Pipeline tab (or the newest week in the offseason). */
export function HomeRedirect() {
  const weeks = useWeeks();
  const meta = useMeta();
  if (weeks.data) {
    const target = weeks.data.current_week ?? weeks.data.weeks[0]?.week;
    if (target) return <Navigate to={`/week/${weeks.data.season}/${target}/pipeline`} replace />;
    return (
      <div className="view">
        <EmptyState glyph="—" title="No weeks yet">
          The calendar has no week to run ({meta.data?.calendar?.phase ?? 'offseason'}) and there are no run
          folders for {weeks.data.season} on the data drive yet.
        </EmptyState>
      </div>
    );
  }
  if (weeks.isError) {
    return (
      <div className="view">
        <EmptyState glyph="!" title="Can't list the weeks">
          {(weeks.error as Error).message}
        </EmptyState>
      </div>
    );
  }
  return <div className="view muted">Loading…</div>;
}

export function WeekRedirect() {
  const { season, week } = useParams();
  return <Navigate to={`/week/${season}/${week}/pipeline`} replace />;
}
