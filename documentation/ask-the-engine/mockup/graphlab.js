(() => {
'use strict';
/* The Connections + Graph lab mockup (AE03): Explore -> Ask the Engine's second and third parts,
   inside the control room's shell. Data: the real app API's answers (dump_graphlab.py), each
   `{http_status, ms, body}`: `status` (GET /api/ask/graph-lab), `people.<q>`, `connections.<key>`,
   `connection_errors.<key>`, `lab.neighbours.<player>`, `lab.map`, `lab.families`,
   `lab.family_of_*`, `lab.chemistry`, `lab.chemistry_300`, `lab.benchmark`. The React page reads
   the same shapes (the AE03 block of web/src/api/types.ts). Anything not from the dump is marked
   mockup-only on the page. */
const D = JSON.parse(document.getElementById('data').textContent);
const P = D.payloads;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem('cr.' + k); return v == null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('cr.' + k, v); } catch (e) { /* storage blocked: fine */ } },
};
const clone = o => JSON.parse(JSON.stringify(o));
const CR_MOCKUP = 'https://claude.ai/artifact/1z7AapJzJwKusNtWzXYfQw';
const ASK_MOCKUP = 'https://claude.ai/artifact/9vCLeQ3eZ6czTeU1dki2TU';

/* ------------------------------------------------------------------ formatting (the React page's lib/graphlab.ts) */
const int = n => (n == null ? '—' : Math.round(n).toLocaleString('en-US'));
const num = (n, d = 2) => (n == null ? '—' : Number(n).toFixed(d));
const pct = (n, d = 0) => (n == null ? '—' : (n * 100).toFixed(d) + '%');
const DT = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
const when = iso => { try { return DT.format(new Date(iso)); } catch (e) { return iso || '—'; } };
function ms(v) {
  if (v == null) return '—';
  if (v < 1) return '<1 ms';
  if (v < 1000) return Math.round(v) + ' ms';
  if (v < 60000) return (v / 1000).toFixed(v < 10000 ? 2 : 1) + ' s';
  return (v / 60000).toFixed(1) + ' min';
}
/** An axis tick at a power of ten: 0.1 ms, 1 ms, 10 ms, 100 ms, 1 s, 10 s, 100 s. */
const msTick = v => (v >= 1000 ? `${+(v / 1000).toPrecision(3)} s` : `${+v.toPrecision(3)} ms`);
const cap = s => (s ? s[0].toUpperCase() + s.slice(1) : s);
const plural = (n, w) => `${int(n)} ${w}${n === 1 ? '' : 's'}`;
const LINK_WORD = { TEAMMATE: 'teammates', COACHED: 'coached', WORKED_WITH: 'same staff' };
const LINK_HELP = {
  TEAMMATE: 'On the field together on the same side (snaps together)',
  COACHED: 'A head coach or coordinator and a player in his unit (the player’s snaps)',
  WORKED_WITH: 'Two coaches on the same staff (the team’s snaps while both were there)',
};
/** "QB · KC" / "coach · NE" (a coach's position is null in a chain, "coach" in the search). */
function personSub(p) {
  const what = p.kind === 'coach' ? 'coach' : (p.position || 'player');
  return [what, p.team].filter(Boolean).join(' · ');
}
/** "2018-2018" -> "2018". */
function seasonText(x) {
  return x ? String(x).replace(/^(\d{4})-\1$/, '$1') : x;
}

/* ------------------------------------------------------------------ the dump, indexed */
const ST = P.status.body;
const LINKS = ST.links;
/** Everyone the dump names, for the mockup's search (the app asks GET /api/ask/people). */
const PEOPLE = new Map();
for (const r of Object.values(P.people)) for (const p of r.body.people) PEOPLE.set(p.id, p);
for (const r of Object.values(P.connections)) {
  if (r.http_status !== 200) continue;
  for (const p of [r.body.a, r.body.b]) {
    const id = `${p.kind}:${p.id}`;
    if (!PEOPLE.has(id)) PEOPLE.set(id, { id, kind: p.kind, name: p.name, position: p.kind === 'coach' ? 'coach' : p.position, team: p.team || null, seasons: p.seasons || null, links: p.links });
  }
}
for (const nb of Object.values(P.lab.neighbours)) {
  const b = nb.body;
  for (const r of [...(b.position || []), ...(b.overall || [])]) {
    const id = `player:${r.neighbour_id}`;
    if (!PEOPLE.has(id)) PEOPLE.set(id, { id, kind: 'player', name: r.neighbour_name, position: r.neighbour_position, team: r.neighbour_team, seasons: null, links: null });
  }
}
// the dump's player who isn't in the map (99 snaps: the neighbours job keeps 100+); his name is
// what the search would have shown
const SHEPLEY = '00-0034229';
if (!PEOPLE.has('player:' + SHEPLEY)) PEOPLE.set('player:' + SHEPLEY, { id: 'player:' + SHEPLEY, kind: 'player', name: 'Dakoda Shepley', position: 'C', team: 'DAL', seasons: null, links: null });
const pairKey = (a, b, mode) => `${a}|${b}|${mode}`;
const PAIRS = {};
for (const [k, r] of Object.entries(P.connections)) {
  if (r.http_status !== 200) continue;
  const b = r.body;
  const key = pairKey(`${b.a.kind}:${b.a.id}`, `${b.b.kind}:${b.b.id}`, b.mode);
  PAIRS[key] = PAIRS[key] || {};
  PAIRS[key][b.k > 1 ? 'k4' : 'k1'] = { key: k, res: r };
}
/** A chain read the other way round (the mockup's swap: the app asks again). */
function reverse(body) {
  const o = clone(body);
  [o.a, o.b] = [o.b, o.a];
  for (const p of o.paths || []) {
    const n = p.people.length - 1;
    p.people.reverse();
    p.links = p.links.reverse().map(l => Object.assign(l, { from: n - l.to, to: n - l.from }));
  }
  return o;
}
function lookup(a, b, mode, k) {
  let hit = PAIRS[pairKey(a, b, mode)], flip = false;
  if (!hit) { hit = PAIRS[pairKey(b, a, mode)]; flip = !!hit; }
  if (!hit) return null;
  let src = k > 1 ? hit.k4 : hit.k1 || hit.k4;
  let trimmed = false;
  if (!src) return { missing: 'more' };
  let body = clone(src.res.body);
  if (k === 1 && body.k > 1) { body.paths = body.paths.slice(0, 1); body.k = 1; trimmed = true; }
  if (flip) body = reverse(body);
  return { body, ms: src.res.ms, key: src.key, flip, trimmed };
}

/* the mockup-only states, each made from a real answer */
const MOCK = {};
(function synth() {
  const t = P.connections.teammates.body;
  MOCK.no_chain = Object.assign(clone(t), { paths: [], found: false, note: 'no chain of person links joins them', ms: 12 });
  const w = Object.assign(clone(t), { paths: [], found: false, ms: 4 });
  w.b = { kind: 'player', id: '00-0099999', name: 'A 2026 rookie', position: 'WR', links: 0 };
  w.note = 'A 2026 rookie has no person links yet (teammates come from participation, 2018-2025; coaches from snap counts)';
  MOCK.no_links = w;
})();

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
  screen: 'engine', // engine | other
  other: '',
  part: 'connections', // ask | connections | lab
  lab: 'neighbours', // neighbours | families | chemistry | benchmark
  // connections
  a: PEOPLE.get('player:00-0033873') || null,
  b: PEOPLE.get('player:00-0030506') || null,
  cmode: 'fewest',
  cx: { state: 'result', body: null, ms: 0, err: null, mock: null, chain: 0, more: false, moreBody: null, moreMissing: false },
  pick: {}, // per picker: {q, open, active, results, searching}
  // lab
  labState: 'current', // current | older | none
  nb: '00-0030506', zoom: false, group: null, hot: null,
  famQ: '', fam: null, famPerson: null, famMsg: '',
  chemMin: 100, chemSort: { col: 'epa_per_target', dir: -1 },
  bench: P.lab.benchmark.http_status === 200 ? 'real' : 'none', // real | none
  benchHot: null,
};
if (!PALS.some(p => p.id === S.pal)) S.pal = 'turf';
if (!['system', 'dark', 'light'].includes(S.mode)) S.mode = 'system';

/* ------------------------------------------------------------------ icons */
const ICON = {
  season: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 13.5h12M3.5 11l3-4 2.5 2.5L13 4"/></svg>',
  teams: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 3.5h10M3 8h10M3 12.5h6"/></svg>',
  models: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M8 1.8l5.5 3.1v6.2L8 14.2l-5.5-3.1V4.9z M8 8v6.2 M8 8l5.5-3.1 M8 8L2.5 4.9"/></svg>',
  alerts: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 11V7a4 4 0 0 1 8 0v4l1 1.5H3z M6.5 14h3"/></svg>',
  health: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1.5 8.5h3l1.5-4 3 8 1.5-4h4"/></svg>',
  playcalling: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="4" cy="12" r="2.2"/><path d="M10.5 10l3.5 3.5M14 10l-3.5 3.5M4 9.3C4 5.5 7 3.2 12 3.2M10 1.6l2 1.6-2 1.6"/></svg>',
  ask: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 3.2h12v8H8.2L5 13.8v-2.6H2z"/><path d="M6.4 5.9a1.6 1.6 0 1 1 2.2 1.5c-.4.2-.6.5-.6.9v.3"/><path d="M8 9.9v.1" stroke-linecap="round"/></svg>',
};

