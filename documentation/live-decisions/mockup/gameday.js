(() => {
'use strict';
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

/* ------------------------------------------------------------------ state */
const PALS = [
  { id: 'turf', name: 'Turf & Pylon', sw: ['#0D1B14', '#FF6B2C', '#4FD98F'], note: 'field green, end-zone pylon orange' },
  { id: 'playbook', name: 'Playbook', sw: ['#FFFFFF', '#1F5FD1', '#CC2F2F'], note: 'whiteboard by day, chalkboard by night' },
];
function systemDark() {
  const t = document.documentElement.getAttribute('data-theme');
  if (t === 'dark') return true;
  if (t === 'light') return false;
  try { return matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) { return true; }
}
const S = {
  pal: store.get('pal', 'turf'),
  mode: store.get('mode', 'system'),
  settings: false,
  gd: 'live', // before | live | between | final | past | nomodels
  moment: 'fourth_close',
  check: 'answer', // the picked play's check: idle | loading | answer | error
  checks: {}, // other games' checks in this mockup session
  sel: null, // the picked game (ESPN event id)
  tab: 'gameday',
  page: 'week',
};
if (!PALS.some(p => p.id === S.pal)) S.pal = 'turf';
if (!['system', 'dark', 'light'].includes(S.mode)) S.mode = 'system';

/* ------------------------------------------------------------------ helpers */
const team = a => D.teams[a] || { nick: a, name: a, color: '#888888' };
const tchip = (a, full) => `<span class="team"><i style="background:${esc(team(a).color)}"></i><b>${esc(full ? team(a).nick : a)}</b></span>`;
const ETF = o => new Intl.DateTimeFormat('en-US', Object.assign({ timeZone: 'America/New_York' }, o));
const tSec = iso => ETF({ hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(new Date(iso));
const tMin = iso => ETF({ hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
const tDay = iso => ETF({ weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(iso));
const tKick = iso => ETF({ weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
const p0 = p => p == null ? '—' : Math.round(p * 100) + '%';
const p1 = p => p == null ? '—' : (p * 100).toFixed(1) + '%';
const pts = g => (g * 100).toFixed(1);
const ago = s => s == null ? '—' : s < 60 ? `${Math.round(s)} s ago` : `${Math.floor(s / 60)} min ago`;
const ordQ = q => q >= 5 ? 'OT' : `Q${q}`;
const CHOICE_ART = { go: 'going for it', fg: 'the field goal', punt: 'punting' };
const ICON = {
  clock: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="6.2"/><path d="M8 4.6V8l2.4 1.6"/></svg>',
  season: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 13.5h12M3.5 11l3-4 2.5 2.5L13 4"/></svg>',
  teams: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 3.5h10M3 8h10M3 12.5h6"/></svg>',
  models: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M8 1.8l5.5 3.1v6.2L8 14.2l-5.5-3.1V4.9z M8 8v6.2 M8 8l5.5-3.1 M8 8L2.5 4.9"/></svg>',
  alerts: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 11V7a4 4 0 0 1 8 0v4l1 1.5H3z M6.5 14h3"/></svg>',
  health: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1.5 8.5h3l1.5-4 3 8 1.5-4h4"/></svg>',
};
const mockOnly = text => `<div class="notice"><span class="ic">i</span><div><b>Mockup-only state.</b> ${text}</div></div>`;

/* ------------------------------------------------------------------ data for the picked state */
// P.moments: the real reader's answers on ESPN's replayed week-4 play logs (dump_payloads.py);
// each moment has `at` (the mock clock), `games` (LiveGamesResponse), `call` and `context`.
const M = P.moments;
const BOARD = M.board || M.fourth_close;
const clone = o => JSON.parse(JSON.stringify(o));
const isLiveGd = () => S.gd === 'live' || S.gd === 'nomodels';
// "Not 3rd / 4th": the dump's own check when it has one; else built here from a real game on the
// board (and labelled on the page)
const NONE = M.none || (() => {
  const g = BOARD.games.games.find(x => x.state === 'in' && x.down && x.down < 3);
  if (!g) return null;
  const off = g.possession;
  const call = Object.assign(clone(M.fourth_close.call), {
    event: g.event, game_id: g.game_id, game: g, kind: 'none', fourth: null, third: null, behind: null, warnings: [],
    reason: `${g.situation.split(' at ')[0]}: checks are for 3rd and 4th downs`, offense: off, defense: off === g.home ? g.away : g.home,
    espn_offense_wp: null, repeat: { same: false, last_check_at: null, text: null },
  });
  call.feed.as_of = BOARD.games.feed.as_of;
  return { at: BOARD.at, games: BOARD.games, call, context: null, invented: true };
})();
function moment() {
  const fc = M.fourth_close;
  if (S.moment === 'stale') return Object.assign({}, fc, { call: fc.call_stale });
  if (S.moment === 'repeat') return Object.assign({}, fc, { call: fc.call_repeat });
  if (S.moment === 'none') return NONE || fc;
  return M[S.moment];
}
const stateMoment = () => isLiveGd() ? moment() : M[S.gd];
const nowIso = () => { const m = stateMoment(); return m.at || (m.games.feed && m.games.feed.as_of) || BOARD.at; };
const curWeek = () => S.gd === 'before' ? M.before.games.week : BOARD.games.week; // the calendar's week
const viewWeek = () => stateMoment().games.week;
function board() {
  const b = clone(stateMoment().games);
  if (isLiveGd()) {
    const g = moment().call.game;
    b.games = b.games.map(x => x.event === g.event ? g : x);
    if (S.gd === 'nomodels') b.models = { available: false, version: null, message: "The decision models aren't trained yet (LD00)." };
  }
  return b;
}
const momentEvent = () => moment().call.game.event;
function checkState(ev) {
  if (ev === momentEvent()) return S.check;
  return S.checks[ev] || 'idle';
}
function callFor(ev) {
  if (ev === momentEvent()) return moment();
  if (NONE && NONE.call.event === ev) return NONE;
  return null;
}
function leadText(g, off) {
  const os = off === g.home ? g.home_score : g.away_score;
  const ds = off === g.home ? g.away_score : g.home_score;
  return os === ds ? 'tied' : `${off} ${os > ds ? 'up' : 'down'} ${Math.abs(os - ds)}`;
}
function pregamePick(g) {
  const p = g.pregame.home_win_prob;
  if (p == null) return '—';
  return p >= 0.5 ? `${g.home} ${p0(p)}` : `${g.away} ${p0(1 - p)}`;
}

/* ------------------------------------------------------------------ mockbar */
function applyTheme() {
  document.documentElement.setAttribute('data-pal', S.pal);
  document.documentElement.setAttribute('data-mode', S.mode === 'system' ? (systemDark() ? 'dark' : 'light') : S.mode);
}
function syncMockbar() {
  $$('#gdPick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.gd)));
  $$('#momentPick button').forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.v === S.moment)); b.disabled = !isLiveGd(); });
  $$('#checkPick button').forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.v === S.check)); b.disabled = !isLiveGd(); });
  ['#momentGrp', '#checkGrp'].forEach(s => { $(s).style.opacity = isLiveGd() ? '' : '.45'; });
  applyTheme();
}
try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (S.mode === 'system') applyTheme(); }); } catch (e) { /* old browsers */ }
$('#gdPick').addEventListener('click', e => {
  const b = e.target.closest('[data-v]'); if (!b) return;
  S.gd = b.dataset.v; S.sel = null; S.checks = {}; S.tab = 'gameday'; S.page = 'week'; S.settings = false;
  syncMockbar(); render();
});
$('#momentPick').addEventListener('click', e => {
  const b = e.target.closest('[data-v]'); if (!b || b.disabled) return;
  S.moment = b.dataset.v; S.sel = null; S.checks = {}; if (S.check === 'idle') S.check = 'answer';
  syncMockbar(); render();
});
$('#checkPick').addEventListener('click', e => {
  const b = e.target.closest('[data-v]'); if (!b || b.disabled) return;
  S.check = b.dataset.v; S.sel = momentEvent();
  syncMockbar(); render();
});

