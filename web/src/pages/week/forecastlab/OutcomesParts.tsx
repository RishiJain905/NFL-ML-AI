// The Outcomes tab's other parts (FL02, D158; mockup: `statPicker`, `list`, `gaps` and
// `scoreboard` in documentation/forecast-lab/mockup/outcomes.js): the stat picker, the week's list
// for one stat, where the curve and the simple curve disagree most, and how the curves did.

import { useState } from 'react';
import type { CurveScoreRow, OutcomeRow, OutcomeTarget, OutcomesResponse } from '../../../api/types';
import { Segmented } from '../../../components/ui';
import { clearBest, markWords, pctText, projectionOf } from '../../../lib/forecastlab';

const GROUPS = ['QB', 'RB', 'WR/TE', 'LB/S'];
/** Rows the list shows before "Show all" (tackles has about 285). */
const LIMIT = 40;
const num = (x: number | null | undefined, digits = 0) =>
  x == null ? '—' : x.toLocaleString('en-US', { maximumFractionDigits: digits });
const projText = (r: OutcomeRow) => (r.kind === 'count' ? num(r.mean, 1) : num(r.p50));

export function StatPicker({ targets, value, onChange }: { targets: OutcomeTarget[]; value: string | null; onChange: (k: string) => void }) {
  const groups = [...GROUPS, ...targets.map((t) => t.group).filter((g) => !GROUPS.includes(g))].filter(
    (g, i, all) => all.indexOf(g) === i && targets.some((t) => t.group === g),
  );
  return (
    <div className="oc-stats" role="group" aria-label="Stat">
      {groups.map((g) => (
        <span className="grp" key={g}>
          <span className="gl">{g}</span>
          {targets
            .filter((t) => t.group === g)
            .map((t) => (
              <button
                key={t.stat}
                type="button"
                className="oc-stat"
                aria-pressed={t.stat === value}
                aria-label={`${g} ${t.label}`}
                onClick={() => onChange(t.stat)}
              >
                {t.label}
              </button>
            ))}
        </span>
      ))}
    </div>
  );
}

