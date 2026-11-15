// The Outcomes tab (FL02, D158; documentation/forecast-lab/README.md §3; mockup:
// documentation/forecast-lab/mockup/, published at https://claude.ai/artifact/GtsLqoMb8Y5mQVaiXy7HDz):
// the whole curve behind each player projection, so any number or band has a chance. The strip,
// the tiles and one note on how to read it; the stat picker; the curve panel (the two boxes, the
// chart, the facts) beside the stat's list; where the curve and the simple curve disagree most;
// after the games, how the curves did. The stat and the player live in the URL (`?stat=`,
// `?player=`), so Back / Forward and a shared link land on the same curve. Read from
// GET /api/weeks/{S}/{W}/outcomes?target= (one stat's rows) and .../outcomes/curve.

import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useOutcomes } from '../../api/client';
import type { OutcomesResponse } from '../../api/types';
import { ForecastLabUpdateCard } from '../../components/run/ForecastLabUpdate';
import { Notice, Tile } from '../../components/ui';
import { comma } from '../../lib/format';
import { byProjection, madeAt } from '../../lib/forecastlab';
import { LabEmpty, MadeStrip } from './forecastlab/common';
import { CurvePanel, type Asks, type BandMode } from './forecastlab/CurvePanel';
import { CurveList, GapsCard, ScoreCard, StatPicker } from './forecastlab/OutcomesParts';
import './forecastlab/outcomes.css';
import { TabError, TabLoading } from './WeekEmpty';

const KEY_RE = /^[a-z_]{1,20}-[a-z]{1,8}$/;

function Body({ r: answer, season, week }: { r: OutcomesResponse; season: number; week: number }) {
  const r = { ...answer, rows: byProjection(answer.rows) };
  const [params, setParams] = useSearchParams();
  const [asks, setAsks] = useState<Record<string, Asks>>({});
  const [mode, setMode] = useState<BandMode>('within');
  const m = r.meta;
  const t = r.targets.find((x) => x.stat === r.stat) ?? null;
  const wanted = params.get('player');
  const row = r.rows.find((x) => x.player_id === wanted) ?? r.rows[0] ?? null;

  const go = (target: string | null, player: string | null) => {
    const next = new URLSearchParams(params);
    if (target) next.set('stat', target);
    if (player) next.set('player', player);
    else next.delete('player');
    setParams(next, { replace: false });
  };
  const show = (target: string, player: string) => {
    go(target, player);
    document.querySelector('.oc-panel')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div className="oc">
      {m ? (
        <MadeStrip
          label="When this was made"
          items={[
            <>
              Made <b>{madeAt(m.made_at)}</b>
            </>,
            m.version ? (
              <>
                curves model <span className="mono">{m.version}</span>
              </>
            ) : null,
            m.source_version ? (
              <>
                built on player model <span className="mono">{m.source_version}</span>
              </>
            ) : null,
            m.games_after_kickoff.length ? (
              <>
                {m.games_after_kickoff.length} game{m.games_after_kickoff.length === 1 ? '' : 's'} made after kickoff
              </>
            ) : null,
          ]}
        />
      ) : null}
      <div className="tiles">
        <Tile
          k="Curves"
          v={comma(r.tiles?.curves ?? r.rows.length)}
          d={r.tiles ? `${comma(r.tiles.players)} players · ${r.tiles.stats} stats` : undefined}
        />
        {t ? <Tile k="This stat" v={comma(t.n)} d={`${t.group} ${t.label}`} /> : null}
        <Tile k="Graded" v={r.graded ? 'Yes' : 'Not yet'} d={r.graded ? 'the actuals are in' : 'after the games'} />
      </div>
      <Notice tone="accent">
        <b>Each curve is the whole spread behind a projection, if he plays.</b> Type any number or band below and read its chance. Ranges are
        wide because football is: a receiver lands within 7 yards of his projection about 1 game in 9. That&apos;s the sport, not the model.
        Next to each chance is <b>the simple curve</b>: what you&apos;d work out by hand from the projection (a bell curve through its 10th and
        90th percentiles; for counts, a plain Poisson).
      </Notice>
      <StatPicker targets={r.targets} value={r.stat} onChange={(k) => go(k, null)} />
      {t ? (
        <div className="oc-main">
          <CurvePanel
            season={season}
            week={week}
            t={t}
            row={row}
            asks={asks[t.stat] ?? {}}
            onAsk={(a) => setAsks({ ...asks, [t.stat]: a })}
            mode={mode}
            onMode={setMode}
          />
          <CurveList
            r={r}
            t={t}
            selected={row?.player_id ?? null}
            onPick={(p) => {
              go(t.stat, p);
              if (window.matchMedia?.('(max-width: 1179px)').matches) document.querySelector('.oc-panel')?.scrollIntoView?.({ behavior: 'smooth' });
            }}
          />
        </div>
      ) : null}
      <GapsCard r={r} onShow={show} />
      <ScoreCard r={r} />
    </div>
  );
}

function TabBody(props: { season: number; week: number; isCurrent: boolean; lastPublishedWeek: number | null }) {
  const [params] = useSearchParams();
  const stat = params.get('stat');
  const q = useOutcomes(props.season, props.week, stat && KEY_RE.test(stat) ? stat : null);
  if (q.isLoading) return <TabLoading />;
  if (q.error) return <TabError error={q.error} />;
  const r = q.data;
  if (!r) return null;
  if (r.status !== 'ok') return <LabEmpty tab="outcomes" status={r.status} hint={r.hint} {...props} />;
  return <Body r={r} season={props.season} week={props.week} />;
}

export function OutcomesTab({
  season,
  week,
  isCurrent,
  lastPublishedWeek,
}: {
  season: number;
  week: number;
  isCurrent?: boolean;
  lastPublishedWeek?: number | null;
}) {
  return (
    <div className="wk-stack">
      <TabBody season={season} week={week} isCurrent={Boolean(isCurrent)} lastPublishedWeek={lastPublishedWeek ?? null} />
      {/* D158: the fallback when the follow-up after the run didn't happen (current week only) */}
      {isCurrent ? <ForecastLabUpdateCard season={season} week={week} /> : null}
    </div>
  );
}
