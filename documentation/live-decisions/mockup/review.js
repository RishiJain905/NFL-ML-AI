/* ------------------------------------------------------------------ the decision review (LD03)
   Included into gameday.js's scope by build_mockup.py (the `@review.js` line), so it shares its
   helpers (esc, tchip, p0, p1, pts, confChip, tDay, tMin, mockOnly, S, P, render ...). The data
   is P.review: the real app's answers (dump_payloads.py's review block) in the shapes of
   web/src/api/types.ts's LD03 block: `week_<S>_<W>` (LiveReviewResponse) and `season_<S>_<W>`
   (LiveSeasonReviewResponse, through that week). nflverse's play text (`desc`) is text only. */

const RV = P.review;
const RV_WEEKS = [['2026_4', '2026 wk 4'], ['2026_1', '2026 wk 1'], ['2026_5', '2026 wk 5 · no plays'], ['2025_18', '2025 wk 18'], ['2025_22', '2025 Super Bowl']];
const RV_NO_MODELS = "The decision models aren't trained yet (LD00): run `uv run nfl live train --promote`.";
const CN = { go: 'Go', fg: 'Field goal', punt: 'Punt' };
const BN = { go: 'Go for it', fg: 'Field goal', punt: 'Punt' };
const CALL_C = { go: 'var(--s1)', fg: 'var(--s2)', punt: 'var(--s3)' };
const EDGE_MAX = 0.06; // the edge bar is full at 6 points
const PLAYOFF = { 19: ['Wild card', 'WC'], 20: ['Divisional round', 'DIV'], 21: ['Conference title games', 'CONF'], 22: ['Super Bowl', 'SB'] };
const weekName = (season, w) => (season >= 2021 && PLAYOFF[w] ? PLAYOFF[w][0] : `Week ${w}`);
const weekShort = (season, w) => (season >= 2021 && PLAYOFF[w] ? PLAYOFF[w][1] : String(w));
const THROUGH = { 19: 'the wild-card round', 20: 'the divisional round', 21: 'the conference title games', 22: 'the Super Bowl' };
const throughText = (season, w) => (season >= 2021 && THROUGH[w] ? THROUGH[w] : `week ${w}`);
const wins = v => (v == null ? '—' : v.toFixed(2));
const signed = e => (e == null ? '—' : `${e < 0 ? '−' : '+'}${pts(Math.abs(e))}`);
const pkey = p => `${p.game_id}-${p.play_id}`;
const qc = p => `${ordQ(p.qtr)} ${p.clock || ''}`.trim();
const lead = p => (p.off_score === p.def_score ? 'tied' : `${p.posteam} ${p.off_score > p.def_score ? 'up' : 'down'} ${Math.abs(p.off_score - p.def_score)}`);
const fgText = p => (p.fg_make == null ? `out of range (${Math.round(p.fg_distance)} yards)` : `${p0(p.fg_make)} from ${Math.round(p.fg_distance)} yards`);
const tags = p => `${p.fake ? '<span class="ptag" title="A fake punt or field goal counts as going for it">fake</span>' : ''}${p.wiped ? '<span class="ptag" title="Wiped out by a penalty after the snap; the down wasn\'t replayed">wiped</span>' : ''}`;

/* ---------- which answers the picked state shows ---------- */
function rvWeekData() {
  if (S.gd === 'final') return S.rvState === 'noplays' ? RV.final_no_plays : RV.week_2026_4;
  return RV['week_' + S.rvWeek];
}
function rvSeasonData() {
  if (S.gd === 'final') return RV.season_2026_4;
  return RV['season_' + S.rvWeek];
}
// the season's weeks the dump holds (the mockup's week stepper jumps between these)
function dumpedWeeks(season) {
  return RV_WEEKS.map(([k]) => k.split('_').map(Number)).filter(([s]) => s === season).map(([, w]) => w).sort((a, b) => a - b);
}

/* ---------- the tab body ---------- */
function reviewView(inFinal) {
  const wk = rvWeekData();
  let h = `<div class="rv">`;
  if (inFinal) h += `<div><button class="btn sm" data-rvback="1">← The week's games</button></div>`;
  h += rvHead(wk);
  if (S.rvState === 'loading') return h + rvLoading() + '</div>';
  if (S.rvState === 'nomodels') {
    return h + mockOnly('The real bundle is loaded; this is the answer without one (status <code class="mono">no_models</code>).') +
      `<div class="notice warn" role="status"><span class="ic warn">!</span><div><b>${esc(RV_NO_MODELS)}</b> The review scores every 4th down with the decision models, so it appears once a bundle is promoted. The week's plays are already in.</div></div></div>`;
  }
  if (S.rvView === 'season') return h + seasonBody(rvSeasonData(), wk) + '</div>';
  if (wk.status === 'no_plays') return h + rvNoPlays(wk) + '</div>';
  return h + rvSummary(wk) + rvHighlights(wk) + rvList(wk) + '</div>';
}

function rvHead(wk) {
  const season = wk.season, week = wk.week;
  const sv = S.rvView === 'season';
  const sd = rvSeasonData();
  const model = (sv ? sd.model : wk.model) || sd.model;
  const through = sd.through_week;
  const title = sv
    ? `The ${season} season${through ? ` through ${throughText(season, through)}` : ''}`
    : 'Every 4th down: the coach’s call next to the bot’s';
  const src = sv ? sd.source : wk.source;
  const srcText = { file: 'from <span class="mono">nfl live review</span>’s file', cache: 'from the app’s cache', computed: 'computed on first open', memory: 'computed this session' }[src] || '';
  const meta = [];
  if (model && model.version) meta.push(`<span>Decision models <span class="mono">${esc(model.version)}</span></span>`);
  meta.push('<span>nflverse play-by-play, not ESPN</span>');
  if (model && model.in_sample === false && model.trained_seasons) meta.push(`<span><b>Out of sample</b>: trained on ${model.trained_seasons[0]}–${model.trained_seasons[1]}</span>`);
  if (srcText) meta.push(`<span>${srcText}</span>`);
  const steps = sv ? '' : weekStepper(season, week);
  let out = `<div class="rv-h"><div class="ttl"><span class="eyebrow">Decision review · ${season} ${esc(weekName(season, week))}</span><h2>${esc(title)}</h2><div class="rv-meta">${meta.join('')}</div></div>
    <div class="right">${steps}<span class="seg" role="group" aria-label="Review of"><button type="button" data-rv="week" aria-pressed="${!sv}">This week</button><button type="button" data-rv="season" aria-pressed="${sv}">Season</button></span></div></div>`;
  if (model && model.in_sample) {
    out += `<div class="notice warn" role="note"><span class="ic warn">!</span><div><b>In-sample season.</b> The decision models were trained on ${model.trained_seasons ? `${model.trained_seasons[0]}–${model.trained_seasons[1]}` : 'seasons that include this one'}, ${season} included: they have seen these plays, so the calibration below flatters the bot and its calls agree with what happened more than they would live. 2026 is the first season the models haven't seen.</div></div>`;
  }
  return out;
}

