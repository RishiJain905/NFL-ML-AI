// The answer to "Check this play" (mockup: callBlock, fourthCard, thirdCard, noneCard): the feed
// notices, then the 4th-down call, the 3rd-down if-stopped table, or why there's no call.
// ESPN's text is rendered as text, never as HTML.

import type { LiveCallResponse, LiveFourth, LiveThird } from '../../../api/types';
import { TipTarget } from '../../../charts/TipTarget';
import {
  RECHECK_LINES,
  capFirst,
  labelLine,
  leadText,
  optionTone,
  p0,
  p1,
  pts,
  quarter,
  rowTip,
  tSec,
  verdict,
} from './model';
import { AsOf, ConfChip, LabelKey } from './parts';

function CallHead({ call, kind, receivedAt }: { call: LiveCallResponse; kind: string; receivedAt: number | null }) {
  const g = call.game;
  return (
    <div className="ch">
      <div className="what">
        <span className="eyebrow">
          {kind} · {call.offense} ball
        </span>
        <b>{g.situation}</b>
        <span className="muted">
          {quarter(g.period)} {g.clock ?? ''} · {leadText(g, call.offense)}
        </span>
      </div>
      <div className="right">
        <AsOf feed={call.feed} game={g} receivedAt={receivedAt} />
      </div>
    </div>
  );
}

function CallFoot({ call, ms }: { call: LiveCallResponse; ms: number }) {
  return (
    <div className="callfoot">
      <span>
        Decision models <span className="mono">{call.models.version ?? '—'}</span>
      </span>
      <span>computed in {ms} ms</span>
      <span>ESPN&apos;s public site API (unofficial)</span>
      <span>Win chances are {call.offense}&apos;s, from the models; ESPN&apos;s own % is shown only as a reference.</span>
    </div>
  );
}

export function FourthCard({ call, f, receivedAt }: { call: LiveCallResponse; f: LiveFourth; receivedAt: number | null }) {
  const v = verdict(f);
  const situation = (call.game.situation ?? '').split(' at ')[0];
  const best = Math.max(...f.options.map((o) => o.wp ?? 0));
  return (
    <div className="card callcard">
      <CallHead call={call} kind="4th down" receivedAt={receivedAt} />
      <div className="callgrid">
        <div className="verdict">
          <span className="eyebrow">The call</span>
          <div className={`big${v.toss ? ' toss' : ''}`}>{v.big}</div>
          <div className="sub">{v.sub}</div>
          <span style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <ConfChip label={f.label} />
            <TipTarget className="muted recheck" lines={RECHECK_LINES} label={RECHECK_LINES.join(' ')}>
              What&apos;s a re-check?
            </TipTarget>
          </span>
          <p className="expl">{labelLine(f)}</p>
          <LabelKey current={f.label} />
        </div>
        <div>
          {/* one honest 0-100% axis; the gap is said in words (wp_now isn't shown on a 4th down) */}
          <div className="opts" role="list" aria-label={`${call.offense} win chance after each choice`}>
            {f.options.map((o) => {
              const tone = optionTone(f, o.choice);
              if (o.wp == null)
                return (
                  <div key={o.choice} className="opt" role="listitem">
                    <span className="on">{o.name}</span>
                    <span className="tr">
                      <span className="bg" />
                    </span>
                    <span className="pv na">not an option</span>
                  </div>
                );
              const lines = [
                o.name,
                `${call.offense} win chance after it: ${p1(o.wp)}`,
                best - o.wp < 1e-9 ? 'The best option' : `${pts(best - o.wp)} points behind the best`,
              ];
              return (
                <div key={o.choice} role="listitem">
                  <TipTarget as="div" className={`opt${tone ? ` ${tone}` : ''}`} lines={lines} label={`${o.name}: ${p1(o.wp)}`}>
                    <span className="on">{o.name}</span>
                    <span className="tr">
                      <span className="bg" />
                      <span className="fill" style={{ width: `${(o.wp * 100).toFixed(2)}%` }} />
                    </span>
                    <span className="pv num">{p1(o.wp)}</span>
                  </TipTarget>
                </div>
              );
            })}
          </div>
          <div className="axis" aria-hidden="true">
            <span />
            <span className="ticks">
              <span style={{ left: 0 }}>0%</span>
              <span style={{ left: '50%' }}>50%</span>
              <span style={{ left: '100%' }}>100%</span>
            </span>
            <span />
          </div>
          <p className="optnote">
            <span>
              {call.offense}&apos;s win chance after each choice. The bars start at 0%, so close calls look close.
            </span>
          </p>
        </div>
      </div>
      <div className="odds">
        <div className="odd">
          <span className="k">If they go</span>
          <span className="v">{p0(f.convert)}</span>
          <span className="d">to convert {situation}</span>
        </div>
        <div className="odd">
          <span className="k">If they kick</span>
          <span className="v">{f.fg_make == null ? '—' : p0(f.fg_make)}</span>
          <span className="d">
            {f.fg_make == null ? `out of range (${Math.round(f.fg_distance)} yards)` : `to make it from ${Math.round(f.fg_distance)} yards`}
          </span>
        </div>
        <div className="odd">
          <span className="k">If they punt</span>
          <span className="v">Own {f.punt_start == null ? '—' : Math.round(f.punt_start)}</span>
          <span className="d">where {call.defense} starts, on average</span>
        </div>
        {call.espn_offense_wp != null ? (
          <div className="odd ref">
            <span className="k">ESPN&apos;s win chance</span>
            <span className="v">{p0(call.espn_offense_wp)}</span>
            <span className="d">for {call.offense} now · a reference, not part of the call</span>
          </div>
        ) : null}
      </div>
      <CallFoot call={call} ms={f.ms} />
    </div>
  );
}

