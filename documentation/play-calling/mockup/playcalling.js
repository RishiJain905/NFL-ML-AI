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

/* ------------------------------------------------------------------ the payloads (dump_payloads.py)
   The reader's answers, the shapes in web/src/api/types.ts (PC01 block):
   teams[season] = PlaycallTeamsResponse; team[season][TEAM][side] = PlaycallTeamResponse;
   history[TEAM][side] = PlaycallHistoryResponse; play_calls[`${season}_${week}`] = PlayCallsResponse;
   states.{not_built, not_yet} = the reader's own empty answers (a team page's and a week's). */
const SEASONS = Object.keys(P.teams).map(Number).sort((a, b) => b - a);
const NEWEST = SEASONS[0];
const teamsFor = s => P.teams[String(s)];
const teamFor = (s, t, side) => ((P.team[String(s)] || {})[t] || {})[side] || null;
const historyFor = (t, side) => (P.history[t] || {})[side] || null;
const WEEK_KEYS = Object.keys(P.play_calls);
const weekFor = k => P.play_calls[k];
const TEAMS_WITH_PAGES = Object.keys(P.team[String(NEWEST)] || {});

/* ------------------------------------------------------------------ state */
const PALS = [
  { id: 'turf', name: 'Turf & Pylon', sw: ['#0D1B14', '#FF6B2C', '#4FD98F'], note: 'field green, end-zone pylon orange' },
  { id: 'playbook', name: 'Playbook', sw: ['#FFFFFF', '#1F5FD1', '#CC2F2F'], note: 'whiteboard by day, chalkboard by night' },
];
function systemDark() {
  try { return matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) { return true; }
}
const S = {
  pal: store.get('pal', 'turf'),
  mode: store.get('mode', 'system'),
  settings: false,
  screen: 'grid', // grid | team | week | other
  state: 'ok', // ok | not_built | not_yet | loading (mockup controls)
  season: NEWEST, // the grid's and the team page's season
  team: TEAMS_WITH_PAGES[0] || 'KC',
  side: store.get('pc.side', 'offense') === 'defense' ? 'defense' : 'offense', // remembered (the app: localStorage)
  win: 'season', // season | last4 | last_season
  fam: 'down_distance',
  hist: false, // History opened
  sort: { id: 'team', dir: 1 },
  gridView: 'tiles', // tiles | table
  weekKey: WEEK_KEYS[0],
  upd: 'real', // the Update play calling card's state (mockup controls; all but 'real' are synthetic)
  pcView: 'fc', // the Play calls tab: fc (the forecast) | tend (PC01's tendencies so far)
  other: '',
};
if (!PALS.some(p => p.id === S.pal)) S.pal = 'turf';
if (!['system', 'dark', 'light'].includes(S.mode)) S.mode = 'system';