function weekStepper(season, week) {
  // in the app: the previous and next week (next up to Game day's week); in the mockup: the weeks the dump has
  const ws = dumpedWeeks(season);
  const prev = ws.filter(w => w < week).pop();
  const next = ws.find(w => w > week);
  const b = (w, txt) => `<button type="button" class="btn sm" data-rvweek="${season}_${w}" title="Mockup: jumps to the weeks the dump has; the app steps one week">${txt}</button>`;
  return `<span class="wkstep">${prev ? b(prev, `‹ ${esc(weekName(season, prev))}`) : ''}${next ? b(next, `${esc(weekName(season, next))} ›`) : ''}</span>`;
}

function rvLoading() {
  return mockOnly('The real answers came back at once (the app had them cached); this is what a first open looks like.') +
    `<div class="card" aria-busy="true"><div class="skel"><i style="width:30%;height:34px"></i><i style="width:92%"></i><i style="width:80%"></i><i style="width:86%"></i><i style="width:60%"></i></div>
    <p class="muted" style="margin:0;padding:0 18px 18px;font-size:13px"><span class="spin" aria-hidden="true" style="margin-right:8px;vertical-align:-1px"></span>${S.rvView === 'season' ? 'Building the season review: the first open of a week or season the server hasn’t reviewed yet can take up to half a minute. It’s kept after that.' : 'Scoring the week’s 4th downs: the first open of a week or season the server hasn’t reviewed yet can take up to half a minute. It’s kept after that.'}</p></div>`;
}

function rvNoPlays(wk) {
  return `<div class="card"><div class="empty"><div class="glyph">—</div><h3>${esc(weekName(wk.season, wk.week))}’s review isn’t ready yet</h3><p>${esc(wk.message || '')}</p>
    <button type="button" class="btn sm" data-rv="season">See the season so far</button></div></div>`;
}

/* ---------- this week: the numbers ---------- */
function callsBars(s) {
  const K = ['go', 'fg', 'punt'];
  const row = (who, o) => {
    const tot = K.reduce((a, k) => a + o[k], 0) || 1;
    return `<div class="row"><span class="who">${who}</span><span class="bar" role="img" aria-label="${who}: ${K.map(k => `${CN[k]} ${o[k]}`).join(', ')}">${K.filter(k => o[k] > 0).map(k => `<span tabindex="0" style="width:${(o[k] / tot * 100).toFixed(2)}%;background:${CALL_C[k]}" data-tip="${esc(`${who}: ${BN[k].toLowerCase()}\n${o[k]} of ${tot} 4th downs (${p0(o[k] / tot)})`)}"></span>`).join('')}</span><small>${K.map(k => `${CN[k]} ${o[k]}`).join(' · ')}</small></div>`;
  };
  return `<div class="tile calls-tile"><span class="k">Who calls what</span><div class="calls">${row('Coaches', s.coach)}${row('The bot', s.bot)}<span class="legend">${K.map(k => `<span><i style="background:${CALL_C[k]}"></i>${BN[k]}</span>`).join('')}</span></div></div>`;
}
function rvSummary(wk) {
  const s = wk.summary;
  const teams = new Set(wk.games.flatMap(g => [g.away, g.home])).size;
  const extra = [s.wiped && `${s.wiped} wiped out by a penalty`, s.fakes && `${s.fakes} fake${s.fakes > 1 ? 's' : ''}`, s.skipped && `${s.skipped} skipped`].filter(Boolean).join(' · ');
  return `<div class="rv-sum">
    <div class="tile"><span class="k">4th downs decided</span><span class="v num">${s.decisions}</span><span class="d">${wk.games.length} game${wk.games.length > 1 ? 's' : ''}${extra ? ' · ' + extra : ''}</span></div>
    <div class="tile" tabindex="0" data-tip="${esc('Toss-up\nThe bot can’t separate the options (under 1 point apart, or the call flips in re-checks): either call is defensible.')}"><span class="k">Coach and bot agree</span><span class="v num">${p0(s.agree / s.decisions)}</span><span class="d">${s.agree} of ${s.decisions} · ${s.toss_ups} toss-ups</span></div>
    <div class="tile" tabindex="0" data-tip="${esc('Go spots\n4th downs where the bot says go and is Confident or Lean (not a toss-up).')}"><span class="k">Went for it in go spots</span><span class="v num">${s.went_in_go_spots}<small>of ${s.go_spots}</small></span><span class="d">${p0(s.go_spots ? s.went_in_go_spots / s.go_spots : null)} of the spots where the bot says go</span></div>
    <div class="tile" tabindex="0" data-tip="${esc('Expected wins given up\nThe win chance each call left on the table against the bot’s best option, added up: 0.05 = 5 points of win chance.')}"><span class="k">Expected wins given up</span><span class="v num">${wins(s.wp_lost)}</span><span class="d">against the bot’s calls · ${teams} teams</span></div>
    ${callsBars(s)}
  </div>`;
}

