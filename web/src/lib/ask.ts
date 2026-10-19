// Ask the Engine (AE01): the page's formatting and wording, pure functions (mockup:
// documentation/ask-the-engine/mockup/ask.js, "formatting"). The server sends fixed texts; these
// only say what a number or a field means.

import type { AskCell, AskExamplesResponse, AskResponse, AskStatus } from '../api/types';

/** Seconds: "under 0.1 s", two decimals under 1 s, one under 10 s, whole seconds after. */
export function secs(s: number | null | undefined): string {
  if (s == null) return '—';
  if (s < 0.05) return 'under 0.1 s';
  if (s < 1) return `${s.toFixed(2)} s`;
  if (s < 10) return `${s.toFixed(1)} s`;
  return `${Math.round(s)} s`;
}

/** A question's cost: $ to 4 decimals; 0 = no model call; null = a call didn't report one. */
export function money(c: number | null | undefined): string {
  if (c == null) return 'cost unknown';
  if (c === 0) return 'no model call';
  if (c < 0.0001) return 'under $0.0001';
  return `$${c.toFixed(4)}`;
}

const CLOCK = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' });
export function clockTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : CLOCK.format(d);
}

export const colName = (c: string) => c.replace(/_/g, ' ');

export function cellText(v: AskCell | undefined): string {
  if (v == null) return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(3)));
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (Array.isArray(v)) return v.map(cellText).join(', ');
  return v;
}

export type ColKind = 'num' | 'list' | 'text';
/** A column is numeric when every non-empty cell is a number, a list column when every one is a list. */
export function colKinds(columns: string[], rows: AskCell[][]): ColKind[] {
  return columns.map((_, i) => {
    let num = 0;
    let list = 0;
    let other = 0;
    for (const r of rows) {
      const v = r[i];
      if (v == null) continue;
      if (typeof v === 'number') num++;
      else if (Array.isArray(v)) list++;
      else other++;
    }
    if (list && !num && !other) return 'list';
    if (num && !list && !other) return 'num';
    return 'text';
  });
}

/** Sort key: a list sorts by its first item. */
export function sortKey(v: AskCell | undefined): string | number | boolean | undefined {
  if (Array.isArray(v)) return v.length ? sortKey(v[0]) : undefined;
  return v ?? undefined;
}
export function compareCells(a: AskCell | undefined, b: AskCell | undefined): number {
  const x = sortKey(a);
  const y = sortKey(b);
  if (x === undefined && y === undefined) return 0;
  if (x === undefined) return 1;
  if (y === undefined) return -1;
  if (typeof x === 'number' && typeof y === 'number') return x - y;
  return String(x).localeCompare(String(y), undefined, { numeric: true });
}

export const STAGE: Record<string, string> = {
  route: 'Routing (the first model call)',
  generate: 'Writing the query',
  fix: 'Fixing the query',
  answer: 'Writing the answer',
};
const PURPOSE: Record<string, string> = {
  route: 'Route (pick a template or write a query)',
  generate: 'Write the query',
  fix: 'Fix the query',
  answer: 'Write the answer',
};

export function timeLines(a: AskResponse): string[] {
  const t = a.timing;
  const lines = [`Took ${secs(t.total_s)} in all`];
  const parts: [number | null, string][] = [
    [t.route_s, 'Route'],
    [t.generate_s, 'Write the query'],
    [t.run_s, 'Run the query'],
    [t.answer_s, 'Write the answer'],
  ];
  for (const [s, label] of parts) if (s != null) lines.push(`${label}: ${secs(s)}`);
  return lines;
}

export function costLines(a: AskResponse): string[] {
  const calls = a.llm?.calls ?? [];
  if (!calls.length) return ['No model call', 'Answered without the model (cost $0)'];
  const lines = [`${calls.length} model call${calls.length === 1 ? '' : 's'} · ${a.llm.model ?? 'model'}`];
  for (const c of calls) {
    const cost = c.cost == null ? 'cost not reported' : `$${c.cost.toFixed(5)}`;
    lines.push(`${PURPOSE[c.purpose] ?? c.purpose}: ${c.ok ? '' : 'failed · '}${c.provider ? `${c.provider} · ` : ''}${secs(c.seconds)} · ${cost}`);
  }
  if (a.cost == null) lines.push('A call didn’t report its cost, so the total is unknown');
  return lines;
}

