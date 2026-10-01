# P03: Game Model v0 (Win Probability, Margin, Predicted Score)

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P02
- **Unlocks:** P04
- **Read first:** [04 → B. Game model](../04-track1-models.md#b-game-model-win-probability), [11 → Game and team targets](../11-prediction-targets.md#game-and-team-targets), [04 → Training and retraining cadence](../04-track1-models.md#training-and-retraining-cadence), [08](../08-experiment-tracking.md)

## Goal

A calibrated **win probability**, **expected margin** and **predicted score** for every game, in **model-only** and **market-informed** versions. Backed by a walk-forward backtest over 2018–2025 that shows how it compares with home-team-always, Elo, ESPN FPI (if collected) and the market.

## Scope

- **In:** the game feature table, v0 models (logistic / ridge), the predicted-score heads, the walk-forward backtest harness (reused by P06 and P08), calibration, weekly refit with current-season weighting, model artifacts, model card.
- **Out:** LightGBM game model and the full injury/weather feature set (P08).

## Tasks

### Features
- [ ] 🤖 `features/game.py`: for each game as of its week:
  - rating differences (net, pass vs pass, rush vs rush)
  - Elo difference
  - home field / neutral site
  - rest difference, short week, coming off a bye
  - travel distance and time zones crossed
  - **QB status:** the expected starter vs the QB the ratings were built on, using injuries + depth charts + the schedules' starting-QB fields
  - divisional game, week of season
- [ ] 🤖 Market features (spread-implied probability, total, implied team totals) for the market-informed version only.
- [ ] 🤖 Leakage tests for the game feature table.

### Backtest harness (reusable)
- [ ] 🤖 `models/backtest.py`: walk-forward by week. For every (season, week) in range: train on all earlier rows (with **current-season sample weights**, [D28](../10-decisions-log.md)), predict that week, save the predictions. Logs per-week and cumulative metrics **live** to W&B, plus final plots.
- [ ] 🤖 Metric helpers: Brier, log loss, accuracy, ECE + reliability diagram, margin MAE, points MAE per team and total.

### Models
- [ ] 🤖 Baselines: home-team-always, Elo probability, market-implied probability (from the spread), FPI (if collected).
- [ ] 🤖 v0 win model: logistic regression (small feature set), and the alternative: ridge on margin → normal CDF with σ fitted on walk-forward residuals.
- [ ] 🤖 v0 score model: ridge regressions for home points and away points (or margin + total split), kept consistent with the margin.
- [ ] 🤖 Calibration layer (Platt / isotonic) fitted on walk-forward predictions, if it helps.
- [ ] 🧑 **Rishi runs** `uv run nfl backtest game --variant model-only --seasons 2018-2025` and watches it fill in W&B.
- [ ] 🧑 **Rishi runs** `uv run nfl backtest game --variant market --seasons 2018-2025`.
- [ ] 🧑 **Rishi runs** the sample-weight sweep (current-season weight 1× / 2× / 3× / 5×), comparing pooled Brier.
- [ ] ✋ **Checkpoint:** choose the v0 configuration (logistic vs margin→probability, weights, calibration on or off). Record it in the model card + decisions log if it differs from the docs.

### Weekly production path
- [ ] 🤖 `nfl train game --season 2026 --week N`: refit on everything before week N (expanding window, weights), predict week N, save `predictions_games.parquet` to the run folder, log a W&B artifact `game-model:{season}-w{NN}`, alias `production` once approved.
- [ ] 🤖 Fallback logic: no current lines → model-only, and flag it.
- [ ] 🧑 **Rishi runs** the first **live** prediction for the current 2026 week and sanity-checks the output table.

### Wrap-up
- [ ] 🤖 Model card `documentation/model_cards/game-model-v0.md` (targets, features, window, backtest results vs every baseline, the closing-line caveat).
- [ ] 🤖 Tests: harness on a tiny synthetic season (no leakage, correct week ordering), metric functions, score/margin consistency.
- [ ] ✋ **Checkpoint:** close P03.

## Rishi-in-the-loop moments: what to look for

- **Backtest live curves:** cumulative Brier score by week for model vs Elo vs market. The model line should end **below Elo** (lower is better). Expect the market to be the toughest line to beat.
- **Reliability diagram:** points should sit close to the diagonal. A model whose 70% calls win 60% of the time is overconfident.
- **By-season table:** look for one bad season dragging everything down (rule changes, or a strange season like 2020).
- **Sample-weight sweep:** if heavier current-season weight hurts early weeks but helps late ones, consider a weight that depends on the week. Note it.

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| **Model-only beats Elo** on pooled walk-forward Brier, 2018–2025 | W&B backtest summary; numbers copied into the model card |
| Market-informed is roughly as good as the market (stretch goal) | Same, reported honestly either way |
| Calibration acceptable | Pooled ECE ≤ 0.03 (or explained) |
| Predicted scores consistent and scored | Points MAE per team and total logged; the margin from the scores matches the expected margin |
| Live prediction works | Current-week `predictions_games.parquet` exists; the W&B artifact has the `production` alias |
| Tests pass | `uv run pytest` |

## Handoff to P04

- The `predictions_games` schema (game_id, win probabilities, margin, predicted scores, confidence, variant, model_version).
- The backtest harness for P06 and P08.
- Saved predictions for every backtest week, so P04's report card can be tested on past weeks.

## Pitfalls / notes

- σ for margin → probability: estimate it on walk-forward residuals, not in-sample.
- Ties: drop them from classification training, or count them as 0.5. Be consistent.
- Neutral-site / international games: home field = 0.
- The 2020 season (no fans) may show a weaker home field. Consider a season-level home-field feature, or just note it.
