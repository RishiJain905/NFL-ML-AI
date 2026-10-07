// Season → Scorecard (mockup: seasonView): the 2026 live season or the 2025 walk-forward backtest
// as an example. Tiles, season-to-date Brier (model / Elo / market), pick accuracy, calibration,
// the player model vs its baseline by group, and pipeline health (live only).

import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useScorecard } from '../../api/client';
import type { ScorecardResponse } from '../../api/types';
import { CalibrationChart } from '../../charts/CalibrationChart';
import { HBars } from '../../charts/HBars';
import { LineChart } from '../../charts/LineChart';
import { Card, CardHeader, Chip, EmptyState, Notice, Segmented, Tile } from '../../components/ui';
import { duration, fixed, pct, signed } from '../../lib/format';
import { Page } from '../Pages';
import { ChartEmpty, ExternalLink, PageError, PageLoading, RunStatusChip } from './common';

type Source = 'live' | 'backtest';
const STORE = 'cr.scorecardSource';

function readSource(): Source {
  try {
    return window.localStorage.getItem(STORE) === 'backtest' ? 'backtest' : 'live';
  } catch {
    return 'live';
  }
}
function saveSource(s: Source) {
  try {
    window.localStorage.setItem(STORE, s);
  } catch {
    // private window or blocked storage: the choice just isn't remembered
  }
}

const b4 = (v: number | null | undefined) => fixed(v, 4);

/** "model better by 0.0140" / "Elo better by …" (lower Brier is better). */
function vsText(model: number | null, other: number | null, name: string): string | undefined {
  if (model == null || other == null) return undefined;
  const d = other - model;
  if (Math.abs(d) < 0.00005) return `level with ${name === 'Elo' ? 'Elo' : 'the market'}`;
  return d > 0 ? `model better by ${d.toFixed(4)}` : `${name} better by ${(-d).toFixed(4)}`;
}

function Tiles({ r }: { r: ScorecardResponse }) {
  const t = r.tiles;
  const live = r.source === 'live';
  return (
    <div className="tiles">
      <Tile
        k="Season Brier · model"
        v={b4(t.brier_model)}
        d="market-informed, as shown · lower is better"
      />
      <Tile k="Elo" v={b4(t.brier_elo)} d={vsText(t.brier_model, t.brier_elo, 'Elo')} />
      <Tile k="Market" v={b4(t.brier_market)} d={vsText(t.brier_model, t.brier_market, 'market')} />
      <Tile k="Pick accuracy" v={pct(t.pick_accuracy, 1)} d={`${t.games_graded} games graded`} />
      <Tile
        k="Calibration error"
        v={fixed(t.ece, 3)}
        d={t.ece_chance != null ? `ECE · ${t.ece_chance.toFixed(3)} for a calibrated model` : 'ECE'}
      />
      {live ? (
        <>
          <Tile
            k="Weeks published"
            v={t.weeks_published ?? '—'}
            d={r.through_week != null ? `graded through week ${r.through_week}` : 'none graded yet'}
          />
          <Tile
            k="Checks pass rate"
            v={
              t.checks_passed != null && t.checks_total
                ? pct(t.checks_passed / t.checks_total)
                : '—'
            }
            d={
              t.checks_total
                ? `${t.checks_passed ?? 0} of ${t.checks_total} digests`
                : 'no digests yet'
            }
          />
          <Tile
            k="LLM spend"
            v={t.llm_spend != null ? `$${t.llm_spend.toFixed(3)}` : '—'}
            d="season to date"
          />
        </>
      ) : null}
      <Tile
        k="Player model"
        v={t.player_improvement != null ? `${signed(t.player_improvement)}%` : '—'}
        d="MAE vs the rolling baseline, players pooled"
      />
    </div>
  );
}

function BrierCard({ r }: { r: ScorecardResponse }) {
  const W = r.weeks;
  const range = !W.length
    ? undefined
    : W.length === 1
      ? `week ${W[0].week}`
      : `weeks ${W[0].week}–${W[W.length - 1].week}`;
  return (
    <Card>
      <CardHeader
        title="Season-to-date Brier"
        sub={`lower is better${range ? ` · ${range}` : ''}`}
      />
      <div className="card-b">
        {W.length >= 2 ? (
          <LineChart
            label={`Season-to-date Brier score by week, ${r.label}`}
            xs={W.map((w) => w.week)}
            xFormat={(x) => `Week ${x}`}
            format={(v) => v.toFixed(4)}
            series={[
              { name: 'Model', color: 'var(--s1)', values: W.map((w) => w.cum_brier_model) },
              { name: 'Elo', color: 'var(--s2)', values: W.map((w) => w.cum_brier_elo) },
              { name: 'Market', color: 'var(--s3)', values: W.map((w) => w.cum_brier_market) },
            ]}
          />
        ) : (
          <ChartEmpty glyph={W.length ? 'W' + W[0].week : '—'}>
            {W.length
              ? `Starts once two weeks are graded. Week ${W[0].week}: model ${b4(W[0].brier_model)} · Elo ${b4(W[0].brier_elo)} · market ${b4(W[0].brier_market)}.`
              : "Starts once two weeks are graded: each Tuesday's run grades the week before against the model, Elo and the market."}
          </ChartEmpty>
        )}
      </div>
    </Card>
  );
}

