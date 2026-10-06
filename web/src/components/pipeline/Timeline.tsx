// View 3: the timeline (mockup: timelineHTML + timelineTick). One row per step with a bar on a
// time axis: solid = finished, striped = running, dashed = expected. Hovering or focusing a bar
// shows its time (the HBars tooltip pattern). Drawn from the model.

import { Fragment, useState } from 'react';
import { duration } from '../../lib/format';
import { glyphOf, timelineLayout, type TimelineRow } from './geometry';
import type { PipelineModel } from './model';

function barClass(row: TimelineRow): string {
  return row.status === 'running'
    ? 'running'
    : row.status === 'failed'
      ? 'failed'
      : row.planned || row.status === 'none'
        ? 'planned'
        : '';
}

function barTip(row: TimelineRow): string {
  if (row.status === 'pending') return `expected ~${duration(row.seconds)}`;
  if (row.status === 'skipped') return `skipped · expected ~${duration(row.seconds)}`;
  return duration(row.seconds);
}

function timeText(row: TimelineRow): string {
  return row.status === 'pending'
    ? `~${duration(row.seconds)}`
    : row.status === 'skipped'
      ? 'skipped'
      : row.status === 'none'
        ? '—'
        : duration(row.seconds);
}

export function Timeline({ model, sittings }: { model: PipelineModel; sittings: number }) {
  const [hover, setHover] = useState<string | null>(null);
  const layout = timelineLayout(model);
  const digest = layout.rows.find((r) => r.key === 'digest');
  const lead =
    layout.digestShare != null
      ? `The digest is about ${layout.digestShare}% of the run's time`
      : digest?.status === 'skipped'
        ? 'The digest was skipped because an earlier step failed'
        : null;
  const share = lead ? `${lead}${sittings > 1 ? ` (this week ran in ${sittings} sittings: the gaps between them are left out)` : ''}. ` : '';

  return (
    <>
      <div className="tablewrap">
        <div className="gantt" style={{ minWidth: 640 }}>
          <div className="ghead">Step</div>
          <div className="ghead">
            <div className="gaxis">
              {layout.ticks.map((t) => (
                <span key={t.label} style={{ left: `${t.left}%` }}>
                  {t.label}
                </span>
              ))}
            </div>
          </div>
          <div className="ghead" style={{ justifyContent: 'flex-end' }}>
            Time
          </div>
          {layout.rows.map((row, i) => {
            const st = model.steps[i];
            const icon = glyphOf(row.status) || String(i + 1);
            const iconClass = row.status === 'ok' || row.status === 'failed' || row.status === 'running' ? row.status : '';
            const tip = `${row.label}\n${barTip(row)}`;
            // tooltips open below the first rows and above the later ones, so the scroll box never clips them
            const above = i >= 4;
            const nearEnd = row.left > 50;
            return (
              <Fragment key={row.key}>
                <div className="gname">
                  <span className={['sicon', iconClass].filter(Boolean).join(' ')}>{icon}</span>
                  <span>
                    <b>{row.label}</b>
                    <small>{st.detail}</small>
                  </span>
                </div>
                <div className="gtrack">
                  {layout.grid.map((g) => (
                    <span key={g} className="gl" style={{ left: `${g}%` }} />
                  ))}
                  {row.hasBar ? (
                    <>
                      <span
                        className={['bar', barClass(row)].filter(Boolean).join(' ')}
                        data-step={row.key}
                        role="img"
                        aria-label={tip.replace('\n', ': ')}
                        tabIndex={0}
                        style={{ left: `${row.left}%`, width: `${row.width}%` }}
                        onPointerEnter={() => setHover(row.key)}
                        onPointerLeave={() => setHover(null)}
                        onFocus={() => setHover(row.key)}
                        onBlur={() => setHover(null)}
                      />
                      {hover === row.key ? (
                        <span
                          role="tooltip"
                          className="charttip"
                          style={{
                            ...(nearEnd ? { right: `${Math.max(0, 100 - row.left - row.width)}%` } : { left: `${row.left}%` }),
                            ...(above ? { bottom: 30 } : { top: 30 }),
                          }}
                        >
                          {tip.split('\n').map((line, j) => (
                            <div key={j} className={j === 0 ? 'th' : undefined}>
                              {line}
                            </div>
                          ))}
                        </span>
                      ) : null}
                    </>
                  ) : null}
                </div>
                <div className="gdur">{timeText(row)}</div>
              </Fragment>
            );
          })}
        </div>
      </div>
      <p className="muted" style={{ margin: '10px 0 0', fontSize: 12.5 }}>
        {share}
        Striped = running, dashed = expected.
      </p>
    </>
  );
}
