// Shared bits for the season and system pages (CR03): loading / error blocks and the status chip
// for a run's status (never colour alone: every chip has an icon and a word).

import type { ReactNode } from 'react';
import { Card, Chip, Notice, type ChipTone } from '../../components/ui';
import './season.css';

export function PageLoading() {
  return (
    <Card>
      <div className="card-b muted" role="status">
        Loading…
      </div>
    </Card>
  );
}

export function PageError({ error, what }: { error: Error; what: string }) {
  return (
    <Notice tone="err" icon="✕">
      <b>Couldn't load {what}.</b> {error.message}
    </Notice>
  );
}

const RUN_STATUS: Record<string, { tone: ChipTone; icon: string; text: string }> = {
  ok: { tone: 'ok', icon: '✓', text: 'ok' },
  published: { tone: 'ok', icon: '✓', text: 'published' },
  already_done: { tone: 'flat', icon: '✓', text: 'already done' },
  idle: { tone: 'flat', icon: '·', text: 'nothing to do' },
  degraded: { tone: 'warn', icon: '!', text: 'degraded' },
  not_ready: { tone: 'warn', icon: '…', text: 'not ready' },
  interrupted: { tone: 'err', icon: '✕', text: 'interrupted' },
  failed: { tone: 'err', icon: '✕', text: 'failed' },
  locked: { tone: 'warn', icon: '!', text: 'locked' },
  running: { tone: 'run', icon: '●', text: 'running' },
};

export function RunStatusChip({ status }: { status: string | null | undefined }) {
  const s = status ? RUN_STATUS[status] : undefined;
  if (!s) return <Chip tone="ghost">{status ? status.replace(/_/g, ' ') : 'unknown'}</Chip>;
  return (
    <Chip tone={s.tone} icon={s.icon}>
      {s.text}
    </Chip>
  );
}

/** A small "card empty" block for charts without enough data. */
export function ChartEmpty({ glyph = '—', children }: { glyph?: string; children: ReactNode }) {
  return (
    <div className="empty season-chart-empty">
      <div className="glyph" aria-hidden="true">
        {glyph}
      </div>
      <p>{children}</p>
    </div>
  );
}

export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a className="btn sm" href={href} target="_blank" rel="noopener noreferrer">
      {children} ↗
    </a>
  );
}
