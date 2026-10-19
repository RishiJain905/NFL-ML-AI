// Explore -> Ask the Engine -> Connections ("six degrees", AE03; mockup:
// documentation/ask-the-engine/mockup/graphlab.js, "Connections"). Two people from the name
// search, Fewest / Strongest links, Connect: the chain drawn as people joined by links (an SVG
// line per link, coloured and dashed by type) with each link's evidence; "Show 3 more chains"
// asks again with k=4. The URL holds the asked pair (?a=&b=&mode=), so a link can be shared:
// opening one connects at once.

import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ApiError, PERSON_ID, useConnections, useGraphLabStatus } from '../../../api/client';
import type {
  ConnectionLink,
  ConnectionMode,
  ConnectionPath,
  ConnectionsResponse,
  PersonMatch,
} from '../../../api/types';
import { TipTarget } from '../../../charts/TipTarget';
import {
  LINK_HELP,
  LINK_TYPES,
  MODE_WHY,
  asMatch,
  capFirst,
  int,
  linkWord,
  ms,
  personSub,
  plural,
  splitCommand,
  when,
} from '../../../lib/graphlab';
import { AskHeader, HistoryStatus } from './AskHeader';
import { PersonPicker } from './PersonPicker';

const MODES: ConnectionMode[] = ['fewest', 'strongest'];
const isMode = (m: string | null): m is ConnectionMode => m === 'fewest' || m === 'strongest';

/** A person the URL names before the answer arrives (the name comes with it). */
function stub(id: string): PersonMatch {
  const [kind] = id.split(':');
  return {
    id,
    kind: kind === 'coach' ? 'coach' : 'player',
    name: '',
    position: null,
    team: null,
    seasons: null,
    links: null,
  };
}
const idOf = (p: ConnectionsResponse['a']) => `${p.kind}:${p.id}`;

/** The picker's person after the URL's pair changed: the same person if the URL still names him
 *  (his name stays), else the one the URL names (a stub), else empty. */
function followUrl(p: PersonMatch | null, id: string | null): PersonMatch | null {
  if (p && p.id === id) return p;
  return id && PERSON_ID.test(id) ? stub(id) : null;
}

