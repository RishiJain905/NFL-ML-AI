// Graph lab -> Neighbours (AE03; mockup: graphlab.js neighboursView): "players most like X by who
// he played with". A player picker (?player=<GSIS id>), his two ranked lists (same position,
// anyone) and the map of every player. Hovering or focusing a row rings him on the map.

import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useNeighbours, useNeighboursMap } from '../../../api/client';
import type { NeighbourRow, PersonMatch } from '../../../api/types';
import { GROUP_WORD, UNITS, fixed, int, pct } from '../../../lib/graphlab';
import { LabAsOfLine, LabError, LabLoading } from './LabCommon';
import { NeighbourMap } from './NeighbourMap';
import { PersonPicker } from './PersonPicker';

const PLAYER = /^00-\d{7}$/;

export function NeighboursView() {
  const [params, setParams] = useSearchParams();
  const raw = params.get('player');
  const player = raw && PLAYER.test(raw) ? raw : null;
  const map = useNeighboursMap();
  const nb = useNeighbours(player);
  const [picked, setPicked] = useState<PersonMatch | null>(null); // the search's name, for "not in the map"
  const [hot, setHot] = useState<string | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);

  const pick = (id: string) => {
    setHot(null);
    setParams({ player: id });
  };

  if (map.error) return <LabError error={map.error} what="neighbours" />;
  if (!map.data) return <LabLoading what="the neighbours map" />;
  const asof = map.data.asof;
  const minSnaps =
    asof?.params && 'min_snaps' in asof.params ? String(asof.params.min_snaps) : '100';
  const dim = asof?.params && 'dimension' in asof.params ? String(asof.params.dimension) : '128';
  const ev = asof?.summary.explained_variance_2d ?? [];
  const kept = ev.reduce((s, x) => s + x, 0);
  const me = nb.data?.found ? nb.data.player : null;
  const pt = player ? map.data.points.find((p) => p.player_id === player) : undefined;
  const ring = nb.data?.found
    ? [...nb.data.position, ...nb.data.overall].map((r) => r.neighbour_id)
    : [];

  return (
    <>
      <div className="gl-head">
        <h2>Neighbours</h2>
        <LabAsOfLine asof={asof} job="neighbours" />
      </div>
      <p className="gl-intro">
        <b>Players most like him by who he played with, and how.</b> Each player with at least{' '}
        {minSnaps} snaps gets a {dim}
        -number fingerprint (a FastRP embedding) from his teammate links, weighted by snaps
        together, and his QB → receiver links; his neighbours are the players with the closest
        fingerprints (cosine similarity, 1 = the same).
      </p>
      <div className="card gl-cx" style={{ gap: 10 }}>
        <div className="gl-pair">
          <PersonPicker
            label="Player"
            kind="player"
            placeholder="Search a player"
            value={null}
            onChange={(p) => {
              if (!p) return;
              setPicked(p);
              pick(p.id.split(':')[1]);
            }}
          />
        </div>
      </div>
      <div aria-live="polite" className="gl-stack">
        {!player ? (
          <div className="card">
            <div className="empty">
              <div className="glyph" aria-hidden="true">
                ≈
              </div>
              <h3>Pick a player</h3>
              <p>Search one above, or click a dot on the map.</p>
            </div>
          </div>
        ) : nb.error ? (
          <LabError error={nb.error} what="neighbours" />
        ) : !nb.data ? (
          <LabLoading what="the neighbours" />
        ) : !nb.data.found ? (
          <div className="notice" role="status">
            <span className="ic" aria-hidden="true">
              i
            </span>
            <div>
              <b>
                {picked && picked.id === `player:${player}` ? picked.name : 'That player'} isn’t in
                the map.
              </b>{' '}
              The neighbours job keeps players with at least {minSnaps} snaps since 2018 who have
              teammate links; he has fewer, so there’s no fingerprint to compare. Connections still
              finds his chains.
            </div>
          </div>
        ) : me ? (
          <>
            <div className="gl-me">
              <b tabIndex={-1}>{me.name}</b>
              <span>
                {[me.position, me.team, `${int(me.snaps)} snaps since 2018`]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </div>
            <div className="gl-two">
              <RankCard
                title={`Same position (${me.position})`}
                rows={nb.data.position}
                onPick={pick}
                onHot={setHot}
                hot={hot}
              />
              <RankCard
                title="Anyone"
                rows={nb.data.overall}
                onPick={pick}
                onHot={setHot}
                hot={hot}
              />
            </div>
          </>
        ) : null}
      </div>
      <section className="card gl-mapcard" aria-labelledby="glMapH">
        <div className="card-h">
          <h2 id="glMapH">The map</h2>
          <span className="muted">{int(map.data.points.length)} players</span>
          <span className="right">
            {pt ? (
              <button
                type="button"
                className="btn sm"
                aria-pressed={zoom}
                onClick={() => setZoom((z) => !z)}
              >
                {zoom ? 'Show everyone' : `Zoom to ${pt.name}`}
              </button>
            ) : null}
          </span>
        </div>
        <div className="card-b">
          <div className="gl-groups" role="group" aria-label="Highlight a position group">
            {UNITS.map((u) => (
              <span key={u.unit} className="u">
                <span>
                  <i className={`gl-u-${u.unit}`} aria-hidden="true" />
                  {u.label}
                </span>
                {u.groups.map((g) => (
                  <button
                    key={g}
                    type="button"
                    aria-pressed={group === g}
                    onClick={() => setGroup((x) => (x === g ? null : g))}
                  >
                    {GROUP_WORD[g]}
                  </button>
                ))}
              </span>
            ))}
          </div>
          <NeighbourMap
            points={map.data.points}
            picked={pt ? player : null}
            ring={ring}
            hot={hot}
            group={group}
            zoom={zoom && !!pt}
            onPick={pick}
            label={`A map of ${map.data.points.length} players, coloured by offense, defense and special teams${
              pt ? `; ${pt.name} and his neighbours are ringed` : ''
            }. The lists above are the accessible answer.`}
          />
          <p className="gl-mapnote">
            <b>What the map shows:</b> each dot is a player, placed by squeezing his {dim}-number
            fingerprint into two (PCA). The two axes keep only {pct(kept)} of the variation (
            {ev.map((x) => pct(x, 1)).join(' + ')}), so near on the map doesn’t always mean similar:
            the lists are the real answer. Hover a dot for the player; click one to pick him.
          </p>
        </div>
      </section>
    </>
  );
}

