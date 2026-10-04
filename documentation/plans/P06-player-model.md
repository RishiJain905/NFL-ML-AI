# P06: Player Model v1 and the Accuracy Scoreboard

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P05
- **Unlocks:** P07, T04
- **Read first:** [04 → C. Player model](../04-track1-models.md#c-player-model), [11 Prediction targets](../11-prediction-targets.md) (all of it), [04 → Leakage rules](../04-track1-models.md#leakage-rules-apply-to-all-models), [08](../08-experiment-tracking.md)

## Goal

Projections with uncertainty ranges for the **main stat of every position group, offense and defense** (see [11 → Priority order](../11-prediction-targets.md#priority-order), P06 items), a real "Players to watch" list, and the **accuracy scoreboard** tracking how good the projections are, week by week, in W&B and in the digest.

## Scope

- **In (targets):**
  - QB passing yards + EPA per dropback
  - RB rushing yards + carries + receptions + scrimmage yards
  - WR/TE receiving yards + targets + receptions
  - EDGE/DL pressures
  - LB/S tackles
- **In (everything else):** baselines, features, quantile and count models, SHAP drivers, watch-list selection, projections written to the graph, the scoreboard, the report-card extension.
- **Out:** TD/sack/INT probabilities, coverage stats, team stat totals, the consistency layer → P08.

## Tasks

### Data and baselines
- [x] 🤖 Player-game target table (only games the player actually played), with position group from rosters + snap data.
- [x] 🤖 Baselines from [11 → Baselines](../11-prediction-targets.md#baselines-what-beating-the-baseline-means): player rolling (last 4 + season to date, pulled toward last season early), season mean, position-average-for-role.

### Features (`features/player.py`)
- [x] 🤖 Usage: snap share and trend, target / carry / air-yards / red-zone shares, average target depth.
- [x] 🤖 Efficiency: yards per target, EPA per target or carry, **NGS** (separation, cushion, YAC above expected, rush yards over expected, time to throw for QBs), PFR (broken tackles, drops, pressures, missed tackles), FTN (blitz faced, play action).
- [x] 🤖 Team context: pass rate over expected, pace, the P03 expected margin and implied team total.
- [x] 🤖 Opponent: opponent-adjusted stats allowed to the position group; pressure rate vs pressure allowed; opponent blitz rate; for LBs, opponent rush rate and pace.
- [x] 🤖 Ripple effects from the graph: vacated targets/carries (Q2), history with the new QB (Q3).
- [x] 🤖 Availability: injury status and practice participation.
- [x] 🤖 Leakage tests for every feature family.

### Models
- [x] 🤖 LightGBM quantile models (P10/P50/P90) for amounts; Poisson/Tweedie for counts (with quantiles from the distribution). One model per target × position group. Current-season sample weights.
- [x] 🤖 Reuse the P03 backtest harness for players: walk-forward 2019–2025, live W&B logging per target (`track1-player`, tag per position group).
- [x] 🧑 **Rishi runs** the **first baseline-vs-model backtest** for each target family (e.g. `uv run nfl backtest player --target rec_yds --seasons 2019-2025`), watching the LightGBM training curves and the scoreboard fill in.
- [x] 🧑 **Rishi runs** the hyperparameter tuning (small grid or Optuna, walk-forward objective).
- [x] ✋ **Checkpoint:** review the scoreboard. Choose which targets ship (only those beating baseline). Record the rest as "not yet better than baseline" (honest, visible in the digest's scoreboard highlights).

### Explanations and watch list
- [x] 🤖 SHAP top-3 drivers per projection → readable phrases from a feature-name → template mapping (`features/descriptions.yaml`).
- [x] 🤖 Watch-list selection per [04](../04-track1-models.md#players-to-watch-definition) (role filter, max 2 per team, 6–8 in the payload), plus tough-spot candidates for *Matchup / risk*.
- [x] 🤖 Write `PlayerProjection` nodes + `HAS_PROJECTION` / `FOR_GAME` into the graph.

### Accuracy scoreboard
- [x] 🤖 After each week: score every projection vs actual. Append rows to the W&B `accuracy_scoreboard` table ([11](../11-prediction-targets.md#the-accuracy-scoreboard)). Dashboard panel: improvement % per target over the season.
- [x] 🤖 Report-card extension: 2–3 scoreboard highlights (including at least one weak spot) + a look-back at last week's watch list (projected vs actual vs range).
- [x] 🤖 Replace the P04 placeholder watch list in the payload; update the templates and checks.

### Weekly path
- [x] 🤖 `player` step in `nfl weekly run`: refit → predict → save `predictions_players.parquet` → graph write → payload.
- [x] 🧑 **Rishi runs** the first live weekly run with the player model, and reviews the digest.
- [x] 🤖 Model cards per target family.
- [x] ✋ **Checkpoint:** close P06.

## Rishi-in-the-loop moments: what to look for

- **LightGBM curves:** validation loss per boosting round. Early stopping should kick in before training loss flattens far below validation (a gap means overfitting; reduce leaves or depth, add regularization).
- **Scoreboard:** improvement % per target. Usage-type targets (targets, carries) should improve most. If yardage improves by about 0%, look at the features rather than tuning harder.
- **Interval coverage:** the P10–P90 range should hold ~80% of outcomes. Below 70% means the ranges are too narrow.
- **SHAP summary:** do the top drivers make football sense (usage share, opponent weakness)? A strange top feature often points to leakage.

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| Each shipped target beats its rolling baseline on MAE (walk-forward) | W&B backtest summaries; model cards |
| Interval coverage 72–88% for shipped targets | Scoreboard |
| Watch-list hit rate > 50% in backtests | Backtest report |
| Scoreboard live | The W&B `accuracy_scoreboard` table has rows for every scored week; the dashboard panel exists |
| Digest uses real projections | The current-week digest's *Players to watch* comes from the model; checks pass |
| No leakage | Leakage tests pass |

## Handoff to P07

- A complete weekly pipeline (ingest → ratings → game → graph → player → digest), run manually.
- Both scorecards (season + accuracy) appending every week.

## Pitfalls / notes

- Rookies and new starters: use the position-average-for-role baseline. Expect lower confidence.
- Exclude players who didn't play from scoring, but log how often a projected player didn't play (useful later).
- Defensive stats from different sources (nflverse tackles vs PFR pressures) need the ID crosswalk from P01. Check join rates.

## As built: deviations from the task list

- **Waivers (Rishi, at kickoff):** the 🧑 runs (first backtests, tuning, the first live run and its digest review) were run by the agent with `--launched-by agent`; the ✋ ship decision was Rishi's ("ship all 11"); the ✋ close was waived.
- **Groups:** WR and TE share one model (with a TE flag), LB and S share one; **EDGE/DL = DL + LB** because nflverse labels many edge rushers LB (D64). 11 models in all.
- **Count targets are scored against the baseline's median** (`baseline_p50`, D65): a count's projection is a negative-binomial median, and a median beats any mean on MAE by itself (pressures: +16% vs the rolling mean, +3% vs its median).
- **Count ranges** come from a negative binomial whose tail level is calibrated on walk-forward history (whole-number P10 / P90 covered 88%); **yards ranges** are conformalized on the same history (CQR). Not in the task list, needed for the 72–88% coverage bar.
- **Ripple effects** (graph Q2 / Q3) are recomputed in Polars from the curated data, not read from `graph_results.json`, so every past week has them (the model-experiment skill's suggestion).
- **Availability** uses the week's own injury report, the "Friday view" (D66); the pressures label lags a week like its features (`Target.label_lag`).
- **LightGBM curves:** one replayed fit per reported season (`lgb/curve_<season>`), not the per-iteration W&B callback on ~160 refits per run.
- **Tuning:** a 12-point grid per target (not Optuna), on 2017–2018 only; pressures on late 2018 (D68).
- **Leakage rule 5:** a `--no-market` research backtest per target is reported next to the canonical one.
- **Watch list:** a 3-defender cap added (a variety rule checked on 2019–2020, confirmed on 2021–2025) and a code-made baseline note (D69).
- **One model card per family** (QB, RB, WR/TE, defense) plus an overview card; a new guide, `guides/player-projections.md`.
