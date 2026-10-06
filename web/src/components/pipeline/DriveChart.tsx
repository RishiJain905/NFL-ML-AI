// View 1: the run as a football drive (mockup: driveHTML + driveTick). Each step is a play that
// moves the ball to its yard line, the digest is the touchdown and the run records are the extra
// point. Everything is drawn from the model, so a changed model updates the same elements.

import { duration } from '../../lib/format';
import {
  FH,
  FW,
  FX0,
  FY0,
  MID_Y,
  PX,
  START_YD,
  ballYard,
  glyphOf,
  goalpostColor,
  isFinished,
  isTouchdown,
  playCount,
  posLabel,
  scorebugNow,
  scrimmageLines,
  startYd,
  xAt,
} from './geometry';
import { STEP_DEFS, STEP_INDEX, type PipelineModel, type ViewStep } from './model';

const LOS_BLUE = '#2D7FF9'; // line of scrimmage: where the run is
const FIRST_DOWN_YELLOW = '#F7D417'; // first-down line: where this step ends

const RECORDS = STEP_INDEX.records;

/** "1st & 15 at OWN 20", "1st & Goal at OPP 15", or "PAT" for the extra point. */
function downAndDistance(i: number): string {
  if (i === RECORDS) return 'PAT';
  const from = startYd(i);
  const yd = STEP_DEFS[i].yd;
  return `1st & ${yd === 100 ? 'Goal' : yd - from} at ${posLabel(from)}`;
}

function labelColor(status: ViewStep['status']): string {
  return status === 'ok' || status === 'degraded'
    ? 'var(--ink)'
    : status === 'running'
      ? 'var(--accent-ink)'
      : status === 'failed'
        ? 'var(--err)'
        : 'var(--ink-3)';
}

function logTime(st: ViewStep): string {
  return st.status === 'pending' || st.status === 'none' ? '—' : st.status === 'skipped' ? 'skipped' : duration(st.seconds);
}

