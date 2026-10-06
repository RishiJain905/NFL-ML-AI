// A run on the Pipeline tab (CR02): the now-bar, the chosen view animated from the run's events,
// and the streaming log. `replay` draws a finished run (a rehearsal's) from its recorded events.
//
// Everything the views draw is `projectRun(foldRun(events), clock)`: the events are folded once per
// batch and projected onto the clock every second, so the running step moves, the GLM wait counts
// up and nothing re-mounts (the views update in place). When the stream says the run is over this
// refetches what the run wrote and tells the page when its records have caught up (`onSettled`),
// so the page never flashes back to the plan in between.

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef } from 'react';
import { invalidateAfterRun, usePipeline } from '../../api/client';
import { eventsToLog, useRunStream } from '../../api/stream';
import type { PipelineResponse, RunInfo } from '../../api/types';
import { useNow } from '../../lib/useNow';
import { FailureBanner } from '../run/FailureBanner';
import { Card, CardHeader, Notice } from '../ui';
import { describeNow, foldRun, projectRun, runKindOf, runStreamKey, type LiveModel } from './live';
import { LogPanel } from './LogPanel';
import { NowBar } from './NowBar';
import { PipelineView } from './PipelineView';
import { ViewPicker, type useViewChoice } from './ViewPicker';
import { VIEWS } from './views';

/** What the week's pipeline says before the run's own records show: the run holds the lock, or nothing has been written. */
const NOT_YET = new Set<string>(['running', 'plan', 'none']);

/** After the run ends, wait at most this long for the week's records to catch up before showing them anyway... */
const HOLD_MAX_MS = 20_000;
/** ...asking again this often while the week still gives its old answer. */
const RETRY_MS = 2_000;

export type ViewChoice = ReturnType<typeof useViewChoice>;

function Banner({ model, replay, info, week }: { model: LiveModel; replay: boolean; info: RunInfo; week: number }) {
  const r = model.run;
  const rehearsal = r.kind === 'rehearsal';
  if (model.outcome === 'live') {
    const what = rehearsal ? 'A rehearsal' : r.kind === 'resume' ? 'A resume' : 'A run';
    return (
      <Notice tone="accent" icon="●">
        <b>
          {what} of week {week} is in progress.
        </b>{' '}
        {info.launched_from === 'terminal' ? 'It was started from a terminal. ' : ''}
        {rehearsal ? 'Nothing live is touched. ' : ''}
        The views below follow it live and the log streams from the run. Closing this tab doesn&apos;t stop a run.
      </Notice>
    );
  }
  if (model.outcome === 'published') {
    return rehearsal ? (
      <Notice icon="✓">
        <b>The rehearsal of week {week} finished.</b> Its files are in the rehearsal folder; nothing live was touched.
      </Notice>
    ) : (
      <Notice icon="✓">
        <b>Week {week} is published.</b> {replay ? '' : 'Updating the week’s records…'}
      </Notice>
    );
  }
  if (model.outcome === 'stopped') {
    return (
      <Notice icon="!">
        <b>{rehearsal ? 'The rehearsal stopped before the digest.' : 'The run stopped before the digest.'}</b>{' '}
        {rehearsal ? 'Nothing live was touched.' : 'Nothing was published for this week.'}
        {r.message ? ` ${r.message}` : ''}
      </Notice>
    );
  }
  return null; // failed: the failure banner
}

export function LiveRun({
  season,
  week,
  info,
  base,
  choice,
  replay = false,
  onSettled,
}: {
  season: number;
  week: number;
  /** `run/current`'s run */
  info: RunInfo;
  /** the week's pipeline response (expected seconds; the earlier steps of a resume) */
  base: PipelineResponse | null;
  choice: ViewChoice;
  /** draw a finished run from its recorded events instead of following it */
  replay?: boolean;
  /** live only: the stream ended and the week's records have caught up (or the wait ran out) */
  onSettled?: () => void;
}) {
  const stream = useRunStream(true, runStreamKey(info));
  const qc = useQueryClient();
  const pipeline = usePipeline(season, week);
  const fold = useMemo(() => foldRun(stream.events), [stream.events]);
  const over = fold.end !== null || stream.end !== null;
  const now = useNow(replay && over ? 60_000 : 1000);
  const model = projectRun(fold, { season, week, base, end: stream.end, kind: runKindOf(info), nowMs: now });
  const lines = useMemo(() => eventsToLog(stream.events), [stream.events]);

  // The server's `end` message: the run is over and the process is gone. Refetch what it wrote.
  const endedAt = useRef<number | null>(null);
  const streamEnded = stream.end !== null;
  useEffect(() => {
    if (replay || !streamEnded || endedAt.current !== null) return;
    endedAt.current = Date.now();
    invalidateAfterRun(qc);
  }, [replay, streamEnded, qc]);

  // ...and hand the page back to its own records once they show the result (or the wait ran out).
  const { dataUpdatedAt, errorUpdatedAt, isFetching, isError, refetch } = pipeline;
  const pipelineState = pipeline.data?.state;
  useEffect(() => {
    const ended = endedAt.current;
    if (replay || !onSettled || ended === null) return;
    // a week that still says running (the lock), plan or none has not caught up: its records would put the plan back
    const caughtUp =
      !isFetching &&
      ((dataUpdatedAt >= ended && !NOT_YET.has(pipelineState ?? 'none')) || (isError && errorUpdatedAt >= ended));
    if (caughtUp || Date.now() - ended > HOLD_MAX_MS) onSettled();
    else if (!isFetching && dataUpdatedAt >= ended && Date.now() - dataUpdatedAt > RETRY_MS) void refetch();
  }, [replay, onSettled, now, dataUpdatedAt, errorUpdatedAt, isFetching, isError, pipelineState, refetch]);

  const rehearsal = model.run.kind === 'rehearsal';
  const viewLabel = VIEWS.find((v) => v.kind === choice.view)?.label ?? '';
  const failedStep = model.steps.find((s) => s.status === 'failed');
  const sub = replay ? 'replayed from its events' : model.outcome === 'live' ? 'live' : 'just finished';

  return (
    <>
      <Banner model={model} replay={replay} info={info} week={week} />
      {model.outcome === 'failed' ? (
        <FailureBanner
          season={season}
          week={week}
          failedStep={model.run.failedStep}
          detail={failedStep?.detail || model.run.message || null}
          error={model.run.message}
        />
      ) : null}
      <NowBar now={describeNow(model)} />
      <Card>
        <CardHeader
          title={rehearsal ? 'The rehearsal' : 'The run'}
          sub={`${viewLabel} · ${sub}`}
          right={
            replay ? undefined : (
              <ViewPicker season={season} week={week} value={choice.view} surprised={choice.surprised} onChoose={choice.choose} />
            )
          }
        />
        <div className="card-b">
          <PipelineView model={model} view={choice.view} sittings={base?.sittings ?? 1} />
        </div>
      </Card>
      <LogPanel lines={lines} source="events" live={model.outcome === 'live'} title={replay && rehearsal ? 'Rehearsal log' : 'Log'} />
    </>
  );
}
