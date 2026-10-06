// Which pipeline view a week shows (mockup: VIEWS, vizFor, setViz, the "Surprise me" handler).
// The choice is remembered per week in this browser only; every storage call is wrapped, so a
// blocked localStorage just means the default (and the choice lasts for this page).

import { useCallback, useState } from 'react';

export type ViewKind = 'drive' | 'map' | 'timeline';

export const VIEWS: { kind: ViewKind; label: string }[] = [
  { kind: 'drive', label: 'Drive chart' },
  { kind: 'map', label: 'Pipeline map' },
  { kind: 'timeline', label: 'Timeline' },
];

export const DEFAULT_VIEW: ViewKind = 'drive';
export const STORAGE_KEY = 'cr.vizByWeek';

const isView = (v: unknown): v is ViewKind => VIEWS.some((x) => x.kind === v);

/** The storage key inside the `cr.vizByWeek` object: "2026-4". */
export const weekKey = (season: number, week: number): string => `${season}-${week}`;

function readAll(): Record<string, unknown> {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    return raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export function readView(season: number, week: number): ViewKind {
  const v = readAll()[weekKey(season, week)];
  return isView(v) ? v : DEFAULT_VIEW;
}

export function writeView(season: number, week: number, view: ViewKind): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...readAll(), [weekKey(season, week)]: view }));
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
}

/** A different view, picked at random: never the current one. */
export function pickOtherView(current: ViewKind, random: () => number = Math.random): ViewKind {
  const others = VIEWS.map((v) => v.kind).filter((k) => k !== current);
  const i = Math.floor(random() * others.length);
  return others[Number.isFinite(i) ? Math.min(others.length - 1, Math.max(0, i)) : 0];
}

interface Choice {
  view: ViewKind;
  surprised: boolean;
}

/**
 * The view for one week. Choices made on this page win; otherwise the saved choice; otherwise the
 * Drive chart. "Surprise me" is remembered like any choice, and shows its chip until a view is
 * picked directly (the chip is per page, like the mockup's `surprised`).
 */
export function useViewChoice(
  season: number,
  week: number,
): { view: ViewKind; surprised: boolean; choose: (v: ViewKind | 'surprise') => void } {
  const key = weekKey(season, week);
  const [session, setSession] = useState<Record<string, Choice>>({});
  const view = session[key]?.view ?? readView(season, week);
  const surprised = session[key]?.surprised ?? false;
  const choose = useCallback(
    (v: ViewKind | 'surprise') => {
      const next = v === 'surprise' ? pickOtherView(view) : v;
      writeView(season, week, next);
      setSession((s) => ({ ...s, [key]: { view: next, surprised: v === 'surprise' } }));
    },
    [season, week, key, view],
  );
  return { view, surprised, choose };
}