/* ---------- this week: the highlights ---------- */
function gameOf(wk, id) { return wk.games.find(g => g.game_id === id) || {}; }
function hlCard(kind, p, wk) {
  const name = kind === 'bold' ? 'Boldest call' : 'Costliest call';
  const def = kind === 'bold' ? 'The go-for-it with the lowest chance to convert, in a game still in doubt, when kicking was a real option' : 'The biggest gap between the bot’s best option and the coach’s call';
  if (!p) {
    return `<div class="card hl"><div class="hl-k"><span class="eyebrow">${name}</span><small>${def}</small></div><p class="none">${kind === 'bold' ? 'No long shot this week: no go-for-it in a game still in doubt when kicking was a real option.' : 'Every coach made the bot’s call.'}</p></div>`;
  }
  const g = gameOf(wk, p.game_id);
  const fig = kind === 'bold'
    ? `<b class="num">${p0(p.convert)}</b><span>chance to convert, by the bot’s numbers</span>`
    : `<b class="num">${pts(p.cost)}</b><span>points of win chance given up</span>`;
  const verdict = p.agree ? `<span class="muted">the bot agreed (+${pts(p.gap)})</span>` : `<span class="muted">cost ${pts(p.cost)} points</span>`;
  return `<div class="card hl"><div class="hl-k"><span class="eyebrow">${name}</span><small>${def}</small></div>
    <div class="hl-who">${tchip(p.posteam, true)}<span>${esc(p.coach || '—')}</span><span>· ${p.posteam === g.home ? 'vs' : 'at'} ${esc(p.defteam)}</span></div>
    <div class="hl-sit">${esc(p.situation)}</div>
    <div class="hl-when">${esc(qc(p))} · ${esc(lead(p))} (${p.off_score}–${p.def_score})</div>
    <div class="hl-fig">${fig}</div>
    <div class="hl-vs"><span>Coach: <b>${CN[p.choice]}</b></span><span>Bot: <b>${BN[p.best]}</b></span>${confChip(p.label, true)}${kind === 'bold' ? verdict : ''}</div>
    <div class="hl-res">Result: <b>${esc(p.result)}</b> ${tags(p)}</div>
    <p class="desc"><span class="src">nflverse play-by-play</span>${esc(p.desc)}</p>
    <div><button type="button" class="btn sm" data-jump="${esc(pkey(p))}">Show it in the list</button></div></div>`;
}
function rvHighlights(wk) {
  const h = wk.highlights;
  const top = h.top || [];
  const mx = Math.max(EDGE_MAX, ...top.map(p => p.cost || 0));
  const rows = top.map((p, i) => `<a href="#p-${esc(pkey(p))}" data-jump="${esc(pkey(p))}" data-tip="${esc(`${p.posteam} ${p.situation}\n${qc(p)} · ${lead(p)}\nCoach: ${CN[p.choice]} · Bot: ${BN[p.best]}${p.label ? ` (${p.label})` : ''}\nGo ${p1(p.wp.go)} · Field goal ${p1(p.wp.fg)} · Punt ${p1(p.wp.punt)}`)}">
      <span class="n">${i + 1}</span>
      <span class="s">${tchip(p.posteam)} <b>${esc(p.situation)}</b><small>${esc(qc(p))} · ${esc(lead(p))} · ${esc(p.coach || '')}</small></span>
      <span class="c">${CN[p.choice]} <span class="muted">→ bot:</span> <b>${BN[p.best]}</b><small>${esc(p.result)}</small></span>
      <span class="tr" aria-hidden="true"><i style="width:${(p.cost / mx * 100).toFixed(1)}%"></i></span>
      <span class="v num">${pts(p.cost)}</span></a>`).join('');
  return `<div class="hls">${hlCard('bold', h.boldest, wk)}${hlCard('cost', h.costliest, wk)}</div>
    <div class="card"><div class="card-h"><h2>The five costliest</h2><span class="muted">points of win chance given up against the bot’s best option</span></div>
      <div class="top5">${rows || '<p class="muted" style="padding:14px 18px;margin:0">No call cost anything this week.</p>'}</div></div>`;
}

