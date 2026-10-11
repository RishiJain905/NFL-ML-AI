// Play calling's situation heat table (mockup: situationsCard): rows = one family's buckets under an
// "All plays" baseline row, columns = the situational metrics. Each cell shows the value and n,
// tinted by the difference from the league in the same situation (diverging: --s2 less → the
// panel → --s1 more). Small samples are hatched and never tinted. Tooltip on hover and focus.

import type { PlaycallMetric, PlaycallSide, PlaycallSituationRow, PlaycallWindow } from '../api/types';
import { allowedNote, cellTip, divTint, fmtRate } from '../lib/playcall';
import { TipTarget } from './TipTarget';
import './playcall.css';

function Cell({
  row,
  m,
  win,
  who,
  minN,
  side,
}: {
  row: PlaycallSituationRow;
  m: PlaycallMetric;
  win: PlaycallWindow;
  who: string;
  minN: number;
  side: PlaycallSide;
}) {
  const c = row.cells[m.metric]?.[win] ?? null;
  const none = !c || c.value == null;
  return (
    <td className={`hc${c?.small ? ' small' : ''}${none ? ' none' : ''}`} style={divTint(m, c)}>
      <TipTarget as="div" className="hct" lines={cellTip(m, c, who, row.label, minN, allowedNote(m, side))}>
        <b className="num">{fmtRate(m, c?.value)}</b>
        <small className="num">{c ? `n ${c.n}` : '—'}</small>
      </TipTarget>
    </td>
  );
}

export function HeatTable({
  label,
  metrics,
  baseline,
  rows,
  win,
  who,
  minN,
  side,
}: {
  label: string;
  metrics: PlaycallMetric[];
  baseline: PlaycallSituationRow;
  rows: PlaycallSituationRow[];
  win: PlaycallWindow;
  who: string;
  minN: number;
  side: PlaycallSide;
}) {
  return (
    <div className="tablewrap">
      <table className="tbl heat" aria-label={label}>
        <thead>
          <tr>
            <th>Situation</th>
            {metrics.map((m) => (
              <th key={m.metric} className="c" title={m.label}>
                {m.short}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr className="baserow">
            <th scope="row">
              {baseline.label}
              <small>the row to compare with</small>
            </th>
            {metrics.map((m) => (
              <Cell key={m.metric} row={baseline} m={m} win={win} who={who} minN={minN} side={side} />
            ))}
          </tr>
          {rows.map((r) => (
            <tr key={r.situation}>
              <th scope="row">{r.label}</th>
              {metrics.map((m) => (
                <Cell key={m.metric} row={r} m={m} win={win} who={who} minN={minN} side={side} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The diverging legend under the heat table and the field. */
export function DivLegend({ minN }: { minN: number }) {
  return (
    <span className="divlegend">
      <span>Less than the league</span>
      <i className="ramp" aria-hidden="true" />
      <span>More</span>
      <span className="muted">· greyed: n under {minN}</span>
    </span>
  );
}
