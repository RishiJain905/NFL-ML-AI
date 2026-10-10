// A finished week's decision review (LD03; mockup: review.js rvSummary, rvHighlights, rvList):
// the week's numbers, the boldest and costliest calls, the five costliest, and every 4th down by
// game. nflverse's play text (`desc`) is data: rendered as text, never HTML.

import { useEffect, useState } from 'react';
import type { LiveReviewResponse, ReviewGame, ReviewPlay, ReviewSummary } from '../../../api/types';
import { TipTarget } from '../../../charts/TipTarget';
import { TeamChip } from '../../../components/TeamChip';
import { Card, Segmented } from '../../../components/ui';
import { dayLabel } from '../../../lib/format';
import { p0, p1, pts } from './model';
import { ConfChip } from './parts';
import {
  CALL_COLOR,
  CALL_LONG,
  CALL_SHORT,
  CHOICES,
  EDGE_MAX,
  edgeWords,
  kickText,
  playClock,
  playKey,
  playLead,
  playsByGame,
  playScore,
  signedPts,
  wins,
} from './review';

type Filter = 'all' | 'disagree';

function Tags({ p }: { p: ReviewPlay }) {
  return (
    <>
      {p.fake ? (
        <span className="ptag" title="A fake punt or field goal counts as going for it">
          fake
        </span>
      ) : null}
      {p.wiped ? (
        <span className="ptag" title="Wiped out by a penalty after the snap; the down wasn't replayed">
          wiped
        </span>
      ) : null}
    </>
  );
}

function PlayText({ desc }: { desc: string }) {
  return (
    <p className="desc">
      <span className="src">nflverse play-by-play</span>
      {desc}
    </p>
  );
}

/* ---------- the week's numbers ---------- */
function CallsBars({ s }: { s: ReviewSummary }) {
  const row = (who: string, o: ReviewSummary['coach']) => {
    const tot = CHOICES.reduce((a, k) => a + o[k], 0) || 1;
    return (
      <div className="row">
        <span className="who">{who}</span>
        <span className="bar">
          {CHOICES.filter((k) => o[k] > 0).map((k) => (
            <TipTarget
              key={k}
              lines={[`${who}: ${CALL_LONG[k].toLowerCase()}`, `${o[k]} of ${tot} 4th downs (${p0(o[k] / tot)})`]}
              label={`${who}: ${CALL_LONG[k]} ${o[k]}`}
              style={{ width: `${((o[k] / tot) * 100).toFixed(2)}%`, background: CALL_COLOR[k] }}
            />
          ))}
        </span>
        <small>{CHOICES.map((k) => `${CALL_SHORT[k]} ${o[k]}`).join(' · ')}</small>
      </div>
    );
  };
  return (
    <div className="tile calls-tile">
      <span className="k">Who calls what</span>
      <div className="calls">
        {row('Coaches', s.coach)}
        {row('The bot', s.bot)}
        <span className="legend">
          {CHOICES.map((k) => (
            <span key={k}>
              <i style={{ background: CALL_COLOR[k] }} />
              {CALL_LONG[k]}
            </span>
          ))}
        </span>
      </div>
    </div>
  );
}

function Summary({ r, s }: { r: LiveReviewResponse; s: ReviewSummary }) {
  const teams = new Set(r.games.flatMap((g) => [g.away, g.home])).size;
  const extra = [
    s.wiped ? `${s.wiped} wiped out by a penalty` : '',
    s.fakes ? `${s.fakes} fake${s.fakes > 1 ? 's' : ''}` : '',
    s.skipped ? `${s.skipped} skipped` : '',
  ].filter(Boolean);
  return (
    <div className="rv-sum">
      <div className="tile">
        <span className="k">4th downs decided</span>
        <span className="v num">{s.decisions}</span>
        <span className="d">{[`${r.games.length} game${r.games.length === 1 ? '' : 's'}`, ...extra].join(' · ')}</span>
      </div>
      <div className="tile" title="Toss-up: the bot can't separate the options (under 1 point apart, or the call flips in re-checks); either call is defensible.">
        <span className="k">Coach and bot agree</span>
        <span className="v num">{p0(s.decisions ? s.agree / s.decisions : null)}</span>
        <span className="d">
          {s.agree} of {s.decisions} · {s.toss_ups} toss-ups
        </span>
      </div>
      <div className="tile" title="Go spots: 4th downs where the bot says go and is Confident or Lean (not a toss-up).">
        <span className="k">Went for it in go spots</span>
        <span className="v num">
          {s.went_in_go_spots}
          <small>of {s.go_spots}</small>
        </span>
        <span className="d">{p0(s.go_spots ? s.went_in_go_spots / s.go_spots : null)} of the spots where the bot says go</span>
      </div>
      <div className="tile" title="The win chance each call left on the table against the bot's best option, added up: 0.05 = 5 points of win chance.">
        <span className="k">Expected wins given up</span>
        <span className="v num">{wins(s.wp_lost)}</span>
        <span className="d">against the bot&apos;s calls · {teams} teams</span>
      </div>
      <CallsBars s={s} />
    </div>
  );
}

