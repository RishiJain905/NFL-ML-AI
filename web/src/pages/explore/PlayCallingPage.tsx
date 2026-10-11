// Explore → Play calling: the teams grid (mockup: gridView). 32 tiles, each with the signature
// numbers (`columns[].signature`: offense PROE, defense blitz rate) on one scale per column, the
// league marker, the percentile and n; sortable by any column; a Tiles / Table switch (the table
// has every column, tinted by the difference from the league); a season switch (`?season=`).

import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams, type To } from 'react-router-dom';
import { usePlaycallTeams, useTeamInfo } from '../../api/client';
import type { PlaycallGridColumn, PlaycallGridTeam, PlaycallSide, PlaycallTeamsResponse } from '../../api/types';
import { DivLegend } from '../../charts/HeatTable';
import { RateBar } from '../../charts/RateBar';
import { TipTarget } from '../../charts/TipTarget';
import { TeamChip } from '../../components/TeamChip';
import { Card, CardHeader, Chip, Notice, Segmented } from '../../components/ui';
import { allowedNote, cellTip, divTint, fmtRate, ordinal, rateDomain, type RateDomain } from '../../lib/playcall';
import { PageError } from '../season/common';
import { ExploreHeader, FtnWaiting, PlaycallEmpty, PlaycallLoading, SeasonSwitch } from './common';
import { asOfText, emptyTitle, seasonParam } from './text';

const SIDE: Record<PlaycallSide, string> = { offense: 'Offense', defense: 'Defense' };
type Sort = { id: string; dir: 1 | -1 };

function sortTeams(r: PlaycallTeamsResponse, sort: Sort): PlaycallGridTeam[] {
  const col = r.columns.find((c) => c.id === sort.id);
  const val = (t: PlaycallGridTeam) => (col ? (t.cells[col.id]?.value ?? null) : null);
  return [...r.teams].sort((a, b) => {
    if (!col) return a.team.localeCompare(b.team) * sort.dir;
    const va = val(a);
    const vb = val(b);
    if (va == null && vb == null) return a.team.localeCompare(b.team);
    if (va == null) return 1;
    if (vb == null) return -1;
    return (va - vb) * sort.dir || a.team.localeCompare(b.team);
  });
}

function teamLink(team: string, season: number | undefined): To {
  return { pathname: `/explore/play-calling/${team}`, search: season ? `?season=${season}` : '' };
}

function Tile({
  t,
  sig,
  sortCol,
  r,
  domains,
  season,
}: {
  t: PlaycallGridTeam;
  sig: PlaycallGridColumn[];
  sortCol: PlaycallGridColumn | undefined;
  r: PlaycallTeamsResponse;
  domains: Record<string, RateDomain>;
  season: number | undefined;
}) {
  const info = useTeamInfo().data?.teams[t.team];
  const navigate = useNavigate();
  const extra = sortCol && !sortCol.signature ? t.cells[sortCol.id] : undefined;
  // the whole tile opens the team page on a click; keyboard users get the team's link (the
  // numbers are their own focusable marks with tooltips, so the tile isn't one big link)
  return (
    <div className="ptile" style={{ ['--tc' as string]: info?.color ?? '#888888' }} onClick={() => navigate(teamLink(t.team, season))}>
      <span className="pt-h">
        <Link to={teamLink(t.team, season)} onClick={(e) => e.stopPropagation()} aria-label={`${info?.name ?? t.team}: open its play-calling page`}>
          {t.team}
        </Link>
        <span className="nick">{info?.nick ?? ''}</span>
        <small>{t.games} g</small>
      </span>
      <span className="pt-sigs">
        {sig.map((col) => {
          const c = t.cells[col.id];
          return (
            <TipTarget key={col.id} className={`psig${c?.small ? ' small' : ''}`} lines={cellTip(col, c, t.team, SIDE[col.side], r.min_n, allowedNote(col, col.side))}>
              <span className="k">{SIDE[col.side]}</span>
              <span className="kk">{col.short}</span>
              <span className="v num">{fmtRate(col, c?.value)}</span>
              <RateBar metric={col} cell={c} compact domain={domains[col.id]} />
              <span className="lg num">
                lg {fmtRate(col, c?.league)}
                {c?.pct != null ? ` · ${ordinal(c.pct)}` : ''}
                {c ? ` · n ${c.n}` : ''}
              </span>
            </TipTarget>
          );
        })}
      </span>
      {sortCol && !sortCol.signature ? (
        <span className={`psort${extra?.small ? ' small' : ''}`}>
          <span>
            {SIDE[sortCol.side]} · {sortCol.short}
          </span>
          <b className="num">{fmtRate(sortCol, extra?.value)}</b>
          <span className="muted num">lg {fmtRate(sortCol, extra?.league)}</span>
        </span>
      ) : null}
    </div>
  );
}

