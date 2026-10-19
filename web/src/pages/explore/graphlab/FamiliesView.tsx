// Graph lab -> Football families (AE03; mockup: graphlab.js familiesView): Leiden communities of
// players and coaches who shared the most snaps, named after their biggest team-era. A filter,
// "Find someone's family" (?person=), the list and the picked family's members (?family=).

import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PERSON_ID, useFamilies } from '../../../api/client';
import type { FamiliesResponse, Family, PersonMatch } from '../../../api/types';
import { int, plural, span } from '../../../lib/graphlab';
import { LabAsOfLine, LabError, LabLoading } from './LabCommon';
import { PersonPicker } from './PersonPicker';

export function FamiliesView() {
  const [params, setParams] = useSearchParams();
  const famRaw = params.get('family');
  const personRaw = params.get('person');
  const person = personRaw && PERSON_ID.test(personRaw) ? personRaw : null;
  const family = !person && famRaw && /^\d{1,7}$/.test(famRaw) ? Number(famRaw) : null;
  const fams = useFamilies(family, person);
  const [q, setQ] = useState('');
  const [who, setWho] = useState<PersonMatch | null>(null);

  if (fams.error) return <LabError error={fams.error} what="families" />;
  if (!fams.data) return <LabLoading what="the families" />;
  const d = fams.data;
  const asof = d.asof;
  const sm = asof?.summary ?? {};
  const minSize = asof?.params && 'min_size' in asof.params ? String(asof.params.min_size) : '5';
  const needle = q.trim().toLowerCase();
  const list = d.families.filter(
    (f) =>
      !needle ||
      [f.name, f.team, ...(f.top_players ?? []), ...(f.top_coaches ?? [])].some((s) =>
        String(s ?? '')
          .toLowerCase()
          .includes(needle),
      ),
  );
  const sel = d.family != null ? (d.families.find((f) => f.community === d.family) ?? null) : null;
  const loadingMembers = fams.isFetching && fams.isPlaceholderData;
  const personName = who && who.id === person ? who.name : 'That person';

  return (
    <>
      <div className="gl-head">
        <h2>Football families</h2>
        <LabAsOfLine asof={asof} job="families" />
      </div>
      <p className="gl-intro">
        <b>Groups of players and coaches who shared the most snaps</b>, found by an algorithm
        (Leiden communities on the teammate and coaching links), not drawn by hand. Each family is
        named after its biggest team-era and the side of the ball most of it played on (“core” when
        it’s both). {int(sm.families ?? d.families.length)} families of at least {minSize} people
        {sm.communities != null ? ` (of ${int(sm.communities)} groups found)` : ''}
        {sm.largest != null
          ? `; the biggest has ${int(sm.largest)}, the middle one ${int(sm.median_size)}`
          : ''}
        .
      </p>
      <div className="gl-tools">
        <div className="gl-filter">
          <label htmlFor="glFamQ">Filter families</label>
          <input
            id="glFamQ"
            type="search"
            placeholder="A name or a team: KC, Kelce"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-describedby="glFamN"
          />
        </div>
        <PersonPicker
          label="Find someone’s family"
          value={null}
          onChange={(p) => {
            if (!p) return;
            setWho(p);
            setParams({ person: p.id });
          }}
        />
      </div>
      {person && d.person_found === false ? (
        <div className="notice" role="status">
          <span className="ic" aria-hidden="true">
            i
          </span>
          <div>
            <b>{personName} isn’t in a family.</b> Families keep groups of at least {minSize}{' '}
            people; someone with few shared snaps can end up in a group too small to list.
          </div>
        </div>
      ) : null}
      <div className="gl-fam">
        <section className="card" aria-labelledby="glFamH">
          <div className="card-h">
            <h2 id="glFamH">Families</h2>
            <span className="muted" id="glFamN" aria-live="polite">
              {list.length === d.families.length
                ? `${list.length}, biggest first`
                : `${list.length} of ${d.families.length}`}
            </span>
          </div>
          {list.length ? (
            <ul className="gl-famlist">
              {list.map((f) => (
                <li key={f.community}>
                  <button
                    type="button"
                    aria-current={d.family === f.community}
                    onClick={() => setParams({ family: String(f.community) })}
                  >
                    <span className="nm">
                      {f.name} <span className="gl-unit">{f.unit}</span>
                    </span>
                    <span className="sz">
                      {f.size}
                      <small>people</small>
                    </span>
                    <span className="tp">
                      {[
                        `${plural(f.players, 'player')}, ${plural(f.coaches, 'coach', 'coaches')}`,
                        (f.top_players ?? []).slice(0, 3).join(', '),
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="none gl-famlist">No family matches “{q.trim()}”.</p>
          )}
        </section>
        <div>
          {sel ? (
            <FamilyCard
              key={sel.community}
              f={sel}
              d={d}
              person={person}
              loading={loadingMembers}
            />
          ) : (
            <div className="card">
              <div className="empty">
                <div className="glyph" aria-hidden="true">
                  fam
                </div>
                <h3>Pick a family</h3>
                <p>
                  Or find someone’s family by name: the family he belongs to opens with its members.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function FamilyCard({
  f,
  d,
  person,
  loading,
}: {
  f: Family;
  d: FamiliesResponse;
  person: string | null;
  loading: boolean;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, []);
  const members = d.members ?? [];
  const players = members.filter((m) => m.kind === 'player');
  const coaches = members.filter((m) => m.kind === 'coach');
  const row = (m: (typeof members)[number]) => {
    const me = `${m.kind}:${m.id}` === person;
    return (
      <tr key={`${m.kind}:${m.id}`} className={me ? 'me' : undefined}>
        <td className="who">
          {m.name}
          {me ? <span className="gl-sr"> (the person you searched)</span> : null}
        </td>
        <td>{m.kind === 'coach' ? 'coach' : m.position}</td>
        <td className="r num">
          {m.snaps == null ? <span className="muted">—</span> : int(m.snaps)}
        </td>
        <td className="r num muted">{m.snaps_total == null ? '—' : int(m.snaps_total)}</td>
      </tr>
    );
  };
  return (
    <section className="card" aria-labelledby="glFamSelH">
      <div className="card-h">
        <h2 id="glFamSelH" ref={heading} tabIndex={-1}>
          {f.name}
        </h2>
        <span className="gl-unit">{f.unit}</span>
      </div>
      <div
        className="card-b"
        style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingBottom: 12 }}
      >
        <div className="gl-meta">
          <span className="chip flat">
            <span className="k">People</span> {f.size}
          </span>
          <span className="chip flat">
            <span className="k">Players</span> {f.players}
          </span>
          <span className="chip flat">
            <span className="k">Coaches</span> {f.coaches}
          </span>
          <span className="chip flat">
            <span className="k">Seasons</span> {span(f.first_season, f.last_season)}
          </span>
          {f.team ? (
            <span className="chip flat">
              <span className="k">Team</span> {f.team}
            </span>
          ) : null}
        </div>
        {f.top_coaches?.length ? (
          <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>
            <b style={{ color: 'var(--ink)' }}>Coaches:</b> {f.top_coaches.join(', ')}
          </div>
        ) : null}
        <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>
          Members: the players by their snaps in this era (the family’s team and seasons; all their
          snaps since 2018 beside it), then the coaches.
        </div>
      </div>
      {loading || d.members == null ? (
        <div className="card-b">
          <span className="muted" role="status">
            Loading the members…
          </span>
        </div>
      ) : (
        <div className="gl-members" tabIndex={0} aria-label={`${f.name}: members`}>
          <table className="tbl">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Position</th>
                <th scope="col" className="r">
                  Snaps in this era
                </th>
                <th scope="col" className="r">
                  Since 2018
                </th>
              </tr>
            </thead>
            <tbody>
              <tr className="sep">
                <td colSpan={4}>Players · {players.length}</td>
              </tr>
              {players.map(row)}
              {coaches.length ? (
                <>
                  <tr className="sep">
                    <td colSpan={4}>Coaches · {coaches.length}</td>
                  </tr>
                  {coaches.map(row)}
                </>
              ) : null}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
