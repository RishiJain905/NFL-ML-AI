// This session's questions (mockup: sessionCard), newest first: a status dot (with the status in
// words for screen readers), the question, the time and the route; a click shows that answer again.

import { STATUS_WORD, sessionMeta } from '../../../lib/ask';
import type { SessionItem } from './session';

export function SessionList({
  items,
  shown,
  onShow,
  onClear,
  stored,
}: {
  items: SessionItem[];
  shown: string | null;
  onShow: (id: string) => void;
  onClear: () => void;
  stored: boolean;
}) {
  return (
    <section className="card ask-hist" aria-labelledby="askHistH">
      <div className="card-h">
        <h2 id="askHistH">This session</h2>
        <span className="right">
          <span className="muted" style={{ fontSize: 12 }}>
            {items.length} question{items.length === 1 ? '' : 's'}
          </span>
        </span>
      </div>
      {items.length ? (
        <ol aria-label="Questions asked, newest first">
          {items.map((it) => (
            <li key={it.id}>
              <button type="button" aria-current={it.id === shown ? 'true' : undefined} onClick={() => onShow(it.id)}>
                <i className={`dot st-${it.answer.status}`} aria-hidden="true" />
                <span className="hq">{it.answer.question}</span>
                <span className="hm">{sessionMeta(it.answer)}</span>
                <span className="ask-sr">, {STATUS_WORD[it.answer.status] ?? it.answer.status}</span>
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <p className="none">Questions you ask stay here until you clear them.</p>
      )}
      <div className="foot">
        <span>{stored ? 'Kept in this browser only' : 'Browser storage is off: this list lasts until you leave the page'}</span>
        {items.length ? (
          <button type="button" className="btn sm" onClick={onClear}>
            Clear session
          </button>
        ) : null}
      </div>
    </section>
  );
}