/* ------------------------------------------------------------------ shell (as the control room) */
function renderSide() {
  const cw = curWeek(), vw = viewWeek();
  const cur = w => String(S.page === 'week' && vw === w);
  const now = nowIso();
  const w5 = cw === 5
    ? `<button class="navi" data-nav="week" data-week="5" aria-current="${cur(5)}"><span class="wk">5</span><span class="grow">Week 5<span class="sub">This week · Thu Oct 8</span></span><span class="chip ok"><span class="ic">✓</span>Published</span></button>`
    : '';
  const w4sub = cw === 4 ? 'This week · Sun Oct 4' : 'Checks passed';
  const w3 = S.gd === 'past' ? `<button class="navi" data-nav="week" data-week="${vw}" aria-current="true"><span class="wk">${vw}</span><span class="grow">Week ${vw}<span class="sub">Stand-in for a past week</span></span><span class="chip flat">Final</span></button>` : '';
  $('#side').innerHTML = `
    <div class="brand"><div class="brand-mark">CR</div><div style="flex:1;min-width:0"><b>Control Room</b><small>NFL Analytics Engine</small></div>
      <button class="iconbtn" id="gearBtn" aria-expanded="${S.settings}" aria-controls="settingsPop" title="Appearance"><svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="2.2"/><path d="M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M3.4 12.6l1.3-1.3M11.3 4.7l1.3-1.3"/></svg></button>
      ${S.settings ? settingsPop() : ''}</div>
    <div class="navgrp"><div class="eyebrow">2026 weeks</div>
      ${w5}
      <button class="navi" data-nav="week" data-week="4" aria-current="${cur(4)}"><span class="wk">4</span><span class="grow">Week 4<span class="sub">${w4sub}</span></span><span class="chip ok"><span class="ic">✓</span>Published</span></button>
      ${w3}
      <button class="navi" disabled title="Live weekly runs started in week 4"><span class="wk">1–3</span><span class="grow">Before go-live<span class="sub">Walk-forward rows only</span></span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">Season</div>
      <button class="navi" data-nav="season" aria-current="${String(S.page === 'season')}">${ICON.season}<span class="grow">Scorecard</span></button>
      <button class="navi" data-nav="season">${ICON.teams}<span class="grow">Teams &amp; rankings</span></button>
      <button class="navi" data-nav="season">${ICON.models}<span class="grow">Models</span></button>
      <button class="navi" data-nav="season">${ICON.alerts}<span class="grow">Alerts</span><span class="chip flat">0</span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">System</div>
      <button class="navi" data-nav="season">${ICON.health}<span class="grow">Health</span><span class="chip ok"><span class="ic">✓</span>OK</span></button>
    </div>
    <div class="side-foot">
      <div><span>Run lock</span><span>free</span></div>
      <div><span>Neo4j</span><span>up · 5.26</span></div>
      <div><span>Data drive</span><span>D:\\nfl-ml-data</span></div>
      <div><span>Mock clock</span><span>${esc(tDay(now))} · ${esc(tMin(now))} ET</span></div>
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
  S.settings = false;
  if (b.dataset.nav === 'week') {
    S.page = 'week';
    const w = Number(b.dataset.week);
    if (S.gd === 'past' && w === curWeek()) { S.gd = 'live'; S.sel = null; }
  } else S.page = 'season';
  syncMockbar(); render(); window.scrollTo({ top: 0 });
});

function renderTop() {
  const top = $('#top');
  const now = nowIso();
  if (S.page !== 'week') {
    top.innerHTML = `<div class="titleblock"><span class="eyebrow">2026 season</span><h1>Season pages</h1><span class="muted">Unchanged by LD02</span></div>`;
    return;
  }
  if (S.gd === 'before') {
    const b = M.before.games, first = b.games[0];
    const left = (new Date(b.first_kickoff) - new Date(now)) / 1000;
    top.innerHTML = `
      <div class="titleblock"><span class="eyebrow">2026 regular season · this week</span><h1>Week 5</h1>
        <div class="chips"><span class="chip ok"><span class="ic">✓</span>Published</span><span class="chip flat">${b.games.length} games · Thu–Mon</span><span class="chip flat">Thursday opener</span><span class="chip flat">Eagles at Jaguars in London</span></div></div>
      <div class="right">
        <div class="clock"><span class="small">First kickoff · ${tchip(first.away)} at ${tchip(first.home)}</span><span class="big">${Math.floor(left / 3600)} h ${Math.round((left % 3600) / 60)} m</span><span class="small">${esc(tDay(b.first_kickoff))}, ${esc(tMin(b.first_kickoff))} ET</span></div>
      </div>`;
    return;
  }
  if (S.gd === 'past') {
    top.innerHTML = `
      <div class="titleblock"><span class="eyebrow">2026 regular season</span><h1>Week ${viewWeek()}</h1>
        <div class="chips"><span class="chip flat">${M.past.games.games.length} games</span><span class="chip flat">All final</span></div></div>
      <div class="right"><div class="clock"><span class="small">Now</span><span class="big">${esc(tMin(now))}</span><span class="small">${esc(tDay(now))} ET · mock clock</span></div></div>`;
    return;
  }
  const b = board();
  const live = b.games.filter(g => g.state === 'in').length;
  top.innerHTML = `
    <div class="titleblock"><span class="eyebrow">2026 regular season · this week</span><h1>Week 4</h1>
      <div class="chips"><span class="chip ok"><span class="ic">✓</span>Published</span>${live ? `<span class="chip run"><span class="dot pulse"></span>${live} games on</span>` : ''}<span class="chip flat">16 games</span><span class="chip flat">Colts at Commanders in London</span></div></div>
    <div class="right">
      <div class="clock"><span class="small">Now</span><span class="big">${esc(tMin(now))}</span><span class="small">${esc(tDay(now))} ET · mock clock</span></div>
    </div>`;
}

const TABS = [['pipeline', 'Pipeline'], ['digest', 'Digest'], ['games', 'Games'], ['players', 'Players'], ['results', 'Results'], ['mlops', 'MLOps'], ['graph', 'Graph'], ['gameday', 'Game day']];
function renderTabs() {
  const nav = $('#tabs');
  if (S.page !== 'week') { nav.hidden = true; return; }
  nav.hidden = false;
  const live = isLiveGd() ? board().games.filter(g => g.state === 'in').length : 0;
  const counts = viewWeek() === 4 ? { games: 16, players: '1,860', graph: 3 } : { games: 15 };
  nav.innerHTML = TABS.map(([k, l]) => `<button class="tab" role="tab" data-tab="${k}" aria-selected="${S.tab === k}">${l}${k === 'gameday' && live ? `<span class="dot pulse" style="color:var(--accent)" aria-hidden="true"></span><span class="count">${live} live</span>` : counts[k] ? `<span class="count">${counts[k]}</span>` : ''}</button>`).join('');
}
$('#tabs').addEventListener('click', e => { const b = e.target.closest('[data-tab]'); if (!b) return; S.tab = b.dataset.tab; render(); });

/* ------------------------------------------------------------------ views */
function render() {
  renderSide(); renderTop(); renderTabs();
  const v = $('#view');
  if (S.page !== 'week') v.innerHTML = otherView('The season pages');
  else if (S.tab !== 'gameday') v.innerHTML = otherView(`The ${TABS.find(t => t[0] === S.tab)[1]} tab`);
  else if (S.gd === 'before') v.innerHTML = beforeView();
  else if (S.gd === 'past') v.innerHTML = pastView();
  else v.innerHTML = boardView();
}
function otherView(what) {
  return `<div class="notice accent"><span class="ic">i</span><div><b>Mockup: ${esc(what)} ${S.page === 'week' ? 'is' : 'are'} unchanged by LD02.</b> It's drawn in the <a href="${CR_MOCKUP}" target="_blank" rel="noopener">control room mockup</a>; this page shows the new Game day tab only.<div style="margin-top:10px"><button class="btn sm" data-go="gameday">Back to Game day</button></div></div></div>`;
}
/* ---------- before the first kickoff ---------- */
function beforeView() {
  const b = M.before.games, first = b.games[0];
  const rows = b.games.map(g => `<tr><td class="num">${esc(tKick(g.kickoff))}</td><td>${tchip(g.away, true)} <span class="muted">at</span> ${tchip(g.home, true)}</td><td class="muted">${esc(D.stadiums[g.game_id] || '')}</td><td class="r num">${esc(g.pregame.spread_text || '—')}</td><td class="r num">${g.pregame.total ?? '—'}</td><td class="r num pick">${esc(pregamePick(g))}</td></tr>`).join('');
  return `
    ${mockOnly("ESPN's real week-5 board, with every game set back to not started (the dump ran after Thursday's game).")}
    <div class="card"><div class="empty"><div class="glyph">GD</div><h3>Game day opens at the first kickoff</h3>
      <p>${esc(tDay(b.first_kickoff))}, ${esc(tMin(b.first_kickoff))} ET: ${esc(team(first.away).nick)} at ${esc(team(first.home).nick)}. From then on this tab lists the games that are on, and checks a 3rd or 4th down when you ask.</p></div></div>
    <div class="card slate"><div class="card-h"><h2>Week 5 slate</h2><span class="muted">${b.games.length} games · times ET · market lines from nflverse · our pick from this week's run</span></div>
      <div class="tablewrap"><table class="tbl"><thead><tr><th>Kickoff</th><th>Game</th><th>Stadium</th><th class="r">Market</th><th class="r">Total</th><th class="r">Our pick</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

/* ---------- a past week ---------- */
function pastView() {
  const b = M.past.games;
  return `
    <div class="card"><div class="empty"><div class="glyph">LD03</div><h3>Decision review: coming in LD03</h3>
      <p>For a finished week this tab will show every 4th down, the bot's call next to the coach's, and the win chance each choice cost or gained. Live checks only run during the current week.</p></div></div>
    <div class="card"><div class="card-h"><h2>Week ${b.week} finals</h2><span class="muted">${b.games.length} games</span></div>
      <div class="card-b"><div class="games" style="grid-template-columns:repeat(auto-fill,minmax(200px,1fr))">${b.games.map(g => gameCard(g, false)).join('')}</div></div></div>`;
}

/* ---------- the board: list + panel ---------- */
function boardView() {
  const b = board();
  const now = nowIso();
  const live = b.games.filter(g => g.state === 'in');
  const pre = b.games.filter(g => g.state === 'pre');
  const post = b.games.filter(g => g.state === 'post');
  if (S.sel == null && isLiveGd()) S.sel = momentEvent();
  const sel = b.games.find(g => g.event === S.sel) || null;
  const grp = (label, gs) => gs.length ? `<div class="grp"><div class="eyebrow"><span>${label}</span><span>${gs.length}</span></div>${gs.map(g => gameCard(g, true)).join('')}</div>` : '';
  let main = '';
  if (!b.models.available) main += `<div class="notice warn"><span class="ic warn">!</span><div><b>${esc(b.models.message)}</b> The game list still works; <b>Check this play</b> stays off until <code class="mono">nfl live train</code> has promoted a bundle.</div></div>`;
  if (S.gd === 'between' && !sel) {
    const nx = pre[0];
    const mins = nx ? Math.round((new Date(nx.kickoff) - new Date(now)) / 60000) : null;
    main += `<div class="card"><div class="empty"><div class="glyph">—</div><h3>No game on right now</h3><p>${nx ? `Next: ${esc(nx.away)} at ${esc(nx.home)}, ${esc(tMin(nx.kickoff))} ET (in ${mins} min).` : ''} The list keeps updating every ${b.refresh_s} s while this tab is open.</p></div></div>`;
  }
  if (S.gd === 'final' && !sel) {
    main += `<div class="card"><div class="empty"><div class="glyph">F</div><h3>Every week-4 game is final</h3><p>The week's decision review (each 4th down, the bot's call next to the coach's, and what it cost or gained) comes in LD03.</p><button class="btn sm" aria-disabled="true" title="Coming in LD03">Open the decision review · LD03</button></div></div>`;
  }
  if (sel) main += panel(sel, b);
  const counts = [live.length && `${live.length} on now`, pre.length && `${pre.length} later`, post.length && `${post.length} final`].filter(Boolean).join(' · ');
  return `
    ${S.gd === 'nomodels' ? mockOnly('The real bundle is loaded; this shows the tab without one.') : ''}${isLiveGd() && moment().invented ? mockOnly(`The dump has no check on a 1st or 2nd down; this one uses the board's real ${esc(NONE.call.game.away)} at ${esc(NONE.call.game.home)} state, with the contract's reason wording.`) : ''}${isLiveGd() && (S.check === 'loading' || S.check === 'error' || Object.values(S.checks).includes('loading')) ? mockOnly('The loading and error cards (and the 0.7 s load after a press) are drawn here; the real answers have neither.') : ''}
    <div class="gd-head"><h2>Game day</h2><span class="muted">${esc(tDay(now))} · ${esc(counts)}</span>
      <div class="right"><span>Updates every ${b.refresh_s} s while this tab is open · <span class="num">${esc(tSec(b.feed.as_of))}</span></span><button class="btn sm" id="refreshBtn">Refresh</button></div></div>
    <div class="gd">
      <div class="glist" aria-label="This week's games">${grp('On now', live)}${grp('Later', pre)}${grp('Final', post)}</div>
      <div class="gd-main">${main}</div>
    </div>`;
}

