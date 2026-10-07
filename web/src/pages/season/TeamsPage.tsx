// Season → Teams & rankings (mockup: teamsView): the biggest Elo risers and fallers, and the power
// rankings (Elo, move, a sparkline, net / off / def EPA) with an AFC / NFC filter. A row opens
// its detail (click, Enter or Space): Elo by week, this week's game, next week's, pass vs rush.

import { Fragment, useState, type KeyboardEvent } from 'react';
import { useTeamInfo, useTeams } from '../../api/client';
import type { TeamGameBrief, TeamRow, TeamsResponse } from '../../api/types';
import { HBars } from '../../charts/HBars';
import { Sparkline } from '../../charts/Sparkline';
import { TeamChip, TeamNick } from '../../components/TeamChip';
import { Card, CardHeader, EmptyState } from '../../components/ui';
import { kickoffLabel, pct, signed } from '../../lib/format';
import { Page } from '../Pages';
import { PageError, PageLoading } from './common';

type Conf = 'All' | 'AFC' | 'NFC';
const COLS = 8;

function weekSpan(weeks: number[]): string {
  if (!weeks.length) return 'Elo';
  return weeks.length === 1 ? `Week ${weeks[0]}` : `Weeks ${weeks[0]}–${weeks[weeks.length - 1]}`;
}

function GameLine({ team, g }: { team: string; g: TeamGameBrief | null }) {
  if (!g) return <b>bye</b>;
  const final = g.away_score != null && g.home_score != null;
  const chance = g.p_home == null ? null : g.home === team ? g.p_home : 1 - g.p_home;
  return (
    <>
      <TeamChip team={g.away} /> <span className="muted">at</span> <TeamChip team={g.home} />
      {' · '}
      {final ? (
        <>
          final <b className="num">{`${g.away_score}–${g.home_score}`}</b>
        </>
      ) : (
        <span>{kickoffLabel(g.kickoff)}</span>
      )}
      {chance != null ? (
        <>
          {' · '}model gave <TeamNick team={team} /> <b>{pct(chance)}</b>
        </>
      ) : null}
    </>
  );
}

function Detail({ t, id }: { t: TeamRow; id: string }) {
  return (
    <tr className="teamdetail" id={id}>
      <td colSpan={COLS}>
        <div className="grid g3" style={{ padding: '6px 4px' }}>
          <div>
            <span className="eyebrow td-h">
              Elo, {weekSpan(t.elo_by_week.map((e) => e.week)).toLowerCase()}
            </span>
            <div className="td-v">
              {t.elo_by_week.map((e, i) => (
                <Fragment key={e.week}>
                  {i ? ' · ' : ''}W{e.week} <b className="num">{Math.round(e.elo)}</b>
                </Fragment>
              ))}
            </div>
          </div>
          <div>
            <span className="eyebrow td-h">
              {t.this_week ? `Week ${t.this_week.week}` : 'This week'}
            </span>
            <div className="td-v">
              <GameLine team={t.team} g={t.this_week} />
            </div>
          </div>
          <div>
            <span className="eyebrow td-h">
              {t.next_week ? `Week ${t.next_week.week}` : 'Next week'}
            </span>
            <div className="td-v">
              <GameLine team={t.team} g={t.next_week} />
            </div>
          </div>
          <div>
            <span className="eyebrow td-h">Pass vs rush (net EPA/play)</span>
            <div className="td-v num">
              pass {signed(t.pass_epa, 3)} · rush {signed(t.rush_epa, 3)}
            </div>
          </div>
        </div>
      </td>
    </tr>
  );
}

function Row({ t, open, onToggle }: { t: TeamRow; open: boolean; onToggle: () => void }) {
  const move = t.prev_rank != null ? t.prev_rank - t.rank : 0;
  const detailId = `team-detail-${t.team}`;
  const onKey = (e: KeyboardEvent<HTMLTableRowElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onToggle();
    }
  };
  return (
    <>
      <tr
        className="teamrow"
        tabIndex={0}
        aria-expanded={open}
        aria-controls={open ? detailId : undefined}
        aria-label={`${t.team}, rank ${t.rank}: ${open ? 'hide' : 'show'} detail`}
        onClick={onToggle}
        onKeyDown={onKey}
      >
        <td className="num">
          <b>{t.rank}</b>
        </td>
        <td>
          <TeamChip team={t.team} full /> {t.div ? <span className="muted">{t.div}</span> : null}
        </td>
        <td className="r num">
          <b>{Math.round(t.elo)}</b>
        </td>
        <td className="r num">
          {move > 0 ? (
            <span className="mover-up" title={`Up ${move} from ${t.prev_rank}`}>
              ▲ {move}
            </span>
          ) : move < 0 ? (
            <span className="mover-down" title={`Down ${-move} from ${t.prev_rank}`}>
              ▼ {-move}
            </span>
          ) : (
            <span className="muted" title={t.prev_rank != null ? 'No change' : 'No earlier week'}>
              —
            </span>
          )}
        </td>
        <td>
          <Sparkline
            values={t.elo_by_week.map((e) => e.elo)}
            label={`${t.team} Elo by week: ${t.elo_by_week.map((e) => `week ${e.week} ${Math.round(e.elo)}`).join(', ')}`}
          />
        </td>
        <td className="r num">{signed(t.net_epa, 3)}</td>
        <td className="r num">{signed(t.off_epa, 3)}</td>
        <td className="r num">{signed(t.def_epa, 3)}</td>
      </tr>
      {open ? <Detail t={t} id={detailId} /> : null}
    </>
  );
}

