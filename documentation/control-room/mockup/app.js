(() => {
'use strict';
const D = JSON.parse(document.getElementById('data').textContent);
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem('cr.' + k); return v == null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('cr.' + k, v); } catch (e) { /* storage blocked: fine */ } },
};
const WANDB = 'https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine';
const DASHBOARD = WANDB + '/reports/2026-Season-Dashboard--VmlldzoxODA1NTI5MQ==';

/* ------------------------------------------------------------------ state */
const PALS = [
  { id: 'turf', name: 'Turf & Pylon', sw: ['#0D1B14', '#FF6B2C', '#4FD98F'], note: 'field green, end-zone pylon orange' },
  { id: 'playbook', name: 'Playbook', sw: ['#FFFFFF', '#1F5FD1', '#CC2F2F'], note: 'whiteboard by day, chalkboard by night' },
];
const VIEWS = [['drive', 'Drive chart'], ['graph', 'Pipeline map'], ['timeline', 'Timeline']];
function systemDark() {
  const t = document.documentElement.getAttribute('data-theme');
  if (t === 'dark') return true;
  if (t === 'light') return false;
  try { return matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) { return true; }
}
const S = {
  pal: store.get('pal', 'turf'),
  mode: store.get('mode', 'system'),
  vizByWeek: (() => { try { return JSON.parse(store.get('vizByWeek', '{}')) || {}; } catch (e) { return {}; } })(),
  surprised: {},
  w5: 'ready', page: 'week', week: 5, tab: 'pipeline', mlops: 'health', settings: false,
  group: 'All', season: '2025', conf: 'All', openTeam: null,
};
if (!PALS.some(p => p.id === S.pal)) S.pal = 'turf';
if (!['system', 'dark', 'light'].includes(S.mode)) S.mode = 'system';
const vizFor = w => VIEWS.some(v => v[0] === S.vizByWeek[w]) ? S.vizByWeek[w] : 'drive';
function setViz(w, v) { S.vizByWeek[w] = v; store.set('vizByWeek', JSON.stringify(S.vizByWeek)); }

