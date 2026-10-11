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
const signed = (x, d) => (x > 0 ? '+' : x < 0 ? MINUS : '') + Math.abs(x).toFixed(d);
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
const ALLOWED_NOTE = "What offenses do against this defense depends on the offenses it faced (PC02 adjusts for it).";
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
  $('#weekPick').innerHTML = WEEK_KEYS.map(k => { const [sn, w] = k.split('_'); const st = weekFor(k).status; return `<button type="button" data-v="${esc(k)}" aria-pressed="${k === S.weekKey}">${sn} wk ${w}${st === 'ok' ? '' : ' · ' + st.replace('_', ' ')}</button>`; }).join('');
  $('#weekGrp').style.opacity = S.screen === 'week' ? '' : '.45';
  $$('#statePick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.state)));
  applyTheme();
}
try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (S.mode === 'system') applyTheme(); }); } catch (e) { /* old browsers */ }
$('#screenPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.screen = b.dataset.v; syncMockbar(); render(); });
$('#teamPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.team = b.dataset.v; S.screen = 'team'; S.season = NEWEST; S.hist = false; syncMockbar(); render(); });
$('#weekPick').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return; S.weekKey = b.dataset.v; S.screen = 'week'; S.state = 'ok'; syncMockbar(); render(); });
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
$('#top').addEventListener('change', e => { if (e.target.id === 'seasonSel') { S.season = Number(e.target.value); render(); } });
$('#top').addEventListener('click', e => {
  const s = e.target.closest('[data-season]'); if (s) { S.season = Number(s.dataset.season); if (S.win === 'last_season' && S.season !== NEWEST) S.win = 'season'; render(); return; }
  const d = e.target.closest('[data-side]'); if (d) { S.side = d.dataset.side; store.set('pc.side', S.side); render(); return; }
  if (e.target.closest('[data-nav-grid]')) { S.screen = 'grid'; syncMockbar(); render(); return; }
  if (e.target.closest('[data-go-week]')) { S.screen = 'week'; syncMockbar(); render(); window.scrollTo({ top: 0 }); }
});

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
    <div class="wintool"><span class="eyebrow">Window</span>${winSeg}<span class="muted">${esc(winHelp(r))}</span></div>
    ${identityCard(r)}
    ${situationsCard(r)}
    ${fieldCard(r)}
    ${weeklyCard(r)}
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
  const shifts = wk.shifts.length ? `<div class="card"><div class="card-h"><h2>Biggest matchup shifts</h2><span class="muted">where a defense allows or calls something far from the league · in standard deviations of the 32 defenses</span></div>
    <ol class="shifts">${wk.shifts.map(sh => { const w = Math.min(100, Math.abs(sh.shift) / 3 * 100); return `<li><span class="shm" aria-hidden="true"><span class="shb ${sh.shift > 0 ? 'up' : 'dn'}" style="width:${w.toFixed(0)}%"></span></span><span class="sht">${esc(sh.text)}</span><span class="num muted shv">${signed(sh.shift, 1)} SD</span></li>`; }).join('')}</ol></div>` : '';
  const games = wk.games.map(g => gameCard(g, wk)).join('');
  return `<div class="notice accent"><span class="ic">i</span><div>${esc(wk.note)}</div></div>
    ${wk.ftn_waiting && wk.ftn_waiting.length ? `<div class="notice"><span class="ic">!</span><div><b>FTN hasn't charted ${wk.ftn_waiting.length === 1 ? 'one game' : wk.ftn_waiting.length + ' games'} yet:</b> ${esc(wk.ftn_waiting.join(' · '))}. Play-action, screens, RPO, motion, blitz and box rates wait for it.</div></div>` : ''}
    ${shifts}
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

/* ------------------------------------------------------------------ interactions */
$('#view').addEventListener('click', e => {
  if (e.target.closest('[data-nav-grid]')) { S.screen = 'grid'; syncMockbar(); render(); return; }
  const t = e.target.closest('[data-team]'); if (t) { S.team = t.dataset.team; S.screen = 'team'; S.hist = false; syncMockbar(); render(); window.scrollTo({ top: 0 }); return; }
  const ot = e.target.closest('[data-open-team]'); if (ot) { S.team = ot.dataset.openTeam; S.screen = 'team'; S.season = NEWEST; S.hist = false; syncMockbar(); render(); window.scrollTo({ top: 0 }); return; }
  const gv = e.target.closest('[data-gv]'); if (gv) { S.gridView = gv.dataset.gv; render(); return; }
  const so = e.target.closest('[data-sort]'); if (so) { const id = so.dataset.sort; S.sort = S.sort.id === id ? { id, dir: -S.sort.dir } : { id, dir: id === 'team' ? 1 : -1 }; render(); return; }
  if (e.target.closest('#sortDir')) { S.sort.dir = -S.sort.dir; render(); return; }
  const w = e.target.closest('[data-win]'); if (w) { S.win = w.dataset.win; render(); return; }
  const f = e.target.closest('[data-fam]'); if (f) { S.fam = f.dataset.fam; render(); return; }
  if (e.target.closest('#histBtn')) { S.hist = !S.hist; render(); }
});
$('#view').addEventListener('change', e => { if (e.target.id === 'sortSel') { const id = e.target.value; S.sort = { id, dir: id === 'team' ? 1 : -1 }; render(); } });
$('#view').addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('tr.teamrow')) { e.preventDefault(); e.target.click(); } });

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