/* ---------- the highlights ---------- */
const HL = {
  bold: {
    name: 'Boldest call',
    def: 'The go-for-it with the lowest chance to convert, in a game still in doubt, when kicking was a real option',
    none: 'No long shot this week: no go-for-it in a game still in doubt when kicking was a real option.',
  },
  cost: {
    name: 'Costliest call',
    def: "The biggest gap between the bot's best option and the coach's call",
    none: "Every coach made the bot's call.",
  },
};

function Highlight({ kind, p, games, onJump }: { kind: 'bold' | 'cost'; p: ReviewPlay | null; games: ReviewGame[]; onJump: (k: string) => void }) {
  const h = HL[kind];
  const head = (
    <div className="hl-k">
      <span className="eyebrow">{h.name}</span>
      <small>{h.def}</small>
    </div>
  );
  if (!p)
    return (
      <Card className="hl">
        {head}
        <p className="none">{h.none}</p>
      </Card>
    );
  const g = games.find((x) => x.game_id === p.game_id);
  return (
    <Card className="hl">
      {head}
      <div className="hl-who">
        <TeamChip team={p.posteam} full />
        <span>{p.coach ?? '—'}</span>
        <span>
          · {g && p.posteam === g.home ? 'vs' : 'at'} {p.defteam}
        </span>
      </div>
      <div className="hl-sit">{p.situation}</div>
      <div className="hl-when">
        {playClock(p)} · {playLead(p)} ({playScore(p)})
      </div>
      <div className="hl-fig">
        {kind === 'bold' ? (
          <>
            <b className="num">{p0(p.convert)}</b>
            <span>chance to convert, by the bot&apos;s numbers</span>
          </>
        ) : (
          <>
            <b className="num">{p.cost == null ? '—' : pts(p.cost)}</b>
            <span>points of win chance given up</span>
          </>
        )}
      </div>
      <div className="hl-vs">
        <span>
          Coach: <b>{CALL_SHORT[p.choice]}</b>
        </span>
        <span>
          Bot: <b>{CALL_LONG[p.best]}</b>
        </span>
        <ConfChip label={p.label} small />
        {kind === 'bold' ? (
          <span className="muted">{p.agree ? `the bot agreed (${signedPts(p.edge)})` : `cost ${p.cost == null ? '—' : pts(p.cost)} points`}</span>
        ) : null}
      </div>
      <div className="hl-res">
        Result: <b>{p.result}</b> <Tags p={p} />
      </div>
      <PlayText desc={p.desc} />
      <div>
        <button type="button" className="btn sm" onClick={() => onJump(playKey(p))}>
          Show it in the list
        </button>
      </div>
    </Card>
  );
}

function TopFive({ plays, onJump }: { plays: ReviewPlay[]; onJump: (k: string) => void }) {
  const mx = Math.max(EDGE_MAX, ...plays.map((p) => p.cost ?? 0));
  return (
    <Card>
      <div className="card-h">
        <h2>The five costliest</h2>
        <span className="muted">points of win chance given up against the bot&apos;s best option</span>
      </div>
      <ol className="top5">
        {plays.length ? (
          plays.map((p, i) => (
            <li key={playKey(p)}>
              <button type="button" onClick={() => onJump(playKey(p))} aria-label={`${i + 1}. ${p.posteam} ${p.situation}, ${playClock(p)}: ${CALL_SHORT[p.choice]}, the bot says ${CALL_LONG[p.best]}; cost ${p.cost == null ? '—' : pts(p.cost)} points`}>
                <span className="n">{i + 1}</span>
                <span className="s">
                  <TeamChip team={p.posteam} /> <b>{p.situation}</b>
                  <small>
                    {playClock(p)} · {playLead(p)}
                    {p.coach ? ` · ${p.coach}` : ''}
                  </small>
                </span>
                <span className="c">
                  {CALL_SHORT[p.choice]} <span className="muted">→ bot:</span> <b>{CALL_LONG[p.best]}</b>
                  <small>{p.result}</small>
                </span>
                <span className="tr" aria-hidden="true">
                  <i style={{ width: `${(((p.cost ?? 0) / mx) * 100).toFixed(1)}%` }} />
                </span>
                <span className="v num">{p.cost == null ? '—' : pts(p.cost)}</span>
              </button>
            </li>
          ))
        ) : (
          <li className="muted" style={{ padding: '14px 18px' }}>
            No call cost anything this week.
          </li>
        )}
      </ol>
    </Card>
  );
}