function gameCard(g, pickable) {
  const pressed = pickable ? ` aria-pressed="${g.event === S.sel}"` : '';
  const tag = pickable ? 'button' : 'div';
  const ball = t => g.state === 'in' && g.possession === t ? '<span class="ball" title="Has the ball" aria-label="has the ball"></span>' : '';
  const won = t => g.state !== 'post' || (t === g.home ? g.home_score >= g.away_score : g.away_score >= g.home_score);
  const row = (t, sc) => `<span class="gt${won(t) ? '' : ' lost'}">${tchip(t)}${ball(t)}</span><span class="gs${g.state === 'pre' ? ' pre' : won(t) ? '' : ' lost'}">${g.state === 'pre' ? '' : sc}</span>`;
  let top;
  if (g.state === 'in') {
    const sit = g.situation ? g.situation.replace(' at ', ' · ') : esc(g.detail || '');
    top = `<span class="live"><span class="dot pulse" aria-hidden="true"></span>${esc(ordQ(g.period))} ${esc(g.clock || '')}</span><span class="gsit${g.decision_down ? ' dd' : ''}">${esc(sit)}</span>`;
  } else if (g.state === 'pre') {
    top = `<span>${esc(tKick(g.kickoff))}</span><span class="gsit">${esc(g.pregame.spread_text || '')}</span>`;
  } else {
    top = `<span>${esc(g.detail || 'Final')}</span><span class="gsit">${esc(tDay(g.kickoff))}</span>`;
  }
  const label = `${g.away} ${g.state === 'pre' ? '' : g.away_score + ' '}at ${g.home}${g.state === 'pre' ? '' : ' ' + g.home_score}${g.state === 'in' ? ', ' + ordQ(g.period) + ' ' + (g.clock || '') + (g.situation ? ', ' + g.situation : '') : g.state === 'pre' ? ', ' + tKick(g.kickoff) : ', final'}`;
  return `<${tag} ${pickable ? `type="button" data-ev="${esc(g.event)}"${pressed} aria-label="${esc(label)}"` : ''} class="gcard"><span class="gtop">${top}</span><span class="grow2">${row(g.away, g.away_score)}${row(g.home, g.home_score)}</span></${tag}>`;
}

