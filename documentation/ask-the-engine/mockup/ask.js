(() => {
'use strict';
/* The Ask the Engine mockup (AE01): Explore -> Ask the Engine inside the control room's shell.
   Data: the real app API's answers (dump_payloads.py): `examples` / `examples_graph_down`
   (AskExamplesResponse), `answers.<key>` = {request, http_status, body: AskResponse},
   `errors.<key>` = {http_status, body: {error}}. The React page reads the same shapes. */
const D = JSON.parse(document.getElementById('data').textContent);
const P = D.payloads;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem('cr.' + k); return v == null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('cr.' + k, v); } catch (e) { /* storage blocked: fine */ } },
};
const CR_MOCKUP = 'https://claude.ai/artifact/1z7AapJzJwKusNtWzXYfQw';
const clone = o => JSON.parse(JSON.stringify(o));

/* ------------------------------------------------------------------ the answers
   Real ones first (the dump), then the mockup-only ones, each made from a real answer and
   flagged `mock` so the page labels it. */
const A = {};
for (const [k, v] of Object.entries(P.answers)) A[k] = v.body;
/* The backend's wording changed after the dump (2026-10-18, AE01 review): patched here by hand so
   the page shows today's text. A source stop mid-question is refusal.by "source"; the model
   error and the 429 / 422 messages read as below. */
(function today() {
  if (A.graph_down && A.graph_down.refusal) A.graph_down.refusal.by = 'source';
  if (A.llm_down && A.llm_down.error) A.llm_down.error.message = "The model didn't give a usable reply: gave up after 2 attempts (HTTP 503).";
  const E = P.errors;
  if (E.rate_limited) E.rate_limited.body.error.message = "That's the limit of 10 questions a minute: wait 42 s, then ask again.";
  if (E.too_long) E.too_long.body.error.message = 'Ask a question of 1-500 characters (source: auto, graph or sql; up to 5 picks from a which-one card).';
})();
(function synth() {
  const ff = clone(A.freeform);
  ff.retries = 1; ff.route_label = 'free-form, 1 retry';
  ff.mock = 'A real free-form answer with its retry count set to 1: a correction round shows like this.';
  A.freeform_retry = ff;
  const fb = clone(A.template_sql);
  fb.answer_kind = 'fallback';
  fb.answer = 'The query returned 1 row: the numbers are in the table below.'; // answer.fallback_sentence
  fb.mock = "A real answer drawn as if the model's sentences had failed the number check (the server's own fixed sentence).";
  A.fallback = fb;
  const oc = clone(A.llm_down);
  oc.error.code = 'out_of_credits';
  oc.error.message = 'OpenRouter is out of credits: top up the account, then ask again.'; // ask_service.ERRORS
  oc.mock = 'The model-down answer with the out-of-credits code and the server’s fixed text.';
  A.out_of_credits = oc;
  const qf = clone(A.freeform);
  Object.assign(qf, { status: 'error', answer: null, answer_kind: 'fixed', columns: [], rows: [], row_count: 0, retries: 2, route_label: 'free-form, 2 retries' });
  qf.error = { code: 'query_failed', message: 'The query still failed after 2 corrections (binder_error).', stage: null, provider: 'InferenceNet', model: qf.llm.model };
  qf.timing = Object.assign({}, qf.timing, { answer_s: null });
  qf.mock = 'A free-form question whose query kept failing the run (route.FIXED["failed"]); the last query tried is shown.';
  A.query_failed = qf;
  const bz = clone(A.template_sql);
  Object.assign(bz, { status: 'refused', answer: "The weekly data refresh is running: ask again when it's done.", answer_kind: 'fixed', columns: [], rows: [], row_count: 0, query: null, params: [], route: 'refused', route_label: 'refused' });
  bz.refusal = { by: 'source', rule: 'source.busy', message: bz.answer };
  bz.timing = { total_s: 0.71, route_s: 0.7, generate_s: null, run_s: 0.0, answer_s: null };
  bz.mock = 'A question asked while the weekly refresh holds the lock (route.FIXED["busy"]).';
  A.busy_refused = bz;
})();
const CARDS = [
  ['template_graph', 'Graph template'], ['template_sql', 'SQL template'], ['lists', 'List cells'], ['freeform', 'Free-form'],
  ['freeform_retry', 'Free-form, 1 retry *'], ['fallback', 'Number check failed *'],
  ['refused_code', 'Refused (code)'], ['refused_router', 'Refused (router)'], ['clarify', 'Which one?'], ['clarify_picked', 'Picked'],
  ['empty', 'No rows'], ['graph_down', 'Graph down'], ['busy_refused', 'Refresh running *'], ['llm_down', 'Model down'], ['out_of_credits', 'Out of credits *'], ['query_failed', 'Query kept failing *'],
  ['running', 'Running'], ['none', 'Nothing asked yet'],
];
const ERRS = [['busy', '409 busy'], ['rate_limited', '429 rate limited'], ['too_long', '422 too long'], ['', 'None']];

/* ------------------------------------------------------------------ state */
const PALS = [
  { id: 'turf', name: 'Turf & Pylon', sw: ['#0D1B14', '#FF6B2C', '#4FD98F'], note: 'field green, end-zone pylon orange' },
  { id: 'playbook', name: 'Playbook', sw: ['#FFFFFF', '#1F5FD1', '#CC2F2F'], note: 'whiteboard by day, chalkboard by night' },
];
function systemDark() { try { return matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) { return true; } }
const S = {
  pal: store.get('pal', 'turf'),
  mode: store.get('mode', 'system'),
  settings: false,
  screen: 'ask', // ask | other
  other: '',
  card: 'template_graph', // the answer shown (an A key), 'running' or 'none'
  err: '', // a request-level refusal notice (an errors key)
  status: 'normal', // normal | graph_down | busy
  sess: 'full',
  question: A.template_graph.question,
  source: 'auto',
  started: 0, // running: when it started
  runFor: null, // running: the answer it will show
  copied: false,
  sort: null, // {col, dir}
  qOpen: false,
};
if (!PALS.some(p => p.id === S.pal)) S.pal = 'turf';
if (!['system', 'dark', 'light'].includes(S.mode)) S.mode = 'system';