/* ---------- every 4th down ---------- */
function Edge({ p }: { p: ReviewPlay }) {
  if (p.edge == null)
    return (
      <span className="edge" title="The coach's option isn't priced (a kick out of range)">
        <span className="eb" />
        <span className="ev muted">—</span>
      </span>
    );
  const cls = p.edge < 0 ? 'cost' : 'gain';
  return (
    <span className="edge" role="img" aria-label={edgeWords(p)}>
      <span className="eb" aria-hidden="true">
        <span className="z" />
        <i className={cls} style={{ width: `${(Math.min(1, Math.abs(p.edge) / EDGE_MAX) * 50).toFixed(1)}%` }} />
      </span>
      <span className={`ev num ${cls}`} aria-hidden="true">
        {signedPts(p.edge)}
      </span>
    </span>
  );
}

function Options({ p }: { p: ReviewPlay }) {
  return (
    <div className="mopts" role="list" aria-label={`${p.posteam} win chance after each option`}>
      {CHOICES.map((k) => {
        const v = p.wp[k];
        const best = k === p.best;
        const who = [k === p.choice ? 'coach' : '', best ? 'bot' : ''].filter(Boolean).join(' · ');
        return (
          <div key={k} className={`mopt${best && v != null ? ' best' : ''}`} role="listitem">
            <span className="on">{CALL_LONG[k]}</span>
            <span className="tr" aria-hidden="true">
              <span className="bg" />
              {v != null ? <span className="fill" style={{ width: `${(v * 100).toFixed(1)}%` }} /> : null}
            </span>
            <span className={`pv${v == null ? ' na' : ''}`}>{v == null ? 'not an option' : p1(v)}</span>
            <span className="who">{who}</span>
          </div>
        );
      })}
    </div>
  );
}

function PlayRow({ p }: { p: ReviewPlay }) {
  return (
    <details className={`rv-row${p.agree ? '' : ' dis'}`} id={`p-${playKey(p)}`}>
      <summary>
        <span className="when">{playClock(p)}</span>
        <span className="off">
          <TeamChip team={p.posteam} />
          <small className="num">{playScore(p)}</small>
        </span>
        <span className="sit">{p.situation}</span>
        <span className="coach">
          <span className="lbl-ph">Coach: </span>
          {CALL_SHORT[p.choice]}
        </span>
        <span className="bot">
          <span className="lbl-ph">Bot: </span>
          <span className="nm">{CALL_LONG[p.best]}</span>
          <ConfChip label={p.label} small />
        </span>
        <Edge p={p} />
        <span className="res">
          {p.result} <Tags p={p} />
        </span>
        <span className="chev" aria-hidden="true">
          ▸
        </span>
      </summary>
      <div className="rv-more">
        <div>
          <Options p={p} />
          <p className="optnote">
            <span>{p.posteam}&apos;s win chance after each option, on one 0–100% axis; the bot&apos;s best in the accent.</span>
          </p>
        </div>
        <div className="modds">
          <div className="kv">
            <span>
              If they go: <b>{p0(p.convert)}</b> to convert
            </span>
            <span>
              If they kick: <b>{kickText(p)}</b>
            </span>
          </div>
          <div className="kv">
            <span>
              The bot: <b>{CALL_LONG[p.best]}</b> by <b>{p.gap == null ? '—' : pts(p.gap)}</b> points
            </span>
            <ConfChip label={p.label} small />
            {p.coach ? (
              <span>
                Coach: <b>{p.coach}</b>
              </span>
            ) : null}
          </div>
          <PlayText desc={p.desc} />
        </div>
      </div>
    </details>
  );
}

