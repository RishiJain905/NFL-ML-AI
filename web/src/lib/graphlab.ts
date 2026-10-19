// Connections and the Graph lab (AE03): the pages' formatting and wording, pure functions (mockup:
// documentation/ask-the-engine/mockup/graphlab.js, "formatting"). The server sends the evidence
// sentences and the messages; these only say what a number or a field means.

import type { ConnectionPerson, PersonMatch } from '../api/types';

const NF = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
/** 8372 -> "8,372"; null -> "—". */
export const int = (n: number | null | undefined): string =>
  n == null ? '—' : NF.format(Math.round(n));
export const fixed = (n: number | null | undefined, d = 2): string =>
  n == null ? '—' : n.toFixed(d);
/** 0.7129 -> "71.3%" (d = 1). */
export const pct = (n: number | null | undefined, d = 0): string =>
  n == null ? '—' : `${(n * 100).toFixed(d)}%`;
export const plural = (n: number, word: string, many = `${word}s`): string =>
  `${int(n)} ${n === 1 ? word : many}`;
export const capFirst = (s: string): string => (s ? s[0].toUpperCase() + s.slice(1) : s);

const WHEN = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});
/** "Oct 19, 10:40 AM" (the viewer's time zone). */
export function when(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : WHEN.format(d);
}

/** Milliseconds: "<1 ms", "79 ms", "1.20 s", "31.9 s", "1.7 min". */
export function ms(v: number | null | undefined): string {
  if (v == null) return '—';
  if (v < 1) return '<1 ms';
  if (v < 1000) return `${Math.round(v)} ms`;
  if (v < 60_000) return `${(v / 1000).toFixed(v < 10_000 ? 2 : 1)} s`;
  return `${(v / 60_000).toFixed(1)} min`;
}
/** An axis tick at a power of ten: "0.1 ms", "10 ms", "1 s", "100 s". */
export const msTick = (v: number): string =>
  v >= 1000 ? `${+(v / 1000).toPrecision(3)} s` : `${+v.toPrecision(3)} ms`;

export const LINK_WORD: Record<string, string> = {
  TEAMMATE: 'teammates',
  COACHED: 'coached',
  WORKED_WITH: 'same staff',
};
export const LINK_HELP: Record<string, string> = {
  TEAMMATE: 'On the field together on the same side (snaps together)',
  COACHED: 'A head coach or coordinator and a player in his unit (the player’s snaps)',
  WORKED_WITH: 'Two coaches on the same staff (the team’s snaps while both were there)',
};
export const LINK_TYPES = ['TEAMMATE', 'COACHED', 'WORKED_WITH'] as const;
export const linkWord = (t: string): string => LINK_WORD[t] ?? t.toLowerCase();

/** "2018-2018" -> "2018" (the people index writes a one-season span as a range). */
export const seasonText = (s: string | null | undefined): string | null =>
  s ? s.replace(/^(\d{4})-\1$/, '$1') : null;

/** "QB · KC" / "coach · NE" (+ the seasons when asked). */
export function personSub(
  p: Pick<PersonMatch | ConnectionPerson, 'kind' | 'position' | 'team' | 'seasons'>,
  seasons = false,
): string {
  const what = p.kind === 'coach' ? 'coach' : (p.position ?? 'player');
  return [what, p.team, seasons ? seasonText(p.seasons) : null].filter(Boolean).join(' · ');
}
/** A chain's person as the search would name them (the URL keeps only ids). */
export function asMatch(p: ConnectionPerson): PersonMatch {
  return {
    id: `${p.kind}:${p.id}`,
    kind: p.kind,
    name: p.name,
    position: p.position,
    team: p.team,
    seasons: p.seasons,
    links: p.links ?? null,
  };
}

export const MODE_WHY = {
  fewest:
    'the chain with the fewest people in it (a breadth-first search). Among equally short chains, the ones through the most shared snaps come first.',
  strongest:
    'every link costs 1 ÷ its snaps together, and the chain with the lowest total wins however long it is, so it often runs through coaching staffs.',
} as const;

// ---- the map: three colours (by unit), a highlight per position group ----
export type Unit = 'off' | 'def' | 'spec';
export const UNIT_OF: Record<string, Unit> = {
  QB: 'off',
  RB: 'off',
  WR: 'off',
  TE: 'off',
  OL: 'off',
  DL: 'def',
  LB: 'def',
  DB: 'def',
  SPEC: 'spec',
};
export const UNITS: { unit: Unit; label: string; groups: string[] }[] = [
  { unit: 'off', label: 'Offense', groups: ['QB', 'RB', 'WR', 'TE', 'OL'] },
  { unit: 'def', label: 'Defense', groups: ['DL', 'LB', 'DB'] },
  { unit: 'spec', label: 'Special teams', groups: ['SPEC'] },
];
export const GROUP_WORD: Record<string, string> = {
  QB: 'QB',
  RB: 'RB',
  WR: 'WR',
  TE: 'TE',
  OL: 'O-line',
  DL: 'D-line',
  LB: 'LB',
  DB: 'DB',
  SPEC: 'Specialists',
};
export const UNIT_VAR: Record<Unit, string> = { off: '--s2', def: '--s1', spec: '--s3' };

// ---- the benchmark ----
export const BENCH_FAMILY: Record<string, { label: string; help: string; colour: string }> = {
  pair: {
    label: 'One pair',
    help: 'One start, one finish: the question Connections asks',
    colour: '--s2',
  },
  source: {
    label: 'One source',
    help: 'One start, every finish (single-source shortest paths)',
    colour: '--s1',
  },
  all: {
    label: 'All pairs',
    help: 'Every start, every finish: the APSP problem the 2026 paper is about',
    colour: '--s3',
  },
};
export const BENCH_DASH = ['', '7 4', '2 4', '10 3 2 3', '1 3', '12 3 3 3 3 3'];
/** The benchmark's algorithm slugs in words (an unknown slug is shown as it is). */
export const ALG_NAME: Record<string, string> = {
  'cypher-shortest': 'Cypher SHORTEST (two-way BFS)',
  'gds-dijkstra-pair': 'GDS Dijkstra (one pair)',
  'scipy-dijkstra': 'scipy Dijkstra',
  'dijkstra-heap': 'Python Dijkstra (heap)',
  bmssp: 'Duan et al. 2025 BMSSP (our Python)',
  'bmssp-constant-degree': 'BMSSP on the constant-degree graph (our Python)',
  'gds-delta-stream': 'GDS Delta-Stepping (stream)',
  'gds-delta-stats': 'GDS Delta-Stepping (stats only)',
  'scipy-dijkstra-all': 'scipy Dijkstra from every source',
  'floyd-warshall-numba': 'Floyd–Warshall (numba)',
  'scipy-floyd-warshall': 'scipy Floyd–Warshall',
  'gds-all-pairs': 'GDS all pairs',
};
export const algName = (slug: string): string => ALG_NAME[slug] ?? slug;

/** A season span: 2023 / "2018–2021". */
export const span = (a: number | null | undefined, b: number | null | undefined): string =>
  a == null ? '—' : a === b || b == null ? String(a) : `${a}–${b}`;

/** The command in a server message ("... run `uv run nfl history-graph lab`.") and the text without backticks. */
export function splitCommand(message: string): { text: string; command: string | null } {
  const m = /`([^`]+)`/.exec(message);
  return { text: message.replace(/`([^`]+)`/g, '$1'), command: m ? m[1] : null };
}