function AccuracyCard({ r }: { r: ScorecardResponse }) {
  const W = r.weeks;
  return (
    <Card>
      <CardHeader title="Pick accuracy" sub="season to date" />
      <div className="card-b">
        {W.length >= 2 ? (
          <LineChart
            label={`Season-to-date pick accuracy, ${r.label}`}
            xs={W.map((w) => w.week)}
            xFormat={(x) => `Week ${x}`}
            format={(v) => `${(v * 100).toFixed(1)}%`}
            yFormat={(v) => `${Math.round(v * 100)}%`}
            area
            series={[
              { name: 'Accuracy', color: 'var(--s1)', values: W.map((w) => w.cum_pick_accuracy) },
            ]}
          />
        ) : (
          <ChartEmpty glyph={W.length ? pct(W[0].cum_pick_accuracy) : '—'}>
            {W.length
              ? `Starts once two weeks are graded. Week ${W[0].week}: ${pct(W[0].pick_accuracy, 1)} of ${W[0].games ?? '—'} picks right.`
              : 'Starts once two weeks are graded.'}
          </ChartEmpty>
        )}
      </div>
    </Card>
  );
}

function CalibrationCard({ r }: { r: ScorecardResponse }) {
  const n = r.calibration.reduce((a, c) => a + c.games, 0);
  return (
    <Card>
      <CardHeader title="Calibration" sub="predicted home win chance vs how often it happened" />
      <div className="card-b">
        {r.calibration.length ? (
          <>
            <CalibrationChart label={`Calibration curve, ${r.label}`} bins={r.calibration} />
            {r.source === 'live' && n < 64 ? (
              <p className="muted" style={{ margin: '8px 0 0', fontSize: 12.5 }}>
                {n} graded games so far: the bins are noisy until about 64 (when the calibration
                signal can first fire).
              </p>
            ) : null}
          </>
        ) : (
          <ChartEmpty>The curve appears once games are graded.</ChartEmpty>
        )}
      </div>
    </Card>
  );
}

function PlayersCard({ r }: { r: ScorecardResponse }) {
  const groups = r.player_groups
    .filter((g): g is typeof g & { improvement: number } => g.improvement != null)
    .sort((a, b) => b.improvement - a.improvement);
  const top = Math.max(0, ...groups.map((g) => Math.abs(g.improvement)));
  const weeks = Math.max(0, ...groups.map((g) => g.weeks));
  return (
    <Card>
      <CardHeader
        title="Player model vs baseline"
        sub={`MAE improvement by group${weeks ? ` · ${weeks} week${weeks === 1 ? '' : 's'}` : ''}`}
      />
      <div className="card-b">
        {groups.length ? (
          <HBars
            label="Player model vs baseline by group"
            max={top * 1.1 || 1}
            rows={groups.map((g) => ({
              label: g.group,
              value: g.improvement,
              tip: `${g.group}\n${signed(g.improvement)}% vs the rolling baseline\n${g.weeks} week${g.weeks === 1 ? '' : 's'} (${g.live_weeks} live)`,
            }))}
            format={(v) => `${signed(v)}%`}
          />
        ) : (
          <ChartEmpty>
            The player scoreboard grades projections once a week's games are final.
          </ChartEmpty>
        )}
      </div>
    </Card>
  );
}