export function ThirdCard({ call, t, receivedAt }: { call: LiveCallResponse; t: LiveThird; receivedAt: number | null }) {
  const behind = Boolean(call.behind?.likely);
  return (
    <div className="card callcard">
      <CallHead call={call} kind="3rd down" receivedAt={receivedAt} />
      <div className="third">
        <div className="fig">
          <b className="num">{p0(t.convert)}</b>
          <span>chance they convert</span>
        </div>
        <div className="fig">
          <b className="num">{p0(t.pass_prob)}</b>
          <span>chance they pass</span>
        </div>
        <div className="fig small">
          <b className="num">{p0(t.wp_now)}</b>
          <span>{call.offense}&apos;s win chance now</span>
        </div>
        {call.espn_offense_wp != null ? (
          <div className="fig small">
            <b className="num">{p0(call.espn_offense_wp)}</b>
            <span>ESPN&apos;s, for reference</span>
          </div>
        ) : null}
      </div>
      <div className="ifs">
        <h3>If they&apos;re stopped short</h3>
        <p>
          The 4th-down call for every spot they could be left with, ready before ESPN posts the 4th down. &quot;By&quot; is
          how many points of win chance the call is ahead of the next best.
        </p>
        {t.note ? (
          <div className="notice">
            <span className="ic" aria-hidden="true">
              i
            </span>
            <div>{t.note}</div>
          </div>
        ) : null}
        <div className="tablewrap">
          <table className="tbl iftbl">
            <thead>
              <tr>
                <th className="gain">If they gain</th>
                <th>They face</th>
                <th>The call</th>
                <th>
                  <TipTarget className="recheck" lines={['How sure the bot is', 'See the key under a 4th-down call']} label="How sure">
                    How sure
                  </TipTarget>
                </th>
                <th className="r">By</th>
              </tr>
            </thead>
            <tbody>
              {t.table.map((r) => {
                const cls = r.gain === 0 ? (behind ? 'yours' : 'nogain') : r.gain < 0 ? 'loss' : '';
                const tag = behind ? 'your TV, likely' : 'the line now';
                return (
                  <tr key={r.gain} className={cls}>
                    <td className="gain">
                      {capFirst(r.gain_text)}
                      {r.gain === 0 ? <span className="tag">· {tag}</span> : null}
                    </td>
                    <td className="num face">
                      {r.situation}
                      {r.gain === 0 ? <span className="tag ph">{behind ? tag : 'no gain'}</span> : null}
                    </td>
                    <td className={`call${r.label === 'Toss-up' ? ' toss' : ''}`}>{r.best_name}</td>
                    <td>
                      <ConfChip label={r.label} small />
                    </td>
                    <td className="r num">
                      <TipTarget lines={rowTip(r)} label={`${r.situation}: ${r.best_name} by ${pts(r.gap)} points`}>
                        +{pts(r.gap)}
                      </TipTarget>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <CallFoot call={call} ms={t.ms} />
    </div>
  );
}

export function NoneCard({ call, receivedAt }: { call: LiveCallResponse; receivedAt: number | null }) {
  const down = (call.game.situation ?? '').split(' ')[0];
  return (
    <div className="card callcard">
      <CallHead call={call} kind={down ? `${capFirst(down)} down` : 'This play'} receivedAt={receivedAt} />
      <div className="empty" style={{ padding: '30px 20px' }}>
        <h3>{call.reason ?? 'Not a 3rd or 4th down'}</h3>
        <p>Press Check again when it&apos;s 3rd or 4th down.</p>
      </div>
    </div>
  );
}

/** The notices above the card, then the card. */
export function CallNotices({ call }: { call: LiveCallResponse }) {
  const noGain = call.third?.table.find((r) => r.gain === 0);
  return (
    <>
      {call.feed.stale ? (
        <div className="notice warn" role="status">
          <span className="ic warn" aria-hidden="true">
            !
          </span>
          <div>
            <b>ESPN didn&apos;t answer ({call.feed.error ?? 'error'}).</b> Showing the last good answer, from{' '}
            {tSec(call.feed.as_of)}. The play may have moved on; press Check again in a few seconds.
          </div>
        </div>
      ) : call.repeat.same ? (
        <div className="notice" role="status">
          <span className="ic" aria-hidden="true">
            =
          </span>
          <div>
            <b>{call.repeat.text ?? 'No new play since your last check.'}</b> A commercial or a review: the call below
            is the same as last time.
          </div>
        </div>
      ) : null}
      {call.behind?.likely ? (
        <div className="notice accent" role="status">
          <span className="ic" aria-hidden="true">
            i
          </span>
          <div>
            <b>{call.behind.text}</b>
            {noGain ? (
              <>
                {' '}
                If your TV already shows <b>{noGain.situation}</b>, the highlighted row is the call.
              </>
            ) : null}
          </div>
        </div>
      ) : null}
      {call.warnings.map((w) => (
        <div key={w} className="notice">
          <span className="ic" aria-hidden="true">
            i
          </span>
          <div>Assumed: {w}</div>
        </div>
      ))}
    </>
  );
}
