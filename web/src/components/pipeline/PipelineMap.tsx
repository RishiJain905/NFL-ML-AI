// View 2: the pipeline map (mockup: mapHTML + mapTick). Sources flow through the nine steps along
// a snake and end at Published. A ring fills as a step runs, the edge into the running step
// flows, and a file or W&B chip appears under each step as it finishes. Drawn from the model.

import type { StepStatus } from '../../api/types';
import { duration } from '../../lib/format';
import { MAP, RING_C, RING_R, clip, isFinished } from './geometry';
import { STEP_DEFS, STEP_INDEX, type PipelineModel, type ViewStep } from './model';

const SOURCES = ['nflverse', 'NGS · PFR · FTN', 'ESPN', 'The Odds API'];

/** The edge that turns the snake's corner starts below the top node's name and sub-line (the mockup ran it through them). */
const LABEL_CLEAR = 78;

function edgeClass(status: StepStatus): string {
  return isFinished(status) ? 'done' : status === 'running' ? 'active' : '';
}

function ringColor(status: StepStatus): string {
  return status === 'ok'
    ? 'var(--ok)'
    : status === 'degraded'
      ? 'var(--warn)'
      : status === 'failed'
        ? 'var(--err)'
        : status === 'running'
          ? 'var(--accent)'
          : 'var(--line-2)';
}

function glyphFor(status: StepStatus, n: number): string {
  return status === 'ok' ? '✓' : status === 'degraded' ? '!' : status === 'failed' ? '!' : status === 'none' ? '–' : String(n);
}

function glyphFill(status: StepStatus): string {
  return status === 'ok'
    ? 'var(--ok)'
    : status === 'degraded'
      ? 'var(--warn)'
      : status === 'failed'
        ? 'var(--err)'
        : status === 'running'
          ? 'var(--accent-ink)'
          : 'var(--ink-3)';
}

/** The small line under a node's name. */
function subLine(st: ViewStep): string {
  switch (st.status) {
    case 'running':
      return clip(st.detail);
    case 'ok':
    case 'degraded':
      return duration(st.seconds);
    case 'failed':
      return 'failed';
    case 'skipped':
      return 'skipped';
    case 'none':
      return 'not recorded';
    default:
      return `~${duration(st.expected)}`;
  }
}