function PipelineCard({ r }: { r: ScorecardResponse }) {
  return (
    <Card>
      <CardHeader title="Pipeline health" sub="one row per weekly run" />
      {r.pipeline.length ? (
        <div className="tablewrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Week</th>
                <th>Status</th>
                <th>On time</th>
                <th>Checks</th>
                <th className="r">Run time</th>
                <th className="r">Drift alerts</th>
                <th>Launched by</th>
              </tr>
            </thead>
            <tbody>
              {r.pipeline.map((p) => (
                <tr key={p.week}>
                  <td>
                    <b>{p.week}</b>
                  </td>
                  <td>
                    <RunStatusChip status={p.status} />
                  </td>
                  <td>
                    {p.on_time === true ? (
                      <Chip tone="ok" icon="✓">
                        on time
                        {p.hours_before_deadline != null
                          ? ` · ${p.hours_before_deadline.toFixed(1)} h before`
                          : ''}
                      </Chip>
                    ) : p.on_time === false ? (
                      <Chip tone="warn" icon="!">
                        late
                      </Chip>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    {p.checks_passed == null
                      ? '—'
                      : p.checks_passed
                        ? p.regenerated
                          ? 'passed after a rewrite'
                          : 'passed'
                        : 'failed'}
                  </td>
                  <td className="r num">{duration(p.seconds)}</td>
                  <td className="r num">{p.drift_alerts ?? '—'}</td>
                  <td>{p.launched_by ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card-b muted">No weekly runs recorded yet this season.</div>
      )}
      {r.notes.filter(isPipelineNote).map((n, i) => (
        <p key={i} className="muted pipeline-note">
          {n}
        </p>
      ))}
    </Card>
  );
}

/** "N published week(s) ran before run records (P07) and have no pipeline row." goes under the
 *  pipeline table; every other note sits above the charts. */
const isPipelineNote = (n: string) => /pipeline row/i.test(n);

function ScorecardBody({ r }: { r: ScorecardResponse }) {
  const empty =
    r.weeks.length === 0 &&
    r.calibration.length === 0 &&
    r.player_groups.length === 0 &&
    r.pipeline.length === 0;
  if (empty) {
    return (
      <EmptyState glyph="0" title={`Nothing graded in ${r.label} yet`}>
        The scorecard fills in as the Tuesday runs grade each week: Brier for the model, Elo and the
        market, pick accuracy, calibration, and the player model against its baseline.
      </EmptyState>
    );
  }
  return (
    <>
      <Tiles r={r} />
      {r.notes
        .filter((n) => r.source !== 'live' || !isPipelineNote(n))
        .map((n, i) => (
          <Notice key={i}>{n}</Notice>
        ))}
      <div className="grid g2">
        <BrierCard r={r} />
        <AccuracyCard r={r} />
        <CalibrationCard r={r} />
        <PlayersCard r={r} />
      </div>
      {r.source === 'live' ? <PipelineCard r={r} /> : null}
    </>
  );
}

export function ScorecardPage() {
  const params = useParams();
  const season = Number(params.season);
  const [source, setSource] = useState<Source>(readSource);
  const querySeason = source === 'backtest' ? season - 1 : season;
  const q = useScorecard(querySeason, source);
  const valid = Number.isInteger(season);
  const r = q.data;

  const pick = (
    <div className="sec-h">
      <Segmented
        label="Which season"
        value={source}
        onChange={(s) => {
          setSource(s);
          saveSource(s);
        }}
        options={[
          { value: 'live', label: `${valid ? season : ''} live`.trim() },
          { value: 'backtest', label: `${valid ? season - 1 : ''} backtest (example)`.trim() },
        ]}
      />
      <p>
        {source === 'live'
          ? r?.through_week != null
            ? `Graded through week ${r.through_week}; each Tuesday's run grades the week before.`
            : "Each Tuesday's run grades the week before."
          : 'The walk-forward backtest, drawn the way the app will draw the live season by January.'}
      </p>
    </div>
  );

  return (
    <Page
      eyebrow="Season"
      title="Scorecard"
      sub="How the models are doing, week by week"
      right={
        r?.dashboard.url ? (
          <ExternalLink href={r.dashboard.url}>
            {r.dashboard.title ?? 'W&B Season Dashboard'}
          </ExternalLink>
        ) : null
      }
    >
      {pick}
      {!valid ? (
        <EmptyState glyph="?" title="No such season" />
      ) : q.isPending ? (
        <PageLoading />
      ) : q.isError ? (
        <PageError error={q.error} what="the scorecard" />
      ) : (
        <ScorecardBody r={q.data} />
      )}
      {r ? (
        <div className="foot">
          {r.source === 'backtest' ? (
            <>
              Weekly numbers come from <span className="mono">runs/backtests/game/market</span> and
              the player scoreboard.
            </>
          ) : (
            <>
              Weekly numbers come from the season scorecard, the accuracy scoreboard and the
              pipeline history; the W&amp;B Season Dashboard is built from the same files.
            </>
          )}
        </div>
      ) : null}
    </Page>
  );
}