export function routeLines(a: AskResponse, limits: AskExamplesResponse['limits'] | undefined): string[] {
  if (a.route === 'template') {
    return [
      a.template?.title ?? 'A template',
      'A tested query from the template library: the model picked it and filled its values (the bound values under the query); the query itself is fixed.',
    ];
  }
  if (a.route === 'generated') {
    return [
      'Free-form',
      `No template fits, so the model wrote this query. The guard checked it before it ran: read only, one statement, a ${limits?.query_timeout_s ?? 10} s limit, at most ${limits?.rows ?? 500} rows.`,
      `Retries: ${a.retries} (correction rounds after a failed check or run; up to ${limits?.corrections ?? 2})`,
    ];
  }
  return [a.route_label ?? a.route];
}

/** Who refused a question, in plain words. */
export function refusedBy(a: AskResponse): string {
  const r = a.refusal;
  if (!r) return 'Refused';
  if (r.by === 'code') return 'Refused before any model call';
  if (r.by === 'source') {
    if (r.rule === 'source.busy') return 'Stopped: the weekly refresh started, so the data isn’t available';
    if (r.rule === 'source.down') return 'Stopped: the data source isn’t available (the graph is down)';
    if (r.rule === 'source.history_down') return 'Stopped: the history graph isn’t running';
    if (r.rule === 'source.history_busy') return 'Stopped: the history graph is being rebuilt';
    return 'Stopped: the data source isn’t available';
  }
  if (r.by === 'router') return 'Refused by the router (the model’s first step)';
  if (r.by === 'generator') return 'Refused by the model while writing the query';
  if (r.by === 'guard') return `Stopped by the guard: ${r.rule}`;
  return `Refused (${r.by || 'unknown'})`;
}

const GLYPH: Record<string, string> = {
  write: 'Read only',
  not_nfl: 'NFL only',
  'source.down': 'Graph down',
  'source.busy': 'Refreshing',
  'source.history_down': 'History down',
  'source.history_busy': 'Rebuilding',
  'source.history_none': 'Not built',
  no_data: 'Not in data',
  out_of_range: 'Out of range',
};
export const refusalGlyph = (rule: string | undefined) => (rule && GLYPH[rule]) || 'Refused';

export const STATUS_WORD: Record<AskStatus, string> = {
  answered: 'answered',
  empty: 'no rows',
  refused: 'refused',
  clarify: 'needs a choice',
  error: 'error',
};

/** The session list's second line: the route for an answer, otherwise what happened. */
export function sessionMeta(a: AskResponse): string {
  const what = a.status === 'answered' ? (a.route_label ?? 'answered') : STATUS_WORD[a.status] ?? a.status;
  const t = clockTime(a.asked_at);
  return t ? `${t} · ${what}` : what;
}

export function errorTitle(code: string | undefined): string {
  if (code === 'out_of_credits') return 'Out of credits';
  if (code === 'query_failed') return 'The query kept failing';
  if (code === 'llm') return 'The model didn’t answer';
  return 'Something went wrong';
}

/** The headline for a request the server refused before asking (409 / 429 / 422 / 503). */
export function requestRefusedTitle(status: number, code: string): string {
  if (status === 409 || code === 'busy') return 'Still finishing the last question.';
  if (status === 429 || code === 'rate_limited') return 'Too many questions in a minute.';
  if (status === 422 || code === 'bad_request' || code === 'too_big') return 'That request wasn’t accepted.';
  if (status === 503) return 'Ask the Engine isn’t available.';
  if (status === 0) return 'Couldn’t reach the app.';
  return 'The question didn’t go through.';
}

export const GRAPH_STATUS: Record<string, { tone: 'ok' | 'err' | 'warn' | 'flat'; label: string }> = {
  up: { tone: 'ok', label: 'Neo4j up' },
  down: { tone: 'err', label: 'Neo4j down' },
  checking: { tone: 'flat', label: 'checking Neo4j' },
  config_problem: { tone: 'err', label: 'Neo4j config problem' },
  none: { tone: 'warn', label: 'no graph built yet' },
  off: { tone: 'flat', label: 'switched off' },
};

// AE02: the history graph (its own container, started only when wanted: down is normal)
export const HISTORY_STATUS: Record<string, { tone: 'ok' | 'err' | 'warn' | 'flat'; label: string }> = {
  up: { tone: 'ok', label: 'running' },
  down: { tone: 'flat', label: 'not running' },
  checking: { tone: 'flat', label: 'checking' },
  busy: { tone: 'warn', label: 'being rebuilt' },
  none: { tone: 'warn', label: 'not built yet' },
  off: { tone: 'flat', label: 'switched off' },
};

/** The history graph can be picked as a source: switched on and built (it may still be down:
 * then a question says how to start it). */
export function historyPickable(ex: AskExamplesResponse | undefined): boolean {
  const h = ex?.sources.history;
  return !!h && h.on && h.status !== 'none' && h.status !== 'off';
}