/* ---------- this week: every 4th down ---------- */
function edgeCell(p) {
  if (p.edge == null) return `<span class="edge"><span class="eb"></span><span class="ev muted" title="The coach’s option isn’t priced (a kick out of range)">—</span></span>`;
  const cls = p.edge < 0 ? 'cost' : 'gain';
  const w = Math.min(1, Math.abs(p.edge) / EDGE_MAX) * 50;
  return `<span class="edge" role="img" aria-label="${p.edge < 0 ? 'cost' : 'gained'} ${pts(Math.abs(p.edge))} points"><span class="eb" aria-hidden="true"><span class="z"></span><i class="${cls}" style="width:${w.toFixed(1)}%"></i></span><span class="ev num ${cls}" aria-hidden="true">${signed(p.edge)}</span></span>`;
}
function rowTip(p) {
  const wp = ['go', 'fg', 'punt'].map(k => `${CN[k]} ${p.wp[k] == null ? 'n/a' : p1(p.wp[k])}`).join(' · ');
  const e = p.edge == null ? 'Not priced' : p.edge < 0 ? `Cost ${pts(-p.edge)} points against the bot` : `Gained ${pts(p.edge)} points over the next best`;
  return `${p.posteam} ${p.situation} · ${qc(p)}\n${p.posteam}'s win chance after each: ${wp}\nCoach: ${CN[p.choice]} · Bot: ${BN[p.best]}${p.label ? ` (${p.label})` : ''}\n${e}`;
}
function rvMore(p) {
  const opts = ['go', 'fg', 'punt'].map(k => {
    const v = p.wp[k], best = k === p.best;
    const who = [k === p.choice && 'coach', best && 'bot'].filter(Boolean).join(' · ');
    if (v == null) return `<div class="mopt" role="listitem"><span class="on">${BN[k]}</span><span class="tr"><span class="bg"></span></span><span class="pv na">not an option</span><span class="who">${who}</span></div>`;
    return `<div class="mopt${best ? ' best' : ''}" role="listitem"><span class="on">${BN[k]}</span><span class="tr" aria-hidden="true"><span class="bg"></span><span class="fill" style="width:${(v * 100).toFixed(1)}%"></span></span><span class="pv">${p1(v)}</span><span class="who">${who}</span></div>`;
  }).join('');
  return `<div class="rv-more">
    <div><div class="mopts" role="list" aria-label="${esc(p.posteam)} win chance after each option">${opts}</div>
      <p class="optnote"><span>${esc(p.posteam)}’s win chance after each option, on one 0–100% axis; the bot’s best in the accent.</span></p></div>
    <div class="modds">
      <div class="kv"><span>If they go: <b>${p0(p.convert)}</b> to convert</span><span>If they kick: <b>${esc(fgText(p))}</b></span></div>
      <div class="kv"><span>The bot: <b>${BN[p.best]}</b> by <b>${p.gap == null ? '—' : pts(p.gap)}</b> points</span>${confChip(p.label, true)}${p.coach ? `<span>Coach: <b>${esc(p.coach)}</b></span>` : ''}</div>
      <p class="desc"><span class="src">nflverse play-by-play</span>${esc(p.desc)}</p>
    </div></div>`;
}
function rvRow(p) {
  return `<details class="rv-row${p.agree ? '' : ' dis'}" id="p-${esc(pkey(p))}"><summary data-tip="${esc(rowTip(p))}">
    <span class="when">${esc(qc(p))}</span>
    <span class="off">${tchip(p.posteam)}<small class="num">${p.off_score}–${p.def_score}</small></span>
    <span class="sit">${esc(p.situation)}</span>
    <span class="coach"><span class="lbl-ph">Coach: </span>${CN[p.choice]}</span>
    <span class="bot"><span class="lbl-ph">Bot: </span><span class="nm">${BN[p.best]}</span>${confChip(p.label, true)}</span>
    ${edgeCell(p)}
    <span class="res">${esc(p.result)} ${tags(p)}</span>
    <span class="chev" aria-hidden="true">▸</span></summary>${rvMore(p)}</details>`;
}
function rvList(wk) {
  const dis = wk.plays.filter(p => !p.agree).length;
  const shown = S.rvFilter === 'disagree' ? wk.plays.filter(p => !p.agree) : wk.plays;
  const byGame = {};
  shown.forEach(p => { (byGame[p.game_id] = byGame[p.game_id] || []).push(p); });
  const blocks = wk.games.map(g => {
    const ps = byGame[g.game_id];
    if (!ps) return '';
    const won = t => (t === g.home ? g.home_score >= g.away_score : g.away_score >= g.home_score);
    return `<section class="rv-game" aria-label="${esc(`${g.away} at ${g.home}`)}"><div class="rv-gh">
        <span class="gm">${tchip(g.away)}<span class="sc${won(g.away) ? '' : ' lost'}">${g.away_score ?? ''}</span><span class="muted">at</span>${tchip(g.home)}<span class="sc${won(g.home) ? '' : ' lost'}">${g.home_score ?? ''}</span></span>
        <span class="meta">Final · ${esc(tDay(g.kickoff))} · ${g.decisions} 4th down${g.decisions === 1 ? '' : 's'}${S.rvFilter === 'disagree' ? ` · ${ps.length} disagreement${ps.length === 1 ? '' : 's'}` : ''}</span>
        <span class="lost-w" tabindex="0" data-tip="${esc('Wins given up\nEach team’s cost against the bot in this game, added up (0.05 = 5 points of win chance)')}">Wins given up: ${[g.away, g.home].map(t => `<span>${esc(t)} <b>${wins(g.wp_lost[t])}</b></span>`).join('')}</span></div>
      <div role="list">${ps.map(p => `<div role="listitem">${rvRow(p)}</div>`).join('')}</div></section>`;
  }).join('');
  return `<div class="card rv-list"><div class="card-h"><h2>Every 4th down</h2><span class="muted">in kickoff order · open a row for the three options and the play</span>
      <div class="right"><span class="edgekey" aria-hidden="true"><span><i style="background:var(--ink-2)"></i>cost against the bot</span><span><i style="background:color-mix(in srgb, var(--ink-3) 45%, var(--panel))"></i>gained over the next best</span></span>
      <span class="seg" role="group" aria-label="Which 4th downs"><button type="button" data-rvf="all" aria-pressed="${S.rvFilter !== 'disagree'}">All ${wk.plays.length}</button><button type="button" data-rvf="disagree" aria-pressed="${S.rvFilter === 'disagree'}">Disagreements only ${dis}</button></span></div></div>
    <div class="rv-cols" aria-hidden="true"><span>When</span><span>Offense</span><span>Situation</span><span>Coach</span><span>The bot</span><span class="r" title="Points of win chance: + gained over the next best option, − cost against the bot">± points</span><span>Result</span><span></span></div>
    ${blocks || '<p class="muted" style="padding:14px 18px;margin:0">No disagreements this week.</p>'}</div>`;
}

