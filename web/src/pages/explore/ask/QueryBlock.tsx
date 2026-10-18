// The query that ran (mockup: queryBlock): collapsed by default, the language tag, Copy, the
// query in monospace (ligatures off), and the values bound to its $names.

import { useEffect, useRef, useState } from 'react';
import type { AskResponse } from '../../../api/types';
import { cellText } from '../../../lib/ask';

export function QueryBlock({ answer, label = 'The query' }: { answer: AskResponse; label?: string }) {
  const [copied, setCopied] = useState<'ok' | 'manual' | null>(null);
  const pre = useRef<HTMLPreElement>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  if (!answer.query) return null;
  const query = answer.query;
  const lang = answer.language === 'cypher' ? 'Cypher' : answer.language === 'sql' ? 'SQL' : 'Query';

  const flash = (v: 'ok' | 'manual') => {
    setCopied(v);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(null), 2000);
  };
  const selectText = () => {
    const sel = window.getSelection();
    if (!sel || !pre.current) return;
    const range = document.createRange();
    range.selectNodeContents(pre.current);
    sel.removeAllRanges();
    sel.addRange(range);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(query);
      flash('ok');
    } catch {
      // no clipboard (an insecure context, a denied permission): select it for Ctrl+C
      selectText();
      flash('manual');
    }
  };

  return (
    <details className="ask-q">
      <summary>
        <span className="tw" aria-hidden="true">
          ▶
        </span>
        {label}
        <span className="lang">{lang}</span>
        <span className="right">
          {copied ? (
            <span className="ask-copied" role="status">
              {copied === 'ok' ? 'Copied' : 'Selected: press Ctrl+C'}
            </span>
          ) : null}
          <button
            type="button"
            className="btn sm"
            onClick={(e) => {
              e.preventDefault(); // a button inside <summary> would toggle the block too
              const d = e.currentTarget.closest('details');
              if (d) d.open = true;
              void copy();
            }}
          >
            Copy
          </button>
        </span>
      </summary>
      <pre className="ask-code" tabIndex={0} aria-label={`${lang} query`} ref={pre}>
        {query}
      </pre>
      {answer.params.length ? (
        <div className="ask-params">
          <span className="eyebrow">Bound values</span>
          <dl>
            {answer.params.map((p) => (
              <div key={p.name} style={{ display: 'contents' }}>
                <dt>${p.name}</dt>
                <dd>{Array.isArray(p.value) ? JSON.stringify(p.value) : cellText(p.value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </details>
  );
}
