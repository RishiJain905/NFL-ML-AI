// The 80% range bar (mockup: rangeHTML): the 10th–90th percentile band, the projection mark,
// a dotted mark at the player's rolling baseline and, once graded, the actual as a ◆. `mini`
// is the table version (mockup: minirange). Tooltip on hover and keyboard focus.

import { TipTarget } from './TipTarget';

/** 79 → "79", 2.64 → "2.6": whole numbers for big stats, one decimal for small ones. */
function statNum(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return v.toFixed(Math.abs(v) >= 20 ? 0 : 1);
}

export function RangeBar({
  who,
  p10,
  p90,
  projection,
  baseline,
  actual,
  mini,
}: {
  who: string;
  p10: number | null;
  p90: number | null;
  projection: number | null;
  baseline: number | null;
  actual?: number | null;
  mini?: boolean;
}) {
  const vals = [p10, p90, projection, baseline, actual].filter((v): v is number => v != null && Number.isFinite(v));
  if (vals.length === 0) return <span className="muted">—</span>;
  const lo = Math.min(0, ...vals);
  const top = Math.max(...vals);
  const hi = top + (top - lo) * (mini ? 0.1 : 0.15) || 1;
  const P = (v: number) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));

  const lines = [
    who,
    `range ${statNum(p10)}–${statNum(p90)} · projection ${statNum(projection)}`,
    `baseline ${statNum(baseline)}`,
  ];
  if (actual != null) lines.push(`actual ${statNum(actual)}`);

  return (
    <TipTarget as="div" className={mini ? 'minirange' : 'range'} lines={lines}>
      <span className="rt" />
      {p10 != null && p90 != null ? (
        <span className="rb" style={{ left: `${P(p10)}%`, width: `${Math.max(0, P(p90) - P(p10))}%` }} />
      ) : null}
      {baseline != null ? <span className="rbase" style={{ left: `${P(baseline)}%` }} /> : null}
      {projection != null ? <span className="rp" style={{ left: `${P(projection)}%` }} /> : null}
      {actual != null && !mini ? <span className="ract" style={{ left: `${P(actual)}%` }} /> : null}
    </TipTarget>
  );
}
