# 08: Experiment Tracking (Weights & Biases / CoreWeave Forge)

Goal: a lasting, visual record of how every model behaves over the season, and of exactly what shipped each week. No numbers that scroll past in a terminal and are gone.

## Project layout

One W&B project: **`nfl-analytics-engine`**. Runs are organized by `group` and `job_type`:

| Group | Job types | What it holds |
|---|---|---|
| `track1-ratings` | `tune`, `eval` | Rating half-life / prior pull-back tuning, trend validation |
| `track1-game` | `tune`, `train`, `eval`, `backtest` | Game model experiments, walk-forward backtests (`bt/*` curves, step `bt/step`), the current-season weight sweep, and weekly fits that log the `game-model` artifact |
| `track1-player` | `tune`, `train`, `eval` | Player model experiments, one tag per position group |
| `track1-graph` | `build`, `query-test` | Graph rebuild counts and timings, golden-test results |
| `digest-dev` | `backtest` | Prompt and section experiments on past weeks |
| `track2-bdb` | `baseline`, `train`, `eval` | Big Data Bowl experiments |
| **`weekly-pipeline`** | `main`, `injury-update` | **Production runs only**, kept separate so research never clutters the record of what shipped |

Tags: `season:2026`, `week:05`, `prod`, model family, position group, and **`launched-by:rishi`** or **`launched-by:agent`** (so W&B shows which runs Rishi launched).

Local W&B files go under the data root on D: (`WANDB_DIR={NFL_DATA_ROOT}/wandb`).

**Live curves:** every training script logs metrics *during* training, not just at the end:
- boosting rounds (LightGBM's W&B callback: train and validation loss per iteration)
- epochs and steps (Track 2 neural nets)
- per-fold and per-season metrics as walk-forward backtests run

The charts fill in as training happens.

**Accuracy scoreboard:** every stat in [11-prediction-targets.md](11-prediction-targets.md) logs its error vs baseline, improvement %, and interval coverage per week to a W&B Table, `accuracy_scoreboard`. A dashboard panel shows all stats side by side.

## What each training or evaluation run logs

| Item | Detail |
|---|---|
| Config | Model type, hyperparameters, feature list (with hash), training window, **dataset version** (snapshot date + hash), market features on or off, git commit |
| Game model metrics | Brier score, log loss, accuracy, ECE, margin MAE, **always next to** the home-team, Elo and market-implied baselines |
| Player model metrics | MAE vs baseline MAE, Spearman correlation, P10–P90 coverage, watch-list hit rate (per position group) |
| Ratings / trend | Next-week predictive correlation, how much the trend adds beyond the rating |
| Track 2 metrics | RMSE overall, by horizon, by role; the gap to the constant-velocity and physics baselines |
| Plots | Reliability diagram, ROC and precision-recall curves for win/loss (secondary), feature importance and SHAP summary, residuals by week, Track 2 trajectory images |
| Artifacts | Trained model file (`game-model`, `player-model-wr`, …) and a **reference artifact** for the training data snapshot, so any result can be reproduced |

Metric correction from the original plan: ROC, precision, recall and F1 aren't the main metrics. Win probability is judged by **Brier score, log loss and calibration**; the player model by **MAE, rank correlation and coverage**; Track 2 by **RMSE**. ROC and precision-recall curves are still logged for the win/loss view.

## What each weekly pipeline run logs

- Data freshness: newest week per source, snapshot dates, whether the readiness check passed.
- Row counts per source, and data-quality check results.
- Model versions used (artifact aliases) and the feature hash.
- Graph: node and relationship counts, rebuild time, query timings, number of insights found and picked.
- Digest: prompt hash, LLM model name, word count per section, **check results** (pass/fail per check and offending tokens), whether it was regenerated, whether a warning banner was shown.
- Market data: used or fell back to model-only.
- The payload and digest as files attached to the run.

## Season scorecard (the long-term view)

At the start of each main run, score the **previous week's saved predictions** and append one row to a W&B Table, `season_scorecard`:

`season, week, games, picks_correct, brier_model, brier_elo, brier_market, logloss_model, ece_model, watchlist_hits, watchlist_total, player_mae_vs_baseline, checks_passed`

A W&B Report, **"2026 Season Dashboard"**, charts:

- weekly and cumulative Brier score: model vs Elo vs market
- cumulative calibration curve
- watch-list hit rate over time
- player-model MAE improvement over baseline, per position group
- pipeline health: on-time delivery, check pass rate

The same numbers feed the digest's **Report card** section, so W&B and the digest always agree.

## Drift signals and responses

| Signal | Threshold | Response |
|---|---|---|
| Game model worse than Elo | Rolling 4-week Brier worse for 3 straight weeks | Investigate features and data; don't retune automatically |
| Calibration drift | Season ECE above 0.05 | Refit the calibration layer |
| Player model worse than baseline | Rolling 4-week MAE worse for a position group for 3 straight weeks | Check feature freshness (snap counts, NGS lag) and role changes |
| Data freshness | Any source more than 1 week stale | Alert; the footer notes it |
| Checks | Failure rate above 20% of runs over a month | Revisit prompt or check rules |

## Model registry

- Each weekly fit → a new artifact version tagged `{season}-w{NN}`.
- The **`production`** alias points to the version the weekly pipeline uses.
- The **`candidate`** alias is for experiments that beat production in walk-forward evaluation. Promote it manually, between weeks, never mid-run.
