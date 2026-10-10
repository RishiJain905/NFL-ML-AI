// The season beside a finished week (LD03; mockup: review.js seasonBody): the league's numbers,
// the coach aggressiveness leaderboard, the bot's calibration on real plays (win chance,
// conversion, field goals) and the weekly trend. Series: the bot --s1, coaches --s2, nflfastR's
// vegas_wp --s3; what happened is ink.

import { useState } from 'react';
import type { LiveSeasonReviewResponse, ReviewCalibration, ReviewCoachRow } from '../../../api/types';
import { ConversionChart, type ConversionPoint } from '../../../charts/ConversionChart';
import { LineChart } from '../../../charts/LineChart';
import { ReliabilityChart } from '../../../charts/ReliabilityChart';
import { TeamChip } from '../../../components/TeamChip';
import { Card, Notice } from '../../../components/ui';
import { comma } from '../../../lib/format';
import { p0, p1 } from './model';
import { type LeaderSort, smallSampleText, sortLeaders, throughText, weekName, weekShort, wins } from './review';

type Wp = NonNullable<ReviewCalibration['wp']>;
type Conv = NonNullable<ReviewCalibration['conversion']>;
type Fg = NonNullable<ReviewCalibration['fg']>;

function LeagueTiles({ L, sv }: { L: ReviewCoachRow; sv: LiveSeasonReviewResponse }) {
  const rest = L.wp_lost - L.wp_lost_timid - L.wp_lost_bold;
  return (
    <div className="rv-sum season">
      <div className="tile">
        <span className="k">4th downs decided</span>
        <span className="v num">{comma(sv.decisions)}</span>
        <span className="d">
          {sv.weeks.length} week{sv.weeks.length === 1 ? '' : 's'} · {sv.leaderboard.length} head coaches
        </span>
      </div>
      <div className="tile">
        <span className="k">Went for it in go spots</span>
        <span className="v num">{p0(L.go_rate_spots)}</span>
        <span className="d">
          {L.went_in_go_spots} of {L.go_spots}, league-wide
        </span>
      </div>
      <div className="tile">
        <span className="k">Went for it in kick spots</span>
        <span className="v num">{p0(L.go_rate_kick_spots)}</span>
        <span className="d">
          {L.went_in_kick_spots} of {L.kick_spots}: the bot says kick
        </span>
      </div>
      <div className="tile" title={`The rest (${wins(rest)}): toss-ups, and a field goal vs a punt.`}>
        <span className="k">Expected wins given up</span>
        <span className="v num">{wins(L.wp_lost)}</span>
        <span className="d">
          {wins(L.wp_lost_timid)} by kicking in go spots · {wins(L.wp_lost_bold)} by going in kick spots
        </span>
      </div>
      <div className="tile">
        <span className="k">Coach and bot agree</span>
        <span className="v num">{p0(L.agree_rate)}</span>
        <span className="d">
          {comma(L.agree)} of {comma(L.decisions)}
        </span>
      </div>
    </div>
  );
}

function RateBar({ rate, league }: { rate: number | null; league: number | null }) {
  return (
    <span className="ratebar" aria-hidden="true">
      <span className="bg" />
      {rate != null ? <i style={{ width: `${(rate * 100).toFixed(1)}%` }} /> : null}
      <span className="lg" style={{ left: `${((league ?? 0) * 100).toFixed(1)}%` }} />
    </span>
  );
}

const SORT_LABEL: Record<LeaderSort, string> = { rank: '#', spots: 'Go spots', lost: 'Wins given up', agree: 'Agree' };