/* ------------------------------------------------------------------ mockbar */
const CX_CARDS = [
  ['teammates', 'Mahomes–Kelce'], ['two_steps', 'Brady–Mahomes'], ['player_coach', 'Mahomes–Belichick (4)'], ['coaches', 'Reid–Belichick'],
  ['rookie_coach', 'Cam Ward–Belichick (4)'], ['brady_rodgers', 'Brady–Rodgers'], ['brady_rodgers_strongest', 'Brady–Rodgers, strongest'],
  ['teammates_strongest_more', 'Mahomes–Kelce, strongest (4)'],
  ['idle', 'Nothing picked'], ['running', 'Running (strongest)'], ['no_chain', 'No chain *'], ['no_links', 'No person links *'],
  ['e:history_down', '503 history down'], ['e:busy', '409 busy'], ['e:not_found', '404 not found'], ['e:bad_id', '422 bad id'], ['e:same_person', '422 same person'],
];
function applyTheme() {
  document.documentElement.setAttribute('data-pal', S.pal);
  document.documentElement.setAttribute('data-mode', S.mode === 'system' ? (systemDark() ? 'dark' : 'light') : S.mode);
  if (S.part === 'lab' && S.lab === 'neighbours') requestAnimationFrame(drawMap);
}
let cxCard = 'teammates';
function syncMockbar() {
  $('#cxPick').innerHTML = CX_CARDS.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${v === cxCard}">${esc(l)}</button>`).join('');
  $$('#labPick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.labState)));
  $$('#benchPick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.bench)));
  $$('#nbPick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === (S.nb === SHEPLEY ? 'missing' : 'found'))));
  applyTheme();
}
try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (S.mode === 'system') applyTheme(); }); } catch (e) { /* old browsers */ }
$('#cxPick').addEventListener('click', e => {
  const b = e.target.closest('[data-v]'); if (!b) return;
  cxCard = b.dataset.v; S.screen = 'engine'; S.part = 'connections';
  const v = cxCard;
  stopRun();
  S.cx = { state: 'idle', body: null, ms: 0, err: null, mock: null, chain: 0, more: false, moreBody: null, moreMissing: false };
  if (v === 'idle') { S.a = null; S.b = null; }
  else if (v === 'running') { S.a = PEOPLE.get('player:00-0019596'); S.b = PEOPLE.get('player:00-0023459'); S.cmode = 'strongest'; S.cx.state = 'running'; S.cx.started = Date.now(); S.cx.hold = true; startTick(); }
  else if (v === 'no_chain' || v === 'no_links') {
    const body = MOCK[v];
    S.a = PEOPLE.get(`${body.a.kind}:${body.a.id}`); S.b = v === 'no_links' ? { id: 'player:00-0099999', kind: 'player', name: body.b.name, position: 'WR', team: 'NYJ', seasons: '2026', links: 0 } : PEOPLE.get(`${body.b.kind}:${body.b.id}`);
    S.cmode = 'fewest';
    Object.assign(S.cx, { state: 'result', body, ms: body.ms, mock: v === 'no_chain' ? 'A real answer with its chains removed: what the page shows when no chain joins two people (`found: false`, the server’s own note).' : 'Made from a real answer: a person with no links yet (the server’s own note wording).' });
  } else if (v.startsWith('e:')) {
    const k = v.slice(2), r = P.connection_errors[k];
    S.a = PEOPLE.get('player:00-0033873'); S.b = k === 'same_person' ? PEOPLE.get('player:00-0033873') : PEOPLE.get('player:00-0030506'); S.cmode = 'fewest';
    Object.assign(S.cx, { state: 'error', err: { status: r.http_status, code: r.body.error.code, message: r.body.error.message } });
  } else {
    const r = P.connections[v], b0 = r.body;
    S.a = PEOPLE.get(`${b0.a.kind}:${b0.a.id}`); S.b = PEOPLE.get(`${b0.b.kind}:${b0.b.id}`); S.cmode = b0.mode;
    showResult(1);
    if (b0.k > 1) showMore();
  }
  syncMockbar(); render(true);
});
$('#labPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.labState = b.dataset.v; S.screen = 'engine'; S.part = 'lab'; if (S.lab === 'benchmark') S.lab = 'neighbours'; syncMockbar(); render(); });
$('#benchPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.bench = b.dataset.v; S.screen = 'engine'; S.part = 'lab'; S.lab = 'benchmark'; syncMockbar(); render(); });
$('#nbPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.nb = b.dataset.v === 'missing' ? SHEPLEY : '00-0030506'; S.zoom = false; S.screen = 'engine'; S.part = 'lab'; S.lab = 'neighbours'; S.labState = 'current'; syncMockbar(); render(); });

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
      <button class="navi" data-nav="engine" aria-current="${String(S.screen === 'engine')}">${ICON.ask}<span class="grow">Ask the Engine</span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">System</div>
      <button class="navi" data-nav="other" data-what="Health">${ICON.health}<span class="grow">Health</span><span class="chip ok"><span class="ic">✓</span>OK</span></button>
    </div>
    <div class="side-foot">
      <div><span>Run lock</span><span>free</span></div>
      <div><span>Neo4j</span><span>${esc((P.meta.neo4j || {}).status || '—')}</span></div>
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

/* ------------------------------------------------------------------ header: the title, the three parts, the history graph's state */
const HIST = { up: ['ok', 'up'], down: ['err', 'down'], checking: ['flat', 'checking'], busy: ['warn', 'being rebuilt'], none: ['warn', 'not built'], off: ['flat', 'off'] };
function statusLine() {
  const h = ST.history, l = ST.links;
  const [tone, word] = HIST[ST.busy ? 'busy' : ST.history_state] || ['flat', ST.history_state];
  const counts = l && l.counts ? Object.entries(l.counts).map(([k, v]) => `${k}: ${int(v)}`) : [];
  const hTip = ['The history graph (Neo4j, its own container)', (h && h.label) || 'not built', 'Connections asks it; the lab views read files the lab job wrote', `State: ${word}`];
  const lTip = ['The person links (`nfl history-graph derive`)', (l && l.label) || 'not made yet', l ? `Made ${when(l.derived_at)}` : '', ...counts].filter(Boolean);
  return `<div class="ask-status" aria-label="The history graph">
    <span tabindex="0" data-tip="${esc(hTip.join('\n'))}"><i class="dot ${tone}"></i>${esc((h && h.label) || 'history graph: not built')} · ${esc(word)}</span>
    <span tabindex="0" data-tip="${esc(lTip.join('\n'))}"><i class="dot ${l ? 'ok' : 'warn'}"></i>${esc((l && l.label) || 'person links: not made yet')}</span>
  </div>`;
}
function partsNav() {
  const parts = [['ask', 'Ask'], ['connections', 'Connections'], ['lab', 'Graph lab']];
  const url = { ask: '/explore/ask', connections: '/explore/ask/connections', lab: '/explore/ask/lab/' + S.lab };
  return `<nav class="gl-parts" aria-label="Ask the Engine">${parts.map(([v, l]) => `<button type="button" data-part="${v}" title="${esc(url[v])}" aria-current="${S.part === v ? 'page' : 'false'}">${esc(l)}</button>`).join('')}</nav>`;
}
function renderTop() {
  const top = $('#top');
  if (S.screen === 'other') { top.innerHTML = `<div class="titleblock"><span class="eyebrow">Control room</span><h1>${esc(S.other)}</h1><span class="muted">Unchanged by AE03</span></div>`; return; }
  top.innerHTML = `<div class="titleblock"><span class="eyebrow">Explore</span><h1>Ask the Engine</h1>${partsNav()}${S.part === 'ask' ? '' : statusLine()}</div>`;
}

/* ------------------------------------------------------------------ the view */
function render(focusResult) {
  hideTip();
  renderSide(); renderTop();
  const v = $('#view');
  if (S.screen === 'other') {
    v.innerHTML = `<div class="notice accent"><span class="ic">i</span><div><b>Mockup: ${esc(S.other)} is unchanged by AE03.</b> It's drawn in the <a href="${CR_MOCKUP}" target="_blank" rel="noopener">control room mockup</a>; this page shows Explore → Ask the Engine's Connections and Graph lab.<div style="margin-top:10px"><button class="btn sm" data-nav-engine>Open Ask the Engine</button></div></div></div>`;
    return;
  }
  if (S.part === 'ask') {
    v.innerHTML = `<div class="notice accent"><span class="ic">i</span><div><b>Ask is AE01's page, unchanged.</b> It's drawn in the <a href="${ASK_MOCKUP}" target="_blank" rel="noopener">Ask the Engine mockup</a> (the question box, the answer card, the session list). AE03 adds only this switch above it: <b>Ask</b> stays at <code>/explore/ask</code>, the new parts are <code>/explore/ask/connections</code> and <code>/explore/ask/lab/&lt;view&gt;</code>.</div></div>`;
    return;
  }
  v.innerHTML = S.part === 'connections' ? connectionsView() : labView();
  if (S.part === 'lab' && S.lab === 'neighbours') { mountMap(); }
  if (focusResult) { const h = $('#glResH'); if (h) h.focus({ preventScroll: true }); }
}

/* ================================================================== Connections */
function pickerHTML(id, label, person, opts = {}) {
  const st = S.pick[id] || {};
  if (person) {
    return `<div class="gl-pick"><span class="eyebrow" id="${id}L">${esc(label)}</span>
      <div class="gl-picked" role="group" aria-labelledby="${id}L"><span class="who"><b>${esc(person.name)}</b><small>${esc([personSub(person), seasonText(person.seasons)].filter(Boolean).join(' · '))}</small></span>
      <button type="button" class="gl-x" data-unpick="${id}" aria-label="Change ${esc(label.toLowerCase())}: clear ${esc(person.name)}">×</button></div><span class="gl-hint"></span></div>`;
  }
  const open = !!st.open && (st.q || '').trim().length >= 2;
  const res = st.results || [];
  let list = '';
  if (open) {
    if (st.searching) list = `<li class="none" role="presentation">Searching…</li>`;
    else if (!res.length) list = `<li class="none" role="presentation">No player or coach matches “${esc(st.q.trim())}”.${P.people.zzqx ? '' : ''} <span class="muted">(The mockup searches only the people in the dump.)</span></li>`;
    else list = res.map((p, i) => `<li role="option" id="${id}-o${i}" data-opt="${id}:${i}" aria-selected="${i === (st.active || 0)}"><span class="nm">${esc(p.name)}</span><span class="sub">${esc([personSub(p), seasonText(p.seasons)].filter(Boolean).join(' · '))}</span>${p.links != null ? `<span class="ln">${int(p.links)} links</span>` : ''}</li>`).join('');
  }
  return `<div class="gl-pick"><label for="${id}">${esc(label)}</label>
    <div class="gl-field"><input id="${id}" type="text" role="combobox" autocomplete="off" spellcheck="false" placeholder="${esc(opts.placeholder || 'A player or a coach')}" value="${esc(st.q || '')}"
      aria-autocomplete="list" aria-expanded="${open && !!res.length}" aria-controls="${id}-list" ${open && res.length ? `aria-activedescendant="${id}-o${st.active || 0}"` : ''} aria-describedby="${id}-h">
    ${open ? `<ul class="gl-list" id="${id}-list" role="listbox" aria-label="${esc(label)}: matches">${list}</ul>` : ''}</div>
    <span class="gl-hint" id="${id}-h" aria-live="polite">${(st.q || '').trim().length === 1 ? 'Type one more letter' : open && !st.searching ? `${res.length} match${res.length === 1 ? '' : 'es'}` : ''}</span></div>`;
}
const MODE_WHY = {
  fewest: '<b>Fewest links:</b> the chain with the fewest people in it (a breadth-first search). Among equally short chains, the ones through the most shared snaps come first.',
  strongest: '<b>Strongest links:</b> every link costs 1 ÷ its snaps together, and the chain with the lowest total wins however long it is, so it often runs through coaching staffs.',
};
function connectionsView() {
  const ready = S.a && S.b && S.a.id !== S.b.id;
  const busy = S.cx.state === 'running';
  return `<div class="gl-stack">
    <p class="gl-intro">The shortest chain between any two players or coaches in the history graph (every play since 2018), person to person: <b>teammates</b> who were on the field together, a coach who <b>coached</b> a player, two coaches on the <b>same staff</b>. Never through a team, or everyone would be two steps apart.</p>
    <form class="card gl-cx" id="cxForm" autocomplete="off">
      <h2>Connect two people</h2>
      <div class="gl-pair">${pickerHTML('pkA', 'From', S.a)}
        <button type="button" class="btn gl-swap" data-swap aria-label="Swap the two people" title="Swap" ${S.a || S.b ? '' : 'disabled'}>⇄</button>
        ${pickerHTML('pkB', 'To', S.b)}</div>
      <div class="gl-modes"><span class="seg" role="group" aria-label="Which chain">${['fewest', 'strongest'].map(m => `<button type="button" data-cmode="${m}" aria-pressed="${S.cmode === m}">${m === 'fewest' ? 'Fewest links' : 'Strongest links'}</button>`).join('')}</span>
        <span class="why" id="cxWhy">${MODE_WHY[S.cmode]}</span>
        <button type="submit" class="btn primary gl-go" ${ready && !busy ? '' : 'disabled'} aria-describedby="cxWhy">${busy ? 'Connecting…' : 'Connect'}</button></div>
      ${S.a && S.b && S.a.id === S.b.id ? '<span class="gl-hint" role="status">Pick two different people.</span>' : ''}
    </form>
    <div id="cxOut" aria-live="polite">${cxOut()}</div>
  </div>`;
}
function cxOut() {
  const c = S.cx;
  if (c.state === 'idle') {
    return `<div class="card"><div class="empty"><div class="glyph" aria-hidden="true">⇄</div><h3>Pick two people</h3><p>Search a player or a coach in each box, then <b>Connect</b>. Try two former teammates, or a quarterback and a coach he never played for.</p></div></div>`;
  }
  if (c.state === 'running') {
    const s = Math.max(0, Math.floor((Date.now() - c.started) / 1000));
    return `<div class="card gl-running" aria-busy="true" role="status"><div><span class="eyebrow">Connecting</span><div class="q">${esc(S.a ? S.a.name : '')} and ${esc(S.b ? S.b.name : '')} · ${S.cmode === 'fewest' ? 'fewest links' : 'strongest links'}</div></div>
      <div class="ask-run" style="padding:0;border:0;background:none;display:flex;gap:10px;align-items:center"><span class="ask-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="secs" id="cxSecs" style="font-family:var(--font-display);font-weight:700;font-size:24px">${s} s</span></div>
      <div class="note">${S.cmode === 'strongest' ? 'A strongest search needs the person graph in memory: a cold graph can take up to half a minute the first time after it starts (the app warms it up in the background); after that a search takes milliseconds. ' : 'A cold graph can take several seconds the first time after it starts; after that a search takes well under a second. '}</div></div>`;
  }
  if (c.state === 'error') return errorCard(c.err);
  const b = c.body;
  const mock = c.mock ? `<div class="notice"><span class="ic">i</span><div><b>Mockup-only state.</b> ${esc(c.mock)}</div></div>` : '';
  if (!b.found || !b.paths.length) {
    return `${mock}<article class="card gl-res" aria-labelledby="glResH"><div class="card-h"><span class="eyebrow" style="flex-basis:100%">No chain</span><h2 id="glResH" tabindex="-1">${esc(b.a.name)} and ${esc(b.b.name)}</h2>${metaChips(b, c, null)}</div>
      <div class="card-b"><div class="ask-state st-empty"><span class="glyph" aria-hidden="true">No link</span><h3>${esc(cap(b.note || 'no chain of person links joins them'))}.</h3><p>The person links cover 2018-2025 from who was on the field, plus this season’s coaches; a player who never took a snap with anyone linked has no chain.</p></div></div></article>`;
  }
  const i = Math.min(c.chain, (c.more && c.moreBody ? c.moreBody.paths : b.paths).length - 1);
  const paths = c.more && c.moreBody ? c.moreBody.paths : b.paths;
  const p = paths[i];
  return `${mock}<article class="card gl-res" aria-labelledby="glResH">
    <div class="card-h"><span class="eyebrow" style="flex-basis:100%">${i === 0 ? (S.cmode === 'fewest' ? 'The shortest chain' : 'The strongest chain') : `Chain ${i + 1} of ${paths.length}`}</span>
      <h2 id="glResH" tabindex="-1">${esc(b.a.name)} to ${esc(b.b.name)}</h2><span class="gl-hops">${plural(p.hops, 'link')}</span></div>
    <div class="card-b">
      ${metaChips(b, c, p)}
      ${chainHTML(p)}
      <div class="gl-legend" aria-hidden="true">${Object.keys(LINK_WORD).map(t => `<span class="gl-ty-${t}"><i class="gl-sw ${t}"></i><span style="color:var(--ink-2)">${esc(LINK_WORD[t])}</span></span>`).join('')}</div>
      ${othersHTML(b, c, paths, i)}
    </div></article>`;
}
function metaChips(b, c, p) {
  const out = [];
  out.push(`<span class="chip flat"><span class="k">Mode</span> ${b.mode === 'fewest' ? 'fewest links' : 'strongest links'}</span>`);
  if (p) out.push(`<span class="chip flat" tabindex="0" data-tip="${esc(`The weakest link\nThe link with the fewest snaps in this chain: ${int(p.weakest_snaps)}${b.mode === 'strongest' ? `\nTotal cost ${p.cost} (the sum of 1 ÷ snaps; lower is stronger)` : ''}`)}"><span class="k">Weakest link</span> ${int(p.weakest_snaps)} snaps</span>`);
  out.push(`<span class="chip flat" tabindex="0" data-tip="${esc(`Time\nThe search took ${ms(b.ms)} on the history graph (${ms(c.ms)} for the whole request)`)}"><span class="k">Time</span> ${esc(ms(b.ms))}</span>`);
  if (b.asof) out.push(`<span class="chip flat" tabindex="0" data-tip="${esc(['As of', b.asof.label, `Person links made ${when(b.asof.derived_at)}`].join('\n'))}"><span class="k">As of</span> ${esc(b.asof.label)}</span>`);
  return `<div class="gl-meta">${out.join('')}</div>`;
}
function lineSVG(t) {
  return `<svg aria-hidden="true" focusable="false"><line class="gl-ln ${t} v" x1="22" y1="0" x2="22" y2="100%"/><line class="gl-ln ${t} h" x1="0" y1="23" x2="100%" y2="23"/></svg>`;
}
function chainHTML(p) {
  const n = p.people.length;
  const items = [];
  p.people.forEach((x, i) => {
    const end = i === 0 || i === n - 1;
    items.push(`<li class="gl-person${end ? ' end' : ''}${x.kind === 'coach' ? ' coach' : ''}"><b>${esc(x.name)}</b><small>${esc(personSub(x))}</small></li>`);
    if (i < n - 1) {
      const l = p.links[i];
      items.push(`<li class="gl-link">${lineSVG(l.type)}<div class="ev"><span class="ty gl-ty-${l.type}" tabindex="0" data-tip="${esc(`${cap(LINK_WORD[l.type] || l.type)}\n${LINK_HELP[l.type] || ''}\n${int(l.snaps)} snaps in all${l.seasons && l.seasons.length ? ` · ${l.seasons.length} season${l.seasons.length === 1 ? '' : 's'}` : ''}`)}"><i class="gl-sw ${l.type}" aria-hidden="true"></i><span class="lbl">${esc(LINK_WORD[l.type] || l.type)}</span></span><span class="tx">${esc(l.text)}</span></div></li>`);
    }
  });
  return `<div class="gl-chainwrap"><ol class="gl-chain" data-hops="${Math.min(p.hops, 9)}" aria-label="The chain: ${plural(p.hops, 'link')}">${items.join('')}</ol></div>`;
}
function othersHTML(b, c, paths, i) {
  if (!c.more) {
    return `<div><button type="button" class="btn sm" data-more>Show 3 more chains</button> <span class="muted" style="font-size:12px">${b.mode === 'fewest' ? 'the next shortest, strongest first' : 'the next strongest (Yen’s algorithm)'}</span></div>`;
  }
  if (c.moreMissing) return `<div class="notice"><span class="ic">i</span><div><b>Mockup:</b> the dump has only one chain for this pair. The app asks again with <code>k=4</code>.</div></div>`;
  if (paths.length < 2) return `<p class="muted" style="margin:0;font-size:13px">No other chain of this length or strength.</p>`;
  return `<div class="gl-others"><h3>${paths.length} chains</h3><ol>${paths.map((p, j) => {
    const mid = p.people.slice(1, -1);
    const via = mid.length ? 'via ' + mid.map(x => `<b>${esc(x.name)}</b>${x.kind === 'coach' ? ' (coach)' : ''}`).join(' → ') : 'directly';
    return `<li><button type="button" data-chain="${j}" aria-current="${j === i}"><span class="n">${plural(p.hops, 'link')}</span><span class="via">${via}</span><span class="tys" aria-label="${esc(p.links.map(l => LINK_WORD[l.type]).join(', '))}">${p.links.map(l => `<i class="gl-sw ${l.type} gl-ty-${l.type}"></i>`).join('')}</span><span class="w">weakest link ${int(p.weakest_snaps)} snaps${b.mode === 'strongest' ? ` · cost ${p.cost}` : ''}${j === i ? ' · shown above' : ''}</span></button></li>`;
  }).join('')}</ol></div>`;
}
function errorCard(e) {
  const cmd = /`([^`]+)`/.exec(e.message || '');
  const msg = esc((e.message || '').replace(/`([^`]+)`/g, '$1'));
  if (e.status === 503) {
    return `<div class="notice ask-banner warn" role="alert"><span class="ic">!</span><div><b>${e.code === 'history_busy' ? 'The history graph is being rebuilt.' : 'The history graph isn’t running.'}</b> ${msg}${cmd ? `<div style="margin-top:8px"><span class="cmd">${esc(cmd[1])}</span></div>` : ''}<div class="muted" style="margin-top:6px;font-size:12px">The Graph lab’s views still work: they read files the lab job wrote. (HTTP 503)</div></div></div>`;
  }
  if (e.status === 409) {
    return `<div class="notice ask-banner warn" role="alert"><span class="ic">!</span><div><b>Busy.</b> ${msg} <span class="muted">(HTTP 409)</span></div><button type="button" class="btn sm ask-x" data-retry>Try again</button></div>`;
  }
  return `<div class="notice ask-banner err" role="alert"><span class="ic">!</span><div><b>${e.status === 404 ? 'Not in the history graph.' : 'That pair can’t be connected.'}</b> ${msg} <span class="muted">(HTTP ${e.status})</span></div></div>`;
}

/* connecting (the mockup plays the dumped answers back) */
let tick = 0, done = 0;
function startTick() { clearInterval(tick); tick = setInterval(() => { const el = $('#cxSecs'); if (el) el.textContent = Math.floor((Date.now() - S.cx.started) / 1000) + ' s'; }, 250); }
function stopRun() { clearInterval(tick); clearTimeout(done); tick = done = 0; }
function showResult(k) {
  const hit = lookup(S.a.id, S.b.id, S.cmode, k);
  S.cx = { state: 'result', body: null, ms: 0, err: null, mock: null, chain: 0, more: false, moreBody: null, moreMissing: false };
  if (!hit || hit.missing) {
    S.cx.state = 'idle';
    return false;
  }
  Object.assign(S.cx, { body: hit.body, ms: hit.ms });
  return true;
}
function showMore() {
  const hit = lookup(S.a.id, S.b.id, S.cmode, 4);
  S.cx.more = true;
  if (!hit || hit.missing) { S.cx.moreMissing = true; return; }
  S.cx.moreBody = hit.body;
}
function connect() {
  if (!S.a || !S.b || S.a.id === S.b.id || S.cx.state === 'running') return;
  stopRun();
  const known = lookup(S.a.id, S.b.id, S.cmode, 1);
  S.cx = { state: 'running', started: Date.now(), body: null, ms: 0, err: null, mock: null, chain: 0, more: false };
  render(); startTick();
  done = setTimeout(() => {
    stopRun();
    if (known && !known.missing) { showResult(1); render(true); return; }
    S.cx = { state: 'idle' };
    render();
    const pairs = Object.values(P.connections).filter(r => r.http_status === 200).map(r => `${r.body.a.name}–${r.body.b.name} (${r.body.mode})`).filter((v, i, s) => s.indexOf(v) === i);
    $('#cxOut').innerHTML = `<div class="notice"><span class="ic">i</span><div><b>Mockup:</b> only the dumped pairs have answers here: ${esc(pairs.join(' · '))}. Pick one with the Mockup controls.</div></div>`;
  }, Math.min(1400, Math.max(500, (known && known.ms) || 600) * 6));
}

/* ================================================================== Graph lab */
const LAB_TABS = [['neighbours', 'Neighbours'], ['families', 'Families'], ['chemistry', 'Chemistry'], ['benchmark', 'Benchmark']];
function labView() {
  const tabs = `<nav class="gl-tabs" aria-label="Graph lab">${LAB_TABS.map(([v, l]) => `<button type="button" data-lab="${v}" title="/explore/ask/lab/${v}" aria-current="${S.lab === v ? 'page' : 'false'}">${esc(l)}</button>`).join('')}</nav>`;
  const body = { neighbours: neighboursView, families: familiesView, chemistry: chemistryView, benchmark: benchmarkView }[S.lab]();
  return `<div class="gl-stack">${tabs}${body}</div>`;
}
function asofFor(asof, job) {
  if (!asof) return '';
  const older = S.labState === 'older' || !asof.current;
  const params = asof.params ? Object.entries(asof.params).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`) : [];
  const out = [`<div class="gl-asof"><span class="chip flat" tabindex="0" data-tip="${esc(['This view as of', `Ran ${when(asof.ran_at)} on the person links ${asof.derive_id}`, ...params].join('\n'))}"><span class="k" style="color:var(--ink-3)">Ran</span>&nbsp;${esc(when(asof.ran_at))}</span>`];
  if (asof.wandb_url) out.push(`<a href="${esc(asof.wandb_url)}" target="_blank" rel="noopener">W&amp;B run ↗</a>`);
  out.push(`<span>seed ${esc(asof.params && asof.params.seed != null ? asof.params.seed : '—')}</span></div>`);
  if (older) out.push(`<div class="notice ask-banner warn" role="status"><span class="ic">!</span><div><b>Made on older person links.</b> The links were made again after this ${esc(job)} run; re-run <span class="gl-cmd">uv run nfl history-graph lab</span> to bring it up to date.${S.labState === 'older' ? ' <span class="muted">(Mockup-only: the real answer with <code>asof.current: false</code>.)</span>' : ''}</div></div>`);
  return out.join('');
}
function notBuilt(job) {
  const msg = job === 'benchmark' ? 'No benchmark yet: run `uv run nfl history-graph bench-paths`.' : `No ${job} results yet: run \`uv run nfl history-graph lab\`.`;
  const cmd = /`([^`]+)`/.exec(msg)[1];
  return `<div class="card"><div class="empty"><div class="glyph" aria-hidden="true">${job === 'benchmark' ? 'ms' : 'lab'}</div><h3>${job === 'benchmark' ? 'The benchmark hasn’t run yet' : 'Not run yet'}</h3><p>${esc(msg.replace(/`([^`]+)`/g, '$1'))}</p><div class="cmd">${esc(cmd)}</div><p class="muted" style="font-size:12px">(Mockup-only: the server’s 404 <code>not_built</code> text.)</p></div></div>`;
}

