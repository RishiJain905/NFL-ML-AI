// Model only vs market, one row per game (mockup: dumbbell()). Each row draws the home team's win
// chance from the market (a filled --s3 dot) and from the model without the market (a --s1 ring),
// joined by a line; rows are sorted by the gap. One tab stop per row, with the mockup's tooltip on
// hover and keyboard focus, and the gap written out so no value rests on position or colour alone.

import type { CSSProperties } from 'react';
import { pct, signed } from '../lib/format';
import { TipTarget } from './TipTarget';
import './dumbbell.css';

export interface DumbbellGame {
  game_id: string;
  away: string;
  home: string;
  p_model_only: number | null;
  p_market: number | null;
}

const AXIS = [0, 0.25, 0.5, 0.75, 1];
const clamp = (p: number) => Math.max(0, Math.min(1, p));

export function Dumbbell({ games, label }: { games: DumbbellGame[]; label: string }) {
  const rows = games
    .flatMap((g) =>
      g.p_model_only != null && g.p_market != null
        ? [{ g, model: g.p_model_only, market: g.p_market, gap: g.p_model_only - g.p_market }]
        : [],
    )
    .sort((a, b) => b.gap - a.gap);
  if (rows.length === 0) return null;
  const width = Math.min(150, Math.max(70, ...rows.map((r) => `${r.g.away} at ${r.g.home}`.length * 7 + 8)));
  return (
    <div>
      <div className="legend" style={{ marginBottom: 6 }}>
        <span>
          <i className="db-key model" aria-hidden="true" />
          Model only
        </span>
        <span>
          <i className="db-key market" aria-hidden="true" />
          Market
        </span>
        <span className="muted">home team&apos;s win chance · sorted by the gap (model only − market)</span>
      </div>
      <div className="dumbbell" role="group" aria-label={label} style={{ '--db-label': `${width}px` } as CSSProperties}>
        {rows.map(({ g, model, market, gap }) => {
          const game = `${g.away} at ${g.home}`;
          const gapText = `${signed(Math.round(gap * 100), 0)} pts`; // rounded first: no "−0 pts"
          const lines = [game, `model only: ${g.home} ${pct(model)}`, `market: ${g.home} ${pct(market)}`, `gap ${gapText}`];
          const lo = clamp(Math.min(model, market));
          const hi = clamp(Math.max(model, market));
          return (
            <TipTarget
              key={g.game_id}
              as="div"
              className="db-row"
              lines={lines}
              label={`${game}: model only ${g.home} ${pct(model)}, market ${g.home} ${pct(market)}, gap ${gapText}`}
            >
              <span className="db-label">{game}</span>
              <span className="db-track" aria-hidden="true">
                <span className="db-link" style={{ left: `${lo * 100}%`, width: `${(hi - lo) * 100}%` }} />
                <span className="db-dot market" style={{ left: `${clamp(market) * 100}%` }} />
                <span className="db-dot model" style={{ left: `${clamp(model) * 100}%` }} />
              </span>
              <span className="db-gap num">{gapText}</span>
            </TipTarget>
          );
        })}
        <div className="db-row db-axis" aria-hidden="true">
          <span />
          <span className="db-track">
            {AXIS.map((t) => (
              <span key={t} className="db-tick" style={{ left: `${t * 100}%` }}>
                {t * 100}%
              </span>
            ))}
          </span>
          <span />
        </div>
      </div>
    </div>
  );
}