function RankCard({
  title,
  rows,
  onPick,
  onHot,
  hot,
}: {
  title: string;
  rows: NeighbourRow[];
  onPick: (id: string) => void;
  onHot: (id: string | null) => void;
  hot: string | null;
}) {
  return (
    <section className="card" aria-label={title}>
      <div className="card-h">
        <h2>{title}</h2>
        <span className="muted">similarity</span>
      </div>
      {rows.length ? (
        <ol className="gl-rank" onPointerLeave={() => onHot(null)}>
          {rows.map((r) => (
            <li
              key={r.neighbour_id}
              className={hot === r.neighbour_id ? 'hot' : undefined}
              onPointerEnter={() => onHot(r.neighbour_id)}
              onFocus={() => onHot(r.neighbour_id)}
              onBlur={() => onHot(null)}
            >
              <span className="r">{r.rank}</span>
              <span className="nm">
                <button
                  type="button"
                  title={`Show ${r.neighbour_name}’s neighbours`}
                  onClick={() => onPick(r.neighbour_id)}
                >
                  {r.neighbour_name}
                </button>
                <small className={r.snaps_together ? '' : 'never'}>
                  {r.neighbour_position} · {r.neighbour_team ?? '—'} ·{' '}
                  {r.snaps_together
                    ? `${int(r.snaps_together)} snaps together`
                    : 'never on the field together'}
                </small>
              </span>
              <span className="gl-bar" role="img" aria-label={`similarity ${fixed(r.similarity)}`}>
                <b style={{ width: `${Math.max(0, Math.min(1, r.similarity)) * 100}%` }} />
              </span>
              <span className="v">{fixed(r.similarity)}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="card-b muted">No neighbours.</p>
      )}
    </section>
  );
}