function GameBlock({ g, plays, filtered }: { g: ReviewGame; plays: ReviewPlay[]; filtered: boolean }) {
  const won = (t: string) => (t === g.home ? (g.home_score ?? 0) >= (g.away_score ?? 0) : (g.away_score ?? 0) >= (g.home_score ?? 0));
  return (
    <section className="rv-game" aria-label={`${g.away} at ${g.home}`}>
      <div className="rv-gh">
        <span className="gm">
          <TeamChip team={g.away} />
          <span className={`sc${won(g.away) ? '' : ' lost'}`}>{g.away_score ?? ''}</span>
          <span className="muted">at</span>
          <TeamChip team={g.home} />
          <span className={`sc${won(g.home) ? '' : ' lost'}`}>{g.home_score ?? ''}</span>
        </span>
        <span className="meta">
          Final{g.kickoff ? ` · ${dayLabel(g.kickoff)}` : ''} · {g.decisions} 4th down{g.decisions === 1 ? '' : 's'}
          {filtered ? ` · ${plays.length} disagreement${plays.length === 1 ? '' : 's'}` : ''}
        </span>
        <span className="lost-w" title="Each team's cost against the bot in this game, added up (0.05 = 5 points of win chance)">
          Wins given up:{' '}
          {[g.away, g.home].map((t) => (
            <span key={t}>
              {t} <b>{wins(g.wp_lost[t])}</b>
            </span>
          ))}
        </span>
      </div>
      <ul className="rv-rows">
        {plays.map((p) => (
          <li key={playKey(p)}>
            <PlayRow p={p} />
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ReviewWeek({ r }: { r: LiveReviewResponse }) {
  const [filter, setFilter] = useState<Filter>('all');
  const [jump, setJump] = useState<{ key: string; n: number } | null>(null);
  const s = r.summary;
  const h = r.highlights;
  // open, scroll to and focus a row after the list has rendered it (the filter may have just changed)
  useEffect(() => {
    if (!jump) return;
    const el = document.getElementById(`p-${jump.key}`) as HTMLDetailsElement | null;
    if (!el) return;
    el.open = true;
    el.classList.add('flash');
    el.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    el.querySelector('summary')?.focus({ preventScroll: true });
    const t = window.setTimeout(() => el.classList.remove('flash'), 2400);
    return () => window.clearTimeout(t);
  }, [jump]);
  if (!s) return null;
  const onJump = (key: string) => {
    const p = r.plays.find((x) => playKey(x) === key);
    if (filter === 'disagree' && p?.agree) setFilter('all');
    setJump((j) => ({ key, n: (j?.n ?? 0) + 1 }));
  };
  const dis = r.plays.filter((p) => !p.agree).length;
  const shown = filter === 'disagree' ? r.plays.filter((p) => !p.agree) : r.plays;
  const groups = playsByGame(r.games, shown);
  return (
    <>
      <Summary r={r} s={s} />
      {h ? (
        <>
          <div className="hls">
            <Highlight kind="bold" p={h.boldest} games={r.games} onJump={onJump} />
            <Highlight kind="cost" p={h.costliest} games={r.games} onJump={onJump} />
          </div>
          <TopFive plays={h.top} onJump={onJump} />
        </>
      ) : null}
      <Card className="rv-list">
        <div className="card-h">
          <h2>Every 4th down</h2>
          <span className="muted">in kickoff order · open a row for the three options and the play</span>
          <div className="right">
            <span className="edgekey" aria-hidden="true">
              <span>
                <i style={{ background: 'var(--ink-2)' }} />
                cost against the bot
              </span>
              <span>
                <i style={{ background: 'color-mix(in srgb, var(--ink-3) 45%, var(--panel))' }} />
                gained over the next best
              </span>
            </span>
            <Segmented
              label="Which 4th downs"
              value={filter}
              onChange={setFilter}
              options={[
                { value: 'all', label: `All ${r.plays.length}` },
                { value: 'disagree', label: `Disagreements only ${dis}` },
              ]}
            />
          </div>
        </div>
        <div className="rv-cols" aria-hidden="true">
          <span>When</span>
          <span>Offense</span>
          <span>Situation</span>
          <span>Coach</span>
          <span>The bot</span>
          <span className="r" title="Points of win chance: + gained over the next best option, − cost against the bot">
            ± points
          </span>
          <span>Result</span>
          <span />
        </div>
        {groups.length ? (
          groups.map(({ game, plays }) => <GameBlock key={game.game_id} g={game} plays={plays} filtered={filter === 'disagree'} />)
        ) : (
          <p className="muted" style={{ padding: '14px 18px', margin: 0 }}>
            {filter === 'disagree' ? 'No disagreements this week.' : 'No 4th downs this week.'}
          </p>
        )}
      </Card>
    </>
  );
}