export function CurveList({
  r,
  t,
  selected,
  onPick,
}: {
  r: OutcomesResponse;
  t: OutcomeTarget;
  selected: string | null;
  onPick: (playerId: string) => void;
}) {
  const [q, setQ] = useState('');
  const [all, setAll] = useState(false);
  const [markIx, setMarkIx] = useState<Record<string, number>>({});
  const mi = Math.min(markIx[t.stat] ?? Math.floor(t.marks.length / 2), t.marks.length - 1);
  const mark = t.marks[mi];
  const needle = q.trim().toLowerCase();
  const found = r.rows.filter((x) => !needle || `${x.player ?? ''} ${x.team ?? ''} ${x.opponent ?? ''} ${x.position ?? ''}`.toLowerCase().includes(needle));
  // the first LIMIT rows until "Show all" (a search shows every match); the picked player always
  const cut = all || needle ? found : found.filter((x, i) => i < LIMIT || x.player_id === selected);
  const rows = cut;
  const span = r.rows.reduce((m, x) => Math.max(m, x.q90 ?? 0), 1);
  const at = (v: number | null) => `${(Math.max(0, Math.min(v ?? 0, span)) / span) * 100}%`;
  return (
    <section className="card oc-list" aria-label={`Every curve for ${t.group} ${t.label}`}>
      <div className="card-h">
        <h2>
          {t.group} {t.label}
        </h2>
        <span className="muted">{t.n} players · sorted by projection</span>
        <div className="right oc-tools">
          <input type="search" placeholder="Find a player or team" aria-label="Find a player or team" value={q} onChange={(e) => setQ(e.target.value)} />
          <label>
            Chance of{' '}
            <select value={mi} onChange={(e) => setMarkIx({ ...markIx, [t.stat]: Number(e.target.value) })} aria-label="Chance of">
              {t.marks.map((m, i) => (
                <option key={m} value={i}>
                  {markWords(t.kind, m)}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <div className="tablewrap">
        <table className="tbl oc-tbl">
          <thead>
            <tr>
              <th>Player</th>
              <th className="r">Projection</th>
              <th className="rg">80% range (the curve&apos;s)</th>
              <th className="r">Within ±{t.band}</th>
              <th className="r">
                {mark != null ? markWords(t.kind, mark) : '—'}
                <br />
                <small className="muted">curve · simple</small>
              </th>
              {r.graded ? <th className="r">Actual</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => {
              const m = x.marks.find((k) => k.mark === mark);
              const sel = x.player_id === selected;
              return (
                <tr key={`${x.player_id}-${x.game_id}`} className={sel ? 'sel' : undefined}>
                  <td className="who">
                    <button type="button" className="oc-pick" aria-pressed={sel} onClick={() => onPick(x.player_id)}>
                      {x.player ?? x.player_id}
                    </button>
                    <small>
                      {x.position} · {x.team} vs {x.opponent}
                    </small>
                  </td>
                  <td className="r num pv">{projText(x)}</td>
                  <td className="rg">
                    <span className="oc-range" aria-hidden="true">
                      <span className="ln" style={{ left: at(x.q10), width: `calc(${at(x.q90)} - ${at(x.q10)})` }} />
                      <span className="md" style={{ left: at(projectionOf(x)) }} />
                    </span>
                  </td>
                  <td className="r num">{pctText(x.p_band)}</td>
                  <td className="r num pv">
                    {pctText(m?.p_over)}
                    <small>{pctText(m?.bell_p_over)}</small>
                  </td>
                  {r.graded ? (
                    <td className="r num">{x.played === false ? <span className="muted">didn&apos;t play</span> : num(x.actual)}</td>
                  ) : null}
                </tr>
              );
            })}
            {rows.length === 0 ? (
              <tr>
                <td colSpan={r.graded ? 6 : 5} className="muted">
                  No player matches &ldquo;{q}&rdquo;.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {cut.length < found.length ? (
        <div className="card-b oc-more">
          <button type="button" className="btn sm" onClick={() => setAll(true)}>
            Show all {found.length}
          </button>
        </div>
      ) : null}
    </section>
  );
}

export function GapsCard({
  r,
  onShow,
}: {
  r: OutcomesResponse;
  onShow: (target: string, playerId: string) => void;
}) {
  if (!r.gaps.length) return null;
  const byKey = new Map(r.targets.map((t) => [t.stat, t]));
  return (
    <section className="card" aria-label="Where the curve and the simple curve disagree most">
      <div className="card-h">
        <h2>Where the curve and the simple curve disagree most</h2>
        <span className="muted">two per stat · the curve learned from past projections like these; the simple curve is the hand method</span>
      </div>
      <div className="card-b">
        <ul className="oc-gaps">
          {r.gaps.map((x) => {
            const t = byKey.get(x.stat);
            const g = x.gap;
            if (!t || !g || g.mark == null) return null;
            return (
              <li key={`${x.player_id}-${x.stat}`}>
                <span>
                  <b>{x.player}</b>{' '}
                  <span className="muted">
                    {x.team} · {t.group} {t.label}, projected {projText(x)}
                  </span>
                  : chance of {markWords(t.kind, g.mark)}
                  <button type="button" className="oc-show" onClick={() => onShow(x.stat, x.player_id)} aria-label={`Show ${x.player ?? ''}'s ${t.label}`}>
                    Show
                  </button>
                </span>
                <span className="two">
                  curve {pctText(g.p_over)} · simple {pctText(g.bell_p_over)}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

type Period = 'week' | 'season';
const CRPS: (keyof CurveScoreRow)[] = ['crps_model', 'crps_bell', 'crps_own'];
const BRIER: (keyof CurveScoreRow)[] = ['brier_model', 'brier_bell', 'brier_own'];


export function ScoreCard({ r }: { r: OutcomesResponse }) {
  const [picked, setPicked] = useState<Period | null>(null);
  const { week, season } = r.scoreboard;
  if (!week.length && !season.length) return null;
  const period: Period = picked ?? (week.length ? 'week' : 'season');
  const rows = period === 'week' ? week : season;
  const byKey = new Map(r.targets.map((t) => [t.stat, t]));
  const fx = (v: unknown, d: number) => (typeof v === 'number' ? v.toFixed(d) : '—');
  const cells = (x: CurveScoreRow, keys: (keyof CurveScoreRow)[], d: number) => {
    const best = clearBest(x, keys);
    return keys.map((k) => (
      <td key={k} className={`r num${k === best ? ' best' : ''}`}>
        {fx(x[k], d)}
      </td>
    ));
  };
  return (
    <section className="card oc-score" aria-label="How the curves did">
      <div className="card-h">
        <h2>How the curves did</h2>
        <span className="muted">lower is better · in bold when one is clearly the lowest</span>
        <div className="right">
          <Segmented<Period>
            label="Period"
            options={[
              { value: 'week', label: 'This week' },
              { value: 'season', label: 'Season to date' },
            ]}
            value={period}
            onChange={setPicked}
          />
        </div>
      </div>
      <div className="tablewrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Stat</th>
              <th className="r">Players</th>
              <th className="r" colSpan={3}>
                Whole-curve miss (CRPS)
                <br />
                <small className="muted">curve · simple · own games</small>
              </th>
              <th className="r" colSpan={3}>
                Threshold chances (Brier)
                <br />
                <small className="muted">curve · simple · own games</small>
              </th>
              <th className="r">Band: said · happened</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => {
              const t = byKey.get(x.stat);
              return (
                <tr key={x.stat}>
                  <td>{t ? `${t.group} ${t.label}` : x.stat}</td>
                  <td className="r num">{x.n}</td>
                  {cells(x, CRPS, 1)}
                  {cells(x, BRIER, 3)}
                  <td className="r num">
                    {pctText(x.band_pred)} · {pctText(x.band_hit)}
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="muted">
                  Nothing graded {period === 'week' ? 'this week yet' : 'yet this season'}.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <div className="card-b note">
        &quot;Own games&quot; is the player&apos;s own last 16 games as the curve. A difference of a few hundredths is noise: passing yards, for
        one, is practically the simple curve.
      </div>
    </section>
  );
}