function examples() {
  if (S.status === 'graph_down') return P.examples_graph_down;
  if (S.status === 'busy') return Object.assign(clone(P.examples), { busy: true, mock: true });
  return P.examples;
}
/** This session's questions, newest first (in the app: localStorage `cr.askSession`). */
function sessionList() {
  if (S.sess === 'empty') return [];
  const keys = ['template_graph', 'template_sql', 'freeform', 'lists', 'refused_code', 'refused_router', 'clarify', 'clarify_picked', 'empty', 'graph_down', 'llm_down'];
  return keys.map(k => ({ k, a: A[k] })).sort((x, y) => (y.a.asked_at || '').localeCompare(x.a.asked_at || ''));
}

/* ------------------------------------------------------------------ formatting (the React page's lib/ask.ts) */
const MAX = (P.examples.limits && P.examples.limits.question_chars) || 500;
function secs(s) {
  if (s == null) return '—';
  if (s < 0.05) return 'under 0.1 s';
  if (s < 1) return s.toFixed(2) + ' s';
  if (s < 10) return s.toFixed(1) + ' s';
  return Math.round(s) + ' s';
}
function money(c) {
  if (c == null) return 'cost unknown';
  if (c === 0) return 'no model call';
  if (c < 0.0001) return 'under $0.0001';
  return '$' + c.toFixed(4);
}
const ET = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' });
const clock = iso => { try { return ET.format(new Date(iso)); } catch (e) { return ''; } };
const colName = c => String(c).replace(/_/g, ' ');
function cellText(v) {
  if (v == null) return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(3)));
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  return String(v);
}
/** A column is numeric when every non-empty cell is a number (right-aligned, sorted as numbers). */
function colKinds(cols, rows) {
  return cols.map((_, i) => {
    let num = 0, list = 0, other = 0;
    for (const r of rows) { const v = r[i]; if (v == null) continue; if (typeof v === 'number') num++; else if (Array.isArray(v)) list++; else other++; }
    return list && !num && !other ? 'list' : num && !list && !other ? 'num' : 'text';
  });
}
function sortRows(rows, col, dir) {
  const key = v => (Array.isArray(v) ? (v.length ? v[0] : null) : v);
  return [...rows].sort((a, b) => {
    const x = key(a[col]), y = key(b[col]);
    if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
    const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
    return c * dir;
  });
}
const STAGE = { route: 'Routing (the first model call)', generate: 'Writing the query', fix: 'Fixing the query', answer: 'Writing the answer' };
const PURPOSE = { route: 'Route (pick a template or write a query)', generate: 'Write the query', fix: 'Fix the query', answer: 'Write the answer' };
function timeTip(a) {
  const t = a.timing || {};
  const lines = [`Took ${secs(t.total_s)} in all`];
  [['route_s', 'Route'], ['generate_s', 'Write the query'], ['run_s', 'Run the query'], ['answer_s', 'Write the answer']].forEach(([k, l]) => { if (t[k] != null) lines.push(`${l}: ${secs(t[k])}`); });
  return lines.join('\n');
}
function costTip(a) {
  const calls = (a.llm && a.llm.calls) || [];
  if (!calls.length) return 'No model call\nAnswered without the model (cost $0)';
  const lines = [`${calls.length} model call${calls.length === 1 ? '' : 's'} · ${a.llm.model || 'model'}`];
  for (const c of calls) lines.push(`${PURPOSE[c.purpose] || c.purpose}: ${c.ok ? '' : 'failed · '}${c.provider ? c.provider + ' · ' : ''}${secs(c.seconds)} · ${c.cost == null ? 'cost not reported' : '$' + c.cost.toFixed(5)}`);
  if (a.cost == null) lines.push('A call didn’t report its cost, so the total is unknown');
  return lines.join('\n');
}
function routeTip(a, ex) {
  const lim = ex.limits || {};
  if (a.route === 'template') return `${(a.template && a.template.title) || 'A template'}\nA tested query from the template library: the model picked it and filled its values ($names below the query); the query itself is fixed.`;
  if (a.route === 'generated') return `Free-form\nNo template fits, so the model wrote this query. The guard checked it before it ran: read only, one statement, a ${lim.query_timeout_s || 10} s limit, at most ${lim.rows || 500} rows.\nRetries: ${a.retries} (correction rounds after a failed check or run; up to ${lim.corrections ?? 2})`;
  return a.route_label || '';
}
/** Who refused a question, in plain words. */
function refusedBy(a) {
  const r = a.refusal || {};
  if (r.by === 'code') return 'Refused before any model call';
  if (r.by === 'source') return r.rule === 'source.busy' ? 'Stopped: the weekly refresh started, so the data isn’t available' : r.rule === 'source.down' ? 'Stopped: the data source isn’t available (the graph is down)' : 'Stopped: the data source isn’t available';
  if (r.by === 'router') return 'Refused by the router (the model’s first step)';
  if (r.by === 'generator') return 'Refused by the model while writing the query';
  if (r.by === 'guard') return `Stopped by the guard: ${r.rule}`;
  return `Refused (${r.by || 'unknown'})`;
}
const GLYPH = { write: 'Read only', not_nfl: 'NFL only', 'source.down': 'Graph down', 'source.busy': 'Refreshing', no_data: 'Not in data', out_of_range: 'Out of range' };