function asof(feed, g) {
  const lp = g && g.last_play;
  const how = lp && lp.age_from === 'snap' ? "timed from the snap (ESPN's play log)" : 'timed from when the app first saw it';
  return `<span class="asof">${ICON.clock}<span>ESPN as of <b>${esc(tSec(feed.as_of))}</b>${lp && lp.age_s != null ? ` · <span tabindex="0" data-tip="Last play\n${esc(how)}">last play ${esc(ago(lp.age_s))}</span>` : ''}</span>${feed.stale ? `<span class="chip warn"><span class="ic">!</span>Last good answer${feed.age_s ? ` · ${Math.round(feed.age_s)} s old` : ''}</span>` : ''}</span>`;
}

function tos(n) {
  if (n == null) return '';
  return `<span class="tos" title="${n} timeouts left" aria-label="${n} timeouts left">${[0, 1, 2].map(i => `<i class="${i < n ? '' : 'used'}"></i>`).join('')}</span>`;
}
function sideBlock(g, t, home) {
  const tm = team(t);
  const sc = t === g.home ? g.home_score : g.away_score;
  const has = g.state === 'in' && g.possession === t;
  return `<div class="side-t${home ? ' home' : ''}"><span class="swatchbar" style="background:${esc(tm.color)}"></span>
    <span class="tn"><b>${esc(t)}${has ? '<span class="ball lg" title="Has the ball"></span>' : ''}</b><small>${esc(tm.nick)}${home ? ' · home' : ''}</small>${g.state === 'in' ? tos(home ? g.home_timeouts : g.away_timeouts) : ''}</span>
    <span class="sc num">${g.state === 'pre' ? '' : sc}</span></div>`;
}