/* ---------- the season ---------- */
function seasonBody(sv, wk) {
  if (sv.status === 'no_plays') return `<div class="card"><div class="empty"><div class="glyph">—</div><h3>No reviewed week yet</h3><p>${esc(sv.message || '')}</p></div></div>`;
  const L = sv.league;
  let h = '';
  if (sv.through_week != null && sv.through_week < wk.week) {
    h += `<div class="notice" role="note"><span class="ic">i</span><div><b>${esc(weekName(wk.season, wk.week))}’s plays aren’t in yet:</b> this is the season through ${esc(throughText(sv.season, sv.through_week))}.</div></div>`;
  }
  const spots = sv.leaderboard.map(r => r.go_spots).filter(n => n > 0);
  if (sv.weeks.length <= 6) {
    h += `<div class="notice" role="note"><span class="ic">i</span><div><b>Early-season samples are small.</b> After ${sv.weeks.length} week${sv.weeks.length > 1 ? 's' : ''} a coach with any has ${Math.min(...spots)} to ${Math.max(...spots)} go spots, so one call moves his rate by ${Math.round(100 / Math.max(...spots))} to ${Math.round(100 / Math.min(...spots))} points. Read the order loosely until about week 8; the calibration needs about 4 weeks of plays before it says much.</div></div>`;
  }
  h += `<div class="rv-sum season">
    <div class="tile"><span class="k">4th downs decided</span><span class="v num">${sv.decisions.toLocaleString('en-US')}</span><span class="d">${sv.weeks.length} week${sv.weeks.length > 1 ? 's' : ''} · ${sv.leaderboard.length} head coaches</span></div>
    <div class="tile"><span class="k">Went for it in go spots</span><span class="v num">${p0(L.go_rate_spots)}</span><span class="d">${L.went_in_go_spots} of ${L.go_spots}, league-wide</span></div>
    <div class="tile"><span class="k">Went for it in kick spots</span><span class="v num">${p0(L.go_rate_kick_spots)}</span><span class="d">${L.went_in_kick_spots} of ${L.kick_spots}: the bot says kick</span></div>
    <div class="tile" tabindex="0" data-tip="${esc(`Expected wins given up\nTimid: kicked in go spots (${wins(L.wp_lost_timid)})\nBold: went in kick spots (${wins(L.wp_lost_bold)})\nThe rest: toss-ups, and a field goal vs a punt (${wins(L.wp_lost - L.wp_lost_timid - L.wp_lost_bold)})`)}"><span class="k">Expected wins given up</span><span class="v num">${wins(L.wp_lost)}</span><span class="d">${wins(L.wp_lost_timid)} by kicking in go spots · ${wins(L.wp_lost_bold)} by going in kick spots</span></div>
    <div class="tile"><span class="k">Coach and bot agree</span><span class="v num">${p0(L.agree_rate)}</span><span class="d">${L.agree.toLocaleString('en-US')} of ${L.decisions.toLocaleString('en-US')}</span></div>
  </div>`;
  h += leaderboard(sv);
  const c = sv.calibration || {};
  if (c.wp) h += calCard(c.wp, sv);
  if (c.conversion) h += convCard(c.conversion);
  h += `<div class="rv-grid2">${c.fg ? fgCard(c.fg) : ''}${trendCard(sv)}</div>`;
  return h;
}

const SORTS = {
  rank: (a, b) => a.rank - b.rank,
  spots: (a, b) => b.go_spots - a.go_spots || a.rank - b.rank,
  lost: (a, b) => b.wp_lost - a.wp_lost || a.rank - b.rank,
  agree: (a, b) => (b.agree_rate ?? -1) - (a.agree_rate ?? -1) || a.rank - b.rank,
};
function leaderboard(sv) {
  const L = sv.league;
  const rows = [...sv.leaderboard].sort(SORTS[S.rvSort] || SORTS.rank);
  const bar = (rate, lg) => `<span class="ratebar" aria-hidden="true"><span class="bg"></span>${rate == null ? '' : `<i style="width:${(rate * 100).toFixed(1)}%"></i>`}<span class="lg" style="left:${((lg || 0) * 100).toFixed(1)}%"></span></span>`;
  const tr = r => {
    const tip = `${r.coach} (${(r.teams || []).join(', ')})\nWent for it in ${r.went_in_go_spots} of ${r.go_spots} go spots (${p0(r.go_rate_spots)}); league ${p0(L.go_rate_spots)}\nAll 4th downs: went ${r.go} of ${r.decisions} (${p0(r.go_rate)})\nWent anyway in ${r.went_in_kick_spots} of ${r.kick_spots} kick spots\nWins given up ${wins(r.wp_lost)}: ${wins(r.wp_lost_timid)} timid, ${wins(r.wp_lost_bold)} bold\nAgreed with the bot ${p0(r.agree_rate)}`;
    return `<tr class="${r.go_spots < 5 ? 'few' : ''}" tabindex="0" data-tip="${esc(tip)}">
      <td class="rk">${r.rank}</td>
      <td class="cn">${esc(r.coach)}<small><span class="sm-only">${esc((r.teams || []).join(', '))} · </span>${r.weeks} week${r.weeks === 1 ? '' : 's'}${r.go_spots < 5 ? ' · few spots' : ''}</small></td>
      <td class="hide-sm">${(r.teams || []).map(t => tchip(t)).join(' ')}</td>
      <td class="num hide-sm">${r.go_spots}</td>
      <td class="num hide-sm">${r.went_in_go_spots}</td>
      <td class="num"><span class="ratecell">${bar(r.go_rate_spots, L.go_rate_spots)}<b>${p0(r.go_rate_spots)}</b></span><small class="sub sm-only">${r.went_in_go_spots} of ${r.go_spots}</small></td>
      <td class="num hide-sm">${p0(r.go_rate)}<small class="sub">${r.go} of ${r.decisions}</small></td>
      <td class="num hide-sm">${r.went_in_kick_spots}<small class="sub">of ${r.kick_spots}</small></td>
      <td class="num">${wins(r.wp_lost)}<small class="sub">${wins(r.wp_lost_timid)} timid · ${wins(r.wp_lost_bold)} bold</small></td>
      <td class="num hide-sm">${p0(r.agree_rate)}</td></tr>`;
  };
  const th = (k, label, tip, cls = 'num') => `<th class="${cls}"${tip ? ` title="${esc(tip)}"` : ''}>${k ? `<button type="button" class="th-sort" data-rvsort="${k}" aria-pressed="${S.rvSort === k}">${label}${S.rvSort === k ? ' ▾' : ''}</button>` : label}</th>`;
  return `<div class="card lb"><div class="card-h"><h2>Coach aggressiveness</h2><span class="muted">ranked by how often he went for it where the bot says go (Confident or Lean)</span>
      <div class="right"><span class="lb-key"><span><i class="b"></i>went for it in go spots</span><span><i class="l"></i>league ${p0(L.go_rate_spots)}</span></span></div></div>
    <div class="tablewrap"><table class="tbl"><thead><tr>
      ${th('rank', '#', 'Rank: go rate in go spots, then go spots', '')}<th>Coach</th><th class="hide-sm">Team</th>
      ${th('spots', 'Go spots', 'The bot says go, Confident or Lean').replace('<th class="num"', '<th class="num hide-sm"')}<th class="num hide-sm">Went</th>
      <th class="num">Rate in go spots</th><th class="num hide-sm" title="Every 4th down">Go rate, all</th><th class="num hide-sm" title="The bot says kick (Confident or Lean) and he went anyway">Went in kick spots</th>
      ${th('lost', 'Wins given up', 'Expected wins given up against the bot: timid = kicked in go spots, bold = went in kick spots')}${th('agree', 'Agree', 'Made the bot’s call').replace('<th class="num"', '<th class="num hide-sm"')}
    </tr></thead><tbody>${rows.map(tr).join('')}</tbody>
    <tfoot><tr><td></td><td class="cn">League</td><td class="hide-sm"></td><td class="num hide-sm">${L.go_spots}</td><td class="num hide-sm">${L.went_in_go_spots}</td><td class="num"><span class="ratecell">${bar(L.go_rate_spots, L.go_rate_spots)}<b>${p0(L.go_rate_spots)}</b></span><small class="sub sm-only">${L.went_in_go_spots} of ${L.go_spots}</small></td><td class="num hide-sm">${p0(L.go_rate)}</td><td class="num hide-sm">${L.went_in_kick_spots}<small class="sub">of ${L.kick_spots}</small></td><td class="num">${wins(L.wp_lost)}</td><td class="num hide-sm">${p0(L.agree_rate)}</td></tr></tfoot></table></div></div>`;
}

