// Explore -> Ask the Engine (AE01; mockup: documentation/ask-the-engine/mockup/ask.js). A
// question box with example chips; the answer (sentences, the rows, the query, the source, the
// route, time and cost) or a refusal / which-one / no-rows / error card; this session's questions
// (localStorage `cr.askSession`, browser only). One question at a time: Cancel (or Escape) only
// stops waiting, the server finishes the question.

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, askQuestion, useAskExamples } from '../../api/client';
import type { AskChoice, AskExamplesResponse, AskPick, AskRequest, AskSource } from '../../api/types';
import { TipTarget } from '../../charts/TipTarget';
import { GRAPH_STATUS, requestRefusedTitle } from '../../lib/ask';
import { AnswerCard } from './ask/AnswerCard';
import { AskBox } from './ask/AskBox';
import { SessionList } from './ask/SessionList';
import { loadSession, newId, saveSession, SESSION_MAX, type SessionItem } from './ask/session';
import { ExploreHeader } from './common';
import './ask.css';

const DEFAULT_MAX = 500;

interface Running {
  question: string;
  started: number;
  controller: AbortController;
}
interface RequestNotice {
  status: number;
  code: string;
  message: string;
}

function StatusLine({ ex }: { ex: AskExamplesResponse }) {
  const g = ex.sources.graph;
  const q = ex.sources.sql;
  const m = ex.model;
  const gs = GRAPH_STATUS[g.status] ?? { tone: 'flat', label: g.status };
  const gLabel = g.on ? (g.label ?? 'weekly graph: not built') : 'weekly graph: off';
  const qLabel = q.on ? (q.label ?? 'curated data') : 'curated data: off';
  return (
    <div className="ask-status" aria-label="Sources and model">
      <TipTarget
        lines={[
          'The weekly graph (Neo4j)',
          g.label ?? 'not built',
          `Rebuilt every week by the weekly run; this-week questions read it as of week ${g.week ?? '?'}`,
          `Neo4j: ${gs.label}`,
        ]}
        label={`${gLabel}, ${gs.label}`}
      >
        <i className={`dot ${gs.tone}`} aria-hidden="true" />
        {gLabel} · {gs.label}
      </TipTarget>
      <TipTarget
        lines={['The curated tables (DuckDB in memory)', q.label ?? '—', ...(q.rebuilt ? [`Rebuilt ${q.rebuilt} by the weekly run's curate step`] : [])]}
        label={qLabel}
      >
        <i className={`dot ${q.on && q.status === 'up' ? 'ok' : 'flat'}`} aria-hidden="true" />
        {qLabel}
      </TipTarget>
      <TipTarget
        lines={[
          `The model: ${m.name ?? '—'}`,
          `Reasoning effort: ${m.effort ?? '—'} · via ${m.provider}`,
          `A model call gives up after ${m.timeout_s} s`,
          `Limits: ${ex.limits.question_chars} characters, ${ex.limits.per_minute} questions a minute, ${ex.limits.rows} rows, a ${ex.limits.query_timeout_s} s query limit`,
        ]}
        label={`Model ${m.label ?? 'unknown'}, effort ${m.effort ?? 'unknown'}`}
      >
        <span className="muted">Model</span> <b>{m.label ?? '—'}</b> · {m.effort ?? '—'}
      </TipTarget>
    </div>
  );
}

function Banners({ ex }: { ex: AskExamplesResponse }) {
  const g = ex.sources.graph;
  const down = g.on && (g.status === 'down' || g.status === 'config_problem');
  return (
    <>
      {down ? (
        <div className="notice ask-banner warn" role="status">
          <span className="ic" aria-hidden="true">
            !
          </span>
          <div>
            <b>The weekly graph isn&apos;t answering (Neo4j {g.status === 'down' ? 'down' : 'config problem'}).</b> Graph
            questions can&apos;t be answered now; questions about the tables still work.
          </div>
        </div>
      ) : null}
      {ex.busy ? (
        <div className="notice ask-banner warn" role="status">
          <span className="ic" aria-hidden="true">
            !
          </span>
          <div>
            <b>The weekly refresh is running.</b> The tables and the graph are being rebuilt, so questions are refused
            until it ends.
          </div>
        </div>
      ) : null}
    </>
  );
}

function RunningCard({ running, onCancel }: { running: Running; onCancel: () => void }) {
  const [now, setNow] = useState(running.started);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, []);
  const s = Math.max(0, Math.floor((now - running.started) / 1000));
  return (
    <div className="card ask-run" aria-busy="true" role="status" aria-label="Asking">
      <div>
        <span className="eyebrow">Asking</span>
        <div className="q">{running.question}</div>
      </div>
      <div className="st">
        <span className="ask-dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span className="secs" aria-label={`${s} seconds so far`}>
          {s} s
        </span>
        <button type="button" className="btn sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <div className="note">
        Most answers take 2–5 s; a free-form query up to about 30 s. <b>Cancel</b> (or <kbd>Esc</kbd>) only stops
        waiting: the server still finishes the question and logs its cost.
      </div>
    </div>
  );
}

