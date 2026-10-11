// The forecast's views (PC02; mockup: the "PC02: the forecast" block of playcalling.js), shared
// by the Play calls tab and a team page: the forecast table (one row per target: the forecast and
// its 80% range, the strip, the rate so far and the change, the result after the game), the
// made-after-kickoff note, the hint, the week's grade, the biggest expected moves, the team page's
// Next game card and its season card "How the forecast has done".

import { Link } from 'react-router-dom';
import type {
  PlayCallsResponse,
  PlaycallForecastMeta,
  PlaycallForecastRecord,
  PlaycallForecastValue,
  PlaycallGradeSummary,
  PlaycallTarget,
  PlaycallTeamResponse,
} from '../../api/types';
import { ForecastLegend, ForecastStrip } from '../../charts/ForecastStrip';
import { TipTarget } from '../../charts/TipTarget';
import { TeamChip } from '../../components/TeamChip';
import { Card, CardHeader, Notice } from '../../components/ui';
import { dayLabel, kickoffLabel } from '../../lib/format';
import { fmtRate } from '../../lib/playcall';
import {
  SCRIPT_TARGETS,
  STATUS,
  contextShort,
  fmtChange,
  fmtErr,
  forecastDomains,
  forecastTip,
  pctText,
  soFarName,
  splitHint,
  statusCounts,
  targetMap,
  weekValues,
  type StripDomain,
} from '../../lib/playcallForecast';
import './explore.css';

function Result({ t, v }: { t: PlaycallTarget; v: PlaycallForecastValue }) {
  if (v.status === 'graded') {
    return (
      <>
        <span className="num fact">{fmtRate(t, v.actual)}</span>
        <span className="fmarks">
          <span className={`fmk ${v.in_range ? 'ok' : 'miss'}`}>{v.in_range ? '✓ in range' : '✗ outside'}</span>
          <span className={`fmk ${v.beat_season_to_date ? 'ok' : 'miss'}`}>
            {v.beat_season_to_date ? `✓ beat ${soFarName(v)}` : `✗ ${soFarName(v)} closer`}
          </span>
        </span>
      </>
    );
  }
  if (!v.status) return null;
  const st = STATUS[v.status];
  return (
    <TipTarget className={`chip ${st.tone} fchip`} lines={[st.text, st.tip]}>
      {st.text}
    </TipTarget>
  );
}

function ForecastRow({ t, v, domain, pieces }: { t: PlaycallTarget; v: PlaycallForecastValue; domain: StripDomain | undefined; pieces?: boolean }) {
  const tip = forecastTip(t, v);
  return (
    <tr className={v.status === 'graded' ? 'graded' : undefined}>
      <th scope="row">
        <TipTarget as="div" className="fname" lines={tip} label={[t.short, ...tip].join(', ')}>
          {t.short}
          <small>{t.per === 'play' ? 'per play' : `per ${t.per}`}</small>
        </TipTarget>
      </th>
      <td className="r ffc">
        <b className="num">{fmtRate(t, v.forecast)}</b>
        <small className="num">
          {fmtRate(t, v.lo)} to {fmtRate(t, v.hi)}
        </small>
        {pieces ? (
          <small className="fpieces">
            {v.team} {fmtRate(t, v.team_term)} · {contextShort(t, v)} {fmtChange(t, v.opponent_effect)}
            {SCRIPT_TARGETS.includes(v.target) ? ` · script ${fmtChange(t, v.script_effect)}` : ''}
          </small>
        ) : null}
      </td>
      <td className="fviz">
        <ForecastStrip v={v} domain={domain} tip={tip} />
      </td>
      <td className={`r fsd${v.small ? ' smallv' : ''}`}>
        <span className="num">{fmtRate(t, v.season_to_date)}</span>
        <small className="num">{fmtChange(t, v.shift)}</small>
      </td>
      <td className="fres">
        <Result t={t} v={v} />
      </td>
    </tr>
  );
}

export interface ForecastGroup {
  title?: string;
  values: PlaycallForecastValue[];
}