/* the bot's win probability against who won: two series + the diagonal, one axis */
function calCard(wp, sv) {
  const W = 620, H = 290, m = { l: 46, r: 16, t: 12, b: 58 };
  const X = v => m.l + v * (W - m.l - m.r), Y = v => m.t + (1 - v) * (H - m.t - m.b);
  const T = [0, 0.25, 0.5, 0.75, 1];
  let s = `<g class="grid">${T.map(t => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/>`).join('')}</g>`;
  s += T.map(t => `<text x="${m.l - 8}" y="${Y(t) + 4}" text-anchor="end">${t * 100}%</text><text x="${X(t)}" y="${H - 38}" text-anchor="middle">${t * 100}%</text>`).join('');
  s += `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(1)}" y2="${Y(1)}" style="stroke:var(--ink-3)" stroke-width="1.5" stroke-dasharray="4 4"/>`;
  const ser = [['The bot', 'var(--s1)', wp.model, 'c'], ['nflfastR vegas_wp', 'var(--s3)', wp.vegas, 's']];
  ser.forEach(([, col, bins]) => {
    const pts2 = bins.filter(b => b.n > 0 && b.mean_pred != null);
    s += `<polyline points="${pts2.map(b => `${X(b.mean_pred)},${Y(b.mean_outcome)}`).join(' ')}" fill="none" style="stroke:${col}" stroke-width="2" stroke-linejoin="round"/>`;
  });
  ser.forEach(([, col, bins, shape]) => {
    s += bins.filter(b => b.n > 0 && b.mean_pred != null).map(b => shape === 'c'
      ? `<circle cx="${X(b.mean_pred)}" cy="${Y(b.mean_outcome)}" r="4.5" style="fill:${col};stroke:var(--panel)" stroke-width="2"/>`
      : `<rect x="${X(b.mean_pred) - 4}" y="${Y(b.mean_outcome) - 4}" width="8" height="8" style="fill:${col};stroke:var(--panel)" stroke-width="2"/>`).join('');
  });
  // n per bin under the axis, and a hover / focus column per bin
  s += `<text x="${m.l - 8}" y="${H - 20}" text-anchor="end" class="nlab">snaps</text>`;
  wp.model.forEach(b => {
    const v = wp.vegas.find(x => Math.abs(x.bin_lo - b.bin_lo) < 1e-9) || {}; // pair by bin, not index
    const x0 = X(b.bin_lo), x1 = X(Math.min(1, b.bin_lo + 0.1));
    s += `<text x="${(x0 + x1) / 2}" y="${H - 20}" text-anchor="middle" class="nlab">${b.n.toLocaleString('en-US')}</text>`;
    const tip = `Said ${Math.round(b.bin_lo * 100)}–${Math.round(b.bin_lo * 100 + 10)}%\nThe bot: said ${p1(b.mean_pred)}, won ${p1(b.mean_outcome)} (${b.n.toLocaleString('en-US')} snaps)\nvegas_wp: said ${p1(v.mean_pred)}, won ${p1(v.mean_outcome)} (${(v.n || 0).toLocaleString('en-US')} snaps)`;
    s += `<rect class="hit" x="${x0}" y="${m.t}" width="${x1 - x0}" height="${H - m.t - m.b}" tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(tip.replace(/\n/g, '; '))}"/>`;
  });
  s += `<text x="${W - m.r}" y="${H - 2}" text-anchor="end">the offense’s win chance, as predicted before the snap →</text>`;
  s += `<text x="${m.l}" y="${H - 2}" text-anchor="start">↑ how often it won</text>`;
  const st = (k, a, b, f, note) => `<div class="st"><span class="k">${k}</span><span class="v"><span><i style="background:var(--s1)"></i><b>${f(a)}</b>the bot</span><span><i style="background:var(--s3);border-radius:1px"></i><b>${f(b)}</b>vegas_wp</span></span>${note ? `<span class="k">${note}</span>` : ''}</div>`;
  return `<div class="card"><div class="card-h"><h2>Is the bot’s win chance right?</h2><span class="muted">every snap of the season’s finished games, grouped by what the bot said, against who won</span></div>
    <div class="cal"><div><div class="legend" style="margin-bottom:6px"><span><i style="background:var(--s1)"></i>The bot</span><span><i style="background:var(--s3);border-radius:1px"></i>nflfastR’s vegas_wp (same snaps)</span><span><i class="dash"></i>Perfectly calibrated</span></div>
      <div class="chart"><svg viewBox="0 0 ${W} ${H}" role="group" aria-label="Calibration of the bot's win probability, ${sv.season}">${s}</svg></div></div>
      <div class="calstats">
        ${st('Brier score (lower is better)', wp.brier, wp.brier_vegas, v => (v == null ? '—' : v.toFixed(3)))}
        ${st('Calibration error (ECE, points)', wp.ece, wp.ece_vegas, v => (v == null ? '—' : (v * 100).toFixed(1)), 'the average distance from the diagonal, weighted by snaps')}
        <p>${wp.snaps.toLocaleString('en-US')} snaps in ${wp.games} games${wp.tie_games ? ` (${wp.tie_games} tie${wp.tie_games > 1 ? 's' : ''}, left out)` : ''}. A point above the diagonal: teams won more often than the bot said.</p>
      </div></div></div>`;
}

