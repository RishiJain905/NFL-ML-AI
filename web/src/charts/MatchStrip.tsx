// The Play calls tab's matchup strip (mockup: matchViz): on one track, the offense's own rate
// (--s1), what the other defense allows or calls (--s2) and the league (a tick). Small samples are
// hollow and dashed. The strip shows the tooltip on hover only: the row's name cell is the
// focusable target with the same lines (visible at every width; the strip hides on a phone).

import type { PlaycallCell, PlaycallMetric } from '../api/types';
import { TipTarget } from './TipTarget';
import './playcall.css';

export function MatchStrip({
  metric,
  offense,
  defense,
  league,
  tip,
}: {
  metric: Pick<PlaycallMetric, 'unit'>;
  offense: PlaycallCell | null;
  defense: PlaycallCell | null;
  league: number | null;
  tip: string[];
}) {
  const vs = [offense?.value, defense?.value, league].filter((v): v is number => v != null);
  if (!vs.length) return null;
  let lo = Math.min(...vs);
  let hi = Math.max(...vs);
  if (metric.unit === 'share') lo = Math.min(lo, 0);
  const pad = (hi - lo) * 0.18 || 0.05;
  if (metric.unit !== 'share') lo -= pad;
  hi += pad;
  const X = (v: number) => `${(((v - lo) / (hi - lo)) * 100).toFixed(1)}%`;
  return (
    <TipTarget as="div" className="mv" lines={tip} focusable={false}>
      <span className="mt" />
      {league != null ? <span className="ml" style={{ left: X(league) }} /> : null}
      {defense?.value != null ? <span className={`md d${defense.small ? ' small' : ''}`} style={{ left: X(defense.value) }} /> : null}
      {offense?.value != null ? <span className={`md o${offense.small ? ' small' : ''}`} style={{ left: X(offense.value) }} /> : null}
    </TipTarget>
  );
}
