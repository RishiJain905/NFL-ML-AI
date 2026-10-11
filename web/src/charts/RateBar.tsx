// Play calling's rate bar (mockup: rateBar / pctMeter / rateRow): the team's rate as a bar from 0
// with the league as a tick; pass rate over expected (PROE) and EPA per play run from the league
// tick instead (the league sits near -2 points; EPA can be negative). A small sample (n < min_n) is hatched and greyed.
// `RateRow` is one Identity line: the name, the value with n, the bar, the league and the
// percentile (a neutral dot meter: "more of it", not "better"), with a tooltip on hover and focus.

import type { PlaycallCell, PlaycallMetric } from '../api/types';
import { centred, fmtRate, niceTop, ordinal, type RateDomain } from '../lib/playcall';
import { TipTarget } from './TipTarget';
import './playcall.css';

const clamp = (v: number) => Math.max(0, Math.min(100, v));


export function RateBar({
  metric,
  cell,
  compact,
  domain,
}: {
  metric: Pick<PlaycallMetric, 'unit'>;
  cell: PlaycallCell | null | undefined;
  compact?: boolean;
  domain?: RateDomain;
}) {
  const cls = `rbar${compact ? ' sm' : ''}${cell?.small ? ' small' : ''}`;
  if (!cell || cell.value == null) {
    return (
      <span className={`${cls} empty`} aria-hidden="true">
        <span className="rt" />
      </span>
    );
  }
  const v = cell.value;
  const L = cell.league;
  if (centred(metric, v, L)) {
    const base = L ?? 0;
    const span = domain?.span ?? Math.max(0.12, Math.abs(v - base) * 1.25);
    const x = clamp(50 + ((v - base) / span) * 50);
    const a = Math.min(x, 50);
    const b = Math.max(x, 50);
    return (
      <span className={cls} aria-hidden="true" data-testid="ratebar">
        <span className="rt" />
        <span className={`rb ${v >= base ? 'up' : 'dn'}`} style={{ left: `${a.toFixed(1)}%`, width: `${Math.max(0.8, b - a).toFixed(1)}%` }} />
        <span className="lgm" style={{ left: '50%' }} />
      </span>
    );
  }
  const top =
    domain?.top ??
    (metric.unit === 'share' ? Math.min(1, niceTop(Math.max(v, L ?? 0) * 1.2)) : niceTop(Math.max(v, L ?? 0) * 1.2));
  const X = (x: number) => clamp((x / top) * 100);
  return (
    <span className={cls} aria-hidden="true" data-testid="ratebar">
      <span className="rt" />
      <span className="rb" style={{ width: `${Math.max(0.8, X(v)).toFixed(1)}%` }} />
      {L != null ? <span className="lgm" style={{ left: `${X(L).toFixed(1)}%` }} /> : null}
    </span>
  );
}

export function PctMeter({ pct }: { pct: number | null | undefined }) {
  if (pct == null) {
    return (
      <span className="pctm">
        <b>—</b>
      </span>
    );
  }
  return (
    <span className="pctm">
      <b>{ordinal(pct)}</b>
      <span className="pm" aria-hidden="true">
        <span className="pd" style={{ left: `${clamp(pct).toFixed(0)}%` }} />
      </span>
    </span>
  );
}

/** The column heads above a list of `RateRow`s, with the bar's legend. */
export function RateHead() {
  return (
    <div className="rrow rhead" aria-hidden="true">
      <span>Metric</span>
      <span className="rv">Team</span>
      <span className="rbar-h">
        <span className="lgkey">
          <i className="b" />
          team
        </span>
        <span className="lgkey">
          <i className="l" />
          league
        </span>
      </span>
      <span className="rlg">League</span>
      <span className="pctm">Pctile</span>
    </div>
  );
}

export function RateRow({
  metric,
  cell,
  tip,
  domain,
}: {
  metric: Pick<PlaycallMetric, 'short' | 'label' | 'unit' | 'digits'>;
  cell: PlaycallCell | null | undefined;
  tip: string[];
  domain?: RateDomain;
}) {
  const small = Boolean(cell?.small);
  const none = !cell || cell.value == null;
  const league = cell?.league != null ? fmtRate(metric, cell.league) : '—';
  return (
    <TipTarget as="div" className={`rrow${small ? ' small' : ''}${none ? ' none' : ''}`} lines={tip}>
      <span className="rl">
        {metric.short}
        <small>{metric.label}</small>
        <small className="ph">
          lg {league}
          {cell?.pct != null ? ` · ${ordinal(cell.pct)} pct` : ''}
        </small>
      </span>
      <span className="rv num">
        {fmtRate(metric, cell?.value)}
        <small>{cell ? `n ${cell.n}${small ? ' · small' : ''}` : 'no data'}</small>
      </span>
      <RateBar metric={metric} cell={cell} domain={domain} />
      <span className="rlg num">{league}</span>
      <PctMeter pct={cell?.pct} />
    </TipTarget>
  );
}
