// The Outcomes tab's curve panel (FL02, D158; mockup: `panel` and `chart` in
// documentation/forecast-lab/mockup/outcomes.js): Rishi's two questions about one player's stat,
// "what's the chance he gets more than X" and "the chance he lands within ± N of his projection"
// (or between A and B), each with the simple curve's chance beside it; the chart; a few facts.
// The whole curve is fetched when a row is picked (GET .../outcomes/curve).

import { useId, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react';
import { useOutcomeCurve } from '../../../api/client';
import type { OutcomeCurve, OutcomeRow, OutcomeTarget } from '../../../api/types';
import { Segmented } from '../../../components/ui';
import { comma } from '../../../lib/format';
import {
  centreOf,
  chanceAbove,
  defaultAbove,
  unitOf,
  chanceBetween,
  chartDomain,
  cdfOf,
  niceTicks,
  oneIn,
  pctText,
  projectionOf,
  simpleCdf,
  spanText,
  type Cdf,
} from '../../../lib/forecastlab';

/** What the reader typed in the two boxes, per stat (strings: a box can be mid-edit). */
export interface Asks {
  above?: string;
  n?: string;
  lo?: string;
  hi?: string;
}
export type BandMode = 'within' | 'between';

const num = (x: number | null | undefined, digits = 0) =>
  x == null ? '—' : x.toLocaleString('en-US', { maximumFractionDigits: digits });
const parse = (s: string | undefined): number | null => {
  if (s == null || s.trim() === '') return null;
  const v = Number(s);
  return Number.isFinite(v) ? v : null;
};

// ---- the chart -------------------------------------------------------------------------------
const W = 600;
const H = 230;
const ML = 40;
const MR = 12;
const MT = 14;
const MB = 30;

function CurveChart({
  t,
  row,
  c,
  F,
  Fs,
  above,
  band,
}: {
  t: OutcomeTarget;
  row: OutcomeRow;
  c: OutcomeCurve;
  F: Cdf;
  Fs: Cdf;
  above: number | null;
  band: [number, number] | null;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const count = t.kind === 'count';
  const [lo, hi] = chartDomain(c, t.kind);
  const pad = count ? 0.5 : 0;
  const x = (k: number) => ML + ((k - lo + pad) / Math.max(hi - lo + 2 * pad, 1)) * (W - ML - MR);
  // yardage: the chance of more than each number (0-100%); counts: the chance of exactly each
  // number, up to the tallest bar
  let top = 1;
  if (count) {
    let m = 0;
    for (let k = lo; k <= hi; k++) m = Math.max(m, F(k) - F(k - 1), Fs(k) - Fs(k - 1));
    top = Math.min(1, Math.max(0.05, Math.ceil((m * 1.1) / 0.05) * 0.05));
  }
  const y = (p: number) => MT + (1 - p / top) * (H - MT - MB);
  const yTicks = count ? [0, top / 2, top] : [0, 0.25, 0.5, 0.75, 1];
  const ks = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
  const proj = projectionOf(row);
  const u = unitOf(t);

  const kAt = (clientX: number, el: SVGSVGElement) => {
    const r = el.getBoundingClientRect();
    const sx = ((clientX - r.left) / Math.max(r.width, 1)) * W;
    return Math.round(lo - pad + ((sx - ML) / (W - ML - MR)) * (hi - lo + 2 * pad));
  };
  const clamp = (k: number) => Math.max(lo, Math.min(hi, k));
  const onMove = (e: MouseEvent<SVGSVGElement>) => setHover(clamp(kAt(e.clientX, e.currentTarget)));
  const start = () => clamp(Math.round(proj ?? lo));
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    const step = e.shiftKey ? 10 : 1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      setHover((h) => clamp((h ?? start()) + (e.key === 'ArrowRight' ? step : -step)));
    } else if (e.key === 'Escape') setHover(null);
  };
  const tip =
    hover == null
      ? null
      : count
        ? `${hover} ${t.label}: ${pctText(F(hover) - F(hover - 1))} · ${hover} or more: ${pctText(1 - F(hover - 1))} (simple ${pctText(1 - Fs(hover - 1))})`
        : `more than ${hover} ${u}: ${pctText(1 - F(hover))} (simple ${pctText(1 - Fs(hover))})`;
  const summary = count
    ? `Bars: the chance of each number of ${t.label}, around ${centreOf(row)}`
    : `The curve: the chance of more than each number of ${t.label}, falling from left to right`;

  return (
    <>
      <div className="oc-legend">
        {count ? (
          <>
            <span>
              <i className="barl" />
              the curve: chance of exactly that many
            </span>
            <span>
              <i className="simple" />
              dots: the simple curve
            </span>
          </>
        ) : (
          <>
            <span>
              <i />
              the curve: chance of more than
            </span>
            <span>
              <i className="simple" />
              the simple curve
            </span>
          </>
        )}
        <span>
          <i className="band" />
          your band
        </span>
        {row.actual != null ? (
          <span>
            <i className="act" />
            what happened
          </span>
        ) : null}
      </div>
      <div className="oc-chartwrap">
        <svg
          className="oc-chart"
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          tabIndex={0}
          aria-label={`${summary}. Arrow keys read it number by number.`}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          onFocus={() => setHover((h) => h ?? start())}
          onBlur={() => setHover(null)}
          onKeyDown={onKey}
        >
          {yTicks.map((p) => (
            <g key={`y${p}`}>
              <line className="grid" x1={ML} x2={W - MR} y1={y(p)} y2={y(p)} />
              <text x={ML - 6} y={y(p) + 4} textAnchor="end">
                {Math.round(p * 100)}%
              </text>
            </g>
          ))}
          {niceTicks(lo, hi).map((v) => (
            <text key={`x${v}`} x={x(v)} y={H - 10} textAnchor="middle">
              {v}
            </text>
          ))}
          <line className="axis" x1={ML} x2={W - MR} y1={y(0)} y2={y(0)} />
          {band && x(Math.min(band[1], hi) + pad) > x(Math.max(band[0], lo) - pad) ? (
            <>
              <rect
                className="bandz"
                x={x(Math.max(band[0], lo) - pad)}
                y={MT}
                width={x(Math.min(band[1], hi) + pad) - x(Math.max(band[0], lo) - pad)}
                height={H - MT - MB}
              />
              <line className="bandl" x1={x(Math.max(band[0], lo) - pad)} x2={x(Math.min(band[1], hi) + pad)} y1={y(0)} y2={y(0)} />
            </>
          ) : null}
          {count ? (
            ks.map((k) => {
              const p = F(k) - F(k - 1);
              const ps = Fs(k) - Fs(k - 1);
              const bw = Math.max(2, ((W - ML - MR) / Math.max(hi - lo + 1, 1)) * 0.72);
              return (
                <g key={k}>
                  <rect className={`bar${above != null && k >= Math.ceil(above) ? ' on' : ''}`} x={x(k) - bw / 2} y={y(p)} width={bw} height={Math.max(0, y(0) - y(p))} />
                  <circle className="sdot" cx={x(k)} cy={y(ps)} r={2.6} />
                </g>
              );
            })
          ) : (
            <>
              <polyline className="simple" points={ks.map((k) => `${x(k).toFixed(1)},${y(1 - Fs(k)).toFixed(1)}`).join(' ')} />
              <polyline className="curve" points={ks.map((k) => `${x(k).toFixed(1)},${y(1 - F(k)).toFixed(1)}`).join(' ')} />
              {above != null && above >= lo && above <= hi ? (
                <>
                  <line className="mk" x1={x(above)} x2={x(above)} y1={MT} y2={y(0)} />
                  <circle className="mkdot" cx={x(above)} cy={y(1 - F(Math.floor(above)))} r={5} />
                  <text className="lab" x={x(above) + 7} y={y(1 - F(Math.floor(above))) - 7}>
                    {pctText(1 - F(Math.floor(above)))}
                  </text>
                </>
              ) : null}
            </>
          )}
          {proj != null && proj >= lo && proj <= hi ? (
            <>
              <line className="proj" x1={x(proj)} x2={x(proj)} y1={y(0)} y2={y(0) + 6} />
              <text className="lab" x={x(proj)} y={y(0) + 17} textAnchor="middle">
                proj.
              </text>
            </>
          ) : null}
          {row.actual != null && row.actual >= lo && row.actual <= hi ? (
            <>
              <line className="act" x1={x(row.actual)} x2={x(row.actual)} y1={MT} y2={y(0)} />
              <text className="act" x={x(row.actual) + 4} y={MT + 10}>
                actual {num(row.actual)}
              </text>
            </>
          ) : null}
          {hover != null ? <line className="hov" x1={x(hover)} x2={x(hover)} y1={MT} y2={y(0)} /> : null}
        </svg>
        {tip ? (
          <div className="oc-tip" role="status" style={{ left: `${(x(hover ?? lo) / W) * 100}%`, top: 0 }}>
            {tip}
          </div>
        ) : null}
      </div>
    </>
  );
}

