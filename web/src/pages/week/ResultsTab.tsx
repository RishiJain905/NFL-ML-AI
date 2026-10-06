// The Results tab (mockup: w4Results, with real data in place of its sample): how this week's
// calls did once the next week's run graded them. Tiles, game by game, the player model vs its
// baseline by group, and the watch list with the actual marked on its range (◆).

import { Link } from 'react-router-dom';
import { useResults } from '../../api/client';
import type { ResultGame, ResultWatch, ResultsResponse } from '../../api/types';
import { HBars } from '../../charts/HBars';
import { RangeBar } from '../../charts/RangeBar';
import { TeamChip } from '../../components/TeamChip';
import { Card, CardHeader, Chip, EmptyState, Notice, Tile } from '../../components/ui';
import { fixed, pct, signed } from '../../lib/format';
import { TabError, TabLoading, WarnNotice } from './WeekEmpty';
import { dateLabel, statNum } from './weekUtil';
import './week.css';

type Graded = Extract<ResultsResponse, { status: 'graded' }>;
type NotGraded = Extract<ResultsResponse, { status: 'not_graded' }>;

const b3 = (v: number | null) => fixed(v, 3);

function GameRowView({ g }: { g: ResultGame }) {
  return (
    <tr>
      <td>
        <TeamChip team={g.away} /> <span className="muted">at</span> <TeamChip team={g.home} />
      </td>
      <td>
        {g.pick ? (
          <>
            <TeamChip team={g.pick} full /> <span className="muted">{pct(g.pick_prob)}</span>
          </>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="num">{g.final ? `${g.final.away}–${g.final.home}` : '—'}</td>
      <td className="c">
        {!g.graded ? (
          <>
            <Chip tone="ghost">not graded</Chip>
            {g.note ? <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{g.note}</div> : null}
          </>
        ) : g.hit === true ? (
          <Chip tone="ok" icon="✓">
            hit
          </Chip>
        ) : g.hit === false ? (
          <Chip tone="err" icon="✕">
            miss
          </Chip>
        ) : (
          <Chip>tie or 50%</Chip>
        )}
      </td>
      <td className="r num">{b3(g.brier_model)}</td>
      <td className="r num">{b3(g.brier_elo)}</td>
      <td className="r num">{b3(g.brier_market)}</td>
      <td className="r num">{signed(g.margin_error, 1)}</td>
    </tr>
  );
}

function WatchRow({ w }: { w: ResultWatch }) {
  return (
    <tr className={w.played ? undefined : 'dnp'}>
      <td>
        <b>{w.player}</b>{' '}
        <span className="muted">
          {w.position} · {w.team}
        </span>
      </td>
      <td>{w.target_label}</td>
      <td className="r num">{statNum(w.projection)}</td>
      <td style={{ minWidth: 170 }}>
        <RangeBar who={w.player} p10={w.p10} p90={w.p90} projection={w.projection} baseline={w.baseline} actual={w.actual} />
      </td>
      <td className="r num">
        <b>{w.actual == null ? '—' : statNum(w.actual)}</b>
      </td>
      {w.played && w.actual != null ? (
        <>
          <td className="c">
            {w.inside === true ? (
              <Chip tone="ok" icon="✓">
                inside
              </Chip>
            ) : w.inside === false ? (
              <Chip tone="warn">outside</Chip>
            ) : (
              <span className="muted" title="No range to grade against (a heuristic pick)">
                —
              </span>
            )}
          </td>
          <td className="c">
            {w.hit === true ? (
              <Chip tone="ok" icon="✓">
                beat
              </Chip>
            ) : w.hit === false ? (
              <Chip tone="err" icon="✕">
                under
              </Chip>
            ) : (
              <span className="muted" title="Not graded against the baseline">
                —
              </span>
            )}
          </td>
        </>
      ) : (
        <td className="c" colSpan={2}>
          {w.status || 'did not play'}
        </td>
      )}
    </tr>
  );
}

const WATCH_COLS = 7;

function WatchCard({ watch }: { watch: ResultWatch[] }) {
  const sided = watch.some((w) => w.side);
  const sections: { name: string | null; rows: ResultWatch[] }[] = sided
    ? [
        { name: 'Offense', rows: watch.filter((w) => w.side === 'offense') },
        { name: 'Defense', rows: watch.filter((w) => w.side === 'defense') },
        { name: 'Other', rows: watch.filter((w) => !w.side) },
      ].filter((x) => x.rows.length)
    : [{ name: null, rows: watch }];
  return (
    <Card>
      <CardHeader title="Watch list" sub="projection, range and the actual" />
      <div className="card-b" style={{ paddingBottom: 0 }}>
        <div className="legend">
          <span>
            <i style={{ background: 'color-mix(in srgb,var(--s1) 30%,transparent)', borderRadius: 2 }} />
            80% range
          </span>
          <span>
            <i className="line" style={{ background: 'var(--s1)', width: 3, height: 12 }} />
            Projection
          </span>
          <span>
            <i className="line" style={{ borderLeft: '2px dotted var(--ink-2)', background: 'none', width: 2, height: 12 }} />
            Baseline
          </span>
          <span>
            <i style={{ background: 'var(--s2)', borderRadius: 1, transform: 'rotate(45deg)', width: 9, height: 9 }} />
            Actual
          </span>
        </div>
      </div>
      <div className="tablewrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Player</th>
              <th>Stat</th>
              <th className="r">Proj.</th>
              <th>Range · ◆ actual</th>
              <th className="r">Actual</th>
              <th className="c">Range</th>
              <th className="c">Baseline</th>
            </tr>
          </thead>
          {sections.map((sec) => {
            const graded = sec.rows.filter((w) => w.played && w.actual != null && w.hit != null);
            return (
              <tbody key={sec.name ?? 'all'} aria-label={sec.name ?? undefined}>
                {sec.name ? (
                  <tr className="grp">
                    <td colSpan={WATCH_COLS}>
                      <h3 className="sub-h">{sec.name}</h3>{' '}
                      <span className="muted">
                        {graded.filter((w) => w.hit).length}/{graded.length} beat their baseline
                      </span>
                    </td>
                  </tr>
                ) : null}
                {sec.rows.map((w) => (
                  <WatchRow key={`${w.player_id}-${w.target_label}`} w={w} />
                ))}
              </tbody>
            );
          })}
        </table>
      </div>
    </Card>
  );
}

function GradedView({ r }: { r: Graded }) {
  const t = r.tiles;
  const eloGap = t.brier_model != null && t.brier_elo != null ? t.brier_elo - t.brier_model : null;
  const topGroup = Math.max(0, ...r.players.map((g) => Math.abs(g.improvement_pct)));

  return (
    <div className="wk-stack">
      <div className="tiles">
        <Tile
          k="Picks right"
          v={t.picks_correct != null && t.picks_total != null ? `${t.picks_correct}/${t.picks_total}` : '—'}
          d={`${t.picks_correct != null && t.picks_total ? pct(t.picks_correct / t.picks_total) : '—'} of games${
            t.not_graded ? ` · ${t.not_graded} not graded` : ''
          }`}
        />
        <Tile k="Brier · model" v={b3(t.brier_model)} d="lower is better" />
        <Tile
          k="Brier · Elo"
          v={b3(t.brier_elo)}
          d={eloGap == null ? undefined : `${eloGap >= 0 ? 'model better' : 'Elo better'} by ${Math.abs(eloGap).toFixed(3)}`}
        />
        <Tile k="Brier · market" v={b3(t.brier_market)} d="closing lines" />
        <Tile
          k="Watch list"
          v={t.watch_hits != null && t.watch_total != null ? `${t.watch_hits}/${t.watch_total}` : '—'}
          d="beat their baseline"
        />
      </div>
      {!r.consistent ? (
        <WarnNotice>
          <b>The recomputed numbers differ from the report card;</b> the report card's are shown.
        </WarnNotice>
      ) : null}
      {r.note ? <Notice>{r.note}</Notice> : null}
      <Card>
        <CardHeader title="Game by game" sub="the digest's pick against the final score" />
        <div className="tablewrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Game</th>
                <th>Pick</th>
                <th>Final</th>
                <th className="c">Result</th>
                <th className="r">Brier model</th>
                <th className="r">Elo</th>
                <th className="r">Market</th>
                <th className="r">Margin error</th>
              </tr>
            </thead>
            <tbody>
              {r.games.map((g) => (
                <GameRowView key={g.game_id} g={g} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {r.players.length ? (
        <Card>
          <CardHeader title="Player model vs baseline" sub="MAE improvement by group" />
          <div className="card-b" style={{ maxWidth: 760 }}>
            <HBars
              label="Player model vs baseline by group"
              max={topGroup || 1}
              rows={r.players.map((g) => ({
                label: g.group,
                value: g.improvement_pct,
                tip: [
                  g.group,
                  `${signed(g.improvement_pct)}% vs the rolling baseline · ${g.n} projections`,
                  ...g.targets.map((x) => `${x.label}: ${signed(x.improvement_pct)}%`),
                ].join('\n'),
              }))}
              format={(v) => `${signed(v)}%`}
            />
          </div>
        </Card>
      ) : (
        <Notice>No scoreboard rows for this week, so there's no player model vs baseline by group.</Notice>
      )}
      <WatchCard watch={r.watch} />
    </div>
  );
}

function NotGradedView({ r, isCurrent, lastPublishedWeek }: { r: NotGraded; isCurrent: boolean; lastPublishedWeek: number | null }) {
  return (
    <EmptyState
      glyph={r.graded_by_week != null ? `W${r.graded_by_week}` : 'SB'}
      title={
        r.graded_by_week != null
          ? `Week ${r.week}'s results are graded by week ${r.graded_by_week}'s run`
          : `Week ${r.week} is graded after the season`
      }
      actions={
        <>
          {r.reason ? (
            <p className="muted" style={{ margin: 0 }}>
              {r.reason}
            </p>
          ) : null}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}>
            {r.graded_by_week != null ? (
              <Link className="btn sm" to={`/week/${r.season}/${r.graded_by_week}/pipeline`}>
                Go to the pipeline
              </Link>
            ) : null}
            {isCurrent && lastPublishedWeek != null && lastPublishedWeek !== r.week ? (
              <Link className="btn sm" to={`/week/${r.season}/${lastPublishedWeek}/results`}>
                See week {lastPublishedWeek}'s Results
              </Link>
            ) : null}
          </div>
        </>
      }
    >
      {r.graded_by_week == null
        ? 'No weekly run comes after it.'
        : `${r.graded_on ? `On ${dateLabel(r.graded_on)}` : 'On the next Tuesday'} the run grades this week: each game against the final score, each projection against the box score, the watch list against its baselines.`}
    </EmptyState>
  );
}

export function ResultsTab({
  season,
  week,
  isCurrent,
  lastPublishedWeek,
}: {
  season: number;
  week: number;
  isCurrent: boolean;
  lastPublishedWeek: number | null;
}) {
  const q = useResults(season, week);
  if (q.isPending) return <TabLoading />;
  if (q.isError) return <TabError error={q.error} />;
  const r = q.data;
  if (r.status === 'not_graded') return <NotGradedView r={r} isCurrent={isCurrent} lastPublishedWeek={lastPublishedWeek} />;
  return <GradedView r={r} />;
}
