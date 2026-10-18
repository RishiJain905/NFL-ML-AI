// The question box (mockup: askBox): the textarea (Enter asks, Shift+Enter is a new line), the
// character counter, the source picker, Ask, the featured example chips and "More examples".
// An example fills the box and focuses it; it never asks by itself (the AE01 mockup review).

import { forwardRef, type FormEvent, type KeyboardEvent } from 'react';
import type { AskExample, AskExamplesResponse, AskSource } from '../../../api/types';
import { Segmented } from '../../../components/ui';

const SOURCES: { value: AskSource; label: string; title: string }[] = [
  { value: 'auto', label: 'Auto', title: 'The engine picks: the weekly graph or the curated tables' },
  { value: 'graph', label: 'Weekly graph', title: 'Only the weekly graph (Neo4j): connections, this week’s games, coaching trees' },
  { value: 'sql', label: 'Tables', title: 'Only the curated tables: stats, records, play-by-play' },
];

function ExampleText({ e }: { e: AskExample }) {
  return (
    <>
      {e.question}
      {e.this_week ? <span className="wk">this week</span> : null}
    </>
  );
}

export const AskBox = forwardRef<
  HTMLTextAreaElement,
  {
    question: string;
    onQuestion: (q: string) => void;
    source: AskSource;
    onSource: (s: AskSource) => void;
    max: number;
    running: boolean;
    onAsk: () => void;
    onExample: (q: string) => void;
    examples: AskExamplesResponse | undefined;
    examplesState: 'loading' | 'error' | 'ok';
  }
>(function AskBox({ question, onQuestion, source, onSource, max, running, onAsk, onExample, examples, examplesState }, ref) {
  const n = question.length;
  const over = n > max;
  const canAsk = !running && !over && question.trim().length > 0;
  const gDown = examples ? ['down', 'config_problem'].includes(examples.sources.graph.status) : false;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (canAsk) onAsk();
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (canAsk) onAsk();
    }
  };
  const total = examples?.groups.reduce((s, g) => s + g.examples.length, 0) ?? 0;
  return (
    <form className="card ask-box" onSubmit={submit} autoComplete="off" aria-label="Ask a question">
      <label className="ask-lbl" htmlFor="askInput">
        Ask a question about NFL data
      </label>
      <textarea
        id="askInput"
        ref={ref}
        className="ask-input"
        rows={3}
        value={question}
        placeholder="Who has Patrick Mahomes thrown to the most this season?"
        aria-describedby="askCount askKeys"
        aria-invalid={over}
        onChange={(e) => onQuestion(e.target.value)}
        onKeyDown={onKey}
      />
      <div className="ask-foot">
        <span className="ask-src">
          <span className="eyebrow">Source</span>
          <Segmented
            label="Source"
            options={SOURCES.map((s) => ({
              ...s,
              label:
                s.value === 'graph' && gDown ? (
                  <>
                    {s.label} <span className="muted">(down)</span>
                  </>
                ) : (
                  s.label
                ),
            }))}
            value={source}
            onChange={onSource}
          />
        </span>
        <span className="ask-go">
          <span className="ask-keys" id="askKeys">
            <kbd>Enter</kbd> to ask · <kbd>Shift</kbd>+<kbd>Enter</kbd> new line
          </span>
          <span className={`ask-count${over ? ' over' : n > max * 0.9 ? ' near' : ''}`} id="askCount" aria-live="polite">
            {n} / {max}
            {over ? ' · too long' : ''}
          </span>
          <button type="submit" className="btn primary" disabled={!canAsk}>
            {running ? 'Asking…' : 'Ask'}
          </button>
        </span>
      </div>
      <div className="ask-ex">
        <span className="eyebrow">Try one</span>
        {examplesState === 'loading' ? <span className="muted">Loading examples…</span> : null}
        {examplesState === 'error' ? (
          <span className="muted">The examples didn&apos;t load; you can still ask.</span>
        ) : null}
        {examples ? (
          <>
            <div className="ask-chips">
              {examples.featured.map((e) => (
                <button key={e.question} type="button" className="ask-chip" title={e.title} onClick={() => onExample(e.question)}>
                  <ExampleText e={e} />
                </button>
              ))}
            </div>
            {examples.groups.length ? (
              <details className="ask-more">
                <summary>More examples ({total})</summary>
                <div className="ask-groups">
                  {examples.groups.map((g) => (
                    <div key={g.source} className="ask-group">
                      <h3>
                        {g.label}
                        <small>{g.examples.length}</small>
                      </h3>
                      <ul>
                        {g.examples.map((e) => (
                          <li key={e.question}>
                            <button type="button" title={e.title} onClick={() => onExample(e.question)}>
                              <span className="grow">{e.question}</span>
                              {e.this_week ? <span className="wk">this week</span> : null}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </details>
            ) : null}
          </>
        ) : null}
      </div>
    </form>
  );
});
