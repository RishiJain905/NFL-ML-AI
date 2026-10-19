// Graph lab -> The path benchmark (AE03; mockup: graphlab.js benchmarkView): how fast
// shortest-path algorithms are on the person graph. Not run yet (404 not_built) or a log-log
// chart of the median time by graph size (one line per algorithm, coloured by kind: one pair /
// one source / all pairs, dash patterns within a kind, a legend that highlights a line, a
// tooltip per point on hover and focus) and the table of every run.

import { useState } from 'react';
import { createPortal } from 'react-dom';
import { useBenchmark } from '../../../api/client';
import type { BenchmarkResponse, BenchmarkRow } from '../../../api/types';
import { BENCH_DASH, BENCH_FAMILY, algName, int, ms, msTick, when } from '../../../lib/graphlab';
import { LabError, LabLoading } from './LabCommon';

const famOf = (f: string) => BENCH_FAMILY[f] ?? { label: f, help: '', colour: '--ink-3' };

function Intro({ b }: { b?: BenchmarkResponse }) {
  const samples = (b?.sizes ?? []).filter((x) => x.requested != null).map((x) => int(x.n));
  const full = (b?.sizes ?? []).find((x) => x.requested == null);
  const where = b
    ? `on ${samples.length ? `samples of ${samples.join(', ')} people and ` : ''}the whole graph${full ? ` (${int(full.n)} people, ${int(full.m)} links)` : ''}`
    : 'on samples of the graph and the whole of it';
  return (
    <p className="gl-intro">
      <b>How fast shortest-path algorithms really are on our person graph</b> (players and coaches
      joined by their links), {where}. Connections asks the one-pair question, which a two-way
      breadth-first search answers in milliseconds; the all-pairs algorithms grow with the cube of
      the size, which is why the 2026 sub-cubic APSP result (Alman &amp; Vassilevska Williams) is a
      landmark for theory but saves nothing here. The history-graph guide explains both.
    </p>
  );
}

export function BenchmarkView() {
  const bench = useBenchmark();
  return (
    <>
      <div className="gl-head">
        <h2>The path benchmark</h2>
        {bench.data ? <BenchMeta b={bench.data} /> : null}
      </div>
      <Intro b={bench.data} />
      {bench.error ? (
        <LabError error={bench.error} what="benchmark" />
      ) : !bench.data ? (
        <LabLoading what="the benchmark" />
      ) : (
        <BenchBody b={bench.data} />
      )}
    </>
  );
}

function BenchMeta({ b }: { b: BenchmarkResponse }) {
  const m = b.machine ?? {};
  const machine = [
    m.cpu_count != null ? `${m.cpu_count} CPUs` : null,
    m.ram_gb != null ? `${m.ram_gb} GB RAM` : null,
    m.neo4j != null ? `Neo4j ${m.neo4j}` : null,
    m.gds != null ? `GDS ${m.gds}` : null,
  ]
    .filter(Boolean)
    .join(', ');
  const ran = b.ran_at ?? b.started_at ?? null;
  return (
    <div className="gl-asof">
      {ran ? <span className="chip flat">Ran {when(ran)}</span> : null}
      {b.smoke ? <span className="chip warn">smoke run</span> : null}
      {b.seed != null ? <span>seed {b.seed}</span> : null}
      {b.seconds != null ? <span>{ms(b.seconds * 1000)} in all</span> : null}
      {machine ? <span>{machine}</span> : null}
      {b.wandb_url ? (
        <a href={b.wandb_url} target="_blank" rel="noopener noreferrer">
          W&amp;B run ↗
        </a>
      ) : null}
    </div>
  );
}