/** The forecast table: groups of rows (a matchup's offense calls, then its defense calls). */
export function ForecastTable({
  groups,
  targets,
  domains,
  pieces,
  graded,
  lastSeason,
  label,
}: {
  groups: ForecastGroup[];
  targets: Map<string, PlaycallTarget>;
  domains: Record<string, StripDomain>;
  pieces?: boolean;
  graded?: boolean;
  lastSeason?: boolean;
  label?: string;
}) {
  return (
    <div className="tablewrap">
      <table className="tbl ftbl" aria-label={label}>
        <thead>
          <tr>
            <th />
            <th className="r">
              Forecast<small>80% range</small>
            </th>
            <th className="fviz" />
            <th className="r">
              {lastSeason ? 'Last season' : 'So far'}
              <small>change</small>
            </th>
            <th>{graded ? 'Actual' : 'After the game'}</th>
          </tr>
        </thead>
        {groups.map((g, i) => (
          <tbody key={g.title ?? i}>
            {g.title ? (
              <tr className="fgrp">
                <th colSpan={5} scope="colgroup">
                  {g.title}
                </th>
              </tr>
            ) : null}
            {g.values.map((v) => {
              const t = targets.get(v.target);
              return t ? <ForecastRow key={`${v.team}-${v.target}`} t={t} v={v} domain={domains[v.target]} pieces={pieces} /> : null;
            })}
          </tbody>
        ))}
      </table>
    </div>
  );
}

