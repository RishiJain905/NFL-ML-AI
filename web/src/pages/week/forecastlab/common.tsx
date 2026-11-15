// What the Availability and Outcomes week tabs (FL02, D158) share: the strip that says when the
// week's file was made and from which model, the designed empty states (`no_model`: nothing
// promoted yet; `none`: nothing for this week yet), and the hint's command split out.

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { ForecastLabStatus } from '../../../api/types';
import { EmptyState } from '../../../components/ui';
import { splitCmd } from '../../../lib/forecastlab';
import './forecastlab.css';

export type LabTab = 'availability' | 'outcomes';

const TAB_NAME: Record<LabTab, string> = { availability: 'Availability', outcomes: 'Outcomes' };

/** The strip at the top of a tab with data: when it was made, from which model. */
export function MadeStrip({ items, label }: { items: ReactNode[]; label: string }) {
  return (
    <div className="flstrip" role="note" aria-label={label}>
      {items.filter(Boolean).map((x, i) => (
        <span key={i}>{x}</span>
      ))}
    </div>
  );
}

const WHAT: Record<LabTab, { glyph: string; noModel: string; body: string }> = {
  availability: {
    glyph: 'AV',
    noModel: 'No availability model yet',
    body: 'This tab shows who might sit this week: the chance each player plays, his expected snap and target or carry share against his usual, and who takes the work if he sits.',
  },
  outcomes: {
    glyph: 'OC',
    noModel: 'No outcome-curves model yet',
    body: "This tab shows the whole curve behind each player projection: the chance he clears any number, or lands in a band you pick, next to a plain bell curve's.",
  },
};

/** The tab's empty state from the answer's `status` and `hint`. */
export function LabEmpty({
  tab,
  status,
  hint,
  season,
  week,
  isCurrent,
  lastPublishedWeek,
}: {
  tab: LabTab;
  status: Exclude<ForecastLabStatus, 'ok'>;
  hint: string | null;
  season: number;
  week: number;
  isCurrent: boolean;
  lastPublishedWeek: number | null;
}) {
  const w = WHAT[tab];
  const { text, cmd } = splitCmd(hint);
  if (status === 'no_model') {
    return (
      <EmptyState
        glyph={w.glyph}
        title={w.noModel}
        actions={
          cmd ? (
            <div className="flacts">
              <div className="cmd">{cmd}</div>
            </div>
          ) : undefined
        }
      >
        {w.body} {text}
      </EmptyState>
    );
  }
  if (!isCurrent) {
    return (
      <EmptyState glyph={w.glyph} title={`No ${tab === 'availability' ? 'availability' : 'outcome curves'} for week ${week}`}>
        Week {week} has no {tab === 'availability' ? 'availability' : 'curves'} file: the Forecast lab started later, or its update didn&apos;t
        run that week.
      </EmptyState>
    );
  }
  return (
    <EmptyState
      glyph={w.glyph}
      title={`Week ${week}'s ${tab === 'availability' ? 'availability' : 'outcome curves'} ${tab === 'availability' ? "isn't" : "aren't"} made yet`}
      actions={
        <div className="flacts">
          {cmd ? <div className="cmd">{cmd}</div> : null}
          {lastPublishedWeek != null && lastPublishedWeek !== week ? (
            <Link className="btn sm" to={`/week/${season}/${lastPublishedWeek}/${tab}`}>
              See week {lastPublishedWeek}&apos;s {TAB_NAME[tab]}
            </Link>
          ) : null}
        </div>
      }
    >
      {text || `It's made right after Run week ${week}.`}
    </EmptyState>
  );
}