export function AskPage() {
  const examples = useAskExamples();
  const ex = examples.data;
  const max = ex?.limits.question_chars ?? DEFAULT_MAX;

  const [items, setItems] = useState<SessionItem[]>(loadSession);
  const itemsRef = useRef(items); // the list as last committed (an answer arrives after an await)
  const [stored, setStored] = useState(true);
  const [shown, setShown] = useState<string | null>(() => items[0]?.id ?? null);
  const [question, setQuestion] = useState('');
  const [source, setSource] = useState<AskSource>('auto');
  const [running, setRunning] = useState<Running | null>(null);
  const [notice, setNotice] = useState<RequestNotice | null>(null);
  const [focusTick, setFocusTick] = useState(0);
  const runRef = useRef<Running | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const commit = useCallback((next: SessionItem[]) => {
    const list = next.slice(0, SESSION_MAX);
    itemsRef.current = list;
    setItems(list);
    setStored(saveSession(list) === list.length);
  }, []);

  // focus moves to a new answer's heading (and to a re-shown one)
  useEffect(() => {
    if (focusTick) heading.current?.focus();
  }, [focusTick]);

  const ask = useCallback(
    async (req: AskRequest) => {
      if (runRef.current) return;
      const q = req.question.trim();
      if (!q || q.length > max) return;
      const body: AskRequest = { ...req, question: q };
      const run: Running = { question: q, started: Date.now(), controller: new AbortController() };
      runRef.current = run;
      setRunning(run);
      setNotice(null);
      try {
        const answer = await askQuestion(body, run.controller.signal);
        if (runRef.current !== run) return; // cancelled while waiting
        const item: SessionItem = { id: newId(answer), request: body, answer };
        commit([item, ...itemsRef.current]);
        setShown(item.id);
        setFocusTick((t) => t + 1);
      } catch (e) {
        if (runRef.current !== run) return; // cancelled: an AbortError, nothing to say
        if (e instanceof ApiError) setNotice({ status: e.status, code: e.code, message: e.message });
        else if (e instanceof DOMException && e.name === 'AbortError') return;
        else setNotice({ status: 0, code: 'network', message: 'The app didn’t answer: is `nfl app` still running?' });
      } finally {
        if (runRef.current === run) {
          runRef.current = null;
          setRunning(null);
        }
      }
    },
    [max, commit],
  );

  const cancel = useCallback(() => {
    const run = runRef.current;
    if (!run) return;
    runRef.current = null;
    run.controller.abort();
    setRunning(null);
    input.current?.focus();
  }, []);

  // Escape cancels a running question
  useEffect(() => {
    if (!running) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [running, cancel]);

  // leaving the page stops waiting too
  useEffect(() => () => runRef.current?.controller.abort(), []);

  const current = items.find((i) => i.id === shown) ?? null;

  const onPick = (choice: AskChoice, value: string) => {
    if (!current) return;
    const pick: AskPick = { text: choice.text, kind: choice.kind, value };
    const earlier = (current.request.picks ?? []).filter((p) => !(p.text === pick.text && p.kind === pick.kind));
    void ask({ ...current.request, picks: [...earlier, pick].slice(0, 5) });
  };

  const onExample = (q: string) => {
    setQuestion(q);
    const el = input.current;
    if (el) {
      el.focus();
      // after React has written the value: put the cursor at the end
      window.requestAnimationFrame(() => el.setSelectionRange(q.length, q.length));
    }
  };

  return (
    <>
      <ExploreHeader eyebrow="Explore" title="Ask the Engine" chips={ex ? <StatusLine ex={ex} /> : undefined} />
      <section className="view">
        {ex ? <Banners ex={ex} /> : null}
        <div className="ask-layout">
          <div className="ask-main">
            <AskBox
              ref={input}
              question={question}
              onQuestion={setQuestion}
              source={source}
              onSource={setSource}
              max={max}
              running={!!running}
              onAsk={() => void ask({ question, source })}
              onExample={onExample}
              examples={ex}
              examplesState={examples.isLoading ? 'loading' : examples.isError && !ex ? 'error' : 'ok'}
            />
            {notice ? (
              <div className={`notice ask-banner ${notice.status === 422 ? 'err' : 'warn'}`} role="alert">
                <span className="ic" aria-hidden="true">
                  !
                </span>
                <div>
                  <b>{requestRefusedTitle(notice.status, notice.code)}</b> {notice.message}
                  {notice.status ? <span className="muted"> (HTTP {notice.status})</span> : null}
                </div>
                <button type="button" className="btn sm ask-x" onClick={() => setNotice(null)}>
                  Dismiss
                </button>
              </div>
            ) : null}
            {running ? (
              <RunningCard key={running.started} running={running} onCancel={cancel} />
            ) : current ? (
              <AnswerCard key={current.id} ref={heading} answer={current.answer} limits={ex?.limits} onPick={onPick} />
            ) : (
              <div className="card">
                <div className="empty">
                  <div className="glyph" aria-hidden="true">
                    ?
                  </div>
                  <h3>Ask about players, teams, games or coaches</h3>
                  <p>
                    The answer comes with the rows behind it and the exact query that produced them. Pick an example above
                    to start from one.
                  </p>
                </div>
              </div>
            )}
          </div>
          <aside className="ask-rail">
            <SessionList
              items={items}
              shown={running ? null : shown}
              stored={stored}
              onShow={(id) => {
                if (runRef.current) return;
                setShown(id);
                // as the mockup: the box gets that question back, ready to edit and ask again
                const it = items.find((i) => i.id === id);
                if (it) setQuestion(it.request.question);
                setNotice(null);
                setFocusTick((t) => t + 1);
              }}
              onClear={() => {
                commit([]);
                setShown(null);
              }}
            />
          </aside>
        </div>
      </section>
    </>
  );
}