/** Made after kickoff: a backfill from the as-of tables (it still saw only earlier games). */
export function BackfillNotice({ meta }: { meta: PlaycallForecastMeta | null }) {
  if (!meta?.backfill) return null;
  const n = meta.after_kickoff.length;
  const games = n === 1 ? 'one game' : `${n} games`;
  return (
    <Notice icon="!">
      <b>Made after kickoff (a backfill).</b> This forecast was made {meta.made_at ? `on ${dayLabel(meta.made_at)}, ` : ''}after {games}{' '}
      had kicked off, from the tables as of week {meta.as_of_week ?? '—'}: its tendencies saw only the games before this week, but its lines may be the closing ones (a Tuesday forecast uses Tuesday's).{' '}
      {n ? (
        <details className="fdet">
          <summary>{n === 1 ? 'The game' : `The ${n} games`}</summary>
          {meta.after_kickoff.join(' · ')}
        </details>
      ) : null}
    </Notice>
  );
}

/** The next command to run (no forecast yet, or not graded yet), from the server's hint. */
export function HintNotice({ hint }: { hint: string | null }) {
  if (!hint) return null;
  const { text, cmd, tail } = splitHint(hint);
  return (
    <Notice>
      {text}
      {cmd ? <div className="cmd">{cmd}</div> : null}
      {tail ? <span className="muted">{tail}</span> : null}
    </Notice>
  );
}

/** The grade's numbers: closer in N of M, the 80% hit rate, the average miss in points. */
export function GradeStats({ s }: { s: PlaycallGradeSummary }) {
  const pts = (x: number | null) => (x == null ? '—' : (x * 100).toFixed(1));
  return (
    <div className="gstats">
      <div className="gst">
        <span className="eyebrow">Closer than the season so far</span>
        <b className="num">
          {s.beat}
          <small> of {s.graded}</small>
        </b>
        <span className="muted">{s.graded ? `${pctText(s.beat / s.graded)} of the forecasts` : '—'}</span>
      </div>
      <div className="gst">
        <span className="eyebrow">Inside the 80% range</span>
        <b className="num">{pctText(s.in_range)}</b>
        <span className="muted">a fair range lands near 80%</span>
      </div>
      <div className="gst">
        <span className="eyebrow">Average miss · points</span>
        <b className="num">
          {pts(s.mae)}
          <small> vs {pts(s.mae_season_to_date)}</small>
        </b>
        <span className="muted">forecast vs season so far · shares and PROE</span>
      </div>
    </div>
  );
}

type GradeRow = PlaycallGradeSummary & { target: string };

/** Per target: graded, the two misses (the smaller bold), a pair of bars, closer in N, in range. */
export function GradeTable({ rows, targets, label }: { rows: GradeRow[]; targets: Map<string, PlaycallTarget>; label: string }) {
  return (
    <div className="tablewrap">
      <table className="tbl gtbl" aria-label={label}>
        <thead>
          <tr>
            <th>Rate</th>
            <th className="r gn">Graded</th>
            <th className="r">Forecast miss</th>
            <th className="r">Season-so-far miss</th>
            <th className="gbars">
              <span className="legend">
                <span>
                  <i style={{ background: 'var(--s1)' }} />
                  forecast
                </span>
                <span>
                  <i style={{ background: 'var(--ink-3)' }} />
                  so far
                </span>
              </span>
            </th>
            <th className="r">Closer</th>
            <th className="r">In range</th>
          </tr>
        </thead>
        <tbody>
          {rows
            .filter((r) => r.graded > 0)
            .map((r) => {
              const t = targets.get(r.target);
              if (!t) return null;
              const a = fmtErr(t, r.mae);
              const b = fmtErr(t, r.mae_season_to_date);
              const both = r.mae != null && r.mae_season_to_date != null && a !== b; // bold neither on a tie
              const better = both && (r.mae as number) < (r.mae_season_to_date as number);
              const worse = both && !better;
              const top = Math.max(r.mae ?? 0, r.mae_season_to_date ?? 0) || 1;
              return (
                <tr key={r.target}>
                  <th scope="row">
                    {t.short}
                    <small>{t.caller === 'defense' ? "defense's call" : "offense's call"}</small>
                  </th>
                  <td className="r num gn">{r.graded}</td>
                  <td className={`r num${better ? ' win' : ''}`}>{a}</td>
                  <td className={`r num${worse ? ' win' : ''}`}>{b}</td>
                  <td className="gbars" aria-hidden="true">
                    <span className={`gb f ${t.caller === 'defense' ? 'd' : 'o'}`} style={{ width: `${(((r.mae ?? 0) / top) * 100).toFixed(0)}%` }} />
                    <span className="gb b" style={{ width: `${(((r.mae_season_to_date ?? 0) / top) * 100).toFixed(0)}%` }} />
                  </td>
                  <td className="r num">
                    {r.beat} of {r.graded}
                  </td>
                  <td className="r num">{pctText(r.in_range)}</td>
                </tr>
              );
            })}
        </tbody>
      </table>
    </div>
  );
}

/** The week's grade: a card once anything is graded, a waiting note before. */
export function WeekGrade({ r }: { r: PlayCallsResponse }) {
  const g = r.graded;
  if (!g) return null;
  const values = weekValues(r);
  if (!g.graded) {
    return (
      <Notice>
        <b>Waiting for the games:</b> none of the week's {g.rows} forecasts is graded yet ({statusCounts(values)}). The grade appears here after{' '}
        <span className="mono">nfl playcalling grade</span>.
      </Notice>
    );
  }
  const waiting = g.waiting ? ` · ${g.waiting} waiting (${statusCounts(values)})` : '';
  return (
    <Card>
      <CardHeader title={`How the forecast did · week ${r.week}`} sub={`${g.graded} graded${waiting} · the bar to beat: each team's own rate so far`} />
      <div className="card-b">
        <GradeStats s={g.overall} />
        <GradeTable rows={g.targets} targets={targetMap(r.forecast)} label={`Week ${r.week}'s grade by rate`} />
        <p className="gnote">
          A miss is |forecast − actual| in the rate's unit (points for shares and PROE, rushers for pass rushers). One week is a few dozen games:
          noisy, so read it with the season's record on each team page.
        </p>
      </div>
    </Card>
  );
}

/** The biggest expected moves: the forecast against the team's own rate so far. */
export function ExpectedMoves({ r }: { r: PlayCallsResponse }) {
  if (r.shifts_kind !== 'forecast' || !r.shifts.length) return null;
  const targets = targetMap(r.forecast);
  return (
    <Card>
      <CardHeader
        title="Biggest expected moves"
        sub="the forecast against the team's own rate so far · the bar: the move over the 80% range's half-width (1 = the range's edge)"
      />
      <ol className="shifts fmoves">
        {r.shifts.map((s) => {
          const t = targets.get(s.metric);
          return (
            <li key={`${s.game_id}-${s.offense}-${s.metric}`}>
              <span className="shm" aria-hidden="true">
                <span className="shb" style={{ width: `${Math.min(100, (s.shift / 1.5) * 100).toFixed(0)}%` }} />
              </span>
              <span>{s.text}</span>
              <span className="num muted shv">
                {t ? fmtChange(t, s.change) : ''}
                <small>{s.shift.toFixed(2)}× half-range</small>
              </span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

/** Each game's forecast: both matchups stacked, each the offense's calls then the defense's. */
export function ForecastGames({ r }: { r: PlayCallsResponse }) {
  const targets = targetMap(r.forecast);
  const domains = forecastDomains(weekValues(r));
  const graded = !!r.graded?.graded;
  const lastSeason = r.window === 'last_season';
  const page = (t: string) => ({ pathname: `/explore/play-calling/${t}`, search: `?season=${r.season}` });
  return (
    <div className="pcgames">
      {r.games.map((g) => (
        <Card key={g.game_id} className="pcgame">
          <CardHeader
            title={
              <>
                <TeamChip team={g.away} full /> <span className="at">at</span> <TeamChip team={g.home} full />
              </>
            }
            sub={g.kickoff ? kickoffLabel(g.kickoff) : undefined}
            right={
              <>
                <Link className="btn sm" to={page(g.away)}>
                  {g.away} page
                </Link>
                <Link className="btn sm" to={page(g.home)}>
                  {g.home} page
                </Link>
              </>
            }
          />
          <div className="pcmatch fc">
            {g.matchups.map((mu) => (
              <section key={mu.offense} aria-label={`${mu.offense} offense against ${mu.defense} defense`}>
                <h3 className="mh">
                  <TeamChip team={mu.offense} /> offense <span className="muted">vs</span> <TeamChip team={mu.defense} /> defense
                </h3>
                {mu.forecast.length ? (
                  <ForecastTable
                    groups={[
                      { title: `${mu.offense}'s calls`, values: mu.forecast.filter((v) => v.caller === 'offense') },
                      { title: `${mu.defense}'s calls`, values: mu.forecast.filter((v) => v.caller === 'defense') },
                    ]}
                    targets={targets}
                    domains={domains}
                    graded={graded}
                    lastSeason={lastSeason}
                    label={`${mu.offense} offense against ${mu.defense} defense: the forecast`}
                  />
                ) : (
                  <p className="muted">No forecast for this matchup.</p>
                )}
              </section>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}

// ---- a team page ----

/** Next game: this team's calls on this side, and the opponent's in the same matchup. */
export function NextGameCard({ r }: { r: PlaycallTeamResponse }) {
  const ng = r.next_game;
  if (!ng) return null;
  const header = (
    <CardHeader
      title={
        <>
          Next game <span className="muted ngwhere">{ng.home ? 'vs' : 'at'}</span> <TeamChip team={ng.opponent} full />
        </>
      }
      sub={`week ${ng.week}${ng.kickoff ? ` · ${kickoffLabel(ng.kickoff)}` : ''}`}
      right={
        <Link className="btn sm" to={`/week/${ng.season}/${ng.week}/play-calls`}>
          Week {ng.week} · Play calls →
        </Link>
      }
    />
  );
  const f = ng.forecast;
  if (!f) {
    const h = ng.forecast_hint ? splitHint(ng.forecast_hint) : null;
    return (
      <Card className="nextg">
        {header}
        <div className="card-b">
          <p className="muted ngnone">
            {h?.text ?? 'No forecast yet.'} The forecast for {r.team} {ng.home ? 'vs' : 'at'} {ng.opponent} appears here once it's made.
          </p>
          {h?.cmd ? <div className="cmd">{h.cmd}</div> : null}
        </div>
      </Card>
    );
  }
  const targets = targetMap(f);
  const all = [...f.own, ...f.opponent];
  const domains = forecastDomains(all);
  const graded = all.some((v) => v.status === 'graded');
  const lastSeason = all.some((v) => v.games_so_far === 0);
  const other = r.side === 'offense' ? 'defense' : 'offense';
  return (
    <Card className="nextg">
      {header}
      <div className="card-b">
        <div className="fnote">{f.note}</div>
        <div className="ngrid">
          <section>
            <h3 className="dh">
              {r.team}'s {r.side} calls
            </h3>
            <ForecastTable groups={[{ values: f.own }]} targets={targets} domains={domains} pieces graded={graded} lastSeason={lastSeason} label={`${r.team}'s ${r.side} calls: the forecast`} />
          </section>
          {f.opponent.length ? (
            <section>
              <h3 className="dh">
                {ng.opponent}'s {other} calls against it
              </h3>
              <ForecastTable
                groups={[{ values: f.opponent }]}
                targets={targets}
                domains={domains}
                pieces
                graded={graded}
                lastSeason={lastSeason}
                label={`${ng.opponent}'s ${other} calls: the forecast`}
              />
            </section>
          ) : null}
        </div>
        <div className="heatfoot">
          <ForecastLegend />
          {f.backfill ? (
            <TipTarget className="chip warn" lines={['Made after kickoff (a backfill from the as-of tables: only earlier games seen)', ...f.after_kickoff]}>
              <span className="ic">!</span>made after kickoff
            </TipTarget>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

/** "How the forecast has done for {TEAM}": this season's graded games of this side's calls. */
export function RecordCard({ r }: { r: PlaycallTeamResponse }) {
  const fr: PlaycallForecastRecord | null = r.forecast_record;
  if (!fr || !fr.targets.length) return null;
  const targets = new Map<string, PlaycallTarget>(fr.targets.map((t) => [t.target, t]));
  const w = fr.weeks;
  const weeks = w.length > 1 ? `weeks ${w[0]}–${w[w.length - 1]}` : w.length ? `week ${w[0]}` : '';
  return (
    <Card>
      <CardHeader title={`How the forecast has done for ${r.team}`} sub={`its ${r.side} calls${weeks ? ` · ${weeks}` : ''} · against its own rate so far`} />
      <div className="card-b">
        <GradeStats s={fr.overall} />
        <GradeTable rows={fr.targets} targets={targets} label={`How the forecast has done for ${r.team}, by rate`} />
        <p className="gnote">
          A few games per rate: one team's record is noisy.
          {fr.targets.some((t) => t.backtest) ? ' The backtest (every team, 2019–2025) is the fairer test of the model.' : ''}
        </p>
      </div>
    </Card>
  );
}