/* ---- Neighbours ---- */
const UNIT = { QB: 'off', RB: 'off', WR: 'off', TE: 'off', OL: 'off', DL: 'def', LB: 'def', DB: 'def', SPEC: 'spec' };
const UNITS = [['off', 'Offense', ['QB', 'RB', 'WR', 'TE', 'OL']], ['def', 'Defense', ['DL', 'LB', 'DB']], ['spec', 'Special teams', ['SPEC']]];
const GRP_WORD = { QB: 'QB', RB: 'RB', WR: 'WR', TE: 'TE', OL: 'O-line', DL: 'D-line', LB: 'LB', DB: 'DB', SPEC: 'Specialists' };
const MAP = P.lab.map.body;
const BYID = new Map(MAP.points.map(p => [p.player_id, p]));
function nbAnswer() {
  const hit = Object.values(P.lab.neighbours).find(r => r.body.player_id === S.nb);
  return hit ? hit.body : null;
}
function neighboursView() {
  if (S.labState === 'none') return notBuilt('neighbours');
  const a = nbAnswer();
  const asof = MAP.asof;
  const ev = (asof.summary && asof.summary.explained_variance_2d) || [];
  const kept = ev.reduce((s, x) => s + x, 0);
  const pt = BYID.get(S.nb);
  let lists;
  if (a && !a.found) {
    const who = PEOPLE.get('player:' + S.nb);
    lists = `<div class="notice"><span class="ic">i</span><div><b>${esc(who ? who.name : 'That player')} isn’t in the map.</b> The neighbours job keeps players with at least ${esc(asof.params.min_snaps)} snaps since 2018 who have teammate links; ${who ? 'he' : 'this player'} has fewer snaps, so there’s no fingerprint to compare. Connections still finds his chains.</div></div>`;
  } else if (!a) {
    lists = `<div class="notice"><span class="ic">i</span><div><b>Mockup:</b> the dump has neighbours for five players (Travis Kelce, Patrick Mahomes, Justin Jefferson, Josh Allen, Cam Ward) and one who isn’t in the map (Dakoda Shepley). ${pt ? `${esc(pt.name)} is on the map below; the app would ask <code>GET /api/ask/lab/neighbours?player=player:${esc(pt.player_id)}</code>.` : ''}</div></div>`;
  } else {
    const me = a.player;
    lists = `<div class="gl-me"><b>${esc(me.name)}</b><span>${esc([me.position, me.team, `${int(me.snaps)} snaps since 2018`].join(' · '))}</span></div>
      <div class="gl-two">${rankCard(`Same position (${esc(me.position)})`, a.position, 'pos')}${rankCard('Anyone', a.overall, 'all')}</div>`;
  }
  const quick = ['00-0030506', '00-0033873', '00-0036322', '00-0034857', '00-0040676'].map(id => BYID.get(id)).filter(Boolean);
  return `<div class="gl-head"><h2>Neighbours</h2>${asofFor(asof, 'neighbours')}</div>
    <p class="gl-intro"><b>Players most like him by who he played with, and how.</b> Each player with at least ${esc(asof.params.min_snaps)} snaps gets a ${esc(asof.params.dimension)}-number fingerprint (a FastRP embedding) from his teammate links, weighted by snaps together, and his QB → receiver links; his neighbours are the players with the closest fingerprints (cosine similarity, 1 = the same).</p>
    <div class="card gl-cx" style="gap:10px"><div class="gl-pair">${pickerHTML('pkN', 'Player', null, { placeholder: 'Search a player' })}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center"><span class="eyebrow">In the dump</span>${quick.map(p => `<button type="button" class="ask-chip" data-nbpick="${p.player_id}" aria-pressed="${S.nb === p.player_id}">${esc(p.name)}</button>`).join('')}</div></div>
    <div id="nbOut" aria-live="polite" class="gl-stack">${lists}</div>
    <section class="card gl-mapcard" aria-labelledby="mapH"><div class="card-h"><h2 id="mapH">The map</h2><span class="muted">${int(MAP.points.length)} players</span>
      <span class="right">${pt ? `<button type="button" class="btn sm" data-zoom aria-pressed="${S.zoom}">${S.zoom ? 'Show everyone' : `Zoom to ${esc(pt.name)}`}</button>` : ''}</span></div>
      <div class="card-b">
        <div class="gl-groups" role="group" aria-label="Highlight a position group">${UNITS.map(([u, l, gs]) => `<span class="u"><span><i class="gl-u-${u}"></i>${esc(l)}</span>${gs.map(g => `<button type="button" data-grp="${g}" aria-pressed="${S.group === g}">${esc(GRP_WORD[g])}</button>`).join('')}</span>`).join('')}</div>
        <div class="gl-map" id="glMap"><canvas id="glCanvas" role="img" aria-label="${esc(`A map of ${MAP.points.length} players, coloured by offense, defense and special teams${pt ? `; ${pt.name} and his neighbours are ringed` : ''}. The lists above are the accessible answer.`)}"></canvas><div class="lbls" id="glLbls"></div></div>
        <p class="gl-mapnote"><b>What the map shows:</b> each dot is a player, placed by squeezing his ${esc(asof.params.dimension)}-number fingerprint into two (PCA). The two axes keep only ${pct(kept)} of the variation (${ev.map(x => pct(x, 1)).join(' + ')}), so near on the map doesn’t always mean similar: the lists are the real answer. Hover a dot for the player; click one to pick him.</p>
      </div></section>`;
}
function rankCard(title, rows, key) {
  if (!rows || !rows.length) return `<section class="card"><div class="card-h"><h2>${title}</h2></div><p class="card-b muted">No neighbours.</p></section>`;
  return `<section class="card" aria-label="${esc(title.replace(/<[^>]+>/g, ''))}"><div class="card-h"><h2>${title}</h2><span class="muted">similarity</span></div>
    <ol class="gl-rank">${rows.map(r => `<li data-hot="${r.neighbour_id}"><span class="r">${r.rank}</span><span class="nm"><button type="button" data-nbpick="${r.neighbour_id}" title="Show ${esc(r.neighbour_name)}’s neighbours">${esc(r.neighbour_name)}</button><small class="${r.snaps_together ? '' : 'never'}">${esc(r.neighbour_position)} · ${esc(r.neighbour_team || '—')} · ${r.snaps_together ? `${int(r.snaps_together)} snaps together` : 'never on the field together'}</small></span>
      <span class="gl-bar" role="img" aria-label="similarity ${num(r.similarity)}"><b style="width:${Math.max(0, Math.min(1, r.similarity)) * 100}%"></b></span><span class="v">${num(r.similarity)}</span></li>`).join('')}</ol></section>`;
}

