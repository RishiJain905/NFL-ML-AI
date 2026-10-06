// The game card's dot strip (mockup: probbar): the home team's win chance from each source on
// one track, away ← 50% → home. The chance shown in the digest is a filled --s1 dot, the
// model-only chance a --s1 ring, Elo --s2, the market --s3. Each dot has its own tooltip.

import type { CSSProperties } from 'react';
import { pct } from '../lib/format';
import { TipTarget } from './TipTarget';

interface Dot {
  p: number | null;
  name: string;
  color: string;
  hollow?: boolean;
  main?: boolean;
}

export function DotStrip({
  home,
  away,
  pHome,
  pModelOnly,
  pElo,
  pMarket,
}: {
  home: string; // the names used in the tooltips ("Commanders")
  away: string;
  pHome: number | null;
  pModelOnly: number | null;
  pElo: number | null;
  pMarket: number | null;
}) {
  // drawn bottom to top: the shown chance last, so it sits on top
  const dots: Dot[] = [
    { p: pElo, name: 'Elo', color: 'var(--s2)' },
    { p: pMarket, name: 'Market', color: 'var(--s3)' },
    { p: pModelOnly, name: 'Model only', color: 'var(--s1)', hollow: true },
    { p: pHome, name: 'Model (shown in the digest)', color: 'var(--s1)', main: true },
  ];
  return (
    <div className="probbar" role="group" aria-label={`${home} win chance by source`}>
      <span className="track" />
      <span className="mid" />
      {dots.map((d) => {
        if (d.p == null) return null;
        const style: CSSProperties = d.hollow
          ? { left: `${d.p * 100}%`, background: 'var(--panel)', border: `2.5px solid ${d.color}` }
          : { left: `${d.p * 100}%`, background: d.color };
        const line = `${home} ${pct(d.p)} · ${away} ${pct(1 - d.p)}`;
        return (
          <TipTarget
            key={d.name}
            className={d.main ? 'mk main' : 'mk'}
            style={style}
            lines={[d.name, line]}
            label={`${d.name}: ${line}`}
          />
        );
      })}
    </div>
  );
}