function SortHeader({ id, label, title, sort, onSort }: { id: string; label: string; title?: string; sort: Sort; onSort: (id: string) => void }) {
  const on = sort.id === id;
  return (
    <button type="button" className="thsort" title={title} onClick={() => onSort(id)} aria-label={`Sort by ${title ?? label}`}>
      {label}
      {on ? (sort.dir > 0 ? ' ▲' : ' ▼') : ''}
    </button>
  );
}

function GridTable({ r, rows, sort, onSort, season }: { r: PlaycallTeamsResponse; rows: PlaycallGridTeam[]; sort: Sort; onSort: (id: string) => void; season: number | undefined }) {
  const navigate = useNavigate();
  const groups = (['offense', 'defense'] as PlaycallSide[]).map((sd) => [sd, r.columns.filter((c) => c.side === sd)] as const);
  const ariaSort = (id: string) => (sort.id === id ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none');
  return (
    <Card>
      <CardHeader title="Every column" sub="sort by any column · cells tinted by the difference from the league" right={<DivLegend minN={r.min_n} />} />
      <div className="tablewrap">
        <table className="tbl gridtbl">
          <thead>
            <tr>
              <th rowSpan={2} aria-sort={ariaSort('team')}>
                <SortHeader id="team" label="Team" sort={sort} onSort={onSort} />
              </th>
              {groups.map(([sd, cs]) => (
                <th key={sd} colSpan={cs.length} className="grph">
                  {SIDE[sd]}
                </th>
              ))}
            </tr>
            <tr>
              {groups.map(([, cs]) =>
                cs.map((c) => (
                  <th key={c.id} className="r" aria-sort={ariaSort(c.id)}>
                    <SortHeader id={c.id} label={c.short} title={`${SIDE[c.side]}: ${c.label}`} sort={sort} onSort={onSort} />
                  </th>
                )),
              )}
            </tr>
          </thead>
          <tbody>
            <tr className="lgrow">
              <td>League</td>
              {groups.map(([, cs]) =>
                cs.map((c) => {
                  const any = r.teams.map((t) => t.cells[c.id]).find((x) => x?.league != null);
                  return (
                    <td key={c.id} className="r num">
                      {fmtRate(c, any?.league)}
                    </td>
                  );
                }),
              )}
            </tr>
            {rows.map((t) => (
              <tr key={t.team} className="teamrow" onClick={() => navigate(teamLink(t.team, season))}>
                <td>
                  {/* keyboard users take the link; a click anywhere on the row opens the page too */}
                  <Link to={teamLink(t.team, season)} className="tlink" onClick={(e) => e.stopPropagation()} aria-label={`${t.team}: open its play-calling page`}>
                    <TeamChip team={t.team} full />
                  </Link>
                </td>
                {groups.map(([sd, cs]) =>
                  cs.map((c) => {
                    const x = t.cells[c.id];
                    return (
                      <td key={c.id} className={`r num hc${x?.small ? ' small' : ''}`} style={divTint(c, x)}>
                        {/* a focusable tooltip per cell (Sol review: no native title on unfocusable cells) */}
                        <TipTarget as="div" className="hct" lines={cellTip(c, x, t.team, SIDE[sd], r.min_n, allowedNote(c, sd))}>
                          {fmtRate(c, x?.value)}
                          <small>n {x?.n ?? 0}</small>
                        </TipTarget>
                      </td>
                    );
                  }),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Grid({ r, season }: { r: PlaycallTeamsResponse; season: number | undefined }) {
  const [sort, setSort] = useState<Sort>({ id: 'team', dir: 1 });
  const [view, setView] = useState<'tiles' | 'table'>('tiles');
  const sig = r.columns.filter((c) => c.signature);
  const sortCol = r.columns.find((c) => c.id === sort.id);
  const rows = useMemo(() => sortTeams(r, sort), [r, sort]);
  const domains = useMemo(
    () => Object.fromEntries(sig.map((c) => [c.id, rateDomain(c, r.teams.map((t) => t.cells[c.id]))])),
    [r, sig],
  );
  const onSort = (id: string) =>
    setSort((s) => (s.id === id ? { id, dir: s.dir > 0 ? -1 : 1 } : { id, dir: id === 'team' ? 1 : -1 }));
  return (
    <>
      {r.window === 'last_season' ? (
        <Notice>
          <b>Before week 1's games:</b> the numbers are last season's.
        </Notice>
      ) : null}
      <div className="sec-h">
        <h2>32 teams</h2>
        <p>
          {r.window === 'season' ? `${r.season} season so far` : `${r.season - 1} season`} · pick a team for its page · grey = fewer than {r.min_n} plays
        </p>
        <div className="right">
          <label className="sortsel">
            <span className="eyebrow">Sort by</span>
            <select value={sort.id} onChange={(e) => setSort({ id: e.target.value, dir: e.target.value === 'team' ? 1 : -1 })}>
              <option value="team">Team</option>
              {(['offense', 'defense'] as PlaycallSide[]).map((sd) => (
                <optgroup key={sd} label={SIDE[sd]}>
                  {r.columns
                    .filter((c) => c.side === sd)
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.short}
                      </option>
                    ))}
                </optgroup>
              ))}
            </select>
          </label>
          <button type="button" className="btn sm" onClick={() => setSort((s) => ({ ...s, dir: s.dir > 0 ? -1 : 1 }))}>
            {sort.dir > 0 ? (sortCol ? 'Lowest first' : 'A → Z') : sortCol ? 'Highest first' : 'Z → A'}
          </button>
          <Segmented
            label="View"
            options={[
              { value: 'tiles', label: 'Tiles' },
              { value: 'table', label: 'Table' },
            ]}
            value={view}
            onChange={setView}
          />
        </div>
      </div>
      {view === 'tiles' ? (
        <div className="ptiles">
          {rows.map((t) => (
            <Tile key={t.team} t={t} sig={sig} sortCol={sortCol} r={r} domains={domains} season={season} />
          ))}
        </div>
      ) : (
        <GridTable r={r} rows={rows} sort={sort} onSort={onSort} season={season} />
      )}
      <div className="foot">
        Pass rate over expected (PROE) is in points; the league sits below 0 (nflverse's expected pass rate isn't re-centred), so compare with the league
        marker, not with 0. A percentile is "more of it", not "better".
      </div>
    </>
  );
}

export function PlayCallingPage() {
  const [params, setParams] = useSearchParams();
  const season = seasonParam(params.get('season'));
  const q = usePlaycallTeams(season);
  const r = q.data;
  const shown = r?.season ?? season;
  return (
    <>
      <ExploreHeader
        eyebrow="Explore"
        title="Play calling"
        chips={
          r && r.status === 'ok' ? (
            <>
              <Chip>{[r.season, asOfText(r)].filter(Boolean).join(' · ')}</Chip>
              <FtnWaiting games={r.ftn_waiting} />
            </>
          ) : null
        }
        right={r && shown ? <SeasonSwitch seasons={r.seasons} value={shown} onChange={(s) => setParams({ season: String(s) })} /> : null}
      />
      <section className="view" aria-label="Play calling">
        {q.isLoading ? <PlaycallLoading what="the teams" /> : null}
        {q.error ? <PageError error={q.error} what="the play-calling teams" /> : null}
        {r && r.status !== 'ok' ? <PlaycallEmpty meta={r} title={emptyTitle(r.status, r.season)} /> : null}
        {r && r.status === 'ok' ? <Grid r={r} season={season} /> : null}
      </section>
    </>
  );
}
