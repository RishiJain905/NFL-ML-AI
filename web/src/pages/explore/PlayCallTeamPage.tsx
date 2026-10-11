// Explore → Play calling → a team (mockup: teamView). An Offense / Defense switch (remembered in
// this browser), the season (`?season=`), then: the five-second summary (the server's sentences),
// one window switch (Season / Last 4 / Last season) for Identity, By situation and Where the ball
// goes; Week by week; and History 2023-2025 (research data), fetched only when opened.

import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { usePlaycallHistory, usePlaycallTeam, useTeamInfo } from '../../api/client';
import type {
  PlaycallHistoryResponse,
  PlaycallSide,
  PlaycallTeamResponse,
  PlaycallWeeklySeries,
  PlaycallWindow,
} from '../../api/types';
import { DirectionStrip, FieldZones } from '../../charts/FieldZones';
import { DivLegend, HeatTable } from '../../charts/HeatTable';
import { RateBar, RateHead, RateRow } from '../../charts/RateBar';
import { RunLanes } from '../../charts/RunLanes';
import { Sparkline } from '../../charts/Sparkline';
import { TipTarget } from '../../charts/TipTarget';
import { Card, CardHeader, Chip, EmptyState, Notice, Segmented } from '../../components/ui';
import { WINDOW_LABEL, allowedNote, cellTip, fmtRate, perWord, rateDomain, readSide, writeSide } from '../../lib/playcall';
import { PageError } from '../season/common';
import { ExploreHeader, FtnWaiting, PlaycallEmpty, PlaycallLoading, SeasonSwitch } from './common';
import { asOfText, emptyTitle, seasonParam } from './text';

const WINDOWS: PlaycallWindow[] = ['season', 'last4', 'last_season'];

/** With 4 games or fewer the last four games are the season so far: one button, not two. */
const lastFourIsSeason = (r: PlaycallTeamResponse) => r.games > 0 && r.games <= 4;

function hasWindow(r: PlaycallTeamResponse, w: PlaycallWindow): boolean {
  return r.identity.some((g) => g.rows.some((x) => x.windows[w] != null));
}

function windowText(r: PlaycallTeamResponse, w: PlaycallWindow): string {
  if (w === 'season') return `Season${r.games ? ` · ${r.games} g` : ''}`;
  if (w === 'last4') return 'Last 4';
  return `Last season · ${r.season - 1}`;
}

function windowHelp(r: PlaycallTeamResponse, w: PlaycallWindow): string {
  if (w === 'season') {
    const base = r.as_of_week != null ? `Every game of ${r.season} before week ${r.as_of_week}.` : `Every game of ${r.season}.`;
    return lastFourIsSeason(r) ? `${base} Last 4 is the same until a fifth game.` : base;
  }
  if (w === 'last4') return 'The last four games (fewer early in the season).';
  return `Every game of ${r.season - 1}, playoffs in.`;
}

/** Who a rate belongs to in a tooltip: "KC" on offense, "vs KC" (what offenses do against it) on defense. */
const whoOf = (r: PlaycallTeamResponse) => (r.side === 'offense' ? r.team : `vs ${r.team}`);