function Leaderboard({ sv, L }: { sv: LiveSeasonReviewResponse; L: ReviewCoachRow }) {
  const [sort, setSort] = useState<LeaderSort>('rank');
  const rows = sortLeaders(sv.leaderboard, sort);
  const sortTh = (k: LeaderSort, title: string, cls = 'num') => (
    <th className={cls} title={title} aria-sort={sort === k ? (k === 'rank' ? 'ascending' : 'descending') : undefined}>
      <button type="button" className="th-sort" aria-pressed={sort === k} onClick={() => setSort(k)}>
        {SORT_LABEL[k]}
        {sort === k ? ' ▾' : ''}
      </button>
    </th>
  );
  return (
    <Card className="lb">
      <div className="card-h">
        <h2>Coach aggressiveness</h2>
        <span className="muted">ranked by how often he went for it where the bot says go (Confident or Lean)</span>
        <div className="right">
          <span className="lb-key">
            <span>
              <i className="b" />
              went for it in go spots
            </span>
            <span>
              <i className="l" />
              league {p0(L.go_rate_spots)}
            </span>
          </span>
        </div>
      </div>
      <div className="tablewrap">
        <table className="tbl">
          <thead>
            <tr>
              {sortTh('rank', 'Rank: go rate in go spots, then go spots', '')}
              <th>Coach</th>
              <th className="hide-sm">Team</th>
              {sortTh('spots', 'The bot says go, Confident or Lean', 'num hide-sm')}
              <th className="num hide-sm">Went</th>
              <th className="num">Rate in go spots</th>
              <th className="num hide-sm" title="Every 4th down">
                Go rate, all
              </th>
              <th className="num hide-sm" title="The bot says kick (Confident or Lean) and he went anyway">
                Went in kick spots
              </th>
              {sortTh('lost', 'Expected wins given up against the bot: timid = kicked in go spots, bold = went in kick spots')}
              {sortTh('agree', "Made the bot's call", 'num hide-sm')}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.coach}-${r.rank}`} className={r.go_spots < 5 ? 'few' : undefined}>
                <td className="rk">{r.rank}</td>
                <td className="cn">
                  {r.coach}
                  <small>
                    <span className="sm-only">{(r.teams ?? []).join(', ')} · </span>
                    {r.weeks} week{r.weeks === 1 ? '' : 's'}
                    {r.go_spots < 5 ? ' · few spots' : ''}
                  </small>
                </td>
                <td className="hide-sm">
                  {(r.teams ?? []).map((t) => (
                    <TeamChip key={t} team={t} />
                  ))}
                </td>
                <td className="num hide-sm">{r.go_spots}</td>
                <td className="num hide-sm">{r.went_in_go_spots}</td>
                <td className="num">
                  <span className="ratecell">
                    <RateBar rate={r.go_rate_spots} league={L.go_rate_spots} />
                    <b>{p0(r.go_rate_spots)}</b>
                  </span>
                  <small className="sub sm-only">
                    {r.went_in_go_spots} of {r.go_spots}
                  </small>
                </td>
                <td className="num hide-sm">
                  {p0(r.go_rate)}
                  <small className="sub">
                    {r.go} of {r.decisions}
                  </small>
                </td>
                <td className="num hide-sm">
                  {r.went_in_kick_spots}
                  <small className="sub">of {r.kick_spots}</small>
                </td>
                <td className="num">
                  {wins(r.wp_lost)}
                  <small className="sub">
                    {wins(r.wp_lost_timid)} timid · {wins(r.wp_lost_bold)} bold
                  </small>
                </td>
                <td className="num hide-sm">{p0(r.agree_rate)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td />
              <td className="cn">League</td>
              <td className="hide-sm" />
              <td className="num hide-sm">{L.go_spots}</td>
              <td className="num hide-sm">{L.went_in_go_spots}</td>
              <td className="num">
                <span className="ratecell">
                  <RateBar rate={L.go_rate_spots} league={L.go_rate_spots} />
                  <b>{p0(L.go_rate_spots)}</b>
                </span>
              </td>
              <td className="num hide-sm">{p0(L.go_rate)}</td>
              <td className="num hide-sm">
                {L.went_in_kick_spots}
                <small className="sub">of {L.kick_spots}</small>
              </td>
              <td className="num">{wins(L.wp_lost)}</td>
              <td className="num hide-sm">{p0(L.agree_rate)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </Card>
  );
}

function Stat({ k, a, b, f, note }: { k: string; a: number | null; b: number | null; f: (v: number | null) => string; note?: string }) {
  return (
    <div className="st">
      <span className="k">{k}</span>
      <span className="v">
        <span>
          <i style={{ background: 'var(--s1)' }} />
          <b>{f(a)}</b>the bot
        </span>
        <span>
          <i style={{ background: 'var(--s3)', borderRadius: 1 }} />
          <b>{f(b)}</b>vegas_wp
        </span>
      </span>
      {note ? <span className="k">{note}</span> : null}
    </div>
  );
}

function Calibration({ wp, season }: { wp: Wp; season: number }) {
  const bins = (bs: Wp['model']) => bs.map((b) => ({ lo: b.bin_lo, hi: Math.min(1, b.bin_lo + 0.1), n: b.n, predicted: b.mean_pred, observed: b.mean_outcome }));
  const tip = (i: number) => {
    const m = wp.model[i];
    // empty bins are left out of each list on its own: pair the two by the bin, not the index
    const v = wp.vegas.find((b) => Math.abs(b.bin_lo - m.bin_lo) < 1e-9);
    const lo = Math.round(m.bin_lo * 100);
    return [
      `Said ${lo}–${lo + 10}%`,
      `The bot: said ${p1(m.mean_pred)}, won ${p1(m.mean_outcome)} (${comma(m.n)} snaps)`,
      v ? `vegas_wp: said ${p1(v.mean_pred)}, won ${p1(v.mean_outcome)} (${comma(v.n)} snaps)` : 'vegas_wp: —',
    ];
  };
  const f3 = (v: number | null) => (v == null ? '—' : v.toFixed(3));
  const fe = (v: number | null) => (v == null ? '—' : (v * 100).toFixed(1));
  return (
    <Card>
      <div className="card-h">
        <h2>Is the bot&apos;s win chance right?</h2>
        <span className="muted">every snap of the season&apos;s finished games, grouped by what the bot said, against who won</span>
      </div>
      <div className="cal">
        <ReliabilityChart
          label={`Calibration of the bot's win probability, ${season}`}
          series={[
            { name: 'The bot', color: 'var(--s1)', shape: 'circle', bins: bins(wp.model) },
            { name: "nflfastR's vegas_wp (same snaps)", color: 'var(--s3)', shape: 'square', bins: bins(wp.vegas) },
          ]}
          tip={tip}
          nLabel="snaps"
          xLabel="the offense's win chance, as predicted before the snap →"
          yLabel="↑ how often it won"
        />
        <div className="calstats">
          <Stat k="Brier score (lower is better)" a={wp.brier} b={wp.brier_vegas} f={f3} />
          <Stat k="Calibration error (ECE, points)" a={wp.ece} b={wp.ece_vegas} f={fe} note="the average distance from the diagonal, weighted by snaps" />
          <p>
            {comma(wp.snaps)} snaps in {wp.games} games
            {wp.tie_games ? ` (${wp.tie_games} tie${wp.tie_games > 1 ? 's' : ''}, left out)` : ''}. A point above the diagonal: teams
            won more often than the bot said.
          </p>
        </div>
      </div>
    </Card>
  );
}