function panel(g, b) {
  const st = checkState(g.event);
  const res = callFor(g.event);
  const off = g.possession;
  let mid;
  if (g.state === 'in') mid = `<span class="q">${esc(ordQ(g.period))}</span><span class="clk">${esc(g.clock || '')}</span>${g.situation ? `<span class="dd">${esc(g.situation)}</span>` : ''}${g.red_zone ? '<span class="chip flat">Red zone</span>' : ''}`;
  else if (g.state === 'pre') mid = `<span class="q">Kickoff</span><span class="clk">${esc(tMin(g.kickoff))}</span><span class="dd">${esc(tDay(g.kickoff))}</span>`;
  else mid = `<span class="q">${esc(g.detail || 'Final')}</span><span class="clk">Final</span>`;
  const espn = g.espn_home_wp == null ? null : (off === g.away ? 1 - g.espn_home_wp : g.espn_home_wp);
  const espnTeam = off === g.away ? g.away : g.home;
  const facts = [
    `<span>Our pre-game pick <b>${esc(pregamePick(g))}</b></span>`,
    `<span>Market <b>${esc(g.pregame.spread_text || '—')}</b>${g.pregame.total != null ? ` · total <b>${g.pregame.total}</b>` : ''}</span>`,
    espn != null ? `<span>ESPN's win % now <b>${esc(espnTeam)} ${p0(espn)}</b></span>` : '',
  ].join('');
  const lp = g.last_play;
  const last = g.state === 'in' && lp && lp.text ? `<div class="lastplay"><span class="eyebrow">Last play · ESPN</span><span class="muted">${esc(ago(lp.age_s))}</span><p>${esc(lp.text)}</p></div>` : '';
  const head = `<div class="gp-h"><h2>${tchip(g.away, true)} <span class="muted" style="font-weight:500">at</span> ${tchip(g.home, true)}</h2><span class="muted">${esc(D.stadiums[g.game_id] || '')}</span>${g.state === 'in' && b.feed ? `<div class="right">${asof((res && st === 'answer' ? res.call.feed : b.feed), g)}</div>` : ''}</div>`;
  let act;
  const can = b.models.available && g.state === 'in';
  if (g.state === 'pre') act = `<button class="btn primary big" aria-disabled="true">Check this play</button><span class="reason">Opens at kickoff (${esc(tMin(g.kickoff))} ET).</span>`;
  else if (g.state === 'post') act = `<button class="btn primary big" aria-disabled="true">Check this play</button><span class="reason">The game is over. Its 4th downs go in the decision review (LD03).</span>`;
  else if (!can) act = `<button class="btn primary big" aria-disabled="true">Check this play</button><span class="reason">${esc(b.models.message)}</span>`;
  else if (st === 'loading') act = `<button class="btn primary big" aria-disabled="true" aria-busy="true"><span class="spin" aria-hidden="true"></span>Checking…</button><span class="reason">Asking ESPN for this game, then running the bot.</span>`;
  else act = `<button class="btn primary big" id="checkBtn" data-ev="${esc(g.event)}">${st === 'answer' ? 'Check again' : st === 'error' ? 'Try again' : 'Check this play'}</button><span class="reason">${st === 'answer' && res ? `Last check ${esc(tSec(res.call.feed.as_of))}. ` : ''}One ESPN call for this game, then the bot: about a second. Nothing is fetched until you press it.</span>`;
  let out = `<div class="card gp">${head}<div class="board">${sideBlock(g, g.away, false)}<div class="mid">${mid}</div>${sideBlock(g, g.home, true)}</div><div class="facts">${facts}</div>${last}<div class="actbar">${act}</div></div>`;
  if (g.state !== 'in' || !can) return out;
  if (st === 'loading') out += `<div class="card callcard" aria-live="polite"><div class="ch"><div class="what"><span class="eyebrow">Checking</span><b>${esc(g.situation || '')}</b></div></div><div class="skel"><i style="width:40%;height:44px"></i><i style="width:92%"></i><i style="width:84%"></i><i style="width:70%"></i></div></div>`;
  else if (st === 'error') out += `<div class="notice err" role="alert"><span class="ic err">!</span><div><b>Couldn't check this play.</b> ESPN didn't answer (timeout) and the app has no earlier answer for this game. Try again in a few seconds; the app waits at least 2 s between calls for one game.</div></div>`;
  else if (st === 'answer') out += res ? callBlock(res) : `<div class="notice"><span class="ic">i</span><div><b>Mockup: no recorded check for this game.</b> Pick a play in the strip at the top to see a call.</div></div>`;
  return out;
}

