// Renders the week's chosen pipeline view. No `key` and no state copied from the model: when the
// model changes (CR02 streams it step by step) the same elements update in place.

import { DriveChart } from './DriveChart';
import type { PipelineModel } from './model';
import { PipelineMap } from './PipelineMap';
import { Timeline } from './Timeline';
import type { ViewKind } from './views';

export type { ViewKind } from './views';

export function PipelineView({ model, view, sittings }: { model: PipelineModel; view: ViewKind; sittings: number }) {
  if (view === 'map') return <PipelineMap model={model} />;
  if (view === 'timeline') return <Timeline model={model} sittings={sittings} />;
  return <DriveChart model={model} />;
}
