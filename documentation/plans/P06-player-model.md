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
- [ ] 🤖 Player-game target table (only games the player actually played), with position group from rosters + snap data.
- [ ] 🤖 Baselines from [11 → Baselines](../11-prediction-targets.md#baselines-what-beating-the-baseline-means): player rolling (last 4 + season to date, pulled toward last season early), season mean, position-average-for-role.

### Features (`features/player.py`)
- [ ] 🤖 Usage: snap share and trend, target / carry / air-yards / red-zone shares, average target depth.
- [ ] 🤖 Efficiency: yards per target, EPA per target or carry, **NGS** (separation, cushion, YAC above expected, rush yards over expected, time to throw for QBs), PFR (broken tackles, drops, pressures, missed tackles), FTN (blitz faced, play action).
- [ ] 🤖 Team context: pass rate over expected, pace, the P03 expected margin and implied team total.
- [ ] 🤖 Opponent: opponent-adjusted stats allowed to the position group; pressure rate vs pressure allowed; opponent blitz rate; for LBs, opponent rush rate and pace.
- [ ] 🤖 Ripple effects from the graph: vacated targets/carries (Q2), history with the new QB (Q3).
- [ ] 🤖 Availability: injury status and practice participation.
- [ ] 🤖 Leakage tests for every feature family.

### Models
- [ ] 🤖 LightGBM quantile models (P10/P50/P90) for amounts; Poisson/Tweedie for counts (with quantiles from the distribution). One model per target × position group. Current-season sample weights.
- [ ] 🤖 Reuse the P03 backtest harness for players: walk-forward 2019–2025, live W&B logging per target (`track1-player`, tag per position group).
- [ ] 🧑 **Rishi runs** the **first baseline-vs-model backtest** for each target family (e.g. `uv run nfl backtest player --target rec_yds --seasons 2019-2025`), watching the LightGBM training curves and the scoreboard fill in.
- [ ] 🧑 **Rishi runs** the hyperparameter tuning (small grid or Optuna, walk-forward objective).
- [ ] ✋ **Checkpoint:** review the scoreboard. Choose which targets ship (only those beating baseline). Record the rest as "not yet better than baseline" (honest, visible in the digest's scoreboard highlights).

### Explanations and watch list
- [ ] 🤖 SHAP top-3 drivers per projection → readable phrases from a feature-name → template mapping (`features/descriptions.yaml`).
- [ ] 🤖 Watch-list selection per [04](../04-track1-models.md#players-to-watch-definition) (role filter, max 2 per team, 6–8 in the payload), plus tough-spot candidates for *Matchup / risk*.
- [ ] 🤖 Write `PlayerProjection` nodes + `HAS_PROJECTION` / `FOR_GAME` into the graph.

### Accuracy scoreboard
- [ ] 🤖 After each week: score every projection vs actual. Append rows to the W&B `accuracy_scoreboard` table ([11](../11-prediction-targets.md#the-accuracy-scoreboard)). Dashboard panel: improvement % per target over the season.
- [ ] 🤖 Report-card extension: 2–3 scoreboard highlights (including at least one weak spot) + a look-back at last week's watch list (projected vs actual vs range).
- [ ] 🤖 Replace the P04 placeholder watch list in the payload; update the templates and checks.

### Weekly path
- [ ] 🤖 `player` step in `nfl weekly run`: refit → predict → save `predictions_players.parquet` → graph write → payload.
- [ ] 🧑 **Rishi runs** the first live weekly run with the player model, and reviews the digest.
- [ ] 🤖 Model cards per target family.
- [ ] ✋ **Checkpoint:** close P06.

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