/* ---------- the call ---------- */
function callBlock(m) {
  const c = m.call;
  let h = '<div aria-live="polite" style="display:flex;flex-direction:column;gap:16px">';
  if (c.feed.stale) h += `<div class="notice warn" role="status"><span class="ic warn">!</span><div><b>ESPN didn't answer (${esc(c.feed.error || 'error')}).</b> Showing the last good answer, from ${esc(tSec(c.feed.as_of))}. The play may have moved on; press Check again in a few seconds.</div></div>`;
  if (c.repeat && c.repeat.same && !c.feed.stale) h += `<div class="notice" role="status"><span class="ic">=</span><div><b>${esc(c.repeat.text || 'No new play since your last check.')}</b> A commercial or a review: the call below is the same as last time.</div></div>`;
  if (c.behind && c.behind.likely) {
    const row = c.third && c.third.table.find(r => r.gain === 0);
    h += `<div class="notice accent" role="status"><span class="ic">i</span><div><b>${esc(c.behind.text)}</b>${row ? ` If your TV already shows <b>${esc(row.situation)}</b>, the highlighted row is the call.` : ''}</div></div>`;
  }
  c.warnings.forEach(w => { h += `<div class="notice"><span class="ic">i</span><div>Assumed: ${esc(w)}</div></div>`; });
  if (c.kind === 'fourth') h += fourthCard(c);
  else if (c.kind === 'third') h += thirdCard(c);
  else h += noneCard(c);
  if (m.context && c.kind !== 'none') h += contextCard(m.context, c);
  return h + '</div>';
}
function callHead(c, kindLabel) {
  const g = c.game;
  return `<div class="ch"><div class="what"><span class="eyebrow">${esc(kindLabel)} · ${esc(c.offense)} ball</span><b>${esc(g.situation || '')}</b><span class="muted">${esc(ordQ(g.period))} ${esc(g.clock || '')} · ${esc(leadText(g, c.offense))}</span></div><div class="right">${asof(c.feed, g)}</div></div>`;
}
function callFoot(c, ms) {
  return `<div class="callfoot"><span>Decision models <span class="mono">${esc(c.models.version || '—')}</span></span><span>computed in ${ms} ms</span><span>ESPN's public site API (unofficial)</span><span>Win chances are ${esc(c.offense)}'s, from the models; ESPN's own % is shown only as a reference.</span></div>`;
}
function confChip(label, sm) {
  if (!label) return '';
  const n = { Confident: 3, Lean: 2, 'Toss-up': 1 }[label];
  return `<span class="conf${sm ? ' sm' : ''}${label === 'Toss-up' ? ' toss' : ''}"><span class="pips" aria-hidden="true">${[0, 1, 2].map(i => `<i class="${i < n ? '' : 'off'}"></i>`).join('')}</span><span class="lt">${esc(label)}</span></span>`;
}
function labelLine(f) {
  const n = f.boot_share == null ? null : Math.round(f.boot_share * 20);
  if (f.label === 'Confident') return f.gap >= 0.05 || n == null ? 'More than 5 points clear: no re-check of the models could flip it.' : `The call held in ${n} of 20 re-checks of the models.`;
  if (f.label === 'Lean') return `The call held in ${n} of 20 re-checks: likely right, not certain.`;
  if (f.gap < 0.01) return 'Under 1 point apart: either choice is defensible, and nobody should be second-guessed for it.';
  return `It flipped in ${20 - n} of 20 re-checks: the data can't separate them.`;
}
const LABEL_KEY = 'How sure the bot is\nConfident: the call held in at least 18 of 20 re-checks, or it is 5+ points clear\nLean: it held in 12 to 17 of 20\nToss-up: under 12 of 20, or the options are under 1 point apart';
function labelKey(cur) {
  return `<span class="labkey" tabindex="0" data-tip="${esc(LABEL_KEY)}">${['Confident', 'Lean', 'Toss-up'].map(l => `<span class="${l === cur ? '' : 'off'}">${confChip(l, true)}</span>`).join('')}</span>`;
}
const RECHECK = "What's a re-check?\nThe bot refits its yards-gained and field-goal models 20 times on resampled data.\nIf the call comes out the same every time, the data really support it.";
function fourthCard(c) {
  const f = c.fourth, g = c.game;
  const avail = f.options.filter(o => o.wp != null).sort((a, b) => b.wp - a.wp);
  const best = avail[0], next = avail[1];
  const toss = f.label === 'Toss-up';
  const big = toss ? `<div class="big toss">Toss-up</div><div class="sub">${esc(best.name)} by ${pts(f.gap)} points over ${esc(CHOICE_ART[next.choice])}</div>`
    : `<div class="big">${esc(f.best_name)}</div><div class="sub">Worth ${pts(f.gap)} more points of win chance than ${esc(CHOICE_ART[next.choice])}</div>`;
  const tied = o => toss && o.wp != null && best.wp - o.wp < 0.01;
  const bars = f.options.map(o => {
    const cls = tied(o) ? ' tied' : o.best && !toss ? ' best' : '';
    if (o.wp == null) return `<div class="opt"><span class="on">${esc(o.name)}</span><span class="tr"><span class="bg"></span></span><span class="pv na">not an option</span></div>`;
    return `<div class="opt${cls}" tabindex="0" data-tip="${esc(o.name)}\n${esc(c.offense)} win chance after it: ${p1(o.wp)}${o.choice === best.choice ? '\nThe best option' : `\n${pts(best.wp - o.wp)} points behind ${best.name.toLowerCase()}`}"><span class="on">${esc(o.name)}</span><span class="tr"><span class="bg"></span><span class="fill" style="width:${(o.wp * 100).toFixed(2)}%"></span></span><span class="pv num">${p1(o.wp)}</span></div>`;
  }).join('');
  const punter = c.defense;
  const odds = `
    <div class="odd"><span class="k">If they go</span><span class="v">${p0(f.convert)}</span><span class="d">to convert ${esc((g.situation || '').split(' at ')[0])}</span></div>
    <div class="odd"><span class="k">If they kick</span><span class="v">${f.fg_make == null ? '—' : p0(f.fg_make)}</span><span class="d">${f.fg_make == null ? `out of range (${f.fg_distance} yards)` : `to make it from ${f.fg_distance} yards`}</span></div>
    <div class="odd"><span class="k">If they punt</span><span class="v">Own ${f.punt_start == null ? '—' : Math.round(f.punt_start)}</span><span class="d">where ${esc(punter)} starts, on average</span></div>
    ${c.espn_offense_wp != null ? `<div class="odd ref"><span class="k">ESPN's win chance</span><span class="v">${p0(c.espn_offense_wp)}</span><span class="d">for ${esc(c.offense)} now · a reference, not part of the call</span></div>` : ''}`;
  return `<div class="card callcard">${callHead(c, '4th down')}
    <div class="callgrid">
      <div class="verdict"><span class="eyebrow">The call</span>${big}
        <span style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">${confChip(f.label)}<span class="muted" tabindex="0" data-tip="${esc(RECHECK)}" style="font-size:12.5px;text-decoration:underline dotted;cursor:help">What's a re-check?</span></span>
        <p class="expl">${esc(labelLine(f))}</p>${labelKey(f.label)}</div>
      <div>
        <div class="opts" role="list" aria-label="${esc(c.offense)} win chance after each choice">${bars}</div>
        <div class="axis" aria-hidden="true"><span></span><span class="ticks"><span style="left:0">0%</span><span style="left:50%">50%</span><span style="left:100%">100%</span></span><span></span></div>
        <p class="optnote"><span>${esc(c.offense)}'s win chance after each choice. The bars start at 0%, so close calls look close.</span></p>
      </div>
    </div>
    <div class="odds">${odds}</div>
    ${callFoot(c, f.ms)}</div>`;
}
function capFirst(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }
function thirdCard(c) {
  const t = c.third, g = c.game;
  const behind = c.behind && c.behind.likely;
  const rows = t.table.map(r => {
    const cls = r.gain === 0 ? (behind ? 'yours' : 'nogain') : r.gain < 0 ? 'loss' : '';
    const wp = ['go', 'fg', 'punt'].map(k => r.wp[k] == null ? null : `${{ go: 'Go', fg: 'Field goal', punt: 'Punt' }[k]} ${p1(r.wp[k])}`).filter(Boolean).join(' · ');
    const tip = `${r.situation}\n${r.best_name}${r.label ? ` · ${r.label}` : ''}, by ${pts(r.gap)} points\n${wp}${r.wp.fg == null ? '\nNo field goal: out of range' : ''}`;
    return `<tr class="${cls}" tabindex="0" data-tip="${esc(tip)}"><td class="gain">${esc(capFirst(r.gain_text))}${r.gain === 0 ? `<span class="tag">${behind ? '· your TV, likely' : '· the line now'}</span>` : ''}</td><td class="num face">${esc(r.situation)}${r.gain === 0 ? `<span class="tag ph">${behind ? 'your TV, likely' : 'no gain'}</span>` : ''}</td><td class="call${r.label === 'Toss-up' ? ' toss' : ''}">${esc(r.best_name)}</td><td>${confChip(r.label, true)}</td><td class="r num">+${pts(r.gap)}</td></tr>`;
  }).join('');
  return `<div class="card callcard">${callHead(c, '3rd down')}
    <div class="third">
      <div class="fig"><b class="num">${p0(t.convert)}</b><span>chance they convert</span></div>
      <div class="fig"><b class="num">${p0(t.pass_prob)}</b><span>chance they pass</span></div>
      <div class="fig small"><b class="num">${p0(t.wp_now)}</b><span>${esc(c.offense)}'s win chance now</span></div>
      ${c.espn_offense_wp != null ? `<div class="fig small"><b class="num">${p0(c.espn_offense_wp)}</b><span>ESPN's, for reference</span></div>` : ''}
    </div>
    <div class="ifs"><h3>If they're stopped short</h3>
      <p>The 4th-down call for every spot they could be left with, ready before ESPN posts the 4th down. "By" is how many points of win chance the call is ahead of the next best.</p>
      ${t.note ? `<div class="notice"><span class="ic">i</span><div>${esc(t.note)}</div></div>` : ''}
      <div class="tablewrap"><table class="tbl iftbl"><thead><tr><th class="gain">If they gain</th><th>They face</th><th>The call</th><th><span tabindex="0" data-tip="${esc(LABEL_KEY)}" style="text-decoration:underline dotted;cursor:help">How sure</span></th><th class="r">By</th></tr></thead><tbody>${rows}</tbody></table></div>
    </div>
    ${callFoot(c, t.ms)}</div>`;
}
function noneCard(c) {
  return `<div class="card callcard">${callHead(c, capFirst((c.game.situation || '').split(' ')[0] || 'This') + ' down')}
    <div class="empty" style="padding:30px 20px"><h3>${esc(c.reason || "Not a 3rd or 4th down")}</h3><p>Press Check again when it's 3rd or 4th down.</p></div></div>`;
}