function Conversion({ cv }: { cv: Conv }) {
  const panel = (down: 3 | 4) => {
    const o = cv.overall.find((x) => x.down === down);
    const what = down === 3 ? '3rd downs' : '4th-down tries';
    const unit = down === 3 ? 'plays' : 'tries';
    const points: ConversionPoint[] = cv.rows
      .filter((r) => r.down === down)
      .sort((a, b) => a.distance - b.distance)
      .map((r) => ({ label: r.label, n: r.n, predicted: r.pred, actual: r.actual }));
    const tip = (p: ConversionPoint) => [
      `${down === 3 ? '3rd' : '4th'} & ${p.label}`,
      `The bot said ${p0(p.predicted)}`,
      `Converted ${p0(p.actual)} (${p.n} ${unit})`,
      ...(p.n < 10 ? ['Fewer than 10: read loosely'] : []),
    ];
    return (
      <div>
        <h3>
          {down === 3 ? '3rd downs' : '4th-down tries'}
          <small>{down === 3 ? 'every one, the fair test' : 'only where the coach went'}</small>
        </h3>
        <p className="ov">
          {what}: converted <b>{p1(o?.actual)}</b>, the bot said <b>{p1(o?.pred)}</b> · {comma(o?.n ?? 0)} {unit}
        </p>
        <ConversionChart label={`${down === 3 ? 'Third' : 'Fourth'}-down conversion: predicted vs actual by distance`} points={points} tip={tip} xLabel="yards to go →" />
      </div>
    );
  };
  return (
    <Card>
      <div className="card-h">
        <h2>Does the bot&apos;s conversion chance match what happened?</h2>
        <span className="muted">by yards to go; n under each distance</span>
        <div className="right">
          <span className="legend">
            <span>
              <i style={{ background: 'var(--s1)' }} />
              The bot said
            </span>
            <span>
              <i className="hollow" />
              Converted
            </span>
          </span>
        </div>
      </div>
      <div className="chartpad">
        <Notice>
          <b>Tries convert more often than the bot says.</b> Coaches go for it when they expect to make it (a short 4th &amp; 2, a weak
          defence), so tries convert more often than an average spot would: that&apos;s selection, not the bot being wrong. 3rd
          downs have no such choice, so they sit next to them as the fair test.
        </Notice>
        <div className="conv">
          {panel(3)}
          {panel(4)}
        </div>
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
          Dashed circles: fewer than 10 plays at that distance.
        </p>
      </div>
    </Card>
  );
}