/* the map: one canvas, ~4.3k dots; picked player and neighbours ringed, labels as HTML */
let mapGeo = null;
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function mountMap() {
  const wrap = $('#glMap'); if (!wrap) return;
  drawMap();
}
function domain() {
  const pt = BYID.get(S.nb);
  if (S.zoom && pt) return { x0: pt.x - 0.38, x1: pt.x + 0.38, y0: pt.y - 0.26, y1: pt.y + 0.26 };
  return { x0: -1.42, x1: 1.42, y0: -1.25, y1: 1.34 };
}
function drawMap() {
  const cv = $('#glCanvas'); if (!cv) return;
  const r = cv.getBoundingClientRect(); if (!r.width) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
  const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const d = domain(), pad = 14;
  const sx = x => pad + (x - d.x0) / (d.x1 - d.x0) * (r.width - pad * 2);
  const sy = y => pad + (1 - (y - d.y0) / (d.y1 - d.y0)) * (r.height - pad * 2);
  const col = { off: cssVar('--s2'), def: cssVar('--s1'), spec: cssVar('--s3') };
  const panel = cssVar('--panel'), ink = cssVar('--ink'), accent = cssVar('--accent');
  g.clearRect(0, 0, r.width, r.height);
  const a = nbAnswer();
  const ring = new Set(a && a.found ? [...a.position, ...a.overall].map(x => x.neighbour_id) : []);
  const rad = S.zoom ? 3.4 : 2.4;
  const xy = [];
  // pass 1: everyone (faded when a group is highlighted)
  for (const p of MAP.points) {
    const x = sx(p.x), y = sy(p.y);
    xy.push([x, y, p]);
    if (x < -5 || y < -5 || x > r.width + 5 || y > r.height + 5) continue;
    const on = !S.group || p.grp === S.group;
    g.globalAlpha = on ? (S.group ? 0.95 : 0.62) : 0.1;
    g.fillStyle = col[UNIT[p.grp] || 'spec'];
    g.beginPath(); g.arc(x, y, rad, 0, Math.PI * 2); g.fill();
  }
  g.globalAlpha = 1;
  // pass 2: the neighbours (a surface ring + an ink ring), then the picked player
  for (const id of ring) {
    const p = BYID.get(id); if (!p) continue;
    const x = sx(p.x), y = sy(p.y);
    g.fillStyle = col[UNIT[p.grp] || 'spec']; g.strokeStyle = panel; g.lineWidth = 2;
    g.beginPath(); g.arc(x, y, rad + 2.2, 0, Math.PI * 2); g.fill(); g.stroke();
    g.strokeStyle = ink; g.lineWidth = 1.2; g.beginPath(); g.arc(x, y, rad + 4.4, 0, Math.PI * 2); g.stroke();
  }
  const me = BYID.get(S.nb);
  if (me) {
    const x = sx(me.x), y = sy(me.y);
    // ink, not a series colour: the picked player must not read as offense or defense
    g.fillStyle = ink; g.strokeStyle = panel; g.lineWidth = 2.5;
    g.beginPath(); g.arc(x, y, rad + 4, 0, Math.PI * 2); g.fill(); g.stroke();
    g.strokeStyle = accent; g.lineWidth = 2; g.beginPath(); g.arc(x, y, rad + 7.5, 0, Math.PI * 2); g.stroke();
  }
  const hot = S.hot && BYID.get(S.hot);
  if (hot) { const x = sx(hot.x), y = sy(hot.y); g.strokeStyle = accent; g.lineWidth = 2.5; g.beginPath(); g.arc(x, y, rad + 7, 0, Math.PI * 2); g.stroke(); }
  mapGeo = { xy, w: r.width, h: r.height };
  // labels: the picked player, and the hovered list row
  const lbl = [];
  const put = (p, cls) => { const x = sx(p.x), y = sy(p.y); const left = x > r.width - 150; lbl.push(`<span class="lbl ${cls}${left ? ' left' : ''}" style="left:${x}px;top:${y}px">${esc(p.name)}</span>`); };
  if (me) put(me, 'me');
  if (hot && hot !== me) put(hot, '');
  $('#glLbls').innerHTML = lbl.join('');
}
function nearest(mx, my) {
  if (!mapGeo) return null;
  let best = null, bd = 14 * 14;
  for (const [x, y, p] of mapGeo.xy) { const dx = x - mx, dy = y - my, dd = dx * dx + dy * dy; if (dd < bd && (!S.group || p.grp === S.group)) { bd = dd; best = p; } }
  return best;
}
let mapRaf = 0;
document.addEventListener('pointermove', e => {
  const cv = e.target.closest && e.target.closest('#glCanvas');
  if (!cv) return;
  cancelAnimationFrame(mapRaf);
  mapRaf = requestAnimationFrame(() => {
    const r = cv.getBoundingClientRect();
    const p = nearest(e.clientX - r.left, e.clientY - r.top);
    if (p) showTip(`<div class="th">${esc(p.name)}</div><div>${esc(GRP_WORD[p.grp] || p.grp)} · ${esc(p.team)}</div><div class="muted">Click to show his neighbours</div>`, e.clientX, e.clientY);
    else hideTip();
  });
});
window.addEventListener('resize', () => { if (S.part === 'lab' && S.lab === 'neighbours') drawMap(); });

