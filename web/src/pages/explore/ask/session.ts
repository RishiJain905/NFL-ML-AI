// This session's questions (AE01): kept in the browser only, under localStorage `cr.askSession`.
// Every read and write is in try / catch: the page works without storage (a private window,
// blocked site data), and a full quota drops the oldest questions until the list fits.

import type { AskRequest, AskResponse } from '../../../api/types';

export const SESSION_KEY = 'cr.askSession';
export const SESSION_MAX = 50;

export interface SessionItem {
  id: string;
  request: AskRequest; // what was sent (the which-one card re-asks with it)
  answer: AskResponse;
}

function valid(x: unknown): x is SessionItem {
  if (!x || typeof x !== 'object') return false;
  const o = x as Partial<SessionItem>;
  return (
    typeof o.id === 'string' &&
    !!o.request &&
    typeof o.request.question === 'string' &&
    !!o.answer &&
    typeof o.answer.question === 'string' &&
    typeof o.answer.status === 'string' &&
    Array.isArray(o.answer.columns) &&
    Array.isArray(o.answer.rows)
  );
}

/** Newest first; anything unreadable is dropped. */
export function loadSession(): SessionItem[] {
  try {
    const raw = window.localStorage.getItem(SESSION_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    const items = Array.isArray(parsed) ? parsed : (parsed as { items?: unknown })?.items;
    return Array.isArray(items) ? items.filter(valid).slice(0, SESSION_MAX) : [];
  } catch {
    return [];
  }
}

/** Saves up to SESSION_MAX items; on a quota error drops the oldest and tries again. Returns
 *  how many were stored (0 when storage is unavailable). */
export function saveSession(items: SessionItem[]): number {
  let list = items.slice(0, SESSION_MAX);
  for (;;) {
    try {
      if (!list.length) window.localStorage.removeItem(SESSION_KEY);
      else window.localStorage.setItem(SESSION_KEY, JSON.stringify({ v: 1, items: list }));
      return list.length;
    } catch {
      if (!list.length) return 0;
      list = list.slice(0, -1);
    }
  }
}

let seq = 0;
export function newId(answer: AskResponse): string {
  seq += 1;
  return `${answer.asked_at || 'q'}-${seq}-${Math.random().toString(36).slice(2, 8)}`;
}
