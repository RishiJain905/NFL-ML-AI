// Pieces shared by the three MLOps sections.

import type { ReactNode } from 'react';
import { Chip, type ChipTone } from '../../../components/ui';
import { safeHref, statusWord } from './util';
import '../week.css';
import '../mlops.css';

/** An external link that opens in a new tab. A URL that isn't http(s) is shown as plain text. */
export function ExtLink({
  href,
  className,
  title,
  label,
  children,
}: {
  href: string | null | undefined;
  className?: string;
  title?: string;
  label?: string;
  children: ReactNode;
}) {
  const safe = safeHref(href);
  if (!safe) return <span className={className}>{children}</span>;
  return (
    <a className={className} href={safe} target="_blank" rel="noopener noreferrer" title={title} aria-label={label}>
      {children}
    </a>
  );
}

const STATUS: Record<string, { tone: ChipTone; icon?: string }> = {
  ok: { tone: 'ok', icon: '✓' },
  alert: { tone: 'err', icon: '!' },
  insufficient_data: { tone: 'ghost' },
  mixed: { tone: 'ghost' },
};

/** A drift status as a chip: the word always travels with the colour. */
export function StatusChip({ status }: { status: string }) {
  const s = STATUS[status] ?? { tone: 'warn' as ChipTone, icon: '!' };
  return (
    <Chip tone={s.tone} icon={s.icon}>
      {statusWord(status)}
    </Chip>
  );
}

/** The mockup's compact empty state inside a card body. */
export function EmptyBlock({ glyph, children }: { glyph: string; children: ReactNode }) {
  return (
    <div className="empty ml-empty">
      <div className="glyph" aria-hidden="true">
        {glyph}
      </div>
      <p>{children}</p>
    </div>
  );
}