/* ------------------------------------------------------------------ helpers */
const team = a => D.teams[a] || { nick: a, name: a, color: '#888888' };
const tchip = (a, full) => `<span class="team"><i style="background:${esc(team(a).color)}"></i><b>${esc(full ? team(a).nick : a)}</b></span>`;
const ETF = o => new Intl.DateTimeFormat('en-US', Object.assign({ timeZone: 'America/New_York' }, o));
const tKick = iso => iso ? ETF({ weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date(iso)) : '';
const tDay = iso => iso ? ETF({ weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(iso)) : '';
const MINUS = '−';
/** Signed, rounded first: a value that rounds to zero has no sign ("0.0", never "−0.0"). */
const signed = (x, d) => { const r = Number(x.toFixed(d)); return (r > 0 ? '+' : r < 0 ? MINUS : '') + Math.abs(r).toFixed(d); };
/** A metric's value in its unit: shares as %, PROE as signed points (0.037 → "+3.7"), means as is. */
function fmt(m, v) {
  if (v == null) return '—';
  if (m.unit === 'share') return (v * 100).toFixed(m.digits) + '%';
  if (m.unit === 'over_expected') return signed(v * 100, m.digits);
  return v.toFixed(m.digits);
}
/** A difference from the league: points for shares and PROE, the unit for means. */
function fmtDiff(m, d) {
  if (d == null) return '—';
  if (m.unit === 'mean') return signed(d, m.digits);
  return signed(d * 100, m.digits) + ' pts';
}
const ord = p => { const n = Math.round(p); const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] || 'th'; return n + s; };
const PLURAL = { play: 'plays', dropback: 'dropbacks', attempt: 'attempts', 'designed run': 'designed runs', snap: 'snaps', target: 'targets', game: 'games' };
const per = (m, n) => n === 1 ? m.per : (PLURAL[m.per] || m.per + 's');
const WIN_LABEL = { season: 'Season', last4: 'Last 4', last_season: 'Last season' };
const ICON = {
  season: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 13.5h12M3.5 11l3-4 2.5 2.5L13 4"/></svg>',
  teams: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 3.5h10M3 8h10M3 12.5h6"/></svg>',
  models: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M8 1.8l5.5 3.1v6.2L8 14.2l-5.5-3.1V4.9z M8 8v6.2 M8 8l5.5-3.1 M8 8L2.5 4.9"/></svg>',
  alerts: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 11V7a4 4 0 0 1 8 0v4l1 1.5H3z M6.5 14h3"/></svg>',
  health: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1.5 8.5h3l1.5-4 3 8 1.5-4h4"/></svg>',
  // an O, an X and a route: the playbook mark
  playcalling: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="4" cy="12" r="2.2"/><path d="M10.5 10l3.5 3.5M14 10l-3.5 3.5M4 9.3C4 5.5 7 3.2 12 3.2M10 1.6l2 1.6-2 1.6"/></svg>',
};
const mockOnly = text => `<div class="notice"><span class="ic">i</span><div><b>Mockup-only state.</b> ${text}</div></div>`;

/** A cell's tooltip: the number, n, the league, the percentile ("more of it", not "better"). */
function cellTip(m, c, who, where, meta, extra) {
  const lines = [`${m.label}${where ? ' · ' + where : ''}`];
  if (!c || c.value == null) { lines.push(`${who}: no ${per(m, 2)} yet`); return lines.join('\n'); }
  lines.push(`${who}: ${fmt(m, c.value)} (${c.n} ${per(m, c.n)}${c.games != null ? `, ${c.games} game${c.games === 1 ? '' : 's'}` : ''})`);
  if (c.league != null) lines.push(`League: ${fmt(m, c.league)} · ${fmtDiff(m, c.diff)}`);
  if (c.pct != null) lines.push(`${ord(c.pct)} percentile: more of it than ${Math.round(c.pct)}% of teams (not "better")`);
  if (c.small) lines.push(`Small sample: under ${meta.min_n} ${per(m, 2)}, not a tendency yet`);
  if (extra) lines.push(extra);
  if (m.help) lines.push(m.help);
  return lines.join('\n');
}
const ALLOWED_NOTE = "What offenses do against this defense depends on the offenses it faced (the forecast adjusts for it).";
const allowedNote = (m, side) => side === 'defense' && m.caller === 'offense' ? ALLOWED_NOTE : null;

/* ---------- the rate bar: the team's rate, the league marker, the percentile ---------- */
function niceTop(v) { // a round top for a 0-based scale
  if (!(v > 0)) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const f = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find(s => s * mag >= v);
  return f * mag;
}
/** Drawn from the league tick, not from 0: PROE, and means that sit near 0 or below (EPA per play). */
const centred = (m, v, L) => m.unit === 'over_expected' || (m.unit === 'mean' && (Math.abs(L ?? 0) < 0.5 || v < 0));
function rateBar(m, c, compact, dom) {
  if (!c || c.value == null) return `<span class="rbar empty${compact ? ' sm' : ''}"><span class="rt"></span></span>`;
  const v = c.value, L = c.league;
  if (centred(m, v, L)) {
    // centred on the league (PROE's league isn't 0; EPA can be negative): the bar runs from the league marker to the team
    const span = dom ? dom.span : Math.max(0.12, Math.abs(v - (L ?? 0)) * 1.25);
    const X = x => Math.max(0, Math.min(100, 50 + ((x - (L ?? 0)) / span) * 50));
    const a = Math.min(X(v), 50), b = Math.max(X(v), 50);
    return `<span class="rbar${compact ? ' sm' : ''}"><span class="rt"></span><span class="rb ${v >= (L ?? 0) ? 'up' : 'dn'}" style="left:${a.toFixed(1)}%;width:${Math.max(0.8, b - a).toFixed(1)}%"></span><span class="lgm" style="left:50%"></span></span>`;
  }
  const top = dom ? dom.top : m.unit === 'share' ? Math.min(1, niceTop(Math.max(v, L ?? 0) * 1.2)) : niceTop(Math.max(v, L ?? 0) * 1.2);
  const X = x => Math.max(0, Math.min(100, (x / top) * 100));
  return `<span class="rbar${compact ? ' sm' : ''}"><span class="rt"></span><span class="rb" style="width:${Math.max(0.8, X(v)).toFixed(1)}%"></span>${L != null ? `<span class="lgm" style="left:${X(L).toFixed(1)}%"></span>` : ''}</span>`;
}
function pctMeter(c) {
  if (!c || c.pct == null) return '<span class="pctm"><b>—</b></span>';
  return `<span class="pctm"><b>${ord(c.pct)}</b><span class="pm"><span class="pd" style="left:${Math.max(0, Math.min(100, c.pct)).toFixed(0)}%"></span></span></span>`;
}
function rateRow(m, c, who, meta, side, where) {
  const tip = cellTip(m, c, who, where, meta, allowedNote(m, side));
  const small = c && c.small;
  return `<div class="rrow${small ? ' small' : ''}${!c || c.value == null ? ' none' : ''}" tabindex="0" data-tip="${esc(tip)}">
    <span class="rl">${esc(m.short)}<small>${esc(m.label)}</small><small class="ph">lg ${esc(fmt(m, c && c.league))}${c && c.pct != null ? ` · ${ord(c.pct)} pct` : ''}</small></span>
    <span class="rv num">${esc(fmt(m, c && c.value))}<small>${c ? `n ${c.n}${small ? ' · small' : ''}` : 'no data'}</small></span>
    ${rateBar(m, c)}
    <span class="rlg num">${c && c.league != null ? esc(fmt(m, c.league)) : '—'}</span>
    ${pctMeter(c)}
  </div>`;
}
const rateHead = () => `<div class="rrow rhead" aria-hidden="true"><span>Metric</span><span class="rv">Team</span><span class="rbar-h"><span class="lgkey"><i class="b"></i>team</span><span class="lgkey"><i class="l"></i>league</span></span><span class="rlg">League</span><span class="pctm">Pctile</span></div>`;

/* ---------- the diverging fill (heat table, field zones, run lanes): more / less than the league ---------- */
function divCap(m, L) { return m.unit === 'mean' ? Math.max(0.05, Math.abs(L ?? 1) * 0.2) : 0.12; }
function divFill(m, c, cap) {
  if (!c || c.value == null || c.diff == null || c.small) return '';
  const t = Math.max(-1, Math.min(1, c.diff / (cap ?? divCap(m, c.league))));
  const amt = Math.round(Math.abs(t) * 62);
  if (amt < 4) return '';
  return `background:color-mix(in srgb, var(${t > 0 ? '--s1' : '--s2'}) ${amt}%, var(--panel))`;
}
const divLegend = meta => `<span class="divlegend"><span>Less than the league</span><i class="ramp" aria-hidden="true"></i><span>More</span><span class="muted">· greyed: n under ${meta.min_n}</span></span>`;

/* ------------------------------------------------------------------ mockbar */
function applyTheme() {
  document.documentElement.setAttribute('data-pal', S.pal);
  document.documentElement.setAttribute('data-mode', S.mode === 'system' ? (systemDark() ? 'dark' : 'light') : S.mode);
}
function syncMockbar() {
  $$('#screenPick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.screen)));
  $('#teamPick').innerHTML = TEAMS_WITH_PAGES.map(t => `<button type="button" data-v="${esc(t)}" aria-pressed="${t === S.team}">${esc(t)}</button>`).join('');
  $('#teamGrp').style.opacity = S.screen === 'team' ? '' : '.45';
  $('#weekPick').innerHTML = WEEK_KEYS.map(k => { const [sn, w] = k.split('_'); const st = weekFor(k).status; const wa = weekFor(k); const tag = st !== 'ok' ? st.replace('_', ' ') : !wa.forecast ? 'no forecast' : wa.graded && wa.graded.graded ? 'graded' : 'forecast'; return `<button type="button" data-v="${esc(k)}" aria-pressed="${k === S.weekKey}">${sn} wk ${w} · ${tag}</button>`; }).join('');
  $('#weekGrp').style.opacity = S.screen === 'week' ? '' : '.45';
  $('#updPick').innerHTML = UPD_PICKS.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${v === S.upd}">${esc(l)}${v === 'real' ? '' : ' *'}</button>`).join('');
  $('#updGrp').style.opacity = S.screen === 'week' ? '' : '.45';
  $$('#statePick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.state)));
  $$('#screenPick button').forEach(b => { if (b.dataset.v === 'plays') b.setAttribute('aria-pressed', String(S.screen === 'team' && S.pb.open)); else if (b.dataset.v === 'team') b.setAttribute('aria-pressed', String(S.screen === 'team' && !S.pb.open)); });
  $$('#pbPick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.pb.state)));
  $$('#libPick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.pb.lib)));
  $$('#motionPick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.pb.motion)));
  ['pbGrp', 'libGrp', 'motionGrp'].forEach(id => { const g = document.getElementById(id); if (g) g.style.opacity = S.screen === 'team' && S.pb.open ? '' : '.45'; });
  applyTheme();
}
try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (S.mode === 'system') applyTheme(); }); } catch (e) { /* old browsers */ }
$('#screenPick').addEventListener('click', e => {
  const b = e.target.closest('[data-v]'); if (!b) return;
  if (b.dataset.v === 'plays') { openPlays(); return; }
  S.screen = b.dataset.v; syncMockbar(); render();
});
function openPlays() {
  S.screen = 'team'; S.state = 'ok'; S.pb.open = true; syncMockbar(); render();
  const c = document.getElementById('pbCard'); if (c) c.scrollIntoView({ block: 'start' });
}
const pbReset = () => { S.pb.week = null; S.pb.sel = null; S.pb.real = null; S.pb.down = S.pb.type = S.pb.result = null; PS.playing = false; PS.key = null; };
$('#pbPick').addEventListener('click', e => {
  const b = e.target.closest('[data-v]'); if (!b) return;
  pbReset(); S.pb.state = b.dataset.v;
  if (S.pb.state === 'filtered') { const f = P.plays_filtered; S.pb.state = 'ok'; S.team = f.team; S.side = f.side; S.season = f.season; S.pb.week = f.week; S.pb.type = f.filters.type; S.pb.result = f.filters.result; S.pb.down = f.filters.down; }
  if (S.pb.state === 'zero') { S.pb.state = 'ok'; S.pb.down = 4; S.pb.result = 'touchdown'; }
  openPlays();
});
$('#libPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.pb.lib = b.dataset.v; S.pb.real = null; PS.key = null; openPlays(); });
$('#motionPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.pb.motion = b.dataset.v; PS.playing = false; PS.key = null; openPlays(); });
$('#teamPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.team = b.dataset.v; S.screen = 'team'; S.season = NEWEST; S.hist = false; pbReset(); syncMockbar(); render(); });
$('#weekPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.weekKey = b.dataset.v; S.screen = 'week'; S.state = 'ok'; syncMockbar(); render(); });
$('#updPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.upd = b.dataset.v; S.screen = 'week'; S.state = 'ok'; const cw = (P.weeks.weeks.find(w => w.is_current) || {}).week; if (cw && P.play_calls[`${P.weeks.season}_${cw}`]) S.weekKey = `${P.weeks.season}_${cw}`; syncMockbar(); render(); });
$('#statePick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.state = b.dataset.v; syncMockbar(); render(); });

/* ------------------------------------------------------------------ shell (as the control room) */
function renderSide() {
  const cur = k => String(S.screen === k);
  const wk = weekFor(S.weekKey);
  const W = P.weeks; // the real sidebar answer (GET /api/weeks)
  $('#side').innerHTML = `
    <div class="brand"><div class="brand-mark">CR</div><div style="flex:1;min-width:0"><b>Control Room</b><small>NFL Analytics Engine</small></div>
      <button class="iconbtn" id="gearBtn" aria-expanded="${S.settings}" aria-controls="settingsPop" title="Appearance"><svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="2.2"/><path d="M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M3.4 12.6l1.3-1.3M11.3 4.7l1.3-1.3"/></svg></button>
      ${S.settings ? settingsPop() : ''}</div>
    <div class="navgrp"><div class="eyebrow">${W.season} weeks</div>
      ${W.weeks.map(w => `<button class="navi" data-nav="${w.week === wk.week && W.season === wk.season ? 'week' : 'other'}" data-what="Week ${w.week}" aria-current="${String(S.screen === 'week' && w.week === wk.week && W.season === wk.season)}"><span class="wk">${w.week}</span><span class="grow">Week ${w.week}<span class="sub">${esc(w.is_current ? 'This week' : (w.detail || w.label))}</span></span><span class="chip ${w.status === 'published' ? 'ok' : w.status === 'failed' ? 'err' : 'flat'}">${w.status === 'published' ? '<span class="ic">✓</span>' : ''}${esc(w.status === 'published' ? 'Published' : w.label)}</span></button>`).join('')}
      <button class="navi" disabled title="Live weekly runs started in week ${W.go_live_week}"><span class="wk">1–${W.go_live_week - 1}</span><span class="grow">Before go-live<span class="sub">Walk-forward rows only</span></span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">Season</div>
      <button class="navi" data-nav="other" data-what="The Scorecard">${ICON.season}<span class="grow">Scorecard</span></button>
      <button class="navi" data-nav="other" data-what="Teams &amp; rankings">${ICON.teams}<span class="grow">Teams &amp; rankings</span></button>
      <button class="navi" data-nav="other" data-what="Models">${ICON.models}<span class="grow">Models</span></button>
      <button class="navi" data-nav="other" data-what="Alerts">${ICON.alerts}<span class="grow">Alerts</span><span class="chip flat">0</span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">Explore</div>
      <button class="navi" data-nav="grid" aria-current="${String(S.screen === 'grid' || S.screen === 'team')}">${ICON.playcalling}<span class="grow">Play calling</span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">System</div>
      <button class="navi" data-nav="other" data-what="Health">${ICON.health}<span class="grow">Health</span><span class="chip ok"><span class="ic">✓</span>OK</span></button>
    </div>
    <div class="side-foot">
      <div><span>Run lock</span><span>free</span></div>
      <div><span>Neo4j</span><span>up · 5.26</span></div>
      <div><span>Data drive</span><span>${esc(P.meta && P.meta.data_root && P.meta.data_root.free_gb != null ? P.meta.data_root.free_gb + ' GB free' : '—')}</span></div>
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
  S.screen = b.dataset.nav;
  if (S.screen === 'other') S.other = b.dataset.what;
  syncMockbar(); render(); window.scrollTo({ top: 0 });
});

/* ------------------------------------------------------------------ the answer for the current screen and state */
const WEEK_EMPTY = { not_built: 'not_built', not_yet: 'not_yet' };
function stateAnswer(kind) { // the reader's own empty answers, when the dump has them
  const st = (P.states || {})[S.state];
  return st && st[kind] ? st[kind] : null;
}
function emptyCard(meta, glyph, title, mockText) {
  const msg = meta && meta.message;
  const cmd = msg && (msg.match(/`([^`]+)`/) || [])[1];
  const text = msg ? esc(msg.replace(/`([^`]+)`/g, '$1')) : esc(mockText || '');
  return `<div class="card"><div class="empty"><div class="glyph">${glyph}</div><h3>${esc(title)}</h3><p>${text}</p>${cmd ? `<div class="cmd" style="margin-top:6px">${esc(cmd)}</div>` : ''}</div></div>`;
}
function loadingCard(what) {
  return `${mockOnly('The app shows this while the answer loads (a short read of one table).')}
    <div class="card pad sk" aria-busy="true" aria-label="Loading ${esc(what)}"><span class="skl w40"></span><span class="skl w70"></span><span class="skl w55"></span><div class="skgrid">${'<span class="skb"></span>'.repeat(8)}</div></div>`;
}

/* ------------------------------------------------------------------ header + tabs */
function renderTop() {
  const top = $('#top');
  if (S.screen === 'other') { top.innerHTML = `<div class="titleblock"><span class="eyebrow">Control room</span><h1>${esc(S.other)}</h1><span class="muted">Unchanged by Play calling (PC01)</span></div>`; return; }
  if (S.screen === 'week') {
    const wk = weekFor(S.weekKey);
    top.innerHTML = `<div class="titleblock"><span class="eyebrow">${wk.season} regular season · this week</span><h1>Week ${wk.week}</h1>
      <div class="chips"><span class="chip ok"><span class="ic">✓</span>Published</span><span class="chip flat">${wk.games.length} games</span></div></div>`;
    return;
  }
  const tr = teamsFor(S.season);
  const asOf = tr.as_of_week != null ? `as of week ${tr.as_of_week}${tr.through_week ? ` · weeks 1–${tr.through_week} played` : ''}` : '';
  const seasons = tr.seasons && tr.seasons.length ? tr.seasons : SEASONS;
  // a segmented switch for two or three seasons; a select beyond that (the tables go back to 2016)
  const seasonSeg = seasons.length <= 3
    ? `<span class="seg" role="group" aria-label="Season">${seasons.map(s => `<button type="button" data-season="${s}" aria-pressed="${s === S.season}"${SEASONS.includes(s) ? '' : ' disabled title="Not in this mockup"'}>${s}</button>`).join('')}</span>`
    : `<label class="sortsel"><span class="eyebrow">Season</span><select id="seasonSel">${seasons.map(s => `<option value="${s}"${s === S.season ? ' selected' : ''}${SEASONS.includes(s) ? '' : ' disabled'}>${s}${SEASONS.includes(s) ? '' : ' (not in this mockup)'}</option>`).join('')}</select></label>`;
  const ftn = tr.ftn_waiting && tr.ftn_waiting.length ? `<span class="chip warn" data-tip="${esc('FTN hasn\'t charted these games yet\n' + tr.ftn_waiting.join('\n') + '\nPlay-action, screens, RPO, motion, blitz and box rates wait for them')}" tabindex="0"><span class="ic">!</span>FTN waiting: ${tr.ftn_waiting.length}</span>` : '';
  if (S.screen === 'grid') {
    top.innerHTML = `<div class="titleblock"><span class="eyebrow">Explore</span><h1>Play calling</h1>
      <div class="chips"><span class="chip flat">${S.season}${asOf ? ' · ' + esc(asOf) : ''}</span>${ftn}</div></div>
      <div class="right">${seasonSeg}</div>`;
    return;
  }
  const t = team(S.team);
  const r = teamFor(S.season, S.team, S.side);
  const ng = r && r.next_game;
  top.innerHTML = `<div class="titleblock"><span class="eyebrow"><button class="linkbtn" data-nav-grid>Play calling</button> · ${S.season}</span>
      <h1 class="tname"><i style="background:${esc(t.color)}"></i>${esc(t.name)}</h1>
      <div class="chips"><span class="chip flat">${r ? `${r.games} game${r.games === 1 ? '' : 's'}` : '—'}${asOf ? ' · ' + esc(asOf) : ''}</span>${ftn}${ng ? `<button class="chip run linkchip" data-go-week>Next: ${ng.home ? 'vs' : 'at'} ${esc(ng.opponent)} · week ${ng.week} → Play calls</button>` : ''}</div></div>
    <div class="right"><span class="seg big" role="group" aria-label="Side">${['offense', 'defense'].map(s => `<button type="button" data-side="${s}" aria-pressed="${S.side === s}">${s === 'offense' ? 'Offense' : 'Defense'}</button>`).join('')}</span>${seasonSeg}</div>`;
}
const TABS = [['pipeline', 'Pipeline'], ['digest', 'Digest'], ['games', 'Games'], ['players', 'Players'], ['results', 'Results'], ['mlops', 'MLOps'], ['graph', 'Graph'], ['game-day', 'Game day'], ['play-calls', 'Play calls']];
function renderTabs() {
  const nav = $('#tabs');
  if (S.screen !== 'week') { nav.hidden = true; nav.style.display = 'none'; nav.innerHTML = ''; return; }
  nav.hidden = false; nav.style.display = '';
  nav.innerHTML = TABS.map(([k, l]) => `<button class="tab" data-tab="${k}" aria-current="${k === 'play-calls' ? 'page' : 'false'}" aria-selected="${k === 'play-calls'}">${l}</button>`).join('');
}
$('#tabs').addEventListener('click', e => { const b = e.target.closest('[data-tab]'); if (!b || b.dataset.tab === 'play-calls') return; S.screen = 'other'; S.other = `The ${b.textContent} tab`; render(); });
$('#top').addEventListener('change', e => { if (e.target.id === 'seasonSel') { pbReset(); S.season = Number(e.target.value); render(); } });
$('#top').addEventListener('click', e => {
  const s = e.target.closest('[data-season]'); if (s) { pbReset(); S.season = Number(s.dataset.season); if (S.win === 'last_season' && S.season !== NEWEST) S.win = 'season'; render(); return; }
  const d = e.target.closest('[data-side]'); if (d) { pbReset(); S.side = d.dataset.side; store.set('pc.side', S.side); render(); return; }
  if (e.target.closest('[data-nav-grid]')) { S.screen = 'grid'; syncMockbar(); render(); return; }
  if (e.target.closest('[data-go-week]')) { goNextWeek(); }
});
function goNextWeek() {
  const r = teamFor(S.season, S.team, S.side); const ng = r && r.next_game;
  const k = ng && `${ng.season}_${ng.week}`;
  if (k && P.play_calls[k]) S.weekKey = k;
  S.screen = 'week'; S.state = 'ok'; syncMockbar(); render(); window.scrollTo({ top: 0 });
}

/* ------------------------------------------------------------------ views */
function render() {
  hideTip();
  renderSide(); renderTop(); renderTabs();
  const v = $('#view');
  if (S.screen === 'other') v.innerHTML = otherView();
  else if (S.screen === 'grid') v.innerHTML = gridView();
  else if (S.screen === 'team') v.innerHTML = teamView();
  else v.innerHTML = weekView();
}
function otherView() {
  return `<div class="notice accent"><span class="ic">i</span><div><b>Mockup: ${esc(S.other)} is unchanged by Play calling (PC01).</b> It's drawn in the <a href="${CR_MOCKUP}" target="_blank" rel="noopener">control room mockup</a>; this page shows the new Explore → Play calling pages and the Play calls week tab only.<div style="margin-top:10px"><button class="btn sm" data-nav-grid>Open Play calling</button></div></div></div>`;
}

/* ================================================================== the teams grid */
function sortedTeams(tr) {
  const col = tr.columns.find(c => c.id === S.sort.id);
  const val = t => { const c = col && t.cells[col.id]; return c && c.value != null ? c.value : null; };
  return [...tr.teams].sort((a, b) => {
    if (!col) return a.team.localeCompare(b.team) * S.sort.dir;
    const va = val(a), vb = val(b);
    if (va == null && vb == null) return a.team.localeCompare(b.team);
    if (va == null) return 1; if (vb == null) return -1;
    return (va - vb) * S.sort.dir || a.team.localeCompare(b.team);
  });
}
const SIDE_SHORT = { offense: 'Offense', defense: 'Defense' };
function gridView() {
  if (S.state === 'loading') return loadingCard('the teams');
  if (S.state !== 'ok') {
    const st = stateAnswer('teams') || Object.assign({}, teamsFor(S.season), S.state === 'not_built' ? { message: null } : {});
    if (S.state === 'not_yet') return mockOnly("A season's grid is never \"not yet\": before week 1's games it shows last season (the reader's <code>window: last_season</code>). Not yet applies to a week's Play calls tab.") + gridView0(teamsFor(S.season));
    return (stateAnswer('teams') ? '' : mockOnly('The reader\'s not-built answer, drawn with a stand-in message.')) + emptyCard(st, 'PC', `No play-calling tables for ${st.season || S.season}`, `Run \`uv run nfl playcalling build --season ${S.season}\` to build them.`);
  }
  return gridView0(teamsFor(S.season));
}
function gridView0(tr) {
  const cols = tr.columns;
  const sig = cols.filter(c => c.signature);
  const sortCol = cols.find(c => c.id === S.sort.id);
  const rows = sortedTeams(tr);
  const sortSel = `<label class="sortsel"><span class="eyebrow">Sort by</span><select id="sortSel">
      <option value="team"${S.sort.id === 'team' ? ' selected' : ''}>Team</option>
      ${['offense', 'defense'].map(sd => `<optgroup label="${SIDE_SHORT[sd]}">${cols.filter(c => c.side === sd).map(c => `<option value="${esc(c.id)}"${c.id === S.sort.id ? ' selected' : ''}>${esc(c.short)}</option>`).join('')}</optgroup>`).join('')}
    </select></label>
    <button class="btn sm" id="sortDir" aria-label="Sort direction">${S.sort.dir > 0 ? (sortCol ? 'Lowest first' : 'A → Z') : (sortCol ? 'Highest first' : 'Z → A')}</button>`;
  const viewSeg = `<span class="seg" role="group" aria-label="View"><button type="button" data-gv="tiles" aria-pressed="${S.gridView === 'tiles'}">Tiles</button><button type="button" data-gv="table" aria-pressed="${S.gridView === 'table'}">Table</button></span>`;
  const winNote = tr.window === 'last_season' ? `<div class="notice"><span class="ic">i</span><div><b>Before week 1's games:</b> the numbers are last season's.</div></div>` : '';
  const body = S.gridView === 'tiles'
    ? `<div class="ptiles">${rows.map(t => tile(t, sig, sortCol, tr)).join('')}</div>`
    : gridTable(tr, rows);
  return `${winNote}
    <div class="sec-h"><h2>32 teams</h2><p>${esc(tr.window === 'season' ? `${tr.season} season so far` : `${tr.season - 1} season`)} · tap a team for its page · grey = fewer than ${tr.min_n} plays</p>
      <div class="right">${sortSel}${viewSeg}</div></div>
    ${body}
    <div class="foot">Read from <span class="mono">playcalling/${tr.season}/team_tendencies.parquet</span> (the newest as-of week). Pass rate over expected (PROE) is in points; the league sits below 0 (nflverse's expected pass rate isn't re-centred), so compare with the league marker, not with 0. A percentile is "more of it", not "better".</div>`;
}
/** One scale per grid column, so the 32 tiles' bars compare. */
function colDomain(tr, col) { return rowDomain(col, tr.teams.map(t => t.cells[col.id])); }
/** One scale for a set of cells (a grid column, a history row), so their bars compare. */
function rowDomain(col, all) {
  const cells = all.filter(c => c && c.value != null);
  if (!cells.length) return undefined;
  if (centred(col, 0, (cells[0] || {}).league)) return { span: Math.max(0.05, ...cells.map(c => Math.abs(c.value - (c.league ?? 0)))) * 1.08 };
  const top = Math.max(...cells.map(c => Math.max(c.value, c.league ?? 0)), 0);
  return { top: col.unit === 'share' ? Math.min(1, niceTop(top)) : niceTop(top) };
}
function tile(t, sig, sortCol, tr) {
  const sigs = sig.map(col => {
    const c = t.cells[col.id];
    const dom = (tr._dom || (tr._dom = {}))[col.id] || (tr._dom[col.id] = colDomain(tr, col));
    return `<span class="psig${c && c.small ? ' small' : ''}" data-tip="${esc(cellTip(col, c, t.team, SIDE_SHORT[col.side], tr, allowedNote(col, col.side)))}">
      <span class="k">${esc(SIDE_SHORT[col.side])}</span><span class="kk">${esc(col.short)}</span>
      <span class="v num">${esc(fmt(col, c && c.value))}</span>
      ${rateBar(col, c, true, dom)}
      <span class="lg num">lg ${esc(fmt(col, c && c.league))}${c && c.pct != null ? ` · ${ord(c.pct)}` : ''}${c ? ` · n ${c.n}` : ''}</span></span>`;
  }).join('');
  const extra = sortCol && !sortCol.signature ? (() => { const c = t.cells[sortCol.id]; return `<span class="psort${c && c.small ? ' small' : ''}"><span>${esc(SIDE_SHORT[sortCol.side])} · ${esc(sortCol.short)}</span><b class="num">${esc(fmt(sortCol, c && c.value))}</b><span class="muted num">lg ${esc(fmt(sortCol, c && c.league))}</span></span>`; })() : '';
  return `<button type="button" class="ptile" data-team="${esc(t.team)}" style="--tc:${esc(team(t.team).color)}">
    <span class="pt-h"><i></i><b>${esc(t.team)}</b><span class="nick">${esc(team(t.team).nick)}</span><small>${t.games} g</small></span>
    <span class="pt-sigs">${sigs}</span>${extra}</button>`;
}
function gridTable(tr, rows) {
  const cols = tr.columns;
  const th = c => {
    const on = S.sort.id === c.id;
    return `<th class="r"><button class="thsort" data-sort="${esc(c.id)}" aria-sort="${on ? (S.sort.dir > 0 ? 'ascending' : 'descending') : 'none'}" title="${esc(c.label)}">${esc(c.short)}${on ? (S.sort.dir > 0 ? ' ▲' : ' ▼') : ''}</button></th>`;
  };
  const groups = ['offense', 'defense'].map(sd => [sd, cols.filter(c => c.side === sd)]);
  const head = `<tr><th rowspan="2"><button class="thsort" data-sort="team">Team${S.sort.id === 'team' ? (S.sort.dir > 0 ? ' ▲' : ' ▼') : ''}</button></th>${groups.map(([sd, cs]) => `<th colspan="${cs.length}" class="grph">${SIDE_SHORT[sd]}</th>`).join('')}</tr><tr>${groups.map(([, cs]) => cs.map(th).join('')).join('')}</tr>`;
  const league = `<tr class="lgrow"><td>League</td>${groups.map(([, cs]) => cs.map(c => { const any = tr.teams.map(t => t.cells[c.id]).find(x => x && x.league != null); return `<td class="r num">${esc(fmt(c, any && any.league))}</td>`; }).join('')).join('')}</tr>`;
  const body = rows.map(t => `<tr class="teamrow" data-team="${esc(t.team)}" tabindex="0"><td>${tchip(t.team, true)}</td>${groups.map(([sd, cs]) => cs.map(c => { const x = t.cells[c.id]; return `<td class="r num hc${x && x.small ? ' small' : ''}" style="${divFill(c, x)}" tabindex="0" data-tip="${esc(cellTip(c, x, t.team, SIDE_SHORT[sd], tr, allowedNote(c, sd)))}">${esc(fmt(c, x && x.value))}<small>n ${x ? x.n : 0}</small></td>`; }).join('')).join('')}</tr>`).join('');
  return `<div class="card"><div class="card-h"><h2>Every column</h2><span class="muted">sort by any column · cells tinted by the difference from the league</span><div class="right">${divLegend(tr)}</div></div>
    <div class="tablewrap"><table class="tbl gridtbl"><thead>${head}</thead><tbody>${league}${body}</tbody></table></div></div>`;
}

/* ================================================================== a team page */
function teamView() {
  if (S.state === 'loading') return loadingCard('the team page');
  const r0 = teamFor(S.season, S.team, S.side);
  if (S.state === 'not_built' || S.state === 'not_yet') {
    const st = stateAnswer('team');
    const meta = st || { message: null };
    const title = S.state === 'not_built' ? `No play-calling tables for ${(st && st.season) || S.season}` : 'Not as of this week yet';
    return (st ? '' : mockOnly('Drawn with a stand-in message; the dump has no such answer.')) + emptyCard(meta, 'PC', title, S.state === 'not_built' ? `Run \`uv run nfl playcalling build --season ${S.season}\` to build them.` : 'The tables are built, but not as of this week yet.');
  }
  if (r0 && r0.status !== 'ok') return emptyCard(r0, 'PC', r0.status === 'not_yet' ? 'Not ready yet' : `No play-calling tables for ${S.season}`);
  if (!r0) return mockOnly(`The dump has no ${S.season} page for ${esc(S.team)}'s ${S.side}; pick another team or season.`);
  const r = r0;
  if (S.win === 'last_season' && !hasWindow(r, 'last_season')) S.win = 'season';
  // with 4 games or fewer, the last four games are the season so far: one button, not two
  const sameAsSeason = r.games > 0 && r.games <= 4;
  if (sameAsSeason && S.win === 'last4') S.win = 'season';
  const wins = ['season', 'last4', 'last_season'].filter(w => hasWindow(r, w) && !(w === 'last4' && sameAsSeason));
  const winSeg = `<span class="seg" role="group" aria-label="Window">${wins.map(w => `<button type="button" data-win="${w}" aria-pressed="${S.win === w}">${esc(winText(r, w))}</button>`).join('')}</span>`;
  const sideWord = S.side === 'offense' ? 'offense' : 'defense';
  return `
    <div class="card summary"><div class="sumh"><span class="eyebrow">${esc(team(S.team).nick)} ${sideWord} · in five seconds</span></div>
      <ul class="sumlist">${r.summary.map(s => `<li>${esc(s)}</li>`).join('')}</ul>
      ${S.side === 'defense' ? `<p class="sumnote">A defense's page shows its own calls (blitz, rushers, box) and what offenses do against it; the second depends on the offenses it faced.</p>` : ''}
    </div>
    ${nextGameCard(r)}
    <div class="wintool"><span class="eyebrow">Window</span>${winSeg}<span class="muted">${esc(winHelp(r))}</span></div>
    ${identityCard(r)}
    ${situationsCard(r)}
    ${fieldCard(r)}
    ${weeklyCard(r)}
    ${recordCard(r)}
    ${playsCard(r)}
    ${historyCard(r)}
    ${r.notes && r.notes.length ? `<div class="foot">${r.notes.map(esc).join('<br>')}</div>` : ''}`;
}
function hasWindow(r, w) {
  return r.identity.some(g => g.rows.some(x => x.windows[w]));
}
function winText(r, w) {
  if (w === 'season') return `Season${r.games ? ` · ${r.games} g` : ''}`;
  if (w === 'last4') return 'Last 4';
  return `Last season · ${r.season - 1}`;
}
function winHelp(r) {
  if (S.win === 'season') return `Every game of ${r.season} before week ${r.as_of_week}.${r.games > 0 && r.games <= 4 ? ' Last 4 is the same until a fifth game.' : ''}`;
  if (S.win === 'last4') return 'The last four games (fewer early in the season).';
  return `Every game of ${r.season - 1}, playoffs in.`;
}
function who(r) { return S.side === 'offense' ? r.team : `vs ${r.team}`; }

/* ---------- Identity ---------- */
function identityCard(r) {
  const groups = r.identity.map(g => `<section class="idgrp"><h3>${esc(g.title)}</h3>${g.note ? `<p class="gnote">${esc(g.note)}</p>` : ''}
      <div class="rrows">${rateHead()}${g.rows.map(m => rateRow(m, m.windows[S.win], who(r), r, S.side, WIN_LABEL[S.win])).join('')}</div></section>`).join('');
  return `<div class="card"><div class="card-h"><h2>Identity</h2><span class="muted">${S.side === 'offense' ? 'what it calls' : 'what it calls, and what offenses do against it'} · ${esc(WIN_LABEL[S.win].toLowerCase())} · the bar is the team, the tick the league</span></div>
    <div class="card-b idgrid">${groups}</div></div>`;
}

/* ---------- By situation ---------- */
function situationsCard(r) {
  const s = r.situations;
  if (!s) return '';
  const fam = s.families.find(f => f.family === S.fam) || s.families[0];
  const famSeg = `<span class="seg" role="group" aria-label="Situation">${s.families.map(f => `<button type="button" data-fam="${f.family}" aria-pressed="${f.family === fam.family}">${esc(f.title)}</button>`).join('')}</span>`;
  const cell = (row, m, base) => {
    const c = (row.cells[m.metric] || {})[S.win];
    const tip = cellTip(m, c, who(r), row.label, r, allowedNote(m, S.side));
    return `<td class="hc${c && c.small ? ' small' : ''}${!c || c.value == null ? ' none' : ''}" style="${divFill(m, c)}" tabindex="0" data-tip="${esc(tip)}"><b class="num">${esc(fmt(m, c && c.value))}</b><small class="num">${c ? `n ${c.n}` : '—'}</small></td>`;
  };
  const head = `<tr><th>Situation</th>${s.metrics.map(m => `<th class="c" title="${esc(m.label)}">${esc(m.short)}${m.situation === 'neutral' ? '' : ''}</th>`).join('')}</tr>`;
  const base = `<tr class="baserow"><th scope="row">${esc(s.baseline.label)}<small>the row to compare with</small></th>${s.metrics.map(m => cell(s.baseline, m, true)).join('')}</tr>`;
  const rows = fam.rows.map(row => `<tr><th scope="row">${esc(row.label)}</th>${s.metrics.map(m => cell(row, m)).join('')}</tr>`).join('');
  return `<div class="card"><div class="card-h"><h2>By situation</h2><span class="muted">${esc(WIN_LABEL[S.win].toLowerCase())} · each cell against the league in the same situation</span><div class="right">${famSeg}</div></div>
    <div class="card-b"><div class="tablewrap"><table class="tbl heat">${'<thead>' + head + '</thead>'}<tbody>${base}${rows}</tbody></table></div>
    <div class="heatfoot">${divLegend(r)}<span class="muted">Hover or focus a cell for the league's rate and the percentile.</span></div></div></div>`;
}

/* ---------- Where the ball goes: the half-field and the offensive line ---------- */
function fieldCard(r) {
  const f = r.field;
  if (!f && !r.runs.length) return '';
  const title = S.side === 'offense' ? 'Where the ball goes' : 'Where offenses go against it';
  return `<div class="card"><div class="card-h"><h2>${title}</h2><span class="muted">${esc(WIN_LABEL[S.win].toLowerCase())} · shares, next to the league's · zones and lanes tinted by the difference</span><div class="right">${divLegend(r)}</div></div>
    <div class="card-b fieldgrid">
      ${f ? `<section><h3 class="dh">Passes: depth × direction</h3>${fieldSVG(f, r)}${dirStrip(f, r)}<div class="rrows" style="margin-top:12px">${rateHead()}${f.depth.map(m => rateRow(m, m.windows[S.win], who(r), r, S.side, WIN_LABEL[S.win])).join('')}</div></section>` : ''}
      ${r.runs.length ? `<section><h3 class="dh">Designed runs: where they go</h3>${linesSVG(r.runs, r)}</section>` : ''}
    </div></div>`;
}
const ZONE_M = { metric: '', label: '', short: '', unit: 'share', digits: 0, per: 'attempt', caller: 'offense', help: null };
function zoneMeta(z, kind) {
  const depthWord = z.depth ? (z.depth === 'deep' ? 'Deep' : 'Short') + ' ' : '';
  return Object.assign({}, ZONE_M, { metric: z.metric || z.lane, label: kind === 'run' ? `Runs to ${z.label.toLowerCase()}` : `${depthWord}${z.direction}`.replace(/^./, c => c.toUpperCase()) + ' passes', per: kind === 'run' ? 'designed run' : 'attempt', help: kind === 'run' ? 'A share of the designed runs with a direction (the seven add to 100%).' : z.depth ? 'A share of the pass attempts with a depth and a direction (the six add to 100%). Deep is 16+ air yards (the gamebook).' : 'A share of the pass attempts with a direction.' });
}
function chalkDefs(id) {
  return `<defs><marker id="${id}" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" style="fill:var(--chalk)"/></marker></defs>`;
}
function fieldSVG(f, r) {
  const W = 360, H = 340, LOS = 272, YD = 7.6, L = 12, R = W - 12;
  const cols = [L, L + (R - L) / 3, L + 2 * (R - L) / 3, R];
  const cx = d => ({ left: (cols[0] + cols[1]) / 2, middle: (cols[1] + cols[2]) / 2, right: (cols[2] + cols[3]) / 2 })[d];
  const deepY = LOS - 16 * YD;
  let s = chalkDefs('arw');
  // the turf: 5-yard stripes, the yard lines, the hash marks, the sidelines
  s += `<rect x="0" y="0" width="${W}" height="${H}" rx="8" style="fill:var(--field)"/>`;
  for (let k = 0; k * 5 * YD < LOS; k++) { const y1 = LOS - (k + 1) * 5 * YD, y2 = LOS - k * 5 * YD; if (k % 2) s += `<rect x="${L}" y="${Math.max(0, y1)}" width="${R - L}" height="${y2 - Math.max(0, y1)}" style="fill:var(--field-2)"/>`; }
  s += `<line x1="${L}" y1="0" x2="${L}" y2="${H}" class="ch" stroke-width="2"/><line x1="${R}" y1="0" x2="${R}" y2="${H}" class="ch" stroke-width="2"/>`;
  for (let y = 1; LOS - y * YD > 4; y++) {
    const yy = LOS - y * YD;
    if (y % 5 === 0) s += `<line x1="${L}" x2="${R}" y1="${yy}" y2="${yy}" class="ch" stroke-width="1.2" opacity=".55"/><text x="${L + 6}" y="${yy - 3}" class="ydl">${y}</text>`;
    else s += `<line x1="${W * 0.41}" x2="${W * 0.41 + 7}" y1="${yy}" y2="${yy}" class="ch" opacity=".5"/><line x1="${W * 0.59 - 7}" x2="${W * 0.59}" y1="${yy}" y2="${yy}" class="ch" opacity=".5"/><line x1="${L}" x2="${L + 6}" y1="${yy}" y2="${yy}" class="ch" opacity=".5"/><line x1="${R - 6}" x2="${R}" y1="${yy}" y2="${yy}" class="ch" opacity=".5"/>`;
  }
  // zone edges: left | middle | right and short | deep (the gamebook's 16 air yards)
  s += `<line x1="${cols[1]}" x2="${cols[1]}" y1="6" y2="${LOS}" class="ch" stroke-dasharray="3 5" opacity=".7"/><line x1="${cols[2]}" x2="${cols[2]}" y1="6" y2="${LOS}" class="ch" stroke-dasharray="3 5" opacity=".7"/>`;
  s += `<line x1="${L}" x2="${R}" y1="${deepY}" y2="${deepY}" class="ch" stroke-dasharray="7 5" stroke-width="1.5"/><text x="${R - 6}" y="${deepY - 5}" text-anchor="end" class="zl">DEEP · 16+ YDS</text><text x="${R - 6}" y="${deepY + 13}" text-anchor="end" class="zl">SHORT</text>`;
  // the line of scrimmage, the line and the quarterback
  s += `<line x1="${L}" x2="${R}" y1="${LOS}" y2="${LOS}" class="los" stroke-width="2.5"/><text x="${L + 6}" y="${LOS + 14}" class="zl">LINE OF SCRIMMAGE</text>`;
  [-2, -1, 0, 1, 2].forEach(i => { s += i === 0 ? `<rect x="${W / 2 - 6}" y="${LOS + 3}" width="12" height="12" rx="1.5" class="pl"/>` : `<circle cx="${W / 2 + i * 18}" cy="${LOS + 9}" r="6.5" class="pl"/>`; });
  const qb = { x: W / 2, y: LOS + 40 };
  s += `<circle cx="${qb.x}" cy="${qb.y}" r="8" class="pl qb"/><text x="${qb.x}" y="${qb.y + 3.5}" text-anchor="middle" class="plt">QB</text>`;
  // throws: one route per zone, as thick as its share
  const zones = f.zones.map(z => ({ z, c: z.windows[S.win], x: cx(z.direction), y: z.depth === 'deep' ? deepY - 64 : LOS - 74 }));
  const n = (zones.find(o => o.c) || {}).c;
  const routes = zones.map(o => {
    if (!o.c || o.c.value == null) return '';
    const w = 1.2 + o.c.value * 22;
    const ex = o.x, ey = o.y + 30;
    const bend = o.z.direction === 'middle' ? 0 : (o.z.direction === 'left' ? -1 : 1) * (o.z.depth === 'deep' ? 26 : 10);
    return `<path d="M${qb.x},${qb.y - 10} Q${(qb.x + ex) / 2 + bend},${(qb.y + ey) / 2 + (o.z.depth === 'deep' ? -30 : 0)} ${ex},${ey}" fill="none" class="route${o.c.small ? ' small' : ''}" stroke-width="${w.toFixed(1)}" marker-end="url(#arw)"/>`;
  }).join('');
  const boxes = zones.map(o => {
    const m = zoneMeta(o.z), c = o.c;
    const tip = cellTip(m, c, who(r), WIN_LABEL[S.win], r, allowedNote({ caller: 'offense' }, S.side));
    return `<g class="zbox${c && c.small ? ' small' : ''}" tabindex="0" data-tip="${esc(tip)}"><rect x="${o.x - 40}" y="${o.y - 2}" width="80" height="34" rx="6" style="${divFill(m, c).replace('background', 'fill') || 'fill:var(--panel)'}"/><text x="${o.x}" y="${o.y + 15}" text-anchor="middle" class="zv">${esc(fmt(m, c && c.value))}</text><text x="${o.x}" y="${o.y + 27}" text-anchor="middle" class="zs">lg ${esc(fmt(m, c && c.league))}</text></g>`;
  }).join('');
  const caption = n ? `${n.n} attempts with a depth and direction${n.small ? ' · small sample' : ''}` : 'no attempts';
  return `<figure class="fieldfig"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(`Pass depth by direction, ${WIN_LABEL[S.win]}: ` + zones.map(o => `${o.z.depth} ${o.z.direction} ${fmt(ZONE_M, o.c && o.c.value)}`).join(', '))}">${s}${routes}${boxes}</svg><figcaption class="muted">${esc(caption)} · offense moving up the page · left and right from the quarterback's view</figcaption></figure>`;
}
function dirStrip(f, r) {
  return `<div class="dirstrip">${f.directions.map(z => { const m = zoneMeta(z), c = z.windows[S.win]; return `<span class="dcell${c && c.small ? ' small' : ''}" style="${divFill(m, c)}" tabindex="0" data-tip="${esc(cellTip(m, c, who(r), 'every depth · ' + WIN_LABEL[S.win], r))}"><span class="eyebrow">${esc(z.direction)}</span><b class="num">${esc(fmt(m, c && c.value))}</b><small class="num">lg ${esc(fmt(m, c && c.league))}</small></span>`; }).join('')}</div>`;
}
const LANE_X = { left_end: 46, left_tackle: 120, left_guard: 170, middle: 220, right_guard: 270, right_tackle: 320, right_end: 394 };
const LANE_SHORT = { left_end: 'L END', left_tackle: 'L TACKLE', left_guard: 'L GUARD', middle: 'MIDDLE', right_guard: 'R GUARD', right_tackle: 'R TACKLE', right_end: 'R END' };
function linesSVG(runs, r) {
  const W = 440, H = 300, LOS = 170;
  let s = chalkDefs('arwr');
  s += `<rect x="0" y="0" width="${W}" height="${H}" rx="8" style="fill:var(--field)"/>`;
  [LOS - 76, LOS - 38].forEach(y => { s += `<line x1="8" x2="${W - 8}" y1="${y}" y2="${y}" class="ch" opacity=".35"/>`; });
  s += `<line x1="8" x2="${W - 8}" y1="${LOS}" y2="${LOS}" class="los" stroke-width="2.5"/><text x="12" y="${LOS - 6}" class="zl">LINE OF SCRIMMAGE</text>`;
  // the line: tackles and guards as circles, the center as a square (a playbook's convention), ends dashed
  const OL = [['T', 120], ['G', 170], ['C', 220], ['G', 270], ['T', 320]];
  OL.forEach(([p, x]) => { s += p === 'C' ? `<rect x="${x - 11}" y="${LOS + 5}" width="22" height="22" rx="2" class="pl"/>` : `<circle cx="${x}" cy="${LOS + 16}" r="11" class="pl"/>`; s += `<text x="${x}" y="${LOS + 20}" text-anchor="middle" class="plt">${p}</text>`; });
  [46, 394].forEach(x => { s += `<circle cx="${x}" cy="${LOS + 16}" r="11" class="pl end"/><text x="${x}" y="${LOS + 20}" text-anchor="middle" class="plt">E</text>`; });
  s += `<circle cx="220" cy="${LOS + 46}" r="10" class="pl qb"/><text x="220" y="${LOS + 50}" text-anchor="middle" class="plt">QB</text>`;
  const rb = { x: 220, y: LOS + 92 };
  s += `<circle cx="${rb.x}" cy="${rb.y}" r="10" class="pl qb"/><text x="${rb.x}" y="${rb.y + 4}" text-anchor="middle" class="plt">RB</text>`;
  const lanes = runs.map(l => ({ l, c: l.windows[S.win], x: LANE_X[l.lane] }));
  const n = (lanes.find(o => o.c) || {}).c;
  const paths = lanes.map(o => {
    if (!o.c || o.c.value == null) return '';
    const w = 1.2 + o.c.value * 26;
    return `<path d="M${rb.x},${rb.y - 12} C${rb.x},${rb.y - 46} ${o.x},${LOS + 52} ${o.x},${LOS - 22}" fill="none" class="route${o.c.small ? ' small' : ''}" stroke-width="${w.toFixed(1)}" marker-end="url(#arwr)"/>`;
  }).join('');
  const boxes = lanes.map(o => {
    const m = zoneMeta(o.l, 'run'), c = o.c;
    const tip = cellTip(m, c, who(r), WIN_LABEL[S.win], r, allowedNote({ caller: 'offense' }, S.side));
    const bx = Math.max(24, Math.min(W - 24, o.x));
    return `<g class="zbox${c && c.small ? ' small' : ''}" tabindex="0" data-tip="${esc(tip)}"><rect x="${bx - 23}" y="40" width="46" height="62" rx="6" style="${divFill(m, c).replace('background', 'fill') || 'fill:var(--panel)'}"/><text x="${bx}" y="54" text-anchor="middle" class="zn">${LANE_SHORT[o.l.lane]}</text><text x="${bx}" y="76" text-anchor="middle" class="zv">${esc(fmt(m, c && c.value))}</text><text x="${bx}" y="92" text-anchor="middle" class="zs">lg ${esc(fmt(m, c && c.league))}</text></g>`;
  }).join('');
  const caption = n ? `${n.n} designed runs with a direction${n.small ? ' · small sample' : ''}` : 'no designed runs';
  return `<figure class="fieldfig"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc('Run direction: ' + lanes.map(o => `${o.l.label} ${fmt(ZONE_M, o.c && o.c.value)}`).join(', '))}">${s}${paths}${boxes}</svg><figcaption class="muted">${esc(caption)} · named for the lineman the run goes at (the gamebook's left end … right end)</figcaption></figure>`;
}

/* ---------- Week by week ---------- */
function weeklyCard(r) {
  const wk = r.weekly;
  if (!wk || !wk.series.length) return '';
  const opp = new Map(wk.games.map(g => [g.week, g]));
  const cards = wk.series.map(se => {
    const pts = se.points.filter(p => p.value != null);
    const last = pts[pts.length - 1];
    return `<div class="wkcard"><div class="wkh"><b>${esc(se.short)}</b><span class="num">${last ? esc(fmt(se, last.value)) : '—'}<small> wk ${last ? last.week : '—'}</small></span></div>
      ${weekSpark(se, opp, r)}
      <div class="wkf muted"><span><i class="lk lg"></i>league ${esc(fmt(se, se.league))}</span><span>${esc(se.label)}</span></div></div>`;
  }).join('');
  return `<div class="card"><div class="card-h"><h2>Week by week</h2><span class="muted">one point per game · the dashed line is the league's season rate · hollow = fewer than ${r.min_n} plays</span></div>
    <div class="card-b wkgrid">${cards}</div></div>`;
}
function weekSpark(se, opp, r) {
  const W = 240, H = 74, m = { l: 8, r: 8, t: 10, b: 18 };
  const pts = se.points;
  const vals = pts.map(p => p.value).filter(v => v != null).concat(se.league != null ? [se.league] : []);
  if (!vals.length) return '<p class="muted">No games yet.</p>';
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 1e-9) { lo -= 0.05; hi += 0.05; }
  const pad = (hi - lo) * 0.15; lo -= pad; hi += pad;
  const n = pts.length;
  const X = i => m.l + (n > 1 ? i * (W - m.l - m.r) / (n - 1) : (W - m.l - m.r) / 2);
  const Y = v => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  let s = '';
  if (se.league != null) s += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(se.league)}" y2="${Y(se.league)}" class="lgline"/>`;
  const seg = pts.map((p, i) => p.value == null ? null : `${X(i).toFixed(1)},${Y(p.value).toFixed(1)}`).filter(Boolean);
  s += `<polyline points="${seg.join(' ')}" fill="none" style="stroke:var(--s1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  s += pts.map((p, i) => {
    if (p.value == null) return '';
    const g = opp.get(p.week);
    const small = p.n < r.min_n;
    const tip = `Week ${p.week}${g ? ` · ${g.home ? 'vs' : 'at'} ${g.opponent}` : ''}\n${se.short}: ${fmt(se, p.value)} (${p.n} ${per(se, p.n)})\nLeague (season): ${fmt(se, se.league)}${small ? `\nSmall sample: under ${r.min_n} ${per(se, 2)}` : ''}`;
    return `<circle cx="${X(i)}" cy="${Y(p.value)}" r="${small ? 3.6 : 4}" class="wpt${small ? ' small' : ''}" tabindex="0" data-tip="${esc(tip)}"/>`;
  }).join('');
  s += pts.map((p, i) => `<text x="${X(i)}" y="${H - 3}" text-anchor="middle" class="wkx">${p.week}</text>`).join('');
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(`${se.label} by week: ` + pts.map(p => `week ${p.week} ${fmt(se, p.value)}`).join(', '))}">${s}</svg></div>`;
}

/* ---------- History, 2023-2025 (research data) ---------- */
function historyCard(r) {
  const title = S.side === 'offense' ? 'Personnel, formations, routes of targets' : 'Coverage shells, man vs zone, packages';
  if (!S.hist) {
    return `<div class="card hist"><div class="card-h"><h2>History, 2023–2025</h2><span class="chip ghost">research data</span><span class="muted">${title}</span>
      <div class="right"><button class="btn sm" id="histBtn" aria-expanded="false">Show history</button></div></div>
      <div class="card-b muted" style="font-size:13px">Participation data (who's on the field, coverage, routes of targets) is published after each season; this shows the newest three seasons from 2023. Opens on request.</div></div>`;
  }
  const h = historyFor(S.team, S.side);
  if (!h) return `<div class="card hist"><div class="card-h"><h2>History, 2023–2025</h2><div class="right"><button class="btn sm" id="histBtn" aria-expanded="true">Hide</button></div></div><div class="card-b">${mockOnly(`The dump has no history for ${esc(S.team)}'s ${S.side}.`)}</div></div>`;
  if (h.status !== 'ok') return `<div class="card hist"><div class="card-h"><h2>History, 2023–2025</h2><div class="right"><button class="btn sm" id="histBtn" aria-expanded="true">Hide</button></div></div>${emptyCard(h, 'H', 'No history tables')}</div>`;
  const groups = h.groups.map(g => {
    const head = `<tr><th>${esc(g.title)}</th>${h.seasons.map(s => `<th>${s}</th>`).join('')}</tr>`;
    const rows = g.rows.map(m => { const dom = rowDomain(m, h.seasons.map(s => m.seasons[String(s)])); return `<tr><th scope="row">${esc(m.short)}</th>${h.seasons.map(s => {
      const c = m.seasons[String(s)];
      return `<td class="hcell${c && c.small ? ' small' : ''}" tabindex="0" data-tip="${esc(cellTip(m, c, who({ team: h.team }), String(s), h, allowedNote(m, S.side)))}"><span class="hv num">${esc(fmt(m, c && c.value))}<small>${c ? `n ${c.n}` : ''}</small></span>${rateBar(m, c, true, dom)}</td>`;
    }).join('')}</tr>`; }).join('');
    return `<section class="hgrp"><div class="tablewrap"><table class="tbl htbl">${'<thead>' + head + '</thead>'}<tbody>${rows}</tbody></table></div>${g.note ? `<p class="gnote">${esc(g.note)}</p>` : ''}</section>`;
  }).join('');
  return `<div class="card hist"><div class="card-h"><h2>History, ${h.seasons[0]}–${h.seasons[h.seasons.length - 1]}</h2><span class="chip ghost">research data</span><span class="muted">${title}</span>
      <div class="right"><button class="btn sm" id="histBtn" aria-expanded="true">Hide</button></div></div>
    <div class="card-b"><div class="notice"><span class="ic">i</span><div>${esc(h.source_note)}</div></div>
      <div class="hgrid">${groups}</div>
      <div class="heatfoot"><span class="divlegend"><span class="lgkey"><i class="b"></i>team</span><span class="lgkey"><i class="l"></i>league</span><span class="muted">· whole seasons, playoffs in · greyed: n under ${h.min_n}</span></span></div></div></div>`;
}

/* ================================================================== the Play calls week tab */
function weekView() {
  if (S.state === 'loading') return loadingCard('the play calls');
  if (S.state === 'not_built' || S.state === 'not_yet') {
    const st = stateAnswer('week');
    const meta = st || { message: null };
    const title = S.state === 'not_built' ? 'No play-calling tables yet' : 'Not this week yet';
    return (st ? '' : mockOnly('Drawn with a stand-in message; the dump has no such answer.')) + emptyCard(meta, 'PC', title, S.state === 'not_built' ? 'Run `uv run nfl playcalling build --season 2026` to build them.' : 'The matchups appear once the week before is played and the tables are rebuilt.');
  }
  const wk = weekFor(S.weekKey);
  if (wk.status !== 'ok') return emptyCard(wk, 'PC', wk.status === 'not_yet' ? `Week ${wk.week}'s matchups aren't ready yet` : 'No play-calling tables yet');
  const hasF = !!wk.forecast;
  const view = hasF ? S.pcView : 'tend';
  const ftn = wk.ftn_waiting && wk.ftn_waiting.length ? `<div class="notice"><span class="ic">!</span><div><b>FTN hasn't charted ${wk.ftn_waiting.length === 1 ? 'one game' : wk.ftn_waiting.length + ' games'} yet:</b> ${esc(wk.ftn_waiting.join(' · '))}. Play-action, screens, RPO, motion, blitz and box rates wait for it.</div></div>` : '';
  const sw = hasF ? `<div class="wintool"><span class="eyebrow">Show</span><span class="seg" role="group" aria-label="View"><button type="button" data-pcv="fc" aria-pressed="${view === 'fc'}">Forecast</button><button type="button" data-pcv="tend" aria-pressed="${view === 'tend'}">Tendencies so far</button></span><span class="muted">${view === 'fc' ? "each team's rate for this game, its 80% range and its rate so far" : "each offense's rates so far next to what the other defense has allowed (no forecast)"}</span></div>` : '';
  const upd = updCard(wk);
  if (view === 'fc') {
    return `${upd}<div class="notice accent"><span class="ic">i</span><div>${esc(wk.note)}</div></div>
      ${backfillNote(wk.forecast)}${hintNotice(wk.forecast_hint)}${ftn}${sw}${forecastWeek(wk)}`;
  }
  return `${upd}${hasF ? '' : `<div class="notice accent"><span class="ic">i</span><div>${esc(wk.note)}</div></div>`}${hintNotice(wk.forecast_hint)}${ftn}${sw}${tendWeek(wk)}`;
}
function tendWeek(wk) {
  const shifts = wk.shifts_kind !== 'forecast' && wk.shifts.length ? `<div class="card"><div class="card-h"><h2>Biggest matchup shifts</h2><span class="muted">where a defense allows or calls something far from the league · in standard deviations of the 32 defenses</span></div>
    <ol class="shifts">${wk.shifts.map(sh => { const w = Math.min(100, Math.abs(sh.shift) / 3 * 100); return `<li><span class="shm" aria-hidden="true"><span class="shb ${sh.shift > 0 ? 'up' : 'dn'}" style="width:${w.toFixed(0)}%"></span></span><span class="sht">${esc(sh.text)}</span><span class="num muted shv">${signed(sh.shift, 1)} SD</span></li>`; }).join('')}</ol></div>` : '';
  const games = wk.games.map(g => gameCard(g, wk)).join('');
  return `${shifts}
    <div class="sec-h"><h2>Every game</h2><p>${esc(wk.window === 'season' ? `${wk.season} season before week ${wk.week}` : `${wk.season - 1} season`)} · each offense against the other defense</p><div class="right"><span class="legend"><span><i style="background:var(--s1)"></i>offense does</span><span><i style="background:var(--s2)"></i>defense allows</span><span><i class="line" style="background:var(--ink-2)"></i>league</span></span></div></div>
    <div class="pcgames">${games}</div>`;
}
function gameCard(g, wk) {
  const mm = g.matchups.map(mu => matchupCol(mu, wk)).join('');
  return `<div class="card pcgame"><div class="card-h"><h2>${tchip(g.away, true)} <span class="muted" style="font-family:var(--font-body);font-size:13px;font-weight:500">at</span> ${tchip(g.home, true)}</h2><span class="muted">${esc(tKick(g.kickoff))}${g.kickoff ? ' ET' : ''}</span>
    <div class="right"><button class="btn sm" data-open-team="${esc(g.away)}">${esc(g.away)} page</button><button class="btn sm" data-open-team="${esc(g.home)}">${esc(g.home)} page</button></div></div>
    <div class="pcmatch">${mm}</div></div>`;
}
function matchupCol(mu, wk) {
  const ms = new Map(wk.metrics.map(m => [m.metric, m]));
  const rows = mu.rows.map(row => {
    const m = ms.get(row.metric); if (!m) return '';
    const defCall = m.caller === 'defense';
    const o = row.offense, d = row.defense;
    const vals = [o && o.value, d && d.value, row.league].filter(v => v != null);
    const solid = o && d && !o.small && !d.small; // the shift is only read on two solid samples
    const tip = [`${m.label}`,
      `${mu.offense} ${defCall ? 'has faced' : 'does'}: ${fmt(m, o && o.value)}${o ? ` (${o.n} ${per(m, o.n)}${o.small ? ', small' : ''})` : ''}`,
      `${mu.defense} ${defCall ? 'calls' : 'allows'}: ${fmt(m, d && d.value)}${d ? ` (${d.n} ${per(m, d.n)}${d.small ? ', small' : ''})` : ''}`,
      `League: ${fmt(m, row.league)}`,
      solid && row.shift != null ? `${mu.defense} sits ${signed(row.shift, 1)} SD from the league's defenses` : null,
      solid && row.same_way != null ? (row.same_way ? 'Both sit on the same side of the league' : 'They pull opposite ways') : null,
      defCall ? null : ALLOWED_NOTE].filter(Boolean).join('\n');
    const big = solid && row.shift != null && Math.abs(row.shift) >= 2; // marked: 2+ SD from the league's defenses, never on a small sample
    return `<tr class="${big ? 'bigshift' : ''}" tabindex="0" data-tip="${esc(tip)}"><th scope="row">${esc(m.short)}${defCall ? '<small>defense\'s call</small>' : ''}</th>
      <td class="r num${o && o.small ? ' smallv' : ''}">${esc(fmt(m, o && o.value))}<small>${o ? 'n ' + o.n : ''}</small></td>
      <td class="r num${d && d.small ? ' smallv' : ''}">${esc(fmt(m, d && d.value))}<small>${d ? 'n ' + d.n : ''}</small></td>
      <td class="r num muted">${esc(fmt(m, row.league))}</td>
      <td class="mviz">${vals.length ? matchViz(m, o, d, row.league) : ''}</td></tr>`;
  }).join('');
  return `<section><h3 class="mh">${tchip(mu.offense)} offense <span class="muted">vs</span> ${tchip(mu.defense)} defense</h3>
    <div class="tablewrap"><table class="tbl mtbl"><thead><tr><th></th><th class="r">${esc(mu.offense)} does</th><th class="r">${esc(mu.defense)} allows</th><th class="r">League</th><th class="mviz"></th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}
function matchViz(m, o, d, L) {
  const vs = [o && o.value, d && d.value, L].filter(v => v != null);
  let lo = Math.min(...vs), hi = Math.max(...vs);
  if (m.unit === 'share') { lo = Math.min(lo, 0); }
  const pad = (hi - lo) * 0.18 || 0.05; lo -= m.unit === 'share' ? 0 : pad; hi += pad;
  const X = v => ((v - lo) / (hi - lo) * 100).toFixed(1);
  return `<span class="mv"><span class="mt"></span>${L != null ? `<span class="ml" style="left:${X(L)}%"></span>` : ''}${d && d.value != null ? `<span class="md d${d.small ? ' small' : ''}" style="left:${X(d.value)}%"></span>` : ''}${o && o.value != null ? `<span class="md o${o.small ? ' small' : ''}" style="left:${X(o.value)}%"></span>` : ''}</span>`;
}

/* ================================================================== PC02: the forecast
   The shapes: PlaycallForecastValue (one team's rate for one game, + its grade), PlaycallForecastMeta
   (wk.forecast / next_game.forecast: the targets, the note, the backfill), PlaycallWeekGrade
   (wk.graded), PlaycallForecastRecord (a team page's forecast_record). "Don't oversell": every
   forecast shows its 80% range and the team's own rate so far (the baseline it has to beat). */
const SCRIPT_TARGETS = ['proe', 'dropback_rate'];
/** A change in a target's unit: points for shares and PROE ("+2.7 pts"), rushers as is ("−0.23"). */
function fmtChange(t, d) {
  if (d == null) return '—';
  if (t.unit === 'mean') return signed(d, t.digits);
  return signed(d * 100, 1) + ' pts';
}
/** An error (MAE) in a target's unit: points for shares and PROE, rushers for rushers. */
function fmtErr(t, e) {
  if (e == null) return '—';
  if (t.unit === 'mean') return e.toFixed(t.digits) + (t.target === 'rushers_avg' ? ' rushers' : '');
  return (e * 100).toFixed(1) + ' pts';
}
const pctTxt = x => x == null ? '—' : Math.round(x * 100) + '%';
/** What the baseline is called: the season so far, or last season's rate before a team's first game. */
const soFarName = v => v.games_so_far === 0 ? 'last season' : 'so far';
const soFarLabel = v => v.games_so_far === 0 ? 'Last season' : 'Season so far';
const STATUS = {
  waiting_ftn: { tone: 'warn', text: 'FTN not in yet', short: 'waiting for FTN', tip: 'FTN charts a game about two days after it is played: graded once it lands.' },
  waiting_build: { tone: 'flat', text: 'Waiting for the tables', short: 'played, waiting for the tables', tip: 'Played; graded after the next `nfl playcalling build` and `nfl playcalling grade`.' },
  not_played: { tone: 'flat', text: 'Not played yet', short: 'not played yet', tip: 'Graded after the game.' },
  no_plays: { tone: 'ghost', text: 'No plays to grade', short: 'with no plays to grade', tip: 'The game had none of the plays this rate counts.' },
};
/** The second piece's name: the opponent, or for run direction the home team's stats crew (it charts run location). */
const ctxName = (t, v) => t.context === 'venue' ? `the home team's stats crew (${v.context_team || 'home'})` : `the opponent${v.context_team ? ` (${v.context_team})` : ''}`;
const ctxShort = (t, v) => t.context === 'venue' ? `crew ${v.context_team || ''}`.trim() : 'opp';
const targetMap = meta => new Map(((meta && meta.targets) || []).map(t => [t.target, t]));

/** One scale per target over a set of forecasts, so a target's rows compare across games. */
function fDomains(values) {
  const by = {};
  values.forEach(v => { (by[v.target] = by[v.target] || []).push(...[v.lo, v.hi, v.forecast, v.season_to_date, v.league, v.actual].filter(x => x != null)); });
  const out = {};
  Object.entries(by).forEach(([k, xs]) => { const lo = Math.min(...xs), hi = Math.max(...xs); const pad = (hi - lo) * 0.05 || 0.01; out[k] = { lo: lo - pad, hi: hi + pad }; });
  return out;
}
/** The tooltip: the forecast, its range, the baseline, the league, the pieces, the grade. */
function fTip(t, v) {
  const lines = [`${t.label} · ${v.team} vs ${v.opponent}`];
  lines.push(`Forecast: ${fmt(t, v.forecast)} (80% range ${fmt(t, v.lo)} to ${fmt(t, v.hi)})`);
  lines.push(`${soFarLabel(v)}: ${fmt(t, v.season_to_date)}${v.games_so_far ? ` (${v.n_so_far} ${per(t, v.n_so_far)}, ${v.games_so_far} game${v.games_so_far === 1 ? '' : 's'})` : ''}${v.small ? ' · small sample' : ''}`);
  lines.push(`Change from ${soFarName(v)}: ${fmtChange(t, v.shift)}`);
  if (v.league != null) lines.push(`League: ${fmt(t, v.league)}`);
  lines.push(`Pieces: ${v.team}'s rate shrunk toward last season and the league ${fmt(t, v.team_term)}, ${ctxName(t, v)} ${fmtChange(t, v.opponent_effect)}${SCRIPT_TARGETS.includes(v.target) ? `, the game script ${fmtChange(t, v.script_effect)}` : ''}`);
  if (v.status === 'graded') {
    lines.push(`Actual: ${fmt(t, v.actual)}${v.n != null ? ` (${v.n} ${per(t, v.n)})` : ''} · ${v.in_range ? 'inside' : 'outside'} the range`);
    lines.push(v.beat_season_to_date ? `The forecast was closer than ${soFarName(v)}` : `${soFarLabel(v)} was closer (or as close)`);
  } else if (v.status && STATUS[v.status]) lines.push(`${STATUS[v.status].text}: ${STATUS[v.status].tip.replace(/`/g, '')}`);
  if (t.help) lines.push(t.help);
  return lines.join('\n');
}
/** The strip: the 80% range as a band, the forecast as a bar, the season so far as a ring, the
 *  league as a tick, and after the game the actual as a dot. The caller's colour: --s1 offense, --s2 defense. */
function fStrip(t, v, dom, tip) {
  if (v.forecast == null || !dom) return '';
  const X = x => Math.max(0, Math.min(100, (x - dom.lo) / (dom.hi - dom.lo) * 100)).toFixed(1);
  const side = v.caller === 'defense' ? 'd' : 'o';
  const band = v.lo != null && v.hi != null ? `<span class="fs-band ${side}" style="left:${X(v.lo)}%;width:${Math.max(0.5, X(v.hi) - X(v.lo)).toFixed(1)}%"></span>` : '';
  return `<span class="fs" data-tip="${esc(tip)}"><span class="fs-t"></span>${band}${v.league != null ? `<span class="fs-lg" style="left:${X(v.league)}%"></span>` : ''}${v.season_to_date != null ? `<span class="fs-sd${v.small ? ' small' : ''}" style="left:${X(v.season_to_date)}%"></span>` : ''}<span class="fs-f ${side}" style="left:${X(v.forecast)}%"></span>${v.status === 'graded' && v.actual != null ? `<span class="fs-a${v.in_range ? '' : ' out'}" style="left:${X(v.actual)}%"></span>` : ''}</span>`;
}
const fLegend = () => `<span class="legend fleg"><span><i class="lk band"></i>80% range</span><span><i class="lk fbar"></i>forecast</span><span><i class="lk ring"></i>season so far</span><span><i class="lk tick"></i>league</span><span><i class="lk adot"></i>actual</span></span>`;
/** After the game: the actual and two marks; before: the status chip. */
function fResult(t, v) {
  if (v.status === 'graded') {
    return `<span class="num fact">${esc(fmt(t, v.actual))}</span><span class="fmarks"><span class="fmk ${v.in_range ? 'ok' : 'miss'}">${v.in_range ? '✓ in range' : '✗ outside'}</span><span class="fmk ${v.beat_season_to_date ? 'ok' : 'miss'}">${v.beat_season_to_date ? '✓ beat ' : '✗ '}${v.beat_season_to_date ? soFarName(v) : soFarName(v) + ' closer'}</span></span>`;
  }
  const st = v.status && STATUS[v.status];
  return st ? `<span class="chip ${st.tone} fchip" tabindex="0" data-tip="${esc(st.text + '\n' + st.tip.replace(/`/g, ''))}">${esc(st.text)}</span>` : '';
}
/** One forecast row (a table row): the name (the focusable tooltip), the forecast and its range,
 *  the strip, the season so far and the change, the result. `pieces` adds the breakdown line. */
function fRow(t, v, dom, pieces) {
  const tip = fTip(t, v);
  const pc = pieces ? `<small class="fpieces">${esc(v.team)} ${esc(fmt(t, v.team_term))} · ${esc(ctxShort(t, v))} ${esc(fmtChange(t, v.opponent_effect))}${SCRIPT_TARGETS.includes(v.target) ? ` · script ${esc(fmtChange(t, v.script_effect))}` : ''}</small>` : '';
  return `<tr class="${v.status === 'graded' ? 'graded' : ''}"><th scope="row"><span class="fname" tabindex="0" data-tip="${esc(tip)}">${esc(t.short)}<small>${esc(t.per === 'play' ? 'per play' : 'per ' + t.per)}</small></span></th>
    <td class="r ffc"><b class="num">${esc(fmt(t, v.forecast))}</b><small class="num">${esc(fmt(t, v.lo))} to ${esc(fmt(t, v.hi))}</small>${pc}</td>
    <td class="fviz">${fStrip(t, v, dom, tip)}</td>
    <td class="r fsd${v.small ? ' smallv' : ''}"><span class="num">${esc(fmt(t, v.season_to_date))}</span><small class="num">${esc(fmtChange(t, v.shift))}</small></td>
    <td class="fres">${fResult(t, v)}</td></tr>`;
}
function fTable(rows, tm, doms, opts) {
  const o = opts || {};
  const body = rows.map(g => {
    const head = g.title ? `<tr class="fgrp"><th colspan="5" scope="colgroup">${g.title}</th></tr>` : '';
    return head + g.values.map(v => { const t = tm.get(v.target); return t ? fRow(t, v, doms[v.target], o.pieces) : ''; }).join('');
  }).join('');
  const soFar = o.lastSeason ? 'Last season' : 'So far';
  return `<div class="tablewrap"><table class="tbl ftbl"><thead><tr><th></th><th class="r">Forecast<small>80% range</small></th><th class="fviz"></th><th class="r">${soFar}<small>change</small></th><th>${o.graded ? 'Actual' : 'After the game'}</th></tr></thead><tbody>${body}</tbody></table></div>`;
}
/** The made-after-kickoff note (a backfill from the as-of tables: the forecast still saw only earlier games). */
function backfillNote(meta) {
  if (!meta || !meta.backfill) return '';
  const n = meta.after_kickoff.length;
  return `<div class="notice"><span class="ic">!</span><div><b>Made after kickoff (a backfill).</b> This forecast was made on ${esc(tDay(meta.made_at))}, after ${n === 1 ? 'one game' : n + ' games'} had kicked off, from the tables as of week ${meta.as_of_week}: its tendencies saw only the games before this week, but its lines may be the closing ones (a Tuesday forecast uses Tuesday's). <details class="fdet"><summary>The ${n === 1 ? 'game' : n + ' games'}</summary>${esc(meta.after_kickoff.join(' · '))}</details></div></div>`;
}
/** A hint ("No forecast for week 6 yet: `cmd` (after ...).") as its text, command and tail. */
function splitHint(hint) {
  const parts = hint.split('`');
  const text = (parts[0] || '').replace(/:\s*$/, '.').trim();
  const cmd = parts.length >= 3 ? parts[1] : null;
  const tail = parts.length >= 3 ? parts.slice(2).join('`').replace(/`/g, '').replace(/^\s*\(|\)?\.?\s*$/g, '').trim() : '';
  return { text, cmd, tail: tail || null };
}
function hintNotice(hint) {
  if (!hint) return '';
  const h = splitHint(hint);
  return `<div class="notice"><span class="ic">i</span><div>${esc(h.text)}${h.cmd ? `<div class="cmd" style="margin-top:6px">${esc(h.cmd)}</div>` : ''}${h.tail ? `<span class="muted">${esc(h.tail)}</span>` : ''}</div></div>`;
}
/** The grade's numbers: closer in N of M, the 80% hit rate, the MAE in points (shares and PROE). */
function gradeStats(s, waiting) {
  return `<div class="gstats">
    <div class="gst"><span class="eyebrow">Closer than the season so far</span><b class="num">${s.beat}<small> of ${s.graded}</small></b><span class="muted">${s.graded ? pctTxt(s.beat / s.graded) + ' of the forecasts' : '—'}</span></div>
    <div class="gst"><span class="eyebrow">Inside the 80% range</span><b class="num">${pctTxt(s.in_range)}</b><span class="muted">a fair range lands near 80%</span></div>
    <div class="gst"><span class="eyebrow">Average miss · points</span><b class="num">${s.mae != null ? (s.mae * 100).toFixed(1) : '—'}<small> vs ${s.mae_season_to_date != null ? (s.mae_season_to_date * 100).toFixed(1) : '—'}</small></b><span class="muted">forecast vs season so far · shares and PROE</span></div>
    ${waiting != null ? `<div class="gst"><span class="eyebrow">Still waiting</span><b class="num">${waiting}</b><span class="muted">not graded yet</span></div>` : ''}
  </div>`;
}
/** Per target: graded, the two MAEs as a pair of bars, closer in N, in range. */
function gradeTable(rows, tm) {
  const body = rows.filter(r => r.graded).map(r => {
    const t = tm.get(r.target) || r;
    const top = Math.max(r.mae || 0, r.mae_season_to_date || 0) || 1;
    // bold the smaller miss, neither when they round the same
    const a = fmtErr(t, r.mae), b = fmtErr(t, r.mae_season_to_date);
    const better = r.mae != null && r.mae_season_to_date != null && a !== b && r.mae < r.mae_season_to_date;
    const worse = r.mae != null && r.mae_season_to_date != null && a !== b && r.mae > r.mae_season_to_date;
    return `<tr><th scope="row">${esc(t.short)}<small>${esc(t.caller === 'defense' ? "defense's call" : "offense's call")}</small></th><td class="r num gn">${r.graded}</td>
      <td class="r num${better ? ' win' : ''}">${esc(a)}</td><td class="r num${worse ? ' win' : ''}">${esc(b)}</td>
      <td class="gbars" aria-hidden="true"><span class="gb f ${t.caller === 'defense' ? 'd' : 'o'}" style="width:${((r.mae || 0) / top * 100).toFixed(0)}%"></span><span class="gb b" style="width:${((r.mae_season_to_date || 0) / top * 100).toFixed(0)}%"></span></td>
      <td class="r num">${r.beat} of ${r.graded}</td><td class="r num">${pctTxt(r.in_range)}</td></tr>`;
  }).join('');
  return `<div class="tablewrap"><table class="tbl gtbl"><thead><tr><th>Rate</th><th class="r gn">Graded</th><th class="r">Forecast miss</th><th class="r">Season-so-far miss</th><th class="gbars"><span class="legend"><span><i style="background:var(--s1)"></i>forecast</span><span><i style="background:var(--ink-3)"></i>so far</span></span></th><th class="r">Closer</th><th class="r">In range</th></tr></thead><tbody>${body}</tbody></table></div>`;
}
function statusCounts(values) {
  const c = {};
  values.forEach(v => { if (v.status && v.status !== 'graded') c[v.status] = (c[v.status] || 0) + 1; });
  return Object.entries(c).map(([k, n]) => `${n} ${STATUS[k] ? STATUS[k].short : k}`).join(' · ');
}
function weekGradeCard(wk, values) {
  const g = wk.graded;
  if (!g) return '';
  const tm = targetMap(wk.forecast);
  if (!g.graded) {
    return `<div class="notice"><span class="ic">i</span><div><b>Waiting for the games:</b> none of the week's ${g.rows} forecasts is graded yet (${esc(statusCounts(values))}). The grade appears here after <span class="mono">nfl playcalling grade</span>.</div></div>`;
  }
  return `<div class="card"><div class="card-h"><h2>How the forecast did · week ${wk.week}</h2><span class="muted">${g.graded} graded${g.waiting ? ` · ${g.waiting} waiting (${esc(statusCounts(values))})` : ''} · the bar to beat: each team's own rate so far</span></div>
    <div class="card-b">${gradeStats(g.overall)}${gradeTable(g.targets, tm)}
    <p class="gnote">A miss is |forecast − actual| in the rate's unit (points for shares and PROE, rushers for pass rushers). One week is a few dozen games: noisy, so read it with the season's record on each team page.</p></div></div>`;
}
function movesCard(wk) {
  if (!wk.shifts.length) return '';
  const tm = targetMap(wk.forecast);
  return `<div class="card"><div class="card-h"><h2>Biggest expected moves</h2><span class="muted">the forecast against the team's own rate so far · the bar: the move over the 80% range's half-width (1 = the range's edge)</span></div>
    <ol class="shifts fmoves">${wk.shifts.map(sh => { const t = tm.get(sh.metric) || { unit: 'share', digits: 1 }; const w = Math.min(100, sh.shift / 1.5 * 100); return `<li><span class="shm" aria-hidden="true"><span class="shb" style="width:${w.toFixed(0)}%"></span></span><span class="sht">${esc(sh.text)}</span><span class="num muted shv">${esc(fmtChange(t, sh.change))}<small>${sh.shift.toFixed(2)}× half-range</small></span></li>`; }).join('')}</ol></div>`;
}
function fGameCard(g, wk, tm, doms) {
  const graded = !!(wk.graded && wk.graded.graded);
  const lastSeason = wk.window === 'last_season';
  const mm = g.matchups.map(mu => {
    const off = mu.forecast.filter(v => v.caller === 'offense'), dfn = mu.forecast.filter(v => v.caller === 'defense');
    if (!mu.forecast.length) return `<section><h3 class="mh">${tchip(mu.offense)} offense <span class="muted">vs</span> ${tchip(mu.defense)} defense</h3><p class="muted">No forecast for this matchup.</p></section>`;
    return `<section aria-label="${esc(`${mu.offense} offense against ${mu.defense} defense`)}"><h3 class="mh">${tchip(mu.offense)} offense <span class="muted">vs</span> ${tchip(mu.defense)} defense</h3>
      ${fTable([{ title: `${esc(mu.offense)}'s calls`, values: off }, { title: `${esc(mu.defense)}'s calls`, values: dfn }], tm, doms, { graded, lastSeason })}</section>`;
  }).join('');
  return `<div class="card pcgame"><div class="card-h"><h2>${tchip(g.away, true)} <span class="muted" style="font-family:var(--font-body);font-size:13px;font-weight:500">at</span> ${tchip(g.home, true)}</h2><span class="muted">${esc(tKick(g.kickoff))}${g.kickoff ? ' ET' : ''}</span>
    <div class="right"><button class="btn sm" data-open-team="${esc(g.away)}">${esc(g.away)} page</button><button class="btn sm" data-open-team="${esc(g.home)}">${esc(g.home)} page</button></div></div>
    <div class="pcmatch fc">${mm}</div></div>`;
}
function forecastWeek(wk) {
  const tm = targetMap(wk.forecast);
  const values = wk.games.flatMap(g => g.matchups.flatMap(mu => mu.forecast));
  const doms = fDomains(values);
  return `${weekGradeCard(wk, values)}
    ${movesCard(wk)}
    <div class="sec-h"><h2>Every game</h2><p>each team's calls for this game · the offense's, then the defense's · hover or focus a rate for its pieces</p><div class="right">${fLegend()}</div></div>
    <div class="pcgames">${wk.games.map(g => fGameCard(g, wk, tm, doms)).join('')}</div>`;
}
/* ---------- a team page: the Next game card and the season card ---------- */
function nextGameCard(r) {
  const ng = r.next_game;
  if (!ng) return '';
  const where = `${ng.home ? 'vs' : 'at'} ${ng.opponent}`;
  const link = `<button class="btn sm" data-go-week>Week ${ng.week} · Play calls →</button>`;
  const head = `<div class="card-h"><h2>Next game <span class="muted ngwhere">${ng.home ? 'vs' : 'at'}</span> ${tchip(ng.opponent, true)}</h2><span class="muted">week ${ng.week}${ng.kickoff ? ' · ' + esc(tKick(ng.kickoff)) + ' ET' : ''}</span><div class="right">${link}</div></div>`;
  if (!ng.forecast) {
    const h = ng.forecast_hint ? splitHint(ng.forecast_hint) : null;
    const cmd = h && h.cmd;
    return `<div class="card nextg">${head}<div class="card-b"><p class="muted" style="margin:0">${esc(h ? h.text : 'No forecast yet.')} The forecast for ${esc(r.team)} ${esc(where)} appears here once it's made.</p>${cmd ? `<div class="cmd" style="margin-top:8px">${esc(cmd)}</div>` : ''}</div></div>`;
  }
  const f = ng.forecast;
  const tm = targetMap(f);
  const all = f.own.concat(f.opponent);
  const doms = fDomains(all);
  const other = r.side === 'offense' ? 'defense' : 'offense';
  const graded = all.some(v => v.status === 'graded');
  const lastSeason = all.some(v => v.games_so_far === 0);
  return `<div class="card nextg">${head}
    <div class="card-b"><div class="fnote">${esc(f.note)}</div>
      <div class="ngrid">
        <section><h3 class="dh">${esc(r.team)}'s ${r.side} calls</h3>${fTable([{ values: f.own }], tm, doms, { pieces: true, graded, lastSeason })}</section>
        <section><h3 class="dh">${esc(ng.opponent)}'s ${other} calls against it</h3>${fTable([{ values: f.opponent }], tm, doms, { pieces: true, graded, lastSeason })}</section>
      </div>
      <div class="heatfoot">${fLegend()}${f.backfill ? `<span class="chip warn" tabindex="0" data-tip="${esc('Made after kickoff (a backfill from the as-of tables: only earlier games seen)\n' + f.after_kickoff.join('\n'))}"><span class="ic">!</span>made after kickoff</span>` : ''}</div></div></div>`;
}
function recordCard(r) {
  const fr = r.forecast_record;
  if (!fr) return '';
  const tm = new Map(fr.targets.map(t => [t.target, t]));
  const wks = fr.weeks.length ? (fr.weeks.length > 1 ? `weeks ${fr.weeks[0]}–${fr.weeks[fr.weeks.length - 1]}` : `week ${fr.weeks[0]}`) : '';
  return `<div class="card"><div class="card-h"><h2>How the forecast has done for ${esc(r.team)}</h2><span class="muted">its ${r.side} calls · ${esc(wks)} · against its own rate so far</span></div>
    <div class="card-b">${gradeStats(fr.overall)}${gradeTable(fr.targets, tm)}
    <p class="gnote">A few games per rate: one team's record is noisy. ${fr.targets.some(t => t.backtest) ? 'The backtest (every team, 2019–2025) is the fairer test of the model.' : ''}</p></div></div>`;
}

/* ================================================================== D132: "Update play calling"
   The current week's Play calls tab runs every post-run play-calling step from the app
   (`nfl playcalling update --auto --expect-week N`), like the Pipeline tab's Run button. Ready:
   pre-flight's `playcalling` gate (P.preflight, the real answer at the dump's pinned clock): the
   five steps, the due ones "will run" with why, the others muted with why not. Running / finished:
   the run's events (run_start, step_start / step_end per step, log lines, run_end). The running,
   finished, failed, nothing-due, not-allowed and another-run states are mockup-only (synthetic,
   built from the real steps), picked in the controls strip. */
const UPD_PICKS = [['real', 'Ready (pre-flight)'], ['blocked', 'Not allowed'], ['none', 'Nothing due'], ['other', 'Another run'], ['running', 'Running'], ['done', 'Finished'], ['failed', 'Failed']];
const UPD_ICON = { will: '●', pending: '○', running: '◐', ok: '✓', failed: '✗', skipped: '–', off: '–' };
function updPreflight() {
  const pf = P.preflight;
  if (!pf || !pf.playcalling) return null;
  const g = pf.playcalling;
  if (S.upd === 'blocked') return Object.assign({}, pf, { playcalling: Object.assign({}, g, { allowed: false, reason: `Needs this week's Tuesday run first (Run week ${pf.week}).` }) });
  if (S.upd === 'none') {
    const why = { ftn: 'every game of last week has FTN', build: 'the tables are current', review: `week ${pf.week - 1} is already reviewed`, grade: `week ${pf.week - 1} is fully graded`, forecast: `week ${pf.week}'s forecast is current` };
    return Object.assign({}, pf, { playcalling: Object.assign({}, g, { allowed: false, reason: 'Everything is up to date.', steps: g.steps.map(s => Object.assign({}, s, { due: false, reason: why[s.step] || s.reason })) }) });
  }
  if (S.upd === 'other') return Object.assign({}, pf, { playcalling: Object.assign({}, g, { allowed: false, reason: 'A run is in progress.' }) });
  return pf;
}
/** Synthetic events of an update (mockup-only): the due steps, in order, with their details. */
function updEvents(pf, phase) {
  const due = pf.playcalling.steps.filter(s => s.due);
  const t0 = Date.parse('2026-10-10T16:01:00Z');
  let seq = 0;
  const ev = (o, s) => Object.assign({ seq: ++seq, t: new Date(t0 + s * 1000).toISOString(), run_id: '20261010T160100Z-pc01' }, o);
  const DETAIL = { ftn: 'FTN: ATL at NO charted (131 plays)', build: 'tables as of week 5 · 2026 · 4 files', review: 'week 4 reviewed: 46 fourth downs', grade: 'week 4: 352 of 352 graded', forecast: 'week 5: 330 forecasts' };
  const LOG = { ftn: ['nflverse ftn_charting: 1 new snapshot', 'curate: ftn_charting 5,214 rows'], build: ['playcalling build 2026: as of weeks 1-5', 'team_tendencies.parquet written'], review: ['decision review week 4'], grade: ['grade week 4: 12 rows were waiting for FTN', 'scoreboard: 352 rows graded'], forecast: ['forecast week 5: 15 games'] };
  const out = [ev({ type: 'run_start', kind: 'playcalling_update', command: pf.playcalling.command, season: pf.season, week: pf.week, steps: due.map(s => s.step), plan: pf.playcalling.steps.map(s => `${s.step}: ${s.reason}`), from_step: null, earlier: {}, launched_by: 'rishi', via: 'control-room' }, 0)];
  let t = 2;
  const failAt = phase === 'failed' ? (due.find(s => s.step === 'grade') || due[due.length - 1]).step : null;
  let broken = null;
  for (const s of due) {
    if (broken && ['build', 'grade', 'forecast'].includes(s.step)) {
      out.push(ev({ type: 'step_start', step: s.step }, t));
      out.push(ev({ type: 'step_end', step: s.step, status: 'skipped', seconds: 0, detail: `skipped: the ${broken} step failed` }, t));
      t += 1;
      continue;
    }
    if (phase === 'running' && s.step === (due[1] || due[0]).step) { out.push(ev({ type: 'step_start', step: s.step }, t)); out.push(ev({ type: 'log', step: s.step, text: LOG[s.step][0], cls: 'dim' }, t + 3)); break; }
    out.push(ev({ type: 'step_start', step: s.step }, t));
    LOG[s.step].forEach((l, i) => out.push(ev({ type: 'log', step: s.step, text: l, cls: i ? 'ok' : 'dim' }, t + 2 + i)));
    const failed = s.step === failAt;
    out.push(ev({ type: 'step_end', step: s.step, status: failed ? 'failed' : 'ok', seconds: 14 + t, detail: failed ? 'scoreboard.parquet is open in another program (PermissionError)' : DETAIL[s.step] }, t + 14));
    if (failed) {
      out.push(ev({ type: 'log', step: s.step, text: `${s.step} failed: scoreboard.parquet is open in another program`, cls: 'err' }, t + 15));
      if (['ftn', 'build'].includes(s.step)) broken = s.step;
    }
    t += 20;
  }
  if (phase === 'done' || phase === 'failed') {
    const ok = out.filter(e => e.type === 'step_end' && e.status === 'ok').map(e => e.step);
    out.push(ev({ type: 'run_end', status: phase === 'failed' ? 'failed' : 'ok', exit_code: phase === 'failed' ? 1 : 0, message: `done: ${ok.join(', ') || 'nothing'}${failAt ? `; failed: ${failAt}` : ''}`, published: false, failed_step: failAt, material: null }, t + 20));
  }
  return out;
}
/** The run's events folded onto the plan's steps: status and detail per step, the log, the end. */
function updFold(steps, events) {
  const st = Object.fromEntries(steps.map(s => [s.step, { status: s.due ? 'will' : 'off', detail: null }]));
  const start = events.find(e => e.type === 'run_start');
  if (start) for (const s of steps) st[s.step].status = start.steps.includes(s.step) ? 'pending' : 'off';
  for (const e of events) {
    if (e.type === 'step_start' && st[e.step]) st[e.step].status = 'running';
    if (e.type === 'step_end' && st[e.step]) { st[e.step].status = e.status === 'degraded' ? 'ok' : e.status; st[e.step].detail = e.detail; }
  }
  return { st, log: events.filter(e => e.type === 'log'), end: events.find(e => e.type === 'run_end') || null };
}
function updCard(wk) {
  const pf = updPreflight();
  if (!pf || pf.season !== wk.season || pf.week !== wk.week) return '';
  const g = pf.playcalling;
  const phase = ['running', 'done', 'failed'].includes(S.upd) ? S.upd : null;
  const events = phase ? updEvents(P.preflight, phase) : [];
  const f = updFold(phase ? P.preflight.playcalling.steps : g.steps, events);
  const steps = phase ? P.preflight.playcalling.steps : g.steps;
  const dueN = g.steps.filter(s => s.due).length;
  const failedStep = f.end && f.end.failed_step;
  let right, top = '';
  if (phase === 'running') right = '<span class="chip run"><span class="ic" style="animation:pulse 1.4s infinite">●</span>Running</span>';
  else if (S.upd === 'other') right = `<button class="btn sm" disabled aria-disabled="true">Update play calling</button><span class="reason">Another run is in progress (Run week ${pf.week})</span>`;
  else {
    // after a run the page re-reads pre-flight: done -> nothing due; failed -> the failed step is due again
    const after = phase === 'done' ? { allowed: false, reason: 'Everything is up to date.', n: 0 } : phase === 'failed' ? { allowed: true, reason: null, n: 1 } : { allowed: g.allowed, reason: g.reason, n: dueN };
    right = `<button class="btn sm primary" id="updBtn" ${after.allowed ? '' : 'disabled aria-disabled="true"'}>Update play calling</button>${after.allowed ? `<span class="reason">${after.n} step${after.n === 1 ? '' : 's'} to run</span>` : `<span class="reason">${esc(after.reason || '')}</span>`}`;
  }
  if (phase === 'done') top = `<div class="notice ok"><span class="ic">✓</span><div><b>Play calling updated.</b> ${esc(f.end.message)}. The forecasts and grades below are refreshed. <button class="btn sm" data-upd-hide>Hide</button></div></div>`;
  if (phase === 'failed') top = `<div class="notice err"><span class="ic">!</span><div><b>The play-calling update failed at ${esc(failedStep)}.</b> ${esc(f.end.message)}. Fix it and press Update play calling again: it runs only what's still due. <button class="btn sm" data-upd-hide>Hide</button></div></div>`;
  const rows = steps.map(s => {
    const x = f.st[s.step];
    const word = { will: 'will run', pending: 'waiting', running: 'running', ok: 'done', failed: 'failed', skipped: 'skipped', off: 'not due' }[x.status];
    return `<li class="ust ${x.status}"><span class="ui" aria-hidden="true">${UPD_ICON[x.status]}</span><span class="ul"><b>${esc(s.label)}</b><small>${esc(x.detail || s.reason)}</small></span><span class="uw">${word}</span></li>`;
  }).join('');
  const log = phase ? `<div class="ulog mono" aria-label="The update's last lines">${f.log.slice(-5).map(l => `<div class="${esc(l.cls || '')}"><span class="muted">${esc(eventClockET(l.t))}</span> ${esc(l.text)}</div>`).join('')}</div>` : '';
  const sub = phase === 'running' ? "its steps and last lines follow the run · closing this tab doesn't stop it" : "the steps after Tuesday's run: Monday night's FTN, the tables, last week's review and grade, this week's forecast";
  return `<div class="card pcupd${phase === 'running' ? ' live' : ''}"><div class="card-h"><h2>Update play calling</h2><span class="muted">${esc(sub)}</span><div class="right uright">${right}</div></div>
    <div class="card-b">${top}<ol class="usteps">${rows}</ol>${log}
    ${!phase && g.command ? `<div class="ufoot muted">Runs <span class="mono">${esc(g.command)}</span> · holds the weekly lock while it runs · only the steps marked "will run"</div>` : ''}</div></div>`;
}
const ET_CLOCK = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'America/New_York' });
const eventClockET = t => { const d = new Date(t); return isNaN(d) ? '' : ET_CLOCK.format(d); };
function updConfirm() {
  const pf = updPreflight(); if (!pf) return;
  const g = pf.playcalling;
  const due = g.steps.filter(s => s.due);
  const scrim = document.createElement('div');
  scrim.className = 'scrim'; scrim.id = 'updScrim';
  scrim.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="updT"><header><span class="eyebrow">Confirm the update</span><h2 id="updT">Update week ${pf.week}'s play calling?</h2></header>
    <div class="mb"><span>Runs the steps that are due, in order. A failed FTN refresh or build skips the steps that need it; any other failure lets the rest run:</span>
      <ol class="usteps compact">${due.map(s => `<li class="ust will"><span class="ui">●</span><span class="ul"><b>${esc(s.label)}</b><small>${esc(s.reason)}</small></span></li>`).join('')}</ol>
      <dl class="kv"><dt>Week</dt><dd><b>${pf.week}</b> · from the calendar</dd><dt>Skipped</dt><dd>${esc(g.steps.filter(s => !s.due).map(s => s.label).join(' · ') || 'nothing')}</dd></dl>
      ${g.command ? `<div class="cmd">${esc(g.command)}</div>` : ''}
      <span>It holds the weekly lock while it runs: Run week ${pf.week} and the injury update wait for it. Its log stays on this tab, not the Pipeline tab.</span></div>
    <footer><button class="btn" data-upd-cancel>Cancel</button><button class="btn primary" data-upd-go>Update play calling</button></footer></div>`;
  document.body.appendChild(scrim);
  scrim.addEventListener('click', e => {
    if (e.target === scrim || e.target.closest('[data-upd-cancel]')) { scrim.remove(); return; }
    if (e.target.closest('[data-upd-go]')) { scrim.remove(); S.upd = 'running'; syncMockbar(); render(); }
  });
  scrim.querySelector('[data-upd-go]').focus();
}

/* ================================================================== PC03: the play browser and the diagram player
   The shapes (web/src/api/types.ts, PC03 block): PlaycallPlaysResponse (a week's list), PlayDiagram
   (one play's geometry: x yards from the offense's own goal line, y from its LEFT sideline; keys
   [t, x, y], the ball [t, x, y, h]; interpolate linearly, hold the last key), RealClipResponse.
   payloads: plays[S][TEAM][side][week | 'newest'], plays_filtered, diagrams['<game>/<play>'],
   states.{not_built.plays, no_week, no_play}. The mockup filters a dumped week in the page; the app
   asks the server (the same rules: playbrowser._matches_type / _matches_result). */
const LIFT = 0.45; // how far a ball in the air is drawn above its shadow, per yard of height
const MIN_SPAN = 50; // the narrowest x range drawn (a 32-yard view would be a tall sliver)
const PB_TYPE = { run: 'Run', pass: 'Pass', sack: 'Sack', scramble: 'Scramble', aborted: 'Aborted', dropback: 'Dropback' };
const PB_TAG = { play_action: 'PA', screen: 'Screen', rpo: 'RPO', deep_shot: 'Deep', motion: 'Motion', no_huddle: 'No huddle', trick_play: 'Trick', fake_kick: 'Fake kick', direct_snap: 'Direct snap' };
const PB_TAG_TIP = { play_action: 'Play-action (FTN)', screen: 'Screen (FTN)', rpo: 'Run-pass option (FTN)', deep_shot: 'Deep shot: 20+ air yards', motion: 'Motion before the snap (FTN)', no_huddle: 'No huddle', trick_play: 'Trick play (FTN)', fake_kick: 'Fake punt or field goal', direct_snap: 'Direct snap' };
// "See a real one": the concepts (playcalling/diagrams.py CONCEPTS), for the mockup's stand-in only
const CONCEPTS = {
  screen: ['Screen pass', 'a screen'], rpo: ['Run-pass option (RPO)', 'an RPO'], play_action_deep: ['Play-action deep shot', 'a play-action throw 20+ air yards'],
  inside_zone: ['Inside zone run', 'a run inside the tackles'], outside_zone: ['Outside zone run', 'a run outside the tackles'],
  cover_1: ['Cover 1', 'a dropback against Cover 1'], cover_3: ['Cover 3', 'a dropback against Cover 3'], '2_man': ['2-man', 'a dropback against 2-man'],
};
S.pb = { open: false, week: null, down: null, type: null, result: null, sel: null, state: 'ok', lib: 'off', real: null, motion: 'system' };
const PS = { key: null, t: 0, playing: false, last: 0, raf: 0 }; // the player's clock (never re-renders the page)

/* ---------- the geometry helpers (the React port's lib/playdiagram.ts) ---------- */
function lerpKeys(keys, t) { // [t, ...values]: linear between keys, the first before, the last after
  if (!keys.length) return null;
  if (t <= keys[0][0]) return keys[0].slice(1);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t < b[0]) { const f = b[0] > a[0] ? (t - a[0]) / (b[0] - a[0]) : 1; return a.slice(1).map((v, j) => v + (b[j + 1] - v) * f); }
  }
  return keys[keys.length - 1].slice(1);
}
const posAt = (keys, t) => lerpKeys(keys, t); // [x, y]
const ballAt = (ball, t) => lerpKeys(ball, t); // [x, y, h]
const EPS = 1e-6;
const phaseAt = (phases, t) => { let p = phases[0] || null; for (const ph of phases) if (ph.t <= t + EPS) p = ph; return p; };
function stepTime(dg, t, dir) { // a phase at a time: the previous phase's start, or the next one's (the end after the last)
  const ph = phaseAt(dg.phases, t); const i = dg.phases.indexOf(ph);
  if (dir > 0) return i + 1 < dg.phases.length ? dg.phases[i + 1].t : dg.duration;
  return i > 0 ? dg.phases[i - 1].t : 0;
}
const restT = dg => dg.phases.length ? dg.phases[dg.phases.length - 1].t : dg.duration; // reduced motion: the last phase, every path drawn
const marksAt = (marks, t) => marks.filter(m => m.t <= t + EPS);
function viewOf(f) { // the x range to draw: field.view, widened to MIN_SPAN, inside -10..110
  let [a, b] = f.view; const span = Math.max(b - a, MIN_SPAN); const c = (a + b) / 2;
  a = c - span / 2; b = c + span / 2;
  if (a < -10) { b += -10 - a; a = -10; } if (b > 110) { a -= b - 110; b = 110; }
  return [Math.max(-10, a), Math.min(110, b)];
}
function trailOf(keys, t) { const pts = keys.filter(k => k[0] < t).map(k => [k[1], k[2]]); const p = posAt(keys, t); return pts.concat([p]); }
function pathLen(pts) { let s = 0; for (let i = 1; i < pts.length; i++) s += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); return s; }
function partial(pts, frac) { // the first `frac` of a polyline, by length
  if (frac >= 1 || pts.length < 2) return pts;
  const want = pathLen(pts) * Math.max(0, frac); const out = [pts[0]]; let s = 0;
  for (let i = 1; i < pts.length; i++) {
    const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    if (s + d >= want) { const f = d ? (want - s) / d : 0; out.push([pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f]); return out; }
    s += d; out.push(pts[i]);
  }
  return out;
}
const nextPhaseT = (dg, t0) => { const n = dg.phases.find(p => p.t > t0 + EPS); return n ? n.t : dg.duration; };
const ptsAttr = pts => pts.map(p => `${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(' ');
const yardNo = x => { const v = x <= 50 ? x : 100 - x; return Math.round(v); };
function reducedMotion() {
  if (S.pb.motion === 'reduced') return true;
  try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
}

/* ---------- the payloads: a week's list (filtered in the page), a diagram, the stand-in clip ---------- */
function matchType(p, k) {
  if (k === 'run') return p.type === 'run';
  if (k === 'pass') return ['pass', 'sack', 'scramble', 'dropback'].includes(p.type);
  if (k === 'sack' || k === 'scramble') return p.type === k;
  return p.tags.includes(k);
}
function matchResult(p, k) {
  const r = p.result;
  if (k === 'first_down') return r.first_down; if (k === 'touchdown') return r.touchdown; if (k === 'turnover') return r.turnover;
  if (k === 'explosive') return p.explosive; if (k === 'negative') return r.yards != null && r.yards < 0;
  if (k === 'incomplete') return r.kind === 'incomplete'; if (k === 'success') return p.epa != null && p.epa > 0; // nflverse success: EPA > 0
  return false;
}
/** The list answer for the page's team, side and season and the browser's week and filters. */
function playsAnswer() {
  const st = S.pb.state;
  if (st === 'not_built') return { ans: P.states.not_built.plays };
  const byWeek = (((P.plays[String(S.season)] || {})[S.team] || {})[S.side]) || null;
  const pf = P.plays_filtered;
  if (!byWeek && !(pf && pf.team === S.team && pf.side === S.side && pf.season === S.season)) return { missing: `The dump has no ${S.season} play list for ${S.team}'s ${S.side}.` };
  const newest = byWeek && (byWeek.newest || Object.values(byWeek)[0]);
  if (st === 'no_plays') return { ans: Object.assign({}, newest, { status: 'no_plays', message: `No plays for ${S.team} in ${S.season}.`, weeks: [], week: null, game: null, plays: [], counts: { total: 0, shown: 0 } }), stand: true };
  if (st === 'no_week') { const nw = P.states.no_week; return { ans: Object.assign({}, nw, { team: S.team, side: S.side, season: S.season, weeks: newest ? newest.weeks : nw.weeks, message: nw.team === S.team && nw.season === S.season ? nw.message : nw.message.replace(/^\w+ /, S.team + ' ') }) }; }
  const f = { down: S.pb.down, type: S.pb.type, result: S.pb.result };
  // the real filtered answer (KC's defense, week 2: passes that made a first down)
  if (pf && pf.team === S.team && pf.side === S.side && pf.season === S.season && S.pb.week === pf.week && f.down === pf.filters.down && f.type === pf.filters.type && f.result === pf.filters.result) return { ans: lib(pf) };
  const wk = S.pb.week == null ? (newest && newest.week) : S.pb.week;
  const base = byWeek && (byWeek[String(wk)] || (newest && newest.week === wk ? newest : null));
  if (!base) return { ans: Object.assign({}, newest || pf, { week: wk, game: ((newest || pf).weeks || []).find(w => w.week === wk) || null, plays: [], counts: { total: 0, shown: 0 } }), missing: `The dump has week ${wk}'s list only with the filters of the real filtered answer (Passes · First downs), or not at all: pick another week.` };
  const plays = base.plays.filter(p => (f.down == null || p.down === f.down) && (f.type == null || matchType(p, f.type)) && (f.result == null || matchResult(p, f.result)));
  return { ans: lib(Object.assign({}, base, { filters: f, plays, counts: { total: base.counts.total, shown: plays.length } })) };
}
const lib = a => S.pb.lib === 'off' ? a : Object.assign({}, a, { library: true });
/** A play's diagram answer (or the 404), with the stand-in `real_example` when the library is on. */
function diagramFor(key) {
  if (S.pb.state === 'no_play') return { err: P.states.no_play };
  const d = P.diagrams[key];
  if (!d) return null;
  if (d.http_status) return { err: d };
  const dg = d.diagram;
  if (S.pb.lib !== 'off' && dg.concept && CONCEPTS[dg.concept]) {
    const c = CONCEPTS[dg.concept];
    return { dg: Object.assign({}, dg, { real_example: { concept: dg.concept, name: c[0], match: c[1], count: standIns(dg.concept).length || 1 } }) };
  }
  return { dg };
}
function standIns(concept) { return Object.values(P.diagrams).filter(d => d.diagram && d.diagram.concept === concept && d.diagram.play.season === 2026).slice(0, 10).map(d => d.diagram); }
/** The mockup's stand-in for a RealClipResponse: NO Big Data Bowl data. A reconstruction of the
 * same concept, densified to 10 keys a second with the players drifting after the snap, so the
 * player can be judged on dense keys; captioned as a stand-in wherever it shows. */
function standInClip(concept, index) {
  const c = CONCEPTS[concept];
  const base = { concept, name: c[0], match: c[1], note: 'Mockup stand-in for the server\'s note (the Big Data Bowl source and its terms: non-commercial use, kept on this computer, never redistributed).' };
  if (S.pb.lib === 'clip_nolib') return Object.assign(base, { status: 'no_library', message: 'The Big Data Bowl library isn\'t built on this computer (`uv run nfl playcalling library`, after the data is downloaded).', examples: [], index: null, diagram: null });
  const all = standIns(concept);
  if (S.pb.lib === 'clip_none' || !all.length) return Object.assign(base, { status: 'none', message: `No real example of ${c[0].toLowerCase()} in the library yet (stand-in message).`, examples: [], index: null, diagram: null });
  const i = Math.max(0, Math.min(all.length - 1, index));
  const src = all[i];
  return Object.assign(base, {
    status: 'ok', message: null, index: i,
    examples: all.map((d, k) => ({ index: k, label: `Stand-in ${k + 1} · from ${d.play.game_label} · ${d.play.down_text || ''}` })),
    diagram: densify(src, i),
  });
}
function densify(dg, seed) {
  const snap = (dg.phases.find(p => p.name === 'snap') || { t: 0.8 }).t;
  const end = dg.duration - 0.6;
  const steps = []; for (let t = 0; t <= end + 1e-9; t += 0.1) steps.push(Math.round(t * 10) / 10);
  const players = dg.players.map((p, k) => {
    const moving = p.track.length > 1;
    const keys = steps.map(t => {
      let [x, y] = posAt(p.track, t);
      const after = Math.max(0, t - snap);
      if (!moving && after > 0) { // the generic spots drift like real players would (synthetic)
        const dir = p.side === 'offense' ? 1 : -0.6;
        x += dir * Math.min(after, 2.4) * (p.role === 'receiver' ? 3.2 : 1.1);
        y += Math.sin(after * 1.7 + k + seed) * 0.9;
      }
      const w = 0.12 * Math.sin(t * 5.3 + k * 1.9); // a tracked dot never sits perfectly still
      return [t, +(x + w).toFixed(2), +(y + w * 0.7).toFixed(2)];
    });
    return Object.assign({}, p, { approx: false, track: keys });
  });
  const ball = steps.map(t => { const b = ballAt(dg.ball, t); return [t, +b[0].toFixed(2), +b[1].toFixed(2), +b[2].toFixed(2)]; });
  return Object.assign({}, dg, {
    kind: 'real', label: `Real play: 2023 week ${1 + seed}, Big Data Bowl data`, players, ball,
    marks: dg.marks.map(m => Object.assign({}, m, { approx: false })),
    known: ['Every player\'s position, 10 times a second (Big Data Bowl tracking)', 'The ball\'s path and the play\'s events (tracking)'],
    approximate: [], unknown: ['Assignments: the dots are where the players were, not what they were asked to do'],
    sources: ['NFL Big Data Bowl (Kaggle), on this computer only'], real_example: null,
  });
}

/* ---------- the browser card (after Week by week, before History; it loads only once opened) ---------- */
function playsCard(r) {
  const sideNote = S.side === 'offense' ? `${S.team}'s plays, one game at a time` : `The plays run against ${S.team}'s defense, one game at a time`;
  if (!S.pb.open) {
    return `<div class="card pbcard" id="pbCard"><div class="card-h"><h2>Plays</h2><span class="muted">${esc(sideNote)}</span>
      <div class="right"><button class="btn sm" id="pbBtn" aria-expanded="false">Show plays</button></div></div>
      <div class="card-b muted" style="font-size:13px">Every play of a game, filtered by down, play type and result. Pick one to watch it drawn as a playbook card, <b>reconstructed from the charting data</b> (there's no public player tracking for these seasons). Opens on request.</div></div>`;
  }
  const head = inner => `<div class="card pbcard" id="pbCard"><div class="card-h"><h2>Plays</h2><span class="muted">${esc(sideNote)}</span>
      <div class="right"><button class="btn sm" id="pbBtn" aria-expanded="true">Hide</button></div></div>${inner}</div>`;
  const got = playsAnswer();
  if (!got.ans) return head(`<div class="card-b">${mockOnly(esc(got.missing))}</div>`);
  const a = got.ans;
  const stand = got.stand ? mockOnly('The reader\'s no-plays answer, drawn with a stand-in message (the dump has none).') : '';
  if (a.status === 'not_built' || a.status === 'no_plays') {
    return head(`<div class="card-b">${stand}${emptyInline(a, a.status === 'not_built' ? `No play-by-play tables for ${a.season}` : 'No plays')}</div>`);
  }
  const weekBar = pbWeekBar(a);
  if (a.status === 'no_week') {
    return head(`<div class="card-b pbbody">${weekBar}${emptyInline(a, 'No plays that week')}</div>`);
  }
  const filters = pbFilters(a);
  const list = pbList(a);
  const sel = a.plays.find(p => `${p.game_id}/${p.play_id}` === S.pb.sel) ? S.pb.sel : (a.plays[0] ? `${a.plays[0].game_id}/${a.plays[0].play_id}` : null);
  if (sel !== S.pb.sel) { S.pb.sel = sel; S.pb.real = null; }
  const libLine = a.library ? '' : `<p class="pblib muted"><span class="ic" aria-hidden="true">○</span> Real examples ("See a real one") need the Big Data Bowl library on this computer.</p>`;
  return head(`<div class="card-b pbbody">${got.missing ? mockOnly(esc(got.missing)) : ''}${weekBar}${filters}
    <div class="pbgrid"><div class="pbleft">${list}${libLine}</div><div class="pbright" id="pbPlayer">${playerPanel(sel)}</div></div></div>`);
}
function emptyInline(meta, title) {
  const msg = meta && meta.message;
  const cmd = msg && (msg.match(/`([^`]+)`/) || [])[1];
  return `<div class="empty pbempty"><div class="glyph">▶</div><h3>${esc(title)}</h3><p>${esc((msg || '').replace(/\s*\(`[^`]+`\)/, '').replace(/`([^`]+)`/g, '$1'))}</p>${cmd ? `<div class="cmd">${esc(cmd)}</div>` : ''}</div>`;
}
function pbWeekBar(a) {
  const ws = a.weeks || [];
  const i = ws.findIndex(w => w.week === a.week);
  const prev = i > 0 ? ws[i - 1] : (i < 0 && ws.length ? null : null), next = i >= 0 && i < ws.length - 1 ? ws[i + 1] : null;
  const opts = ws.map(w => `<option value="${w.week}"${w.week === a.week ? ' selected' : ''}>${esc(w.label)} · ${w.plays} plays</option>`).join('')
    + (a.week == null && S.pb.state === 'no_week' ? `<option selected disabled>Week 5 · no game</option>` : '');
  return `<div class="pbweek"><label class="sortsel"><span class="eyebrow">Game</span><select id="pbWeek" aria-label="Week">${opts}</select></label>
    <span class="seg" role="group" aria-label="Step weeks"><button type="button" data-pb-week="${prev ? prev.week : ''}" ${prev ? '' : 'disabled'} aria-label="Previous week">‹</button><button type="button" data-pb-week="${next ? next.week : ''}" ${next ? '' : 'disabled'} aria-label="Next week">›</button></span>
    ${a.counts && a.status === 'ok' ? `<span class="pbcount num" aria-live="polite">Showing <b>${a.counts.shown}</b> of ${a.counts.total} plays</span>` : ''}</div>`;
}
const ORD_D = { 1: '1st', 2: '2nd', 3: '3rd', 4: '4th' };
function pbFilters(a) {
  const f = a.filters || {};
  const grp = (key, label, opts) => `<div class="pbfg" role="group" aria-label="${esc(label)}"><span class="eyebrow">${esc(label)}</span><span class="pbchips">${opts.map(([v, l]) => `<button type="button" class="pbchip" data-pb-f="${key}" data-v="${esc(v)}" aria-pressed="${String(f[key] != null && String(f[key]) === String(v))}">${esc(l)}</button>`).join('')}</span></div>`;
  const any = f.down != null || f.type != null || f.result != null;
  return `<div class="pbfilters">${grp('down', 'Down', a.options.down.map(d => [d, ORD_D[d] || d]))}${grp('type', 'Play type', a.options.type.map(o => [o.value, o.label]))}${grp('result', 'Result', a.options.result.map(o => [o.value, o.label]))}
    ${any ? `<button type="button" class="linkbtn pbclear" data-pb-clear>Clear filters</button>` : ''}</div>`;
}
function playChips(p) {
  const chips = [`<span class="chip flat pbt">${esc(PB_TYPE[p.type] || p.type)}</span>`]
    .concat(p.tags.map(t => `<span class="chip ghost pbt" data-tip="${esc(PB_TAG_TIP[t] || t)}">${esc(PB_TAG[t] || t)}</span>`));
  if (p.result.touchdown) chips.push('<span class="chip ok pbt">TD</span>');
  if (p.result.turnover) chips.push('<span class="chip err pbt">Turnover</span>');
  if (!p.charted) chips.push('<span class="chip warn pbt" data-tip="FTN hasn\'t charted this play: the hash, the box, the rushers and motion are not known, so the diagram draws more generic spots">not charted</span>');
  return chips.join('');
}
const epaTxt = e => e == null ? '—' : signed(e, 2);
function pbList(a) {
  if (!a.plays.length) {
    return `<div class="empty pbempty"><div class="glyph">∅</div><h3>No plays match these filters</h3><p>${a.counts.total} plays in ${esc(a.game ? a.game.label : 'this game')}; none is ${esc(filterWords(a))}.</p><button class="btn sm" data-pb-clear>Clear filters</button></div>`;
  }
  return `<ol class="plist" aria-label="Plays, ${esc(a.game ? a.game.label : '')}">${a.plays.map(p => {
    const k = `${p.game_id}/${p.play_id}`;
    const on = k === S.pb.sel;
    return `<li><button type="button" class="prow${on ? ' on' : ''}${p.result.first_down ? ' fd' : ''}" data-pb-play="${esc(k)}" aria-pressed="${on}">
      <span class="pr1"><span class="pq num">${esc(p.quarter_text || '')} ${esc(p.clock || '')}</span><b class="pdd">${esc(p.down_text || 'No down')}</b><span class="psp muted">${esc(p.spot || '')}</span>
        <span class="pch">${playChips(p)}</span>
        <span class="pres">${esc(p.result.text)}</span><span class="pepa num" data-tip="${esc('Expected points added: ' + epaTxt(p.epa))}">${esc(epaTxt(p.epa))}<small>EPA</small></span></span>
      <span class="pr2">${esc(p.desc || '')}</span></button></li>`;
  }).join('')}</ol>`;
}
function filterWords(a) {
  const f = a.filters, out = [];
  if (f.down != null) out.push(`on ${ORD_D[f.down]} down`);
  if (f.type) out.push((a.options.type.find(o => o.value === f.type) || {}).label.toLowerCase());
  if (f.result) out.push((a.options.result.find(o => o.value === f.result) || {}).label.toLowerCase());
  return out.join(', ') || 'filtered';
}

/* ---------- the diagram player ---------- */
function currentDiagram(key) { // what the player shows: the reconstruction, or the real clip
  const got = diagramFor(key);
  if (!got || got.err) return { got };
  if (S.pb.real) { const clip = standInClip(S.pb.real.concept, S.pb.real.index); return { got, clip, dg: clip.diagram }; }
  return { got, dg: got.dg };
}
function playerPanel(key) {
  if (!key) return '';
  const { got, clip, dg } = currentDiagram(key);
  if (!got) return mockOnly(`The dump has no diagram for play ${esc(key)}: the mockup dumped every play of the newest week and week 3 (offense), the newest week on defense, and a few 2025 plays. Pick another play.`);
  if (got.err) return `<div class="card pdcard">${emptyInline({ message: got.err.error ? got.err.error.message : 'No such play.' }, 'This play can\'t be drawn')}</div>`;
  const rec = got.dg;
  if (PS.key !== key + (clip ? '#real' + clip.index : '')) { PS.key = key + (clip ? '#real' + clip.index : ''); PS.playing = false; PS.t = reducedMotion() && dg ? restT(dg) : 0; }
  const realBar = rec.real_example && !clip ? `<div class="pdreal"><button type="button" class="btn sm" data-pb-real="${esc(rec.real_example.concept)}"><span aria-hidden="true">▶</span> See a real one: ${esc(rec.real_example.name)}</button><span class="muted">matches ${esc(rec.real_example.match || 'this play')} · ${rec.real_example.count} real example${rec.real_example.count === 1 ? '' : 's'}</span></div>` : '';
  if (clip && clip.status !== 'ok') {
    return `<div class="card pdcard">${clipNav(clip)}${emptyInline(clip, clip.status === 'no_library' ? 'No Big Data Bowl library here' : `No real ${clip.name.toLowerCase()} yet`)}</div>`;
  }
  return `<div class="card pdcard${dg.kind === 'real' ? ' real' : ''}">
    ${clip ? clipNav(clip) : realBar}
    ${pdLabel(dg)}
    <div class="pdfield">${fieldSvg(dg)}</div>
    ${pdControls(dg)}
    ${pdLegend(dg)}
    ${clip ? `<p class="pdnote muted">${esc(clip.note)}</p>` : ''}
    ${playInfo(dg)}</div>`;
}
function clipNav(clip) {
  const n = clip.examples.length;
  return `<div class="pdreal real"><button type="button" class="btn sm" data-pb-back><span aria-hidden="true">←</span> Back to the reconstruction</button>
    <span class="grow"><b>${esc(clip.name)}</b>${clip.match ? `<span class="muted"> · ${esc(clip.match)}</span>` : ''}</span>
    ${n ? `<span class="seg" role="group" aria-label="Real examples"><button type="button" data-pb-ex="${clip.index - 1}" ${clip.index > 0 ? '' : 'disabled'} aria-label="Previous example">‹</button><button type="button" disabled class="exn">${clip.index + 1} of ${n}</button><button type="button" data-pb-ex="${clip.index + 1}" ${clip.index < n - 1 ? '' : 'disabled'} aria-label="Next example">›</button></span>` : ''}</div>`;
}
/** The label: part of the player, always on, at every width. */
function pdLabel(dg) {
  const real = dg.kind === 'real';
  return `<div class="pdlabel${real ? ' real' : ''}" role="note"><span class="ic" aria-hidden="true">${real ? '●' : '◇'}</span><b>${esc(dg.label)}</b>${real ? `<span class="standin">Illustrative stand-in: the app plays real Big Data Bowl tracking here</span>` : ''}</div>`;
}
function fieldSvg(dg) {
  const f = dg.field, [x0, x1] = viewOf(f), W = f.width;
  const top = -3.2, bot = W + 3.2;
  let s = `<defs><marker id="pdA" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="pdah"/></marker>
    <marker id="pdAs" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="pdahs"/></marker></defs>`;
  s += `<rect x="${x0}" y="${top}" width="${x1 - x0}" height="${bot - top}" class="pdturf2"/><rect x="${x0}" y="0" width="${x1 - x0}" height="${W}" class="pdturf"/>`;
  // the end zones, tinted
  if (x0 < 0) s += `<rect x="${x0}" y="0" width="${Math.min(0, x1) - x0}" height="${W}" class="pdez"/>`;
  if (x1 > 100) s += `<rect x="${Math.max(100, x0)}" y="0" width="${x1 - Math.max(100, x0)}" height="${W}" class="pdez"/>`;
  // yard lines every 5, numbers every 10, hash and sideline ticks every yard
  for (let x = Math.ceil(x0); x <= Math.floor(x1); x++) {
    if (x < 0 || x > 100) continue;
    if (x % 5 === 0) s += `<line x1="${x}" x2="${x}" y1="0" y2="${W}" class="pdyl${x === 0 || x === 100 ? ' goal' : ''}"/>`;
    else s += `<line x1="${x}" x2="${x}" y1="0.4" y2="1.2" class="pdtk"/><line x1="${x}" x2="${x}" y1="${W - 1.2}" y2="${W - 0.4}" class="pdtk"/><line x1="${x}" x2="${x}" y1="23.08" y2="24.08" class="pdtk"/><line x1="${x}" x2="${x}" y1="${W - 24.08}" y2="${W - 23.08}" class="pdtk"/>`;
    if (x % 10 === 0 && x > 0 && x < 100) { const n = yardNo(x); s += `<text x="${x}" y="8.6" class="pdno" text-anchor="middle">${n}</text><text x="${x}" y="${W - 6.6}" class="pdno" text-anchor="middle">${n}</text>`; }
  }
  s += `<line x1="${x0}" x2="${x1}" y1="0" y2="0" class="pdside"/><line x1="${x0}" x2="${x1}" y1="${W}" y2="${W}" class="pdside"/>`;
  // the line of scrimmage and the line to gain (none on goal to go)
  s += `<line x1="${f.los}" x2="${f.los}" y1="0" y2="${W}" class="pdlos"/><text x="${f.los}" y="${top + 2.3}" class="pdlt" text-anchor="middle">LOS</text>`;
  if (f.first_down != null && f.first_down < 100) s += `<line x1="${f.first_down}" x2="${f.first_down}" y1="0" y2="${W}" class="pdfd"/><text x="${f.first_down}" y="${top + 2.3}" class="pdlt fd" text-anchor="middle">1ST DOWN</text>`;
  else if (f.first_down == null) s += `<text x="${Math.min(x1 - 6, 104)}" y="${top + 2.3}" class="pdlt fd" text-anchor="middle">GOAL TO GO</text>`;
  if (!f.hash) s += `<text x="${x1 - 0.8}" y="${W + 2.4}" class="pdlt" text-anchor="end">hash not charted: the ball is drawn in the middle</text>`;
  const tag = dg.kind === 'real' ? 'REAL PLAY · TRACKING STAND-IN' : 'RECONSTRUCTED · NOT TRACKING';
  s += `<text x="${x0 + 0.8}" y="${W + 2.4}" class="pdwm">${tag}</text>`;
  const aria = `${dg.label}. ${dg.play.game_label || ''}: ${dg.play.down_text || ''} at ${dg.play.spot || ''}. ${dg.play.result.text}.`;
  return `<svg viewBox="${x0} ${top} ${x1 - x0} ${bot - top}" role="img" aria-label="${esc(aria)}" preserveAspectRatio="xMidYMid meet">${s}<g id="pdDyn">${dynLayer(dg, PS.t)}</g></svg>`;
}
/** The carrier is ringed once he has the ball: after the snap, the ball on the ground at his spot
 * (works for a reconstruction's phases and for a real clip's tracking events alike). */
function snapT(dg) { const s = dg.phases.find(p => p.name === 'snap' || p.name === 'ball_snap'); return s ? s.t : 0; }
function hasBall(dg, x, y, t) {
  if (t <= snapT(dg) + 0.25) return false;
  const b = ballAt(dg.ball, t); return !!b && b[2] < 2.2 && Math.hypot(b[0] - x, b[1] - y) < 1.6;
}
/** Everything that moves, at time t. */
function dynLayer(dg, t) {
  let s = '';
  const vis = marksAt(dg.marks, t);
  const endT = (() => { const e = dg.marks.find(m => ['end', 'incomplete', 'interception', 'sack'].includes(m.kind)); return e ? e.t : dg.duration; })();
  // the trails: where each player has been (only the ones that moved a couple of yards)
  for (const p of dg.players) {
    if (p.track.length < 2) continue;
    const tr = trailOf(p.track, t);
    if (pathLen(tr) < 1.5) continue;
    s += `<polyline points="${ptsAttr(tr)}" class="pdtr ${p.side === 'offense' ? 'o' : 'd'}${p.approx ? ' ap' : ''}"/>`;
  }
  for (const m of vis) {
    const pts = m.points;
    const prog = (a, b) => Math.max(0, Math.min(1, (t - a) / Math.max(0.05, b - a)));
    if (m.kind === 'motion') s += `<polyline points="${ptsAttr(partial(pts, prog(m.t, nextPhaseT(dg, m.t))))}" class="pdmk motion" marker-end="url(#pdA)"/>`;
    else if (m.kind === 'fake') { const pp = partial(pts, prog(m.t, nextPhaseT(dg, m.t))); s += `<polyline points="${ptsAttr(pp)}" class="pdmk fake" marker-end="url(#pdA)"/><text x="${pts[1][0] - 0.6}" y="${pts[1][1] + 2.6}" class="pdmt" text-anchor="end">fake</text>`; }
    else if (m.kind === 'handoff') s += `<g class="pdho"><circle cx="${pts[0][0]}" cy="${pts[0][1]}" r="0.9"/></g>`;
    else if (m.kind === 'lane') s += `<polyline points="${ptsAttr(pts)}" class="pdmk lane" marker-end="url(#pdAs)"/><text x="${pts[pts.length - 1][0] + 0.6}" y="${pts[pts.length - 1][1] - 2.4}" class="pdmt strong">${esc((m.text || '').replace(/^Run /, ''))}</text>`;
    else if (m.kind === 'run') s += `<polyline points="${ptsAttr(partial(pts, prog(m.t, endT)))}" class="pdmk run${m.approx ? ' ap' : ''}" marker-end="url(#pdA)"/>`;
    else if (m.kind === 'throw') s += throwArc(dg, m, t);
    else if (m.kind === 'end') s += endTag(dg, pts[0], m.text, 'end');
    else if (m.kind === 'incomplete' || m.kind === 'interception') s += `<g class="pdx"><path d="M${pts[0][0] - 0.9},${pts[0][1] - 0.9} l1.8,1.8 m0,-1.8 l-1.8,1.8"/></g>` + endTag(dg, pts[0], m.text, 'bad');
    else if (m.kind === 'sack') s += `<g class="pdx"><path d="M${pts[0][0]},${pts[0][1] - 1.4} l0.45,0.95 1,-0.3 -0.45,0.95 0.9,0.6 -1,0.25 0.1,1 -0.95,-0.5 -0.55,0.85 -0.1,-1 -1,0.15 0.6,-0.85 -0.75,-0.65 1,-0.15z" class="burst"/></g>` + endTag(dg, pts[0], m.text, 'bad');
  }
  // the players: the defense under the offense, the target and the carrier on top
  const order = [...dg.players].sort((a, b) => (a.side === 'defense' ? 0 : 1) - (b.side === 'defense' ? 0 : 1) || (a.target || a.carrier ? 1 : 0) - (b.target || b.carrier ? 1 : 0));
  for (const p of order) {
    const [x, y] = posAt(p.track, t);
    const cls = `pdp ${p.side === 'offense' ? 'o' : 'd'}${p.approx ? ' ap' : ''}`;
    let g = '';
    if (p.rusher) g += `<circle cx="${x}" cy="${y}" r="1.75" class="pdrush"/>`;
    if (p.target && !(p.carrier && t >= endT - EPS)) g += `<circle cx="${x}" cy="${y}" r="2.05" class="pdtgt"/>`;
    if (p.carrier && hasBall(dg, x, y, t)) g += `<circle cx="${x}" cy="${y}" r="2.05" class="pdcar"/>`;
    g += `<circle cx="${x}" cy="${y}" r="1.12" class="${cls}"/><text x="${x}" y="${y + 0.38}" class="pdpl ${p.side === 'offense' ? 'o' : 'd'}${p.approx ? ' ap' : ''}" text-anchor="middle">${esc(p.label)}</text>`;
    s += `<g>${g}</g>`;
  }
  // the ball: its shadow on the ground, the ball lifted by its height
  const b = ballAt(dg.ball, t);
  if (b) {
    const [bx, by, h] = b;
    if (h > 1.2) s += `<ellipse cx="${bx}" cy="${by}" rx="0.55" ry="0.32" class="pdbs"/>`;
    const sc = 1 + Math.max(0, h - 1) * 0.06;
    s += `<ellipse cx="${bx}" cy="${by - Math.max(0, h - 1) * LIFT}" rx="${(0.62 * sc).toFixed(2)}" ry="${(0.4 * sc).toFixed(2)}" class="pdball"/>`;
  }
  return s;
}
function throwArc(dg, m, t) {
  const [a, b] = m.points;
  const keys = dg.ball.filter(k => k[0] >= m.t - EPS);
  const catchK = keys.find(k => Math.abs(k[1] - b[0]) < 0.05 && Math.abs(k[2] - b[1]) < 0.05);
  const tEnd = catchK ? catchK[0] : (keys.length ? keys[keys.length - 1][0] : m.t);
  const lift = k => [k[1], k[2] - Math.max(0, k[3] - 1) * LIFT];
  const pts = [[a[0], a[1]]].concat(keys.filter(k => k[0] <= Math.min(t, tEnd) + EPS).map(lift));
  if (t < tEnd) { const bb = ballAt(dg.ball, t); pts.push([bb[0], bb[1] - Math.max(0, bb[2] - 1) * LIFT]); }
  return `<polyline points="${ptsAttr(pts)}" class="pdmk throw"${t >= tEnd ? ' marker-end="url(#pdAs)"' : ''}/><line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" class="pdmk throwg"/>`;
}
function endTag(dg, p, text, tone) {
  if (!text) return '';
  const [, x1] = viewOf(dg.field);
  const fs = 1.45, w = text.length * fs * 0.52 + 1.6, h = 2.6;
  const left = p[0] + 2 + w > x1 - 0.5;
  const tx = left ? p[0] - 2 - w : p[0] + 2;
  const ty = Math.max(0.6, Math.min(dg.field.width - h - 0.6, p[1] - h - 0.8));
  return `<g class="pdtag ${tone}"><line x1="${p[0]}" y1="${p[1]}" x2="${left ? tx + w : tx}" y2="${ty + h / 2}"/><rect x="${tx}" y="${ty}" width="${w}" height="${h}" rx="0.5"/><text x="${tx + 0.8}" y="${ty + 1.85}">${esc(text)}</text></g>`;
}
const ICO = {
  restart: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M3 8a5 5 0 1 0 1.6-3.7M3 2.5v3h3"/></svg>',
  back: '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M3 3h1.8v10H3zM13 3v10L5.6 8z"/></svg>',
  fwd: '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M11.2 3H13v10h-1.8zM3 3v10l7.4-5z"/></svg>',
  play: '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M4 2.5v11l9.5-5.5z"/></svg>',
  pause: '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M3.5 2.5h3v11h-3zM9.5 2.5h3v11h-3z"/></svg>',
};
function pdControls(dg) {
  const rm = reducedMotion();
  const ph = phaseAt(dg.phases, PS.t);
  const idx = dg.phases.indexOf(ph);
  const ticks = dg.phases.map((p, i) => `<button type="button" class="pdtick${p === ph ? ' on' : ''}" style="left:${(p.t / dg.duration * 100).toFixed(1)}%" data-pb-seek="${p.t}" aria-label="${esc(`Jump to ${i + 1}: ${p.text}`)}" data-tip="${esc(p.text)}"></button>`).join('');
  return `<div class="pdctl">
    <div class="pdbtns">
      <button type="button" class="iconbtn" data-pb-ctl="restart" aria-label="Restart">${ICO.restart}</button>
      <button type="button" class="iconbtn" data-pb-ctl="back" aria-label="Step back a phase">${ICO.back}</button>
      ${rm ? '' : `<button type="button" class="iconbtn play" data-pb-ctl="play" id="pdPlay" aria-label="${PS.playing ? 'Pause' : 'Play'}">${PS.playing ? ICO.pause : ICO.play}</button>`}
      <button type="button" class="iconbtn" data-pb-ctl="fwd" aria-label="Step forward a phase">${ICO.fwd}</button>
    </div>
    <div class="pdscrub"><input type="range" id="pdScrub" min="0" max="${dg.duration}" step="0.02" value="${PS.t.toFixed(2)}" aria-label="Time in the animation" aria-valuetext="${esc(ph ? ph.text : '')}"><span class="pdticks">${ticks}</span></div>
    <span class="pdtime num" id="pdTime">${PS.t.toFixed(1)} / ${dg.duration.toFixed(1)} s</span>
  </div>
  <div class="pdphase" id="pdPhase" aria-live="polite"><span class="pdpn num">${idx + 1}/${dg.phases.length}</span><span>${esc(ph ? ph.text : '')}</span>${rm ? '<span class="chip ghost">reduced motion: step through the phases</span>' : ''}</div>`;
}
function pdLegend(dg) {
  const real = dg.kind === 'real';
  return `<div class="pdlegend" aria-label="Legend">
    <span><i class="lg-dot o"></i>offense</span><span><i class="lg-dot d"></i>defense</span>
    ${real ? '' : '<span><i class="lg-dot ap"></i>generic spot (approximate)</span>'}
    <span><i class="lg-ring rush"></i>rusher</span><span><i class="lg-ring tgt"></i>target</span><span><i class="lg-ring car"></i>ball carrier</span>
    <span><i class="lg-line los"></i>line of scrimmage</span><span><i class="lg-line fd"></i>line to gain</span>${real ? '' : '<span><i class="lg-line ap"></i>approximate path</span>'}
  </div>`;
}
function playInfo(dg) {
  const p = dg.play;
  const li = (arr, cls) => arr && arr.length ? arr.map(x => `<li>${esc(x)}</li>`).join('') : `<li class="muted">${cls === 'ap' ? 'Nothing' : 'None'}</li>`;
  return `<div class="pdinfo">
    <div class="pdhead"><span class="eyebrow">${esc(p.game_label || '')}${p.season ? ` · ${p.season}` : ''}</span>
      <div class="pdh1"><b class="num">${esc(p.quarter_text || '')} ${esc(p.clock || '')}</b><b>${esc(p.down_text || '')}</b><span class="muted">${esc(p.spot || '')}</span>${playChips(p)}</div>
      <div class="pdh2"><b>${esc(p.result.text)}</b><span class="muted num">EPA ${esc(epaTxt(p.epa))}</span></div>
      ${p.desc ? `<p class="pddesc">${esc(p.desc)}</p>` : ''}</div>
    <div class="pdkau">
      <section><h4><i class="kk kn"></i>Known</h4><ul>${li(dg.known)}</ul></section>
      <section><h4><i class="kk ap"></i>Approximate <small>drawn at generic spots</small></h4><ul>${li(dg.approximate, 'ap')}</ul></section>
      <section><h4><i class="kk un"></i>Not in the data <small>not drawn</small></h4><ul>${li(dg.unknown)}</ul></section>
    </div>
    <p class="pdsrc muted">Sources: ${esc((dg.sources || []).join(' · '))}</p></div>`;
}
/* ---------- the clock ---------- */
function pdShown() { const k = S.pb.sel; if (!k) return null; const c = currentDiagram(k); return c.dg || null; }
function pdDraw() {
  const dg = pdShown(); const g = document.getElementById('pdDyn');
  if (!dg || !g) { PS.playing = false; return; }
  g.innerHTML = dynLayer(dg, PS.t);
  const sc = document.getElementById('pdScrub'); const ph = phaseAt(dg.phases, PS.t);
  if (sc) { sc.value = PS.t.toFixed(2); sc.setAttribute('aria-valuetext', ph ? ph.text : ''); }
  const tm = document.getElementById('pdTime'); if (tm) tm.textContent = `${PS.t.toFixed(1)} / ${dg.duration.toFixed(1)} s`;
  const pe = document.getElementById('pdPhase');
  if (pe) { const span = pe.children[1]; const idx = dg.phases.indexOf(ph); pe.children[0].textContent = `${idx + 1}/${dg.phases.length}`; if (span && span.textContent !== (ph ? ph.text : '')) span.textContent = ph ? ph.text : ''; }
  document.querySelectorAll('.pdtick').forEach((b, i) => b.classList.toggle('on', dg.phases[i] === ph));
  const pb = document.getElementById('pdPlay'); if (pb) { pb.innerHTML = PS.playing ? ICO.pause : ICO.play; pb.setAttribute('aria-label', PS.playing ? 'Pause' : 'Play'); }
}
function pdFrame(now) {
  if (!PS.playing) return;
  const dg = pdShown(); if (!dg) { PS.playing = false; return; }
  PS.t = Math.min(dg.duration, PS.t + Math.min(0.1, (now - PS.last) / 1000)); PS.last = now;
  if (PS.t >= dg.duration) PS.playing = false;
  pdDraw();
  if (PS.playing) PS.raf = requestAnimationFrame(pdFrame);
}
function pdPlay() {
  const dg = pdShown(); if (!dg || reducedMotion()) return;
  if (PS.t >= dg.duration - 1e-3) PS.t = 0;
  PS.playing = true; PS.last = performance.now(); cancelAnimationFrame(PS.raf); PS.raf = requestAnimationFrame(pdFrame); pdDraw();
}
function pdPause() { PS.playing = false; cancelAnimationFrame(PS.raf); pdDraw(); }
function pdControl(what) {
  const dg = pdShown(); if (!dg) return;
  if (what === 'play') { PS.playing ? pdPause() : pdPlay(); return; }
  pdPause();
  if (what === 'restart') { PS.t = 0; if (!reducedMotion()) { pdPlay(); return; } }
  else if (what === 'back') PS.t = stepTime(dg, PS.t, -1);
  else if (what === 'fwd') PS.t = stepTime(dg, PS.t, 1);
  pdDraw();
}
function pbSelect(key, auto) {
  S.pb.sel = key; S.pb.real = null; PS.key = null;
  render();
  if (auto && !reducedMotion()) pdPlay();
}
function pbFocusPlayer() { const el = document.getElementById('pbPlayer'); if (el && innerWidth < 1100) el.scrollIntoView({ block: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' }); }

/* ------------------------------------------------------------------ interactions */
$('#view').addEventListener('click', e => {
  const pbb = e.target.closest('#pbBtn'); if (pbb) { S.pb.open = !S.pb.open; pdPause(); render(); return; }
  const pw = e.target.closest('[data-pb-week]'); if (pw && pw.dataset.pbWeek) { S.pb.week = Number(pw.dataset.pbWeek); S.pb.sel = null; S.pb.real = null; if (S.pb.state === 'no_week') S.pb.state = 'ok'; pdPause(); render(); return; }
  const pf = e.target.closest('[data-pb-f]'); if (pf) { const k = pf.dataset.pbF; const v = k === 'down' ? Number(pf.dataset.v) : pf.dataset.v; S.pb[k] = S.pb[k] === v ? null : v; pdPause(); render(); const again = document.querySelector(`[data-pb-f="${k}"][data-v="${CSS.escape(String(v))}"]`); if (again) again.focus(); return; }
  if (e.target.closest('[data-pb-clear]')) { S.pb.down = S.pb.type = S.pb.result = null; pdPause(); render(); return; }
  const pp = e.target.closest('[data-pb-play]'); if (pp) { pbSelect(pp.dataset.pbPlay, true); const again = document.querySelector(`[data-pb-play="${CSS.escape(pp.dataset.pbPlay)}"]`); if (again) again.focus({ preventScroll: true }); pbFocusPlayer(); return; }
  const pc = e.target.closest('[data-pb-ctl]'); if (pc) { pdControl(pc.dataset.pbCtl); return; }
  const sk = e.target.closest('[data-pb-seek]'); if (sk) { pdPause(); PS.t = Number(sk.dataset.pbSeek); pdDraw(); return; }
  const rl = e.target.closest('[data-pb-real]'); if (rl) { pdPause(); S.pb.real = { concept: rl.dataset.pbReal, index: 0 }; PS.key = null; render(); if (!reducedMotion() && pdShown()) pdPlay(); return; }
  const ex = e.target.closest('[data-pb-ex]'); if (ex) { pdPause(); S.pb.real = Object.assign({}, S.pb.real, { index: Number(ex.dataset.pbEx) }); PS.key = null; render(); if (!reducedMotion()) pdPlay(); return; }
  if (e.target.closest('[data-pb-back]')) { pdPause(); S.pb.real = null; PS.key = null; render(); return; }
  if (e.target.closest('[data-nav-grid]')) { S.screen = 'grid'; syncMockbar(); render(); return; }
  const t = e.target.closest('[data-team]'); if (t) { S.team = t.dataset.team; S.screen = 'team'; S.hist = false; syncMockbar(); render(); window.scrollTo({ top: 0 }); return; }
  const ot = e.target.closest('[data-open-team]'); if (ot) { S.team = ot.dataset.openTeam; S.screen = 'team'; S.season = NEWEST; S.hist = false; syncMockbar(); render(); window.scrollTo({ top: 0 }); return; }
  const gv = e.target.closest('[data-gv]'); if (gv) { S.gridView = gv.dataset.gv; render(); return; }
  const so = e.target.closest('[data-sort]'); if (so) { const id = so.dataset.sort; S.sort = S.sort.id === id ? { id, dir: -S.sort.dir } : { id, dir: id === 'team' ? 1 : -1 }; render(); return; }
  if (e.target.closest('#sortDir')) { S.sort.dir = -S.sort.dir; render(); return; }
  const w = e.target.closest('[data-win]'); if (w) { S.win = w.dataset.win; render(); return; }
  const f = e.target.closest('[data-fam]'); if (f) { S.fam = f.dataset.fam; render(); return; }
  if (e.target.closest('#histBtn')) { S.hist = !S.hist; render(); return; }
  const pv = e.target.closest('[data-pcv]'); if (pv) { S.pcView = pv.dataset.pcv; render(); return; }
  if (e.target.closest('#updBtn')) { updConfirm(); return; }
  if (e.target.closest('[data-upd-hide]')) { S.upd = 'real'; syncMockbar(); render(); return; }
  if (e.target.closest('[data-go-week]')) goNextWeek();
});
$('#view').addEventListener('change', e => {
  if (e.target.id === 'sortSel') { const id = e.target.value; S.sort = { id, dir: id === 'team' ? 1 : -1 }; render(); return; }
  if (e.target.id === 'pbWeek') { S.pb.week = Number(e.target.value); S.pb.sel = null; S.pb.real = null; if (S.pb.state === 'no_week') S.pb.state = 'ok'; pdPause(); render(); const w = document.getElementById('pbWeek'); if (w) w.focus(); }
});
$('#view').addEventListener('input', e => { if (e.target.id === 'pdScrub') { PS.playing = false; cancelAnimationFrame(PS.raf); PS.t = Number(e.target.value); pdDraw(); } });
$('#view').addEventListener('keydown', e => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('tr.teamrow')) { e.preventDefault(); e.target.click(); return; }
  // the play list: up / down move through the plays (and play the one reached), Home / End jump
  if (e.target.matches('.prow') && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
    e.preventDefault();
    const rows = $$('.prow'); const i = rows.indexOf(e.target);
    const j = e.key === 'Home' ? 0 : e.key === 'End' ? rows.length - 1 : Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)));
    if (j !== i) { const k = rows[j].dataset.pbPlay; pbSelect(k, true); const again = document.querySelector(`[data-pb-play="${CSS.escape(k)}"]`); if (again) again.focus(); }
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
document.addEventListener('keydown', e => { if (e.key === 'Escape') { const sc = document.getElementById('updScrim'); if (sc) sc.remove(); hideTip(); if (S.settings) { S.settings = false; renderSide(); } } });

/* ------------------------------------------------------------------ boot */
syncMockbar();
render();
})();