/* ---- Families ---- */
const FAM = P.lab.families.body;
const FAM_MEMBERS = { [P.lab.family_of_mahomes.body.family]: P.lab.family_of_mahomes.body.members };
function familiesView() {
  if (S.labState === 'none') return notBuilt('families');
  const asof = FAM.asof, sm = asof.summary || {};
  const q = S.famQ.trim().toLowerCase();
  const list = FAM.families.filter(f => !q || [f.name, f.team, ...(f.top_players || []), ...(f.top_coaches || [])].some(s => String(s || '').toLowerCase().includes(q)));
  const sel = S.fam != null ? FAM.families.find(f => f.community === S.fam) : null;
  return `<div class="gl-head"><h2>Football families</h2>${asofFor(asof, 'families')}</div>
    <p class="gl-intro"><b>Groups of players and coaches who shared the most snaps</b>, found by an algorithm (Leiden communities on the teammate and coaching links), not drawn by hand. Each family is named after its biggest team-era and the side of the ball most of it played on (“core” when it’s both). ${int(sm.families)} families of at least ${esc(asof.params.min_size)} people (of ${int(sm.communities)} groups found); the biggest has ${int(sm.largest)}, the middle one ${int(sm.median_size)}.</p>
    <div class="gl-tools"><div class="gl-filter"><label for="famQ">Filter families</label><input id="famQ" type="search" placeholder="A name or a team: KC, Kelce" value="${esc(S.famQ)}" aria-describedby="famN"></div>
      ${pickerHTML('pkF', 'Find someone’s family', S.famPerson, { placeholder: 'A player or a coach' })}</div>
    ${S.famMsg ? `<div class="notice"><span class="ic">i</span><div>${S.famMsg}</div></div>` : ''}
    <div class="gl-fam">
      <section class="card" aria-labelledby="famH"><div class="card-h"><h2 id="famH">Families</h2><span class="muted" id="famN" aria-live="polite">${list.length === FAM.families.length ? `${list.length}, biggest first` : `${list.length} of ${FAM.families.length}`}</span></div>
        ${list.length ? `<ul class="gl-famlist">${list.map(f => `<li><button type="button" data-fam="${f.community}" aria-current="${S.fam === f.community}"><span class="nm">${esc(f.name)} <span class="gl-unit">${esc(f.unit)}</span></span><span class="sz">${f.size}<small>people</small></span><span class="tp">${esc([`${plural(f.players, 'player')}, ${plural(f.coaches, 'coach').replace('coachs', 'coaches')}`, (f.top_players || []).slice(0, 3).join(', ')].filter(Boolean).join(' · '))}</span></button></li>`).join('')}</ul>` : `<p class="none gl-famlist">No family matches “${esc(S.famQ)}”.</p>`}</section>
      <div>${sel ? familyCard(sel) : `<div class="card"><div class="empty"><div class="glyph" aria-hidden="true">fam</div><h3>Pick a family</h3><p>Or find someone’s family by name: the family he or she belongs to opens with its members.</p></div></div>`}</div>
    </div>`;
}
function familyCard(f) {
  const mem = FAM_MEMBERS[f.community];
  const meId = S.famPerson ? S.famPerson.id : null;
  let body;
  if (!mem) body = `<div class="card-b"><div class="notice"><span class="ic">i</span><div><b>Mockup:</b> the dump has members for one family (${esc((FAM.families.find(x => x.community === P.lab.family_of_mahomes.body.family) || {}).name)}, Patrick Mahomes’). The app asks <code>GET /api/ask/lab/families?family=${f.community}</code>.</div></div></div>`;
  else {
    const players = mem.filter(m => m.kind === 'player'), coaches = mem.filter(m => m.kind === 'coach');
    const row = m => `<tr class="${`${m.kind}:${m.id}` === meId ? 'me' : ''}"><td class="who">${esc(m.name)}${`${m.kind}:${m.id}` === meId ? ' <span class="gl-sr">(the person you searched)</span>' : ''}</td><td>${esc(m.kind === 'coach' ? 'coach' : m.position)}</td><td class="r num">${m.snaps == null ? '<span class="muted">—</span>' : int(m.snaps)}</td><td class="r num muted">${m.snaps_total == null ? '—' : int(m.snaps_total)}</td></tr>`;
    body = `<div class="gl-members" tabindex="0" aria-label="Members"><table class="tbl"><thead><tr><th scope="col">Name</th><th scope="col">Position</th><th scope="col" class="r">Snaps in this era</th><th scope="col" class="r">Since 2018</th></tr></thead><tbody>
      <tr class="sep"><td colspan="4">Players · ${players.length}</td></tr>${players.map(row).join('')}
      ${coaches.length ? `<tr class="sep"><td colspan="4">Coaches · ${coaches.length}</td></tr>${coaches.map(row).join('')}` : ''}</tbody></table></div>`;
  }
  return `<section class="card" aria-labelledby="famSelH"><div class="card-h"><h2 id="famSelH" tabindex="-1">${esc(f.name)}</h2><span class="gl-unit">${esc(f.unit)}</span></div>
    <div class="card-b" style="display:flex;flex-direction:column;gap:8px;padding-bottom:12px">
      <div class="gl-meta"><span class="chip flat"><span class="k">People</span> ${f.size}</span><span class="chip flat"><span class="k">Players</span> ${f.players}</span><span class="chip flat"><span class="k">Coaches</span> ${f.coaches}</span><span class="chip flat"><span class="k">Seasons</span> ${f.first_season === f.last_season ? f.first_season : `${f.first_season}–${f.last_season}`}</span><span class="chip flat"><span class="k">Team</span> ${esc(f.team)}</span></div>
      ${(f.top_coaches || []).length ? `<div style="font-size:13px;color:var(--ink-2)"><b style="color:var(--ink)">Coaches:</b> ${esc(f.top_coaches.join(', '))}</div>` : ''}
      <div style="font-size:12px;color:var(--ink-3)">Members: the players by their snaps in this era (the family’s team and seasons; all their snaps since 2018 beside it), then the coaches.</div></div>${body}</section>`;
}

