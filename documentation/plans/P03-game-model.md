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
- [x] 🤖 `features/game.py`: for each game as of its week:
  - rating differences (net, pass vs pass, rush vs rush)
  - Elo difference
  - home field / neutral site
  - rest difference, short week, coming off a bye
  - travel distance and time zones crossed
  - **QB status:** the expected starter vs the QB the ratings were built on, using injuries + depth charts + the schedules' starting-QB fields
  - divisional game, week of season
- [x] 🤖 Market features (spread-implied probability, total, implied team totals) for the market-informed version only.
- [x] 🤖 Leakage tests for the game feature table.

### Backtest harness (reusable)
- [x] 🤖 `models/backtest.py`: walk-forward by week. For every (season, week) in range: train on all earlier rows (with **current-season sample weights**, [D28](../10-decisions-log.md)), predict that week, save the predictions. Logs per-week and cumulative metrics **live** to W&B, plus final plots.
- [x] 🤖 Metric helpers: Brier, log loss, accuracy, ECE + reliability diagram, margin MAE, points MAE per team and total.

### Models
- [x] 🤖 Baselines: home-team-always, Elo probability, market-implied probability (from the spread), FPI (if collected).
- [x] 🤖 v0 win model: logistic regression (small feature set), and the alternative: ridge on margin → normal CDF with σ fitted on walk-forward residuals.
- [x] 🤖 v0 score model: ridge regressions for home points and away points (or margin + total split), kept consistent with the margin.
- [x] 🤖 Calibration layer (Platt / isotonic) fitted on walk-forward predictions, if it helps.
- [x] 🧑 **Rishi runs** `uv run nfl backtest game --variant model-only --seasons 2018-2025` and watches it fill in W&B. *(Delegated: run by the agent under Rishi's P03 waiver. Final W&B run `5x33rk56`: Brier 0.2199 vs Elo 0.2221.)*
- [x] 🧑 **Rishi runs** `uv run nfl backtest game --variant market --seasons 2018-2025`. *(Delegated, `ekz4277b`: 0.2102 vs closing market 0.2104.)*
- [x] 🧑 **Rishi runs** the sample-weight sweep (current-season weight 1× / 2× / 3× / 5×), comparing pooled Brier. *(Delegated: `uv run nfl backtest game-weights`, sweep `aoflnafa`. Flat, within 0.0001, so 3× kept.)*
- [x] ✋ **Checkpoint:** choose the v0 configuration (logistic vs margin→probability, weights, calibration on or off). Record it in the model card + decisions log if it differs from the docs. *(Under Rishi's P03 waiver: margin → probability on net rating + Elo + home field + QB status, 3× weights, no calibration, chosen on the 2013–2017 tuning window. D48.)*

### Weekly production path
- [x] 🤖 `nfl train game --season 2026 --week N`: refit on everything before week N (expanding window, weights), predict week N, save `predictions_games.parquet` to the run folder, log a W&B artifact `game-model:{season}-w{NN}`, alias `production` once approved.
- [x] 🤖 Fallback logic: no current lines → model-only, and flag it.
- [x] 🧑 **Rishi runs** the first **live** prediction for the current 2026 week and sanity-checks the output table. *(Delegated: `nfl train game --season 2026 --week 4 --promote`, final run `ump6sftd`; the agent sanity-checked the table.)*

### Wrap-up
- [x] 🤖 Model card `documentation/model_cards/game-model-v0.md` (targets, features, window, backtest results vs every baseline, the closing-line caveat).
- [x] 🤖 Tests: harness on a tiny synthetic season (no leakage, correct week ordering), metric functions, score/margin consistency.
- [x] ✋ **Checkpoint:** close P03. *(Waived by Rishi for P03: "keep everything, finish it", 2026-10-03.)*

### As built: deviations from the task list (all logged in the decisions log)

- **Rishi-in-the-loop.** Rishi waived P03's 🧑 and ✋ steps for this phase. Every 🧑 command ran with `--launched-by agent` and is logged as delegated in PROGRESS.
- **Features that were built but not used (D48).** Pass/rush matchups, rest difference, short week, bye, divisional game, travel distance and time zones crossed are in `features/game_features`, but v0 doesn't use them: none helped on the 2013–2017 tuning window. Week of season isn't a feature, because the ratings' preseason prior already carries early-season uncertainty.
- **QB status (D49)** is a directional `qb_adj` (expected starter's value − the value of the QBs the ratings were built on), not a binary "backup starts" flag. Backtests use what a Tuesday run knows; live runs add the schedule's projected starter, the depth chart and Out/Doubtful replacements. The listed-starter "oracle" (`--qb-mode actual`) is research only.
- **Baselines (D50).** FPI isn't a baseline, because only the current snapshot is collected. The market baseline is the spread through a walk-forward normal CDF; the vig-free moneyline is logged as a reference.
- **Calibration.** The layer exists (`calibration: platt | isotonic`) but is off: it made Brier worse on the tuning window. Pooled ECE is 0.0315, just above the 0.03 target. It is explained by the noise floor (95th percentile 0.031 for a perfectly calibrated model) plus home field shrinking since 2020 (see the model card).
- **Score model.** It is a margin + total split (one ridge head each), so score, margin and probability always agree.
- **Commands.** `nfl features game` writes the feature table. The weight sweep is its own command, `nfl backtest game-weights`, run as a W&B sweep. Research runs (`--qb-mode actual`, non-default settings) save under suffixed folders so they never overwrite the canonical backtest predictions.
- **Code review (Sol).** It found no leakage. It did find reversed defense signs in the matchup features; after the fix, the features were re-selected on the tuning window and the matchups dropped out. It also found: incomplete market rows could become the digest row; an injured Tuesday starter wasn't replaced in live runs; baselines weren't always scored on the model's games; the QB prior was measured on data overlapping the reporting window; the oracle foresaw in-game injuries; and the canonical backtest folder held a research run. All fixed. The first round of W&B runs is superseded.
- **Data fixes.** `config/stadiums.yaml` gained 13 historical stadiums, a `tz` on every entry, and `game_venues` corrections for the 2025 international games nflverse mislabels (doc 03 → Findings from P03).

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
