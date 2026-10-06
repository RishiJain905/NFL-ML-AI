// Formatting shared by the screens. Kickoff times are stored in UTC and shown in US Eastern
// with daylight saving handled by Intl, as the calendar does (ops/calendar.py).

const ET = 'America/New_York';

export function fmtET(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return new Intl.DateTimeFormat('en-US', { timeZone: ET, ...opts }).format(d);
}

/** "Thu Oct 8, 8:15 PM ET" */
export function kickoffLabel(iso: string | null | undefined): string {
  if (!iso) return '—';
  const day = fmtET(iso, { weekday: 'short', month: 'short', day: 'numeric' });
  const time = fmtET(iso, { hour: 'numeric', minute: '2-digit' });
  return `${day}, ${time} ET`;
}

/** "Thu Oct 8" */
export function dayLabel(iso: string | null | undefined): string {
  return fmtET(iso, { weekday: 'short', month: 'short', day: 'numeric' });
}

/** "Mon 9:12 PM ET" */
export function clockLabel(ms: number): string {
  return `${fmtET(new Date(ms).toISOString(), { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET`;
}

/** Time until `iso`: "58 h 15 m", "42 m", or passed. */
export function countdown(nowMs: number, iso: string | null | undefined): { text: string; passed: boolean } {
  if (!iso) return { text: '—', passed: false };
  const diff = new Date(iso).getTime() - nowMs;
  if (Number.isNaN(diff)) return { text: '—', passed: false };
  if (diff <= 0) return { text: 'Kicked off', passed: true };
  const mins = Math.floor(diff / 60_000);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return { text: h > 0 ? `${h} h ${m} m` : `${m} m`, passed: false };
}

const DAY: Record<string, string> = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' };

/** ["thu", "sun", "mon"] → "Thu–Mon" */
export function daysRange(days: string[]): string {
  if (days.length === 0) return '';
  const first = DAY[days[0]] ?? days[0];
  const last = DAY[days[days.length - 1]] ?? days[days.length - 1];
  return first === last ? first : `${first}–${last}`;
}

/** The calendar's slate tags (ops/calendar.py → slate()) as chip text. */
export const SPECIAL_LABEL: Record<string, string> = {
  thursday: 'Thursday game',
  thanksgiving: 'Thanksgiving',
  black_friday: 'Black Friday',
  christmas: 'Christmas',
  midweek: 'Midweek game',
  friday: 'Friday game',
  saturday: 'Saturday games',
  monday_doubleheader: 'Monday doubleheader',
  neutral_site: 'Neutral site',
  international: 'International',
  morning_kickoff: 'Morning kickoff',
  early_season: 'Early season',
  final_week: 'Final regular week',
  playoffs: 'Playoffs',
};

export function specialChips(special: string[]): string[] {
  return special.filter((s) => s !== 'byes').map((s) => SPECIAL_LABEL[s] ?? s.replace(/_/g, ' '));
}

const ROUND: Record<string, string> = {
  WC: 'Wild Card round',
  DIV: 'Divisional round',
  CON: 'Conference championships',
  SB: 'Super Bowl',
};

export function weekTitle(week: number, gameType?: string | null): string {
  return gameType && ROUND[gameType] ? ROUND[gameType] : `Week ${week}`;
}

export function seasonEyebrow(season: number, phase?: string | null): string {
  const p = phase === 'playoffs' ? 'playoffs' : phase === 'preseason' ? 'pre-season' : 'regular season';
  return `${season} ${p}`;
}