// ---- the panel -------------------------------------------------------------------------------

export function CurvePanel({
  season,
  week,
  t,
  row,
  asks,
  onAsk,
  mode,
  onMode,
}: {
  season: number;
  week: number;
  t: OutcomeTarget;
  row: OutcomeRow | null;
  asks: Asks;
  onAsk: (a: Asks) => void;
  mode: BandMode;
  onMode: (m: BandMode) => void;
}) {
  const ids = useId();
  const q = useOutcomeCurve(season, week, row?.player_id ?? null, row ? t.stat : null);
  if (!row) {
    return (
      <section className="card oc-panel" aria-label="A player's curve">
        <div className="oc-empty">Pick a player in the list to see his curve.</div>
      </section>
    );
  }
  const u = unitOf(t);
  const head = (
    <div className="card-h">
      <h2>{row.player ?? row.player_id}</h2>
      <span className="muted">
        {row.position} · {row.team} vs {row.opponent} · {t.label}
      </span>
    </div>
  );
  const proj = (
    <div className="oc-proj">
      Projection <b>{t.kind === 'count' ? num(row.mean, 1) : num(row.p50)}</b> {u} · 80% range {num(row.p10)}–{num(row.p90)} (the
      player model&apos;s) · if he plays
    </div>
  );
  const c = q.data?.player_id === row.player_id && q.data.stat === t.stat ? q.data : null;
  if (!c) {
    return (
      <section className="card oc-panel" aria-label={`${row.player ?? ''}'s curve`}>
        {head}
        <div className="card-b">
          {proj}
          <div className="oc-empty" role="status">
            {q.isError ? 'This curve could not be loaded.' : 'Loading the curve…'}
          </div>
        </div>
      </section>
    );
  }

  const F = cdfOf(c);
  const Fs = simpleCdf(row, c.grid_lo);
  const count = t.kind === 'count';
  const ctr = centreOf(row);
  // the number box
  const opening = defaultAbove(t, projectionOf(row));
  const above = asks.above != null ? parse(asks.above) : opening;
  const pA = above != null ? chanceAbove(F, t.kind, above) : null;
  const sA = above != null ? chanceAbove(Fs, t.kind, above) : null;
  // the band box: within ± N of the projection, or between A and B
  const nRaw = asks.n != null ? parse(asks.n) : t.band;
  const n = nRaw != null ? Math.max(0, Math.round(nRaw)) : null;
  const a = asks.lo != null ? parse(asks.lo) : ctr - t.band;
  const b = asks.hi != null ? parse(asks.hi) : ctr + t.band;
  const band: [number, number] | null =
    mode === 'within' ? (n != null ? [ctr - n, ctr + n] : null) : a != null && b != null && b >= a ? [a, b] : null;
  const pB = band ? chanceBetween(F, band[0], band[1]) : null;
  const sB = band ? chanceBetween(Fs, band[0], band[1]) : null;
  const vs = (p: number, s: number) => (
    // a difference of 5 points or more reads a shade stronger; never red / green
    <span className={`vs${Math.abs(p - s) >= 0.05 ? ' far' : ''}`}>the simple curve: {pctText(s)}</span>
  );

  const facts: ReactNode[] = [];
  if (!count && F(0) >= 0.005) {
    facts.push(
      <>
        No {u} at all (0 or fewer): <b>{pctText(F(0))}</b>
      </>,
    );
  }
  facts.push(
    <>
      Half of games like this land between <b>{num(row.q25)}</b> and <b>{num(row.q75)}</b>; 8 in 10 between <b>{num(row.q10)}</b> and{' '}
      <b>{num(row.q90)}</b>
    </>,
  );
  if (row.n_analogs) facts.push(<>Built from {comma(row.n_analogs)} past projections like this one</>);
  if (row.played === false) facts.push(<>He didn&apos;t play: a curve is &quot;if he plays&quot;, so this one isn&apos;t graded</>);
  else if (row.actual != null) {
    const inBand = Math.abs(row.actual - ctr) <= t.band;
    facts.push(
      <>
        He had <b>{num(row.actual)}</b> {u}: the curve gave {pctText(chanceAbove(F, 'count', row.actual))} to {num(row.actual)} or more ·{' '}
        {inBand ? 'inside' : 'outside'} ±{t.band} of the projection
      </>,
    );
  }

  return (
    <section className="card oc-panel" aria-label={`${row.player ?? ''}'s curve`}>
      {head}
      <div className="card-b">
        {proj}
        <div className="oc-asks">
          <div className="oc-ask">
            <label className="q" htmlFor={`${ids}-above`}>
              {count ? 'Chance of' : 'Chance of more than'}{' '}
              <input
                id={`${ids}-above`}
                type="number"
                inputMode="decimal"
                step={count ? 1 : 0.5}
                value={asks.above ?? String(opening)}
                onChange={(e) => onAsk({ ...asks, above: e.target.value })}
              />{' '}
              {count ? `${u} or more` : u}
            </label>
            {pA != null && sA != null ? (
              <>
                <div className="big" aria-live="polite">
                  {pctText(pA)}
                  <small>{oneIn(pA)}</small>
                </div>
                {vs(pA, sA)}
              </>
            ) : (
              <span className="bad">Type a number.</span>
            )}
          </div>
          <div className="oc-ask">
            <Segmented<BandMode>
              label="Band"
              options={[
                { value: 'within', label: 'Within ±' },
                { value: 'between', label: 'Between' },
              ]}
              value={mode}
              onChange={onMode}
            />
            {mode === 'within' ? (
              <label className="q" htmlFor={`${ids}-n`}>
                Within ±{' '}
                <input
                  id={`${ids}-n`}
                  type="number"
                  min={0}
                  step={1}
                  value={asks.n ?? String(t.band)}
                  onChange={(e) => onAsk({ ...asks, n: e.target.value })}
                />{' '}
                {u} of {ctr}
              </label>
            ) : (
              <span className="q">
                <label htmlFor={`${ids}-lo`}>Between</label>
                <input
                  id={`${ids}-lo`}
                  type="number"
                  step={1}
                  value={asks.lo ?? String(ctr - t.band)}
                  onChange={(e) => onAsk({ ...asks, lo: e.target.value })}
                />
                <label htmlFor={`${ids}-hi`}>and</label>
                <input
                  id={`${ids}-hi`}
                  type="number"
                  step={1}
                  value={asks.hi ?? String(ctr + t.band)}
                  onChange={(e) => onAsk({ ...asks, hi: e.target.value })}
                />
                {u}
              </span>
            )}
            {band && pB != null && sB != null ? (
              <>
                <div className="big" aria-live="polite">
                  {pctText(pB)}
                  <small>{oneIn(pB)}</small>
                </div>
                <span className={`vs${Math.abs(pB - sB) >= 0.05 ? ' far' : ''}`}>
                  {spanText(Math.max(band[0], c.grid_lo), band[1])} {u} · the simple curve: {pctText(sB)}
                </span>
              </>
            ) : (
              <span className="bad">{mode === 'within' ? 'Type a number.' : 'Type two numbers, the smaller first.'}</span>
            )}
          </div>
        </div>
        <CurveChart t={t} row={row} c={c} F={F} Fs={Fs} above={above} band={band} />
        <ul className="oc-facts">
          {facts.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}