/* ---- Chemistry ---- */
const CHEM = { 100: P.lab.chemistry.body, 300: P.lab.chemistry_300.body };
function chemAnswer() {
  if (CHEM[S.chemMin]) return { body: CHEM[S.chemMin], mock: false };
  const base = S.chemMin > 300 ? CHEM[300] : CHEM[100];
  return { body: Object.assign(clone(base), { min_targets: S.chemMin, duos: base.duos.filter(d => d.targets >= S.chemMin) }), mock: true };
}
const DUO_COLS = [
  ['qb', 'QB', 't'], ['receiver', 'Receiver', 't'], ['position', 'Pos', 't'], ['targets', 'Targets', 'n'], ['catches', 'Catches', 'n'], ['yards', 'Yards', 'n'],
  ['touchdowns', 'TD', 'n'], ['epa_per_target', 'EPA / target', 'n'], ['catch_rate', 'Catch rate', 'n'], ['first_season', 'Seasons', 'n'], ['teams', 'Teams', 't'],
];
function chemistryView() {
  if (S.labState === 'none') return notBuilt('chemistry');
  const { body: c, mock } = chemAnswer();
  const sort = S.chemSort;
  const duos = [...c.duos].sort((x, y) => {
    const a = x[sort.col], b = y[sort.col];
    const v = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b));
    return v * sort.dir;
  });
  const th = DUO_COLS.map(([k, l, t]) => {
    const s = sort.col === k ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none';
    return `<th scope="col" class="${t === 'n' ? 'r' : ''}" aria-sort="${s}"><button type="button" data-csort="${k}">${esc(l)}<span class="arr" aria-hidden="true">${s === 'ascending' ? '▲' : s === 'descending' ? '▼' : ''}</span></button></th>`;
  }).join('');
  const cell = (d, k) => {
    if (k === 'epa_per_target') return `<td class="r num"><b>${num(d[k], 3)}</b></td>`;
    if (k === 'catch_rate') return `<td class="r num">${pct(d[k], 1)}</td>`;
    if (k === 'first_season') return `<td class="r num">${d.first_season === d.last_season ? d.first_season : `${d.first_season}–${d.last_season}`}</td>`;
    if (k === 'teams') return `<td>${esc((d.teams || []).join(', '))}</td>`;
    if (k === 'qb' || k === 'receiver') return `<td class="who">${esc(d[k])}</td>`;
    if (k === 'position') return `<td>${esc(d[k])}</td>`;
    return `<td class="r num">${int(d[k])}</td>`;
  };
  return `<div class="gl-head"><h2>Chemistry</h2>${asofFor(c.asof, 'chemistry')}</div>
    <p class="gl-intro"><b>Quarterback → receiver duos, ranked by EPA per target</b> (expected points added each time that QB threw to that receiver, 2018 on, playoffs included), with a minimum number of targets so a hot month doesn’t top the list. Beside them, <b>the most central receivers and QBs</b>: PageRank on the QB → receiver target graph, where a receiver scores high when many QBs, and central ones, threw to him a lot.</p>
    <section class="card" aria-labelledby="duoH"><div class="card-h"><h2 id="duoH">The duos</h2><span class="muted">${c.duos.length === 50 ? 'the top 50' : `${c.duos.length}`} with ${int(c.min_targets)}+ targets</span>
      <span class="right"><span class="eyebrow">Min. targets</span><span class="seg" role="group" aria-label="Minimum targets">${[100, 200, 300, 500].map(n => `<button type="button" data-cmin="${n}" aria-pressed="${S.chemMin === n}">${n}</button>`).join('')}</span></span></div>
      ${mock ? '<div class="card-b" style="padding-bottom:0"><span class="gl-mock">Mockup-only: filtered from the dumped answer; the app asks <code>min_targets=' + S.chemMin + '</code>.</span></div>' : ''}
      <div class="gl-tablewrap" tabindex="0" aria-label="The duos"><table class="tbl gl-tbl"><thead><tr>${th}</tr></thead><tbody>${duos.map(d => `<tr>${DUO_COLS.map(([k]) => cell(d, k)).join('')}</tr>`).join('')}</tbody></table></div></section>
    <div class="gl-two">${prCard('Most central receivers', c.receivers, 'receiver')}${prCard('Most central quarterbacks', c.qbs, 'qb')}</div>`;
}
function prCard(title, rows, role) {
  const max = Math.max(...rows.map(r => r.score), 1e-9);
  return `<section class="card" aria-label="${esc(title)}"><div class="card-h"><h2>${esc(title)}</h2><span class="muted">PageRank</span></div>
    <ol class="gl-pr">${rows.map(r => `<li><span class="r">${r.rank}</span><span class="nm"><b>${esc(r.name)}</b><small>${esc(r.position)} · ${role === 'receiver' ? `${plural(r.qbs, 'QB')} threw to him` : `threw to ${plural(r.receivers, 'receiver')}`}</small></span><span class="gl-bar" role="img" aria-label="score ${num(r.score, 2)}"><b style="width:${(r.score / max) * 100}%"></b></span><span class="v">${num(r.score, 2)}</span></li>`).join('')}</ol></section>`;
}