function Movers({ r }: { r: TeamsResponse }) {
  const info = useTeamInfo().data?.teams;
  const nick = (t: string) => info?.[t]?.nick ?? t;
  const name = (t: string) => info?.[t]?.name ?? t;
  const top = Math.max(
    0,
    ...r.risers.map((x) => Math.abs(x.change)),
    ...r.fallers.map((x) => Math.abs(x.change)),
  );
  const span =
    r.week != null ? `Elo change, entering week ${r.week - 1} → week ${r.week}` : 'Elo change';
  return (
    <div className="grid g2">
      <Card>
        <CardHeader title="Biggest risers" sub={span} />
        <div className="card-b">
          {r.risers.length ? (
            <HBars
              label="Biggest Elo risers"
              max={top || 1}
              color="var(--ok)"
              rows={r.risers.map((x) => ({
                label: nick(x.team),
                value: x.change,
                tip: `${name(x.team)}\n${signed(x.change)} Elo`,
              }))}
              format={(v) => signed(v)}
            />
          ) : (
            <span className="muted">Needs two weeks of Elo.</span>
          )}
        </div>
      </Card>
      <Card>
        <CardHeader title="Biggest fallers" sub={span} />
        <div className="card-b">
          {r.fallers.length ? (
            <HBars
              label="Biggest Elo fallers"
              max={top || 1}
              color="var(--err)"
              rows={r.fallers.map((x) => ({
                label: nick(x.team),
                value: x.change,
                tip: `${name(x.team)}\n${signed(x.change)} Elo`,
              }))}
              format={(v) => signed(v)}
            />
          ) : (
            <span className="muted">Needs two weeks of Elo.</span>
          )}
        </div>
      </Card>
    </div>
  );
}

function Rankings({ r }: { r: TeamsResponse }) {
  const [conf, setConf] = useState<Conf>('All');
  const [openTeam, setOpenTeam] = useState<string | null>(null);
  const rows = conf === 'All' ? r.teams : r.teams.filter((t) => t.conf === conf);
  return (
    <Card>
      <CardHeader
        title="Power rankings"
        sub={`by Elo${r.week != null ? ` entering week ${r.week}` : ''} · click a team for detail`}
        right={
          <div className="filters" role="group" aria-label="Conference">
            {(['All', 'AFC', 'NFC'] as Conf[]).map((c) => (
              <button
                key={c}
                type="button"
                className="fchip"
                aria-pressed={conf === c}
                onClick={() => setConf(c)}
              >
                {c}
              </button>
            ))}
          </div>
        }
      />
      <div className="tablewrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>#</th>
              <th>Team</th>
              <th className="r">Elo</th>
              <th className="r">Move</th>
              <th>{weekSpan(r.weeks)}</th>
              <th className="r">Net EPA</th>
              <th className="r">Off EPA</th>
              <th className="r">Def EPA allowed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <Row
                key={t.team}
                t={t}
                open={openTeam === t.team}
                onToggle={() => setOpenTeam((o) => (o === t.team ? null : t.team))}
              />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function TeamsPage() {
  const q = useTeams();
  const r = q.data;
  return (
    <Page
      eyebrow="Season"
      title="Teams & rankings"
      sub={
        r
          ? r.week != null
            ? `${r.season} · Elo and ratings entering week ${r.week}`
            : `${r.season} · no ratings yet`
          : 'Elo and EPA ratings, week by week'
      }
    >
      {q.isPending ? (
        <PageLoading />
      ) : q.isError ? (
        <PageError error={q.error} what="the rankings" />
      ) : !q.data.teams.length ? (
        <EmptyState glyph="—" title={`No ratings for ${q.data.season} yet`}>
          Elo and the EPA ratings are written by the weekly run's ratings step.
        </EmptyState>
      ) : (
        <>
          <Movers r={q.data} />
          <Rankings r={q.data} />
          <div className="foot">
            Ratings come from <span className="mono">features/team_elo.parquet</span> and{' '}
            <span className="mono">team_ratings.parquet</span>
            {q.data.week != null ? ` as of week ${q.data.week}'s run` : ''}; the slate and win
            chances from the week's predictions.
          </div>
        </>
      )}
    </Page>
  );
}