export function DriveChart({ model }: { model: PipelineModel }) {
  const td = isTouchdown(model);
  const yd = ballYard(model);
  const rec = model.steps[RECORDS];
  const gp = goalpostColor(rec.status);
  const lines = model.done ? null : scrimmageLines(model);
  const running = model.idx >= 0 ? model.steps[model.idx] : undefined;
  const runArc = (() => {
    if (!running || model.idx === RECORDS) return null;
    const x1 = xAt(startYd(model.idx));
    const x2 = xAt(yd);
    if (x2 - x1 < 1) return null; // the step has only just started: no play to draw yet
    const h = Math.max(10, (x2 - x1) * 0.5);
    return `M${x1} ${MID_Y} Q${(x1 + x2) / 2} ${MID_Y - h} ${x2} ${MID_Y}`;
  })();
  const now = scorebugNow(model);
  const posts = xAt(105);

  return (
    <>
      <div className="scorebug" style={{ marginBottom: 12 }}>
        <div>
          <span className="k">{model.season}</span>
          <span className="v">WK {model.week}</span>
        </div>
        <div>
          <span className="k">Drive</span>
          <span className="v">
            {playCount(model)} plays · {Math.round(yd - START_YD)} yds
          </span>
        </div>
        <div className="now">
          <span className="k">{now.k}</span>
          <span className="v" title={now.v}>
            {now.v}
          </span>
        </div>
        <div>
          <span className="k">Clock</span>
          <span className="v">{duration(model.elapsed)}</span>
        </div>
      </div>
      <div className="vizwrap">
        <svg
          viewBox={`0 0 1000 ${FY0 + FH + 14}`}
          role="img"
          aria-label="The weekly run drawn as a football drive: each step is a play, the digest is the touchdown"
        >
          {/* step labels above the field */}
          {STEP_DEFS.map((d, i) => {
            if (i === RECORDS) return null;
            const row = i % 2 === 0 ? FY0 - 30 : FY0 - 12;
            return (
              <g key={d.name}>
                <line x1={xAt(d.yd)} x2={xAt(d.yd)} y1={row + 4} y2={FY0} style={{ stroke: 'var(--line-2)' }} strokeDasharray="2 2" />
                <text
                  x={xAt(d.yd) - 3}
                  y={row}
                  textAnchor="end"
                  fontSize={10.5}
                  fontWeight={700}
                  letterSpacing=".05em"
                  style={{ fill: labelColor(model.steps[i].status) }}
                >
                  {d.label.toUpperCase()}
                </text>
              </g>
            );
          })}

          {/* the field */}
          <rect x={FX0} y={FY0} width={FW} height={FH} rx={6} style={{ fill: 'var(--field)' }} />
          {[0, 20, 40, 60, 80].map((y) => (
            <rect key={y} x={xAt(y)} y={FY0} width={10 * PX} height={FH} style={{ fill: 'var(--field-2)' }} />
          ))}
          <rect x={FX0} y={FY0} width={10 * PX} height={FH} style={{ fill: 'var(--field-2)' }} />
          <rect
            x={xAt(100)}
            y={FY0}
            width={10 * PX}
            height={FH}
            style={{ fill: td ? 'var(--accent)' : 'var(--field-2)', opacity: td ? 0.85 : 1 }}
          />
          {Array.from({ length: 21 }, (_, n) => n * 5).map((y) => (
            <line
              key={y}
              x1={xAt(y)}
              x2={xAt(y)}
              y1={FY0}
              y2={FY0 + FH}
              style={{ stroke: 'var(--chalk)' }}
              strokeWidth={y % 10 === 0 ? 1.6 : 0.8}
              opacity={y % 10 === 0 ? 0.85 : 0.45}
            />
          ))}
          {Array.from({ length: 99 }, (_, n) => n + 1)
            .filter((y) => y % 5 !== 0)
            .map((y) => (
              <g key={y} style={{ stroke: 'var(--chalk)' }} opacity={0.5}>
                <line x1={xAt(y)} x2={xAt(y)} y1={FY0 + 88} y2={FY0 + 95} />
                <line x1={xAt(y)} x2={xAt(y)} y1={FY0 + FH - 95} y2={FY0 + FH - 88} />
              </g>
            ))}
          {[10, 20, 30, 40, 50, 60, 70, 80, 90].map((y) => {
            const n = y <= 50 ? y : 100 - y;
            return (
              <g key={y}>
                <text className="field-num" x={xAt(y)} y={FY0 + FH - 22} textAnchor="middle" fontSize={24} style={{ fill: 'var(--chalk)' }}>
                  {n}
                </text>
                <text
                  className="field-num"
                  x={xAt(y)}
                  y={FY0 + 40}
                  textAnchor="middle"
                  fontSize={24}
                  style={{ fill: 'var(--chalk)' }}
                  transform={`rotate(180 ${xAt(y)} ${FY0 + 32})`}
                >
                  {n}
                </text>
              </g>
            );
          })}
          <text
            className="field-num"
            x={FX0 + 5 * PX}
            y={FY0 + FH / 2}
            textAnchor="middle"
            fontSize={22}
            letterSpacing={3}
            style={{ fill: 'var(--chalk)' }}
            transform={`rotate(-90 ${FX0 + 5 * PX} ${FY0 + FH / 2})`}
          >
            DATA
          </text>
          <text
            className="field-num"
            x={posts}
            y={FY0 + FH / 2}
            textAnchor="middle"
            fontSize={22}
            letterSpacing={3}
            style={{ fill: td ? 'var(--on-accent)' : 'var(--chalk)' }}
            transform={`rotate(90 ${posts} ${FY0 + FH / 2})`}
          >
            DIGEST
          </text>

          {/* goal posts: the run records are the extra point */}
          <g data-testid="goalposts" data-status={rec.status} style={{ stroke: gp }} strokeWidth={3} fill="none" strokeLinecap="round">
            <path
              d={`M${posts} ${FY0 - 4} V${FY0 - 16} M${posts - 16} ${FY0 - 16} H${posts + 16} M${posts - 16} ${FY0 - 16} V${FY0 - 44} M${posts + 16} ${FY0 - 16} V${FY0 - 44}`}
            />
          </g>
          <text x={posts - 24} y={FY0 - 26} textAnchor="end" fontSize={10.5} fontWeight={700} letterSpacing=".06em" style={{ fill: 'var(--ink-3)' }}>
            PAT · RECORDS
          </text>

          {/* finished plays are arcs; a failed one is a fumble */}
          {STEP_DEFS.map((d, i) => {
            if (i === RECORDS) return null;
            const st = model.steps[i];
            const from = startYd(i);
            if (isFinished(st.status)) {
              const x1 = xAt(from);
              const x2 = xAt(d.yd);
              const h = Math.max(16, (x2 - x1) * 0.5);
              return (
                <g key={d.name} data-play={d.name}>
                  <path
                    d={`M${x1} ${MID_Y} Q${(x1 + x2) / 2} ${MID_Y - h} ${x2} ${MID_Y}`}
                    fill="none"
                    style={{ stroke: 'var(--chalk)' }}
                    strokeWidth={2.2}
                    strokeLinecap="round"
                  />
                  <circle cx={x2} cy={MID_Y} r={3.5} style={{ fill: 'var(--chalk)' }} />
                </g>
              );
            }
            if (st.status === 'failed') {
              const x = xAt(from + (d.yd - from) * Math.min(1, Math.max(0, st.progress)));
              return (
                <g key={d.name} data-play={d.name}>
                  <g style={{ stroke: 'var(--err)' }} strokeWidth={3} strokeLinecap="round">
                    <path d={`M${x - 9} ${MID_Y + 22} l18 18 M${x + 9} ${MID_Y + 22} l-18 18`} />
                  </g>
                  <text x={x} y={MID_Y + 58} textAnchor="middle" fontSize={12} fontWeight={800} letterSpacing=".1em" style={{ fill: 'var(--err)' }}>
                    FUMBLE
                  </text>
                </g>
              );
            }
            return null;
          })}
          {runArc ? <path data-testid="run-arc" d={runArc} fill="none" style={{ stroke: 'var(--accent)' }} strokeWidth={2.4} strokeDasharray="6 5" /> : null}

          {td ? (
            <text
              className="field-num"
              x={xAt(50)}
              y={MID_Y + 8}
              textAnchor="middle"
              fontSize={40}
              letterSpacing={6}
              style={{ fill: 'var(--chalk)' }}
              opacity={0.9}
            >
              TOUCHDOWN
            </text>
          ) : null}

          {lines ? (
            <>
              <line data-testid="los" x1={xAt(lines.from)} x2={xAt(lines.from)} y1={FY0} y2={FY0 + FH} stroke={LOS_BLUE} strokeWidth={3} opacity={0.95} />
              <line
                data-testid="fdl"
                x1={xAt(lines.target)}
                x2={xAt(lines.target)}
                y1={FY0}
                y2={FY0 + FH}
                stroke={FIRST_DOWN_YELLOW}
                strokeWidth={3}
                opacity={0.95}
              />
            </>
          ) : null}

          <g data-testid="ball" transform={`translate(${xAt(yd)} ${MID_Y}) rotate(-12)`}>
            <ellipse cx={0} cy={0} rx={13} ry={8} style={{ fill: 'var(--ball)' }} stroke="rgba(0,0,0,.35)" />
            <path d="M-6 0 H6 M-3 -2.5 V2.5 M0 -2.5 V2.5 M3 -2.5 V2.5" stroke="#fff" strokeWidth={1.3} strokeLinecap="round" />
          </g>
        </svg>
      </div>
      <div className="legend" style={{ margin: '10px 0 6px' }}>
        <span>
          <i className="line" style={{ background: LOS_BLUE }} />
          Line of scrimmage: where the run is
        </span>
        <span>
          <i className="line" style={{ background: FIRST_DOWN_YELLOW }} />
          First-down line: where this step ends
        </span>
        <span>
          <i className="line" style={{ background: 'var(--ink-2)' }} />
          Arcs: finished steps
        </span>
      </div>
      <ul className="drivelog" aria-label="Drive log">
        {STEP_DEFS.map((d, i) => {
          const st = model.steps[i];
          const cls =
            st.status === 'running'
              ? 'running'
              : st.status === 'failed'
                ? 'failed'
                : st.status === 'pending' || st.status === 'skipped' || st.status === 'none'
                  ? 'pending'
                  : '';
          const g = glyphOf(st.status);
          return (
            <li key={d.name} className={cls} data-row={d.name}>
              <span className="dd">{downAndDistance(i)}</span>
              <span className="pl">{g ? `${g} ${d.label}` : d.label}</span>
              <span className="dt">{st.detail}</span>
              <span className="t">{logTime(st)}</span>
            </li>
          );
        })}
      </ul>
    </>
  );
}
