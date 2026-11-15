// The Availability tab (FL02, D158; documentation/forecast-lab/README.md §3; mockup:
// documentation/forecast-lab/mockup/, published at https://claude.ai/artifact/NiZt8wAJn6jqL1RWFwxDZU):
// who might sit this week and who takes the work, the biggest role changes, every player of one
// team, and after the games how it did. The strip says which view (Tuesday: no injury report yet;
// Saturday: after the final report) and when each was made; one note says how to read it. The
// team lives in the URL (`?team=`). Read from GET /api/weeks/{S}/{W}/availability[?team=].

import { useSearchParams } from 'react-router-dom';
import { useAvailability } from '../../api/client';
import type { AvailabilityResponse, AvailabilityView } from '../../api/types';
import { ForecastLabUpdateCard } from '../../components/run/ForecastLabUpdate';
import { Notice, Tile } from '../../components/ui';
import { comma } from '../../lib/format';
import { madeAt } from '../../lib/forecastlab';
import { AvailScoreCard, EveryPlayer, HowGood, Movers, RiskSections } from './forecastlab/AvailabilityParts';
import { LabEmpty, MadeStrip } from './forecastlab/common';
import './forecastlab/availability.css';
import { TabError, TabLoading } from './WeekEmpty';

const VIEW_WORD: Record<AvailabilityView, string> = { tuesday: 'Tuesday', saturday: 'Saturday' };
const TEAM_RE = /^[A-Z]{2,3}$/;
/** "2026_10_WAS_NYG" -> "WAS at NYG". */
const gameText = (g: string) => g.split('_').slice(2).join(' at ');

function Strip({ r }: { r: AvailabilityResponse }) {
  const m = r.meta;
  if (!m) return null;
  const views = (['tuesday', 'saturday'] as const).filter((v) => m.views[v]);
  return (
    <MadeStrip
      label="When this was made"
      items={[
        ...views.map((v) => (
          <>
            {VIEW_WORD[v]} view <b>{madeAt(m.views[v]?.made_at)}</b>
          </>
        )),
        r.tiles ? <>showing the {VIEW_WORD[r.tiles.view]} view</> : null,
        m.version ? (
          <>
            model <span className="mono">{m.version}</span>
          </>
        ) : null,
        m.trained_through ? <>trained through {m.trained_through}</> : null,
        m.games_after_kickoff.length ? (
          <>
            {m.games_after_kickoff.length} game{m.games_after_kickoff.length === 1 ? '' : 's'} made after kickoff (
            {m.games_after_kickoff.map(gameText).join(', ')})
          </>
        ) : null,
      ]}
    />
  );
}

function Tiles({ r }: { r: AvailabilityResponse }) {
  const t = r.tiles;
  if (!t) return null;
  const sat = t.view === 'saturday';
  return (
    <div className="tiles">
      <Tile k="Players" v={comma(t.players)} d={`${r.teams.length} teams this week`} />
      <Tile
        k="Might sit"
        v={comma(t.at_risk)}
        d={`${t.by_group.report} on the report · ${t.by_group.uncertain} regulars · ${t.by_group.reserve} reserve`}
      />
      <Tile
        k="This week's report"
        v={
          sat ? (
            <>
              {t.out}
              <small>out</small>
            </>
          ) : (
            '—'
          )
        }
        d={sat ? `${t.doubtful} doubtful · ${t.questionable} questionable` : 'not out yet on Tuesday'}
      />
      <Tile k="View" v={VIEW_WORD[t.view]} d={sat ? 'after the final injury report' : 'before the injury report'} />
    </div>
  );
}

function Body({ r, season, week }: { r: AvailabilityResponse; season: number; week: number }) {
  const [params, setParams] = useSearchParams();
  const asked = params.get('team');
  const team = asked && TEAM_RE.test(asked) && r.teams.includes(asked) ? asked : null;
  const tq = useAvailability(season, week, team);
  const sat = r.tiles?.view === 'saturday';
  const pickTeam = (t: string) => {
    const next = new URLSearchParams(params);
    next.set('team', t);
    setParams(next);
  };
  const players = tq.data?.team === team ? (tq.data?.players ?? []) : [];
  return (
    <div className="av">
      <Strip r={r} />
      <Tiles r={r} />
      <Notice tone="accent">
        {sat ? (
          <>
            <b>This is Saturday&apos;s view:</b> the week&apos;s final injury report is in, so the chance he plays is the sharp one.
            Tuesday&apos;s chance shows beside it.
          </>
        ) : (
          <>
            <b>This is Tuesday&apos;s view:</b> there&apos;s no injury report for the week yet, so the chance he plays leans on last week
            (who played, who was listed, reserve lists). Saturday&apos;s view, after the final report, is the sharp one.
          </>
        )}{' '}
        Snap, target and carry shares are <b>if he plays</b>. A share&apos;s change is in points: 31% to 47% of the carries is +16 points.
      </Notice>
      <RiskSections r={r} />
      <Movers r={r} />
      <EveryPlayer teams={r.teams} team={team} players={players} loading={tq.isFetching} onTeam={pickTeam} />
      <AvailScoreCard r={r} />
      <HowGood />
    </div>
  );
}

function TabBody(props: { season: number; week: number; isCurrent: boolean; lastPublishedWeek: number | null }) {
  const q = useAvailability(props.season, props.week);
  if (q.isLoading) return <TabLoading />;
  if (q.error) return <TabError error={q.error} />;
  const r = q.data;
  if (!r) return null;
  if (r.status !== 'ok') return <LabEmpty tab="availability" status={r.status} hint={r.hint} {...props} />;
  return <Body r={r} season={props.season} week={props.week} />;
}

export function AvailabilityTab({
  season,
  week,
  isCurrent,
  lastPublishedWeek,
}: {
  season: number;
  week: number;
  isCurrent?: boolean;
  lastPublishedWeek?: number | null;
}) {
  return (
    <div className="wk-stack">
      <TabBody season={season} week={week} isCurrent={Boolean(isCurrent)} lastPublishedWeek={lastPublishedWeek ?? null} />
      {/* D158: the fallback when the follow-up after the run didn't happen (current week only) */}
      {isCurrent ? <ForecastLabUpdateCard season={season} week={week} /> : null}
    </div>
  );
}
