// "Is this team good at this?" (mockup: contextCard): the offense's rates this season, last
// season and the league's; the coach's go rate where the bot says go, by distance; the kicker by
// distance (this kick's band highlighted); the punter's net. As of the start of the week.

import type { LiveCallResponse, LiveContextResponse, LiveMeasure, LiveStat } from '../../../api/types';
import { TipTarget } from '../../../charts/TipTarget';
import { bandText, distanceBand, kickBand, kickerLong, p0 } from './model';

function StatCell({ s }: { s: LiveStat }) {
  if (!s.den)
    return (
      <span className="muted" title="No attempts">
        —
      </span>
    );
  return (
    <>
      <span className="r">{p0(s.rate)}</span>
      <small>
        {s.num} of {s.den}
      </small>
    </>
  );
}

function Versus({ m, team, ts, ls }: { m: LiveMeasure; team: string; ts: number; ls: number }) {
  const league = m.league_this.rate ?? m.league_last.rate;
  const x = (v: number) => `${Math.max(0, Math.min(100, v * 100)).toFixed(1)}%`;
  const line = (who: string, s: LiveStat) => `${who}: ${s.den ? `${p0(s.rate)} (${s.num} of ${s.den})` : '—'}`;
  return (
    <TipTarget
      className="vs"
      lines={[`${team} against the league`, line(`${team} ${ts}`, m.this), line(`${team} ${ls}`, m.last), `League ${ts}: ${p0(m.league_this.rate)}`]}
    >
      <span className="t" />
      {league != null ? <span className="lg" style={{ left: x(league) }} /> : null}
      {m.last.rate != null ? <span className="d2" style={{ left: x(m.last.rate) }} /> : null}
      {m.this.rate != null ? <span className="d1" style={{ left: x(m.this.rate) }} /> : null}
    </TipTarget>
  );
}

const MEASURES = [
  ['go_rate', 'Goes for it on 4th down', 'went for it, of its 4th downs'],
  ['fourth_conv', 'Converts when it goes', 'converted, of its 4th-down tries'],
  ['short_conv', 'Short yardage', '3rd or 4th & 2 or less, converted'],
  ['red_zone_td', 'Red-zone touchdowns', 'drives inside the 20 that end in a TD'],
] as const;

/** The distance and the kick this play is about: the 4th down itself, or the 3rd down's "no gain" row. */
function playSpot(call: LiveCallResponse): { togo: number | null; kick: number | null } {
  if (call.fourth) {
    const fg = call.fourth.options.find((o) => o.choice === 'fg');
    return { togo: call.game.distance, kick: fg?.wp != null ? Math.round(call.fourth.fg_distance) : null };
  }
  const row = call.third?.table.find((r) => r.gain === 0);
  return { togo: row?.ydstogo ?? call.game.distance, kick: row && row.wp.fg != null ? row.yardline_100 + 18 : null };
}

