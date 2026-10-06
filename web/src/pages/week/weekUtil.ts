// Small helpers shared by the week tabs (formatting the mockup does inline).

import type { ProjectionRow } from '../../api/types';
import { fixed, fmtET, pct } from '../../lib/format';

/** The market line as the mockup writes it. nflverse's spread: + = the home team is favoured. */
export function marketLine(spread: number | null, home: string, away: string): string {
  if (spread == null) return '—';
  if (spread > 0) return `${home} −${fixed(spread)}`;
  if (spread < 0) return `${away} −${fixed(-spread)}`;
  return 'PK';
}

/** "Thu 8:15 PM ET" */
export function kickShort(iso: string | null | undefined): string {
  return `${fmtET(iso, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET`;
}

/** A YYYY-MM-DD date (US Eastern) as "Tue Oct 13". Noon UTC keeps it on the same day in ET. */
export function dateLabel(ymd: string | null | undefined): string {
  if (!ymd) return '—';
  return fmtET(`${ymd.slice(0, 10)}T12:00:00Z`, { weekday: 'short', month: 'short', day: 'numeric' }).replace(',', '');
}

/** 79 → "79", 2.64 → "2.6": whole numbers for big stats, one decimal for small ones. */
export function statNum(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return v.toFixed(Math.abs(v) >= 20 ? 0 : 1);
}

/** A projection in its own unit: a yes/no stat as a chance, the rest as a number. */
export function projText(r: Pick<ProjectionRow, 'kind' | 'projection'>): string {
  return r.kind === 'prob' ? pct(r.projection) : statNum(r.projection);
}

/** Signed difference, whole for big ones: +41 / −0.4. */
export function deltaText(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  const sign = v > 0 ? '+' : v < 0 ? '−' : '';
  return sign + Math.abs(v).toFixed(Math.abs(v) >= 10 ? 0 : 1);
}

export const SECTION_LABEL: Record<string, string> = {
  matchup_risk: 'Matchup / risk',
  non_obvious: 'Non-obvious',
  more: 'More',
};

/** "q2_injury_ripple" → "Q2 injury ripple" */
export function queryLabel(name: string): string {
  return name.replace(/^q(\d+)_/, 'Q$1 ').replace(/_/g, ' ');
}