export function PipelineMap({ model }: { model: PipelineModel }) {
  const status = (name: keyof typeof STEP_INDEX): StepStatus => model.steps[STEP_INDEX[name]].status;
  const pubDone = isFinished(status('digest'));
  const [sx, sy] = MAP.sources;
  const [px, py] = MAP.published;
  const [ix] = MAP.ingest;
  const [rx, ry] = MAP.records;

  return (
    <>
      <div className="vizwrap">
        <svg
          viewBox="0 0 1000 400"
          role="img"
          aria-label="Pipeline map: sources flow through each step; files and W&B artifacts appear as each step finishes"
        >
          {/* edges: each one belongs to the step it flows into */}
          <path className={`flow-edge ${edgeClass(status('ingest'))}`} d={`M${sx + 62} ${sy} H${ix - 36}`} />
          {STEP_DEFS.slice(1).map((d, n) => {
            const a = MAP[STEP_DEFS[n].name];
            const b = MAP[d.name];
            const path =
              a[1] !== b[1]
                ? `M${a[0]} ${a[1] + LABEL_CLEAR} V${b[1] - 36}`
                : `M${a[0] + (b[0] > a[0] ? 36 : -36)} ${a[1]} H${b[0] + (b[0] > a[0] ? -36 : 36)}`;
            return <path key={d.name} className={`flow-edge ${edgeClass(status(d.name))}`} d={path} />;
          })}
          <path
            className={`flow-edge ${pubDone && status('records') !== 'pending' ? 'done' : ''}`}
            d={`M${rx - 36} ${ry} H${px + 40}`}
          />

          {/* the sources */}
          <g>
            {SOURCES.map((t, i) => (
              <g key={t}>
                <rect x={sx - 62} y={sy - 44 + i * 23} width={124} height={19} rx={9.5} style={{ fill: 'var(--raised)', stroke: 'var(--line-2)' }} />
                <text x={sx} y={sy - 30.5 + i * 23} textAnchor="middle" fontSize={11} style={{ fill: 'var(--ink-2)' }}>
                  {t}
                </text>
              </g>
            ))}
            <text x={sx} y={sy + 62} textAnchor="middle" fontSize={12.5} fontWeight={700} style={{ fill: 'var(--ink)' }}>
              Sources
            </text>
          </g>

          {/* the steps */}
          {STEP_DEFS.map((d, i) => {
            const [x, y] = MAP[d.name];
            const st = model.steps[i];
            const top = y < 200;
            const chipY = top ? y - 76 : y + 92;
            const chipText = d.chip(model.season, model.week);
            const chipW = chipText.length * 6.6 + 26;
            return (
              <g key={d.name} data-node={d.name}>
                <circle
                  className="node-ring"
                  cx={x}
                  cy={y}
                  r={RING_R}
                  style={{ stroke: st.status === 'ok' || st.status === 'degraded' || st.status === 'failed' ? ringColor(st.status) : 'var(--line-2)' }}
                />
                {st.status === 'running' ? (
                  <circle
                    className="node-prog"
                    cx={x}
                    cy={y}
                    r={RING_R}
                    strokeDasharray={RING_C}
                    strokeDashoffset={RING_C * (1 - Math.min(1, Math.max(0, st.progress)))}
                    transform={`rotate(-90 ${x} ${y})`}
                  />
                ) : null}
                <circle className="node-core" cx={x} cy={y} r={26} />
                <text className="field-num" x={x} y={y + 7} textAnchor="middle" fontSize={21} style={{ fill: glyphFill(st.status) }}>
                  {glyphFor(st.status, i + 1)}
                </text>
                <text x={x} y={top ? y + 54 : y + 52} textAnchor="middle" fontSize={12.5} fontWeight={700} style={{ fill: 'var(--ink)' }}>
                  {d.label}
                </text>
                <text x={x} y={top ? y + 69 : y + 67} textAnchor="middle" fontSize={10.5} style={{ fill: 'var(--ink-3)' }}>
                  {subLine(st)}
                </text>
                <g className="artifact-chip" style={{ opacity: isFinished(st.status) ? 1 : 0 }}>
                  <rect
                    x={x - chipW / 2}
                    y={chipY - 12}
                    width={chipW}
                    height={22}
                    rx={5}
                    style={{ fill: d.wb ? 'var(--accent-wash)' : 'var(--raised)', stroke: d.wb ? 'var(--accent)' : 'var(--line-2)' }}
                  />
                  <text x={x} y={chipY + 3} textAnchor="middle" fontSize={11} fontWeight={600} style={{ fill: 'var(--ink-2)' }}>
                    {chipText}
                  </text>
                </g>
              </g>
            );
          })}

          {/* the finish: Published */}
          <g data-node="published">
            <circle
              cx={px}
              cy={py}
              r={40}
              strokeWidth={2}
              style={{ fill: pubDone ? 'var(--accent)' : 'var(--raised)', stroke: pubDone ? 'var(--accent)' : 'var(--line-2)' }}
            />
            <text
              className="field-num"
              x={px}
              y={py + 6}
              textAnchor="middle"
              fontSize={17}
              letterSpacing=".04em"
              style={{ fill: pubDone ? 'var(--on-accent)' : 'var(--ink-3)' }}
            >
              {pubDone ? 'LIVE' : 'DIGEST'}
            </text>
            <text x={px} y={py + 60} textAnchor="middle" fontSize={12.5} fontWeight={700} style={{ fill: 'var(--ink)' }}>
              Published
            </text>
            <text x={px} y={py + 75} textAnchor="middle" fontSize={10.5} style={{ fill: 'var(--ink-3)' }}>
              {pubDone ? `week${String(model.week).padStart(2, '0')}-digest.md` : 'not yet'}
            </text>
          </g>
        </svg>
      </div>
      <div className="legend" style={{ marginTop: 8 }}>
        <span>
          <i style={{ background: 'var(--ok)' }} />
          Finished
        </span>
        <span>
          <i style={{ background: 'var(--accent)' }} />
          Running (the moving line is data flowing in)
        </span>
        <span>
          <i style={{ background: 'var(--line-2)' }} />
          Waiting
        </span>
        <span>
          <i style={{ background: 'var(--accent-wash)', boxShadow: 'inset 0 0 0 1px var(--accent)', borderRadius: 3 }} />
          {'W&B artifact or run'}
        </span>
      </div>
    </>
  );
}