export function ConnectionsPage() {
  const status = useGraphLabStatus(); // also warms the history graph up on the server
  const [params, setParams] = useSearchParams();
  const urlA = params.get('a');
  const urlB = params.get('b');
  const urlMode = params.get('mode');
  const asked = !!urlA && !!urlB && PERSON_ID.test(urlA) && PERSON_ID.test(urlB) && urlA !== urlB;
  const askedMode: ConnectionMode = isMode(urlMode) ? urlMode : 'fewest';

  const [a, setA] = useState<PersonMatch | null>(() =>
    urlA && PERSON_ID.test(urlA) ? stub(urlA) : null,
  );
  const [b, setB] = useState<PersonMatch | null>(() =>
    urlB && PERSON_ID.test(urlB) ? stub(urlB) : null,
  );
  const [mode, setMode] = useState<ConnectionMode>(askedMode);
  // The URL is the committed pair: when it changes without a click here (the browser's Back and
  // Forward buttons), the pickers and the mode follow it, so they never disagree with the chain
  // shown. A pick that isn't asked yet is left alone while the URL stays the same.
  const urlKey = `${urlA ?? ''}|${urlB ?? ''}|${askedMode}`;
  const [seenKey, setSeenKey] = useState(urlKey);
  if (seenKey !== urlKey) {
    setSeenKey(urlKey);
    setA(followUrl(a, urlA));
    setB(followUrl(b, urlB));
    setMode(askedMode);
  }

  const conn = useConnections(urlA, urlB, askedMode, 1, asked);
  // a shared link: the pickers show the names once the answer has them
  const shownA = a && !a.name && conn.data && idOf(conn.data.a) === a.id ? asMatch(conn.data.a) : a;
  const shownB = b && !b.name && conn.data && idOf(conn.data.b) === b.id ? asMatch(conn.data.b) : b;

  const ask = (pa: PersonMatch | null, pb: PersonMatch | null, m: ConnectionMode) => {
    if (!pa || !pb || pa.id === pb.id) return;
    if (asked && pa.id === urlA && pb.id === urlB && m === askedMode) {
      void conn.refetch();
      return;
    }
    setParams({ a: pa.id, b: pb.id, mode: m });
  };
  const ready = !!shownA && !!shownB && shownA.id !== shownB.id;
  const running = asked && conn.isFetching && !conn.data;

  return (
    <>
      <AskHeader status={<HistoryStatus st={status.data} />} />
      <section className="view">
        <div className="gl-stack">
          <p className="gl-intro">
            The shortest chain between any two players or coaches in the history graph (every play
            since 2018), person to person: <b>teammates</b> who were on the field together, a coach
            who <b>coached</b> a player, two coaches on the <b>same staff</b>. Never through a team,
            or everyone would be two steps apart.
          </p>
          <form
            className="card gl-cx"
            autoComplete="off"
            onSubmit={(e) => {
              e.preventDefault();
              ask(shownA, shownB, mode);
            }}
          >
            <h2>Connect two people</h2>
            <div className="gl-pair">
              <PersonPicker
                label="From"
                inputId="glPickA"
                value={shownA}
                onChange={setA}
                onPicked={() => document.getElementById(shownB ? 'glConnect' : 'glPickB')?.focus()}
              />
              <button
                type="button"
                className="btn gl-swap"
                aria-label="Swap the two people"
                title="Swap"
                disabled={!shownA && !shownB}
                onClick={() => {
                  setA(shownB);
                  setB(shownA);
                  if (asked) ask(shownB, shownA, mode);
                }}
              >
                ⇄
              </button>
              <PersonPicker
                label="To"
                inputId="glPickB"
                value={shownB}
                onChange={setB}
                onPicked={() => document.getElementById(shownA ? 'glConnect' : 'glPickA')?.focus()}
              />
            </div>
            <div className="gl-modes">
              <span className="seg" role="group" aria-label="Which chain">
                {MODES.map((m) => (
                  <button
                    key={m}
                    type="button"
                    aria-pressed={mode === m}
                    onClick={() => {
                      setMode(m);
                      if (asked && ready) ask(shownA, shownB, m); // a shown chain follows the switch
                    }}
                  >
                    {m === 'fewest' ? 'Fewest links' : 'Strongest links'}
                  </button>
                ))}
              </span>
              <span className="why" id="cxWhy">
                <b>{mode === 'fewest' ? 'Fewest links:' : 'Strongest links:'}</b> {MODE_WHY[mode]}
              </span>
              <button
                id="glConnect"
                type="submit"
                className="btn primary gl-go"
                disabled={!ready || running}
                aria-describedby="cxWhy"
              >
                {running ? 'Connecting…' : 'Connect'}
              </button>
            </div>
            {shownA && shownB && shownA.id === shownB.id ? (
              <span className="gl-hint" role="status">
                Pick two different people.
              </span>
            ) : null}
          </form>
          <div aria-live="polite">
            {!asked ? (
              <div className="card">
                <div className="empty">
                  <div className="glyph" aria-hidden="true">
                    ⇄
                  </div>
                  <h3>Pick two people</h3>
                  <p>
                    Search a player or a coach in each box, then <b>Connect</b>. Try two former
                    teammates, or a quarterback and a coach he never played for.
                  </p>
                </div>
              </div>
            ) : conn.error ? (
              <ConnectionError error={conn.error} onRetry={() => void conn.refetch()} />
            ) : conn.data ? (
              <ConnectionResult key={`${urlA}|${urlB}|${askedMode}`} res={conn.data} />
            ) : (
              <Running
                mode={askedMode}
                a={shownA?.name || 'the first person'}
                b={shownB?.name || 'the second'}
              />
            )}
          </div>
        </div>
      </section>
    </>
  );
}

function Running({ mode, a, b }: { mode: ConnectionMode; a: string; b: string }) {
  const [start] = useState(() => Date.now());
  const [now, setNow] = useState(start);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, []);
  const s = Math.max(0, Math.floor((now - start) / 1000));
  return (
    <div className="card gl-running" aria-busy="true" role="status" aria-label="Connecting">
      <div>
        <span className="eyebrow">Connecting</span>
        <div className="q">
          {a} and {b} · {mode === 'fewest' ? 'fewest links' : 'strongest links'}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <span className="ask-dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span
          style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 24 }}
          aria-label={`${s} seconds so far`}
        >
          {s} s
        </span>
      </div>
      <div className="note">
        {mode === 'strongest'
          ? 'A strongest search needs the person graph in memory: a cold graph can take up to half a minute the first time after it starts (the app warms it up in the background); after that a search takes milliseconds.'
          : 'A cold graph can take several seconds the first time after it starts; after that a search takes well under a second.'}
      </div>
    </div>
  );
}

