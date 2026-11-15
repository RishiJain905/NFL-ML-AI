// The Outcomes week tab mockup (FL02, D158). Plain JavaScript over the real answers in
// payloads.json (dump_payloads.py). The React port (web/src/pages/week/OutcomesTab.tsx and
// web/src/lib/forecastlab.ts) mirrors these functions and this markup.
/* eslint-disable */
(function () {
  'use strict';
  const D = JSON.parse(document.getElementById('data').textContent);
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  // ---- the curve's arithmetic (web/src/lib/forecastlab.ts) -------------------------------------
  const BELL_Z = 2.5631031310892007; // P10-P90 of a normal spans 2.563 sigma (curves.py)
  /** P(Y <= k) on whole numbers; past the list's end it's 1. */
  function cdfAt(c, k) {
    k = Math.floor(k);
    if (k < c.grid_lo) return 0;
    const i = k - c.grid_lo;
    return i >= c.cdf.length ? 1 : c.cdf[i];
  }
  function erf(x) { // Abramowitz-Stegun 7.1.26 (1.5e-7)
    const s = Math.sign(x); x = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * x);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return s * y;
  }
  const phi = (z) => 0.5 * (1 + erf(z / Math.SQRT2));
  /** The simple curve (curves.py `bell_cdf`): amounts a normal on P50 through P10 / P90;
   *  counts a Poisson at the mean. */
  function simpleAt(r, lo, k) {
    k = Math.floor(k);
    if (k < lo) return 0;
    if (r.kind === 'count') {
      const mu = Math.max(r.mean ?? 0, 0);
      let term = Math.exp(-mu), sum = term;
      for (let j = 1; j <= Math.max(k, 0); j++) { term *= mu / j; sum += term; }
      return Math.min(1, sum);
    }
    const sigma = Math.max(((r.p90 ?? 0) - (r.p10 ?? 0)) / BELL_Z, 1e-6);
    return phi((k + 0.5 - (r.p50 ?? 0)) / sigma);
  }
  const roundHalfUp = (x) => Math.floor(x + 0.5);
  const centreOf = (r) => roundHalfUp((r.kind === 'count' ? r.mean : r.p50) ?? 0);
  /** "more than x" (amounts) / "x or more" (counts): P(Y > x) or P(Y >= x). */
  const chanceAbove = (F, kind, x) => (kind === 'count' ? 1 - F(Math.ceil(x) - 1) : 1 - F(Math.floor(x)));
  const chanceBetween = (F, a, b) => Math.max(0, F(Math.floor(b)) - F(Math.ceil(a) - 1));

  // ---- words ---------------------------------------------------------------------------------
  const pct = (p) => (p == null ? '—' : p < 0.005 ? '<1%' : p > 0.995 ? '>99%' : `${Math.round(p * 100)}%`);
  function oneIn(p) {
    if (p == null || p <= 0) return '';
    if (p < 0.005) return 'less than 1 in 100';
    if (p < 0.45) return `about 1 in ${Math.round(1 / p)}`;
    if (p > 0.95) return 'almost always';
    return `about ${Math.round(p * 10)} in 10`;
  }
  const num = (x, d = 0) => (x == null ? '—' : Number(x).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: 0 }));
  const unit = (t) => t.label; // "receiving yards", "carries"
  const unitShort = (t) => (t.kind === 'count' ? t.label : 'yards');
  /** A standard mark in words: "more than 49.5" / "5 or more". */
  const markWords = (t, m) => (t.kind === 'count' ? `${Math.ceil(m)} or more` : `more than ${m}`);
  const projOf = (r) => (r.kind === 'count' ? r.mean : r.p50);
  const projText = (r) => (r.kind === 'count' ? num(r.mean, 1) : num(r.p50));

  // ---- state -----------------------------------------------------------------------------------
  const STAT_ORDER = ['QB', 'RB', 'WR/TE', 'LB/S'];
  const anyTarget = D.outcomes[Object.keys(D.outcomes)[0]];
  const S = {
    state: 'ok',
    target: 'rec_yds-wrte',
    player: null, // player_id
    q: '',
    above: {}, // per target: the number typed
    bandMode: 'within',
    bandN: {},
    between: {},
    markIx: {},
    sbView: 'week',
  };
  const answer = () => D.outcomes[S.target];
  const target = () => anyTarget.targets.find((t) => t.target_key === S.target);
  const curveOf = (pid) => D.curves[`${pid}/${S.target}`] || null;

  // synthetic actuals (graded state): a quantile picked from a hash of the player id
  function hash01(s) { let h = 2166136261; for (const ch of s) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); } return ((h >>> 0) % 10000) / 10000; }
  function syntheticActual(r) {
    const u = hash01(r.player_id + r.target_key);
    const qs = [[0.05, r.q05], [0.1, r.q10], [0.25, r.q25], [0.5, r.q50], [0.75, r.q75], [0.9, r.q90], [0.95, r.q95]];
    let lo = qs[0], hi = qs[qs.length - 1];
    for (let i = 0; i < qs.length - 1; i++) if (u >= qs[i][0] && u <= qs[i + 1][0]) { lo = qs[i]; hi = qs[i + 1]; }
    const v = lo[1] + ((u - lo[0]) / Math.max(hi[0] - lo[0], 1e-6)) * (hi[1] - lo[1]);
    return Math.max(0, Math.round(v));
  }
  function withGrades(a) {
    if (S.state !== 'graded') return a;
    const rows = a.rows.map((r, i) => ({ ...r, played: i % 17 !== 5, actual: i % 17 !== 5 ? syntheticActual(r) : null }));
    const sb = (k, n, c, b, o) => ({ target_key: k, n, crps_model: c, crps_bell: b, crps_own: o, brier_model: 0.18, brier_bell: 0.19, brier_own: 0.205, band_pred: 0.12, band_hit: 0.11, pit_ece: 0.018 });
    const week = [sb('pass_yds-qb', 26, 47.9, 48.0, 52.3), sb('rec_yds-wrte', 236, 15.2, 15.9, 16.8), sb('carries-rb', 84, 2.21, 2.6, 2.44), sb('tackles-lbs', 285, 1.31, 1.42, 1.39)];
    return { ...a, rows, graded: true, scoreboard: { week, season: week.map((x) => ({ ...x, n: x.n * 1, weeks: 1 })) } };
  }

  // ---- shell -----------------------------------------------------------------------------------
  const TABS = ['Pipeline', 'Digest', 'Games', 'Players', 'Results', 'MLOps', 'Graph', 'Game day', 'Play calls', 'Availability', 'Outcomes'];
  $('#tabs').innerHTML = TABS.map((t) => `<a class="tab" href="#" ${t === 'Outcomes' ? 'aria-current="page" aria-selected="true"' : ''}>${t}</a>`).join('');
  function seg(id, key, apply) {
    const el = $(id);
    const sync = () => el.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === key())));
    el.addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; apply(b.dataset.v); sync(); render(); });
    sync();
  }
  const root = document.documentElement;
  // the viewer's theme first (an explicit choice, else the system's); the strip switches it
  root.dataset.pal = 'turf';
  root.dataset.mode = root.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  seg('#palPick', () => root.dataset.pal, (v) => (root.dataset.pal = v));
  seg('#modePick', () => root.dataset.mode, (v) => (root.dataset.mode = v));
  seg('#statePick', () => S.state, (v) => (S.state = v));

  // ---- pieces ----------------------------------------------------------------------------------
  function strip(a) {
    const m = a.meta;
    const items = [`Made <b>${esc(fmtET(m.made_at))}</b>`, `curves model <span class="mono">${esc(m.version)}</span>`, `built on player model <span class="mono">${esc(m.source_version)}</span>`];
    if (m.games_after_kickoff.length) items.push(`${m.games_after_kickoff.length} game${m.games_after_kickoff.length === 1 ? '' : 's'} made after kickoff`);
    return `<div class="flstrip" role="note" aria-label="When this was made">${items.map((x) => `<span>${x}</span>`).join('')}</div>`;
  }
  function fmtET(iso) {
    const d = new Date(iso);
    const day = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric' }).format(d);
    const t = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).format(d);
    return `${day}, ${t} ET`;
  }
  function explainer(t) {
    return `<div class="notice accent" role="note"><span class="ic" aria-hidden="true">i</span><div>
      <b>Each curve is the whole spread behind a projection, if he plays.</b> Type any number or band below and read its chance.
      Ranges are wide because football is: a receiver lands within 7 yards of his projection about 1 game in 9. That's the sport, not the model.
      Next to each chance is <b>the simple curve</b>: what you'd work out by hand from the projection (a bell curve through its 10th and 90th percentiles; for counts, a plain Poisson).
    </div></div>`;
  }
  function statPicker(a) {
    const groups = STAT_ORDER.map((g) => {
      const ts = a.targets.filter((t) => t.group === g);
      if (!ts.length) return '';
      return `<span class="grp"><span class="gl">${esc(g)}</span>${ts.map((t) => {
        const have = Boolean(D.outcomes[t.target_key]);
        return `<button type="button" class="oc-stat" data-t="${t.target_key}" aria-pressed="${t.target_key === S.target}" ${have ? '' : 'disabled title="Not in the mockup\'s data (the app has every stat)"'}>${esc(t.label)}</button>`;
      }).join('')}</span>`;
    }).join('');
    return `<div class="oc-stats" role="group" aria-label="Stat">${groups}</div>`;
  }

  function panel(a, t, row) {
    if (!row) return `<section class="card oc-panel"><div class="oc-empty">Pick a player in the list to see his curve.</div></section>`;
    const c = curveOf(row.player_id);
    const head = `<div class="card-h"><h2>${esc(row.player)}</h2><span class="muted">${esc(row.position)} · ${esc(row.team)} vs ${esc(row.opponent)} · ${esc(unit(t))}</span></div>`;
    const proj = `<div class="oc-proj">Projection <b>${projText(row)}</b> ${esc(unitShort(t))} · 80% range ${num(row.p10)}–${num(row.p90)} (the player model's) · if he plays</div>`;
    if (!c) return `<section class="card oc-panel">${head}<div class="card-b">${proj}<div class="oc-empty">The mockup's data has this player's row but not his whole curve (the app fetches it on click).</div></div></section>`;
    const F = (k) => cdfAt(c, k);
    const Fs = (k) => simpleAt(row, c.grid_lo, k);
    const ctr = centreOf(row);
    // the number box
    // the standard mark nearest his projection (web/src/lib/forecastlab.ts defaultAbove)
    const near = t.marks.reduce((b, m) => (Math.abs(m - projOf(row)) < Math.abs(b - projOf(row)) ? m : b), t.marks[0]);
    const defAbove = t.kind === 'count' ? Math.ceil(near) : near;
    const above = S.above[S.target] ?? defAbove;
    const okAbove = Number.isFinite(above);
    const pA = okAbove ? chanceAbove(F, t.kind, above) : null;
    const sA = okAbove ? chanceAbove(Fs, t.kind, above) : null;
    // the band box
    const N = S.bandN[S.target] ?? t.band;
    const bt = S.between[S.target] ?? [ctr - t.band, ctr + t.band];
    const [bl, bh] = S.bandMode === 'within' ? [ctr - N, ctr + N] : bt;
    const okBand = Number.isFinite(bl) && Number.isFinite(bh) && bh >= bl;
    const pB = okBand ? chanceBetween(F, bl, bh) : null;
    const sB = okBand ? chanceBetween(Fs, bl, bh) : null;
    const vs = (p, s) => `<span class="vs${Math.abs(p - s) >= 0.05 ? ' far' : ''}">the simple curve: ${pct(s)}</span>`;
    const u = unitShort(t);
    const ask1 = `<div class="oc-ask"><label class="q" for="ocAbove">${t.kind === 'count' ? 'Chance of' : 'Chance of more than'} <input id="ocAbove" type="number" inputmode="decimal" step="${t.kind === 'count' ? 1 : 0.5}" value="${okAbove ? above : ''}"> ${t.kind === 'count' ? `${esc(u)} or more` : esc(u)}</label>
      ${okAbove ? `<div class="big" aria-live="polite">${pct(pA)}<small>${oneIn(pA)}</small></div>${vs(pA, sA)}` : '<span class="bad">Type a number.</span>'}</div>`;
    const bandInputs = S.bandMode === 'within'
      ? `<label class="q" for="ocN">Within ± <input id="ocN" type="number" min="0" step="1" value="${N}"> ${esc(u)} of ${ctr}</label>`
      : `<span class="q"><label for="ocLo">Between</label> <input id="ocLo" type="number" step="1" value="${bt[0]}"> <label for="ocHi">and</label> <input id="ocHi" type="number" step="1" value="${bt[1]}"> ${esc(u)}</span>`;
    const ask2 = `<div class="oc-ask"><span class="seg" role="group" aria-label="Band"><button type="button" data-b="within" aria-pressed="${S.bandMode === 'within'}">Within ±</button><button type="button" data-b="between" aria-pressed="${S.bandMode === 'between'}">Between</button></span>
      ${bandInputs}
      ${okBand ? `<div class="big" aria-live="polite">${pct(pB)}<small>${oneIn(pB)}</small></div><span class="vs">${Math.max(bl, c.grid_lo) < 0 ? `−${Math.abs(Math.max(bl, c.grid_lo))} to ${bh}` : `${Math.max(bl, c.grid_lo)}–${bh}`} ${esc(u)} · the simple curve: ${pct(sB)}</span>` : '<span class="bad">Type two numbers, the smaller first.</span>'}</div>`;
    const facts = [];
    if (t.kind === 'amount' && F(0) >= 0.005) facts.push(`No ${esc(u)} at all (0 or fewer): <b>${pct(F(0))}</b>`);
    facts.push(`Half of games like this land between <b>${num(row.q25)}</b> and <b>${num(row.q75)}</b>; 8 in 10 between <b>${num(row.q10)}</b> and <b>${num(row.q90)}</b>`);
    if (row.n_analogs) facts.push(`Built from ${num(row.n_analogs)} past projections like this one`);
    if (row.played === false) facts.push("He didn't play: a curve is \"if he plays\", so this one isn't graded");
    else if (row.actual != null) {
      const inBand = Math.abs(row.actual - ctr) <= t.band;
      facts.push(`He had <b>${num(row.actual)}</b> ${esc(u)}: the curve gave ${pct(chanceAbove(F, 'count', row.actual))} to ${num(row.actual)} or more · ${inBand ? 'inside' : 'outside'} ±${t.band} of the projection`);
    }
    return `<section class="card oc-panel" aria-label="${esc(row.player)}'s curve">${head}<div class="card-b">${proj}<div class="oc-asks">${ask1}${ask2}</div>
      ${chart(t, row, c, F, Fs, okAbove ? above : null, okBand ? [bl, bh] : null)}
      <ul class="oc-facts">${facts.map((f) => `<li>${f}</li>`).join('')}</ul></div></section>`;
  }

  // ---- the chart: chance of more than X (amounts) / chance of exactly X (counts) ----------------
  const W = 600, H = 230, ML = 40, MR = 12, MT = 14, MB = 30;
  function domain(c, F, kind) {
    let lo = c.grid_lo, hi = c.grid_lo + c.cdf.length - 1;
    while (lo < hi && F(lo) < 0.004) lo++;
    while (hi > lo && F(hi - 1) > 0.996) hi--;
    if (kind === 'amount' && c.grid_lo <= 0 && lo > 0 && (F(0) >= 0.004 || lo < 10)) lo = 0; // yardage starts at 0
    return [lo, hi];
  }
  function ticks(lo, hi, n = 5) {
    const step0 = (hi - lo) / n, p = Math.pow(10, Math.floor(Math.log10(step0 || 1)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= step0) || p * 10;
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(Math.round(v * 100) / 100);
    return out;
  }
  function chart(t, row, c, F, Fs, above, band) {
    const [lo, hi] = domain(c, F, t.kind);
    const pad = t.kind === 'count' ? 0.5 : 0;
    const x = (k) => ML + ((k - lo + pad) / Math.max(hi - lo + 2 * pad, 1)) * (W - ML - MR);
    // amounts: 0-100% (a chance of more than); counts: up to the tallest bar (a chance of exactly)
    let top = 1;
    if (t.kind === 'count') { let m = 0; for (let k = lo; k <= hi; k++) m = Math.max(m, F(k) - F(k - 1), Fs(k) - Fs(k - 1)); top = Math.min(1, Math.ceil((m * 1.1) / 0.05) * 0.05); }
    const y = (p) => MT + (1 - p / top) * (H - MT - MB);
    const g = [];
    const yt = t.kind === 'count' ? [0, top / 2, top] : [0, 0.25, 0.5, 0.75, 1];
    for (const p of yt) g.push(`<line class="grid" x1="${ML}" x2="${W - MR}" y1="${y(p)}" y2="${y(p)}"/><text x="${ML - 6}" y="${y(p) + 4}" text-anchor="end">${Math.round(p * 100)}%</text>`);
    for (const v of ticks(lo, hi)) g.push(`<text x="${x(v)}" y="${H - 10}" text-anchor="middle">${v}</text>`);
    g.push(`<line class="axis" x1="${ML}" x2="${W - MR}" y1="${y(0)}" y2="${y(0)}"/>`);
    if (band) { const a0 = x(Math.max(band[0], lo) - pad), a1 = x(Math.min(band[1], hi) + pad); if (a1 > a0) g.push(`<rect class="bandz" x="${a0}" y="${MT}" width="${a1 - a0}" height="${H - MT - MB}"/><line class="bandl" x1="${a0}" x2="${a1}" y1="${y(0)}" y2="${y(0)}"/>`); }
    let summary;
    if (t.kind === 'count') {
      const bw = Math.max(2, ((W - ML - MR) / Math.max(hi - lo + 1, 1)) * 0.72);
      for (let k = lo; k <= hi; k++) {
        const p = F(k) - F(k - 1), ps = Fs(k) - Fs(k - 1);
        const on = above != null && k >= Math.ceil(above);
        g.push(`<rect class="bar${on ? ' on' : ''}" x="${x(k) - bw / 2}" y="${y(p)}" width="${bw}" height="${y(0) - y(p)}"><title>${k}: ${pct(p)} · the simple curve ${pct(ps)}</title></rect>`);
        g.push(`<circle class="sdot" cx="${x(k)}" cy="${y(ps)}" r="2.6"/>`);
      }
      summary = `Bars: the chance of each number of ${t.label}, around ${centreOf(row)}`;
    } else {
      const pts = [], spts = [];
      for (let k = lo; k <= hi; k++) { pts.push(`${x(k).toFixed(1)},${y(1 - F(k)).toFixed(1)}`); spts.push(`${x(k).toFixed(1)},${y(1 - Fs(k)).toFixed(1)}`); }
      g.push(`<polyline class="simple" points="${spts.join(' ')}"/><polyline class="curve" points="${pts.join(' ')}"/>`);
      if (above != null && above >= lo && above <= hi) {
        const p = 1 - F(Math.floor(above));
        g.push(`<line class="mk" x1="${x(above)}" x2="${x(above)}" y1="${MT}" y2="${y(0)}"/><circle class="mkdot" cx="${x(above)}" cy="${y(p)}" r="5"/><text class="lab" x="${x(above) + 7}" y="${y(p) - 7}">${pct(p)}</text>`);
      }
      summary = `The curve: the chance of more than each number of ${t.label}, falling from left to right`;
    }
    const pr = projOf(row);
    if (pr != null && pr >= lo && pr <= hi) g.push(`<line class="proj" x1="${x(pr)}" x2="${x(pr)}" y1="${y(0)}" y2="${y(0) + 6}"/><text x="${x(pr)}" y="${y(0) + 17}" text-anchor="middle" class="lab">proj.</text>`);
    if (row.actual != null) g.push(`<line class="act" x1="${x(row.actual)}" x2="${x(row.actual)}" y1="${MT}" y2="${y(0)}"/><text class="act" x="${x(row.actual) + 4}" y="${MT + 10}">actual ${num(row.actual)}</text>`);
    const legend = t.kind === 'count'
      ? `<span><i class="barl"></i>the curve: chance of exactly that many</span><span><i class="simple"></i>dots: the simple curve</span><span><i class="band"></i>your band</span>`
      : `<span><i></i>the curve: chance of more than</span><span><i class="simple"></i>the simple curve</span><span><i class="band"></i>your band</span>`;
    return `<div class="oc-legend">${legend}${row.actual != null ? '<span><i class="act"></i>what happened</span>' : ''}</div>
      <div class="oc-chartwrap"><svg class="oc-chart" viewBox="0 0 ${W} ${H}" role="img" tabindex="0" aria-label="${esc(summary)}. Arrow keys read it number by number." data-lo="${lo}" data-hi="${hi}">${g.join('')}<line class="hov" id="ocHov" x1="0" x2="0" y1="${MT}" y2="${y(0)}" visibility="hidden"/></svg><div class="oc-tip" id="ocTip" hidden></div></div>`;
  }

  // ---- the list --------------------------------------------------------------------------------
  function list(a, t, rows, sel) {
    const mi = S.markIx[S.target] ?? Math.floor(t.marks.length / 2);
    const mark = t.marks[mi];
    const q = S.q.trim().toLowerCase();
    const shown = rows.filter((r) => !q || `${r.player} ${r.team} ${r.opponent} ${r.position}`.toLowerCase().includes(q));
    const span = rows.reduce((m, r) => Math.max(m, r.q90 ?? 0), 1);
    const bar = (r) => `<span class="oc-range" aria-hidden="true"><span class="ln" style="left:${(Math.max(r.q10, 0) / span) * 100}%;width:${((r.q90 - Math.max(r.q10, 0)) / span) * 100}%"></span><span class="md" style="left:${(projOf(r) / span) * 100}%"></span></span>`;
    const body = shown.map((r) => {
      const m = r.marks.find((x) => x.mark === mark) || {};
      return `<tr class="${r.player_id === sel ? 'sel' : ''}">
        <td class="who"><button type="button" class="oc-pick" data-p="${r.player_id}" aria-pressed="${r.player_id === sel}">${esc(r.player)}</button><small>${esc(r.position)} · ${esc(r.team)} vs ${esc(r.opponent)}</small></td>
        <td class="r num pv">${projText(r)}</td>
        <td class="rg">${bar(r)}</td>
        <td class="r num">${pct(r.p_band)}</td>
        <td class="r num pv">${pct(m.p_over)}<small>${pct(m.bell_p_over)}</small></td>
        ${a.graded ? `<td class="r num">${r.played === false ? '<span class="muted">didn\'t play</span>' : num(r.actual)}</td>` : ''}
      </tr>`;
    }).join('');
    return `<section class="card oc-list" aria-label="Every curve for ${esc(t.label)}">
      <div class="card-h"><h2>${esc(t.group)} ${esc(t.label)}</h2><span class="muted">${t.n} players · sorted by projection</span>
        <div class="right oc-tools"><input type="search" id="ocQ" placeholder="Find a player or team" aria-label="Find a player or team" value="${esc(S.q)}">
        <label>Chance of <select id="ocMark">${t.marks.map((m, i) => `<option value="${i}" ${i === mi ? 'selected' : ''}>${markWords(t, m)}</option>`).join('')}</select></label></div></div>
      <div class="tablewrap"><table class="tbl oc-tbl"><thead><tr><th>Player</th><th class="r">Projection</th><th class="rg">80% range (the curve's)</th><th class="r">Within ±${t.band}</th><th class="r">${esc(markWords(t, mark))}<br><small class="muted">curve · simple</small></th>${a.graded ? '<th class="r">Actual</th>' : ''}</tr></thead>
      <tbody>${body || `<tr><td colspan="6" class="muted">No player matches “${esc(S.q)}”.</td></tr>`}</tbody></table></div>
      ${rows.length < t.n ? `<div class="card-b muted" style="font-size:12px">Mockup: the first ${rows.length} of ${t.n} rows (the app lists them all).</div>` : ''}
    </section>`;
  }

  function gapWords(r) {
    const t = anyTarget.targets.find((x) => x.target_key === r.target_key);
    const g = r.gap;
    return { t, text: `<b>${esc(r.player)}</b> <span class="muted">${esc(r.team)} · ${esc(t.group)} ${esc(t.label)}, projected ${projText(r)}</span>: chance of ${esc(markWords(t, g.mark))}`, two: `curve ${pct(g.p_over)} · simple ${pct(g.bell_p_over)}` };
  }
  function gaps(a) {
    if (!a.gaps.length) return '';
    return `<section class="card"><div class="card-h"><h2>Where the curve and the simple curve disagree most</h2><span class="muted">two per stat · the curve learned from past projections like these; the simple curve is the hand method</span></div>
      <div class="card-b"><ul class="oc-gaps">${a.gaps.map((r) => { const w = gapWords(r); const have = Boolean(D.outcomes[r.target_key]);
        return `<li><span>${w.text}${have ? `<button type="button" class="oc-show" data-t="${r.target_key}" data-p="${r.player_id}">Show</button>` : ''}</span><span class="two">${w.two}</span></li>`; }).join('')}</ul></div></section>`;
  }

  function scoreboard(a) {
    if (!a.graded) return '';
    const rows = a.scoreboard[S.sbView];
    // the lowest in bold only when it's clearly lower (2% under the next): a 0.1-yard gap looks like nothing
    const clear = (r, ks) => { const v = ks.map((k) => r[k]).filter((x) => x != null).sort((p, q) => p - q); return v.length > 1 && v[0] <= v[1] * 0.98 ? v[0] : null; };
    const fx = (x, d) => (x == null ? '—' : Number(x).toFixed(d));
    const cell = (r, k, ks, d) => `<td class="r num${r[k] === clear(r, ks) ? ' best' : ''}">${fx(r[k], d)}</td>`;
    const C = ['crps_model', 'crps_bell', 'crps_own'], B = ['brier_model', 'brier_bell', 'brier_own'];
    return `<section class="card oc-score"><div class="card-h"><h2>How the curves did</h2><span class="muted">lower is better · in bold when one is clearly the lowest</span>
      <div class="right"><span class="seg" role="group" aria-label="Period"><button type="button" data-sb="week" aria-pressed="${S.sbView === 'week'}">This week</button><button type="button" data-sb="season" aria-pressed="${S.sbView === 'season'}">Season to date</button></span></div></div>
      <div class="tablewrap"><table class="tbl"><thead><tr><th>Stat</th><th class="r">Players</th><th class="r" colspan="3">Whole-curve miss (CRPS)<br><small class="muted">curve · simple · own games</small></th><th class="r" colspan="3">Threshold chances (Brier)<br><small class="muted">curve · simple · own games</small></th><th class="r">Band: said · happened</th></tr></thead>
      <tbody>${rows.map((r) => { const t = anyTarget.targets.find((x) => x.target_key === r.target_key);
        return `<tr><td>${esc(t.group)} ${esc(t.label)}</td><td class="r num">${r.n}</td>${C.map((k) => cell(r, k, C, 1)).join('')}${B.map((k) => cell(r, k, B, 3)).join('')}<td class="r num">${pct(r.band_pred)} · ${pct(r.band_hit)}</td></tr>`; }).join('')}</tbody></table></div>
      <div class="card-b note">"Own games" is the player's own last 16 games as the curve. A difference under a few hundredths is noise: passing yards, for one, is practically the simple curve.</div></section>`;
  }

  // ---- render ----------------------------------------------------------------------------------
  function render() {
    const v = $('#view');
    if (S.state === 'loading') { v.innerHTML = `<div class="card"><div class="card-b muted" role="status">Loading…</div></div>`; return; }
    if (S.state === 'none' || S.state === 'no_model') {
      const st = S.state === 'none' ? D.states.outcomes_none : { hint: "This model hasn't been promoted yet: its backtest decides what ships (`nfl forecastlab backtest --model curves --promote`)." };
      const cmd = /`([^`]+)`/.exec(st.hint || '')?.[1];
      const text = (st.hint || '').replace(/\s*\(?`[^`]+`\)?/g, '');
      v.innerHTML = S.state === 'none'
        ? `<div class="card"><div class="empty"><div class="glyph">OC</div><h3>Week 11's outcome curves aren't made yet</h3><p>${esc(text)}</p><a class="btn sm" href="#">See week 10's Outcomes</a></div></div>`
        : `<div class="card"><div class="empty"><div class="glyph">OC</div><h3>No outcome-curves model yet</h3><p>This tab shows the whole curve behind each player projection: the chance he clears any number, or lands in a band you pick, next to the simple curve's. ${esc(text)}</p>${cmd ? `<div class="cmd">${esc(cmd)}</div>` : ''}</div></div>`;
      return;
    }
    const a = withGrades(answer());
    const t = target();
    // by the projection shown (counts: the mean), biggest first (web/src/lib/forecastlab.ts byProjection)
    const rows = a.rows.map((r, i) => ({ r, i })).sort((x, y) => (projOf(y.r) ?? -1) - (projOf(x.r) ?? -1) || x.i - y.i).map((x) => x.r);
    if (!S.player || !rows.some((r) => r.player_id === S.player)) {
      const f = D.featured.find((x) => x.target_key === S.target);
      S.player = (f && rows.some((r) => r.player_id === f.player_id) ? f.player_id : rows[0]?.player_id) ?? null;
    }
    const row = rows.find((r) => r.player_id === S.player) || null;
    v.innerHTML = `<div class="oc">${strip(a)}<div class="tiles">
        <div class="tile"><span class="k">Curves</span><span class="v">${num(a.tiles.curves)}</span><span class="d">${num(a.tiles.players)} players · ${a.tiles.stats} stats</span></div>
        <div class="tile"><span class="k">This stat</span><span class="v">${t.n}</span><span class="d">${esc(t.group)} ${esc(t.label)}</span></div>
        <div class="tile"><span class="k">Graded</span><span class="v">${a.graded ? 'Yes' : 'Not yet'}</span><span class="d">${a.graded ? 'the actuals are in' : 'after the games'}</span></div></div>
      ${explainer(t)}${statPicker(a)}
      <div class="oc-main">${panel(a, t, row)}${list(a, t, rows, S.player)}</div>
      ${gaps(a)}${scoreboard(a)}</div>`;
    wire(t, row);
  }

  function wire(t, row) {
    const v = $('#view');
    v.querySelectorAll('.oc-stat').forEach((b) => b.addEventListener('click', () => { S.target = b.dataset.t; S.player = null; render(); }));
    v.querySelectorAll('.oc-pick').forEach((b) => b.addEventListener('click', () => { S.player = b.dataset.p; render(); if (matchMedia('(max-width: 1179px)').matches) $('.oc-panel').scrollIntoView({ behavior: 'smooth', block: 'start' }); }));
    v.querySelectorAll('.oc-show').forEach((b) => b.addEventListener('click', () => { S.target = b.dataset.t; S.player = b.dataset.p; render(); $('.oc-panel').scrollIntoView({ behavior: 'smooth' }); }));
    v.querySelectorAll('[data-b]').forEach((b) => b.addEventListener('click', () => { S.bandMode = b.dataset.b; render(); }));
    v.querySelectorAll('[data-sb]').forEach((b) => b.addEventListener('click', () => { S.sbView = b.dataset.sb; render(); }));
    const keep = (id, fn) => { const el = $(id); if (!el) return; el.addEventListener('change', () => { fn(el.value); render(); const again = $(id); again?.focus(); }); };
    keep('#ocAbove', (x) => (S.above[S.target] = x === '' ? NaN : Number(x)));
    keep('#ocN', (x) => (S.bandN[S.target] = x === '' ? NaN : Math.max(0, Math.round(Number(x)))));
    keep('#ocLo', (x) => (S.between[S.target] = [x === '' ? NaN : Number(x), (S.between[S.target] ?? [0, 0])[1]]));
    keep('#ocHi', (x) => (S.between[S.target] = [(S.between[S.target] ?? [0, 0])[0], x === '' ? NaN : Number(x)]));
    const lo = $('#ocLo'); if (lo && !S.between[S.target]) S.between[S.target] = [Number(lo.value), Number($('#ocHi').value)];
    const qEl = $('#ocQ');
    qEl.addEventListener('input', () => { S.q = qEl.value; render(); const again = $('#ocQ'); again.focus(); again.setSelectionRange(S.q.length, S.q.length); });
    $('#ocMark').addEventListener('change', (e) => { S.markIx[S.target] = Number(e.target.value); render(); $('#ocMark').focus(); });
    // the chart's reading: hover or the arrow keys
    const svg = $('.oc-chart'); if (!svg || !row) return;
    const c = curveOf(row.player_id); const F = (k) => cdfAt(c, k); const Fs = (k) => simpleAt(row, c.grid_lo, k);
    const lo2 = Number(svg.dataset.lo), hi2 = Number(svg.dataset.hi); let cur = null;
    const show = (k) => {
      cur = Math.max(lo2, Math.min(hi2, k));
      const pad = t.kind === 'count' ? 0.5 : 0;
      const px = ML + ((cur - lo2 + pad) / Math.max(hi2 - lo2 + 2 * pad, 1)) * (W - ML - MR);
      const hov = $('#ocHov'); hov.setAttribute('x1', px); hov.setAttribute('x2', px); hov.setAttribute('visibility', 'visible');
      const tip = $('#ocTip'); tip.hidden = false;
      const r = svg.getBoundingClientRect(); tip.style.left = `${(px / W) * r.width}px`; tip.style.top = `${(MT / H) * r.height}px`;
      tip.innerHTML = t.kind === 'count'
        ? `${cur} ${esc(t.label)}: <b>${pct(F(cur) - F(cur - 1))}</b> · ${cur} or more: <b>${pct(1 - F(cur - 1))}</b> <span class="muted">(simple ${pct(1 - Fs(cur - 1))})</span>`
        : `more than ${cur} ${esc(unitShort(t))}: <b>${pct(1 - F(cur))}</b> <span class="muted">(simple ${pct(1 - Fs(cur))})</span>`;
    };
    const hide = () => { $('#ocTip').hidden = true; $('#ocHov').setAttribute('visibility', 'hidden'); };
    svg.addEventListener('mousemove', (e) => { const r = svg.getBoundingClientRect(); const sx = ((e.clientX - r.left) / r.width) * W; const pad = t.kind === 'count' ? 0.5 : 0; show(Math.round(lo2 - pad + ((sx - ML) / (W - ML - MR)) * (hi2 - lo2 + 2 * pad))); });
    svg.addEventListener('mouseleave', hide); svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (e) => { const step = e.shiftKey ? 10 : 1; if (e.key === 'ArrowRight') { show((cur ?? Math.round(projOf(row))) + step); e.preventDefault(); } if (e.key === 'ArrowLeft') { show((cur ?? Math.round(projOf(row))) - step); e.preventDefault(); } if (e.key === 'Escape') hide(); });
    svg.addEventListener('focus', () => show(cur ?? Math.round(projOf(row))));
  }
  render();
})();