export function ContextCard({ ctx, call }: { ctx: LiveContextResponse; call: LiveCallResponse }) {
  const T = ctx.team;
  const ts = ctx.this_season;
  const ls = ctx.last_season;
  const { togo, kick } = playSpot(call);
  const cb = togo == null ? null : distanceBand(togo);
  const cg = T.coach_go;
  const moved = T.coach_last_team && T.coach_last_team !== T.team ? T.coach_last_team : null;
  const n1 = (v: number | null) => (v == null ? '—' : v.toFixed(1));
  return (
    <div className="card ctx">
      <div className="card-h">
        <h2>Is {T.team} good at this?</h2>
        <span className="muted">{ctx.through} · as of the start of the week</span>
        <div className="right">
          <span className="ctxlegend" aria-hidden="true">
            <span>
              <i className="d1" />
              {T.team} {ts}
            </span>
            <span>
              <i className="d2" />
              {ls}
            </span>
            <span>
              <i className="lg" />
              league {ts}
            </span>
          </span>
        </div>
      </div>
      <div className="ctxgrid">
        <section>
          <div className="ctxblock">
            <h3>
              The team<small>0–100% scale on the right</small>
            </h3>
            <table className="ctbl">
              <thead>
                <tr>
                  <th />
                  <th>{ts}</th>
                  <th>{ls}</th>
                  <th>League</th>
                  <th className="vscell" />
                </tr>
              </thead>
              <tbody>
                {MEASURES.map(([k, label, desc]) => {
                  const m = T[k];
                  return (
                    <tr key={k}>
                      <td>
                        {label}
                        <small>{desc}</small>
                      </td>
                      <td>
                        <StatCell s={m.this} />
                      </td>
                      <td>
                        <StatCell s={m.last} />
                      </td>
                      <td className="lg">
                        {p0(m.league_this.rate)}
                        <small>
                          {p0(m.league_last.rate)} in {ls}
                        </small>
                      </td>
                      <td className="vscell">
                        <Versus m={m} team={T.team} ts={ts} ls={ls} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="ctxblock">
            <h3>
              Coach: {T.coach ?? '—'}
              {moved ? <small>{moved} last season</small> : null}
            </h3>
            <p className="ctxline">
              {cg ? (
                <>
                  Where the bot says go, he went for it{' '}
                  <b>
                    {cg.this.num} of {cg.this.den}
                  </b>{' '}
                  times this season ({p0(cg.this.rate)}) and{' '}
                  <b>
                    {cg.last.num} of {cg.last.den}
                  </b>{' '}
                  in {ls}
                  {moved ? ` with ${moved}` : ''} ({p0(cg.last.rate)}). League in {ls}: {p0(cg.league_last.rate)}.
                </>
              ) : (
                'Not enough "go" spots yet.'
              )}
            </p>
            <table className="ctbl">
              <thead>
                <tr>
                  <th>Bot says go on</th>
                  <th>{ts}</th>
                  <th>{ls}</th>
                  <th>League {ls}</th>
                </tr>
              </thead>
              <tbody>
                {T.coach_go_by_distance.map((r) => (
                  <tr key={r.band} className={r.band === cb ? 'here' : ''}>
                    <td>
                      4th &amp; {bandText(r.band)}
                      {r.band === cb ? <span className="heretag">this play</span> : null}
                    </td>
                    <td>
                      <StatCell s={r.this} />
                    </td>
                    <td>
                      <StatCell s={r.last} />
                    </td>
                    <td className="lg">{p0(r.league_last.rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section>
          {T.kicker && kick != null ? (
            <div className="ctxblock">
              <h3>
                Kicker: {T.kicker.name ?? '—'}
                <small>{kickerLong(T.kicker.long, T.kicker.long_season)}</small>
              </h3>
              <table className="ctbl">
                <thead>
                  <tr>
                    <th>Field goals from</th>
                    <th>{ts}</th>
                    <th>{ls}</th>
                    <th>League {ls}</th>
                  </tr>
                </thead>
                <tbody>
                  {T.kicker.bands.map((r) => {
                    const here = r.band === kickBand(kick);
                    return (
                      <tr key={r.band} className={here ? 'here' : ''}>
                        <td>
                          {bandText(r.band)} yards
                          {here ? <span className="heretag">this kick: {kick}</span> : null}
                        </td>
                        <td>
                          <StatCell s={r.this} />
                        </td>
                        <td>
                          <StatCell s={r.last} />
                        </td>
                        <td className="lg">{p0(r.league_last.rate)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
          {T.punter ? (
            <div className="ctxblock">
              <h3>
                Punter: {T.punter.name ?? '—'}
                <small>net yards per punt</small>
              </h3>
              <table className="ctbl">
                <thead>
                  <tr>
                    <th />
                    <th>{ts}</th>
                    <th>{ls}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>{T.team}</td>
                    <td>
                      <span className="r">{n1(T.punter.this.net)}</span>
                      <small>{T.punter.this.punts} punts</small>
                    </td>
                    <td>
                      <span className="r">{n1(T.punter.last.net)}</span>
                      <small>{T.punter.last.punts} punts</small>
                    </td>
                  </tr>
                  <tr>
                    <td>League</td>
                    <td className="lg">{n1(T.punter.league_this.net)}</td>
                    <td className="lg">{n1(T.punter.league_last.net)}</td>
                  </tr>
                </tbody>
              </table>
              <p className="ctxline">Net = gross yards, minus returns, minus 20 for a touchback.</p>
            </div>
          ) : null}
          {!T.punter && !(T.kicker && kick != null) ? <p className="muted">No kick in range on this play.</p> : null}
        </section>
      </div>
    </div>
  );
}
