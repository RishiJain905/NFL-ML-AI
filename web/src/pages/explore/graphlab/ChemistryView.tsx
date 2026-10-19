// Graph lab -> Chemistry (AE03; mockup: graphlab.js chemistryView): QB -> receiver duos by EPA
// per target with a minimum number of targets (?min=100|200|300|500), every header sorts; and the
// PageRank lists of the most central receivers and QBs.

import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CHEMISTRY_MINS, useChemistry } from '../../../api/client';
import type { ChemistryDuo, PageRankRow } from '../../../api/types';
import { fixed, int, pct, plural, span } from '../../../lib/graphlab';
import { LabAsOfLine, LabError, LabLoading } from './LabCommon';

type Col = keyof ChemistryDuo;
const COLS: { key: Col; label: string; num: boolean }[] = [
  { key: 'qb', label: 'QB', num: false },
  { key: 'receiver', label: 'Receiver', num: false },
  { key: 'position', label: 'Pos', num: false },
  { key: 'targets', label: 'Targets', num: true },
  { key: 'catches', label: 'Catches', num: true },
  { key: 'yards', label: 'Yards', num: true },
  { key: 'touchdowns', label: 'TD', num: true },
  { key: 'epa_per_target', label: 'EPA / target', num: true },
  { key: 'catch_rate', label: 'Catch rate', num: true },
  { key: 'first_season', label: 'Seasons', num: true },
  { key: 'teams', label: 'Teams', num: false },
];

function sortValue(d: ChemistryDuo, k: Col): number | string {
  const v = d[k];
  if (Array.isArray(v)) return v.join(', ');
  return v;
}

export function ChemistryView() {
  const [params, setParams] = useSearchParams();
  const raw = Number(params.get('min'));
  const min = (CHEMISTRY_MINS as readonly number[]).includes(raw) ? raw : 100;
  const chem = useChemistry(min);
  const [sort, setSort] = useState<{ col: Col; dir: 1 | -1 }>({ col: 'epa_per_target', dir: -1 });

  if (chem.error) return <LabError error={chem.error} what="chemistry" />;
  if (!chem.data) return <LabLoading what="the chemistry" />;
  const c = chem.data;
  const duos = [...c.duos].sort((x, y) => {
    const a = sortValue(x, sort.col);
    const b = sortValue(y, sort.col);
    const v =
      typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b));
    return v * sort.dir;
  });
  const onSort = (k: Col, num: boolean) =>
    setSort((s) =>
      s.col === k ? { col: k, dir: s.dir === 1 ? -1 : 1 } : { col: k, dir: num ? -1 : 1 },
    );

  return (
    <>
      <div className="gl-head">
        <h2>Chemistry</h2>
        <LabAsOfLine asof={c.asof} job="chemistry" />
      </div>
      <p className="gl-intro">
        <b>Quarterback → receiver duos, ranked by EPA per target</b> (expected points added each
        time that QB threw to that receiver, 2018 on, playoffs included), with a minimum number of
        targets so a hot month doesn’t top the list. Beside them,{' '}
        <b>the most central receivers and QBs</b>: PageRank on the QB → receiver target graph, where
        a receiver scores high when many QBs, and central ones, threw to him a lot.
      </p>
      <section className="card" aria-labelledby="glDuoH">
        <div className="card-h">
          <h2 id="glDuoH">The duos</h2>
          <span className="muted" aria-live="polite">
            {c.duos.length >= 50 ? `the top ${c.duos.length}` : plural(c.duos.length, 'duo')} with{' '}
            {int(c.min_targets)}+ targets
            {chem.isFetching ? ' · loading…' : ''}
          </span>
          <span className="right">
            <span className="eyebrow">Min. targets</span>
            <span className="seg" role="group" aria-label="Minimum targets">
              {CHEMISTRY_MINS.map((n) => (
                <button
                  key={n}
                  type="button"
                  aria-pressed={min === n}
                  onClick={() => setParams(n === 100 ? {} : { min: String(n) })}
                >
                  {n}
                </button>
              ))}
            </span>
          </span>
        </div>
        {duos.length ? (
          <div className="gl-tablewrap" tabIndex={0} aria-label="The duos">
            <table className="tbl gl-tbl">
              <thead>
                <tr>
                  {COLS.map(({ key, label, num }) => {
                    const s =
                      sort.col === key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none';
                    return (
                      <th key={key} scope="col" className={num ? 'r' : undefined} aria-sort={s}>
                        <button type="button" onClick={() => onSort(key, num)}>
                          {label}
                          <span className="arr" aria-hidden="true">
                            {s === 'ascending' ? '▲' : s === 'descending' ? '▼' : ''}
                          </span>
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {duos.map((d) => (
                  <tr key={`${d.qb_id}-${d.receiver_id}`}>
                    <td className="who">{d.qb}</td>
                    <td className="who">{d.receiver}</td>
                    <td>{d.position}</td>
                    <td className="r num">{int(d.targets)}</td>
                    <td className="r num">{int(d.catches)}</td>
                    <td className="r num">{int(d.yards)}</td>
                    <td className="r num">{int(d.touchdowns)}</td>
                    <td className="r num">
                      <b>{fixed(d.epa_per_target, 3)}</b>
                    </td>
                    <td className="r num">{pct(d.catch_rate, 1)}</td>
                    <td className="r num">{span(d.first_season, d.last_season)}</td>
                    <td>{d.teams.join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="card-b muted">No duo has {int(c.min_targets)} targets.</p>
        )}
      </section>
      <div className="gl-two">
        <PageRankCard title="Most central receivers" rows={c.receivers} role="receiver" />
        <PageRankCard title="Most central quarterbacks" rows={c.qbs} role="qb" />
      </div>
    </>
  );
}

function PageRankCard({
  title,
  rows,
  role,
}: {
  title: string;
  rows: PageRankRow[];
  role: 'receiver' | 'qb';
}) {
  const max = Math.max(1e-9, ...rows.map((r) => r.score));
  return (
    <section className="card" aria-label={title}>
      <div className="card-h">
        <h2>{title}</h2>
        <span className="muted">PageRank</span>
      </div>
      <ol className="gl-pr">
        {rows.map((r) => (
          <li key={r.player_id}>
            <span className="r">{r.rank}</span>
            <span className="nm">
              <b>{r.name}</b>
              <small>
                {r.position} ·{' '}
                {role === 'receiver'
                  ? `${plural(r.qbs, 'QB')} threw to him`
                  : `threw to ${plural(r.receivers, 'receiver')}`}
              </small>
            </span>
            <span className="gl-bar" role="img" aria-label={`score ${fixed(r.score)}`}>
              <b style={{ width: `${(r.score / max) * 100}%` }} />
            </span>
            <span className="v">{fixed(r.score)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