function FieldGoals({ fg }: { fg: Fg }) {
  return (
    <Card>
      <div className="card-h">
        <h2>Field goals: made vs predicted</h2>
        <span className="muted">
          {comma(fg.n)} kicks · made {p1(fg.made)}, the bot said {p1(fg.pred)}
        </span>
      </div>
      <div className="tablewrap">
        <table className="tbl fgt">
          <thead>
            <tr>
              <th>Distance</th>
              <th className="num">Kicks</th>
              <th className="num">Made</th>
              <th className="num">Bot said</th>
              <th className="num hide-sm">
                <span className="legend fgkey">
                  <span>
                    <i style={{ background: 'var(--s1)' }} />
                    said
                  </span>
                  <span>
                    <i className="hollow" />
                    made
                  </span>
                  <span className="muted">0–100%</span>
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {fg.rows.map((r) => (
              <tr key={r.band}>
                <td>{r.band.replace('-', '–')} yards</td>
                <td className="num">{r.n}</td>
                <td className="num">
                  <b>{p0(r.made)}</b>
                </td>
                <td className="num">{p0(r.pred)}</td>
                <td className="num hide-sm">
                  <span className="fgdots" role="img" aria-label={`said ${p1(r.pred)}, made ${p1(r.made)}`}>
                    <span className="bg" />
                    {r.pred != null ? <span className="p" style={{ left: `${(r.pred * 100).toFixed(1)}%` }} /> : null}
                    {r.made != null ? <span className="a" style={{ left: `${(r.made * 100).toFixed(1)}%` }} /> : null}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Trend({ sv }: { sv: LiveSeasonReviewResponse }) {
  const t = sv.trend;
  const names = new Map(t.map((r) => [weekShort(sv.season, r.week), weekName(sv.season, r.week)]));
  return (
    <Card>
      <div className="card-h">
        <h2>Week by week</h2>
        <span className="muted">share of 4th downs</span>
      </div>
      <div className="chartpad">
        <LineChart
          label="Go rate by week: the bot and the coaches"
          xs={t.map((r) => weekShort(sv.season, r.week))}
          xFormat={(x) => names.get(String(x)) ?? String(x)}
          series={[
            { name: 'The bot says go', color: 'var(--s1)', values: t.map((r) => r.go_rate_bot) },
            { name: 'Coaches went for it', color: 'var(--s2)', values: t.map((r) => r.go_rate_coach) },
          ]}
          format={(v) => p0(v)}
        />
        {t.length <= 1 ? (
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            One week so far: the lines start with week 2.
          </p>
        ) : null}
      </div>
    </Card>
  );
}

export function ReviewSeason({ sv, week }: { sv: LiveSeasonReviewResponse; week: number }) {
  const L = sv.league;
  if (!L) return null;
  const small = smallSampleText(sv.weeks.length, sv.leaderboard);
  const c = sv.calibration;
  return (
    <>
      {sv.through_week != null && sv.through_week < week ? (
        <Notice>
          <b>{weekName(sv.season, week)}&apos;s plays aren&apos;t in yet:</b> this is the season through{' '}
          {throughText(sv.season, sv.through_week)}.
        </Notice>
      ) : null}
      {small ? (
        <Notice>
          <b>Early-season samples are small.</b> {small}
        </Notice>
      ) : null}
      <LeagueTiles L={L} sv={sv} />
      <Leaderboard sv={sv} L={L} />
      {c?.wp ? <Calibration wp={c.wp} season={sv.season} /> : null}
      {c?.conversion ? <Conversion cv={c.conversion} /> : null}
      <div className="rv-grid2">
        {c?.fg ? <FieldGoals fg={c.fg} /> : null}
        <Trend sv={sv} />
      </div>
    </>
  );
}
