// Play calling's page text helpers (PC01): the "as of" line, the empty state's title, the
// `?season=` parameter.

import type { PlaycallMeta } from '../../api/types';

/** "as of week 5 · weeks 1–4 played". */
export function asOfText(meta: Pick<PlaycallMeta, 'as_of_week' | 'through_week'>): string {
  if (meta.as_of_week == null) return '';
  const played = meta.through_week ? ` · weeks 1–${meta.through_week} played` : ' · before week 1';
  return `as of week ${meta.as_of_week}${played}`;
}

/** The empty state's title for a status other than ok. */
export function emptyTitle(status: PlaycallMeta['status'], season: number): string {
  return status === 'not_yet' ? 'Not ready yet' : `No play-calling tables for ${season}`;
}

/** A season from `?season=` (four digits), else undefined (the newest built season). */
export function seasonParam(raw: string | null): number | undefined {
  return raw && /^\d{4}$/.test(raw) ? Number(raw) : undefined;
}