/* conversion: predicted vs what happened, 3rd downs next to attempted 4th downs */
function convChart(rows, down) {
  const W = 520, H = 250, m = { l: 40, r: 10, t: 10, b: 48 };
  const rs = rows.filter(r => r.down === down).sort((a, b) => a.distance - b.distance);
  const n = rs.length;
  const X = i => m.l + (i + 0.5) * (W - m.l - m.r) / n, Y = v => m.t + (1 - v) * (H - m.t - m.b);
  const T = [0, 0.25, 0.5, 0.75, 1];
  let s = `<g class="grid">${T.map(t => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/>`).join('')}</g>`;
  s += T.map(t => `<text x="${m.l - 6}" y="${Y(t) + 4}" text-anchor="end">${t * 100}%</text>`).join('');
  const pp = rs.map((r, i) => (r.pred == null ? null : `${X(i)},${Y(r.pred)}`)).filter(Boolean);
  s += `<polyline points="${pp.join(' ')}" fill="none" style="stroke:var(--s1)" stroke-width="2" stroke-linejoin="round"/>`;
  rs.forEach((r, i) => {
    if (r.pred != null) s += `<circle cx="${X(i)}" cy="${Y(r.pred)}" r="4" style="fill:var(--s1);stroke:var(--panel)" stroke-width="2"/>`;
    if (r.actual != null) s += `<circle cx="${X(i)}" cy="${Y(r.actual)}" r="4.5" style="fill:var(--panel);stroke:var(--ink-2)" stroke-width="2"${r.n < 10 ? ' stroke-dasharray="2 2" opacity=".6"' : ''}/>`;
    const x0 = m.l + i * (W - m.l - m.r) / n, w = (W - m.l - m.r) / n;
    s += `<text x="${X(i)}" y="${H - 30}" text-anchor="middle">${esc(r.label)}</text><text x="${X(i)}" y="${H - 16}" text-anchor="middle" class="nlab">${r.n}</text>`;
    const tip = `${down === 3 ? '3rd' : '4th'} & ${r.label}\nThe bot said ${p0(r.pred)}\nConverted ${p0(r.actual)} (${r.n} ${down === 3 ? 'plays' : 'tries'})${r.n < 10 ? '\nFewer than 10: read loosely' : ''}`;
    s += `<rect class="hit" x="${x0}" y="${m.t}" width="${w}" height="${H - m.t - m.b}" tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(tip.replace(/\n/g, '; '))}"/>`;
  });
  s += `<text x="${m.l - 6}" y="${H - 16}" text-anchor="end" class="nlab">n</text><text x="${W - m.r}" y="${H - 1}" text-anchor="end">yards to go →</text>`;
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="group" aria-label="${down === 3 ? 'Third' : 'Fourth'}-down conversion: predicted vs actual by distance">${s}</svg></div>`;
}
function convCard(cv) {
  const ov = d => cv.overall.find(o => o.down === d) || {};
  const o3 = ov(3), o4 = ov(4);
  const line = (o, what) => `<p class="ov">${what}: converted <b>${p1(o.actual)}</b>, the bot said <b>${p1(o.pred)}</b> · ${(o.n || 0).toLocaleString('en-US')} ${what === '3rd downs' ? 'plays' : 'tries'}</p>`;
  return `<div class="card"><div class="card-h"><h2>Does the bot’s conversion chance match what happened?</h2><span class="muted">by yards to go; n under each distance</span>
      <div class="right"><span class="legend"><span><i style="background:var(--s1)"></i>The bot said</span><span><i class="hollow"></i>Converted</span></span></div></div>
    <div class="chartpad"><div class="notice" role="note"><span class="ic">i</span><div><b>Tries convert more often than the bot says.</b> Coaches go for it when they expect to make it (a short 4th & 2, a weak defence), so tries convert more often than an average spot would: that’s selection, not the bot being wrong. 3rd downs have no such choice, so they sit next to them as the fair test.</div></div>
      <div class="conv"><div><h3>3rd downs<small>every one, the fair test</small></h3>${line(o3, '3rd downs')}${convChart(cv.rows, 3)}</div>
        <div><h3>4th-down tries<small>only where the coach went</small></h3>${line(o4, '4th-down tries')}${convChart(cv.rows, 4)}</div></div>
      <p class="muted" style="margin:0;font-size:12px">Dashed circles: fewer than 10 plays at that distance.</p></div></div>`;
}
function fgCard(fg) {
  const dots = r => `<span class="fgdots" aria-hidden="true"><span class="bg"></span>${r.pred != null ? `<span class="p" style="left:${(r.pred * 100).toFixed(1)}%"></span>` : ''}${r.made != null ? `<span class="a" style="left:${(r.made * 100).toFixed(1)}%"></span>` : ''}</span>`;
  const rows = fg.rows.map(r => `<tr tabindex="0" data-tip="${esc(`${r.band.replace('-', '–')} yards\nThe bot said ${p1(r.pred)}\nMade ${p1(r.made)} of ${r.n} kicks`)}"><td>${esc(r.band.replace('-', '–'))} yards</td><td class="num">${r.n}</td><td class="num"><b>${p0(r.made)}</b></td><td class="num">${p0(r.pred)}</td><td class="num hide-sm">${dots(r)}</td></tr>`).join('');
  return `<div class="card"><div class="card-h"><h2>Field goals: made vs predicted</h2><span class="muted">${fg.n.toLocaleString('en-US')} kicks · made ${p1(fg.made)}, the bot said ${p1(fg.pred)}</span></div>
    <div class="tablewrap"><table class="tbl fgt"><thead><tr><th>Distance</th><th class="num">Kicks</th><th class="num">Made</th><th class="num">Bot said</th><th class="num hide-sm"><span class="legend" style="justify-content:flex-end;font-size:11px;text-transform:none;letter-spacing:0"><span><i style="background:var(--s1)"></i>said</span><span><i class="hollow"></i>made</span><span class="muted">0–100%</span></span></th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}
