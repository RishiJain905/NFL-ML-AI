// Small helpers for the MLOps tab: formatting, link safety, and the drift grouping.

import type { DriftRow, VsRow, WandbJob } from '../../../api/types';
import { comma, duration } from '../../../lib/format';

/** 7,792 → "8 KB", 11,400,000 → "11.4 MB" (the mockup's bytes(), with a bytes case below 1 KB). */
export function bytes(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${Math.round(n / 1e3)} KB`;
  return `${n} B`;
}

/** Only http(s) links are rendered as links: a URL from a response is data, not markup. */
export function safeHref(url: string | null | undefined): string | undefined {
  return url && /^https?:\/\//i.test(url) ? url : undefined;
}

/** A value in the unit its row declares: seconds → "16m 29s", usd → "$0.011", count → "1,862". */
export function fmtMeasure(unit: VsRow['unit'], v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  switch (unit) {
    case 'seconds':
      return duration(v);
    case 'usd':
      return `$${v.toFixed(3)}`;
    case 'flag':
      return v ? 'yes' : 'no';
    default:
      return comma(v);
  }
}

/** This week minus last week, as "+2m 10s" / "−$0.004" / "same" / "changed" (flags). */
export function deltaText(unit: VsRow['unit'], now: number | null, before: number | null): string {
  if (now == null || before == null) return '—';
  if (unit === 'flag') return now === before ? 'same' : 'changed';
  if (fmtMeasure(unit, now) === fmtMeasure(unit, before)) return 'same';
  const d = now - before;
  return `${d > 0 ? '+' : '−'}${fmtMeasure(unit, Math.abs(d))}`;
}

export const JOB_LABEL: Record<WandbJob, string> = {
  game: 'game',
  graph: 'graph',
  scoreboard: 'scoreboard',
  player: 'player',
  team: 'team',
  digest: 'digest',
  pipeline: 'pipeline',
  dashboard: 'dashboard',
  injury_update: 'injury update',
  other: 'other',
};

export function statusWord(status: string): string {
  return status.replace(/_/g, ' ');
}

type DriftLike = Pick<DriftRow, 'name' | 'group' | 'status'>;

/** One drift signal and its rows (the player signals have one row per position group). */
export interface DriftGroup<T extends DriftLike = DriftRow> {
  name: string;
  rows: T[];
  /** alert if any row alerts; the shared status when all agree; else "mixed" */
  status: string;
}

function aggregate(rows: DriftLike[]): string {
  if (rows.some((r) => r.status === 'alert')) return 'alert';
  const first = rows[0].status;
  return rows.every((r) => r.status === first) ? first : 'mixed';
}

/** Rows grouped by signal name, in the run's order, signals with an alert first. */
export function groupDrift<T extends DriftLike>(rows: T[]): DriftGroup<T>[] {
  const by = new Map<string, T[]>();
  for (const r of rows) {
    const list = by.get(r.name);
    if (list) list.push(r);
    else by.set(r.name, [r]);
  }
  const groups = [...by].map(([name, rs]) => ({ name, rows: rs, status: aggregate(rs) }));
  return [...groups.filter((g) => g.status === 'alert'), ...groups.filter((g) => g.status !== 'alert')];
}

/** "0.231 vs threshold 0.250" when the run recorded them. */
export function valueText(r: Pick<DriftRow, 'value' | 'threshold'>): string | null {
  if (r.value == null) return null;
  const n = (x: number) => String(Number(x.toFixed(3)));
  return r.threshold == null ? `value ${n(r.value)}` : `value ${n(r.value)} vs threshold ${n(r.threshold)}`;
}

export function unique<T>(xs: T[]): T[] {
  return [...new Set(xs)];
}

/** W&B's project URL, or (when the response leaves it null) the part of any run / artifact URL
 *  before `/runs/` or `/artifacts/`. */
export function projectBase(projectUrl: string | null | undefined, urls: (string | null | undefined)[]): string | null {
  if (safeHref(projectUrl)) return projectUrl as string;
  for (const u of urls) {
    const m = u && safeHref(u) ? /^(https?:\/\/.+?)\/(?:runs|artifacts)\//.exec(u) : null;
    if (m) return m[1];
  }
  return null;
}