/* ------------------------------------------------------------------ icons */
const ICON = {
  season: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 13.5h12M3.5 11l3-4 2.5 2.5L13 4"/></svg>',
  teams: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 3.5h10M3 8h10M3 12.5h6"/></svg>',
  models: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M8 1.8l5.5 3.1v6.2L8 14.2l-5.5-3.1V4.9z M8 8v6.2 M8 8l5.5-3.1 M8 8L2.5 4.9"/></svg>',
  alerts: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 11V7a4 4 0 0 1 8 0v4l1 1.5H3z M6.5 14h3"/></svg>',
  health: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1.5 8.5h3l1.5-4 3 8 1.5-4h4"/></svg>',
  playcalling: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="4" cy="12" r="2.2"/><path d="M10.5 10l3.5 3.5M14 10l-3.5 3.5M4 9.3C4 5.5 7 3.2 12 3.2M10 1.6l2 1.6-2 1.6"/></svg>',
  // a speech bubble with a question mark: Ask the Engine
  ask: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 3.2h12v8H8.2L5 13.8v-2.6H2z"/><path d="M6.4 5.9a1.6 1.6 0 1 1 2.2 1.5c-.4.2-.6.5-.6.9v.3"/><path d="M8 9.9v.1" stroke-linecap="round"/></svg>',
};