/* ---------- is this team good at this? ---------- */
const BANDS_K = d => d < 30 ? '<30' : d < 40 ? '30-39' : d < 50 ? '40-49' : '50+';
const BANDS_C = d => d <= 2 ? '1-2' : d <= 5 ? '3-5' : '6+';
const bandLabel = b => b.replace('-', '–');
function statCell(s) { return s.den ? `<span class="r">${p0(s.rate)}</span><small>${s.num} of ${s.den}</small>` : '<span class="muted" title="No attempts">—</span>'; }
function vsViz(m, t) {
  const L = m.league_this.rate ?? m.league_last.rate;
  const X = v => `${Math.max(0, Math.min(100, v * 100)).toFixed(1)}%`;
  return `<span class="vs" aria-hidden="true"><span class="t"></span>${L != null ? `<span class="lg" style="left:${X(L)}"></span>` : ''}${m.last.rate != null ? `<span class="d2" style="left:${X(m.last.rate)}"></span>` : ''}${m.this.rate != null ? `<span class="d1" style="left:${X(m.this.rate)}"></span>` : ''}</span>`;
}
function contextCard(x, c) {
  const T = x.team, g = c.game;
  const ts = x.this_season, ls = x.last_season;
  const measures = [
    ['go_rate', 'Goes for it on 4th down', 'went for it, of its 4th downs'],
    ['fourth_conv', 'Converts when it goes', 'converted, of its 4th-down tries'],
    ['short_conv', 'Short yardage', '3rd or 4th & 2 or less, converted'],
    ['red_zone_td', 'Red-zone touchdowns', 'drives inside the 20 that end in a TD'],
  ];
  const mrows = measures.map(([k, l, d]) => {
    const m = T[k];
    const tip = `${l}\n${T.team} ${ts}: ${m.this.den ? p0(m.this.rate) + ` (${m.this.num} of ${m.this.den})` : '—'}\n${T.team} ${ls}: ${m.last.den ? p0(m.last.rate) + ` (${m.last.num} of ${m.last.den})` : '—'}\nLeague ${ts}: ${p0(m.league_this.rate)}`;
    return `<tr tabindex="0" data-tip="${esc(tip)}"><td>${esc(l)}<small>${esc(d)}</small></td><td>${statCell(m.this)}</td><td>${statCell(m.last)}</td><td class="lg">${p0(m.league_this.rate)}<small>${p0(m.league_last.rate)} in ${ls}</small></td><td class="vscell">${vsViz(m)}</td></tr>`;
  }).join('');
  // the distance and the kick this play is about
  let dist = g.distance, fgd = null;
  if (c.kind === 'fourth') { fgd = c.fourth.options.find(o => o.choice === 'fg').wp != null ? c.fourth.fg_distance : null; }
  else if (c.third) { const r = c.third.table.find(r => r.gain === 0); dist = r ? r.ydstogo : dist; fgd = r && r.wp.fg != null ? r.yardline_100 + 18 : null; }
  const cb = BANDS_C(dist);
  const coachRows = T.coach_go_by_distance.map(r => `<tr class="${r.band === cb ? 'here' : ''}"><td>4th &amp; ${esc(bandLabel(r.band))}${r.band === cb ? '<span class="heretag">this play</span>' : ''}</td><td>${statCell(r.this)}</td><td>${statCell(r.last)}</td><td class="lg">${p0(r.league_last.rate)}</td></tr>`).join('');
  const cg = T.coach_go;
  const coach = `<div class="ctxblock"><h3>Coach: ${esc(T.coach || '—')}${T.coach_last_team && T.coach_last_team !== T.team ? `<small>${esc(T.coach_last_team)} last season</small>` : ''}</h3>
    <p class="ctxline">${cg ? `Where the bot says go, he went for it <b>${cg.this.num} of ${cg.this.den}</b> times this season (${p0(cg.this.rate)}) and <b>${cg.last.num} of ${cg.last.den}</b> in ${ls}${T.coach_last_team && T.coach_last_team !== T.team ? ` with ${esc(T.coach_last_team)}` : ''} (${p0(cg.last.rate)}). League in ${ls}: ${p0(cg.league_last.rate)}.` : 'Not enough "go" spots yet.'}</p>
    <table class="ctbl"><thead><tr><th>Bot says go on</th><th>${ts}</th><th>${ls}</th><th>League ${ls}</th></tr></thead><tbody>${coachRows}</tbody></table></div>`;
  let right = '';
  const K = T.kicker;
  if (K && fgd != null) {
    const kb = BANDS_K(fgd);
    const krows = K.bands.map(r => `<tr class="${r.band === kb ? 'here' : ''}"><td>${esc(bandLabel(r.band))} yards${r.band === kb ? `<span class="heretag">this kick: ${fgd}</span>` : ''}</td><td>${statCell(r.this)}</td><td>${statCell(r.last)}</td><td class="lg">${p0(r.league_last.rate)}</td></tr>`).join('');
    right += `<div class="ctxblock"><h3>Kicker: ${esc(K.name || '—')}<small>${K.long_season != null && K.long_season > 1900 ? `career long ${K.long ?? '—'} (${K.long_season})` : `long ${K.long ?? '—'} (career) · ${K.long_season ?? '—'} this season`}</small></h3>
      <table class="ctbl"><thead><tr><th>Field goals from</th><th>${ts}</th><th>${ls}</th><th>League ${ls}</th></tr></thead><tbody>${krows}</tbody></table></div>`;
  }
  const PU = T.punter;
  if (PU) {
    const n = v => v == null ? '—' : v.toFixed(1);
    right += `<div class="ctxblock"><h3>Punter: ${esc(PU.name || '—')}<small>net yards per punt</small></h3>
      <table class="ctbl"><thead><tr><th></th><th>${ts}</th><th>${ls}</th></tr></thead><tbody>
      <tr><td>${esc(T.team)}</td><td><span class="r">${n(PU.this.net)}</span><small>${PU.this.punts} punts</small></td><td><span class="r">${n(PU.last.net)}</span><small>${PU.last.punts} punts</small></td></tr>
      <tr><td>League</td><td class="lg">${n(PU.league_this.net)}</td><td class="lg">${n(PU.league_last.net)}</td></tr></tbody></table>
      <p class="ctxline">Net = gross yards, minus returns, minus 20 for a touchback.</p></div>`;
  }
  return `<div class="card ctx"><div class="card-h"><h2>Is ${esc(T.team)} good at this?</h2><span class="muted">${esc(x.through)} · as of the start of the week</span>
      <div class="right"><span class="ctxlegend"><span><i class="d1"></i>${esc(T.team)} ${ts}</span><span><i class="d2"></i>${ls}</span><span><i class="lg"></i>league ${ts}</span></span></div></div>
    <div class="ctxgrid">
      <section><div class="ctxblock"><h3>The team<small>0–100% scale on the right</small></h3>
        <table class="ctbl"><thead><tr><th></th><th>${ts}</th><th>${ls}</th><th>League</th><th class="vscell"></th></tr></thead><tbody>${mrows}</tbody></table></div>${coach}</section>
      <section>${right || '<p class="muted">No kick in range on this play.</p>'}</section>
    </div></div>`;
}

