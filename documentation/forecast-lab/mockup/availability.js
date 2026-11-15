// The Availability week tab mockup (FL02, D158). Plain JavaScript over the real answer in
// payloads_availability.json (dump_payloads.py). The React port (web/src/pages/week/
// AvailabilityTab.tsx, forecastlab/AvailabilityParts.tsx, web/src/lib/forecastlab.ts) mirrors it.
/* eslint-disable */
(function () {
  'use strict';
  const D = JSON.parse(document.getElementById('data').textContent);
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  // ---- words (web/src/lib/forecastlab.ts) ------------------------------------------------------
  const pct = (p) => (p == null ? '—' : p < 0.005 ? '<1%' : p > 0.995 ? '>99%' : `${Math.round(p * 100)}%`);
  /** A change of share in points: "+16 points" (shares are 0-1). */
  const pts = (d) => (d == null ? '—' : `${d >= 0 ? '+' : '−'}${Math.abs(Math.round(d * 100))} point${Math.abs(Math.round(d * 100)) === 1 ? '' : 's'}`);
  const KIND = { snaps: 'of the snaps', targets: 'of the targets', carries: 'of the carries' };
  const STATUS_TONE = { Out: 'err', Doubtful: 'warn', Questionable: 'warn' };
  const PRACTICE = { full: 'full practice', limited: 'limited practice', dnp: 'did not practice' };
  const ROSTER = { RES: 'reserve list', DEV: 'practice squad', INA: 'inactive' };
  /** A share (0-1) as a whole percent: 100% is a real share. */
  const share = (x) => (x == null ? '—' : `${Math.round(x * 100)}%`);
  const where = (r) => `${r.team} ${r.home ? 'vs' : 'at'} ${r.opponent}`;
  function fmtET(iso) {
    const d = new Date(iso);
    const day = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric' }).format(d);
    const t = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).format(d);
    return `${day}, ${t} ET`;
  }
  /** How much playing time is at stake: his usual snap share times the chance he sits. */
  const stake = (r) => (r.base_snap ?? 0) * (1 - (r.p_play ?? r.base_p_play ?? 1));

  // ---- the synthetic states (mockup only) --------------------------------------------------------
  function hash01(s) { let h = 2166136261; for (const ch of s) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); } return ((h >>> 0) % 10000) / 10000; }
  // the model worker's scratch Saturday run gave e.g. A.J. Brown (Questionable, limited) 39% -> 65%,
  // D.J. Reed (Out) 38% -> 0.1%; the rest are made up from the real Tuesday rows
  const SAT = {
    'A.J. Brown': ['Questionable', 'limited', 0.65],
    'D.J. Reed': ['Out', 'dnp', 0.001],
    'Kyler Murray': ['Doubtful', 'dnp', 0.12],
    'Sam Darnold': ['Questionable', 'full', 0.84],
    'Garrett Wilson': ['Out', 'dnp', 0.002],
    'Bobby Okereke': ['Questionable', 'limited', 0.58],
    'Deshaun Watson': ['Questionable', 'full', 0.9],
  };
  function saturday(base) {
    const rows = base.at_risk.map((r) => {
      const s = SAT[r.player];
      if (!s || r.risk_group === 'reserve') return r;
      return { ...r, view: 'saturday', report_status: s[0], practice_status: s[1], p_play_tuesday: r.p_play, p_play: s[2], risk_group: 'report' };
    });
    const order = { report: 0, uncertain: 1, reserve: 2 };
    rows.sort((a, b) => order[a.risk_group] - order[b.risk_group]);
    const by = { report: 0, uncertain: 0, reserve: 0 }; rows.forEach((r) => by[r.risk_group]++);
    const cnt = (s) => rows.filter((r) => r.report_status === s).length;
    return {
      ...base,
      at_risk: rows,
      meta: { ...base.meta, views: { ...base.meta.views, saturday: { made_at: '2026-11-14T15:20:00+00:00', rows: base.tiles.players } } },
      tiles: { ...base.tiles, view: 'saturday', by_group: by, out: cnt('Out') + 31, doubtful: cnt('Doubtful') + 4, questionable: cnt('Questionable') + 52 },
    };
  }
  function graded(base) {
    const play = (r) => (r.played != null ? r : { ...r, played: hash01(r.player_id) < (r.p_play ?? 0.9), actual_snap: null });
    const snap = (r) => (r.played && r.actual_snap == null ? { ...r, actual_snap: Math.min(1, Math.max(0.02, (r.snap_p50 ?? 0.5) + (hash01(r.player_id + 's') - 0.5) * 0.3)) } : r);
    const g = (r) => snap(play(r));
    const row = (view, output, n, bm, bb, mm, mb, cov) => ({ view, output, group: 'ALL', n, brier_model: bm, brier_baseline: bb, mae_model: mm, mae_baseline: mb, coverage_80: cov });
    const week = [row('tuesday', 'play', 1073, 0.101, 0.146, null, null, null), row('tuesday', 'snap', 905, null, null, 0.118, 0.129, 0.81), row('tuesday', 'tgt_share', 240, null, null, 0.046, 0.05, null), row('tuesday', 'carry_share', 96, null, null, 0.088, 0.101, null)];
    return { ...base, graded: true, at_risk: base.at_risk.map(g), scoreboard: { week, season: week.map((x) => ({ ...x, weeks: 1 })) }, _g: g };
  }

  // ---- state -----------------------------------------------------------------------------------
  const S = { state: 'tuesday', team: null, showAll: false };
  const answer = () => (S.state === 'saturday' ? saturday(D.base) : S.state === 'graded' ? graded(D.base) : D.base);

  const TABS = ['Pipeline', 'Digest', 'Games', 'Players', 'Results', 'MLOps', 'Graph', 'Game day', 'Play calls', 'Availability', 'Outcomes'];
  $('#tabs').innerHTML = TABS.map((t) => `<a class="tab" href="#" ${t === 'Availability' ? 'aria-current="page" aria-selected="true"' : ''}>${t}</a>`).join('');
  const root = document.documentElement;
  root.dataset.pal = 'turf';
  root.dataset.mode = root.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  function seg(id, key, apply) {
    const el = $(id);
    const sync = () => el.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === key())));
    el.addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; apply(b.dataset.v); sync(); render(); });
    sync();
  }
  seg('#palPick', () => root.dataset.pal, (v) => (root.dataset.pal = v));
  seg('#modePick', () => root.dataset.mode, (v) => (root.dataset.mode = v));
  seg('#statePick', () => S.state, (v) => (S.state = v));

  // ---- pieces ----------------------------------------------------------------------------------
  function strip(a) {
    const m = a.meta;
    const items = ['tuesday', 'saturday'].filter((v) => m.views[v]).map((v) => `${v === 'tuesday' ? 'Tuesday' : 'Saturday'} view <b>${esc(fmtET(m.views[v].made_at))}</b>`);
    items.push(`showing the ${a.tiles.view === 'saturday' ? 'Saturday' : 'Tuesday'} view`, `model <span class="mono">${esc(m.version)}</span>`, `trained through ${esc(m.trained_through)}`);
    if (m.games_after_kickoff.length) items.push(`${m.games_after_kickoff.length} game${m.games_after_kickoff.length === 1 ? '' : 's'} made after kickoff (${m.games_after_kickoff.map((g) => esc(g.slice(8).replace('_', ' at '))).join(', ')})`);
    return `<div class="flstrip" role="note" aria-label="When this was made">${items.map((x) => `<span>${x}</span>`).join('')}</div>`;
  }
  function tiles(a) {
    const t = a.tiles, sat = t.view === 'saturday';
    return `<div class="tiles">
      <div class="tile"><span class="k">Players</span><span class="v">${t.players.toLocaleString('en-US')}</span><span class="d">${a.teams.length} teams this week</span></div>
      <div class="tile"><span class="k">Might sit</span><span class="v">${t.at_risk}</span><span class="d">${t.by_group.report} on the report · ${t.by_group.uncertain} regulars · ${t.by_group.reserve} reserve</span></div>
      <div class="tile"><span class="k">This week's report</span><span class="v">${sat ? `${t.out}<small>out</small>` : '—'}</span><span class="d">${sat ? `${t.doubtful} doubtful · ${t.questionable} questionable` : 'not out yet on Tuesday'}</span></div>
      <div class="tile"><span class="k">View</span><span class="v">${sat ? 'Saturday' : 'Tuesday'}</span><span class="d">${sat ? 'after the final injury report' : 'before the injury report'}</span></div></div>`;
  }
  function note(a) {
    const sat = a.tiles.view === 'saturday';
    return `<div class="notice accent" role="note"><span class="ic" aria-hidden="true">i</span><div>
      ${sat
        ? "<b>This is Saturday's view:</b> the week's final injury report is in, so the chance he plays is the sharp one. Tuesday's chance shows beside it."
        : "<b>This is Tuesday's view:</b> there's no injury report for the week yet, so the chance he plays leans on last week (who played, who was listed, reserve lists). Saturday's view, after the final report, is the sharp one."}
      Snap, target and carry shares are <b>if he plays</b>. A share's change is in points: 31% to 47% of the carries is +16 points.</div></div>`;
  }
  function chips(r) {
    const c = [];
    if (r.report_status) c.push(`<span class="chip ${STATUS_TONE[r.report_status] || ''}">${esc(r.report_status)}</span>`);
    if (r.practice_status) c.push(`<span class="chip flat">${esc(PRACTICE[r.practice_status] || r.practice_status)}</span>`);
    if (r.prev_status) c.push(`<span class="chip ghost">${esc(r.prev_status)} last week</span>`);
    if (ROSTER[r.roster_status]) c.push(`<span class="chip ghost">${ROSTER[r.roster_status]}</span>`);
    return c.length ? `<div class="av-chips">${c.join('')}</div>` : '';
  }
  function riskRow(r) {
    const p = r.p_play ?? r.base_p_play;
    const moved = r.view === 'saturday' && r.p_play_tuesday != null ? `<span class="move">Tuesday ${pct(r.p_play_tuesday)} → Saturday ${pct(r.p_play)}</span>` : '';
    const role = r.snap_p50 != null
      ? `If he plays: <b>${share(r.snap_p50)}</b> of the snaps <span class="muted">(80%: ${share(r.snap_p10)}–${share(r.snap_p90)})</span> · usually ${share(r.base_snap)}`
      : `Usually ${share(r.base_snap)} of the snaps`;
    // one entry per teammate, his gains joined: "Chimere Dike WR +18 points of the snaps, +2 of the targets"
    const byWho = []; for (const i of r.inherit) { let e = byWho.find((x) => x.player_id === i.player_id); if (!e) byWho.push((e = { ...i, gains: [] })); e.gains.push(i); }
    const inh = byWho.length
      ? `<div class="av-inh"><span class="lbl">If he sits:</span>${byWho.map((e) => `<span><b>${esc(e.player)}</b> ${esc(e.position)} ${e.gains.map((g) => `${pts(g.delta)} ${KIND[g.kind] || g.kind}`).join(', ')}</span>`).join('')}</div>`
      : '';
    const after = r.played == null ? '' : `<div class="av-after">${r.played ? `<b>Played</b>${r.actual_snap != null ? `: ${share(r.actual_snap)} of the snaps` : ''}` : "<b>Didn't play</b>"}</div>`;
    return `<li class="av-row"><div class="who"><b>${esc(r.player)}</b><small>${esc(r.position)} · ${esc(where(r))}</small>${chips(r)}</div>
      <div class="av-chance" aria-label="Chance he plays ${pct(p)}"><span class="big">${pct(p)}<small>to play · usual for his status ${pct(r.base_p_play)}</small></span>
        <span class="av-meter" aria-hidden="true"><b style="width:${(p ?? 0) * 100}%"></b><i style="left:${(r.base_p_play ?? 0) * 100}%"></i></span>${moved}</div>
      <div class="av-role">${role}</div>${inh}${after}</li>`;
  }
  const LIMIT = 15;
  function sections(a) {
    const g = (k) => a.at_risk.filter((r) => r.risk_group === k);
    const report = g('report');
    const uncertain = g('uncertain').slice().sort((x, y) => stake(y) - stake(x));
    const reserve = g('reserve');
    const shownU = S.showAll ? uncertain : uncertain.slice(0, LIMIT);
    return `
      <section class="card av-sec" aria-label="On this week's injury report"><div class="card-h"><h2>On this week's injury report</h2><span class="n">${report.length}</span><span class="muted">Out, Doubtful or Questionable, most likely to sit first</span></div>
        ${report.length ? `<ul class="av-rows">${report.map(riskRow).join('')}</ul>` : `<div class="av-empty">${a.tiles.view === 'tuesday' ? "No report yet: the first one comes out Wednesday, and Saturday's view reads the final one." : 'Nobody with a regular role is on this week\'s report.'}</div>`}</section>
      <section class="card av-sec" aria-label="Regulars with a low chance"><div class="card-h"><h2>Regulars with a low chance</h2><span class="n">${uncertain.length}</span><span class="muted">played at least ${pct(a.tiles.role_floor)} of the snaps lately · the most playing time at stake first</span></div>
        <ul class="av-rows">${shownU.map(riskRow).join('')}</ul>
        ${uncertain.length > shownU.length ? `<div class="av-more"><button type="button" class="btn sm" id="avAll">Show all ${uncertain.length}</button></div>` : ''}</section>
      <details class="card av-sec"><summary><div class="card-h"><h2>On a reserve list</h2><span class="n">${reserve.length}</span><span class="muted">not expected back this week · kept for who took their work</span></div></summary>
        <ul class="av-rows">${reserve.map(riskRow).join('')}</ul></details>`;
  }
  function movers(a) {
    const box = (title, list, model, base) => `<div class="card pad av-mv"><h3>${title}</h3><ol>${list.slice(0, 6).map((r) => `<li><span><b>${esc(r.player)}</b><small>${esc(r.position)} · ${esc(r.team)}</small></span><span class="ft">${share(r[base])} → ${share(r[model])}</span><span class="d">${pts(r.change)}</span></li>`).join('') || '<li class="muted">Nobody this week</li>'}</ol></div>`;
    return `<div class="sec-h"><h2>Biggest role changes</h2><p>the model's share if he plays against his usual (last two games for snaps, his rolling share for targets and carries)</p></div>
      <div class="av-movers">${box('More snaps', a.movers.snap_up, 'snap_p50', 'base_snap')}${box('Fewer snaps', a.movers.snap_down, 'snap_p50', 'base_snap')}${box('More of the targets', a.movers.targets_up, 'tgt_share', 'base_tgt_share')}${box('More of the carries', a.movers.carries_up, 'carry_share', 'base_carry_share')}</div>`;
  }
  function everyPlayer(a) {
    const have = D.team;
    const picker = `<div class="av-teams" role="group" aria-label="Team">${a.teams.map((t) => `<button type="button" class="av-team" data-t="${t}" aria-pressed="${S.team === t}" ${have[t] ? '' : 'title="Not in the mockup\'s data (the app has every team)"'}>${t}</button>`).join('')}</div>`;
    let body = '<div class="card-b muted">Pick a team to see every player: the chance he plays and his shares against his usual.</div>';
    if (S.team && !have[S.team]) body = `<div class="card-b muted">The mockup's data has MIN, DET and WAS (the app fetches any team).</div>`;
    else if (S.team) {
      let rows = have[S.team].players;
      if (S.state === 'graded') rows = rows.map(answer()._g);
      const gr = rows.some((r) => r.played != null);
      const sideRows = (side, label) => {
        const rs = rows.filter((r) => r.side === side);
        if (!rs.length) return '';
        return `<tr class="av-side"><td colspan="${gr ? 7 : 6}">${label}</td></tr>` + rs.map((r) => `<tr class="${r.at_risk ? 'risk' : ''}"><td><b>${esc(r.player)}</b><small>${esc(r.position)}</small></td>
          <td class="r num">${pct(r.p_play)}<small>${pct(r.base_p_play)}</small></td>
          <td class="r num">${share(r.snap_p50)}<small class="wide">${share(r.snap_p10)}–${share(r.snap_p90)}</small></td><td class="r num">${share(r.base_snap)}</td>
          <td class="r num">${r.tgt_share != null ? `${share(r.tgt_share)}<small>${share(r.base_tgt_share)}</small>` : '—'}</td>
          <td class="r num">${r.carry_share != null ? `${share(r.carry_share)}<small>${share(r.base_carry_share)}</small>` : '—'}</td>
          ${gr ? `<td class="r num">${r.played == null ? '—' : r.played ? share(r.actual_snap) : "didn't play"}</td>` : ''}</tr>`).join('');
      };
      body = `<div class="tablewrap"><table class="tbl av-tbl"><thead><tr><th>Player</th><th class="r">Plays<br><small class="muted">model · usual</small></th><th class="r">Snaps if he plays<br><small class="muted">and 80% range</small></th><th class="r">Usually</th><th class="r">Targets<br><small class="muted">model · usual</small></th><th class="r">Carries<br><small class="muted">model · usual</small></th>${gr ? '<th class="r">Played</th>' : ''}</tr></thead>
        <tbody>${sideRows('off', 'Offense')}${sideRows('def', 'Defense')}</tbody></table></div>`;
    }
    return `<section class="card" aria-label="Every player"><div class="card-h"><h2>Every player</h2><span class="muted">${S.team ? `${esc(S.team)} · the marked rows might sit` : 'one team at a time'}</span></div><div class="card-b">${picker}</div>${body}</section>`;
  }
  function good() {
    return `<section class="card" aria-label="How good is this"><div class="card-h"><h2>How good is this</h2><span class="muted">the backtest, 2019–2025: the model beat its baseline in every season</span></div><div class="card-b av-good">
      <div><b>0.107 vs 0.149</b>Will he play, Tuesday: the error (Brier) against the usual rate for his status; lower is better</div>
      <div><b>0.069 vs 0.116</b>The same on Saturday, with the final report</div>
      <div><b>12.0 vs 13.1 points</b>Snap share if he plays: off by, against his last two games</div>
      <div><b>80–81%</b>of the time the snap share landed in its 80% range</div></div></section>`;
  }
  function scoreboard(a) {
    if (!a.scoreboard.week.length && !a.scoreboard.season.length) return '';
    const rows = a.scoreboard.week;
    const OUT = { play: 'Will he play', snap: 'Snap share', tgt_share: 'Target share', carry_share: 'Carry share' };
    return `<section class="card av-score" aria-label="How it did"><div class="card-h"><h2>How it did this week</h2><span class="muted">lower is better · in bold when one is clearly lower</span></div><div class="tablewrap"><table class="tbl"><thead><tr><th>Output</th><th>View</th><th class="r">Players</th><th class="r">Model</th><th class="r">Baseline</th><th class="r">80% range held</th></tr></thead>
      <tbody>${rows.map((r) => { const m = r.output === 'play' ? r.brier_model : r.mae_model, b = r.output === 'play' ? r.brier_baseline : r.mae_baseline; const best = m <= b * 0.98;
        const f = (v) => (v == null ? '—' : r.output === 'play' ? v.toFixed(3) : `${(v * 100).toFixed(1)} pts`);
        return `<tr><td>${OUT[r.output]}</td><td>${r.view === 'saturday' ? 'Saturday' : 'Tuesday'}</td><td class="r num">${r.n}</td><td class="r num${best ? ' best' : ''}">${f(m)}</td><td class="r num">${f(b)}</td><td class="r num">${r.coverage_80 == null ? '—' : pct(r.coverage_80)}</td></tr>`; }).join('')}</tbody></table></div>
      <div class="card-b muted" style="font-size:12px">Will he play: the Brier score against the usual rate for his status. Shares: how far off, in points, against his usual share.</div></section>`;
  }

  function render() {
    const v = $('#view');
    if (S.state === 'loading') { v.innerHTML = `<div class="card"><div class="card-b muted" role="status">Loading…</div></div>`; return; }
    if (S.state === 'none') { v.innerHTML = `<div class="card"><div class="empty"><div class="glyph">AV</div><h3>Week 11's availability isn't made yet</h3><p>Nothing for week 11. It's made right after Run week 11, once that week's run is published.</p><a class="btn sm" href="#">See week 10's Availability</a></div></div>`; return; }
    const a = answer();
    v.innerHTML = `<div class="av">${strip(a)}${tiles(a)}${note(a)}${sections(a)}${movers(a)}${everyPlayer(a)}${scoreboard(a)}${good()}</div>`;
    const all = $('#avAll'); if (all) all.addEventListener('click', () => { S.showAll = true; render(); });
    v.querySelectorAll('.av-team').forEach((b) => b.addEventListener('click', () => { S.team = b.dataset.t; render(); }));
  }
  render();
})();