/* ------------------------------------------------------------------ mockbar */
function applyTheme() {
  document.documentElement.setAttribute('data-pal', S.pal);
  document.documentElement.setAttribute('data-mode', S.mode === 'system' ? (systemDark() ? 'dark' : 'light') : S.mode);
}
function syncMockbar() {
  $('#cardPick').innerHTML = CARDS.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${v === S.card}">${esc(l)}</button>`).join('');
  $('#errPick').innerHTML = ERRS.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${v === S.err}">${esc(l)}</button>`).join('');
  $$('#statusPick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.status)));
  $$('#sessPick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.sess)));
  applyTheme();
}
try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (S.mode === 'system') applyTheme(); }); } catch (e) { /* old browsers */ }
$('#cardPick').addEventListener('click', e => {
  const b = e.target.closest('[data-v]'); if (!b) return;
  S.screen = 'ask'; S.err = ''; S.sort = null; S.copied = false;
  if (b.dataset.v === 'running') { startRun(S.question || A.template_graph.question, 'template_graph', true); syncMockbar(); return; }
  stopRun(); S.card = b.dataset.v;
  if (A[S.card]) S.question = A[S.card].question;
  syncMockbar(); render(true);
});
$('#errPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.screen = 'ask'; S.err = b.dataset.v; if (S.err === 'too_long') S.question = (S.question + ' ').repeat(12).slice(0, 520); syncMockbar(); render(); });
$('#statusPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.status = b.dataset.v; S.screen = 'ask'; syncMockbar(); render(); });
$('#sessPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.sess = b.dataset.v; S.screen = 'ask'; syncMockbar(); render(); });

/* ------------------------------------------------------------------ shell (as the control room) */
function renderSide() {
  const W = P.weeks;
  $('#side').innerHTML = `
    <div class="brand"><div class="brand-mark">CR</div><div style="flex:1;min-width:0"><b>Control Room</b><small>NFL Analytics Engine</small></div>
      <button class="iconbtn" id="gearBtn" aria-expanded="${S.settings}" aria-controls="settingsPop" title="Appearance"><svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="2.2"/><path d="M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M3.4 12.6l1.3-1.3M11.3 4.7l1.3-1.3"/></svg></button>
      ${S.settings ? settingsPop() : ''}</div>
    <div class="navgrp"><div class="eyebrow">${W.season} weeks</div>
      ${W.weeks.map(w => `<button class="navi" data-nav="other" data-what="Week ${w.week}"><span class="wk">${w.week}</span><span class="grow">Week ${w.week}<span class="sub">${esc(w.is_current ? 'This week' : (w.detail || w.label))}</span></span><span class="chip ${w.status === 'published' ? 'ok' : w.status === 'failed' ? 'err' : 'flat'}">${w.status === 'published' ? '<span class="ic">✓</span>' : ''}${esc(w.status === 'published' ? 'Published' : w.label)}</span></button>`).join('')}
      <button class="navi" disabled title="Live weekly runs started in week ${W.go_live_week}"><span class="wk">1–${W.go_live_week - 1}</span><span class="grow">Before go-live<span class="sub">Walk-forward rows only</span></span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">Season</div>
      <button class="navi" data-nav="other" data-what="The Scorecard">${ICON.season}<span class="grow">Scorecard</span></button>
      <button class="navi" data-nav="other" data-what="Teams &amp; rankings">${ICON.teams}<span class="grow">Teams &amp; rankings</span></button>
      <button class="navi" data-nav="other" data-what="Models">${ICON.models}<span class="grow">Models</span></button>
      <button class="navi" data-nav="other" data-what="Alerts">${ICON.alerts}<span class="grow">Alerts</span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">Explore</div>
      <button class="navi" data-nav="other" data-what="Play calling">${ICON.playcalling}<span class="grow">Play calling</span></button>
      <button class="navi" data-nav="ask" aria-current="${String(S.screen === 'ask')}">${ICON.ask}<span class="grow">Ask the Engine</span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">System</div>
      <button class="navi" data-nav="other" data-what="Health">${ICON.health}<span class="grow">Health</span><span class="chip ok"><span class="ic">✓</span>OK</span></button>
    </div>
    <div class="side-foot">
      <div><span>Run lock</span><span>${S.status === 'busy' ? 'held' : 'free'}</span></div>
      <div><span>Neo4j</span><span>${S.status === 'graph_down' ? 'down' : esc((P.meta.neo4j || {}).status || '—')}</span></div>
      <div><span>Data drive</span><span>${esc(P.meta.data_root && P.meta.data_root.free_gb != null ? P.meta.data_root.free_gb + ' GB free' : '—')}</span></div>
    </div>`;
}
function settingsPop() {
  return `<div class="popover" id="settingsPop" role="dialog" aria-label="Appearance">
    <span class="eyebrow">Theme</span>
    <div class="themes">${PALS.map(p => `<button type="button" class="themecard" data-pal="${p.id}" aria-pressed="${p.id === S.pal}"><i>${p.sw.map(c => `<b style="background:${c}"></b>`).join('')}</i><span><b>${esc(p.name)}</b><small>${esc(p.note)}</small></span></button>`).join('')}</div>
    <span class="eyebrow">Mode</span>
    <span class="seg">${['system', 'dark', 'light'].map(m => `<button type="button" data-mode="${m}" aria-pressed="${S.mode === m}">${m[0].toUpperCase() + m.slice(1)}</button>`).join('')}</span>
    <span class="muted" style="font-size:11.5px">Saved in this browser.</span>
  </div>`;
}
$('#side').addEventListener('click', e => {
  if (e.target.closest('#gearBtn')) { S.settings = !S.settings; renderSide(); return; }
  const pb = e.target.closest('.themecard[data-pal]'); if (pb) { S.pal = pb.dataset.pal; store.set('pal', S.pal); applyTheme(); renderSide(); return; }
  const mb = e.target.closest('#settingsPop [data-mode]'); if (mb) { S.mode = mb.dataset.mode; store.set('mode', S.mode); applyTheme(); renderSide(); return; }
  if (e.target.closest('#settingsPop')) return;
  const b = e.target.closest('[data-nav]'); if (!b) return;
  S.settings = false; S.screen = b.dataset.nav; S.other = b.dataset.what || '';
  render(); window.scrollTo({ top: 0 });
});

/* ------------------------------------------------------------------ header: the sources' freshness, Neo4j, the model */
function statusLine(ex) {
  const g = ex.sources.graph, q = ex.sources.sql, m = ex.model;
  const GS = { up: ['ok', 'Neo4j up'], down: ['err', 'Neo4j down'], checking: ['flat', 'checking Neo4j'], config_problem: ['err', 'Neo4j config problem'], none: ['warn', 'no graph built yet'], off: ['flat', 'switched off'] };
  const [gt, gl] = GS[g.status] || ['flat', g.status];
  const gTip = `The weekly graph (Neo4j)\n${g.label || 'not built'}\nRebuilt every week by the weekly run; this-week questions read it as of week ${g.week ?? '?'}\nNeo4j: ${gl}`;
  const qTip = `The curated tables (DuckDB in memory)\n${q.label || '—'}${q.rebuilt ? `\nRebuilt ${q.rebuilt} by the weekly run's curate step` : ''}`;
  const mTip = `The model: ${m.name || '—'}\nReasoning effort: ${m.effort || '—'} · via ${m.provider}\nA question gives up after ${m.timeout_s} s per model call\nLimits: ${ex.limits.question_chars} characters, ${ex.limits.per_minute} questions a minute, ${ex.limits.rows} rows, a ${ex.limits.query_timeout_s} s query limit`;
  return `<div class="ask-status" aria-label="Sources and model">
    <span tabindex="0" data-tip="${esc(gTip)}"><i class="dot ${gt}"></i>${esc(g.on ? (g.label || 'weekly graph: not built') : 'weekly graph: off')} · ${esc(gl)}</span>
    <span tabindex="0" data-tip="${esc(qTip)}"><i class="dot ${q.on && q.status === 'up' ? 'ok' : 'flat'}"></i>${esc(q.on ? (q.label || 'curated data') : 'curated data: off')}</span>
    <span tabindex="0" data-tip="${esc(mTip)}"><span class="muted">Model</span> <b>${esc(m.label || '—')}</b> · ${esc(m.effort || '—')}</span>
  </div>`;
}
function renderTop() {
  const top = $('#top');
  if (S.screen === 'other') { top.innerHTML = `<div class="titleblock"><span class="eyebrow">Control room</span><h1>${esc(S.other)}</h1><span class="muted">Unchanged by Ask the Engine (AE01)</span></div>`; return; }
  const ex = examples();
  top.innerHTML = `<div class="titleblock"><span class="eyebrow">Explore</span><h1>Ask the Engine</h1>${statusLine(ex)}</div>`;
}

/* ------------------------------------------------------------------ the view */
function render(focusAnswer) {
  hideTip();
  renderSide(); renderTop();
  const v = $('#view');
  if (S.screen === 'other') {
    v.innerHTML = `<div class="notice accent"><span class="ic">i</span><div><b>Mockup: ${esc(S.other)} is unchanged by Ask the Engine (AE01).</b> It's drawn in the <a href="${CR_MOCKUP}" target="_blank" rel="noopener">control room mockup</a>; this page shows Explore → Ask the Engine only.<div style="margin-top:10px"><button class="btn sm" data-nav-ask>Open Ask the Engine</button></div></div></div>`;
    return;
  }
  const ex = examples();
  v.innerHTML = `${banners(ex)}
    <div class="ask-layout">
      <div class="ask-main">${askBox(ex)}${noticeFor()}${S.card === 'running' ? runningCard() : answerArea(ex)}</div>
      <aside class="ask-rail">${sessionCard()}</aside>
    </div>`;
  if (focusAnswer) { const h = $('#askAnswerH'); if (h) h.focus({ preventScroll: false }); }
}
function banners(ex) {
  const out = [];
  const g = ex.sources.graph.status;
  if (ex.sources.graph.on && (g === 'down' || g === 'config_problem')) {
    out.push(`<div class="notice ask-banner warn" role="status"><span class="ic">!</span><div><b>The weekly graph isn't answering (Neo4j ${g === 'down' ? 'down' : 'config problem'}).</b> Graph questions can't be answered now; questions about the tables still work.</div></div>`);
  }
  if (ex.busy) {
    out.push(`<div class="notice ask-banner warn" role="status"><span class="ic">!</span><div><b>The weekly refresh is running.</b> The tables and the graph are being rebuilt, so questions are refused until it ends.${ex.mock ? ' <span class="muted">(Mockup-only: the real status answer with <code>busy: true</code>.)</span>' : ''}</div></div>`);
  }
  return out.join('');
}
function askBox(ex) {
  const n = S.question.length;
  const over = n > MAX;
  const running = S.card === 'running';
  const srcs = [['auto', 'Auto', 'The engine picks: the weekly graph or the curated tables'], ['graph', 'Weekly graph', 'Only the weekly graph (Neo4j): connections, this week’s games, coaching trees'], ['sql', 'Tables', 'Only the curated tables: stats, records, play-by-play']];
  const gDown = ['down', 'config_problem'].includes(ex.sources.graph.status);
  const chips = ex.featured.map(e => `<button type="button" class="ask-chip" data-ex="${esc(e.question)}" title="${esc(e.title)}">${esc(e.question)}${e.this_week ? '<span class="wk">this week</span>' : ''}</button>`).join('');
  const groups = ex.groups.map(g => `<div class="ask-group"><h3>${esc(g.label)}<small>${g.examples.length}</small></h3><ul>${g.examples.map(e => `<li><button type="button" data-ex="${esc(e.question)}"><span class="grow">${esc(e.question)}</span>${e.this_week ? '<span class="wk">this week</span>' : ''}</button></li>`).join('')}</ul></div>`).join('');
  return `<form class="card ask-box" id="askForm" autocomplete="off">
    <label class="ask-lbl" for="askInput">Ask a question about NFL data</label>
    <textarea class="ask-input" id="askInput" rows="3" placeholder="Who has Patrick Mahomes thrown to the most this season?" aria-describedby="askCount askKeys" aria-invalid="${over}">${esc(S.question)}</textarea>
    <div class="ask-foot">
      <span class="ask-src"><span class="eyebrow">Source</span><span class="seg" role="group" aria-label="Source">${srcs.map(([v, l, t]) => `<button type="button" data-src="${v}" aria-pressed="${S.source === v}" title="${esc(t)}">${esc(l)}${v === 'graph' && gDown ? ' <span class="muted">(down)</span>' : ''}</button>`).join('')}</span></span>
      <span class="ask-go">
        <span class="ask-keys" id="askKeys"><kbd>Enter</kbd> to ask · <kbd>Shift</kbd>+<kbd>Enter</kbd> new line</span>
        <span class="ask-count${over ? ' over' : n > MAX * 0.9 ? ' near' : ''}" id="askCount" aria-live="polite">${n} / ${MAX}${over ? ' · too long' : ''}</span>
        <button type="submit" class="btn primary" ${running || over || !S.question.trim() ? 'disabled' : ''}>${running ? 'Asking…' : 'Ask'}</button>
      </span>
    </div>
    <div class="ask-ex">
      <span class="eyebrow">Try one</span>
      <div class="ask-chips">${chips}</div>
      <details class="ask-more"><summary>More examples (${ex.groups.reduce((s, g) => s + g.examples.length, 0)})</summary><div class="ask-groups">${groups}</div></details>
    </div>
  </form>`;
}
function noticeFor() {
  if (!S.err) return '';
  const e = P.errors[S.err];
  const msg = (e && e.body && e.body.error && e.body.error.message) || '';
  const head = { busy: 'Still finishing the last question.', rate_limited: 'Too many questions in a minute.', too_long: 'That request wasn’t accepted.' }[S.err];
  return `<div class="notice ask-banner ${S.err === 'too_long' ? 'err' : 'warn'}" role="alert"><span class="ic">!</span><div><b>${esc(head)}</b> ${esc(msg)} <span class="muted">(HTTP ${e.http_status})</span></div><button type="button" class="btn sm ask-x" data-dismiss>Dismiss</button></div>`;
}
function runningCard() {
  const s = Math.max(0, Math.floor((Date.now() - S.started) / 1000));
  return `<div class="card ask-run" aria-busy="true">
    <div><span class="eyebrow">Asking</span><div class="q">${esc(S.runQ)}</div></div>
    <div class="st"><span class="ask-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="secs" id="askSecs" aria-label="seconds so far">${s} s</span><button type="button" class="btn sm" data-cancel>Cancel</button></div>
    <div class="note">Most answers take 2–5 s; a free-form query up to about 30 s. <b>Cancel</b> (or <kbd>Esc</kbd>) only stops waiting: the server still finishes the question and logs its cost.</div>
  </div>`;
}
function answerArea(ex) {
  if (S.card === 'none') {
    return `<div class="card"><div class="empty"><div class="glyph">?</div><h3>Ask about players, teams, games or coaches</h3><p>The answer comes with the rows behind it and the exact query that produced them. Pick an example above to start from one.</p></div></div>`;
  }
  const a = A[S.card];
  if (!a) return '';
  const mock = a.mock ? `<div class="notice"><span class="ic">i</span><div><b>Mockup-only state.</b> ${esc(a.mock)}</div></div>` : '';
  return mock + answerCard(a, ex);
}
function head(a, eyebrow) {
  return `<div class="card-h"><span class="eyebrow" style="flex-basis:100%">${esc(eyebrow)}</span><h2 id="askAnswerH" tabindex="-1">${esc(a.question)}</h2><span class="when">${esc(clock(a.asked_at))}</span></div>`;
}
function metaChips(a, ex) {
  const out = [];
  if (a.source) out.push(`<span class="chip flat" tabindex="0" data-tip="${esc(`Source\n${a.source.label}${a.source.rebuilt ? `\nTables rebuilt ${a.source.rebuilt}` : ''}`)}"><span class="k">Source</span> ${esc(a.source.label)}</span>`);
  if (a.route === 'template' || a.route === 'generated') out.push(`<span class="chip ${a.route === 'generated' ? 'run' : 'flat'}" tabindex="0" data-tip="${esc(routeTip(a, ex))}"><span class="k">Route</span> ${esc(a.route_label)}</span>`);
  out.push(`<span class="chip flat" tabindex="0" data-tip="${esc(timeTip(a))}"><span class="k">Time</span> ${esc(secs(a.timing && a.timing.total_s))}</span>`);
  out.push(`<span class="chip flat" tabindex="0" data-tip="${esc(costTip(a))}"><span class="k">Cost</span> ${esc(money(a.cost))}</span>`);
  return `<div class="ask-meta">${out.join('')}</div>`;
}
function queryBlock(a, label) {
  if (!a.query) return '';
  const lang = a.language === 'cypher' ? 'Cypher' : a.language === 'sql' ? 'SQL' : 'Query';
  const params = a.params && a.params.length ? `<div class="ask-params"><span class="eyebrow">Bound values</span><dl>${a.params.map(p => `<dt>$${esc(p.name)}</dt><dd>${esc(Array.isArray(p.value) ? JSON.stringify(p.value) : cellText(p.value))}</dd>`).join('')}</dl></div>` : '';
  return `<details class="ask-q" id="askQ"${S.qOpen ? ' open' : ''}>
    <summary><span class="tw" aria-hidden="true">▶</span>${esc(label || 'The query')}<span class="lang">${lang}</span>
      <span class="right">${S.copied ? '<span class="ask-copied" role="status">Copied</span>' : ''}<button type="button" class="btn sm" data-copy>Copy</button></span></summary>
    <pre class="ask-code" tabindex="0" aria-label="${lang} query">${esc(a.query)}</pre>
    ${params}
  </details>`;
}
function rowsTable(a) {
  const cols = a.columns, kinds = colKinds(cols, a.rows);
  const rows = S.sort ? sortRows(a.rows, S.sort.col, S.sort.dir) : a.rows;
  const th = cols.map((c, i) => {
    const s = S.sort && S.sort.col === i ? (S.sort.dir > 0 ? 'ascending' : 'descending') : 'none';
    return `<th scope="col" class="${kinds[i] === 'num' ? 'r' : ''}" aria-sort="${s}"><button type="button" data-sort="${i}" title="Sort by ${esc(colName(c))}">${esc(colName(c))}<span class="arr" aria-hidden="true">${s === 'ascending' ? '▲' : s === 'descending' ? '▼' : ''}</span></button></th>`;
  }).join('');
  const td = (v, k) => {
    if (v == null) return '<td class="null">—</td>';
    if (Array.isArray(v)) { const shown = v.slice(0, 8); return `<td><span class="ask-li">${shown.map(x => `<span>${esc(cellText(x))}</span>`).join('')}${v.length > 8 ? `<span class="more">+${v.length - 8}</span>` : ''}</span></td>`; }
    return `<td class="${k === 'num' ? 'r num' : String(v).length > 40 ? 'txt long' : 'txt'}">${esc(cellText(v))}</td>`;
  };
  const count = `Showing ${a.rows.length} of ${a.row_count} row${a.row_count === 1 ? '' : 's'}${a.truncated ? ` · more rows existed than the ${a.rows.length}-row cap` : ''}`;
  return `<div class="ask-rows-h"><h3>The rows</h3><p>${esc(count)}${a.rows.length > 1 ? ' · click a header to sort' : ''}</p></div>
    <div class="ask-tablewrap" tabindex="0" aria-label="The rows behind the answer"><table class="tbl ask-tbl"><thead><tr>${th}</tr></thead><tbody>${rows.map(r => `<tr>${r.map((v, i) => td(v, kinds[i])).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function answerCard(a, ex) {
  if (a.status === 'answered') {
    const mark = a.answer_kind === 'model'
      ? `<span class="ask-checked" tabindex="0" data-tip="${esc('The number check\nThe model wrote these sentences; every number in them was found in the rows below.')}"><span class="ic">✓</span>Every number checked against the rows</span>`
      : `<div class="notice"><span class="ic">i</span><div><b>Only the table is shown.</b> The model's sentences didn't pass the number check (a number in them wasn't in the rows), so this fixed line points to the table instead.</div></div>`;
    return `<article class="card ask-card" aria-labelledby="askAnswerH">${head(a, 'Answer')}
      <div class="card-b"><p class="ask-say">${esc(a.answer)}</p>${mark}${metaChips(a, ex)}${rowsTable(a)}${queryBlock(a)}</div></article>`;
  }
  if (a.status === 'refused') {
    const r = a.refusal || {};
    const glyph = GLYPH[r.rule] || 'Refused';
    return `<article class="card ask-card" aria-labelledby="askAnswerH">${head(a, 'Not answered')}
      <div class="card-b"><div class="ask-state st-refused"><span class="glyph" aria-hidden="true">${esc(glyph)}</span><h3>${esc(r.message || a.answer)}</h3><p class="who">${esc(refusedBy(a))}${r.rule ? ` · rule <code>${esc(r.rule)}</code>` : ''}</p></div>${metaChips(a, ex)}${queryBlock(a, 'The query it would have run')}</div></article>`;
  }
  if (a.status === 'clarify') {
    const cs = a.choices || [];
    const title = cs.length === 1 ? `Which ${cs[0].text}?` : 'Which ones?';
    return `<article class="card ask-card" aria-labelledby="askAnswerH">${head(a, 'Needs a choice')}
      <div class="card-b"><div class="ask-state st-clarify"><span class="glyph" aria-hidden="true">Which?</span><h3>${esc(title)}</h3><p>${esc(a.answer)} Pick one and the same question is asked again with that ${esc(cs[0] ? cs[0].kind : 'name')}.</p>
      ${cs.map((c, ci) => `<div class="ask-opts" role="group" aria-label="${esc(c.text)}">${cs.length > 1 ? `<span class="eyebrow">${esc(c.text)}</span>` : ''}${c.options.map((o, oi) => `<button type="button" class="ask-opt" data-pick="${ci}:${oi}">${esc(o.label)}<span class="arrow" aria-hidden="true">→</span></button>`).join('')}</div>`).join('')}
      </div>${metaChips(a, ex)}</div></article>`;
  }
  if (a.status === 'empty') {
    return `<article class="card ask-card" aria-labelledby="askAnswerH">${head(a, 'No rows')}
      <div class="card-b"><div class="ask-state st-empty"><span class="glyph" aria-hidden="true">0 rows</span><h3>${esc(a.answer || 'No rows matched.')}</h3><p>The query ran and found nothing. Try a wider season range, another spelling or another source.</p>${a.columns.length ? `<div class="ask-cols" aria-label="Columns it would show">${a.columns.map(c => `<code>${esc(c)}</code>`).join('')}</div>` : ''}</div>${metaChips(a, ex)}${queryBlock(a)}</div></article>`;
  }
  // error
  const e = a.error || {};
  const title = e.code === 'out_of_credits' ? 'Out of credits' : e.code === 'query_failed' ? 'The query kept failing' : e.code === 'llm' ? 'The model didn’t answer' : 'Something went wrong';
  const kv = [['Stage', e.stage ? (STAGE[e.stage] || e.stage) : null, ''], ['Provider', e.provider, 'mono'], ['Model', e.model, 'mono'], ['Code', e.code, 'mono']].filter(([, v]) => v);
  return `<article class="card ask-card" aria-labelledby="askAnswerH">${head(a, 'Error')}
    <div class="card-b"><div class="ask-state st-error"><span class="glyph" aria-hidden="true">${e.code === 'out_of_credits' ? '$0' : 'Error'}</span><h3>${esc(title)}</h3><p>${esc(e.message || '')}</p>
    <dl class="ask-kv">${kv.map(([k, v, c]) => `<dt>${esc(k)}</dt><dd class="${c}">${esc(v)}</dd>`).join('')}</dl></div>${metaChips(a, ex)}${queryBlock(a, 'The last query tried')}</div></article>`;
}
function sessionCard() {
  const list = sessionList();
  const items = list.map(({ k, a }) => `<li><button type="button" data-hist="${k}" aria-current="${String(S.card === k)}"><i class="dot st-${a.status}" aria-hidden="true"></i><span class="hq">${esc(a.question)}</span><span class="hm">${esc(clock(a.asked_at))} · ${esc({ answered: a.route_label, empty: 'no rows', refused: 'refused', clarify: 'needs a choice', error: 'error' }[a.status] || a.status)}</span><span class="ask-sr">, ${esc({ answered: 'answered', empty: 'no rows', refused: 'refused', clarify: 'needs a choice', error: 'error' }[a.status] || a.status)}</span></button></li>`).join('');
  return `<section class="card ask-hist" aria-labelledby="askHistH">
    <div class="card-h"><h2 id="askHistH">This session</h2><span class="right"><span class="muted" style="font-size:12px">${list.length} question${list.length === 1 ? '' : 's'}</span></span></div>
    ${list.length ? `<ol>${items}</ol>` : '<p class="none">Questions you ask stay here until you clear them.</p>'}
    <div class="foot"><span>Kept in this browser only</span>${list.length ? '<button type="button" class="btn sm" data-clear>Clear session</button>' : ''}</div>
  </section>`;
}

/* ------------------------------------------------------------------ running (the mockup plays the dumped answer back) */
let tick = 0, done = 0;
function startRun(q, key, hold) {
  stopRun();
  S.card = 'running'; S.runQ = q; S.runFor = key; S.started = Date.now(); S.err = '';
  render();
  tick = setInterval(() => { const el = $('#askSecs'); if (el) el.textContent = Math.floor((Date.now() - S.started) / 1000) + ' s'; }, 250);
  if (!hold && key) {
    const ms = Math.min(4000, Math.max(900, ((A[key].timing || {}).total_s || 1) * 1000));
    done = setTimeout(() => { stopRun(); S.card = key; S.sort = null; S.copied = false; syncMockbar(); render(true); }, ms);
  }
}
function stopRun() { clearInterval(tick); clearTimeout(done); tick = done = 0; }
function cancelRun() {
  stopRun(); S.card = 'none'; S.err = '';
  syncMockbar(); render();
  const i = $('#askInput'); if (i) i.focus();
}
function ask() {
  const q = S.question.trim();
  if (!q || q.length > MAX || S.card === 'running') return;
  // the mockup has only the dumped answers: a question that matches one plays it back
  const key = Object.keys(P.answers).find(k => P.answers[k].body.question === q && !P.answers[k].request.picks && k !== 'graph_down' && k !== 'llm_down');
  if (key) { startRun(q, key); return; }
  S.card = 'none'; S.err = '';
  render();
  $('#view .ask-main').insertAdjacentHTML('beforeend', '<div class="notice"><span class="ic">i</span><div><b>Mockup:</b> only the dumped answers exist here. Ask one of: ' + Object.values(P.answers).filter(x => !x.request.picks).map(x => esc(x.body.question)).filter((v, i, s) => s.indexOf(v) === i).join(' · ') + '</div></div>');
}

/* ------------------------------------------------------------------ events */
const view = $('#view');
view.addEventListener('input', e => {
  if (e.target.id !== 'askInput') return;
  S.question = e.target.value;
  const n = S.question.length, over = n > MAX;
  const c = $('#askCount'); c.textContent = `${n} / ${MAX}${over ? ' · too long' : ''}`; c.className = 'ask-count' + (over ? ' over' : n > MAX * 0.9 ? ' near' : '');
  e.target.setAttribute('aria-invalid', String(over));
  const b = $('#askForm [type=submit]'); if (b) b.disabled = S.card === 'running' || over || !S.question.trim();
});
view.addEventListener('keydown', e => {
  if (e.target.id === 'askInput' && e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(); }
});
view.addEventListener('submit', e => { e.preventDefault(); ask(); });
view.addEventListener('click', e => {
  const src = e.target.closest('[data-src]'); if (src) { S.source = src.dataset.src; $$('[data-src]').forEach(b => b.setAttribute('aria-pressed', String(b === src))); return; }
  const ex = e.target.closest('[data-ex]');
  if (ex) { S.question = ex.dataset.ex; const i = $('#askInput'); i.value = S.question; i.dispatchEvent(new Event('input', { bubbles: true })); i.focus(); i.setSelectionRange(i.value.length, i.value.length); return; }
  if (e.target.closest('[data-cancel]')) { cancelRun(); return; }
  if (e.target.closest('[data-dismiss]')) { S.err = ''; syncMockbar(); render(); return; }
  if (e.target.closest('[data-copy]')) {
    e.preventDefault();
    const a = A[S.card]; S.qOpen = true;
    const done = () => { S.copied = true; render(); setTimeout(() => { S.copied = false; const c = $('.ask-copied'); if (c) c.remove(); }, 1600); };
    try { navigator.clipboard.writeText(a.query).then(done, done); } catch (err) { done(); }
    return;
  }
  const so = e.target.closest('[data-sort]');
  if (so) { const col = Number(so.dataset.sort); S.sort = S.sort && S.sort.col === col ? (S.sort.dir > 0 ? { col, dir: -1 } : null) : { col, dir: 1 }; render(); const again = $(`[data-sort="${col}"]`); if (again) again.focus(); return; }
  const pk = e.target.closest('[data-pick]');
  if (pk) {
    const [ci, oi] = pk.dataset.pick.split(':').map(Number);
    const c = A[S.card].choices[ci], o = c.options[oi];
    const real = P.answers.clarify_picked;
    const pick = (real.request.picks || [])[0] || {};
    if (pick.value === o.value) { startRun(A.clarify.question, 'clarify_picked'); return; }
    view.querySelector('.ask-state.st-clarify').insertAdjacentHTML('beforeend', `<div class="notice" style="margin-top:8px"><span class="ic">i</span><div><b>Mockup:</b> the dump has the follow-up for the first option only. The app re-asks with <code>picks: [{text: "${esc(c.text)}", kind: "${esc(c.kind)}", value: "${esc(o.value)}"}]</code>.</div></div>`);
    return;
  }
  const h = e.target.closest('[data-hist]'); if (h) { stopRun(); S.card = h.dataset.hist; S.question = A[S.card].question; S.sort = null; S.err = ''; syncMockbar(); render(true); return; }
  if (e.target.closest('[data-clear]')) { S.sess = 'empty'; syncMockbar(); render(); return; }
  if (e.target.closest('[data-nav-ask]')) { S.screen = 'ask'; render(); }
});
view.addEventListener('toggle', e => { if (e.target.id === 'askQ') S.qOpen = e.target.open; }, true);

/* ------------------------------------------------------------------ tooltips (hover and focus) */
const tip = $('#tip');
function showTip(html, x, y) {
  tip.innerHTML = html; tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > innerHeight - 8) top = y - r.height - 14;
  tip.style.left = Math.max(8, left) + 'px'; tip.style.top = Math.max(8, top) + 'px';
}
function tipHTML(t) { const [h, ...rest] = t.dataset.tip.split('\n'); return `<div class="th">${esc(h)}</div>${rest.map(l => `<div>${esc(l)}</div>`).join('')}`; }
function hideTip() { tip.hidden = true; }
document.addEventListener('pointermove', e => { const t = e.target.closest && e.target.closest('[data-tip]'); if (t) showTip(tipHTML(t), e.clientX, e.clientY); else hideTip(); });
document.addEventListener('focusin', e => { const t = e.target.closest && e.target.closest('[data-tip]'); if (t) { const r = t.getBoundingClientRect(); showTip(tipHTML(t), r.left + Math.min(r.width, 220) * 0.5, r.bottom - 6); } else hideTip(); });
document.addEventListener('focusout', hideTip);
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  hideTip();
  if (S.card === 'running') { cancelRun(); return; }
  if (S.settings) { S.settings = false; renderSide(); }
});

/* ------------------------------------------------------------------ boot */
syncMockbar();
render();
})();
