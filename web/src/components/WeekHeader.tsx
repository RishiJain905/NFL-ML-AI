// The header above a week's tabs (mockup: renderTop). The current week shows the countdown to
// its first kickoff and the retry window; a past week shows when it was published, whether
// that was before the first kickoff, and a link to its digest run in W&B. Slate tags come
// from the calendar's plan for that week (`detail.plan`), so past weeks have them too.

import { useGames, useTeamInfo } from '../api/client';
import type { Calendar, WeekDetail, WeekEntry } from '../api/types';
import { countdown, daysRange, fmtET, kickoffLabel, seasonEyebrow, specialChips, weekTitle } from '../lib/format';
import { useNow } from '../lib/useNow';
import { TeamChip } from './TeamChip';
import { Chip, WeekStatusChip } from './ui';

function injuryUpdateReason(entry: WeekEntry): string {
  if (entry.status !== 'published') return "Needs this week's Tuesday run first";
  return 'Opens on Saturday (CR02)';
}

function FirstKickoff({ season, week }: { season: number; week: number }) {
  const games = useGames(season, week);
  const first = games.data?.games[0];
  if (!first) return <span className="small">First kickoff</span>;
  return (
    <span className="small">
      First kickoff · <TeamChip team={first.away} /> at <TeamChip team={first.home} />
    </span>
  );
}

export function WeekHeader({
  season,
  week,
  entry,
  calendar,
  detail,
}: {
  season: number;
  week: number;
  entry: WeekEntry | undefined;
  calendar: Calendar | null | undefined;
  detail?: WeekDetail;
}) {
  const now = useNow();
  const teams = useTeamInfo().data?.teams;
  const isCurrent = Boolean(
    detail?.is_current ?? entry?.is_current ?? (calendar && calendar.season === season && calendar.week === week),
  );
  const plan = detail?.plan ?? (isCurrent ? calendar : null);
  const left = countdown(now, isCurrent ? plan?.deadline : null);
  const status = detail?.entry ?? entry;
  const publishedAt = detail?.published_at ?? status?.published_at ?? null;
  const nick = (t: string) => teams?.[t]?.nick ?? t;
  const abroad = (g: string) => {
    const [away, home] = g.split('@');
    return `${nick(away)} at ${nick(home)}`;
  };

  return (
    <header className="top">
      <div className="titleblock">
        <span className="eyebrow">
          {seasonEyebrow(season, plan?.phase)}
          {isCurrent ? ' · this week' : ''}
        </span>
        <h1>{detail?.title ?? weekTitle(week, plan?.game_type)}</h1>
        <div className="chips">
          {status ? <WeekStatusChip status={status.status} label={status.label} /> : <Chip tone="ghost">No run</Chip>}
          {!isCurrent && detail?.on_time === false ? (
            <Chip tone="warn" icon="!" title="Published after the week's first kickoff">
              Late run{detail.first_live_week ? ' · first live week' : ''}
            </Chip>
          ) : null}
          {!isCurrent && detail?.on_time === true ? (
            <Chip tone="ok" icon="✓" title="Published before the week's first kickoff">
              On time
            </Chip>
          ) : null}
          {plan ? (
            <>
              <Chip>
                {plan.games} games{plan.days.length ? ` · ${daysRange(plan.days)}` : ''}
              </Chip>
              {specialChips(plan.special.filter((s) => s !== 'international' && s !== 'neutral_site')).map((s) => (
                <Chip key={s}>{s}</Chip>
              ))}
              {plan.international.map((g) => (
                <Chip key={g} title="Played abroad: a neutral site">
                  {abroad(g)} abroad
                </Chip>
              ))}
              {plan.neutral_sites
                .filter((g) => !plan.international.includes(g))
                .map((g) => (
                  <Chip key={g}>{abroad(g)} · neutral site</Chip>
                ))}
              {plan.byes.length ? <Chip>Byes: {plan.byes.join(', ')}</Chip> : null}
            </>
          ) : null}
          {!isCurrent && status && !status.has_run_summary && status.has_run ? (
            <Chip tone="ghost" title="This week ran before P07 added run records">
              No run records
            </Chip>
          ) : null}
        </div>
      </div>
      <div className="right">
        {isCurrent && plan ? (
          <div className="clock">
            <FirstKickoff season={season} week={week} />
            <span className="big">{left.text}</span>
            <span className="small">
              {kickoffLabel(plan.deadline)}
              {plan.retry_until && !left.passed
                ? ` · retry window ends ${fmtET(plan.retry_until, { weekday: 'short', hour: 'numeric' })}`
                : ''}
            </span>
          </div>
        ) : publishedAt ? (
          <div className="clock">
            <span className="small">Published</span>
            <span className="big">{fmtET(publishedAt, { weekday: 'short', hour: 'numeric', minute: '2-digit' })}</span>
            <span className="small">
              {fmtET(publishedAt, { month: 'short', day: 'numeric' })} ET
              {detail?.on_time === false ? " · after the week's first kickoff" : ''}
            </span>
          </div>
        ) : null}
        {status && isCurrent ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
            <button type="button" className="btn sm" aria-disabled="true" disabled title="Runs on Saturday, after the week's Tuesday run">
              Saturday injury update
            </button>
            <span className="reason">{injuryUpdateReason(status)}</span>
          </div>
        ) : null}
        {!isCurrent && detail?.wandb_url ? (
          <a className="btn sm" href={detail.wandb_url} target="_blank" rel="noopener noreferrer" title="The week's digest run in W&B">
            Open week {week} in W&amp;B ↗
          </a>
        ) : null}
      </div>
    </header>
  );
}