/* ------------------------------------------------------------------ helpers */
const team = a => D.teams[a] || { nick: a, name: a, color: '#888888', conf: '', div: '' };
const tchip = (a, full) => `<span class="team"><i style="background:${esc(team(a).color)}"></i><b>${esc(full ? team(a).nick : a)}</b></span>`;
const ETF = o => new Intl.DateTimeFormat('en-US', Object.assign({ timeZone: 'America/New_York' }, o));
const kick = iso => ETF({ weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
const kickDay = iso => ETF({ weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(iso));
const pct = (p, d = 0) => p == null ? '—' : (p * 100).toFixed(d) + '%';
const fx = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d);
const sgn = (v, d = 1) => v == null ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d);
const comma = v => Number(v).toLocaleString('en-US');
function dur(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + ' s';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ' + String(sec % 60).padStart(2, '0') + 's';
  return Math.floor(sec / 3600) + ' h ' + Math.round((sec % 3600) / 60) + ' m';
}
function clockStr(sec) { // pipeline clock from 10:00:12 ET
  const t = 10 * 3600 + 12 + Math.round(sec);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
function rng(seed) {
  return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}
function gauss(r) { let u = 0, v = 0; while (u === 0) u = r(); while (v === 0) v = r(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
const ICON = {
  season: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 13.5h12M3.5 11l3-4 2.5 2.5L13 4"/></svg>',
  teams: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 3.5h10M3 8h10M3 12.5h6"/></svg>',
  models: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M8 1.8l5.5 3.1v6.2L8 14.2l-5.5-3.1V4.9z M8 8v6.2 M8 8l5.5-3.1 M8 8L2.5 4.9"/></svg>',
  alerts: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 11V7a4 4 0 0 1 8 0v4l1 1.5H3z M6.5 14h3"/></svg>',
  health: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1.5 8.5h3l1.5-4 3 8 1.5-4h4"/></svg>',
};

/* ------------------------------------------------------------------ pipeline definition */
const DATASETS = D.w4_ingest.results.map(r => `${r.source} · ${r.dataset}`);
const TARGETS = ['pass_yds-qb', 'pass_epa-qb', 'pass_tds-qb', 'ints-qb', 'rush_yds-qb', 'rush_yds-rb', 'carries-rb', 'receptions-rb', 'scrim_yds-rb', 'td-rb',
  'rec_yds-wrte', 'targets-wrte', 'receptions-wrte', 'td-wrte', 'pressures-edge', 'sacks-edge', 'qb_hits-edge', 'tackles-lbs', 'cov_tgt-cbs',
  'cov_cmp-cbs', 'cov_yds-cbs', 'int-cbs', 'pd-cbs'];
const nOf = (p, n) => Math.min(n, Math.floor(p * n) + 1);
const STEPS = [
  { k: 'ingest', name: 'Ingest', real: 25, demo: 3.6, yd: 30, chip: 'raw snapshots',
    sub: p => `dataset ${nOf(p, 30)}/30 · ${DATASETS[nOf(p, 30) - 1]}`,
    done5: '30 datasets (0 optional failures)',
    logs: [[0, 'ingest: nflverse, NGS, PFR, FTN, ESPN, The Odds API'], ...DATASETS.map((d, i) => [(i + 1) / 31, `  ✓ ${d}`, 'dim']), [1, 'ingest: 30 datasets (0 optional failures)', 'ok']] },
  { k: 'ready', name: 'Ready check', real: 1, demo: 1.1, yd: 35, chip: '16/16 final',
    sub: () => 'week 4: 16/16 final · checking play-by-play', done5: 'week 4: 16/16 final, 16/16 in play-by-play',
    logs: [[0, 'ready: is week 4 complete in the data?'], [1, '2026 week 4: READY (16/16 games final, 16/16 in play-by-play)', 'ok']] },
  { k: 'curate', name: 'Curate', real: 16, demo: 2.6, yd: 45, chip: 'nfl.duckdb',
    sub: p => p < 0.78 ? `table ${nOf(p / 0.78, 32)}/32` : `quality check ${nOf((p - 0.78) / 0.22, 17)}/17`,
    done5: '32 tables; 17/17 quality checks pass',
    logs: [[0, 'curate: building 32 tables into curated/'], [0.5, '  games, plays, player_games, team_games, snaps, injuries …', 'dim'], [0.8, '  quality: games unique ✓  plays unique ✓  player_games unique ✓ …', 'dim'], [1, 'curate: 32 tables; quality checks pass', 'ok']] },
  { k: 'ratings', name: 'Ratings & Elo', real: 4, demo: 1.3, yd: 50, chip: 'team_elo.parquet',
    sub: p => ['team_ratings', 'team_elo', 'team_trends', 'team_trend_drivers'][nOf(p, 4) - 1], done5: 'ratings, Elo and trends rebuilt through week 4',
    logs: [[0, 'ratings: half-life 12, prior 0.1 (D46)'], [1, 'ratings: team_ratings, team_elo, team_trends, team_trend_drivers rebuilt', 'ok']] },
  { k: 'game', name: 'Game model', real: 15, demo: 2.4, yd: 60, chip: 'W&B · game-model', wb: true,
    sub: p => p < 0.5 ? 'fitting game-model v0 through week 4' : p < 0.85 ? 'predicting 15 games' : 'logging game-model:2026-w05',
    done5: '15 games · game-model:2026-w05 → production',
    logs: [[0, 'game: refresh game_features.parquet, fit v0 (walk-forward)'], [0.6, '  15 games, all with lines (market rows primary)', 'dim'], [0.9, '  W&B train-2026-w05 · artifact game-model:2026-w05 [2026-w05, production]', 'acc'], [1, 'game: predictions_games.parquet written', 'ok']] },
  { k: 'graph', name: 'Knowledge graph', real: 150, demo: 4.8, yd: 72, chip: 'Neo4j · graph-results', wb: true,
    sub: p => p < 0.3 ? "wiping last week's graph" : p < 0.8 ? `loading nodes & relationships ${Math.round((p - 0.3) / 0.5 * 100)}%` : p < 0.92 ? `running queries ${nOf((p - 0.8) / 0.12, 12)}/12` : 'GDS: PageRank + KNN',
    done5: '≈17.4k nodes · 12 queries · GDS ok · 4 insights picked',
    logs: [[0, 'graph: Neo4j up (bolt://localhost:7687)'], [0.3, '  wiped in 48 s; loading as of 2026 week 5', 'dim'], [0.8, '  loaded; running the query library', 'dim'], [0.93, '  gds/status ok (PageRank, KNN)', 'dim'], [1, 'graph: insights ranked · W&B graph-2026-w05', 'ok']] },
  { k: 'player', name: 'Player model', real: 95, demo: 5.4, yd: 85, chip: 'W&B · player-model', wb: true,
    sub: p => p < 0.1 ? 'scoreboard: grading week 4' : p < 0.85 ? `refitting ${nOf((p - 0.1) / 0.75, 23)}/23 · ${TARGETS[nOf((p - 0.1) / 0.75, 23) - 1]}` : p < 0.93 ? 'team stat totals' : 'consistency + graph projections',
    done5: 'graded week 4 · 23 stats · team totals · consistency ok',
    logs: [[0, 'player: scoreboard — grading week 4 against the box scores'], [0.1, '  refitting 23 live targets', 'dim'], [0.5, '  … rec_yds-wrte, targets-wrte, receptions-wrte, td-wrte …', 'dim'], [0.86, '  team: pass_yds, rush_yds, sacks_made, sacks_taken', 'dim'], [0.94, '  consistency: receptions ≤ targets (clamp on)', 'dim'], [1, 'player: projections written · W&B player-model:2026-w05', 'ok']] },
  { k: 'digest', name: 'Digest (GLM)', real: 540, demo: 9.5, yd: 100, chip: 'digest.md · W&B digest', wb: true,
    sub: p => p < 0.06 ? 'building payload.json' : p < 0.84 ? `GLM writing on BaseTen · ${comma(Math.round((p - 0.06) / 0.78 * 41200))} reasoning tokens` : p < 0.96 ? `checks ${nOf((p - 0.84) / 0.12, 11)}/11` : 'rendering digest.md',
    done5: 'checks passed first time · GLM on BaseTen 8m 41s',
    logs: [[0, 'digest: payload built (graph sections: 4 insights)'], [0.07, '  openrouter → z-ai/glm-5.3-flash · order: baseten/fp8, novita/fp8, relace', 'acc'], [0.4, '  … still writing (reasoning: max)', 'dim'], [0.84, '  GLM done: 8m 41s · 41,200 reasoning tokens · $0.012', 'acc'], [0.9, '  checks: complete ✓ number_provenance ✓ entity_binding ✓ meaning ✓ length ✓ …', 'dim'], [1, 'digest: reports/2026/week05-digest.md (checks passed)', 'ok']] },
  { k: 'records', name: 'Run records', real: 20, demo: 1.5, yd: 100, chip: 'pipeline-2026-w05', wb: true,
    sub: p => ['run_summary.json', 'pipeline_history.parquet', 'drift: 5 signals', 'W&B pipeline-2026-w05', 'season dashboard'][nOf(p, 5) - 1],
    done5: 'summary · history · 5 drift signals · W&B · dashboard',
    logs: [[0, 'records: freshness → drift → alerts → summary → history → W&B → dashboard'], [0.5, '  drift: game_vs_elo insufficient_data · calibration insufficient_data · player ok', 'dim'], [1, 'records: run_summary.json · pipeline-2026-w05 · season-2026-w05', 'ok']] },
];
const STEP_BY = Object.fromEntries(STEPS.map((s, i) => [s.k, i]));
const FAIL_AT = { k: 'player', p: 0.62, msg: 'player: refit failed for sacks-edge — calibration seed missing (runs/backtests/player/sacks-edge)' };
const W4_DETAIL = {
  ingest: '30 datasets (0 optional failures)', ready: 'week 3: 16/16 final, 16/16 in play-by-play', curate: '32 tables; quality checks pass',
  ratings: 'team_ratings, team_elo, team_trends, team_trend_drivers rebuilt', game: '16 games · game-model:2026-w04 → production',
  graph: '15,522 nodes, 588,330 relationships in 144 s; 3 insights picked', player: '1,860 projections (11 stats) written to the graph',
  digest: 'checks passed after one regeneration · GLM 38m 49s', records: 'not recorded (week 4 ran before run records existed)',
};

/* ------------------------------------------------------------------ simulation */
const SIM = { t: 0, speed: 1, raf: 0, last: 0, fail: false, from: 0, lines: [], logPtr: 0, sig: '' };
const DEMO_START = STEPS.map((s, i) => STEPS.slice(0, i).reduce((a, b) => a + b.demo, 0));
function simModel(t) {
  const steps = []; let elapsed = 0, idx = -1, failed = false, done = true;
  const failIdx = SIM.fail ? STEP_BY[FAIL_AT.k] : -1;
  let cursor = 0;
  for (let i = 0; i < STEPS.length; i++) {
    const s = STEPS[i];
    if (failed && s.k !== 'records') { steps.push({ k: s.k, status: 'skipped', p: 0, el: 0 }); continue; }
    if (i < SIM.from) { steps.push({ k: s.k, status: 'ok', p: 1, el: s.real }); elapsed += s.real; continue; }
    const stop = i === failIdx ? FAIL_AT.p : 1;
    const span = s.demo * stop;
    const local = t - cursor;
    cursor += span;
    if (local <= 0) { steps.push({ k: s.k, status: 'pending', p: 0, el: 0 }); done = false; continue; }
    if (local < span) { const p = local / s.demo; steps.push({ k: s.k, status: 'running', p, el: p * s.real }); elapsed += p * s.real; idx = i; done = false; continue; }
    if (i === failIdx) { steps.push({ k: s.k, status: 'failed', p: stop, el: stop * s.real }); elapsed += stop * s.real; failed = true; continue; }
    steps.push({ k: s.k, status: 'ok', p: 1, el: s.real }); elapsed += s.real;
  }
  if (idx === -1 && !done) { const f = steps.findIndex(x => x.status === 'pending'); if (f >= 0) { steps[f] = { k: steps[f].k, status: 'running', p: 0, el: 0 }; idx = f; } }
  const remaining = steps.reduce((a, st, i) => a + (st.status === 'pending' ? STEPS[i].real : st.status === 'running' ? (1 - st.p) * STEPS[i].real : 0), 0);
  return { steps, idx, done, failed, elapsed, remaining, live: true };
}
function finalModel(kind) { // 'w4' | 'ok' | 'failed' | 'idle'
  if (kind === 'w4') {
    const steps = D.w4_steps.map(s => ({ k: s.name, status: 'ok', p: 1, el: s.seconds }));
    steps.push({ k: 'records', status: 'none', p: 0, el: 0 });
    return { steps, idx: -1, done: true, failed: false, elapsed: steps.reduce((a, b) => a + b.el, 0), remaining: 0, real: true };
  }
  if (kind === 'idle') return { steps: STEPS.map(s => ({ k: s.k, status: 'pending', p: 0, el: 0 })), idx: -1, done: false, failed: false, elapsed: 0, remaining: STEPS.reduce((a, s) => a + s.real, 0), idle: true };
  SIM.fail = kind === 'failed'; SIM.from = 0;
  const m = simModel(1e9);
  return m;
}
function logEvents() {
  const out = [];
  STEPS.forEach((s, i) => {
    if (i < SIM.from) return;
    const failHere = SIM.fail && s.k === FAIL_AT.k;
    s.logs.forEach(([f, text, cls]) => {
      if (failHere && f > FAIL_AT.p) return;
      out.push({ i, f, text, cls });
    });
    if (failHere) {
      out.push({ i, f: FAIL_AT.p, text: '✕ ' + FAIL_AT.msg, cls: 'err' });
      out.push({ i, f: FAIL_AT.p, text: '  step failed → digest skipped; run records still written', cls: 'warn' });
    }
  });
  return out;
}
let EVENTS = [];
function startRun(opts = {}) {
  cancelAnimationFrame(SIM.raf);
  SIM.fail = !!opts.fail; SIM.from = opts.from ? STEP_BY[opts.from] : 0;
  SIM.t = 0; SIM.speed = SIM.speed || 1; SIM.lines = []; SIM.logPtr = 0; SIM.sig = '';
  EVENTS = logEvents();
  const pre = SIM.from ? `$ nfl weekly run --season 2026 --week 5 --from-step ${STEPS[SIM.from].k}` : '$ nfl weekly run --auto --expect-week 5';
  SIM.lines.push({ ts: clockStr(0), text: pre, cls: 'acc' });
  SIM.lines.push({ ts: clockStr(0), text: 'lock taken (runs/.weekly.lock) · calendar: 2026 week 5, deadline Thu 2026-10-08 20:15 ET (58.2 h)', cls: 'dim' });
  S.w5 = 'running'; S.page = 'week'; S.week = 5; S.tab = 'pipeline';
  syncMockbar(); render();
  SIM.last = performance.now();
  SIM.raf = requestAnimationFrame(loop);
}
function loop(ts) {
  const dt = Math.min(0.1, (ts - SIM.last) / 1000); SIM.last = ts;
  SIM.t += dt * SIM.speed;
  const m = simModel(SIM.t);
  // emit logs up to now
  while (SIM.logPtr < EVENTS.length) {
    const e = EVENTS[SIM.logPtr];
    const st = m.steps[e.i];
    const reached = st.status === 'ok' || st.status === 'failed' || (st.status === 'running' && st.p >= e.f) || (st.status === 'skipped');
    if (!reached) break;
    const elBefore = m.steps.slice(0, e.i).reduce((a, b) => a + b.el, 0) + STEPS[e.i].real * Math.min(e.f, st.p || 1);
    SIM.lines.push({ ts: clockStr(elBefore), text: e.text, cls: e.cls || '' });
    SIM.logPtr++;
    if (onLivePipe()) appendConsoleLine(SIM.lines[SIM.lines.length - 1]);
  }
  if (onLivePipe()) updatePipeline(m);
  if (m.done) {
    S.w5 = m.failed ? 'failed' : 'published';
    SIM.lines.push({ ts: clockStr(m.elapsed), text: m.failed ? 'failed: 2026 week 05 (exit 1). Fix it, then resume with `nfl weekly run --season 2026 --week 5 --from-step player`' : 'ok: 2026 week 05 published (exit 0) · 57.8 h before the deadline', cls: m.failed ? 'err' : 'ok' });
    syncMockbar(); render();
    toast(m.failed ? 'Week 5 failed at the player step' : 'Week 5 published', m.failed ? 'The run stopped and wrote its records. Use "Resume from player" once it\'s fixed.' : 'Checks passed first time · 57.8 h before Thursday\'s kickoff. The app also sends a browser notification.', m.failed);
    return;
  }
  SIM.raf = requestAnimationFrame(loop);
}
const onLivePipe = () => S.page === 'week' && S.week === 5 && S.tab === 'pipeline';
function currentModel(week) {
  if (week === 4) return finalModel('w4');
  if (S.w5 === 'running') return simModel(SIM.t);
  if (S.w5 === 'published') return finalModel('ok');
  if (S.w5 === 'failed') return finalModel('failed');
  return finalModel('idle');
}

/* ------------------------------------------------------------------ mockbar */
function applyTheme() {
  document.documentElement.setAttribute('data-pal', S.pal);
  document.documentElement.setAttribute('data-mode', S.mode === 'system' ? (systemDark() ? 'dark' : 'light') : S.mode);
}
function syncMockbar() {
  $$('#statePick button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === S.w5)));
  applyTheme();
}
try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (S.mode === 'system') applyTheme(); }); } catch (e) { /* old browsers */ }
$('#statePick').addEventListener('click', e => {
  S.settings = false;
  const b = e.target.closest('[data-v]'); if (!b) return;
  const v = b.dataset.v;
  if (v === 'running') { startRun({}); return; }
  cancelAnimationFrame(SIM.raf);
  S.w5 = v; S.page = 'week'; S.week = 5; S.tab = 'pipeline';
  if (v === 'failed' || v === 'published') { SIM.fail = v === 'failed'; SIM.from = 0; buildFinalLog(); }
  syncMockbar(); render();
});
function buildFinalLog() {
  EVENTS = logEvents(); SIM.lines = [{ ts: clockStr(0), text: '$ nfl weekly run --auto --expect-week 5', cls: 'acc' }];
  const m = finalModel(SIM.fail ? 'failed' : 'ok');
  EVENTS.forEach(e => {
    const st = m.steps[e.i];
    const el = m.steps.slice(0, e.i).reduce((a, b) => a + b.el, 0) + STEPS[e.i].real * Math.min(e.f, st.p || 1);
    SIM.lines.push({ ts: clockStr(el), text: e.text, cls: e.cls || '' });
  });
  SIM.lines.push({ ts: clockStr(m.elapsed), text: SIM.fail ? 'failed: 2026 week 05 (exit 1)' : 'ok: 2026 week 05 published (exit 0)', cls: SIM.fail ? 'err' : 'ok' });
}

/* ------------------------------------------------------------------ shell */
const W5_BADGE = { notready: ['ghost', 'Waiting on week 4'], ready: ['run', 'Ready to run'], running: ['run', 'Running'], failed: ['err', 'Failed'], published: ['ok', 'Published'] };
function renderSide() {
  const b = W5_BADGE[S.w5];
  const cur = (page, week) => String(S.page === page && (week == null || S.week === week));
  $('#side').innerHTML = `
    <div class="brand"><div class="brand-mark">CR</div><div style="flex:1;min-width:0"><b>Control Room</b><small>NFL Analytics Engine</small></div>
      <button class="iconbtn" id="gearBtn" aria-expanded="${S.settings}" aria-controls="settingsPop" title="Appearance"><svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="2.2"/><path d="M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M3.4 12.6l1.3-1.3M11.3 4.7l1.3-1.3"/></svg></button>
      ${S.settings ? settingsPop() : ''}</div>
    <div class="navgrp"><div class="eyebrow">2026 weeks</div>
      <button class="navi" data-nav="week" data-week="5" aria-current="${cur('week', 5)}"><span class="wk">5</span><span class="grow">Week 5<span class="sub">This week · Thu Oct 8</span></span><span class="chip ${b[0]}">${S.w5 === 'running' ? '<span class="dot pulse"></span>' : ''}${esc(b[1])}</span></button>
      <button class="navi" data-nav="week" data-week="4" aria-current="${cur('week', 4)}"><span class="wk">4</span><span class="grow">Week 4<span class="sub">Checks passed</span></span><span class="chip ok"><span class="ic">✓</span>Published</span></button>
      <button class="navi" disabled title="Live weekly runs started in week 4"><span class="wk">1–3</span><span class="grow">Before go-live<span class="sub">Walk-forward rows only</span></span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">Season</div>
      <button class="navi" data-nav="season" aria-current="${cur('season')}">${ICON.season}<span class="grow">Scorecard</span></button>
      <button class="navi" data-nav="teams" aria-current="${cur('teams')}">${ICON.teams}<span class="grow">Teams &amp; rankings</span></button>
      <button class="navi" data-nav="models" aria-current="${cur('models')}">${ICON.models}<span class="grow">Models</span></button>
      <button class="navi" data-nav="alerts" aria-current="${cur('alerts')}">${ICON.alerts}<span class="grow">Alerts</span><span class="chip flat">0</span></button>
    </div>
    <div class="navgrp"><div class="eyebrow">System</div>
      <button class="navi" data-nav="health" aria-current="${cur('health')}">${ICON.health}<span class="grow">Health</span><span class="chip ok"><span class="ic">✓</span>OK</span></button>
    </div>
    <div class="side-foot">
      <div><span>Run lock</span><span>${S.w5 === 'running' ? 'held by week 5' : 'free'}</span></div>
      <div><span>Neo4j</span><span>up · 5.26</span></div>
      <div><span>Data drive</span><span>D:\\nfl-ml-data</span></div>
      <div><span>Mock clock</span><span>Tue Oct 6 · 10:00 ET</span></div>
    </div>`;
}
function settingsPop() {
  return `<div class="popover" id="settingsPop" role="dialog" aria-label="Appearance">
    <span class="eyebrow">Theme</span>
    <div class="themes">${PALS.map(p => `<button type="button" class="themecard" data-pal="${p.id}" aria-pressed="${p.id === S.pal}"><i>${p.sw.map(c => `<b style="background:${c}"></b>`).join('')}</i><span><b>${esc(p.name)}</b><small>${esc(p.note)}</small></span></button>`).join('')}</div>
    <span class="eyebrow">Mode</span>
    <span class="seg">${['system', 'dark', 'light'].map(m => `<button type="button" data-mode="${m}" aria-pressed="${S.mode === m}">${m[0].toUpperCase() + m.slice(1)}</button>`).join('')}</span>
    <span class="muted" style="font-size:11.5px">Saved in this browser. Each week's Pipeline tab has its own view picker.</span>
  </div>`;
}
$('#side').addEventListener('click', e => {
  if (e.target.closest('#gearBtn')) { S.settings = !S.settings; renderSide(); return; }
  const pb = e.target.closest('.themecard[data-pal]'); if (pb) { S.pal = pb.dataset.pal; store.set('pal', S.pal); applyTheme(); renderSide(); return; }
  const mb = e.target.closest('#settingsPop [data-mode]'); if (mb) { S.mode = mb.dataset.mode; store.set('mode', S.mode); applyTheme(); renderSide(); return; }
  if (e.target.closest('#settingsPop')) return;
  const b = e.target.closest('[data-nav]'); if (!b) return;
  S.settings = false;
  S.page = b.dataset.nav;
  if (S.page === 'week') { const w = Number(b.dataset.week); if (w !== S.week) S.tab = 'pipeline'; S.week = w; }
  SIM.sig = ''; render(); window.scrollTo({ top: 0 });
});

function renderTop() {
  const top = $('#top');
  if (S.page === 'week' && S.week === 5) {
    const first = D.w5_slate[0];
    const st = W5_BADGE[S.w5];
    top.innerHTML = `
      <div class="titleblock"><span class="eyebrow">2026 regular season · this week</span><h1>Week 5</h1>
        <div class="chips"><span class="chip ${st[0]}">${S.w5 === 'running' ? '<span class="dot pulse"></span>' : ''}${esc(st[1])}</span><span class="chip flat">15 games · Thu–Mon</span><span class="chip flat">Thursday opener</span><span class="chip flat">Eagles at Jaguars in London</span><span class="chip flat">Byes: CAR, KC</span></div></div>
      <div class="right">
        <div class="clock"><span class="small">First kickoff · ${tchip(first.away)} at ${tchip(first.home)}</span><span class="big">58 h 15 m</span><span class="small">Thu Oct 8, 8:15 PM ET · retry window ends Wed 6 PM</span></div>
        <div style="display:flex;flex-direction:column;gap:4px;align-items:flex-start">
          <button class="btn sm" aria-disabled="true" title="Runs on Saturday, after this week's Tuesday run">Saturday injury update</button>
          <span class="reason">${S.w5 === 'published' ? 'Opens Sat Oct 10 (first real update)' : "Needs this week's Tuesday run first"}</span>
        </div>
      </div>`;
  } else if (S.page === 'week') {
    top.innerHTML = `
      <div class="titleblock"><span class="eyebrow">2026 regular season</span><h1>Week 4</h1>
        <div class="chips"><span class="chip ok"><span class="ic">✓</span>Published</span><span class="chip warn"><span class="ic">!</span>Late run · first live week</span><span class="chip flat">16 games</span><span class="chip flat">Colts at Commanders in London</span><span class="chip flat">Morning kickoff</span></div></div>
      <div class="right">
        <div class="clock"><span class="small">Published</span><span class="big">Sun 5:02 AM</span><span class="small">Oct 4 ET · Thursday's game kept its earlier pick</span></div>
        <a class="btn sm" href="${WANDB}" target="_blank" rel="noopener">Open week 4 in W&amp;B ↗</a>
      </div>`;
  } else {
    const T = { season: ['2026 season', 'Scorecard', 'How the models are doing, week by week'], teams: ['2026 season', 'Teams & rankings', 'Elo and EPA ratings as of the week-4 run'], models: ['Models', 'What runs in production', 'Versions, backtests and model cards'], alerts: ['Operations', 'Alerts', 'Drift signals, failed steps and stale data'], health: ['System', 'Health', 'Services, keys and the data drive'] }[S.page];
    top.innerHTML = `<div class="titleblock"><span class="eyebrow">${esc(T[0])}</span><h1>${esc(T[1])}</h1><span class="muted">${esc(T[2])}</span></div>
      <div class="right">${S.page === 'season' ? `<a class="btn sm" href="${DASHBOARD}" target="_blank" rel="noopener">2026 Season Dashboard in W&amp;B ↗</a>` : ''}</div>`;
  }
}

const TABS = [['pipeline', 'Pipeline'], ['digest', 'Digest'], ['games', 'Games'], ['players', 'Players'], ['results', 'Results'], ['mlops', 'MLOps'], ['graph', 'Graph']];
function renderTabs() {
  const nav = $('#tabs');
  if (S.page !== 'week') { nav.hidden = true; return; }
  nav.hidden = false;
  const counts = S.week === 4 ? { games: 16, players: '1,860', graph: 3 } : { games: 15 };
  nav.innerHTML = TABS.map(([k, l]) => `<button class="tab" role="tab" data-tab="${k}" aria-selected="${S.tab === k}">${l}${counts[k] ? `<span class="count">${counts[k]}</span>` : ''}${k === 'pipeline' && S.week === 5 && S.w5 === 'running' ? '<span class="dot pulse" style="color:var(--accent)"></span>' : ''}</button>`).join('');
}
$('#tabs').addEventListener('click', e => { const b = e.target.closest('[data-tab]'); if (!b) return; S.tab = b.dataset.tab; SIM.sig = ''; render(); });

function render() {
  renderSide(); renderTop(); renderTabs();
  const v = $('#view');
  let html = '';
  if (S.page === 'week') html = S.week === 5 ? week5View() : week4View();
  else html = ({ season: seasonView, teams: teamsView, models: modelsView, alerts: alertsView, health: healthView })[S.page]();
  v.innerHTML = html;
  wireCharts();
  const pipe = $('#pipe');
  if (pipe) { SIM.sig = ''; updatePipeline(currentModel(S.week)); scrollConsole(); }
}

/* ------------------------------------------------------------------ pipeline region */
function pipelineBlock(week) {
  const m = currentModel(week);
  const live = week === 5 && S.w5 === 'running';
  const viz = vizFor(week);
  const title = VIEWS.find(v => v[0] === viz)[1];
  const lines = week === 4 ? w4Log() : (S.w5 === 'notready' || S.w5 === 'ready') ? idleLog() : SIM.lines;
  return `
    ${(live || (week === 5 && (S.w5 === 'published' || S.w5 === 'failed'))) ? nowbar(m, week) : ''}
    <div class="card">
      <div class="card-h"><h2>${week === 5 && (S.w5 === 'ready' || S.w5 === 'notready') ? 'The plan for this run' : 'The run'}</h2><span class="muted">${title} · ${week === 4 ? 'real step times from weekly_run.json' : live ? 'live' : S.w5 === 'ready' || S.w5 === 'notready' ? 'expected times' : 'mock run'}</span>
        <div class="right"><span class="seg" id="vizSeg" aria-label="Pipeline view for week ${week}">${VIEWS.map(([k, l]) => `<button type="button" data-viz="${k}" aria-pressed="${viz === k}">${l}</button>`).join('')}<button type="button" data-viz="surprise" title="Pick a different view at random for this week">Surprise me</button></span>${S.surprised[week] ? '<span class="chip run">surprise pick</span>' : ''}${live ? `<span class="seg" id="speedPick"><button type="button" data-sp="1" aria-pressed="${SIM.speed === 1}">1×</button><button type="button" data-sp="4" aria-pressed="${SIM.speed === 4}">4×</button></span><button class="btn sm" id="skipBtn">Skip to end</button>` : ''}</div></div>
      <div class="card-b"><div id="pipe"></div></div>
    </div>
    <div class="card">
      <div class="card-h"><h2>Log</h2><span class="muted">${week === 4 ? 'rebuilt from the step details' : 'streamed from the run'} · secrets scrubbed</span></div>
      <div class="card-b"><div class="console" id="console" aria-live="polite">${lines.map(consoleLine).join('')}${live ? '<span class="cursor"></span>' : ''}</div></div>
    </div>`;
}
function consoleLine(l) { return `<div class="ln"><span class="ts">${esc(l.ts)}</span><span class="${esc(l.cls || '')}">${esc(l.text)}</span></div>`; }
function appendConsoleLine(l) {
  const c = $('#console'); if (!c) return;
  const cur = c.querySelector('.cursor');
  const div = document.createElement('div'); div.innerHTML = consoleLine(l);
  c.insertBefore(div.firstChild, cur || null);
  scrollConsole();
}
function scrollConsole() { const c = $('#console'); if (c) c.scrollTop = c.scrollHeight; }
function idleLog() {
  const notready = S.w5 === 'notready';
  return [
    { ts: '10:00:00', text: '$ nfl weekly run --auto --dry-run', cls: 'acc' },
    { ts: '10:00:01', text: 'Tue 2026-10-06 10:00 ET: 2026 week 5 (regular)' },
    { ts: '10:00:01', text: notready ? 'previous week 4: 15/16 final, NOT complete (ATL@NO not in play-by-play yet)' : 'previous week 4: 16/16 final, complete', cls: notready ? 'warn' : 'ok' },
    { ts: '10:00:01', text: 'deadline (first kickoff): Thu 2026-10-08 20:15 ET, 58.2 h from now' },
    { ts: '10:00:01', text: "retry window for 'not ready' ends Wed 2026-10-07 18:00 ET", cls: 'dim' },
    { ts: '10:00:01', text: 'slate: 15 games on thu, sun, mon; special: thursday, morning_kickoff, byes' },
    { ts: '10:00:01', text: 'byes: CAR, KC · lock: free', cls: 'dim' },
    { ts: '10:00:01', text: notready ? 'dry run: would run … for 2026 week 05; week 4 isn\'t final yet (exit 3)' : 'dry run: would run ingest, ready, curate, ratings, game, graph, player, digest for 2026 week 05', cls: notready ? 'warn' : 'ok' },
  ];
}
function w4Log() {
  const out = [{ ts: '03:18:17', text: '$ nfl weekly run --season 2026 --week 4 --promote', cls: 'acc' }];
  D.w4_steps.forEach(s => {
    const t = s.started.slice(11, 19);
    const det = s.detail.split(';').map(x => x.replace(/D:\\nfl-ml-data\\/g, '').replace(/https:\/\/wandb\.ai\/\S+\/runs\//, 'W&B ').trim()).join(' · ');
    out.push({ ts: t, text: `${s.name}: ${det}`, cls: 'ok' });
    if (s.name === 'graph') out.push({ ts: '03:59:25', text: '$ nfl weekly run --season 2026 --week 4 --from-step player --promote   (resumed with the review fixes)', cls: 'acc' });
    if (s.name === 'player') out.push({ ts: '04:23:24', text: '$ nfl weekly run --season 2026 --week 4 --from-step digest   (driver wording from the fact-check)', cls: 'acc' });
  });
  out.push({ ts: '05:02:13', text: 'published reports/2026/week04-digest.md · checks passed after one regeneration', cls: 'ok' });
  return out;
}
function nowbar(m, week) {
  const total = m.elapsed + m.remaining;
  const i = m.idx >= 0 ? m.idx : -1;
  let name, sub;
  if (m.failed && m.done) { name = 'Failed · player'; sub = FAIL_AT.msg; }
  else if (m.done) { name = 'Published'; sub = 'Digest written, checks passed first time, records and dashboard updated (mock run)'; }
  else { name = STEPS[i].name; sub = STEPS[i].sub(m.steps[i].p); }
  return `<div class="card"><div class="nowbar">
    <div><span class="eyebrow">${m.done ? (m.failed ? 'Stopped' : 'Final') : `Step ${i + 1} of ${STEPS.length}`}</span><div class="stepname" id="nowName" style="${m.failed && m.done ? 'color:var(--err)' : ''}">${esc(name)}</div></div>
    <div style="display:flex;flex-direction:column;gap:8px;min-width:0"><span class="sub" id="nowSub">${esc(sub)}</span><div class="progress"><b id="nowProg" style="width:${(m.elapsed / total * 100).toFixed(1)}%;${m.failed ? 'background:var(--err)' : ''}"></b></div></div>
    <div class="stat"><div class="big" id="nowElapsed">${dur(m.elapsed)}</div><div class="small" id="nowEta">${m.done ? (m.failed ? 'exit 1 · records written' : '57.8 h before kickoff') : `about ${dur(m.remaining)} left`}</div></div>
  </div></div>`;
}

function updatePipeline(m) {
  const pipe = $('#pipe'); if (!pipe) return;
  const viz = vizFor(S.week);
  const sig = viz + '|' + m.steps.map(s => s.status).join(',');
  if (sig !== SIM.sig) {
    SIM.sig = sig;
    pipe.innerHTML = viz === 'drive' ? driveHTML(m) : viz === 'graph' ? mapHTML(m) : timelineHTML(m);
  }
  if (viz === 'drive') driveTick(m); else if (viz === 'graph') mapTick(m); else timelineTick(m);
  // now bar
  const nn = $('#nowName');
  if (nn && m.live && !m.done && m.idx >= 0) {
    nn.textContent = STEPS[m.idx].name;
    $('#nowSub').textContent = STEPS[m.idx].sub(m.steps[m.idx].p);
    const total = m.elapsed + m.remaining;
    $('#nowProg').style.width = (m.elapsed / total * 100).toFixed(1) + '%';
    $('#nowElapsed').textContent = dur(m.elapsed);
    $('#nowEta').textContent = `about ${dur(m.remaining)} left`;
    const eb = nn.parentElement.querySelector('.eyebrow'); if (eb) eb.textContent = `Step ${m.idx + 1} of ${STEPS.length}`;
  }
}
document.addEventListener('click', e => {
  const vz = e.target.closest('[data-viz]');
  if (vz) {
    let v = vz.dataset.viz;
    if (v === 'surprise') { const others = VIEWS.map(x => x[0]).filter(x => x !== vizFor(S.week)); v = others[Math.floor(Math.random() * others.length)]; S.surprised[S.week] = true; }
    else S.surprised[S.week] = false;
    setViz(S.week, v); SIM.sig = ''; render(); return;
  }
  const sp = e.target.closest('[data-sp]');
  if (sp) { SIM.speed = Number(sp.dataset.sp); $$('#speedPick button').forEach(b => b.setAttribute('aria-pressed', String(b === sp))); }
  if (e.target.closest('#skipBtn')) { SIM.t = 1e9; }
});

function stepDetail(m, i, week) {
  const st = m.steps[i], s = STEPS[i];
  if (week === 4) return W4_DETAIL[s.k];
  if (st.status === 'ok') return s.done5;
  if (st.status === 'running') return s.sub(st.p);
  if (st.status === 'failed') return FAIL_AT.msg;
  if (st.status === 'skipped') return 'skipped (an earlier step failed)';
  return `expected ~${dur(s.real)}`;
}

/* ---------- view 1: drive chart ---------- */
const FX0 = 40, FW = 920, FY0 = 54, FH = 226;
const PX = FW / 120;
const X = yd => FX0 + 10 * PX + yd * PX;
const posLabel = yd => yd === 50 ? '50' : yd < 50 ? `OWN ${yd}` : `OPP ${100 - yd}`;
function ballYd(m) {
  let yd = 20;
  m.steps.forEach((st, i) => {
    const s = STEPS[i];
    if (s.k === 'records') return;
    const from = i === 0 ? 20 : STEPS[i - 1].yd;
    if (st.status === 'ok') yd = s.yd;
    else if (st.status === 'running' || st.status === 'failed') yd = from + (s.yd - from) * Math.min(1, st.p);
  });
  return yd;
}
function driveHTML(m) {
  const week = S.week;
  let field = `<rect x="${FX0}" y="${FY0}" width="${FW}" height="${FH}" style="fill:var(--field)" rx="6"/>`;
  for (let yd = 0; yd < 100; yd += 10) if ((yd / 10) % 2 === 0) field += `<rect x="${X(yd)}" y="${FY0}" width="${10 * PX}" height="${FH}" style="fill:var(--field-2)"/>`;
  const done = m.done && !m.failed;
  field += `<rect x="${FX0}" y="${FY0}" width="${10 * PX}" height="${FH}" style="fill:var(--field-2)"/>`;
  field += `<rect x="${X(100)}" y="${FY0}" width="${10 * PX}" height="${FH}" style="fill:${done ? 'var(--accent)' : 'var(--field-2)'};opacity:${done ? 0.85 : 1}"/>`;
  for (let yd = 0; yd <= 100; yd += 5) field += `<line x1="${X(yd)}" x2="${X(yd)}" y1="${FY0}" y2="${FY0 + FH}" style="stroke:var(--chalk)" stroke-width="${yd % 10 === 0 ? 1.6 : 0.8}" opacity="${yd % 10 === 0 ? 0.85 : 0.45}"/>`;
  for (let yd = 1; yd < 100; yd++) if (yd % 5) field += `<line x1="${X(yd)}" x2="${X(yd)}" y1="${FY0 + 88}" y2="${FY0 + 95}" style="stroke:var(--chalk)" opacity=".5"/><line x1="${X(yd)}" x2="${X(yd)}" y1="${FY0 + FH - 95}" y2="${FY0 + FH - 88}" style="stroke:var(--chalk)" opacity=".5"/>`;
  [10, 20, 30, 40, 50, 60, 70, 80, 90].forEach(yd => {
    const n = yd <= 50 ? yd : 100 - yd;
    field += `<text class="field-num" x="${X(yd)}" y="${FY0 + FH - 22}" text-anchor="middle" font-size="24" style="fill:var(--chalk)">${n}</text>`;
    field += `<text class="field-num" x="${X(yd)}" y="${FY0 + 40}" text-anchor="middle" font-size="24" style="fill:var(--chalk)" transform="rotate(180 ${X(yd)} ${FY0 + 32})">${n}</text>`;
  });
  field += `<text class="field-num" x="${FX0 + 5 * PX}" y="${FY0 + FH / 2}" text-anchor="middle" font-size="22" letter-spacing="3" style="fill:var(--chalk)" transform="rotate(-90 ${FX0 + 5 * PX} ${FY0 + FH / 2})">DATA</text>`;
  field += `<text class="field-num" x="${X(105)}" y="${FY0 + FH / 2}" text-anchor="middle" font-size="22" letter-spacing="3" style="fill:${done ? 'var(--on-accent)' : 'var(--chalk)'}" transform="rotate(90 ${X(105)} ${FY0 + FH / 2})">DIGEST</text>`;
  // goal posts (records = the extra point)
  const rec = m.steps[STEP_BY.records];
  const gpC = rec.status === 'ok' ? 'var(--ok)' : rec.status === 'running' ? 'var(--accent)' : rec.status === 'none' ? 'var(--line-2)' : '#E8C547';
  field += `<g style="stroke:${gpC}" stroke-width="3" fill="none" stroke-linecap="round"><path d="M${X(105)} ${FY0 - 4} V${FY0 - 16} M${X(105) - 16} ${FY0 - 16} H${X(105) + 16} M${X(105) - 16} ${FY0 - 16} V${FY0 - 44} M${X(105) + 16} ${FY0 - 16} V${FY0 - 44}"/></g>`;
  field += `<text x="${X(105) - 24}" y="${FY0 - 26}" text-anchor="end" font-size="10.5" font-weight="700" letter-spacing=".06em" style="fill:var(--ink-3)">PAT · RECORDS</text>`;
  // step labels above the field
  let labels = '';
  STEPS.forEach((s, i) => {
    if (s.k === 'records') return;
    const st = m.steps[i];
    const row = i % 2 === 0 ? FY0 - 30 : FY0 - 12;
    const col = st.status === 'ok' ? 'var(--ink)' : st.status === 'running' ? 'var(--accent-ink)' : st.status === 'failed' ? 'var(--err)' : 'var(--ink-3)';
    labels += `<line x1="${X(s.yd)}" x2="${X(s.yd)}" y1="${row + 4}" y2="${FY0}" style="stroke:var(--line-2)" stroke-dasharray="2 2"/>`;
    labels += `<text x="${X(s.yd) - 3}" y="${row}" text-anchor="end" font-size="10.5" font-weight="700" letter-spacing=".05em" style="fill:${col}">${esc(s.name.toUpperCase())}</text>`;
  });
  // completed play arcs
  let arcs = '';
  const midY = FY0 + FH / 2;
  m.steps.forEach((st, i) => {
    const s = STEPS[i]; if (s.k === 'records') return;
    const from = i === 0 ? 20 : STEPS[i - 1].yd;
    if (st.status === 'ok') {
      const x1 = X(from), x2 = X(s.yd), h = Math.max(16, (x2 - x1) * 0.5);
      arcs += `<path d="M${x1} ${midY} Q${(x1 + x2) / 2} ${midY - h} ${x2} ${midY}" fill="none" style="stroke:var(--chalk)" stroke-width="2.2" stroke-linecap="round"/><circle cx="${x2}" cy="${midY}" r="3.5" style="fill:var(--chalk)"/>`;
    }
    if (st.status === 'failed') {
      const x = X(from + (s.yd - from) * st.p);
      arcs += `<g style="stroke:var(--err)" stroke-width="3" stroke-linecap="round"><path d="M${x - 9} ${midY + 22} l18 18 M${x + 9} ${midY + 22} l-18 18"/></g><text x="${x}" y="${midY + 58}" text-anchor="middle" font-size="12" font-weight="800" letter-spacing=".1em" style="fill:var(--err)">FUMBLE</text>`;
    }
  });
  arcs += `<path id="runArc" d="" fill="none" style="stroke:var(--accent)" stroke-width="2.4" stroke-dasharray="6 5"/>`;
  const lines = m.done ? '' : `<line id="los" x1="0" x2="0" y1="${FY0}" y2="${FY0 + FH}" stroke="#2D7FF9" stroke-width="3" opacity=".95"/><line id="fdl" x1="0" x2="0" y1="${FY0}" y2="${FY0 + FH}" stroke="#F7D417" stroke-width="3" opacity=".95"/>`;
  const ball = `<g id="ball"><ellipse cx="0" cy="0" rx="13" ry="8" style="fill:var(--ball)" stroke="rgba(0,0,0,.35)"/><path d="M-6 0 H6 M-3 -2.5 V2.5 M0 -2.5 V2.5 M3 -2.5 V2.5" stroke="#fff" stroke-width="1.3" stroke-linecap="round"/></g>`;
  const td = done ? `<text x="${X(50)}" y="${midY + 8}" text-anchor="middle" class="field-num" font-size="40" letter-spacing="6" style="fill:var(--chalk)" opacity=".9">TOUCHDOWN</text>` : '';
  // drive log
  const rows = STEPS.map((s, i) => {
    const st = m.steps[i];
    const from = i === 0 ? 20 : STEPS[i - 1].yd;
    const dd = s.k === 'records' ? 'PAT' : `1st & ${s.yd === 100 ? 'Goal' : s.yd - from} at ${posLabel(from)}`;
    const cls = st.status === 'running' ? 'running' : st.status === 'failed' ? 'failed' : (st.status === 'pending' || st.status === 'skipped' || st.status === 'none') ? 'pending' : '';
    const t = st.status === 'pending' ? '—' : st.status === 'none' ? '—' : st.status === 'skipped' ? 'skipped' : dur(st.el);
    return `<li class="${cls}" data-row="${s.k}"><span class="dd">${dd}</span><span class="pl">${st.status === 'ok' ? '✓ ' : st.status === 'failed' ? '✕ ' : st.status === 'running' ? '● ' : ''}${esc(s.name)}</span><span class="dt" id="dl-${s.k}">${esc(stepDetail(m, i, week))}</span><span class="t" id="dlt-${s.k}">${t}</span></li>`;
  }).join('');
  const plays = m.steps.filter((s, i) => s.status === 'ok' && STEPS[i].k !== 'records').length;
  return `
    <div class="scorebug" style="margin-bottom:12px">
      <div><span class="k">2026</span><span class="v">WK ${week}</span></div>
      <div><span class="k">Drive</span><span class="v" id="sbPlays">${plays} plays · ${Math.round(ballYd(m) - 20)} yds</span></div>
      <div class="now"><span class="k">${m.done ? (m.failed ? 'Turnover' : 'Final') : m.idle ? 'Waiting' : 'Now'}</span><span class="v" id="sbNow">${esc(m.done ? (m.failed ? 'Fumble at the player step' : week === 4 ? 'Touchdown · digest published' : 'Touchdown · digest published') : m.idle ? (S.w5 === 'notready' ? 'Waiting on week 4' : 'Kickoff when you press Run') : STEPS[m.idx].name)}</span></div>
      <div><span class="k">Clock</span><span class="v" id="sbClock">${dur(m.elapsed)}</span></div>
    </div>
    <div class="vizwrap"><svg viewBox="0 0 1000 ${FY0 + FH + 14}" role="img" aria-label="The weekly run drawn as a football drive: each step is a play, the digest is the touchdown">${labels}${field}${arcs}${td}${lines}${ball}</svg></div>
    <div class="legend" style="margin:10px 0 6px"><span><i class="line" style="background:#2D7FF9"></i>Line of scrimmage: where the run is</span><span><i class="line" style="background:#F7D417"></i>First-down line: where this step ends</span><span><i class="line" style="background:var(--ink-2)"></i>Arcs: finished steps</span></div>
    <ul class="drivelog">${rows}</ul>`;
}
function driveTick(m) {
  const b = $('#ball'); if (!b) return;
  const yd = ballYd(m);
  const midY = FY0 + FH / 2;
  b.setAttribute('transform', `translate(${X(yd)} ${midY}) rotate(-12)`);
  const los = $('#los'), fdl = $('#fdl');
  if (los) {
    let from = 20, target = STEPS[0].yd;
    if (m.idx >= 0) { from = m.idx === 0 ? 20 : STEPS[m.idx - 1].yd; target = STEPS[m.idx].yd; }
    else { const last = m.steps.map(s => s.status).lastIndexOf('ok'); if (last >= 0 && last < STEPS.length - 1) { from = STEPS[last].yd; target = STEPS[last + 1].yd; } }
    los.setAttribute('x1', X(from)); los.setAttribute('x2', X(from));
    fdl.setAttribute('x1', X(target)); fdl.setAttribute('x2', X(target));
    fdl.style.display = m.done ? 'none' : ''; los.style.display = m.done ? 'none' : '';
  }
  const arc = $('#runArc');
  if (arc) {
    if (m.idx >= 0 && STEPS[m.idx].k !== 'records') {
      const from = m.idx === 0 ? 20 : STEPS[m.idx - 1].yd;
      const x1 = X(from), x2 = X(yd), h = Math.max(10, (x2 - x1) * 0.5);
      arc.setAttribute('d', `M${x1} ${midY} Q${(x1 + x2) / 2} ${midY - h} ${x2} ${midY}`);
    } else arc.setAttribute('d', '');
  }
  if (m.idx >= 0) {
    const k = STEPS[m.idx].k;
    const dl = $('#dl-' + k); if (dl) dl.textContent = STEPS[m.idx].sub(m.steps[m.idx].p);
    const dt = $('#dlt-' + k); if (dt) dt.textContent = dur(m.steps[m.idx].el);
    const sb = $('#sbNow'); if (sb) sb.textContent = `${STEPS[m.idx].name} — ${STEPS[m.idx].sub(m.steps[m.idx].p)}`;
  }
  const sc = $('#sbClock'); if (sc) sc.textContent = dur(m.elapsed);
  const sp = $('#sbPlays'); if (sp) sp.textContent = `${m.steps.filter((s, i) => s.status === 'ok' && STEPS[i].k !== 'records').length} plays · ${Math.round(yd - 20)} yds`;
}

/* ---------- view 2: pipeline map ---------- */
const MAP = {
  sources: [95, 112], ingest: [245, 112], ready: [395, 112], curate: [545, 112], ratings: [695, 112], game: [855, 112],
  graph: [855, 286], player: [675, 286], digest: [495, 286], records: [315, 286], published: [125, 286],
};
function mapHTML(m) {
  const C = 2 * Math.PI * 33;
  const edgeCls = k => { const st = m.steps[STEP_BY[k]]; return st.status === 'ok' ? 'done' : st.status === 'running' ? 'active' : ''; };
  const pubOk = m.steps[STEP_BY.digest].status === 'ok';
  let edges = '';
  const seq = ['ingest', 'ready', 'curate', 'ratings', 'game', 'graph', 'player', 'digest', 'records'];
  edges += `<path class="flow-edge ${edgeCls('ingest')}" d="M${MAP.sources[0] + 62} ${MAP.sources[1]} H${MAP.ingest[0] - 36}"/>`;
  for (let i = 1; i < seq.length; i++) {
    const a = MAP[seq[i - 1]], b = MAP[seq[i]];
    if (a[1] !== b[1]) edges += `<path class="flow-edge ${edgeCls(seq[i])}" d="M${a[0]} ${a[1] + 36} V${b[1] - 36}"/>`;
    else edges += `<path class="flow-edge ${edgeCls(seq[i])}" d="M${a[0] + (b[0] > a[0] ? 36 : -36)} ${a[1]} H${b[0] + (b[0] > a[0] ? -36 : 36)}"/>`;
  }
  edges += `<path class="flow-edge ${pubOk && m.steps[STEP_BY.records].status !== 'pending' ? 'done' : ''}" d="M${MAP.records[0] - 36} ${MAP.records[1]} H${MAP.published[0] + 40}"/>`;
  // sources block
  const src = ['nflverse', 'NGS · PFR · FTN', 'ESPN', 'The Odds API'];
  let nodes = `<g>${src.map((t, i) => `<rect x="${MAP.sources[0] - 62}" y="${MAP.sources[1] - 44 + i * 23}" width="124" height="19" rx="9.5" style="fill:var(--raised);stroke:var(--line-2)"/><text x="${MAP.sources[0]}" y="${MAP.sources[1] - 30.5 + i * 23}" text-anchor="middle" font-size="11" style="fill:var(--ink-2)">${t}</text>`).join('')}<text x="${MAP.sources[0]}" y="${MAP.sources[1] + 62}" text-anchor="middle" font-size="12.5" font-weight="700" style="fill:var(--ink)">Sources</text></g>`;
  seq.forEach((k, i) => {
    const [x, y] = MAP[k]; const st = m.steps[STEP_BY[k]]; const s = STEPS[STEP_BY[k]];
    const ring = st.status === 'ok' ? 'var(--ok)' : st.status === 'failed' ? 'var(--err)' : st.status === 'running' ? 'var(--accent)' : 'var(--line-2)';
    const glyph = st.status === 'ok' ? '✓' : st.status === 'failed' ? '!' : st.status === 'none' ? '–' : String(i + 1);
    const glyphFill = st.status === 'ok' ? 'var(--ok)' : st.status === 'failed' ? 'var(--err)' : st.status === 'running' ? 'var(--accent-ink)' : 'var(--ink-3)';
    const top = y < 200;
    const chipY = top ? y - 76 : y + 92;
    const chipW = s.chip.length * 6.6 + 26;
    const showChip = st.status === 'ok';
    nodes += `<g>
      <circle class="node-ring" cx="${x}" cy="${y}" r="33" style="stroke:${st.status === 'ok' || st.status === 'failed' ? ring : 'var(--line-2)'}"/>
      ${st.status === 'running' ? `<circle class="node-prog" id="ring-${k}" cx="${x}" cy="${y}" r="33" stroke-dasharray="${C}" stroke-dashoffset="${C}" transform="rotate(-90 ${x} ${y})"/>` : ''}
      <circle class="node-core" cx="${x}" cy="${y}" r="26"/>
      <text x="${x}" y="${y + 7}" text-anchor="middle" class="field-num" font-size="21" style="fill:${glyphFill}">${glyph}</text>
      <text x="${x}" y="${top ? y + 54 : y + 52}" text-anchor="middle" font-size="12.5" font-weight="700" style="fill:var(--ink)">${esc(s.name)}</text>
      <text id="msub-${k}" x="${x}" y="${top ? y + 69 : y + 67}" text-anchor="middle" font-size="10.5" style="fill:var(--ink-3)">${esc(st.status === 'running' ? '' : st.status === 'ok' ? dur(st.el) : st.status === 'failed' ? 'failed' : st.status === 'skipped' ? 'skipped' : st.status === 'none' ? 'not recorded' : '~' + dur(s.real))}</text>
      <g class="artifact-chip" style="opacity:${showChip ? 1 : 0}">
        <rect x="${x - chipW / 2}" y="${chipY - 12}" width="${chipW}" height="22" rx="5" style="fill:${s.wb ? 'var(--accent-wash)' : 'var(--raised)'};stroke:${s.wb ? 'var(--accent)' : 'var(--line-2)'}"/>
        <text x="${x}" y="${chipY + 3}" text-anchor="middle" font-size="11" font-weight="600" style="fill:var(--ink-2)">${esc(S.week === 4 ? s.chip.replace('2026-w05', '2026-w04') : s.chip)}</text>
      </g>
    </g>`;
  });
  const [px, py] = MAP.published;
  const pubDone = pubOk;
  nodes += `<g><circle cx="${px}" cy="${py}" r="40" style="fill:${pubDone ? 'var(--accent)' : 'var(--raised)'};stroke:${pubDone ? 'var(--accent)' : 'var(--line-2)'}" stroke-width="2"/>
    <text x="${px}" y="${py + 6}" text-anchor="middle" class="field-num" font-size="17" letter-spacing=".04em" style="fill:${pubDone ? 'var(--on-accent)' : 'var(--ink-3)'}">${pubDone ? 'LIVE' : 'DIGEST'}</text>
    <text x="${px}" y="${py + 60}" text-anchor="middle" font-size="12.5" font-weight="700" style="fill:var(--ink)">Published</text>
    <text x="${px}" y="${py + 75}" text-anchor="middle" font-size="10.5" style="fill:var(--ink-3)">${pubDone ? `week${String(S.week).padStart(2, '0')}-digest.md` : 'not yet'}</text></g>`;
  return `<div class="vizwrap"><svg viewBox="0 0 1000 400" role="img" aria-label="Pipeline map: sources flow through each step; files and W&B artifacts appear as each step finishes">${edges}${nodes}</svg></div>
    <div class="legend" style="margin-top:8px"><span><i style="background:var(--ok)"></i>Finished</span><span><i style="background:var(--accent)"></i>Running (the moving line is data flowing in)</span><span><i style="background:var(--line-2)"></i>Waiting</span><span><i style="background:var(--accent-wash);box-shadow:inset 0 0 0 1px var(--accent);border-radius:3px"></i>W&amp;B artifact or run</span></div>`;
}
function mapTick(m) {
  if (m.idx < 0) return;
  const k = STEPS[m.idx].k, st = m.steps[m.idx];
  const C = 2 * Math.PI * 33;
  const r = $('#ring-' + k); if (r) r.setAttribute('stroke-dashoffset', String(C * (1 - st.p)));
  const sub = $('#msub-' + k); if (sub) { const t = STEPS[m.idx].sub(st.p); sub.textContent = t.length > 34 ? t.slice(0, 33) + '…' : t; }
}

/* ---------- view 3: timeline ---------- */
function timelineHTML(m) {
  const total = Math.max(m.elapsed + m.remaining, 1);
  const step = total > 1800 ? 600 : total > 900 ? 180 : 120;
  let ticks = ''; for (let t = 0; t <= total; t += step) ticks += `<span style="left:${t / total * 100}%">${Math.round(t / 60)}m</span>`;
  let gl = ''; for (let t = step; t < total; t += step) gl += `<span class="gl" style="left:${t / total * 100}%"></span>`;
  let start = 0;
  const rows = STEPS.map((s, i) => {
    const st = m.steps[i];
    const planned = st.status === 'pending' || st.status === 'skipped';
    const w = planned ? s.real : st.el;
    const left = start / total * 100, width = w / total * 100;
    start += planned ? s.real : st.el;
    const cls = st.status === 'running' ? 'running' : st.status === 'failed' ? 'failed' : planned ? 'planned' : st.status === 'none' ? 'planned' : '';
    const ic = st.status === 'ok' ? '✓' : st.status === 'failed' ? '✕' : st.status === 'running' ? '●' : String(i + 1);
    const icc = st.status === 'ok' ? 'ok' : st.status === 'failed' ? 'failed' : st.status === 'running' ? 'running' : '';
    return `<div class="gname"><span class="sicon ${icc}">${ic}</span><span><b>${esc(s.name)}</b><small id="gs-${s.k}">${esc(stepDetail(m, i, S.week))}</small></span></div>
      <div class="gtrack">${gl}${st.status === 'none' ? '' : `<span class="bar ${cls}" id="gb-${s.k}" style="left:${left}%;width:${width}%" data-tip="${esc(s.name)}\n${planned ? 'expected ~' + dur(s.real) : dur(st.el)}"></span>`}</div>
      <div class="gdur" id="gd-${s.k}">${st.status === 'pending' ? '~' + dur(s.real) : st.status === 'skipped' ? 'skipped' : st.status === 'none' ? '—' : dur(st.el)}</div>`;
  }).join('');
  const share = S.week === 4 ? Math.round(D.w4_steps.find(s => s.name === 'digest').seconds / m.elapsed * 100) : Math.round(STEPS[STEP_BY.digest].real / STEPS.reduce((a, s) => a + s.real, 0) * 100);
  return `<div class="tablewrap"><div class="gantt" style="min-width:640px">
      <div class="ghead">Step</div><div class="ghead"><div class="gaxis">${ticks}</div></div><div class="ghead" style="justify-content:flex-end">Time</div>
      ${rows}
    </div></div>
    <p class="muted" style="margin:10px 0 0;font-size:12.5px">The digest's GLM call is about ${share}% of the run's time${S.week === 4 ? ' (week 4 ran in three sittings: the gaps between them are left out)' : ''}. Striped = running, dashed = expected.</p>`;
}
function timelineTick(m) {
  if (m.idx < 0) return;
  const total = Math.max(m.elapsed + m.remaining, 1);
  let start = 0;
  m.steps.forEach((st, i) => {
    const s = STEPS[i];
    const planned = st.status === 'pending' || st.status === 'skipped';
    const w = planned ? s.real : st.el;
    const b = $('#gb-' + s.k);
    if (b) { b.style.left = (start / total * 100) + '%'; b.style.width = (w / total * 100) + '%'; }
    start += w;
  });
  const k = STEPS[m.idx].k, st = m.steps[m.idx];
  const d = $('#gd-' + k); if (d) d.textContent = dur(st.el);
  const g = $('#gs-' + k); if (g) g.textContent = STEPS[m.idx].sub(st.p);
}

/* ------------------------------------------------------------------ week 5 */
function week5View() {
  if (S.tab === 'pipeline') return week5Pipeline();
  if (S.w5 === 'published') return `<div class="notice accent"><span class="ic">i</span><div><b>Mockup: week 5 hasn't really run.</b> In the app this tab fills in from the run you just watched. Week 4 shows the same tab with real data.<div style="margin-top:10px"><button class="btn sm" data-goto="4:${S.tab}">Open week 4's ${esc(TABS.find(t => t[0] === S.tab)[1])} tab</button></div></div></div>` + (S.tab === 'games' ? slateCard() : '');
  const E = {
    digest: ['MD', 'The digest appears here when the run finishes', "Rendered from reports/2026/week05-digest.md, with its checks, the GLM's provider, time and cost beside it. If Saturday's injury update publishes an addendum, it shows up under the digest."],
    players: ['23', 'Player projections arrive with the player step', 'This week adds P08\'s stats: touchdowns, interceptions, sacks, QB hits, coverage numbers and team totals, plus the new watch list (10 offense, 10 defense, 5 tough spots).'],
    results: ['W6', "Week 5's results are graded by week 6's run", 'On Tue Oct 13 the run grades this week: each game against the final score, each projection against the box score, the watch list against its baselines.'],
    mlops: ['ML', 'Run records appear when the run finishes', 'Step timings, data freshness, quality checks, the five drift signals, alerts, model versions, and this week\'s W&B runs and artifacts.'],
    graph: ['KG', 'The graph is rebuilt during the run', 'Node and relationship counts, the insights the digest used and the ones it skipped, GDS status, and links that open each query in Neo4j Browser.'],
  };
  if (S.tab === 'games') return slateCard();
  const e = E[S.tab];
  return `<div class="card"><div class="empty"><div class="glyph">${e[0]}</div><h3>${esc(e[1])}</h3><p>${esc(e[2])}</p>${S.w5 === 'ready' || S.w5 === 'notready' ? `<button class="btn sm" data-tab-go="pipeline">Go to the pipeline</button>` : ''}<button class="btn sm" data-goto="4:${S.tab}">See week 4's ${esc(TABS.find(t => t[0] === S.tab)[1])}</button></div></div>`;
}
function slateCard() {
  const rows = D.w5_slate.map(g => {
    const fav = g.spread == null ? '—' : g.spread > 0 ? `${g.home} −${fx(g.spread)}` : g.spread < 0 ? `${g.away} −${fx(-g.spread)}` : 'PK';
    return `<tr><td class="num">${esc(kick(g.kick))}</td><td>${tchip(g.away, true)} <span class="muted">at</span> ${tchip(g.home, true)}</td><td class="muted">${esc(g.stadium || '')}</td><td class="r num">${esc(fav)}</td><td class="r num">${fx(g.total)}</td><td class="c"><span class="chip ghost">after the run</span></td><td class="c"><span class="chip ghost">after the run</span></td></tr>`;
  }).join('');
  return `<div class="card"><div class="card-h"><h2>Week 5 slate</h2><span class="muted">15 games from the schedule · market lines from nflverse · byes: CAR, KC</span></div>
    <div class="tablewrap"><table class="tbl"><thead><tr><th>Kickoff (ET)</th><th>Game</th><th>Stadium</th><th class="r">Market</th><th class="r">Total</th><th class="c">Win %</th><th class="c">Predicted score</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}
function week5Pipeline() {
  if (S.w5 === 'ready' || S.w5 === 'notready') return preflight() + pipelineBlock(5);
  let head = '';
  if (S.w5 === 'failed') head = `<div class="notice" style="border-color:color-mix(in srgb,var(--err) 50%,transparent);background:color-mix(in srgb,var(--err) 7%,var(--panel))"><span class="ic" style="color:var(--err)">✕</span><div style="flex:1;min-width:0"><b>The player step failed.</b> ${esc(FAIL_AT.msg)}. Steps before it are saved; the digest didn't run, so nothing was published. Fix the cause, then resume. The app only offers a resume for this week, from the step that failed.
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;align-items:center"><button class="btn primary" id="resumeBtn">Resume week 5 from player</button><span class="cmd">nfl weekly run --season 2026 --week 5 --from-step player</span></div></div></div>`;
  if (S.w5 === 'published') head = `<div class="tiles">
    <div class="tile sample"><span class="k">Status</span><span class="v" style="color:var(--ok)">Published</span><span class="d">mock run · 10:14 AM ET</span></div>
    <div class="tile sample"><span class="k">Before the deadline</span><span class="v">57.8<small>h</small></span><span class="d">Thu 8:15 PM ET kickoff</span></div>
    <div class="tile sample"><span class="k">Digest checks</span><span class="v">11/11</span><span class="d">passed first time</span></div>
    <div class="tile sample"><span class="k">GLM</span><span class="v">8m 41s</span><span class="d">BaseTen · $0.012</span></div>
    <div class="tile sample"><span class="k">Drift alerts</span><span class="v">0</span><span class="d">2 signals still need more weeks</span></div></div>`;
  return head + pipelineBlock(5);
}
function preflight() {
  const nr = S.w5 === 'notready';
  const C = [
    ['ok', 'Calendar', '2026 week 5 · 15 games · Thu Oct 8 → Mon Oct 12'],
    [nr ? 'err' : 'ok', 'Week 4 is final', nr ? '15/16 final · Falcons at Saints not in play-by-play yet' : '16/16 final · 16/16 in play-by-play'],
    ['ok', 'Not published yet', 'no week-5 digest on D:'],
    ['ok', 'Run lock', 'free'],
    ['ok', 'Data drive', 'D:\\nfl-ml-data found'],
    ['ok', 'Neo4j', 'up (the run starts it if it\'s down)'],
    ['ok', 'Keys', 'NEO4J_PASSWORD · WANDB_API_KEY · OPENROUTER_API_KEY set'],
    ['ok', 'Season setting', 'seasons.current = 2026'],
  ];
  return `<div class="card runpanel">
    <div><div class="sec-h"><h2 style="font-family:var(--font-display);font-size:22px">Pre-flight</h2><p>Checked when the page opens and again before the run starts</p></div>
      <ul class="checks">${C.map(c => `<li><span class="st ${c[0]}">${c[0] === 'ok' ? '✓' : '✕'}</span><span>${esc(c[1])}</span><span class="v">${esc(c[2])}</span></li>`).join('')}</ul></div>
    <div>
      <span class="eyebrow">Tuesday run</span>
      <button class="btn primary big" id="runBtn" ${nr ? 'disabled' : ''}>Run week 5</button>
      ${nr ? `<div class="reason"><span>⏳</span><span>Week 4 isn't final in the data yet. nflverse usually has Monday night's game by Tuesday morning. Try again after 6:00 AM ET; the retry window runs to Wed 6:00 PM ET.</span></div><button class="btn sm" id="recheckBtn">Check again</button>` : `<div class="reason">The week comes from the calendar, never from a picker. Most of the time goes to the GLM writing the digest: about 10–40 minutes in all.</div>`}
      <div class="cmd">nfl weekly run --auto --expect-week 5</div>
      <div class="reason">Publishes the digest and moves W&amp;B's <b>production</b> alias. Closing this tab doesn't stop a run.</div>
    </div></div>`;
}
document.addEventListener('click', e => {
  if (e.target.closest('#runBtn')) confirmRun();
  if (e.target.closest('#resumeBtn')) startRun({ from: 'player' });
  if (e.target.closest('#recheckBtn')) { S.w5 = 'ready'; syncMockbar(); render(); toast('Week 4 is final now', 'Falcons at Saints is in play-by-play (16/16). The Run button is ready.'); }
  const g = e.target.closest('[data-goto]');
  if (g) { const [w, t] = g.dataset.goto.split(':'); S.page = 'week'; S.week = Number(w); S.tab = t; render(); window.scrollTo({ top: 0 }); }
  const tg = e.target.closest('[data-tab-go]');
  if (tg) { S.tab = tg.dataset.tabGo; render(); }
});
function confirmRun() {
  const root = $('#modalRoot');
  root.innerHTML = `<div class="scrim" id="scrim"><div class="modal" role="dialog" aria-modal="true" aria-labelledby="mTitle">
    <header><span class="eyebrow">Confirm the week</span><h2 id="mTitle">Run 2026 week 5?</h2></header>
    <div class="mb">
      <div class="kv"><dt>Week</dt><dd><b>5</b> · from the calendar</dd><dt>Last week</dt><dd>week 4 · 16/16 final</dd><dt>Deadline</dt><dd>Thu Oct 8, 8:15 PM ET · 58 h</dd><dt>Steps</dt><dd>ingest → ready → curate → ratings → game → graph → player → digest</dd><dt>Publishes</dt><dd>week05-digest.md · production alias</dd></div>
      <div class="cmd">nfl weekly run --auto --expect-week 5</div>
      <span>If the calendar's week has changed by the time the run starts, it stops before touching anything (<code>--expect-week</code>).</span>
    </div>
    <footer><button class="btn" id="mCancel">Cancel</button><button class="btn primary" id="mGo">Run week 5</button></footer>
  </div></div>`;
  $('#mGo').focus();
  const close = () => { root.innerHTML = ''; };
  $('#mCancel').onclick = close;
  $('#scrim').onclick = e => { if (e.target.id === 'scrim') close(); };
  $('#mGo').onclick = () => { close(); startRun({}); };
  document.addEventListener('keydown', function k(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', k); } });
}
function toast(title, text, bad) {
  const r = $('#toastRoot');
  r.innerHTML = `<div class="toast" role="status" style="${bad ? 'border-left-color:var(--err)' : ''}"><b>${esc(title)}</b>${esc(text)}</div>`;
  clearTimeout(toast.t); toast.t = setTimeout(() => { r.innerHTML = ''; }, 6000);
}

/* ------------------------------------------------------------------ week 4 */
function week4View() {
  return ({ pipeline: w4Pipeline, digest: w4Digest, games: w4Games, players: w4Players, results: w4Results, mlops: w4MLOps, graph: w4Graph })[S.tab]();
}
const totalLLM = () => D.w4_llm.calls.reduce((a, c) => a + c.cost, 0);
function w4Pipeline() {
  const stepSec = D.w4_steps.reduce((a, s) => a + s.seconds, 0);
  return `<div class="tiles">
      <div class="tile"><span class="k">Status</span><span class="v" style="color:var(--ok)">Published</span><span class="d">Sun Oct 4, 5:02 AM ET</span></div>
      <div class="tile"><span class="k">Steps</span><span class="v">8/8 <small>ok</small></span><span class="d">run in three sittings (2 resumes)</span></div>
      <div class="tile"><span class="k">Time in steps</span><span class="v">${dur(stepSec)}</span><span class="d">digest ${Math.round(D.w4_steps.find(s => s.name === 'digest').seconds / stepSec * 100)}% of it</span></div>
      <div class="tile"><span class="k">Digest checks</span><span class="v">11/11</span><span class="d">after one regeneration</span></div>
      <div class="tile"><span class="k">LLM cost</span><span class="v">$${totalLLM().toFixed(3)}</span><span class="d">2 GLM calls · DeepInfra</span></div>
    </div>
    <div class="notice"><span class="ic">i</span><div>Week 4 ran before P07 added run records, so it has no <code>run_summary.json</code> and no W&amp;B pipeline run. The steps and times below come from <code>weekly_run.json</code>. From week 5 every run has the full record.</div></div>
    ${pipelineBlock(4)}`;
}
function md(src) {
  const L = src.replace(/\r/g, '').split('\n'); let h = ''; let i = 0;
  const il = t => esc(t).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  while (i < L.length) {
    const l = L[i];
    if (!l.trim()) { i++; continue; }
    if (/^#{1,3} /.test(l)) { const n = l.match(/^#+/)[0].length; h += `<h${n}>${il(l.replace(/^#+ /, ''))}</h${n}>`; i++; continue; }
    if (/^---+$/.test(l.trim())) { h += '<hr>'; i++; continue; }
    if (l.startsWith('|')) {
      const rows = []; while (i < L.length && L[i].startsWith('|')) rows.push(L[i++]);
      const cells = r => r.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      h += `<div class="tablewrap"><table><thead><tr>${cells(rows[0]).map(c => `<th>${il(c)}</th>`).join('')}</tr></thead><tbody>${rows.slice(2).map(r => `<tr>${cells(r).map(c => `<td>${il(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
      continue;
    }
    if (/^- /.test(l)) { const it = []; while (i < L.length && /^- /.test(L[i])) it.push(L[i++].slice(2)); h += `<ul>${it.map(x => `<li>${il(x)}</li>`).join('')}</ul>`; continue; }
    const para = []; while (i < L.length && L[i].trim() && !/^(#{1,3} |\||- |---)/.test(L[i])) para.push(L[i++]);
    const p = para.join(' ');
    h += /^_.*_$/.test(p) ? `<p><em>${il(p.slice(1, -1))}</em></p>` : `<p>${il(p)}</p>`;
  }
  return h;
}
function w4Digest() {
  const c = D.w4_checks, f = c.final;
  const wc = f.word_counts || {};
  const maxW = Math.max(...Object.values(wc));
  const calls = D.w4_llm.calls;
  return `<div class="split">
    <div class="card pad"><article class="digest">${md(D.w4_digest)}</article></div>
    <div class="rail">
      <div class="card"><div class="card-h"><h2>Checks</h2><div class="right"><span class="chip ok"><span class="ic">✓</span>Passed</span></div></div><div class="card-b" style="display:grid;gap:12px">
        <div class="kv"><dt>Result</dt><dd>all passed after one regeneration</dd><dt>Warning banner</dt><dd>${c.banner ? 'yes' : 'none'}</dd><dt>Writers</dt><dd>${esc((c.writers || []).join(', '))}</dd></div>
        <ul class="checklist">${f.checks.map(x => `<li><span class="mono">${esc(x.name)}</span><span><span class="p">✓</span> <span class="muted">${x.level}</span></span></li>`).join('')}</ul>
      </div></div>
      <div class="card"><div class="card-h"><h2>Words per section</h2></div><div class="card-b">${hbarsHTML(Object.entries(wc).map(([k, v]) => ({ label: k.replace(/_/g, ' '), v, tip: `${k.replace(/_/g, ' ')}\n${v} words` })), { max: maxW, fmt: v => v, color: 'var(--s1)' })}</div></div>
      <div class="card"><div class="card-h"><h2>The writer</h2><div class="right"><a class="btn sm" href="${WANDB}/runs/0eh6h6ll" target="_blank" rel="noopener">digest run ↗</a></div></div><div class="card-b" style="display:grid;gap:12px">
        <div class="kv"><dt>Model</dt><dd class="mono">${esc(D.w4_llm.model)}</dd><dt>Route</dt><dd>${esc(D.w4_llm.provider)} → ${esc(calls[0].provider)}</dd><dt>Prompt</dt><dd class="mono">248ed5fb0b33</dd><dt>Total cost</dt><dd>$${totalLLM().toFixed(4)}</dd></div>
        <div class="tablewrap"><table class="tbl"><thead><tr><th>Call</th><th class="r">Time</th><th class="r">Reasoning</th><th class="r">Cost</th></tr></thead><tbody>${calls.map((x, i) => `<tr><td>${i === 0 ? 'first draft' : 'rewrite'}</td><td class="r num">${dur(x.latency_s)}</td><td class="r num">${comma(x.reasoning_tokens)}</td><td class="r num">$${x.cost.toFixed(4)}</td></tr>`).join('')}</tbody></table></div>
        <div class="notice" style="font-size:12.5px"><span class="ic">i</span><span>Since P09 the route tries BaseTen first (D88). Week 5 is the first live run on it.</span></div>
      </div></div>
      <div class="card"><div class="card-h"><h2>Files</h2></div><div class="card-b"><div class="kv mono" style="font-size:11.5px"><dt>digest</dt><dd>reports/2026/week04-digest.md</dd><dt>payload</dt><dd>runs/2026/week04/payload.json</dd><dt>checks</dt><dd>…/week04/checks.json</dd><dt>raw LLM</dt><dd>…/week04/raw_llm_output.json</dd><dt>Saturday</dt><dd>no addendum</dd></div></div></div>
    </div></div>`;
}
const QB_CHANGES = new Set(['Michael Penix Jr.', 'Jalon Daniels', 'Marcus Mariota', 'Kyler Murray', 'Case Keenum']);
const isQBChange = name => QB_CHANGES.has(name);
function w4Games() {
  const G = D.w4_games;
  const gaps = G.filter(g => g.p_market != null).map(g => ({ g, gap: g.p_home_model_only - g.p_market })).sort((a, b) => Math.abs(b.gap) - Math.abs(a.gap)).slice(0, 3);
  const cards = G.map(g => {
    const favHome = g.p_home >= 0.5;
    const final = g.score_home != null;
    const mk = (p, cls, color, label, hollow) => p == null ? '' : `<span class="mk ${cls}" style="left:${p * 100}%;${hollow ? `background:var(--panel);border:2.5px solid ${color}` : `background:${color}`}" data-tip="${esc(label)}\n${esc(team(g.home).nick)} ${pct(p)} · ${esc(team(g.away).nick)} ${pct(1 - p)}"></span>`;
    const spread = g.spread == null ? '—' : g.spread > 0 ? `${g.home} −${fx(g.spread)}` : g.spread < 0 ? `${g.away} −${fx(-g.spread)}` : 'PK';
    const qbA = `${esc(g.qb_away)}${isQBChange(g.qb_away) ? ' ⚠' : ''}`, qbH = `${esc(g.qb_home)}${isQBChange(g.qb_home) ? ' ⚠' : ''}`;
    return `<div class="game">
      <div class="gh"><span>${esc(kick(g.kick))} ET${g.neutral ? ' · neutral site' : ''}</span><span class="chip ${g.conf === 'strong' ? 'ok' : g.conf === 'solid' ? 'run' : 'flat'}">${esc(g.conf)}</span></div>
      <div class="rows">
        <span class="tn">${tchip(g.away, true)}<small>${qbA}</small></span><span class="pct" style="${!favHome ? '' : 'color:var(--ink-3)'}">${pct(1 - g.p_home)}</span><span class="pts">${final ? `<b>${g.score_away}</b>` : Math.round(g.pts_away)}</span>
        <span class="tn">${tchip(g.home, true)}<small>${qbH}</small></span><span class="pct" style="${favHome ? '' : 'color:var(--ink-3)'}">${pct(g.p_home)}</span><span class="pts">${final ? `<b>${g.score_home}</b>` : Math.round(g.pts_home)}</span>
      </div>
      <div class="probbar" aria-label="Home win chance by source"><span class="track"></span><span class="mid"></span>${mk(g.p_elo, '', 'var(--s2)', 'Elo')}${mk(g.p_market, '', 'var(--s3)', 'Market')}${mk(g.p_home_model_only, '', 'var(--s1)', 'Model only', true)}${mk(g.p_home, 'main', 'var(--s1)', 'Model (shown in the digest)')}</div>
      <div class="gf"><span>${esc(g.away)} ← → ${esc(g.home)} · market ${esc(spread)} · total ${fx(g.total)}</span><span>${final ? `<span class="chip ${((g.score_home > g.score_away) === favHome) ? 'ok' : 'err'}">${((g.score_home > g.score_away) === favHome) ? '✓ hit' : '✕ miss'} · final</span>` : '<span class="muted">result after week 5\'s ingest</span>'}</span></div>
    </div>`;
  }).join('');
  return `<div class="grid g3">
      <div class="tile"><span class="k">Games predicted</span><span class="v">16</span><span class="d">15 before kickoff + Thursday's kept pick</span></div>
      <div class="tile"><span class="k">Lines used</span><span class="v">16/16</span><span class="d">market-informed rows shown in the digest</span></div>
      <div class="tile"><span class="k">Model</span><span class="v" style="font-size:20px">game-model-v0</span><span class="d">trained through 2026-w03</span></div>
    </div>
    <div class="card"><div class="card-h"><h2>Where the model disagrees with the market</h2><span class="muted">model-only win chance vs the market's, biggest gaps</span></div><div class="card-b"><div class="grid g3">${gaps.map(({ g, gap }) => `<div><div style="font-weight:700">${tchip(g.away, true)} at ${tchip(g.home, true)}</div><div class="muted" style="font-size:13px;margin-top:4px">Model only gives ${esc(team(g.home).nick)} <b style="color:var(--ink)">${pct(g.p_home_model_only)}</b>, market ${pct(g.p_market)} (${sgn(gap * 100, 0)} pts)</div></div>`).join('')}</div></div></div>
    <div class="sec-h"><h2 style="font-family:var(--font-display);font-size:20px">All games</h2><div class="right legend"><span><i style="background:var(--s1)"></i>Model (shown)</span><span><i style="background:var(--panel);box-shadow:inset 0 0 0 2.5px var(--s1)"></i>Model only</span><span><i style="background:var(--s2)"></i>Elo</span><span><i style="background:var(--s3)"></i>Market</span><span class="muted">dots: home team's win chance, away ← 50% → home</span></div></div>
    <div class="games">${cards}</div>
    <div class="foot">Steelers at Browns is the only final in the data so far (24–27). The rest are graded by Tuesday's week-5 run.</div>`;
}
function rangeHTML(r, mx, actual) {
  const P = v => Math.max(0, Math.min(100, v / mx * 100));
  return `<div class="range" data-tip="${esc(r.player)}\nrange ${fx(r.p10, 0)}–${fx(r.p90, 0)} · projection ${fx(r.unit === 'count' ? r.mean : r.p50, 1)}\nbaseline ${fx(r.baseline, 1)}${actual != null ? `\nactual (sample) ${fx(actual, 0)}` : ''}"><span class="rt"></span><span class="rb" style="left:${P(r.p10)}%;width:${P(r.p90) - P(r.p10)}%"></span><span class="rbase" style="left:${P(r.baseline)}%"></span><span class="rp" style="left:${P(r.unit === 'count' ? r.mean : r.p50)}%"></span>${actual != null ? `<span class="ract" style="left:${P(actual)}%"></span>` : ''}</div>`;
}
const TOUGH = [
  { player: 'Jaxon Smith-Njigba', team: 'SEA', pos: 'WR', opp: 'LAC', target: 'receiving yards', proj: 79, base: 120, lo: 17, hi: 146 },
  { player: 'Dallas Turner', team: 'MIN', pos: 'LB', opp: 'MIA', target: 'pressures', proj: 2.6, base: 4, lo: 1, hi: 4 },
  { player: 'Maxx Crosby', team: 'LV', pos: 'DE', opp: 'KC', target: 'pressures', proj: 2.1, base: 3.2, lo: 1, hi: 3 },
];
function w4Players() {
  const groups = ['All', 'QB', 'RB', 'WR/TE', 'EDGE/DL', 'LB/S'];
  const rows = D.w4_players.filter(r => S.group === 'All' || r.group === S.group).sort((a, b) => (b.outperf ?? 0) - (a.outperf ?? 0)).slice(0, S.group === 'All' ? 30 : 18);
  const watch = D.w4_watch.map(r => {
    const mx = r.p90 * 1.15;
    const proj = r.unit === 'count' ? r.mean : r.p50;
    return `<div class="wcard"><div class="wh"><span class="wn">${esc(r.player)}</span><span class="chip flat">#${r.rank}</span></div>
      <span class="wm">${tchip(r.team)} ${esc(r.pos)} · ${r.home ? 'vs' : 'at'} ${esc(team(r.opp).nick)}${r.inj ? ` · <span style="color:var(--warn);font-weight:600">${esc(r.inj)}</span>` : ''}</span>
      <span class="wp">${fx(proj, proj >= 20 ? 0 : 1)}<small>${esc(r.target)}</small></span>
      ${rangeHTML(r, mx)}
      <span class="why"><b class="delta-up">${sgn(r.outperf, r.outperf >= 10 ? 0 : 1)}</b> vs his baseline ${fx(r.baseline, 1)} · ${esc(r.drivers[0] ? r.drivers[0].phrase : 'no single factor stands out')}</span></div>`;
  }).join('');
  const table = rows.map(r => {
    const proj = r.unit === 'count' ? r.mean : r.p50;
    const mx = Math.max(r.p90, r.baseline) * 1.1 || 1;
    return `<tr><td><b>${esc(r.player)}</b> <span class="muted">${esc(r.pos)}</span></td><td>${tchip(r.team)} <span class="muted">${r.home ? 'vs' : 'at'} ${esc(r.opp)}</span></td><td>${esc(r.target)}</td><td class="r num"><b>${fx(proj, proj >= 20 ? 0 : 1)}</b></td>
      <td><div class="minirange" data-tip="${esc(r.player)}\nrange ${fx(r.p10, 0)}–${fx(r.p90, 0)} · baseline ${fx(r.baseline, 1)}"><span class="rt"></span><span class="rb" style="left:${r.p10 / mx * 100}%;width:${(r.p90 - r.p10) / mx * 100}%"></span><span class="rbase" style="left:${r.baseline / mx * 100}%"></span><span class="rp" style="left:${proj / mx * 100}%"></span></div></td>
      <td class="r num">${fx(r.baseline, 1)}</td><td class="r num"><span class="${(r.outperf ?? 0) >= 0 ? 'delta-up' : 'delta-down'}">${sgn(r.outperf, Math.abs(r.outperf) >= 10 ? 0 : 1)}</span></td><td>${esc(r.conf)}</td><td>${r.inj ? `<span class="chip warn">${esc(r.inj)}</span>` : ''}</td></tr>`;
  }).join('');
  return `<div class="sec-h"><h2 style="font-family:var(--font-display);font-size:22px">Players to watch</h2><p>The 8 model picks from the digest · ${comma(D.w4_player_counts.total)} projections in all</p></div>
    <div class="legend"><span><i style="background:color-mix(in srgb,var(--s1) 30%,transparent);border-radius:2px"></i>80% range (10th–90th percentile)</span><span><i class="line" style="background:var(--s1);width:3px;height:12px"></i>Projection</span><span><i class="line" style="border-left:2px dotted var(--ink-2);background:none;width:2px;height:12px"></i>His rolling baseline</span></div>
    <div class="watch">${watch}</div>
    <div class="card"><div class="card-h"><h2>Tough spots</h2><span class="muted">projected well below their own baseline</span></div><div class="card-b"><div class="grid g3">${TOUGH.map(t => `<div><b>${esc(t.player)}</b> <span class="muted">${esc(t.pos)} · ${tchip(t.team)} vs ${esc(team(t.opp).nick)}</span><div style="margin-top:4px;font-size:13px">${t.proj} ${esc(t.target)} projected · baseline ${t.base} <b class="delta-down">${sgn(t.proj - t.base, t.proj >= 20 ? 0 : 1)}</b></div></div>`).join('')}</div></div></div>
    <div class="card"><div class="card-h"><h2>All projections</h2><span class="muted">sorted by how far above his baseline · main stat per group</span><div class="right filters">${groups.map(g => `<button class="fchip" data-group="${g}" aria-pressed="${S.group === g}">${g}</button>`).join('')}</div></div>
      <div class="tablewrap"><table class="tbl"><thead><tr><th>Player</th><th>Team</th><th>Stat</th><th class="r">Proj.</th><th>Range</th><th class="r">Baseline</th><th class="r">vs base</th><th>Conf.</th><th>Injury</th></tr></thead><tbody>${table}</tbody></table></div></div>
    <div class="foot">Week 4 projected 11 stats (P06). From week 5 the model projects 23, plus team totals.</div>`;
}
document.addEventListener('click', e => { const g = e.target.closest('[data-group]'); if (g) { S.group = g.dataset.group; render(); } });

function sampleResults() {
  const r = rng(20261004);
  const games = D.w4_games.map(g => {
    let hs, as;
    if (g.score_home != null) { hs = g.score_home; as = g.score_away; }
    else {
      const margin = g.margin + gauss(r) * 12.5;
      const total = Math.max(24, g.pts_home + g.pts_away + gauss(r) * 8);
      hs = Math.max(0, Math.round((total + margin) / 2)); as = Math.max(0, Math.round((total - margin) / 2));
      if (hs === as) hs += 3;
    }
    const y = hs > as ? 1 : 0;
    return { g, hs, as, y, real: g.score_home != null, bm: (g.p_home - y) ** 2, be: (g.p_elo - y) ** 2, bk: g.p_market == null ? null : (g.p_market - y) ** 2, hit: (g.p_home >= 0.5) === (y === 1) };
  });
  const watch = D.w4_watch.map(w => {
    const sd = (w.p90 - w.p10) / 2.56;
    let a = Math.max(0, (w.unit === 'count' ? w.mean : w.p50) + gauss(r) * sd);
    if (w.unit === 'count') a = Math.round(a);
    return { w, a, inside: a >= w.p10 && a <= w.p90, beat: a > w.baseline };
  });
  const groups = ['QB', 'RB', 'WR/TE', 'EDGE/DL', 'LB/S'].map(gr => ({ gr, imp: +(2 + r() * 8).toFixed(1) }));
  return { games, watch, groups };
}
function w4Results() {
  const R = sampleResults();
  const n = R.games.length;
  const avg = k => R.games.reduce((a, x) => a + (x[k] ?? 0), 0) / R.games.filter(x => x[k] != null).length;
  const hits = R.games.filter(x => x.hit).length;
  const rows = R.games.map(x => {
    const g = x.g; const pickHome = g.p_home >= 0.5; const pick = pickHome ? g.home : g.away; const pp = pickHome ? g.p_home : 1 - g.p_home;
    return `<tr><td>${tchip(g.away)} <span class="muted">at</span> ${tchip(g.home)}</td><td>${tchip(pick, true)} <span class="muted">${pct(pp)}</span></td><td class="num">${x.as}–${x.hs} ${x.real ? '<span class="chip ok">real</span>' : '<span class="chip ghost">sample</span>'}</td><td class="c">${x.hit ? '<span class="chip ok">✓ hit</span>' : '<span class="chip err">✕ miss</span>'}</td><td class="r num">${x.bm.toFixed(3)}</td><td class="r num">${x.be.toFixed(3)}</td><td class="r num">${x.bk == null ? '—' : x.bk.toFixed(3)}</td><td class="r num">${sgn((x.hs - x.as) - g.margin, 1)}</td></tr>`;
  }).join('');
  const wrows = R.watch.map(x => `<tr><td><b>${esc(x.w.player)}</b> <span class="muted">${esc(x.w.pos)} · ${esc(x.w.team)}</span></td><td>${esc(x.w.target)}</td><td class="r num">${fx(x.w.unit === 'count' ? x.w.mean : x.w.p50, 1)}</td><td style="min-width:170px">${rangeHTML(x.w, x.w.p90 * 1.3, x.a)}</td><td class="r num"><b>${fx(x.a, x.w.unit === 'count' ? 0 : 0)}</b></td><td class="c">${x.inside ? '<span class="chip ok">✓ inside</span>' : '<span class="chip warn">outside</span>'}</td><td class="c">${x.beat ? '<span class="chip ok">✓ beat</span>' : '<span class="chip err">✕ under</span>'}</td></tr>`).join('');
  return `<div class="notice accent"><span class="ic">!</span><div><b>Sample results.</b> Week 4 is graded by week 5's run on Tuesday, so the scores and box-score numbers below are made up to show the layout. Steelers at Browns (24–27) is the only real final. Everything else on this tab becomes real after Tuesday.</div></div>
    <div class="tiles">
      <div class="tile sample"><span class="k">Picks right</span><span class="v">${hits}/${n}</span><span class="d">${pct(hits / n)} of games</span></div>
      <div class="tile sample"><span class="k">Brier · model</span><span class="v">${avg('bm').toFixed(3)}</span><span class="d">lower is better</span></div>
      <div class="tile sample"><span class="k">Brier · Elo</span><span class="v">${avg('be').toFixed(3)}</span><span class="d">${avg('bm') < avg('be') ? 'model better' : 'Elo better'} by ${Math.abs(avg('be') - avg('bm')).toFixed(3)}</span></div>
      <div class="tile sample"><span class="k">Brier · market</span><span class="v">${avg('bk').toFixed(3)}</span><span class="d">closing lines</span></div>
      <div class="tile sample"><span class="k">Watch list</span><span class="v">${R.watch.filter(x => x.beat).length}/${R.watch.length}</span><span class="d">beat their baseline</span></div>
    </div>
    <div class="card"><div class="card-h"><h2>Game by game</h2><span class="muted">the digest's pick against the final score</span></div>
      <div class="tablewrap"><table class="tbl"><thead><tr><th>Game</th><th>Pick</th><th>Final</th><th class="c">Result</th><th class="r">Brier model</th><th class="r">Elo</th><th class="r">Market</th><th class="r">Margin error</th></tr></thead><tbody>${rows}</tbody></table></div></div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h2>Player model vs baseline</h2><span class="muted">sample · MAE improvement by group</span></div><div class="card-b">${hbarsHTML(R.groups.map(x => ({ label: x.gr, v: x.imp, tip: `${x.gr}\n${x.imp}% better than the rolling baseline (sample)` })), { max: 12, fmt: v => '+' + v.toFixed(1) + '%', color: 'var(--s1)' })}</div></div>
      <div class="card"><div class="card-h"><h2>Watch list</h2><span class="muted">sample · projection, range and the actual</span></div><div class="tablewrap"><table class="tbl"><thead><tr><th>Player</th><th>Stat</th><th class="r">Proj.</th><th>Range · ◆ actual</th><th class="r">Actual</th><th class="c">Range</th><th class="c">Baseline</th></tr></thead><tbody>${wrows}</tbody></table></div></div>
    </div>`;
}
function w4MLOps() {
  const SECS = [['health', 'Health'], ['wandb', 'W&B runs'], ['artifacts', 'Artifacts']];
  const blurb = { health: 'Did the run go cleanly, and was the data fresh?', wandb: "This week's W&B runs, their main charts redrawn here, each linked to W&B", artifacts: 'Every model, graph and digest version, its aliases, and where production points' }[S.mlops];
  const nav = `<div class="sec-h"><span class="seg" id="mlopsSeg" role="tablist" aria-label="MLOps sections">${SECS.map(([k, l]) => `<button type="button" role="tab" data-mlops="${k}" aria-pressed="${S.mlops === k}" aria-selected="${S.mlops === k}">${l}</button>`).join('')}</span><p>${esc(blurb)}</p></div>`;
  return nav + ({ health: mlHealth, wandb: mlWandb, artifacts: mlArtifacts })[S.mlops]();
}
document.addEventListener('click', e => { const b = e.target.closest('[data-mlops]'); if (b) { S.mlops = b.dataset.mlops; render(); } });
function mlHealth() {
  const st = D.w4_steps;
  const maxS = Math.max(...st.map(s => s.seconds));
  const g = D.w4_graph;
  const stepSec = st.reduce((a, s) => a + s.seconds, 0);
  const digSec = st.find(s => s.name === 'digest').seconds;
  const signals = [
    ['game_vs_elo', 'Brier vs Elo over rolling 4-week windows', "needs 6 graded weeks · first possible with week 10's run"],
    ['calibration', 'Season ECE vs the noise level', 'needs 64 graded games · about week 8'],
    ['player_vs_baseline', 'MAE vs the rolling baseline, per group', "walk-forward weeks 1–3 count · first possible with week 7's run"],
    ['data_freshness', 'Snapshots older than 7 days, sources a week behind', 'checked every run (week 4 ran before run records)'],
    ['checks', 'Share of digests that failed their checks', 'needs 4 weeks of digests'],
  ];
  const ing = D.w4_ingest.results;
  const qc = D.quality_checks;
  const vs = [
    ['Time in steps', dur(stepSec)], ['GLM writing the digest', dur(D.w4_llm.calls.reduce((a, c) => a + c.latency_s, 0))], ['LLM cost', '$' + totalLLM().toFixed(3)],
    ['Games predicted', '16'], ['Player projections', comma(D.w4_player_counts.total)], ['Graph nodes', comma(g.nodes)], ['Datasets ingested', String(ing.length)], ['Digest passed first time', 'no · 1 rewrite'],
  ];
  return `<div class="tiles">
      <div class="tile"><span class="k">Run status</span><span class="v" style="color:var(--ok)">ok</span><span class="d">8/8 steps ok · 0 degraded</span></div>
      <div class="tile"><span class="k">Data</span><span class="v">${ing.length} <small>datasets</small></span><span class="d">${ing.filter(r => r.status !== 'ok').length} failures · snapshot ${esc(D.w4_ingest.snapshot_date)}</span></div>
      <div class="tile"><span class="k">Quality checks</span><span class="v">${qc.filter(q => q.passed).length}/${qc.length}</span><span class="d">${qc.filter(q => q.level === 'block').length} blocking, all pass</span></div>
      <div class="tile"><span class="k">Drift alerts</span><span class="v">0</span><span class="d">5 signals · not enough weeks yet</span></div>
      <div class="tile"><span class="k">Projections</span><span class="v">${comma(D.w4_player_counts.total)}</span><span class="d">all written to the graph</span></div>
    </div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h2>Step timings</h2><span class="muted">from weekly_run.json</span></div><div class="card-b">
        ${hbarsHTML(st.map(s => ({ label: s.name, v: s.seconds, tip: `${s.name}\n${dur(s.seconds)}` })), { max: maxS, fmt: v => dur(v), color: 'var(--s2)' })}
        <p class="muted" style="margin:10px 0 0;font-size:12.5px">The digest is the long pole (${Math.round(digSec / stepSec * 100)}%): a first draft and one rewrite. BaseTen-first routing (D88) should cut it to about 3–10 minutes.</p></div></div>
      <div class="card"><div class="card-h"><h2>What ran</h2><span class="muted">versions and settings behind this week</span></div><div class="card-b"><div class="kv">
        <dt>Game model</dt><dd class="mono">${esc(D.w4_model.game)}</dd><dt>Trained through</dt><dd>${esc(D.w4_model.trained_through)}</dd>
        <dt>Player model</dt><dd class="mono">${esc(D.w4_player_counts.model)} · 11 stats</dd><dt>Graph</dt><dd>live build · ${esc(kickDay(D.w4_graph.built_at))}</dd>
        <dt>Digest writer</dt><dd class="mono">${esc(D.w4_llm.model)}</dd><dt>Route</dt><dd>${esc(D.w4_llm.provider)} → ${esc(D.w4_llm.calls[0].provider)}</dd>
        <dt>Prompt</dt><dd class="mono">248ed5fb0b33</dd><dt>Promoted</dt><dd>game-model, player-model → production</dd><dt>Launched by</dt><dd>agent (delegated)</dd></div></div></div>
    </div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h2>Data freshness</h2><span class="muted">what the run read</span></div><div class="tablewrap"><table class="tbl"><thead><tr><th>Source</th><th>Newest</th><th class="c">State</th></tr></thead><tbody>
        ${[['Play-by-play', 'snapshot 2026-10-04 · through week 3'], ['Injury reports', 'snapshot 2026-10-04'], ['Next Gen Stats', 'through week 3'], ['PFR advanced', 'through week 3'], ['FTN charting', 'through week 3'], ['Market lines', 'nflverse schedules + The Odds API'], ['ESPN news', '2026-10-04']].map(r => `<tr><td>${r[0]}</td><td class="muted">${r[1]}</td><td class="c"><span class="chip ok">✓ fresh</span></td></tr>`).join('')}
      </tbody></table></div></div>
      <div class="card"><div class="card-h"><h2>This week vs last week</h2><span class="muted">week 4 is the first live week</span></div><div class="tablewrap"><table class="tbl"><thead><tr><th>Measure</th><th class="r">Week 4</th><th class="r">Week 3</th><th>Trend</th></tr></thead><tbody>
        ${vs.map(r => `<tr><td>${r[0]}</td><td class="r num"><b>${esc(r[1])}</b></td><td class="r muted">no live run</td><td class="muted" style="font-size:12px">from week 5</td></tr>`).join('')}
      </tbody></table></div><div class="card-b" style="padding-top:8px"><span class="muted" style="font-size:12.5px">From week 5 each row gets the change and a season trend line from <span class="mono">pipeline_history.parquet</span>, so a slow or shrinking week stands out.</span></div></div>
    </div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h2>Ingest, dataset by dataset</h2><span class="muted">raw/_runs manifest · ${esc(D.w4_ingest.snapshot_date)}</span></div><div class="tablewrap" style="max-height:380px;overflow:auto"><table class="tbl"><thead><tr><th>Source</th><th>Dataset</th><th class="r">Rows</th><th class="c">Status</th></tr></thead><tbody>
        ${ing.map(r => `<tr><td class="muted">${esc(r.source)}</td><td class="mono">${esc(r.dataset)}</td><td class="r num">${r.rows == null ? '—' : comma(r.rows)}</td><td class="c"><span class="chip ${r.status === 'ok' ? 'ok' : 'err'}">${r.status === 'ok' ? '✓ ok' : esc(r.status)}</span></td></tr>`).join('')}
      </tbody></table></div></div>
      <div class="card"><div class="card-h"><h2>Quality checks</h2><span class="muted">curate · block = stops the run, warn = flags it</span></div><div class="tablewrap" style="max-height:380px;overflow:auto"><table class="tbl"><thead><tr><th>Check</th><th>Level</th><th class="c">Result</th></tr></thead><tbody>
        ${qc.map(q => `<tr><td><span>${esc(q.name)}</span><div class="muted" style="font-size:11.5px">${esc(q.detail || '')}</div></td><td><span class="chip flat">${esc(q.level)}</span></td><td class="c"><span class="chip ${q.passed ? 'ok' : 'err'}">${q.passed ? '✓ pass' : '✕ fail'}</span></td></tr>`).join('')}
      </tbody></table></div></div>
    </div>
    <div class="card"><div class="card-h"><h2>Drift signals</h2><span class="muted">they ask a person to look; they never retune anything</span><div class="right"><span class="chip ghost">not enough weeks yet</span></div></div><div class="card-b">
      ${signals.map(s => `<div class="signal"><b>${s[0]}</b><span class="chip ghost">insufficient data</span><p>${esc(s[1])} · ${esc(s[2])}</p></div>`).join('')}
    </div></div>`;
}
function dumbbell(games) {
  const rows = games.filter(g => g.p_market != null).map(g => ({ g, gap: g.p_home_model_only - g.p_market })).sort((a, b) => b.gap - a.gap);
  const W = 620, rh = 22, m = { l: 84, r: 16, t: 8, b: 26 };
  const H = m.t + rows.length * rh + m.b;
  const X = p => m.l + p * (W - m.l - m.r);
  let s = `<g class="grid">${[0, .25, .5, .75, 1].map(t => `<line x1="${X(t)}" x2="${X(t)}" y1="${m.t}" y2="${H - m.b}"/>`).join('')}</g>`;
  s += [0, .25, .5, .75, 1].map(t => `<text x="${X(t)}" y="${H - 8}" text-anchor="middle">${t * 100}%</text>`).join('');
  rows.forEach((r, i) => {
    const y = m.t + i * rh + rh / 2, g = r.g;
    s += `<text x="${m.l - 8}" y="${y + 4}" text-anchor="end">${esc(g.away)} at ${esc(g.home)}</text>`;
    s += `<line x1="${X(g.p_market)}" x2="${X(g.p_home_model_only)}" y1="${y}" y2="${y}" style="stroke:var(--line-2)" stroke-width="3" stroke-linecap="round"/>`;
    s += `<circle cx="${X(g.p_market)}" cy="${y}" r="5.5" style="fill:var(--s3);stroke:var(--panel)" stroke-width="2" data-tip="${esc(g.away)} at ${esc(g.home)}\nmarket: ${esc(g.home)} ${pct(g.p_market)}"/>`;
    s += `<circle cx="${X(g.p_home_model_only)}" cy="${y}" r="5.5" style="fill:var(--panel);stroke:var(--s1)" stroke-width="2.5" data-tip="${esc(g.away)} at ${esc(g.home)}\nmodel only: ${esc(g.home)} ${pct(g.p_home_model_only)}\ngap ${sgn(r.gap * 100, 0)} pts"/>`;
  });
  return `<div class="legend" style="margin-bottom:6px"><span><i style="background:var(--panel);box-shadow:inset 0 0 0 2.5px var(--s1)"></i>Model only</span><span><i style="background:var(--s3)"></i>Market</span><span class="muted">home team's win chance · sorted by the gap</span></div><div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Model-only versus market home win chance for each week-4 game">${s}</svg></div>`;
}
function wbCard(title, runName, id, keys, body) {
  return `<div class="card"><div class="card-h"><h2>${esc(title)}</h2><span class="muted mono" style="font-size:11.5px">${esc(runName)} · ${esc(keys)}</span><div class="right">${id ? `<a class="btn sm" href="${WANDB}/runs/${id}" target="_blank" rel="noopener">${id} ↗</a>` : ''}</div></div><div class="card-b">${body}</div></div>`;
}
function mlWandb() {
  const runs = [
    ['game', 'train-2026-w04', 'st8440zw', 'track1-game / train'], ['graph', 'graph-2026-w04', 'g95ulxqj', 'track1-graph / build'],
    ['player', 'scoreboard-2026-w04', 'ip2ny9sz', 'track1-player / eval'], ['player', 'train-2026-w04', 'kl4fzvl8', 'track1-player / train'],
    ['digest', 'digest-2026-w04', '0eh6h6ll', 'weekly-pipeline / main'], ['records', 'season-2026-w04', 'ugjybk9h', 'season-dashboard / dashboard'],
  ];
  const sb = D.sb2026.filter(x => x.improvement_pct != null);
  const groups = [...new Set(sb.map(x => x.position_group))].map(gr => { const xs = sb.filter(x => x.position_group === gr); const w = xs.reduce((a, x) => a + x.n_scored, 0); return { gr, v: xs.reduce((a, x) => a + x.improvement_pct * x.n_scored, 0) / w }; }).sort((a, b) => b.v - a.v);
  const per = Object.entries(D.w4_player_counts.by_target).map(([k, v]) => { const [gr, t] = k.split('|'); return { label: `${t} · ${gr}`, v }; }).sort((a, b) => b.v - a.v);
  const gt = D.w4_graph.timings;
  const stages = [['wipe', gt.wipe_s], ['load', gt.load_s], ['queries', gt.queries_s], ['schema', gt.schema_s], ['tables', gt.tables_s], ['counts', gt.counts_s], ['inputs', gt.inputs_s]];
  const wc = D.w4_checks.final.word_counts;
  return `<div class="grid g2">
      <div class="card"><div class="card-h"><h2>Runs this week</h2><div class="right"><a class="btn sm" href="${WANDB}" target="_blank" rel="noopener">project ↗</a></div></div><div class="card-b"><ul class="runs">
        ${runs.map(r => `<li><span class="job">${r[0]}</span><span><b>${r[1]}</b> <span class="muted">${r[3]}</span></span><a href="${WANDB}/runs/${r[2]}" target="_blank" rel="noopener">${r[2]} ↗</a></li>`).join('')}
        <li><span class="job">records</span><span><b>pipeline-2026-w04</b> <span class="muted">weekly-pipeline / pipeline</span></span><span class="chip ghost">none (before P07)</span></li>
      </ul></div></div>
      <div class="card"><div class="card-h"><h2>Season dashboard</h2><div class="right"><a class="btn sm" href="${DASHBOARD}" target="_blank" rel="noopener">report ↗</a></div></div><div class="card-b" style="display:grid;gap:10px">
        <p style="margin:0">The W&amp;B Report reads the newest <span class="mono">season-dashboard</span> run (tag <span class="mono">dashboard-current</span>), now <span class="mono">ugjybk9h</span>. Its charts are redrawn on <b>Season → Scorecard</b>.</p>
        <p class="muted" style="margin:0;font-size:12.5px">Only the player series have points until week 4 is graded by Tuesday's run.</p>
        <div class="notice" style="font-size:12.5px"><span class="ic">i</span><span>From week 5 the list also has <span class="mono">pipeline-2026-w05</span> (step times, freshness and drift tables) and <span class="mono">team-model</span> in the player run.</span></div>
      </div></div>
    </div>
    <div class="grid g2">
      ${wbCard('Game fit: model only vs market', 'train-2026-w04', 'st8440zw', 'slate/*', dumbbell(D.w4_games))}
      ${wbCard('Player scoreboard: vs the baseline', 'scoreboard-2026-w04', 'ip2ny9sz', 'scoreboard/improvement_*', `<p class="muted" style="margin:0 0 10px;font-size:12.5px">MAE improvement over the rolling baseline by group, 2026 weeks 1–3 (walk-forward rows; no live week was graded yet).</p>${hbarsHTML(groups.map(x => ({ label: x.gr, v: x.v, tip: `${x.gr}\n${x.v.toFixed(1)}% better than the baseline` })), { max: Math.max(...groups.map(x => x.v)) * 1.1, fmt: v => '+' + v.toFixed(1) + '%', color: 'var(--s1)' })}`)}
      ${wbCard('Player fit: projections per stat', 'train-2026-w04', 'kl4fzvl8', 'projections_per_target', hbarsHTML(per.map(x => ({ label: x.label, v: x.v, tip: `${x.label}\n${x.v} projections` })), { max: per[0].v, fmt: v => comma(v), color: 'var(--s2)' }))}
      ${wbCard('Graph build: time by stage', 'graph-2026-w04', 'g95ulxqj', 'load/* · query_seconds', `${hbarsHTML(stages.map(([k, v]) => ({ label: k, v, tip: `${k}\n${v} s` })), { max: gt.wipe_s > gt.load_s ? gt.wipe_s : gt.load_s, fmt: v => v + ' s', color: 'var(--s3)' })}<p class="muted" style="margin:10px 0 0;font-size:12.5px">${comma(D.w4_graph.nodes)} nodes, ${comma(D.w4_graph.rels)} relationships, ${D.w4_graph.mismatches} count mismatches, ${Math.round(gt.total_s)} s in all.</p>`)}
      ${wbCard('Digest: words per section', 'digest-2026-w04', '0eh6h6ll', 'words_per_section · check_issue_counts', `${hbarsHTML(Object.entries(wc).map(([k, v]) => ({ label: k.replace(/_/g, ' '), v, tip: `${k}\n${v} words` })), { max: Math.max(...Object.values(wc)), fmt: v => v, color: 'var(--s1)' })}<div style="margin-top:12px;display:flex;gap:6px;flex-wrap:wrap;align-items:center"><span class="muted" style="font-size:12.5px">Check issues (final):</span>${D.w4_checks.final.checks.map(c => `<span class="chip ok" title="${esc(c.name)}">${esc(c.name)} 0</span>`).join('')}</div>`)}
      ${wbCard('Pipeline run: time per step', 'pipeline-2026-w04', null, 'step_seconds', '<div class="empty" style="padding:22px 10px"><div class="glyph">P07</div><p>Week 4 ran before the pipeline run existed. From week 5 this card shows the run\'s step times, data freshness and drift tables, and links the week\'s artifacts.</p></div>')}
    </div>`;
}
function bytes(n) { return n == null ? '—' : n > 1e6 ? (n / 1e6).toFixed(1) + ' MB' : (n / 1e3).toFixed(0) + ' KB'; }
function mlArtifacts() {
  const A = D.wb_artifacts;
  const prodOf = k => Array.isArray(A[k]) ? A[k].find(v => v.aliases.includes('production')) : null;
  const card = (name, type, note, firstNote) => {
    const vs = A[name];
    const body = !Array.isArray(vs) ? `<div class="empty" style="padding:18px 10px"><div class="glyph">—</div><p>No versions yet. ${esc(firstNote)}</p></div>`
      : `<div class="tablewrap"><table class="tbl"><thead><tr><th>Version</th><th>Created (ET)</th><th>Aliases</th><th>Logged by</th><th class="r">Size</th></tr></thead><tbody>${vs.slice(0, 6).map(v => `<tr><td class="mono"><b>${esc(v.version)}</b></td><td class="num">${esc(ETF({ month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(v.created + 'Z')))}</td><td>${v.aliases.filter(a => a !== 'latest').map(a => `<span class="chip ${a === 'production' ? 'run' : 'flat'}">${esc(a)}</span>`).join(' ')}${v.aliases.includes('latest') ? ' <span class="chip ghost">latest</span>' : ''}</td><td>${v.run ? `<a class="mono" href="${WANDB}/runs/${v.run}" target="_blank" rel="noopener">${esc(v.run)} ↗</a>` : '—'}</td><td class="r num">${bytes(v.size)}</td></tr>`).join('')}${vs.length > 6 ? `<tr><td colspan="5" class="muted">${vs.length - 6} older versions (re-runs while week 4 was built)</td></tr>` : ''}</tbody></table></div>`;
    return `<div class="card"><div class="card-h"><h2 class="mono" style="font-family:var(--font-mono);font-size:15px">${esc(name)}</h2><span class="chip flat">${esc(type)}</span><span class="muted">${esc(note)}</span><div class="right"><a class="btn sm" href="${WANDB}/artifacts/${type}/${name}" target="_blank" rel="noopener">W&amp;B ↗</a></div></div>${body}</div>`;
  };
  const tile = (k, label) => { const v = prodOf(k); return `<div class="tile"><span class="k">${label} · production</span><span class="v">${v ? esc(v.version) : '—'}</span><span class="d">${v ? `${esc(v.aliases.filter(a => !['latest', 'production'].includes(a)).join(', '))} · <a class="mono" href="${WANDB}/runs/${v.run}" target="_blank" rel="noopener">${esc(v.run)}</a>` : 'first version on Tuesday\'s run'}</span></div>`; };
  return `<div class="tiles">${tile('game-model', 'game-model')}${tile('player-model', 'player-model')}${tile('team-model', 'team-model')}
      <div class="tile"><span class="k">Versions in all</span><span class="v">${Object.values(A).filter(Array.isArray).reduce((a, v) => a + v.length, 0)}</span><span class="d">across ${Object.values(A).filter(Array.isArray).length} live artifacts</span></div></div>
    <div class="card"><div class="card-h"><h2>What the week-4 digest was built from</h2><span class="muted">lineage</span></div><div class="card-b" style="display:grid;gap:10px">
      <div class="lineage"><span class="art"><b>model</b>game-model:v4</span><span class="arr">→</span><span class="art"><b>graph</b>graph-results:v1</span><span class="arr">→</span><span class="art"><b>model</b>player-model:v1</span><span class="arr">→</span><span class="art" style="border-color:var(--accent);background:var(--accent-wash)"><b>published</b>digest:v8</span></div>
      <span class="muted" style="font-size:12.5px">All four carry the alias <span class="mono">2026-w04</span>. From week 5 the pipeline run uses them in W&amp;B, so this chain comes straight from W&amp;B's lineage.</span></div></div>
    <div class="grid g2">
      ${card('game-model', 'model', 'weekly game fit', '')}
      ${card('player-model', 'model', 'all player stats, one artifact', '')}
      ${card('graph-results', 'graph', 'graph_results.json per build', '')}
      ${card('digest', 'digest', 'payload, checks, raw LLM output, digest.md', '')}
      ${card('team-model', 'model', 'team stat totals (P08)', "The first one comes from Tuesday's week-5 run.")}
      ${card('injury-update', 'digest', 'Saturday comparison', 'The first one comes from Saturday Oct 10.')}
    </div>
    <div class="foot">Read from the W&amp;B API on the server (read-only, cached). The W&amp;B key never reaches the browser.</div>`;
}
function w4Graph() {
  const g = D.w4_graph;
  const nc = Object.entries(g.node_counts).sort((a, b) => b[1] - a[1]);
  const selIds = new Set(g.selected.map(s => s.brief));
  const cand = g.candidates.filter(c => !selIds.has(c.brief)).slice(0, 10);
  const secName = s => s === 'matchup_risk' ? 'Matchup / risk' : 'Non-obvious';
  return `<div class="tiles">
      <div class="tile"><span class="k">Nodes</span><span class="v">${comma(g.nodes)}</span><span class="d">${nc.length} labels</span></div>
      <div class="tile"><span class="k">Relationships</span><span class="v">${comma(g.rels)}</span><span class="d">${g.mismatches} count mismatches</span></div>
      <div class="tile"><span class="k">Build</span><span class="v">${Math.round(g.timings.total_s)} <small>s</small></span><span class="d">wipe ${Math.round(g.timings.wipe_s)} s · load ${Math.round(g.timings.load_s)} s · queries ${g.timings.queries_s} s</span></div>
      <div class="tile"><span class="k">Insights used</span><span class="v">${g.selected.length} <small>of ${g.n_candidates}</small></span><span class="d">${g.skipped.started} skipped: game already started</span></div>
    </div>
    <div class="split">
      <div class="card"><div class="card-h"><h2>Used in the digest</h2><div class="right"><a class="btn sm" href="http://localhost:7474/browser/" target="_blank" rel="noopener" title="Opens on your machine">Neo4j Browser ↗</a></div></div><div class="card-b">
        ${g.selected.map(s => `<div class="insight"><div class="ih"><span class="im">${esc(s.matchup)}</span><span class="chip run">${secName(s.section)}</span><span class="chip flat mono">${esc(s.query)}</span><span class="meter">strength <i><b style="width:${s.strength * 100}%"></b></i> ${s.strength.toFixed(2)}</span><span class="chip flat">${esc(s.confidence)} confidence</span></div><p>${esc(s.brief)}</p></div>`).join('')}
      </div></div>
      <div class="card"><div class="card-h"><h2>Query results</h2><span class="muted">rows per query</span></div><div class="card-b">${hbarsHTML(Object.entries(g.queries).map(([k, v]) => ({ label: k.replace(/^q(\d+)_/, 'Q$1 ').replace(/_/g, ' '), v, tip: `${k}\n${v} rows` })), { max: Math.max(...Object.values(g.queries)), fmt: v => v, color: 'var(--s3)' })}</div></div>
    </div>
    <div class="card"><div class="card-h"><h2>Found but not used</h2><span class="muted">the next strongest candidates · the digest has room for a few</span></div>
      <div class="tablewrap"><table class="tbl"><thead><tr><th>Game</th><th>Kind</th><th>Query</th><th class="r">Strength</th><th>Finding</th></tr></thead><tbody>
        ${cand.map(c => `<tr><td style="white-space:nowrap"><b>${esc(c.matchup)}</b></td><td>${esc(c.type.replace(/_/g, ' '))}</td><td class="mono">${esc(c.query)}</td><td class="r num">${c.strength.toFixed(2)}</td><td style="min-width:320px">${esc(c.brief)}</td></tr>`).join('')}
      </tbody></table></div></div>
    <div class="card"><div class="card-h"><h2>Nodes by label</h2></div><div class="card-b">${hbarsHTML(nc.map(([k, v]) => ({ label: k, v, tip: `${k}\n${comma(v)} nodes` })), { max: nc[0][1], fmt: v => comma(v), color: 'var(--s2)' })}</div></div>`;
}

/* ------------------------------------------------------------------ season pages */
function seasonView() {
  const live = S.season === '2026';
  const W = D.s2025_weeks.filter(w => w.week <= 18);
  const last = W[W.length - 1];
  const cal = D.s2025_cal;
  const N = cal.reduce((a, c) => a + c.n, 0);
  const ece = cal.reduce((a, c) => a + c.n * Math.abs(c.pred - c.obs), 0) / N;
  const groups = [...new Set(D.s2025_player.map(x => x.group))];
  const gAvg = groups.map(gr => { const xs = D.s2025_player.filter(x => x.group === gr); return { gr, v: xs.reduce((a, x) => a + x.imp, 0) / xs.length }; }).sort((a, b) => b.v - a.v);
  const sb26 = D.sb2026.filter(x => x.improvement_pct != null);
  const g26 = [...new Set(sb26.map(x => x.position_group))].map(gr => { const xs = sb26.filter(x => x.position_group === gr); const w = xs.reduce((a, x) => a + x.n_scored, 0); return { gr, v: xs.reduce((a, x) => a + x.improvement_pct * x.n_scored, 0) / w }; }).sort((a, b) => b.v - a.v);
  const pick = `<div class="sec-h"><span class="seg" id="seasonPick"><button type="button" data-season="2026" aria-pressed="${live}">2026 live</button><button type="button" data-season="2025" aria-pressed="${!live}">2025 backtest (example)</button></span><p>${live ? 'Only week 4 is live so far, and it gets graded by Tuesday\'s run.' : 'The 2025 walk-forward backtest, drawn the way the app will draw 2026 by January.'}</p></div>`;
  if (live) {
    return pick + `<div class="tiles">
        <div class="tile"><span class="k">Games graded</span><span class="v">0</span><span class="d">week 4 is graded Tuesday</span></div>
        <div class="tile"><span class="k">Weeks published</span><span class="v">1</span><span class="d">week 4 (late, first live week)</span></div>
        <div class="tile"><span class="k">Checks pass rate</span><span class="v">100%</span><span class="d">1 of 1 digests</span></div>
        <div class="tile"><span class="k">LLM spend</span><span class="v">$${totalLLM().toFixed(3)}</span><span class="d">season to date</span></div>
      </div>
      <div class="grid g2">
        <div class="card"><div class="card-h"><h2>Season-to-date Brier</h2></div><div class="card-b"><div class="empty" style="padding:30px 10px"><div class="glyph">—</div><p>Starts with Tuesday's run, which grades week 4's 16 games against the model, Elo and the market.</p></div></div></div>
        <div class="card"><div class="card-h"><h2>Player model vs baseline</h2><span class="muted">weeks 1–3, walk-forward rows · real</span></div><div class="card-b">${hbarsHTML(g26.map(x => ({ label: x.gr, v: x.v, tip: `${x.gr}\n${x.v.toFixed(1)}% better than the rolling baseline (weeks 1–3)` })), { max: Math.max(...g26.map(x => x.v)) * 1.1, fmt: v => '+' + v.toFixed(1) + '%', color: 'var(--s1)' })}</div></div>
      </div>
      <div class="card"><div class="card-h"><h2>Pipeline health</h2><span class="muted">one row per weekly run</span></div><div class="tablewrap"><table class="tbl"><thead><tr><th>Week</th><th>Status</th><th>On time</th><th>Checks</th><th class="r">Run time</th><th class="r">Drift alerts</th></tr></thead><tbody>
        <tr><td><b>4</b></td><td><span class="chip ok">✓ published</span></td><td><span class="chip warn">late · first live week</span></td><td>passed after 1 rewrite</td><td class="r num">${dur(D.w4_steps.reduce((a, s) => a + s.seconds, 0))}</td><td class="r num">0</td></tr>
        <tr><td><b>5</b></td><td colspan="5" class="muted">Tuesday Oct 6</td></tr>
      </tbody></table></div></div>`;
  }
  return pick + `<div class="tiles">
      <div class="tile"><span class="k">Season Brier · model</span><span class="v">${last.cum_model.toFixed(4)}</span><span class="d">market-informed, as shown</span></div>
      <div class="tile"><span class="k">Elo</span><span class="v">${last.cum_elo.toFixed(4)}</span><span class="d">model better by ${(last.cum_elo - last.cum_model).toFixed(4)}</span></div>
      <div class="tile"><span class="k">Market</span><span class="v">${last.cum_market.toFixed(4)}</span><span class="d">${last.cum_model <= last.cum_market ? 'model ahead' : 'market ahead'} by ${Math.abs(last.cum_market - last.cum_model).toFixed(4)}</span></div>
      <div class="tile"><span class="k">Pick accuracy</span><span class="v">${pct(last.cum_acc, 1)}</span><span class="d">${comma(W.reduce((a, w) => a + w.n, 0))} regular-season games</span></div>
      <div class="tile"><span class="k">Calibration error</span><span class="v">${ece.toFixed(3)}</span><span class="d">ECE, 10 bins, incl. playoffs</span></div>
    </div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h2>Season-to-date Brier</h2><span class="muted">lower is better · weeks 1–18</span></div><div class="card-b">
        <div class="legend" style="margin-bottom:6px"><span><i class="line" style="background:var(--s1)"></i>Model</span><span><i class="line" style="background:var(--s2)"></i>Elo</span><span><i class="line" style="background:var(--s3)"></i>Market</span></div>
        ${lineChart({ label: 'Season-to-date Brier score by week, 2025', xs: W.map(w => w.week), xFmt: x => 'Week ' + x, fmt: v => v.toFixed(4), series: [{ name: 'Model', color: 'var(--s1)', vals: W.map(w => w.cum_model) }, { name: 'Elo', color: 'var(--s2)', vals: W.map(w => w.cum_elo) }, { name: 'Market', color: 'var(--s3)', vals: W.map(w => w.cum_market) }] })}
      </div></div>
      <div class="card"><div class="card-h"><h2>Pick accuracy</h2><span class="muted">season to date</span></div><div class="card-b">
        ${lineChart({ label: 'Season-to-date pick accuracy, 2025', xs: W.map(w => w.week), xFmt: x => 'Week ' + x, fmt: v => (v * 100).toFixed(1) + '%', yFmt: v => Math.round(v * 100) + '%', area: true, series: [{ name: 'Accuracy', color: 'var(--s1)', vals: W.map(w => w.cum_acc) }] })}
      </div></div>
      <div class="card"><div class="card-h"><h2>Calibration</h2><span class="muted">predicted home win chance vs how often it happened</span></div><div class="card-b">${calChart(cal)}</div></div>
      <div class="card"><div class="card-h"><h2>Player model vs baseline</h2><span class="muted">2025 average MAE improvement by group</span></div><div class="card-b">${hbarsHTML(gAvg.map(x => ({ label: x.gr, v: x.v, tip: `${x.gr}\n${x.v.toFixed(1)}% better than the rolling baseline` })), { max: Math.max(...gAvg.map(x => x.v)) * 1.1, fmt: v => '+' + v.toFixed(1) + '%', color: 'var(--s1)' })}</div></div>
    </div>
    <div class="foot">Weekly numbers come from <span class="mono">runs/backtests/game/market</span> and the player scoreboard; the app reads the same files the W&amp;B Season Dashboard is built from.</div>`;
}
document.addEventListener('click', e => { const b = e.target.closest('[data-season]'); if (b) { S.season = b.dataset.season; render(); } });

function teamsView() {
  const weeks = D.elo_weeks; const li = weeks.length - 1;
  let rows = Object.entries(D.elo).map(([t, v]) => ({ t, elo: v[li], prev: v[li - 1], hist: v, r: D.ratings_w4[t] || {} }));
  if (S.conf !== 'All') rows = rows.filter(x => team(x.t).conf === S.conf);
  rows.sort((a, b) => b.elo - a.elo);
  const allSorted = Object.entries(D.elo).map(([t, v]) => ({ t, elo: v[li], prev: v[li - 1] }));
  const rankNow = [...allSorted].sort((a, b) => b.elo - a.elo).map(x => x.t);
  const rankPrev = [...allSorted].sort((a, b) => b.prev - a.prev).map(x => x.t);
  const movers = allSorted.map(x => ({ t: x.t, d: x.elo - x.prev })).sort((a, b) => b.d - a.d);
  const spark = v => { const mn = Math.min(...v), mx = Math.max(...v); const P = v.map((y, i) => `${4 + i * (72 / (v.length - 1))},${22 - (mx === mn ? 9 : (y - mn) / (mx - mn) * 18)}`); const last = P[P.length - 1].split(','); return `<svg width="84" height="26" viewBox="0 0 84 26"><polyline points="${P.join(' ')}" fill="none" style="stroke:var(--s2)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${last[0]}" cy="${last[1]}" r="3.5" style="fill:var(--s2);stroke:var(--panel)" stroke-width="2"/></svg>`; };
  const body = rows.map(x => {
    const rk = rankNow.indexOf(x.t) + 1, mv = rankPrev.indexOf(x.t) + 1 - rk;
    const open = S.openTeam === x.t;
    const w5 = D.w5_slate.find(g => g.home === x.t || g.away === x.t);
    const w4 = D.w4_games.find(g => g.home === x.t || g.away === x.t);
    return `<tr class="teamrow" data-team="${x.t}" aria-expanded="${open}"><td class="num"><b>${rk}</b></td><td>${tchip(x.t, true)} <span class="muted">${esc(team(x.t).div)}</span></td><td class="r num"><b>${Math.round(x.elo)}</b></td><td class="r num">${mv > 0 ? `<span class="mover-up">▲ ${mv}</span>` : mv < 0 ? `<span class="mover-down">▼ ${-mv}</span>` : '<span class="muted">—</span>'}</td><td>${spark(x.hist)}</td><td class="r num">${sgn(x.r.net, 3)}</td><td class="r num">${sgn(x.r.off, 3)}</td><td class="r num">${sgn(x.r.def, 3)}</td></tr>
      ${open ? `<tr class="teamdetail"><td colspan="8"><div class="grid g3" style="padding:6px 4px">
        <div><span class="eyebrow">Elo, weeks 1–4</span><div style="font-size:13px;margin-top:4px">${x.hist.map((v, i) => `W${weeks[i]} <b>${Math.round(v)}</b>`).join(' · ')}</div></div>
        <div><span class="eyebrow">Week 4</span><div style="font-size:13px;margin-top:4px">${w4 ? `${tchip(w4.away)} at ${tchip(w4.home)} · model gave ${esc(team(x.t).nick)} <b>${pct(w4.home === x.t ? w4.p_home : 1 - w4.p_home)}</b>` : 'bye'}</div></div>
        <div><span class="eyebrow">Week 5</span><div style="font-size:13px;margin-top:4px">${w5 ? `${tchip(w5.away)} at ${tchip(w5.home)} · ${esc(kick(w5.kick))}` : '<b>bye</b>'}</div></div>
        <div><span class="eyebrow">Pass vs rush (net EPA/play)</span><div style="font-size:13px;margin-top:4px">pass ${sgn(x.r.pass, 3)} · rush ${sgn(x.r.rush, 3)}</div></div>
      </div></td></tr>` : ''}`;
  }).join('');
  return `<div class="grid g2">
      <div class="card"><div class="card-h"><h2>Biggest risers</h2><span class="muted">Elo change, entering week 3 → week 4</span></div><div class="card-b">${hbarsHTML(movers.slice(0, 5).map(x => ({ label: team(x.t).nick, v: x.d, tip: `${team(x.t).name}\n${sgn(x.d, 1)} Elo` })), { max: Math.max(...movers.map(m => Math.abs(m.d))), fmt: v => sgn(v, 1), color: 'var(--ok)' })}</div></div>
      <div class="card"><div class="card-h"><h2>Biggest fallers</h2><span class="muted">Elo change, entering week 3 → week 4</span></div><div class="card-b">${hbarsHTML(movers.slice(-5).reverse().map(x => ({ label: team(x.t).nick, v: Math.abs(x.d), tip: `${team(x.t).name}\n${sgn(x.d, 1)} Elo` })), { max: Math.max(...movers.map(m => Math.abs(m.d))), fmt: v => '−' + v.toFixed(1), color: 'var(--err)' })}</div></div>
    </div>
    <div class="card"><div class="card-h"><h2>Power rankings</h2><span class="muted">by Elo entering week ${weeks[li]} · click a team for detail</span><div class="right filters">${['All', 'AFC', 'NFC'].map(c => `<button class="fchip" data-conf="${c}" aria-pressed="${S.conf === c}">${c}</button>`).join('')}</div></div>
      <div class="tablewrap"><table class="tbl"><thead><tr><th>#</th><th>Team</th><th class="r">Elo</th><th class="r">Move</th><th>Weeks 1–4</th><th class="r">Net EPA</th><th class="r">Off EPA</th><th class="r">Def EPA allowed</th></tr></thead><tbody>${body}</tbody></table></div></div>
    <div class="foot">Ratings come from <span class="mono">features/team_elo.parquet</span> and <span class="mono">team_ratings.parquet</span> as of the week-4 run; Tuesday's run adds week 5.</div>`;
}
document.addEventListener('click', e => {
  const c = e.target.closest('[data-conf]'); if (c) { S.conf = c.dataset.conf; render(); return; }
  const t = e.target.closest('[data-team]'); if (t) { S.openTeam = S.openTeam === t.dataset.team ? null : t.dataset.team; render(); }
});

function modelsView() {
  const P6 = [['rec_yds · WR/TE', 9.2], ['rush_yds · RB', 7.9], ['scrim_yds · RB', 7.4], ['carries · RB', 6.9], ['targets · WR/TE', 5.2], ['pass_yds · QB', 5.0], ['tackles · LB/S', 4.8], ['pass_epa · QB', 4.7], ['receptions · WR/TE', 3.4], ['pressures · EDGE', 3.0], ['receptions · RB', 2.7]];
  return `<div class="grid g2">
    <div class="card"><div class="card-h"><h2>Game model</h2><div class="right"><span class="chip run">production · v0</span></div></div><div class="card-b" style="display:grid;gap:12px">
      <div class="tiles"><div class="tile"><span class="k">Model only</span><span class="v">0.2199</span><span class="d">vs Elo 0.2221 · 2018–2025</span></div><div class="tile"><span class="k">Market-informed</span><span class="v">0.2102</span><span class="d">vs closing market 0.2104</span></div></div>
      <div class="kv"><dt>Version</dt><dd class="mono">game-model-v0:2026-w04</dd><dt>Refit</dt><dd>every Tuesday, walk-forward</dd><dt>v1 (trees on top)</dt><dd>evaluated, not promoted (D79)</dd></div>
      <button class="btn sm" title="In the app this opens the model card" aria-disabled="true">Model card</button></div></div>
    <div class="card"><div class="card-h"><h2>Team ratings &amp; Elo</h2><div class="right"><span class="chip run">production</span></div></div><div class="card-b" style="display:grid;gap:12px">
      <div class="tiles"><div class="tile"><span class="k">Rating MSE</span><span class="v">0.1041</span><span class="d">vs 0.1194 / 0.1249 baselines</span></div><div class="tile"><span class="k">Elo Brier</span><span class="v">0.2214</span><span class="d">walk-forward, 11 seasons</span></div></div>
      <div class="kv"><dt>Settings</dt><dd>half-life 12 · prior 0.1 · alpha 250 (D46)</dd><dt>Trends</dt><dd>descriptive only (D47)</dd></div></div></div>
    <div class="card"><div class="card-h"><h2>Player models</h2><div class="right"><span class="chip run">production · v1</span><span class="chip flat">23 live stats</span></div></div><div class="card-b">
      <p class="muted" style="margin:0 0 10px;font-size:13px">MAE improvement over the rolling baseline, 2019–2025 walk-forward. All 11 P06 stats beat it in 7 of 7 seasons; the 12 P08 stats gain 2.0–9.7% (MAE) or 2.6–8.8% (Brier).</p>
      ${hbarsHTML(P6.map(([l, v]) => ({ label: l, v, tip: `${l}\n+${v}% vs the rolling baseline` })), { max: 10, fmt: v => '+' + v.toFixed(1) + '%', color: 'var(--s1)' })}</div></div>
    <div class="card"><div class="card-h"><h2>Team stat totals &amp; the digest writer</h2></div><div class="card-b" style="display:grid;gap:14px">
      <div class="kv"><dt>Team totals</dt><dd>4 of 5 shipped (D80)</dd><dt>Pass yards</dt><dd>+2.5% vs baseline</dd><dt>Rush yards</dt><dd>+1.6%</dd><dt>Sacks made / taken</dt><dd>tie on MAE, −1.7% deviance</dd><dt>Takeaways</dt><dd>not shipped (−0.1%)</dd></div>
      <div class="kv"><dt>Writer</dt><dd class="mono">z-ai/glm-5.3-flash</dd><dt>Route</dt><dd>OpenRouter · BaseTen → Novita → Relace (D88)</dd><dt>Prompt</dt><dd class="mono">7a529f64c5a6</dd><dt>Checks</dt><dd>11 (provenance, binding, meaning, length …)</dd></div>
    </div></div>
  </div>`;
}
function alertsView() {
  const when = [['data_freshness', 'any run', 1], ['checks', "week 8's run (4 weeks of digests)", 8], ['player_vs_baseline', "week 7's run", 7], ['calibration', 'about week 8 (64 graded games)', 8], ['game_vs_elo', "week 10's run", 10]];
  return `<div class="card"><div class="empty"><div class="glyph">0</div><h3>No alerts in 2026 yet</h3><p>Alerts come from a failed step, a "not ready" past the retry window, a degraded step, a digest that failed its checks, a lock left behind, and the five drift signals. They also go to W&amp;B's alerts (email or app).</p></div></div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h2>When each signal can first fire</h2><span class="muted">2026 season</span></div><div class="card-b">
        <div style="position:relative;padding:6px 0 4px">${(() => { const X5 = w => ((w - 4) / (18 - 4)) * 100; return `<div style="position:relative;height:28px;border-bottom:1px solid var(--line-2)">${[4, 6, 8, 10, 12, 14, 16, 18].map(w => `<span style="position:absolute;left:${X5(w)}%;bottom:-18px;transform:translateX(-50%);font-size:11px;color:var(--ink-3)">W${w}</span>`).join('')}<span style="position:absolute;left:${X5(5)}%;top:0;bottom:0;border-left:2px solid var(--accent)"></span><span style="position:absolute;left:calc(${X5(5)}% + 6px);top:2px;font-size:11px;font-weight:600;color:var(--accent-ink)">this week</span></div>` + when.map(w => `<div style="display:grid;grid-template-columns:150px minmax(0,1fr);gap:10px;align-items:center;margin-top:${w[0] === 'data_freshness' ? 26 : 10}px"><span class="mono">${w[0]}</span><div style="position:relative;height:14px"><span style="position:absolute;left:${X5(Math.max(4, w[2]))}%;right:0;top:4px;height:6px;border-radius:3px;background:color-mix(in srgb,var(--s2) 35%,transparent)" data-tip="${w[0]}\nfrom ${w[1]}"></span><span style="position:absolute;left:${X5(Math.max(4, w[2]))}%;top:0;width:12px;height:12px;border-radius:50%;transform:translateX(-50%);background:var(--s2);box-shadow:0 0 0 2px var(--panel)"></span></div></div>`).join(''); })()}</div>
      </div></div>
      <div class="card"><div class="card-h"><h2>What an alert looks like</h2><span class="chip warn">example · 2025 week-10 simulation</span></div><div class="card-b" style="display:grid;gap:10px">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="chip warn"><span class="ic">!</span>warn</span><b class="mono">calibration</b><span class="muted">2025 week 10 · simulation</span></div>
        <p style="margin:0">ECE 0.053 over 135 games, above the flat 0.05 limit.</p>
        <div class="notice" style="font-size:12.5px"><span class="ic">→</span><div><b>Investigation:</b> a perfectly calibrated model averages 0.082 on that many games, so the flat limit sat below the noise floor. <b>Decision D75:</b> the limit became the noise level, falling from about 0.16 after five weeks to 0.08 by week 18.</div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn sm" aria-disabled="true" title="In the app: opens the run that raised it">Open the run</button><button class="btn sm" aria-disabled="true" title="In the app: adds an investigation note">Add a note</button></div>
      </div></div>
    </div>`;
}
function healthView() {
  const svc = [['Neo4j', 'up · 5.26.31 Community · GDS 2.13.13 · APOC', 'ok'], ['Docker Desktop', 'running · container nfl-neo4j', 'ok'], ['Weights & Biases', 'reachable · project nfl-analytics-engine', 'ok'], ['OpenRouter', 'routing check ok · BaseTen first', 'ok'], ['Data drive', 'D:\\nfl-ml-data · raw, curated, features, models, runs, reports', 'ok'], ['Run lock', S.w5 === 'running' ? 'held by the week-5 run' : 'free', S.w5 === 'running' ? 'run' : 'ok']];
  const keys = [['NFL_DATA_ROOT', 'set'], ['NEO4J_PASSWORD', 'set'], ['WANDB_API_KEY', 'set'], ['OPENROUTER_API_KEY', 'set'], ['ODDS_API_KEY', 'set'], ['ODDS_API_KEY2', 'set'], ['KAGGLE_USERNAME', 'not set'], ['KAGGLE_KEY', 'not set']];
  return `<div class="notice"><span class="ic">i</span><div>Statuses here are <b>samples</b> for the mockup. In the app they come from <span class="mono">nfl doctor</span> and <span class="mono">nfl data-status</span>, which report <i>set / not set / connection OK</i> and never show a value. Keys stay on the server and never reach the browser.</div></div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h2>Services</h2></div><div class="tablewrap"><table class="tbl"><tbody>${svc.map(s => `<tr><td><b>${esc(s[0])}</b></td><td class="muted">${esc(s[1])}</td><td class="c"><span class="chip ${s[2]}">${s[2] === 'ok' ? '✓ ok' : '● busy'}</span></td></tr>`).join('')}</tbody></table></div></div>
      <div class="card"><div class="card-h"><h2>Keys</h2><span class="muted">names only</span></div><div class="tablewrap"><table class="tbl"><tbody>${keys.map(k => `<tr><td class="mono">${k[0]}</td><td class="c"><span class="chip ${k[1] === 'set' ? 'ok' : 'ghost'}">${k[1] === 'set' ? '✓ set' : 'not set · optional'}</span></td></tr>`).join('')}</tbody></table></div></div>
    </div>
    <div class="card"><div class="card-h"><h2>Recent runs</h2><span class="muted">pipeline_history.parquet</span></div><div class="tablewrap"><table class="tbl"><thead><tr><th>When</th><th>Command</th><th>Status</th><th>Launched by</th></tr></thead><tbody>
      ${S.w5 === 'published' || S.w5 === 'failed' ? `<tr><td class="num">Tue Oct 6 10:00</td><td class="mono">nfl weekly run --auto --expect-week 5</td><td><span class="chip ${S.w5 === 'failed' ? 'err' : 'ok'}">${S.w5 === 'failed' ? '✕ failed' : '✓ ok'}</span></td><td>rishi (control room)</td></tr>` : ''}
      <tr><td class="num">Sun Oct 4 04:23</td><td class="mono">nfl weekly run --season 2026 --week 4 --from-step digest</td><td><span class="chip ok">✓ ok</span></td><td>agent</td></tr>
      <tr><td class="num">Sun Oct 4 03:59</td><td class="mono">nfl weekly run --season 2026 --week 4 --from-step player --promote</td><td><span class="chip ok">✓ ok</span></td><td>agent</td></tr>
    </tbody></table></div></div>`;
}

/* ------------------------------------------------------------------ charts */
const CH = {}; let CHN = 0;
function niceTicks(lo, hi, n) {
  const span = hi - lo || 1; const raw = span / n; const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || 10 * mag;
  const out = []; for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) out.push(+v.toFixed(10));
  return out;
}
function lineChart(o) {
  const id = 'c' + (++CHN);
  const W = 620, H = o.h || 230, m = { l: 50, r: 18, t: 12, b: 28 };
  const all = o.series.flatMap(s => s.vals.filter(v => v != null));
  const ticks = niceTicks(Math.min(...all), Math.max(...all), 4);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const n = o.xs.length;
  const X = i => m.l + i * (W - m.l - m.r) / Math.max(1, n - 1);
  const Y = v => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
  const yF = o.yFmt || o.fmt;
  let s = `<g class="grid">${ticks.map(t => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/>`).join('')}</g>`;
  s += ticks.map(t => `<text x="${m.l - 8}" y="${Y(t) + 4}" text-anchor="end">${esc(yF(t))}</text>`).join('');
  const every = n > 12 ? 3 : 1;
  s += o.xs.map((x, i) => i % every === 0 || i === n - 1 ? `<text x="${X(i)}" y="${H - 8}" text-anchor="middle">${esc(x)}</text>` : '').join('');
  o.series.forEach(se => {
    const pts = se.vals.map((v, i) => v == null ? null : `${X(i)},${Y(v)}`).filter(Boolean);
    if (o.area) s += `<polygon points="${X(0)},${Y(y0)} ${pts.join(' ')} ${X(n - 1)},${Y(y0)}" style="fill:${se.color}" opacity=".10"/>`;
    s += `<polyline points="${pts.join(' ')}" fill="none" style="stroke:${se.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    const lv = se.vals[n - 1];
    if (lv != null) s += `<circle cx="${X(n - 1)}" cy="${Y(lv)}" r="4" style="fill:${se.color};stroke:var(--panel)" stroke-width="2"/>`;
  });
  s += `<line class="xh" id="${id}-xh" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" visibility="hidden"/>`;
  s += `<g id="${id}-dots"></g>`;
  s += `<rect x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="transparent" data-hover="${id}"/>`;
  CH[id] = { o, X, Y, W, n, m };
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.label)}">${s}</svg></div>`;
}
function calChart(cal) {
  const W = 620, H = 260, m = { l: 50, r: 18, t: 12, b: 34 };
  const X = v => m.l + v * (W - m.l - m.r), Y = v => m.t + (1 - v) * (H - m.t - m.b);
  let s = `<g class="grid">${[0, .25, .5, .75, 1].map(t => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/>`).join('')}</g>`;
  s += [0, .25, .5, .75, 1].map(t => `<text x="${m.l - 8}" y="${Y(t) + 4}" text-anchor="end">${t * 100}%</text><text x="${X(t)}" y="${H - 14}" text-anchor="middle">${t * 100}%</text>`).join('');
  s += `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(1)}" y2="${Y(1)}" style="stroke:var(--ink-3)" stroke-width="1.5" stroke-dasharray="4 4"/>`;
  s += `<text x="${X(0.97)}" y="${Y(0.97) + 16}" text-anchor="end" class="lbl">perfect calibration</text>`;
  s += `<polyline points="${cal.map(c => `${X(c.pred)},${Y(c.obs)}`).join(' ')}" fill="none" style="stroke:var(--s1)" stroke-width="2" stroke-linejoin="round"/>`;
  const mx = Math.max(...cal.map(c => c.n));
  s += cal.map(c => `<circle cx="${X(c.pred)}" cy="${Y(c.obs)}" r="${4 + 6 * Math.sqrt(c.n / mx)}" style="fill:var(--s1);stroke:var(--panel)" stroke-width="2" data-tip="Predicted ${pct(c.pred, 1)}\nHappened ${pct(c.obs, 1)}\n${c.n} games"/>`).join('');
  s += `<text x="${W - m.r}" y="${H - 1}" text-anchor="end">predicted home win chance →</text>`;
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Calibration curve for 2025">${s}</svg></div>`;
}
function hbarsHTML(rows, o) {
  return `<div style="display:grid;grid-template-columns:minmax(80px,max-content) minmax(0,1fr) auto;gap:8px 12px;align-items:center;font-size:13px">${rows.map(r => `<span class="dim" style="white-space:nowrap">${esc(r.label)}</span><span style="position:relative;height:16px"><span style="position:absolute;left:0;top:2px;height:12px;width:${Math.max(0.6, Math.abs(r.v) / o.max * 100)}%;max-width:100%;background:${o.color};border-radius:0 4px 4px 0" data-tip="${esc(r.tip || r.label)}"></span></span><span class="num" style="text-align:right;white-space:nowrap">${esc(o.fmt(r.v))}</span>`).join('')}</div>`;
}
function wireCharts() {
  $$('[data-hover]').forEach(rect => {
    const c = CH[rect.dataset.hover]; if (!c) return;
    const svg = rect.ownerSVGElement;
    const xh = svg.getElementById(rect.dataset.hover + '-xh');
    const dots = svg.getElementById(rect.dataset.hover + '-dots');
    const move = e => {
      const b = svg.getBoundingClientRect();
      const vx = (e.clientX - b.left) / b.width * c.W;
      const i = Math.max(0, Math.min(c.n - 1, Math.round((vx - c.m.l) / ((c.W - c.m.l - c.m.r) / Math.max(1, c.n - 1)))));
      const x = c.X(i);
      xh.setAttribute('x1', x); xh.setAttribute('x2', x); xh.setAttribute('visibility', 'visible');
      dots.innerHTML = c.o.series.map(s => s.vals[i] == null ? '' : `<circle cx="${x}" cy="${c.Y(s.vals[i])}" r="4.5" style="fill:${s.color};stroke:var(--panel)" stroke-width="2"/>`).join('');
      showTip(`<div class="th">${esc(c.o.xFmt(c.o.xs[i]))}</div>${c.o.series.map(s => `<div class="tr"><span><i style="background:${s.color}"></i>${esc(s.name)}</span><b class="num">${esc(c.o.fmt(s.vals[i]))}</b></div>`).join('')}`, e.clientX, e.clientY);
    };
    rect.addEventListener('pointermove', move);
    rect.addEventListener('pointerleave', () => { xh.setAttribute('visibility', 'hidden'); dots.innerHTML = ''; hideTip(); });
  });
}
const tip = $('#tip');
function showTip(html, x, y) {
  tip.innerHTML = html; tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > innerHeight - 8) top = y - r.height - 14;
  tip.style.left = Math.max(8, left) + 'px'; tip.style.top = Math.max(8, top) + 'px';
}
function hideTip() { tip.hidden = true; }
document.addEventListener('pointermove', e => {
  const t = e.target.closest && e.target.closest('[data-tip]');
  if (t) { const [h, ...rest] = t.dataset.tip.split('\n'); showTip(`<div class="th">${esc(h)}</div>${rest.map(l => `<div>${esc(l)}</div>`).join('')}`, e.clientX, e.clientY); }
  else if (!e.target.closest || !e.target.closest('[data-hover]')) hideTip();
});

/* ------------------------------------------------------------------ boot */
syncMockbar();
render();
})();
