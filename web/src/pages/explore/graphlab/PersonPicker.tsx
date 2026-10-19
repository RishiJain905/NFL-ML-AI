// A person picker (AE03): a combobox over GET /api/ask/people. Two letters at least, 200 ms
// after the last key; each match shows the name, position or "coach", team, seasons and links.
// Arrows move, Enter picks, Escape closes. A picked person becomes a card with a clear button.

import { useEffect, useId, useRef, useState } from 'react';
import { usePeopleSearch } from '../../../api/client';
import type { GraphLabPersonKind, PersonMatch } from '../../../api/types';
import { int, personSub } from '../../../lib/graphlab';

const DEBOUNCE_MS = 200;

export function PersonPicker({
  label,
  value,
  onChange,
  kind,
  placeholder = 'A player or a coach',
  inputId,
  onPicked,
}: {
  label: string;
  value: PersonMatch | null;
  onChange: (p: PersonMatch | null) => void;
  kind?: GraphLabPersonKind;
  placeholder?: string;
  inputId?: string; // the input's id (a page moves focus to it)
  /** After a pick, where focus goes (default: the picked card's clear button). */
  onPicked?: () => void;
}) {
  const autoId = useId();
  const id = inputId ?? autoId;
  const [text, setText] = useState('');
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const clearRef = useRef<HTMLButtonElement>(null);
  const ownInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setQ(text), DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [text]);

  const search = usePeopleSearch(q, kind);
  const results = q.trim().length >= 2 ? (search.data?.people ?? []) : [];
  const typing = text.trim() !== q.trim() || search.isFetching;
  const long = text.trim().length >= 2;
  const showList = open && long;
  const listId = `${id}-list`;
  const optId = (i: number) => `${id}-o${i}`;

  const choose = (p: PersonMatch) => {
    onChange(p);
    setText('');
    setQ('');
    setOpen(false);
    setActive(0);
    // the input is replaced by the card: keep focus on the page
    window.requestAnimationFrame(() => (onPicked ? onPicked() : clearRef.current?.focus()));
  };

  if (value) {
    return (
      <div className="gl-pick">
        <span className="eyebrow" id={`${id}-l`}>
          {label}
        </span>
        <div className="gl-picked" role="group" aria-labelledby={`${id}-l`}>
          <span className="who">
            <b>{value.name || 'Loading…'}</b>
            <small>{value.name ? personSub(value, true) : ''}</small>
          </span>
          <button
            ref={clearRef}
            type="button"
            className="gl-x"
            aria-label={`Change ${label.toLowerCase()}: clear ${value.name || 'this person'}`}
            onClick={() => {
              onChange(null);
              // the input takes the card's place: focus it once it's drawn
              window.requestAnimationFrame(() => ownInput.current?.focus());
            }}
          >
            ×
          </button>
        </div>
        <span className="gl-hint" />
      </div>
    );
  }

  let hint = '';
  if (text.trim().length === 1) hint = 'Type one more letter';
  else if (showList && !typing) hint = `${results.length} match${results.length === 1 ? '' : 'es'}`;

  return (
    <div className="gl-pick">
      <label htmlFor={id}>{label}</label>
      <div className="gl-field">
        <input
          id={id}
          ref={ownInput}
          type="text"
          role="combobox"
          autoComplete="off"
          spellCheck={false}
          maxLength={60}
          placeholder={placeholder}
          value={text}
          aria-autocomplete="list"
          aria-expanded={showList && results.length > 0}
          aria-controls={listId}
          aria-activedescendant={
            showList && results.length ? optId(Math.min(active, results.length - 1)) : undefined
          }
          aria-describedby={`${id}-h`}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
              if (!results.length) return;
              e.preventDefault();
              setOpen(true);
              setActive(
                (a) => (a + (e.key === 'ArrowDown' ? 1 : -1) + results.length) % results.length,
              );
            } else if (e.key === 'Enter') {
              if (showList && results.length) {
                e.preventDefault();
                choose(results[Math.min(active, results.length - 1)]);
              } else e.preventDefault(); // never submit a half-typed name
            } else if (e.key === 'Escape') {
              if (showList) {
                e.stopPropagation();
                setOpen(false);
              }
            }
          }}
        />
        {showList ? (
          <ul className="gl-list" id={listId} role="listbox" aria-label={`${label}: matches`}>
            {typing && !results.length ? (
              <li className="none" role="presentation">
                Searching…
              </li>
            ) : search.isError ? (
              <li className="none" role="presentation">
                The search didn’t answer: is `nfl app` still running?
              </li>
            ) : search.data && !search.data.ready ? (
              <li className="none" role="presentation">
                No people index yet: run <code>uv run nfl history-graph derive</code>.
              </li>
            ) : !results.length ? (
              <li className="none" role="presentation">
                No {kind === 'player' ? 'player' : 'player or coach'} matches “{text.trim()}”.
              </li>
            ) : (
              results.map((p, i) => (
                <li
                  key={p.id}
                  id={optId(i)}
                  role="option"
                  aria-selected={i === active}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => choose(p)}
                  onMouseEnter={() => setActive(i)}
                >
                  <span className="nm">{p.name}</span>
                  <span className="sub">{personSub(p, true)}</span>
                  {p.links != null ? <span className="ln">{int(p.links)} links</span> : null}
                </li>
              ))
            )}
          </ul>
        ) : null}
      </div>
      <span className="gl-hint" id={`${id}-h`} aria-live="polite">
        {hint}
      </span>
    </div>
  );
}