function Summary({ r, nick }: { r: PlaycallTeamResponse; nick: string }) {
  return (
    <Card className="summary">
      <span className="eyebrow">
        {nick} {r.side} · in five seconds
      </span>
      {r.summary.length ? (
        <ul className="sumlist">
          {r.summary.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      ) : (
        <p className="sumnote">No clear tendencies yet: every rate is still on a small sample. The rows below show what there is.</p>
      )}
      {r.side === 'defense' ? (
        <p className="sumnote">
          A defense's page shows its own calls (blitz, rushers, box) and what offenses do against it; the second depends on the offenses it faced.
        </p>
      ) : null}
    </Card>
  );
}

function Identity({ r, win }: { r: PlaycallTeamResponse; win: PlaycallWindow }) {
  return (
    <Card>
      <CardHeader
        title="Identity"
        sub={`${r.side === 'offense' ? 'what it calls' : 'what it calls, and what offenses do against it'} · ${WINDOW_LABEL[win].toLowerCase()} · the bar is the team, the tick the league`}
      />
      <div className="card-b idgrid">
        {r.identity.map((g) => (
          <section key={g.id} className="idgrp" aria-label={g.title}>
            <h3>{g.title}</h3>
            {g.note ? <p className="gnote">{g.note}</p> : null}
            <div className="rrows">
              <RateHead />
              {g.rows.map((m) => (
                <RateRow
                  key={m.metric}
                  metric={m}
                  cell={m.windows[win]}
                  tip={cellTip(m, m.windows[win], whoOf(r), WINDOW_LABEL[win], r.min_n, allowedNote(m, r.side))}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </Card>
  );
}

function Situations({ r, win }: { r: PlaycallTeamResponse; win: PlaycallWindow }) {
  const s = r.situations;
  const [fam, setFam] = useState<string>(s?.families[0]?.family ?? 'down_distance');
  if (!s || !s.families.length) return null;
  const family = s.families.find((f) => f.family === fam) ?? s.families[0];
  return (
    <Card>
      <CardHeader
        title="By situation"
        sub={`${WINDOW_LABEL[win].toLowerCase()} · each cell against the league in the same situation`}
        right={
          <Segmented
            label="Situation"
            options={s.families.map((f) => ({ value: f.family, label: f.title }))}
            value={family.family}
            onChange={setFam}
          />
        }
      />
      <div className="card-b">
        <HeatTable
          label={`${r.team} ${r.side} by ${family.title.toLowerCase()}`}
          metrics={s.metrics}
          baseline={s.baseline}
          rows={family.rows}
          win={win}
          who={whoOf(r)}
          minN={r.min_n}
          side={r.side}
        />
        <div className="heatfoot">
          <DivLegend minN={r.min_n} />
          <span className="muted">Hover or focus a cell for the league's rate and the percentile.</span>
        </div>
      </div>
    </Card>
  );
}

function BallGoes({ r, win }: { r: PlaycallTeamResponse; win: PlaycallWindow }) {
  const f = r.field;
  if (!f && !r.runs.length) return null;
  const who = whoOf(r);
  return (
    <Card>
      <CardHeader
        title={r.side === 'offense' ? 'Where the ball goes' : 'Where offenses go against it'}
        sub={`${WINDOW_LABEL[win].toLowerCase()} · shares, next to the league's · zones and lanes tinted by the difference`}
        right={<DivLegend minN={r.min_n} />}
      />
      <div className="card-b fieldgrid">
        {f ? (
          <section aria-label="Passes: depth by direction">
            <h3 className="dh">Passes: depth × direction</h3>
            <FieldZones zones={f.zones} win={win} who={who} minN={r.min_n} side={r.side} />
            <DirectionStrip directions={f.directions} win={win} who={who} minN={r.min_n} side={r.side} />
            {f.depth.length ? (
              <div className="rrows" style={{ marginTop: 12 }}>
                <RateHead />
                {f.depth.map((m) => (
                  <RateRow key={m.metric} metric={m} cell={m.windows[win]} tip={cellTip(m, m.windows[win], who, WINDOW_LABEL[win], r.min_n, allowedNote(m, r.side))} />
                ))}
              </div>
            ) : null}
          </section>
        ) : null}
        {r.runs.length ? (
          <section aria-label="Designed runs: where they go">
            <h3 className="dh">Designed runs: where they go</h3>
            <RunLanes lanes={r.runs} win={win} who={who} minN={r.min_n} side={r.side} />
          </section>
        ) : null}
      </div>
    </Card>
  );
}

function WeekCard({ se, r }: { se: PlaycallWeeklySeries; r: PlaycallTeamResponse }) {
  const games = new Map((r.weekly?.games ?? []).map((g) => [g.week, g]));
  const pts = se.points.filter((p): p is { week: number; value: number; n: number } => p.value != null);
  const last = pts[pts.length - 1];
  return (
    <div className="wkcard">
      <div className="wkh">
        <b>{se.short}</b>
        <span className="num">
          {last ? fmtRate(se, last.value) : '—'}
          <small> wk {last ? last.week : '—'}</small>
        </span>
      </div>
      {pts.length ? (
        <Sparkline
          values={pts.map((p) => p.value)}
          color="var(--s1)"
          width={204}
          height={64}
          reference={se.league}
          label={`${se.label} by week: ${pts.map((p) => `week ${p.week} ${fmtRate(se, p.value)}`).join(', ')}`}
          points={pts.map((p) => {
            const g = games.get(p.week);
            const small = p.n < r.min_n;
            return {
              xLabel: String(p.week),
              hollow: small,
              tip: [
                `Week ${p.week}${g ? ` · ${g.home ? 'vs' : 'at'} ${g.opponent}` : ''}`,
                `${se.short}: ${fmtRate(se, p.value)} (${p.n} ${perWord(se.per, p.n)})`,
                `League (season): ${fmtRate(se, se.league)}`,
                ...(small ? [`Small sample: under ${r.min_n} ${perWord(se.per, 2)}`] : []),
              ],
            };
          })}
        />
      ) : (
        <p className="muted">No games yet.</p>
      )}
      <div className="wkf muted">
        <span>
          <i className="lk lg" aria-hidden="true" />
          league {fmtRate(se, se.league)}
        </span>
        <span>{se.label}</span>
      </div>
    </div>
  );
}

function Weekly({ r }: { r: PlaycallTeamResponse }) {
  if (!r.weekly || !r.weekly.series.length) return null;
  return (
    <Card>
      <CardHeader title="Week by week" sub={`one point per game · the dashed line is the league's season rate · hollow = fewer than ${r.min_n} plays`} />
      <div className="card-b wkgrid">
        {r.weekly.series.map((se) => (
          <WeekCard key={se.metric} se={se} r={r} />
        ))}
      </div>
    </Card>
  );
}

function HistoryBody({ h, side }: { h: PlaycallHistoryResponse; side: PlaycallSide }) {
  if (h.status !== 'ok') return <PlaycallEmpty meta={h} title="No history tables" glyph="H" />;
  const who = side === 'offense' ? h.team : `vs ${h.team}`;
  return (
    <>
      <Notice>{h.source_note}</Notice>
      <div className="hgrid">
        {h.groups.map((g) => (
          <section key={g.id} className="hgrp" aria-label={g.title}>
            <div className="tablewrap">
              <table className="tbl htbl">
                <thead>
                  <tr>
                    <th>{g.title}</th>
                    {h.seasons.map((s) => (
                      <th key={s}>{s}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {g.rows.map((m) => {
                    // one scale per row, so the bars keep the seasons' order (Sol review, PC01)
                    const domain = rateDomain(m, h.seasons.map((s) => m.seasons[String(s)]));
                    return (
                      <tr key={m.metric}>
                        <th scope="row">{m.short}</th>
                        {h.seasons.map((s) => {
                          const c = m.seasons[String(s)] ?? null;
                          return (
                            <td key={s} className={`hcell${c?.small ? ' small' : ''}`}>
                              <TipTarget as="div" className="hct" lines={cellTip(m, c, who, String(s), h.min_n, allowedNote(m, side))}>
                                <span className="hv num">
                                  {fmtRate(m, c?.value)}
                                  <small>{c ? `n ${c.n}` : ''}</small>
                                </span>
                                <RateBar metric={m} cell={c} compact domain={domain} />
                              </TipTarget>
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {g.note ? <p className="gnote">{g.note}</p> : null}
          </section>
        ))}
      </div>
      <div className="heatfoot">
        <span className="divlegend">
          <span className="lgkey">
            <i className="b" />
            team
          </span>
          <span className="lgkey">
            <i className="l" />
            league
          </span>
          <span className="muted">· whole seasons, playoffs in · greyed: n under {h.min_n}</span>
        </span>
      </div>
    </>
  );
}

function History({ team, side }: { team: string; side: PlaycallSide }) {
  const [open, setOpen] = useState(false);
  const q = usePlaycallHistory(team, side, open);
  const h = q.data;
  const what = side === 'offense' ? 'Personnel, formations, routes of targets' : 'Coverage shells, man vs zone, packages';
  const span = h && h.seasons.length ? `${h.seasons[0]}–${h.seasons[h.seasons.length - 1]}` : '2023–2025';
  return (
    <Card className="hist">
      <CardHeader
        title={`History, ${span}`}
        sub={
          <>
            <Chip tone="ghost">research data</Chip> {what}
          </>
        }
        right={
          <button type="button" className="btn sm" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? 'Hide' : 'Show history'}
          </button>
        }
      />
      <div className="card-b">
        {!open ? (
          <p className="muted histnote">
            Participation data (who's on the field, coverage, routes of targets) is published after each season; this shows the newest three
            seasons from 2023. Opens on request.
          </p>
        ) : q.isLoading ? (
          <div className="muted" role="status">
            Loading the history…
          </div>
        ) : q.error ? (
          <PageError error={q.error} what="the history" />
        ) : h ? (
          <HistoryBody h={h} side={side} />
        ) : null}
      </div>
    </Card>
  );
}

function TeamBody({ r, nick }: { r: PlaycallTeamResponse; nick: string }) {
  const wins = WINDOWS.filter((w) => hasWindow(r, w) && !(w === 'last4' && lastFourIsSeason(r)));
  const [picked, setPicked] = useState<PlaycallWindow>('season');
  const win = wins.includes(picked) ? picked : (wins[0] ?? 'season');
  return (
    <>
      <Summary r={r} nick={nick} />
      <div className="wintool">
        <span className="eyebrow">Window</span>
        <Segmented label="Window" options={wins.map((w) => ({ value: w, label: windowText(r, w) }))} value={win} onChange={setPicked} />
        <span className="muted">{windowHelp(r, win)}</span>
      </div>
      <Identity r={r} win={win} />
      <Situations r={r} win={win} />
      <BallGoes r={r} win={win} />
      <Weekly r={r} />
      <History team={r.team} side={r.side} />
      {r.notes.length ? (
        <div className="foot">
          {r.notes.map((n) => (
            <div key={n}>{n}</div>
          ))}
        </div>
      ) : null}
    </>
  );
}

export function PlayCallTeamPage() {
  const { team = '' } = useParams();
  const code = team.toUpperCase();
  const valid = /^[A-Z]{2,3}$/.test(code);
  const [params, setParams] = useSearchParams();
  const season = seasonParam(params.get('season'));
  const [side, setSideState] = useState<PlaycallSide>(readSide);
  const setSide = (s: PlaycallSide) => {
    writeSide(s);
    setSideState(s);
  };
  const q = usePlaycallTeam(code, side, season);
  const info = useTeamInfo().data?.teams[code];
  const r = q.data;
  const shown = r?.season ?? season;
  const back = { pathname: '/explore/play-calling', search: season ? `?season=${season}` : '' };
  const ng = r?.status === 'ok' ? r.next_game : null;
  return (
    <>
      <ExploreHeader
        eyebrow={
          <>
            <Link to={back}>Play calling</Link>
            {shown ? ` · ${shown}` : ''}
          </>
        }
        title={
          <>
            <i style={{ background: info?.color ?? '#888888' }} aria-hidden="true" />
            {info?.name ?? code}
          </>
        }
        chips={
          r && r.status === 'ok' ? (
            <>
              <Chip>{[`${r.games} game${r.games === 1 ? '' : 's'}`, asOfText(r)].filter(Boolean).join(' · ')}</Chip>
              <FtnWaiting games={r.ftn_waiting} />
              {ng ? (
                <Link className="chip run" to={`/week/${ng.season}/${ng.week}/play-calls`}>
                  Next: {ng.home ? 'vs' : 'at'} {ng.opponent} · week {ng.week} → Play calls
                </Link>
              ) : null}
            </>
          ) : null
        }
        right={
          <>
            <span className="seg big" role="group" aria-label="Side">
              {(['offense', 'defense'] as PlaycallSide[]).map((s) => (
                <button key={s} type="button" aria-pressed={side === s} onClick={() => setSide(s)}>
                  {s === 'offense' ? 'Offense' : 'Defense'}
                </button>
              ))}
            </span>
            {r && shown ? <SeasonSwitch seasons={r.seasons} value={shown} onChange={(s) => setParams({ season: String(s) })} /> : null}
          </>
        }
      />
      <section className="view" aria-label={`${code} play calling`}>
        {!valid ? (
          <EmptyState
            glyph="?"
            title="No such team"
            actions={
              <Link className="btn sm" to="/explore/play-calling">
                All teams
              </Link>
            }
          >
            Pick a team from the Play calling page.
          </EmptyState>
        ) : null}
        {valid && q.isLoading ? <PlaycallLoading what="the team page" /> : null}
        {q.error ? <PageError error={q.error} what={`${code}'s play calling`} /> : null}
        {r && r.status !== 'ok' ? <PlaycallEmpty meta={r} title={emptyTitle(r.status, r.season)} /> : null}
        {r && r.status === 'ok' ? <TeamBody key={`${r.team}-${r.season}`} r={r} nick={info?.nick ?? r.team} /> : null}
      </section>
    </>
  );
}