function BenchBody({ b }: { b: BenchmarkResponse }) {
  const [hot, setHot] = useState<string | null>(null);
  const algs = [...new Set(b.rows.map((r) => r.algorithm))];
  const famOfAlg: Record<string, string> = {};
  for (const r of b.rows) famOfAlg[r.algorithm] ??= r.family;
  const dash: Record<string, string> = {};
  for (const f of Object.keys(BENCH_FAMILY))
    algs
      .filter((a) => famOfAlg[a] === f)
      .forEach((a, i) => (dash[a] = BENCH_DASH[i % BENCH_DASH.length]));
  for (const a of algs) dash[a] ??= '';
  if (!b.rows.length) return <p className="muted">The benchmark has no rows.</p>;
  const skipped = b.rows.filter((r) => r.median_ms == null).length;
  return (
    <>
      {b.smoke ? (
        <div className="notice" role="note">
          <span className="ic" aria-hidden="true">
            i
          </span>
          <div>
            <b>A quick check run.</b> This is a smoke run ({b.pairs ?? '?'} pairs,{' '}
            {b.sources ?? '?'} sources, {b.repeats ?? '?'} repeat{b.repeats === 1 ? '' : 's'}, an
            all-pairs budget of {b.budget_s ?? '?'} s): the shape is right, the numbers are rough
            until the full run replaces it.
          </div>
        </div>
      ) : null}
      {b.agrees_all === false ? (
        <div className="notice ask-banner err" role="alert">
          <span className="ic" aria-hidden="true">
            !
          </span>
          <div>
            <b>Not every algorithm agreed.</b> At least one returned different path lengths from the
            reference: see “Agrees” below.
          </div>
        </div>
      ) : null}
      <section className="card" aria-labelledby="glBchH">
        <div className="card-h">
          <h2 id="glBchH">Median time by graph size</h2>
          <span className="muted">both axes on a log scale: each grid line up is 10× slower</span>
        </div>
        <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <BenchChart b={b} algs={algs} famOfAlg={famOfAlg} dash={dash} hot={hot} />
          <div className="gl-blegend">
            {Object.entries(BENCH_FAMILY).map(([f, fam]) => {
              const mine = algs.filter((a) => famOfAlg[a] === f);
              if (!mine.length) return null;
              return (
                <div key={f}>
                  <h4>
                    <i style={{ background: `var(${fam.colour})` }} aria-hidden="true" />
                    {fam.label}
                  </h4>
                  <div className="muted" style={{ fontSize: 11.5, marginBottom: 4 }}>
                    {fam.help}
                  </div>
                  {mine.map((a) => (
                    <button
                      key={a}
                      type="button"
                      aria-pressed={hot === a}
                      onClick={() => setHot((h) => (h === a ? null : a))}
                    >
                      <svg width="28" height="8" aria-hidden="true">
                        <line
                          x1="1"
                          y1="4"
                          x2="27"
                          y2="4"
                          style={{
                            stroke: `var(${fam.colour})`,
                            strokeWidth: 2,
                            strokeDasharray: dash[a],
                          }}
                        />
                      </svg>
                      {algName(a)}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      </section>
      <section className="card" aria-labelledby="glBtH">
        <div className="card-h">
          <h2 id="glBtH">Every run</h2>
          <span className="muted">
            “agrees” = the same path lengths as the reference
            {skipped ? `; ${skipped} skipped (predicted past the time budget)` : ''}
          </span>
        </div>
        <div className="gl-tablewrap" tabIndex={0} aria-label="Benchmark results">
          <table className="tbl gl-tbl">
            <thead>
              <tr>
                {[
                  'Algorithm',
                  'Kind',
                  'People',
                  'Links',
                  'Runs',
                  'Median',
                  'p90',
                  'Fastest',
                  'Agrees',
                  'Note',
                ].map((h, i) => (
                  <th
                    key={h}
                    scope="col"
                    className={i >= 2 && i <= 7 ? 'r' : undefined}
                    style={{ padding: '9px 10px' }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r, i) => (
                <tr key={i}>
                  <td className="who" title={r.algorithm}>
                    {algName(r.algorithm)}
                  </td>
                  <td>{famOf(r.family).label}</td>
                  <td className="r num">{int(r.size_n)}</td>
                  <td className="r num">{int(r.size_m)}</td>
                  <td className="r num">{r.runs}</td>
                  <td className="r num">
                    {r.median_ms == null ? (
                      <span className="muted">skipped</span>
                    ) : (
                      <b>{ms(r.median_ms)}</b>
                    )}
                  </td>
                  <td className="r num">{ms(r.p90_ms)}</td>
                  <td className="r num">{ms(r.min_ms)}</td>
                  <td>
                    {r.agrees == null ? (
                      <span className="muted">—</span>
                    ) : r.agrees ? (
                      <span className="gl-agree ok">✓ yes</span>
                    ) : (
                      <span className="gl-agree no">✕ no</span>
                    )}
                  </td>
                  <td className="muted note">{r.note ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

const W = 760;
const H = 380;
const L = 64;
const R = 18;
const T = 16;
const B = 46;

function BenchChart({
  b,
  algs,
  famOfAlg,
  dash,
  hot,
}: {
  b: BenchmarkResponse;
  algs: string[];
  famOfAlg: Record<string, string>;
  dash: Record<string, string>;
  hot: string | null;
}) {
  const [tip, setTip] = useState<{ x: number; y: number; r: BenchmarkRow } | null>(null);
  const rows = b.rows.filter(
    (r): r is BenchmarkRow & { median_ms: number } =>
      r.median_ms != null && r.median_ms > 0 && r.size_n > 0,
  );
  if (!rows.length) return null;
  const xs = rows.map((r) => r.size_n);
  const ys = rows.map((r) => r.median_ms);
  const lx0 = Math.log10(Math.min(...xs) * 0.85);
  const lx1 = Math.log10(Math.max(...xs) * 1.15);
  const ly0 = Math.floor(Math.log10(Math.min(...ys)));
  const ly1 = Math.max(ly0 + 1, Math.ceil(Math.log10(Math.max(...ys))));
  const X = (n: number) => L + ((Math.log10(n) - lx0) / (lx1 - lx0)) * (W - L - R);
  const Y = (v: number) => T + (1 - (Math.log10(v) - ly0) / (ly1 - ly0)) * (H - T - B);
  const yt: number[] = [];
  for (let e = ly0; e <= ly1; e++) yt.push(10 ** e);
  const xt = [...new Set(b.sizes.length ? b.sizes.map((s) => s.n) : xs)];
  const tipLines = (r: BenchmarkRow) => [
    algName(r.algorithm),
    `${famOf(r.family).label} · ${int(r.size_n)} people, ${int(r.size_m)} links`,
    `Median ${ms(r.median_ms)} · p90 ${ms(r.p90_ms)} · fastest ${ms(r.min_ms)}`,
    `${r.runs} runs${r.agrees === false ? ' · DISAGREES with Cypher' : ''}`,
  ];
  return (
    <div className="gl-chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="group"
        aria-label="Median milliseconds by number of people, log-log, one line per algorithm"
      >
        <g className="grid">
          {yt.map((v) => (
            <line key={`y${v}`} x1={L} x2={W - R} y1={Y(v)} y2={Y(v)} />
          ))}
          {xt.map((n) => (
            <line key={`x${n}`} y1={T} y2={H - B} x1={X(n)} x2={X(n)} />
          ))}
        </g>
        {yt.map((v) => (
          <text key={`yt${v}`} x={L - 8} y={Y(v) + 4} textAnchor="end">
            {msTick(v)}
          </text>
        ))}
        {xt.map((n) => (
          <text key={`xt${n}`} x={X(n)} y={H - B + 16} textAnchor="middle">
            {int(n)}
          </text>
        ))}
        <text className="axis-t" x={(L + W - R) / 2} y={H - 8} textAnchor="middle">
          people in the graph (log scale)
        </text>
        <text
          className="axis-t"
          transform={`translate(14 ${(T + H - B) / 2}) rotate(-90)`}
          textAnchor="middle"
        >
          median time (log scale)
        </text>
        {algs.map((a) => {
          const rs = rows.filter((r) => r.algorithm === a).sort((x, y) => x.size_n - y.size_n);
          const colour = `var(${famOf(famOfAlg[a]).colour})`;
          const d = rs
            .map((r, i) => `${i ? 'L' : 'M'}${X(r.size_n).toFixed(1)},${Y(r.median_ms).toFixed(1)}`)
            .join('');
          return (
            <g key={a} className={hot && hot !== a ? 'dim' : undefined}>
              <path className="ln" d={d} style={{ stroke: colour, strokeDasharray: dash[a] }} />
              {rs.map((r) => (
                <g key={r.size_n}>
                  <circle
                    className="pt"
                    cx={X(r.size_n)}
                    cy={Y(r.median_ms)}
                    r={4}
                    style={{ fill: colour }}
                  />
                  <circle
                    className="hit"
                    cx={X(r.size_n)}
                    cy={Y(r.median_ms)}
                    r={11}
                    tabIndex={0}
                    role="img"
                    aria-label={`${algName(a)}, ${int(r.size_n)} people: median ${ms(r.median_ms)}`}
                    onPointerMove={(e) => setTip({ x: e.clientX, y: e.clientY, r })}
                    onPointerLeave={() => setTip(null)}
                    onFocus={(e) => {
                      const box = e.currentTarget.getBoundingClientRect();
                      setTip({ x: box.left + box.width / 2, y: box.bottom, r });
                    }}
                    onBlur={() => setTip(null)}
                  />
                </g>
              ))}
            </g>
          );
        })}
      </svg>
      {tip
        ? createPortal(
            <span
              role="tooltip"
              className="charttip"
              style={{
                position: 'fixed',
                left: Math.min(tip.x + 14, window.innerWidth - 280),
                top: tip.y + 14,
              }}
            >
              {tipLines(tip.r).map((l, i) => (
                <div key={i} className={i === 0 ? 'th' : undefined}>
                  {l}
                </div>
              ))}
            </span>,
            document.body,
          )
        : null}
    </div>
  );
}
