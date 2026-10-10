// Small pieces of the Game day tab (mockup: confChip, asof, tos, the label key).

import type { LiveFeed, LiveGame, LiveLabel } from '../../../api/types';
import { TipTarget } from '../../../charts/TipTarget';
import { Chip } from '../../../components/ui';
import { useNow } from '../../../lib/useNow';
import { LABEL_KEY_LINES, LABEL_PIPS, ago, lastPlayAge, tSec } from './model';

/** Confident / Lean / Toss-up as three pips and the word (never colour alone). */
export function ConfChip({ label, small }: { label: LiveLabel | null; small?: boolean }) {
  if (!label) return null;
  const n = LABEL_PIPS[label];
  return (
    <span className={`conf${small ? ' sm' : ''}${label === 'Toss-up' ? ' toss' : ''}`} role="img" aria-label={label}>
      <span className="pips" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <i key={i} className={i < n ? '' : 'off'} />
        ))}
      </span>
      <span className="lt">{label}</span>
    </span>
  );
}

/** All three labels, the current one in full: Lean is rare, so this is where it's explained. */
export function LabelKey({ current }: { current: LiveLabel | null }) {
  return (
    <TipTarget className="labkey" lines={LABEL_KEY_LINES} label={`How sure the bot is: ${LABEL_KEY_LINES.slice(1).join('; ')}`}>
      {(['Confident', 'Lean', 'Toss-up'] as LiveLabel[]).map((l) => (
        <span key={l} className={l === current ? '' : 'off'}>
          <ConfChip label={l} small />
        </span>
      ))}
    </TipTarget>
  );
}

const CLOCK = (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
    <circle cx="8" cy="8" r="6.2" />
    <path d="M8 4.6V8l2.4 1.6" />
  </svg>
);

/** "ESPN as of 3:57:45 PM · last play 35 s ago", ticking; a chip when it's an older answer. */
export function AsOf({ feed, game, receivedAt }: { feed: LiveFeed; game: LiveGame | null; receivedAt: number | null }) {
  const now = useNow(5_000);
  const lp = game?.last_play ?? null;
  const age = lastPlayAge(lp?.age_s, receivedAt, now);
  const how = lp?.age_from === 'snap' ? "Timed from the snap (ESPN's play log)" : 'Timed from when the app first saw it';
  return (
    <span className="asof">
      {CLOCK}
      <span>
        ESPN as of <b>{tSec(feed.as_of)}</b>
        {age != null ? (
          <>
            {' · '}
            <TipTarget lines={['Last play', how]} label={`last play ${ago(age)}`}>
              last play {ago(age)}
            </TipTarget>
          </>
        ) : null}
      </span>
      {feed.stale ? (
        <Chip tone="warn" icon="!">
          Last good answer{feed.age_s ? ` · ${Math.round(feed.age_s)} s old` : ''}
        </Chip>
      ) : null}
    </span>
  );
}

export function Timeouts({ n }: { n: number | null }) {
  if (n == null) return null;
  return (
    <span className="tos" title={`${n} timeouts left`} aria-label={`${n} timeouts left`} role="img">
      {[0, 1, 2].map((i) => (
        <i key={i} className={i < n ? '' : 'used'} />
      ))}
    </span>
  );
}

export const Ball = ({ large }: { large?: boolean }) => (
  <span className={`ball${large ? ' lg' : ''}`} title="Has the ball" aria-label="has the ball" role="img" />
);
