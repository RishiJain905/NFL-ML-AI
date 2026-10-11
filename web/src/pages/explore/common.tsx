// Shared bits for Explore → Play calling and the Play calls week tab (PC01): the empty state that
// names the command to run (the server's `message`), the loading block, the FTN-waiting chip and
// the season switch.

import type { ReactNode } from 'react';
import type { PlaycallMeta } from '../../api/types';
import { Card, Chip, EmptyState, Segmented } from '../../components/ui';
import './explore.css';

/** The server's message, with a `command` in backticks pulled out as a copyable line. */
export function PlaycallEmpty({ meta, title, glyph = 'PC' }: { meta: Pick<PlaycallMeta, 'message'>; title: string; glyph?: string }) {
  const msg = meta.message ?? '';
  const cmd = /`([^`]+)`/.exec(msg)?.[1];
  return (
    <EmptyState glyph={glyph} title={title} actions={cmd ? <div className="cmd">{cmd}</div> : undefined}>
      {msg.replace(/`([^`]+)`/g, '$1') || null}
    </EmptyState>
  );
}

export function PlaycallLoading({ what }: { what: string }) {
  return (
    <Card className="pad sk">
      <div role="status" aria-busy="true" aria-label={`Loading ${what}`}>
        <span className="skl w40" />
        <span className="skl w70" />
        <span className="skl w55" />
        <div className="skgrid">
          {Array.from({ length: 6 }, (_, i) => (
            <span key={i} className="skb" />
          ))}
        </div>
      </div>
    </Card>
  );
}

/** "FTN waiting: 1", naming the games FTN hasn't charted yet. */
export function FtnWaiting({ games }: { games: string[] }) {
  if (!games.length) return null;
  return (
    <Chip
      tone="warn"
      icon="!"
      title={`FTN hasn't charted these games yet: ${games.join('; ')}. Play-action, screens, RPO, motion, blitz and box rates wait for them.`}
    >
      FTN waiting: {games.length}
    </Chip>
  );
}

/** A segmented switch for two or three seasons; a select beyond that (the tables go back to 2016). */
export function SeasonSwitch({ seasons, value, onChange }: { seasons: number[]; value: number; onChange: (s: number) => void }) {
  if (seasons.length < 2) return null;
  if (seasons.length > 3) {
    return (
      <label className="sortsel">
        <span className="eyebrow">Season</span>
        <select value={String(value)} onChange={(e) => onChange(Number(e.target.value))}>
          {seasons.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <Segmented
      label="Season"
      options={seasons.map((s) => ({ value: String(s), label: String(s) }))}
      value={String(value)}
      onChange={(v) => onChange(Number(v))}
    />
  );
}

/** The page header shared by the grid and a team page (the season pages' `top` layout). */
export function ExploreHeader({
  eyebrow,
  title,
  chips,
  right,
}: {
  eyebrow: ReactNode;
  title: ReactNode;
  chips?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <header className="top">
      <div className="titleblock">
        <span className="eyebrow">{eyebrow}</span>
        <h1 className="tname">{title}</h1>
        {chips ? <div className="chips">{chips}</div> : null}
      </div>
      {right ? <div className="right">{right}</div> : null}
    </header>
  );
}
