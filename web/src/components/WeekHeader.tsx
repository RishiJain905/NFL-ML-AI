// The header above a week's tabs (mockup: renderTop). The current week shows the countdown to
// its first kickoff and the retry window; a past week shows when it was published.

import type { Calendar, WeekEntry } from '../api/types';
import { countdown, daysRange, fmtET, kickoffLabel, seasonEyebrow, specialChips, weekTitle } from '../lib/format';
import { useNow } from '../lib/useNow';
import { Chip, WeekStatusChip } from './ui';

function injuryUpdateReason(entry: WeekEntry): string {
  if (entry.status !== 'published') return "Needs this week's Tuesday run first";
  return 'Opens on Saturday (CR02)';
}

export function WeekHeader({ season, week, entry, calendar }: { season: number; week: number; entry: WeekEntry | undefined; calendar: Calendar | null | undefined }) {
  const now = useNow();
  const isCurrent = Boolean(entry?.is_current ?? (calendar && calendar.season === season && calendar.week === week));
  const cal = isCurrent ? calendar : null;
  const left = countdown(now, cal?.deadline);

  return (
    <header className="top">
      <div className="titleblock">
        <span className="eyebrow">
          {seasonEyebrow(season, cal?.phase)}
          {isCurrent ? ' · this week' : ''}
        </span>
        <h1>{weekTitle(week, cal?.game_type)}</h1>
        <div className="chips">
          {entry ? <WeekStatusChip status={entry.status} label={entry.label} /> : <Chip tone="ghost">No run</Chip>}
          {cal ? (
            <>
              <Chip>
                {cal.games} games{cal.days.length ? ` · ${daysRange(cal.days)}` : ''}
              </Chip>
              {specialChips(cal.special).map((s) => (
                <Chip key={s}>{s}</Chip>
              ))}
              {cal.international.map((g) => (
                <Chip key={g}>Abroad: {g.replace('@', ' at ')}</Chip>
              ))}
              {cal.byes.length ? <Chip>Byes: {cal.byes.join(', ')}</Chip> : null}
            </>
          ) : null}
          {!isCurrent && entry && !entry.has_run_summary && entry.has_run ? (
            <Chip tone="ghost" title="This week ran before P07 added run records">
              No run records
            </Chip>
          ) : null}
        </div>
      </div>
      <div className="right">
        {cal ? (
          <div className="clock">
            <span className="small">First kickoff</span>
            <span className="big">{left.text}</span>
            <span className="small">
              {kickoffLabel(cal.deadline)}
              {cal.retry_until && !left.passed
                ? ` · retry window ends ${fmtET(cal.retry_until, { weekday: 'short', hour: 'numeric' })}`
                : ''}
            </span>
          </div>
        ) : entry?.published_at ? (
          <div className="clock">
            <span className="small">Published</span>
            <span className="big">{fmtET(entry.published_at, { weekday: 'short', hour: 'numeric', minute: '2-digit' })}</span>
            <span className="small">{fmtET(entry.published_at, { month: 'short', day: 'numeric' })} ET</span>
          </div>
        ) : null}
        {entry && isCurrent ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
            <button type="button" className="btn sm" aria-disabled="true" disabled title="Runs on Saturday, after the week's Tuesday run">
              Saturday injury update
            </button>
            <span className="reason">{injuryUpdateReason(entry)}</span>
          </div>
        ) : null}
      </div>
    </header>
  );
}