/* ------------------------------------------------------------------ interactions */
$('#view').addEventListener('click', e => {
  const go = e.target.closest('[data-go]'); if (go) { S.tab = go.dataset.go; render(); return; }
  const card = e.target.closest('button.gcard[data-ev]');
  if (card) { S.sel = card.dataset.ev; render(); return; }
  const chk = e.target.closest('#checkBtn');
  if (chk) {
    const ev = chk.dataset.ev;
    const set = v => { if (ev === momentEvent()) S.check = v; else S.checks[ev] = v; };
    set('loading'); syncMockbar(); render();
    setTimeout(() => { set('answer'); syncMockbar(); render(); }, 700);
    return;
  }
  if (e.target.closest('#refreshBtn')) {
    const b = e.target.closest('#refreshBtn'); b.innerHTML = '<span class="spin" aria-hidden="true"></span>Refreshing'; b.setAttribute('aria-disabled', 'true');
    setTimeout(() => render(), 600);
  }
});

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
document.addEventListener('pointermove', e => {
  const t = e.target.closest && e.target.closest('[data-tip]');
  if (t) showTip(tipHTML(t), e.clientX, e.clientY); else hideTip();
});
document.addEventListener('focusin', e => {
  const t = e.target.closest && e.target.closest('[data-tip]');
  if (t) { const r = t.getBoundingClientRect(); showTip(tipHTML(t), r.left + Math.min(r.width, 220) * 0.5, r.bottom - 6); } else hideTip();
});
document.addEventListener('focusout', hideTip);
document.addEventListener('keydown', e => { if (e.key === 'Escape') { hideTip(); if (S.settings) { S.settings = false; renderSide(); } } });

/* ------------------------------------------------------------------ boot */
syncMockbar();
render();
})();
