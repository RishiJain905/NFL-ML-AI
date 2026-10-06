// The Pipeline card's view picker (mockup: #vizSeg): Drive chart · Pipeline map · Timeline, and
// "Surprise me", which picks a different view at random and shows a "surprise pick" chip.
// The remembered per-week choice lives in `useViewChoice` (views.ts).

import { VIEWS, type ViewKind } from './views';

// the hook lives in views.ts (no components there); it is re-exported here for callers of the picker
// eslint-disable-next-line react-refresh/only-export-components
export { useViewChoice } from './views';

export function ViewPicker({
  season,
  week,
  value,
  surprised,
  onChoose,
}: {
  season: number;
  week: number;
  value: ViewKind;
  surprised: boolean;
  onChoose: (v: ViewKind | 'surprise') => void;
}) {
  return (
    <>
      <span className="seg" role="group" aria-label={`Pipeline view for ${season} week ${week}`}>
        {VIEWS.map((v) => (
          <button key={v.kind} type="button" aria-pressed={value === v.kind} onClick={() => onChoose(v.kind)}>
            {v.label}
          </button>
        ))}
        <button type="button" title="Pick a different view at random for this week" onClick={() => onChoose('surprise')}>
          Surprise me
        </button>
      </span>
      {surprised ? <span className="chip run">surprise pick</span> : null}
    </>
  );
}