/* the week by week trend: coaches' go rate against the bot's (every 4th down), one 0-100% axis */
function trendCard(sv) {
  const t = sv.trend, n = t.length;
  const W = 620, H = 240, m = { l: 44, r: 14, t: 12, b: 30 };
  const X = i => m.l + (n <= 1 ? (W - m.l - m.r) / 2 : i * (W - m.l - m.r) / (n - 1)), Y = v => m.t + (1 - v) * (H - m.t - m.b);
  const T = [0, 0.25, 0.5, 0.75, 1];
  let s = `<g class="grid">${T.map(v => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"/>`).join('')}</g>`;
  s += T.map(v => `<text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${v * 100}%</text>`).join('');
  const every = n > 12 ? 2 : 1;
  s += t.map((r, i) => (i % every === 0 || i === n - 1 ? `<text x="${X(i)}" y="${H - 10}" text-anchor="middle">${esc(weekShort(sv.season, r.week))}</text>` : '')).join('');
  const ser = [['The bot says go', 'var(--s1)', 'go_rate_bot'], ['Coaches went for it', 'var(--s2)', 'go_rate_coach']];
  ser.forEach(([, col, k]) => {
    const pp = t.map((r, i) => (r[k] == null ? null : `${X(i)},${Y(r[k])}`)).filter(Boolean);
    if (pp.length > 1) s += `<polyline points="${pp.join(' ')}" fill="none" style="stroke:${col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    t.forEach((r, i) => { if (r[k] != null && (n <= 12 || i === n - 1)) s += `<circle cx="${X(i)}" cy="${Y(r[k])}" r="${n <= 12 ? 4 : 4}" style="fill:${col};stroke:var(--panel)" stroke-width="2"/>`; });
  });
  t.forEach((r, i) => {
    const half = n <= 1 ? (W - m.l - m.r) / 2 : (W - m.l - m.r) / (n - 1) / 2;
    const tip = `${weekName(sv.season, r.week)} · ${r.decisions} 4th downs\nThe bot says go: ${p0(r.go_rate_bot)}\nCoaches went for it: ${p0(r.go_rate_coach)}\nAgreed: ${p0(r.agree_rate)} · wins given up ${wins(r.wp_lost)}\nTries converted ${p0(r.conv_actual)} (the bot said ${p0(r.conv_pred)}, ${r.attempts} tries)`;
    s += `<rect class="hit" x="${Math.max(m.l, X(i) - half)}" y="${m.t}" width="${Math.min(W - m.r, X(i) + half) - Math.max(m.l, X(i) - half)}" height="${H - m.t - m.b}" tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(tip.replace(/\n/g, '; '))}"/>`;
  });
  return `<div class="card"><div class="card-h"><h2>Week by week</h2><span class="muted">share of 4th downs</span>
      <div class="right"><span class="legend">${ser.map(([nm, col]) => `<span><i style="background:${col}"></i>${nm}</span>`).join('')}</span></div></div>
    <div class="chartpad"><div class="chart"><svg viewBox="0 0 ${W} ${H}" role="group" aria-label="Go rate by week: the bot and the coaches">${s}</svg></div>
    ${n <= 1 ? '<p class="muted" style="margin:0;font-size:12px">One week so far: the lines start with week 2.</p>' : ''}</div></div>`;
}

/* ---------- the current week, every game final (Game day's `final` phase) ---------- */
function finalEmpty(b) {
  const wk = rvWeekData();
  if (S.rvState === 'noplays' || wk.status === 'no_plays') {
    return `<div class="card"><div class="empty"><div class="glyph">F</div><h3>Every week-${b.week} game is final</h3>
      <p>Live checks are over for this week. The decision review reads nflverse’s play-by-play, not ESPN: ${esc(wk.message || '')}</p>
      <button type="button" class="btn sm" aria-disabled="true" title="Ready after Tuesday's run">Open the decision review</button>
      <span class="muted" style="font-size:12.5px">Ready after Tuesday’s run. Until then, pick a game on the left for its final score.</span></div></div>`;
  }
  const s = wk.summary || {};
  return `<div class="card"><div class="empty"><div class="glyph">F</div><h3>Every week-${b.week} game is final</h3>
    <p>Live checks are over for this week. The decision review is ready: ${s.decisions} 4th downs, the bot’s call next to each coach’s, and what each call gained or cost.</p>
    <button type="button" class="btn primary" data-rvopen="1">Open the decision review</button></div></div>`;
}

/* ---------- clicks ---------- */
function jumpTo(key) {
  if (S.rvFilter === 'disagree') {
    const p = rvWeekData().plays.find(x => pkey(x) === key);
    if (p && p.agree) { S.rvFilter = 'all'; render(); }
  }
  const el = document.getElementById('p-' + key);
  if (!el) return;
  el.open = true;
  el.classList.add('flash');
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  const sm = el.querySelector('summary');
  if (sm) sm.focus({ preventScroll: true });
  setTimeout(() => el.classList.remove('flash'), 2400);
}
$('#view').addEventListener('click', e => {
  const t = e.target.closest('[data-rv],[data-rvf],[data-rvsort],[data-jump],[data-rvopen],[data-rvback],[data-rvweek]');
  if (!t) return;
  if (t.dataset.rv) { S.rvView = t.dataset.rv; render(); return; }
  if (t.dataset.rvf) { S.rvFilter = t.dataset.rvf; render(); return; }
  if (t.dataset.rvsort) { S.rvSort = t.dataset.rvsort; render(); return; }
  if (t.dataset.rvopen) { S.rvOpen = true; render(); window.scrollTo({ top: 0 }); return; }
  if (t.dataset.rvback) { S.rvOpen = false; render(); return; }
  if (t.dataset.rvweek) { S.rvWeek = t.dataset.rvweek; syncMockbar(); render(); return; }
  if (t.dataset.jump) { e.preventDefault(); jumpTo(t.dataset.jump); }
});
