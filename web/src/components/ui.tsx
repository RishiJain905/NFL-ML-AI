// Small building blocks ported from the mockup (styles in src/styles/app.css). State is never
// shown by colour alone: every chip carries an icon or a word.

import type { ReactNode } from 'react';
import type { WeekStatus } from '../api/types';

export type ChipTone = 'ok' | 'warn' | 'err' | 'run' | 'ghost' | 'flat';

export function Chip({
  tone = 'flat',
  icon,
  pulse,
  children,
  title,
}: {
  tone?: ChipTone;
  icon?: string;
  pulse?: boolean;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={`chip ${tone}`} title={title}>
      {pulse ? <span className="dot pulse" aria-hidden="true" /> : null}
      {icon ? (
        <span className="ic" aria-hidden="true">
          {icon}
        </span>
      ) : null}
      {children}
    </span>
  );
}

const WEEK_TONE: Record<WeekStatus, { tone: ChipTone; icon?: string; pulse?: boolean }> = {
  published: { tone: 'ok', icon: '✓' },
  running: { tone: 'run', pulse: true },
  failed: { tone: 'err', icon: '✕' },
  partial: { tone: 'warn', icon: '!' },
  ready: { tone: 'run', icon: '▶' },
  waiting: { tone: 'ghost', icon: '…' },
  not_run: { tone: 'ghost' },
};

const SHORT: Record<WeekStatus, string> = {
  published: 'Published',
  running: 'Running',
  failed: 'Failed',
  partial: 'Incomplete',
  ready: 'Ready',
  waiting: 'Waiting',
  not_run: 'No run',
};

/** `short` is for the sidebar (the full label goes in the title and the week header). */
export function WeekStatusChip({ status, label, short }: { status: WeekStatus; label: string; short?: boolean }) {
  const t = WEEK_TONE[status] ?? { tone: 'flat' as ChipTone };
  return (
    <Chip tone={t.tone} icon={t.icon} pulse={t.pulse} title={short ? label : undefined}>
      {short ? SHORT[status] ?? label : label}
    </Chip>
  );
}

export function Tile({ k, v, d, sample }: { k: string; v: ReactNode; d?: ReactNode; sample?: boolean }) {
  return (
    <div className={`tile${sample ? ' sample' : ''}`}>
      <span className="k">{k}</span>
      <span className="v">{v}</span>
      {d ? <span className="d">{d}</span> : null}
    </div>
  );
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`card ${className}`.trim()}>{children}</div>;
}

export function CardHeader({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="card-h">
      <h2>{title}</h2>
      {sub ? <span className="muted">{sub}</span> : null}
      {right ? <div className="right">{right}</div> : null}
    </div>
  );
}

export function Notice({
  icon = 'i',
  tone,
  children,
}: {
  icon?: string;
  tone?: 'accent' | 'err';
  children: ReactNode;
}) {
  const style =
    tone === 'err'
      ? {
          borderColor: 'color-mix(in srgb, var(--err) 50%, transparent)',
          background: 'color-mix(in srgb, var(--err) 7%, var(--panel))',
        }
      : undefined;
  return (
    <div className={`notice${tone === 'accent' ? ' accent' : ''}`} style={style} role="note">
      <span className="ic" aria-hidden="true">
        {icon}
      </span>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

export function EmptyState({
  glyph,
  title,
  children,
  actions,
}: {
  glyph: string;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <Card>
      <div className="empty">
        <div className="glyph" aria-hidden="true">
          {glyph}
        </div>
        <h3>{title}</h3>
        {children ? <p>{children}</p> : null}
        {actions}
      </div>
    </Card>
  );
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string; title?: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <span className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          title={o.title}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </span>
  );
}