/* ---- Benchmark (the real answer: `nfl history-graph bench-paths`) ---- */
const FAMILY = { pair: ['One pair', 'One start, one finish: the question Connections asks'], source: ['One source', 'One start, every finish (single-source shortest paths)'], all: ['All pairs', 'Every start, every finish: the APSP problem the 2026 paper is about'] };
const DASH = ['', '7 4', '2 4', '10 3 2 3', '1 3', '12 3 3 3 3 3'];
const ALG_NAME = {
  'cypher-shortest': 'Cypher SHORTEST (two-way BFS)', 'gds-dijkstra-pair': 'GDS Dijkstra (one pair)', 'scipy-dijkstra': 'scipy Dijkstra',
  'dijkstra-heap': 'Python Dijkstra (heap)', bmssp: 'Duan et al. 2025 BMSSP (our Python)', 'bmssp-constant-degree': 'BMSSP on the constant-degree graph (our Python)',
  'gds-delta-stream': 'GDS Delta-Stepping (stream)', 'gds-delta-stats': 'GDS Delta-Stepping (stats only)', 'scipy-dijkstra-all': 'scipy Dijkstra from every source',
  'floyd-warshall-numba': 'Floyd–Warshall (numba)', 'scipy-floyd-warshall': 'scipy Floyd–Warshall', 'gds-all-pairs': 'GDS all pairs',
};
const algName = a => ALG_NAME[a] || a;
const famVar = f => (f === 'pair' ? '--s2' : f === 'source' ? '--s1' : '--s3');
function benchmarkView() {
  const real = P.lab.benchmark.http_status === 200 ? P.lab.benchmark.body : null;
  if (S.bench === 'none' || !real) {
    return `<div class="gl-head"><h2>The path benchmark</h2></div>${benchIntro(null)}${notBuilt('benchmark')}`;
  }
  const b = real;
  const algs = [...new Set(b.rows.map(r => r.algorithm))];
  const famOf = a => b.rows.find(r => r.algorithm === a).family;
  const dashOf = {};
  for (const f of Object.keys(FAMILY)) algs.filter(a => famOf(a) === f).forEach((a, i) => { dashOf[a] = DASH[i % DASH.length]; });
  const m = b.machine || {};
  const machine = [m.cpu_count != null ? `${m.cpu_count} CPUs` : '', m.ram_gb != null ? `${m.ram_gb} GB RAM` : '', m.neo4j ? `Neo4j ${m.neo4j}` : '', m.gds ? `GDS ${m.gds}` : ''].filter(Boolean).join(', ');
  const skipped = b.rows.filter(r => r.median_ms == null).length;
  return `<div class="gl-head"><h2>The path benchmark</h2><div class="gl-asof"><span class="chip flat">Ran ${esc(when(b.ran_at || b.started_at))}</span>${b.smoke ? '<span class="chip warn">smoke run</span>' : ''}<span>seed ${esc(b.seed)}</span>${b.seconds != null ? `<span>${esc(ms(b.seconds * 1000))} in all</span>` : ''}<span>${esc(machine)}</span>${b.wandb_url ? `<a href="${esc(b.wandb_url)}" target="_blank" rel="noopener">W&amp;B run ↗</a>` : ''}</div></div>
    ${benchIntro(b)}
    ${b.smoke ? `<div class="notice" role="note"><span class="ic">i</span><div><b>A quick check run.</b> This is a smoke run (${esc(b.pairs)} pairs, ${esc(b.sources)} sources, ${esc(b.repeats)} repeat${b.repeats === 1 ? '' : 's'}, an all-pairs budget of ${esc(b.budget_s)} s): the shape is right, the numbers are rough until the full run replaces it.</div></div>` : ''}
    ${b.agrees_all === false ? '<div class="notice ask-banner err" role="alert"><span class="ic">!</span><div><b>Not every algorithm agreed.</b> At least one returned different path lengths from the reference: see “Agrees” below.</div></div>' : ''}
    <section class="card" aria-labelledby="bchH"><div class="card-h"><h2 id="bchH">Median time by graph size</h2><span class="muted">both axes on a log scale: each grid line up is 10× slower</span></div>
      <div class="card-b" style="display:flex;flex-direction:column;gap:14px">${benchChart(b, algs, famOf, dashOf)}
        <div class="gl-blegend">${Object.entries(FAMILY).map(([f, [l, help]]) => { const mine = algs.filter(a => famOf(a) === f); return mine.length ? `<div><h4><i style="background:var(${famVar(f)})"></i>${esc(l)}</h4><div class="muted" style="font-size:11.5px;margin-bottom:4px">${esc(help)}</div>${mine.map(a => `<button type="button" data-balg="${esc(a)}" aria-pressed="${S.benchHot === a}"><svg width="28" height="8" aria-hidden="true"><line x1="1" y1="4" x2="27" y2="4" style="stroke:var(${famVar(f)});stroke-width:2;stroke-dasharray:${dashOf[a]}"/></svg>${esc(algName(a))}</button>`).join('')}</div>` : ''; }).join('')}</div></div></section>
    <section class="card" aria-labelledby="btH"><div class="card-h"><h2 id="btH">Every run</h2><span class="muted">“agrees” = the same path lengths as the reference${skipped ? `; ${skipped} skipped (predicted past the time budget)` : ''}</span></div>
      <div class="gl-tablewrap" tabindex="0" aria-label="Benchmark results"><table class="tbl gl-tbl"><thead><tr>${['Algorithm', 'Kind', 'People', 'Links', 'Runs', 'Median', 'p90', 'Fastest', 'Agrees', 'Note'].map((h, i) => `<th scope="col"${i >= 2 && i <= 7 ? ' class="r"' : ''} style="padding:9px 10px">${h}</th>`).join('')}</tr></thead>
      <tbody>${b.rows.map(r => `<tr><td class="who" title="${esc(r.algorithm)}">${esc(algName(r.algorithm))}</td><td>${esc((FAMILY[r.family] || [r.family])[0])}</td><td class="r num">${int(r.size_n)}</td><td class="r num">${int(r.size_m)}</td><td class="r num">${r.runs}</td><td class="r num">${r.median_ms == null ? '<span class="muted">skipped</span>' : `<b>${esc(ms(r.median_ms))}</b>`}</td><td class="r num">${esc(ms(r.p90_ms))}</td><td class="r num">${esc(ms(r.min_ms))}</td><td>${r.agrees == null ? '<span class="muted">—</span>' : r.agrees ? '<span class="gl-agree ok">✓ yes</span>' : '<span class="gl-agree no">✕ no</span>'}</td><td class="muted note">${esc(r.note || '')}</td></tr>`).join('')}</tbody></table></div></section>`;
}
function benchIntro(b) {
  const sizes = (b && b.sizes) || [];
  const samples = sizes.filter(x => x.requested != null).map(x => int(x.n));
  const full = sizes.find(x => x.requested == null);
  const where = b ? `on ${samples.length ? `samples of ${samples.join(', ')} people and ` : ''}the whole graph${full ? ` (${int(full.n)} people, ${int(full.m)} links)` : ''}` : 'on samples of the graph and the whole of it';
  return `<p class="gl-intro"><b>How fast shortest-path algorithms really are on our person graph</b> (players and coaches joined by their links), ${where}. Connections asks the one-pair question, which a two-way breadth-first search answers in milliseconds; the all-pairs algorithms grow with the cube of the size, which is why the 2026 sub-cubic APSP result (Alman &amp; Vassilevska Williams) is a landmark for theory but saves nothing here. The history-graph guide explains both.</p>`;
}
function benchChart(b, algs, famOf, dashOf) {
  const W = 760, H = 380, L = 64, R = 18, T = 16, B = 46;
  const rows = b.rows.filter(r => r.median_ms != null && r.median_ms > 0);
  const xs = rows.map(r => r.size_n), ys = rows.map(r => r.median_ms);
  const lx0 = Math.log10(Math.min(...xs) * 0.85), lx1 = Math.log10(Math.max(...xs) * 1.15);
  const ly0 = Math.floor(Math.log10(Math.min(...ys))), ly1 = Math.max(ly0 + 1, Math.ceil(Math.log10(Math.max(...ys))));
  const X = n => L + (Math.log10(n) - lx0) / (lx1 - lx0) * (W - L - R);
  const Y = v => T + (1 - (Math.log10(v) - ly0) / (ly1 - ly0)) * (H - T - B);
  const yt = []; for (let e = ly0; e <= ly1; e++) yt.push(Math.pow(10, e));
  const xt = [...new Set((b.sizes || []).map(s => s.n))];
  const lines = algs.map(a => {
    const rs = rows.filter(r => r.algorithm === a).sort((x, y) => x.size_n - y.size_n);
    const f = famOf(a);
    const dim = S.benchHot && S.benchHot !== a ? 'dim' : '';
    const d = rs.map((r, i) => `${i ? 'L' : 'M'}${X(r.size_n).toFixed(1)},${Y(r.median_ms).toFixed(1)}`).join('');
    const pts = rs.map(r => `<circle class="pt" cx="${X(r.size_n).toFixed(1)}" cy="${Y(r.median_ms).toFixed(1)}" r="4" style="fill:var(${famVar(f)})"/><circle class="hit" tabindex="0" cx="${X(r.size_n).toFixed(1)}" cy="${Y(r.median_ms).toFixed(1)}" r="11" data-tip="${esc(`${algName(a)}\n${(FAMILY[f] || [f])[0]} · ${int(r.size_n)} people, ${int(r.size_m)} links\nMedian ${ms(r.median_ms)} · p90 ${ms(r.p90_ms)} · fastest ${ms(r.min_ms)}\n${r.runs} runs${r.agrees === false ? ' · DISAGREES with the reference' : ''}`)}" aria-label="${esc(`${algName(a)}, ${int(r.size_n)} people: median ${ms(r.median_ms)}`)}"/>`).join('');
    return `<g class="${dim}"><path class="ln" d="${d}" style="stroke:var(${famVar(f)});stroke-dasharray:${dashOf[a]}"/>${pts}</g>`;
  }).join('');
  return `<div class="gl-chart"><svg viewBox="0 0 ${W} ${H}" role="group" aria-label="Median milliseconds by number of people, log-log, one line per algorithm">
    <g class="grid">${yt.map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/>`).join('')}${xt.map(n => `<line y1="${T}" y2="${H - B}" x1="${X(n)}" x2="${X(n)}"/>`).join('')}</g>
    ${yt.map(v => `<text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${esc(msTick(v))}</text>`).join('')}
    ${xt.map(n => `<text x="${X(n)}" y="${H - B + 16}" text-anchor="middle">${int(n)}</text>`).join('')}
    <text class="axis-t" x="${(L + W - R) / 2}" y="${H - 8}" text-anchor="middle">people in the graph (log scale)</text>
    <text class="axis-t" transform="translate(14 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">median time (log scale)</text>
    ${lines}</svg></div>`;
}

/* ------------------------------------------------------------------ the pickers (the mockup searches the dump) */
const pickTimers = {};
function search(id, q, kind) {
  const st = S.pick[id] = Object.assign(S.pick[id] || {}, { q, open: true, active: 0 });
  clearTimeout(pickTimers[id]);
  if (q.trim().length < 2) { st.results = []; st.searching = false; refreshPicker(id); return; }
  st.searching = true; refreshPicker(id);
  pickTimers[id] = setTimeout(() => { // the app: debounced 200 ms, then GET /api/ask/people?q=
    const toks = q.trim().toLowerCase().split(/\s+/);
    st.results = [...PEOPLE.values()].filter(p => (!kind || p.kind === kind) && toks.every(t => p.name.toLowerCase().includes(t))).sort((a, b) => (b.links || 0) - (a.links || 0)).slice(0, 10);
    st.searching = false; refreshPicker(id);
  }, 200);
}
function refreshPicker(id) {
  const input = $('#' + id); if (!input) return;
  const pos = input.selectionStart;
  const wrap = input.closest('.gl-pick');
  const person = null;
  const label = wrap.querySelector('label').textContent;
  const tmp = document.createElement('div');
  tmp.innerHTML = pickerHTML(id, label, person, { placeholder: input.placeholder });
  wrap.replaceWith(tmp.firstElementChild);
  const again = $('#' + id); again.focus(); try { again.setSelectionRange(pos, pos); } catch (e) { /* fine */ }
}
function choose(id, p) {
  S.pick[id] = {};
  if (id === 'pkA') S.a = p;
  else if (id === 'pkB') S.b = p;
  else if (id === 'pkN') { S.nb = p.id.split(':')[1]; S.hot = null; syncMockbar(); render(); return; }
  else if (id === 'pkF') { pickFamilyOf(p); return; }
  if (id === 'pkA' || id === 'pkB') { S.cx = { state: 'idle' }; }
  render();
  const next = id === 'pkA' && !S.b ? '#pkB' : id === 'pkB' && !S.a ? '#pkA' : '#cxForm [type=submit]';
  const el = $(next); if (el) el.focus();
}
function pickFamilyOf(p) {
  const m = P.lab.family_of_mahomes.body.members;
  const hit = m.find(x => `${x.kind}:${x.id}` === p.id);
  S.famPerson = p; S.famMsg = '';
  if (hit) { S.fam = P.lab.family_of_mahomes.body.family; }
  else { S.fam = null; S.famMsg = `<b>Mockup:</b> the dump answers “find someone’s family” for Patrick Mahomes and Andy Reid. The app asks <code>GET /api/ask/lab/families?person=${esc(p.id)}</code>; a person in no family gets <code>person_found: false</code>.`; }
  render();
  const h = $('#famSelH'); if (h) h.focus();
}

/* ------------------------------------------------------------------ events */
const view = $('#view'), top = $('#top');
top.addEventListener('click', e => { const b = e.target.closest('[data-part]'); if (!b) return; S.part = b.dataset.part; render(); });
view.addEventListener('input', e => {
  const t = e.target;
  if (t.matches('.gl-pick input')) { search(t.id, t.value, t.id === 'pkN' ? 'player' : null); return; }
  if (t.id === 'famQ') { const pos = t.selectionStart; S.famQ = t.value; render(); const i = $('#famQ'); i.focus(); try { i.setSelectionRange(pos, pos); } catch (er) { /* fine */ } }
});
view.addEventListener('keydown', e => {
  const t = e.target;
  if (!t.matches('.gl-pick input')) return;
  const st = S.pick[t.id] || {}, res = st.results || [];
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (!res.length) return;
    e.preventDefault(); st.active = ((st.active || 0) + (e.key === 'ArrowDown' ? 1 : -1) + res.length) % res.length; st.open = true; refreshPicker(t.id);
  } else if (e.key === 'Enter') {
    if (st.open && res.length) { e.preventDefault(); choose(t.id, res[st.active || 0]); }
  } else if (e.key === 'Escape') {
    if (st.open) { e.stopPropagation(); st.open = false; refreshPicker(t.id); }
  }
});
view.addEventListener('focusout', e => {
  const t = e.target;
  if (!t.matches || !t.matches('.gl-pick input')) return;
  setTimeout(() => { const st = S.pick[t.id]; if (st && st.open && document.activeElement !== $('#' + t.id)) { st.open = false; refreshPickerQuiet(t.id); } }, 150);
});
function refreshPickerQuiet(id) {
  const input = $('#' + id); if (!input) return;
  const wrap = input.closest('.gl-pick');
  const tmp = document.createElement('div');
  tmp.innerHTML = pickerHTML(id, wrap.querySelector('label').textContent, null, { placeholder: input.placeholder });
  wrap.replaceWith(tmp.firstElementChild);
}
view.addEventListener('mousedown', e => { if (e.target.closest('[data-opt]')) e.preventDefault(); });
view.addEventListener('submit', e => { e.preventDefault(); connect(); });
view.addEventListener('click', e => {
  const o = e.target.closest('[data-opt]');
  if (o) { const [id, i] = o.dataset.opt.split(':'); choose(id, S.pick[id].results[Number(i)]); return; }
  const un = e.target.closest('[data-unpick]');
  if (un) {
    const id = un.dataset.unpick;
    if (id === 'pkA') S.a = null; else if (id === 'pkB') S.b = null; else if (id === 'pkF') { S.famPerson = null; S.famMsg = ''; }
    if (id === 'pkA' || id === 'pkB') S.cx = { state: 'idle' };
    render(); const i = $('#' + id); if (i) i.focus(); return;
  }
  if (e.target.closest('[data-swap]')) { [S.a, S.b] = [S.b, S.a]; if (S.cx.state === 'result' && S.a && S.b) { const m = S.cx.more; showResult(1); if (m) showMore(); } render(); return; }
  const cm = e.target.closest('[data-cmode]'); if (cm) { S.cmode = cm.dataset.cmode; if (S.cx.state === 'result') { S.cx = { state: 'idle' }; } render(); return; }
  if (e.target.closest('[data-more]')) { showMore(); render(); const f = $('.gl-others button[aria-current="true"]'); if (f) f.focus(); return; }
  const ch = e.target.closest('[data-chain]'); if (ch) { S.cx.chain = Number(ch.dataset.chain); render(true); return; }
  if (e.target.closest('[data-retry]')) { S.cx = { state: 'idle' }; connect(); return; }
  const lb = e.target.closest('[data-lab]'); if (lb) { S.lab = lb.dataset.lab; render(); return; }
  const nb = e.target.closest('[data-nbpick]'); if (nb) { S.nb = nb.dataset.nbpick; S.hot = null; syncMockbar(); render(); const h = $('.gl-me b'); if (h) { h.setAttribute('tabindex', '-1'); h.focus(); } return; }
  if (e.target.closest('[data-zoom]')) { S.zoom = !S.zoom; render(); return; }
  const gp = e.target.closest('[data-grp]'); if (gp) { S.group = S.group === gp.dataset.grp ? null : gp.dataset.grp; render(); const again = $(`[data-grp="${gp.dataset.grp}"]`); if (again) again.focus(); return; }
  if (e.target.closest('#glCanvas')) {
    const r = e.target.getBoundingClientRect(); const p = nearest(e.clientX - r.left, e.clientY - r.top);
    if (p) { S.nb = p.player_id; S.hot = null; render(); }
    return;
  }
  const fm = e.target.closest('[data-fam]'); if (fm) { S.fam = Number(fm.dataset.fam); S.famPerson = null; S.famMsg = ''; render(); const h = $('#famSelH'); if (h) h.focus({ preventScroll: true }); return; }
  const cmin = e.target.closest('[data-cmin]'); if (cmin) { S.chemMin = Number(cmin.dataset.cmin); render(); return; }
  const cs = e.target.closest('[data-csort]'); if (cs) { const k = cs.dataset.csort; S.chemSort = S.chemSort.col === k ? { col: k, dir: -S.chemSort.dir } : { col: k, dir: ['qb', 'receiver', 'position', 'teams'].includes(k) ? 1 : -1 }; render(); const again = $(`[data-csort="${k}"]`); if (again) again.focus(); return; }
  const ba = e.target.closest('[data-balg]'); if (ba) { S.benchHot = S.benchHot === ba.dataset.balg ? null : ba.dataset.balg; render(); const again = $(`[data-balg="${CSS.escape(ba.dataset.balg)}"]`); if (again) again.focus(); return; }
  if (e.target.closest('[data-nav-engine]')) { S.screen = 'engine'; render(); }
});
// hovering or focusing a neighbour's row rings him on the map
function hotFrom(e) { const li = e.target.closest && e.target.closest('[data-hot]'); const id = li ? li.dataset.hot : null; if (id !== S.hot) { S.hot = id; $$('.gl-rank li').forEach(x => x.classList.toggle('hot', x.dataset.hot === id)); drawMap(); } }
view.addEventListener('pointerover', e => { if (S.part === 'lab' && S.lab === 'neighbours') hotFrom(e); });
view.addEventListener('focusin', e => { if (S.part === 'lab' && S.lab === 'neighbours') hotFrom(e); });

/* ------------------------------------------------------------------ tooltips (hover and focus) */
const tip = $('#tip');
function showTip(html, x, y) {
  tip.innerHTML = html; tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let left = x + 14, topY = y + 14;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  if (topY + r.height > innerHeight - 8) topY = y - r.height - 14;
  tip.style.left = Math.max(8, left) + 'px'; tip.style.top = Math.max(8, topY) + 'px';
}
function tipHTML(t) { const [h, ...rest] = t.dataset.tip.split('\n'); return `<div class="th">${esc(h)}</div>${rest.map(l => `<div>${esc(l)}</div>`).join('')}`; }
function hideTip() { tip.hidden = true; }
document.addEventListener('pointermove', e => {
  if (e.target.closest && e.target.closest('#glCanvas')) return;
  const t = e.target.closest && e.target.closest('[data-tip]'); if (t) showTip(tipHTML(t), e.clientX, e.clientY); else hideTip();
});
document.addEventListener('focusin', e => { const t = e.target.closest && e.target.closest('[data-tip]'); if (t) { const r = t.getBoundingClientRect(); showTip(tipHTML(t), r.left + Math.min(r.width, 220) * 0.5, r.bottom - 6); } else hideTip(); });
document.addEventListener('focusout', hideTip);
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  hideTip();
  if (S.settings) { S.settings = false; renderSide(); }
});

/* ------------------------------------------------------------------ boot */
showResult(1);
syncMockbar();
render();
})();
