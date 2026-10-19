// Explore -> Ask the Engine's header (AE03): the title, the three-part switch (Ask ·
// Connections · Graph lab: route links, so each part has a shareable URL) and a part's status
// line. Ask (AE01) passes its sources line; the two new parts pass the history graph's.

import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import type { GraphLabStatus } from '../../../api/types';
import { TipTarget } from '../../../charts/TipTarget';
import { int, when } from '../../../lib/graphlab';
import './graphlab.css';

export function AskParts() {
  return (
    <nav className="gl-parts" aria-label="Ask the Engine">
      <NavLink to="/explore/ask" end>
        Ask
      </NavLink>
      <NavLink to="/explore/ask/connections">Connections</NavLink>
      <NavLink to="/explore/ask/lab">Graph lab</NavLink>
    </nav>
  );
}

export function AskHeader({ status }: { status?: ReactNode }) {
  return (
    <header className="top">
      <div className="titleblock">
        <span className="eyebrow">Explore</span>
        <h1 className="tname">Ask the Engine</h1>
        <AskParts />
        {status}
      </div>
    </header>
  );
}

const HIST: Record<string, { tone: string; word: string }> = {
  up: { tone: 'ok', word: 'up' },
  down: { tone: 'err', word: 'down' },
  checking: { tone: 'flat', word: 'checking' },
  busy: { tone: 'warn', word: 'being rebuilt' },
};

/** The history graph's label and state, and the person links' label (Connections, Graph lab). */
export function HistoryStatus({ st }: { st: GraphLabStatus | undefined }) {
  if (!st) return null;
  const h = st.history;
  const l = st.links;
  const s = HIST[st.busy ? 'busy' : st.history_state] ?? { tone: 'flat', word: st.history_state };
  const hLabel = h?.label ?? 'history graph: not built';
  const lLabel = l?.label ?? 'person links: not made yet';
  return (
    <div className="ask-status" aria-label="The history graph">
      <TipTarget
        lines={[
          'The history graph (Neo4j, its own container)',
          h?.label ?? 'not built',
          'Connections asks it; the lab views read files the lab job wrote',
          `State: ${s.word}`,
        ]}
        label={`${hLabel}, ${s.word}`}
      >
        <i className={`dot ${s.tone}`} aria-hidden="true" />
        {hLabel} · {s.word}
      </TipTarget>
      <TipTarget
        lines={[
          'The person links (`nfl history-graph derive`)',
          l?.label ?? 'not made yet',
          ...(l?.derived_at ? [`Made ${when(l.derived_at)}`] : []),
          ...Object.entries(l?.counts ?? {}).map(([k, v]) => `${k}: ${int(v)}`),
        ]}
        label={lLabel}
      >
        <i className={`dot ${l ? 'ok' : 'warn'}`} aria-hidden="true" />
        {lLabel}
      </TipTarget>
    </div>
  );
}