function ConnectionError({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const e = error instanceof ApiError ? error : null;
  const status = e?.status ?? 0;
  const { text, command } = splitCommand(
    e?.message ?? 'The app didn’t answer: is `nfl app` still running?',
  );
  if (status === 503) {
    return (
      <div className="notice ask-banner warn" role="alert">
        <span className="ic" aria-hidden="true">
          !
        </span>
        <div>
          <b>
            {e?.code === 'history_busy'
              ? 'The history graph is being rebuilt.'
              : e?.code === 'no_links'
                ? 'No person links yet.'
                : 'The history graph isn’t running.'}
          </b>{' '}
          {text}
          {command ? (
            <div style={{ marginTop: 8 }}>
              <span className="cmd">{command}</span>
            </div>
          ) : null}
          <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
            The Graph lab’s views still work: they read files the lab job wrote. (HTTP 503)
          </div>
        </div>
      </div>
    );
  }
  if (status === 409 || status >= 500 || !e) {
    return (
      <div className="notice ask-banner warn" role="alert">
        <span className="ic" aria-hidden="true">
          !
        </span>
        <div>
          <b>
            {status === 409
              ? 'Busy.'
              : status === 504
                ? 'Too slow.'
                : status
                  ? 'The search failed.'
                  : 'No answer.'}
          </b>{' '}
          {text}
          {status ? <span className="muted"> (HTTP {status})</span> : null}
        </div>
        <button type="button" className="btn sm ask-x" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }
  return (
    <div className="notice ask-banner err" role="alert">
      <span className="ic" aria-hidden="true">
        !
      </span>
      <div>
        <b>{status === 404 ? 'Not in the history graph.' : 'That pair can’t be connected.'}</b>{' '}
        {text} <span className="muted">(HTTP {status})</span>
      </div>
    </div>
  );
}

function ConnectionResult({ res }: { res: ConnectionsResponse }) {
  const [more, setMore] = useState(false);
  const [chain, setChain] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const four = useConnections(idOf(res.a), idOf(res.b), res.mode, 4, more);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [chain]);

  if (!res.found || !res.paths.length) {
    return (
      <article className="card gl-res" aria-labelledby="glResH">
        <div className="card-h">
          <span className="eyebrow" style={{ flexBasis: '100%' }}>
            No chain
          </span>
          <h2 id="glResH" ref={heading} tabIndex={-1}>
            {res.a.name} and {res.b.name}
          </h2>
        </div>
        <div className="card-b">
          <Meta res={res} path={null} />
          <div className="ask-state st-empty">
            <span className="glyph" aria-hidden="true">
              No link
            </span>
            <h3>{capFirst(res.note ?? 'no chain of person links joins them')}.</h3>
            <p>
              The person links cover 2018-2025 from who was on the field, plus this season’s
              coaches; a player who never took a snap with anyone linked has no chain.
            </p>
          </div>
        </div>
      </article>
    );
  }
  const paths = more && four.data?.paths.length ? four.data.paths : res.paths;
  const i = Math.min(chain, paths.length - 1);
  const p = paths[i];
  return (
    <article className="card gl-res" aria-labelledby="glResH">
      <div className="card-h">
        <span className="eyebrow" style={{ flexBasis: '100%' }}>
          {i === 0
            ? res.mode === 'fewest'
              ? 'The shortest chain'
              : 'The strongest chain'
            : `Chain ${i + 1} of ${paths.length}`}
        </span>
        <h2 id="glResH" ref={heading} tabIndex={-1}>
          {res.a.name} to {res.b.name}
        </h2>
        <span className="gl-hops">{plural(p.hops, 'link')}</span>
      </div>
      <div className="card-b">
        <Meta res={res} path={p} />
        <Chain path={p} />
        <div className="gl-legend" aria-hidden="true">
          {LINK_TYPES.map((t) => (
            <span key={t} className={`gl-ty-${t}`}>
              <i className={`gl-sw ${t}`} />
              <span style={{ color: 'var(--ink-2)' }}>{linkWord(t)}</span>
            </span>
          ))}
        </div>
        {!more ? (
          <div>
            <button type="button" className="btn sm" onClick={() => setMore(true)}>
              Show 3 more chains
            </button>{' '}
            <span className="muted" style={{ fontSize: 12 }}>
              {res.mode === 'fewest'
                ? 'the next shortest, strongest first'
                : 'the next strongest (Yen’s algorithm)'}
            </span>
          </div>
        ) : four.isFetching && !four.data ? (
          <p className="muted" role="status" style={{ margin: 0, fontSize: 13 }}>
            Finding more chains…
          </p>
        ) : four.error ? (
          <ConnectionError error={four.error} onRetry={() => void four.refetch()} />
        ) : paths.length < 2 ? (
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            No other chain joins them.
          </p>
        ) : (
          <Others paths={paths} shown={i} mode={res.mode} onPick={setChain} />
        )}
      </div>
    </article>
  );
}

function Meta({ res, path }: { res: ConnectionsResponse; path: ConnectionPath | null }) {
  return (
    <div className="gl-meta">
      <span className="chip flat">
        <span className="k">Mode</span> {res.mode === 'fewest' ? 'fewest links' : 'strongest links'}
      </span>
      {path && path.weakest_snaps != null ? (
        <TipTarget
          className="chip flat"
          lines={[
            'The weakest link',
            `The link with the fewest snaps in this chain: ${int(path.weakest_snaps)}`,
            ...(res.mode === 'strongest' && path.cost != null
              ? [`Total cost ${path.cost} (the sum of 1 ÷ snaps; lower is stronger)`]
              : []),
          ]}
          label={`Weakest link ${int(path.weakest_snaps)} snaps`}
        >
          <span className="k">Weakest link</span> {int(path.weakest_snaps)} snaps
        </TipTarget>
      ) : null}
      <TipTarget
        className="chip flat"
        lines={['Time', `The search took ${ms(res.ms)} on the history graph`]}
        label={`Time ${ms(res.ms)}`}
      >
        <span className="k">Time</span> {ms(res.ms)}
      </TipTarget>
      <TipTarget
        className="chip flat"
        lines={[
          'As of',
          res.asof.label,
          ...(res.asof.derived_at ? [`Person links made ${when(res.asof.derived_at)}`] : []),
        ]}
        label={`As of ${res.asof.label}`}
      >
        <span className="k">As of</span> {res.asof.label}
      </TipTarget>
    </div>
  );
}

function LinkLine({ type }: { type: string }) {
  return (
    <svg aria-hidden="true" focusable="false">
      <line className={`gl-ln ${type} v`} x1="22" y1="0" x2="22" y2="100%" />
      <line className={`gl-ln ${type} h`} x1="0" y1="23" x2="100%" y2="23" />
    </svg>
  );
}

function LinkEvidence({ link }: { link: ConnectionLink }) {
  const seasons = link.seasons?.length ?? 0;
  return (
    <div className="ev">
      <TipTarget
        className={`ty gl-ty-${link.type}`}
        lines={[
          capFirst(linkWord(link.type)),
          LINK_HELP[link.type] ?? '',
          `${link.snaps == null ? 'Snaps not known' : `${int(link.snaps)} snaps in all`}${seasons ? ` · ${plural(seasons, 'season')}` : ''}`,
        ]}
        label={`${linkWord(link.type)}: ${link.text}`}
      >
        <i className={`gl-sw ${link.type}`} aria-hidden="true" />
        <span className="lbl">{linkWord(link.type)}</span>
      </TipTarget>
      <span className="tx">{link.text}</span>
    </div>
  );
}

/** The chain: people (pills) and links (an SVG line each + the evidence). Up to 2 links (3 on a
 *  wide card) it runs left to right; longer chains and narrow screens run top to bottom. */
export function Chain({ path }: { path: ConnectionPath }) {
  const n = path.people.length;
  return (
    <div className="gl-chainwrap">
      <ol
        className="gl-chain"
        data-hops={Math.min(path.hops, 9)}
        aria-label={`The chain: ${plural(path.hops, 'link')}`}
      >
        {path.people.map((x, i) => {
          const items = [
            <li
              key={`p${i}`}
              className={`gl-person${i === 0 || i === n - 1 ? ' end' : ''}${x.kind === 'coach' ? ' coach' : ''}`}
            >
              <b>{x.name}</b>
              <small>{personSub(x)}</small>
            </li>,
          ];
          const link = path.links[i];
          if (i < n - 1 && link) {
            items.push(
              <li key={`l${i}`} className="gl-link">
                <LinkLine type={link.type} />
                <LinkEvidence link={link} />
              </li>,
            );
          }
          return items;
        })}
      </ol>
    </div>
  );
}

function Others({
  paths,
  shown,
  mode,
  onPick,
}: {
  paths: ConnectionPath[];
  shown: number;
  mode: ConnectionMode;
  onPick: (i: number) => void;
}) {
  return (
    <div className="gl-others">
      <h3>{paths.length} chains</h3>
      <ol>
        {paths.map((p, j) => {
          const mid = p.people.slice(1, -1);
          return (
            <li key={j}>
              <button type="button" aria-current={j === shown} onClick={() => onPick(j)}>
                <span className="n">{plural(p.hops, 'link')}</span>
                <span className="via">
                  {mid.length ? 'via ' : 'directly'}
                  {mid.map((x, k) => (
                    <span key={k}>
                      {k ? ' → ' : ''}
                      <b>{x.name}</b>
                      {x.kind === 'coach' ? ' (coach)' : ''}
                    </span>
                  ))}
                </span>
                <span className="tys" aria-label={p.links.map((l) => linkWord(l.type)).join(', ')}>
                  {p.links.map((l, k) => (
                    <i key={k} className={`gl-sw ${l.type} gl-ty-${l.type}`} />
                  ))}
                </span>
                <span className="w">
                  {p.weakest_snaps != null ? `weakest link ${int(p.weakest_snaps)} snaps` : ''}
                  {mode === 'strongest' && p.cost != null ? ` · cost ${p.cost}` : ''}
                  {j === shown ? ' · shown above' : ''}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
