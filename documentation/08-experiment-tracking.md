# 08: Experiment Tracking (Weights & Biases / CoreWeave Forge)

Goal: a lasting, visual record of how every model behaves over the season, and of exactly what shipped each week. No numbers that scroll past in a terminal and are gone.

## Project layout

One W&B project: **`nfl-analytics-engine`**. Runs are organized by `group` and `job_type`:

| Group | Job types | What it holds |
|---|---|---|
| `track1-ratings` | `tune`, `eval` | Rating half-life / prior pull-back tuning, trend validation |
| `track1-game` | `tune`, `train`, `eval`, `backtest` | Game model experiments, walk-forward backtests (`bt/*` curves, step `bt/step`), the current-season weight sweep, and weekly fits that log the `game-model` artifact |
| `track1-player` | `tune`, `train`, `eval`, `backtest` | Player model (P06): one walk-forward backtest per target × group (`bt/*` curves, LightGBM `lgb/curve_<season>`, scoreboard rows, feature importance, SHAP summary), a grid sweep per target, the weekly fit (`player-model` artifact) and the weekly scoreboard run (`scoreboard/*` curves + the `accuracy_scoreboard` table); tags `group:<qb/rb/wrte/edge/lbs>`, `target:<name>` |
| `track1-graph` | `build` | Graph rebuilds (`nfl graph build` and the weekly `graph` step; tag `live` / `backtest`): a `load/*` curve per loaded table (seconds, rows, rows per second), `count/node/*` and `count/rel/*`, `time/*` per phase, `query/<name>/rows` and `/seconds`, `insights/*`, and tables of the candidates and picks. Golden tests run in pytest (`-m integration`), not W&B (P05) |
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

**As built in P06 (player model):**
- **LightGBM curves:** a walk-forward backtest refits every target about 160 times, so the per-iteration W&B callback would bury the charts. Instead each reported season's opening fit is replayed with the previous season held out and logged as one `lgb/curve_<season>` chart (train vs validation loss per boosting round).
- **Accuracy scoreboard:** `runs/<season>/accuracy_scoreboard.parquet` on D: holds one row per (week, target, group); `mode = live` rows are graded pre-kickoff projections (from week 4 of 2026, scored by week 5's run), `mode = backtest` rows are walk-forward re-runs (2026 weeks 1–3; every 2019–2025 week in `runs/backtests/player/scoreboard.parquet`). The weekly `scoreboard-S-wNN` run logs the table and one `scoreboard/improvement_<target>_<group>` and `scoreboard/coverage_<target>_<group>` curve per model over the season: that's the dashboard panel until P07's W&B Report. Count targets are scored against the baseline's median (D65).
- **Artifacts:** one `player-model` artifact holds all 11 models' boosters (not one per group).
- Details: [W&B guide §4.7 and §6.3b](guides/weights-and-biases.md), [player model card](model_cards/player-model-v1.md).

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

**As built in P04.** The `weekly-pipeline` / `main` run is logged by the digest step (`nfl digest`, also the last step of `nfl weekly run`); `nfl digest --backtest` logs the same run to `digest-dev` / `backtest`.
- **Config:** season, week, mode, run time, prompt hash, LLM provider and model, word budgets, followed teams, git commit, dataset version.
- **Summary:** `checks_passed`, `check/<name>` (1/0) and `check_issues/<name>`, `regenerated`, `banner`, `words/<section>` and `words_total`, `market_data_used`, item counts, the report card numbers (`rc/*`), and for a real LLM `llm/calls`, `llm/latency_s`, `llm/prompt_tokens`, `llm/completion_tokens`, `llm/reasoning_tokens`, `llm/cost_usd`, `llm/providers`, `llm/fallback`, `llm/final_writer`.
- **Charts** (added after Rishi saw only system charts on mobile: these runs are one-shot, so nothing else draws a chart): `season/*` lines over the graded weeks: `brier_model` / `brier_elo` / `brier_market` and their `cum_*` versions, `pick_accuracy` and `cum_pick_accuracy`, `points_mae`, `watchlist_hit_rate` and its cumulative version. These are standard panels, so they render in the mobile app. Plus bar charts `words_per_section` and `check_issue_counts` (web).
- **Tables:** `season_scorecard` (the whole season so far), `check_issues`, `game_outlook`.
- **Artifact:** `digest` (`digest-backtest` for backtests), aliased `<season>-w<NN>`, holding `payload.json`, `raw_llm_output.json`, `checks.json` and `digest.md`.

The game-model refit logs its own `track1-game` / `train` run. Data freshness per source is in the digest header and `payload.meta.sources`. Ingest row counts are in the raw run manifest; logging them to W&B is P07 work.

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

**As built in P05.** Each graph rebuild is its own `track1-graph` / `build` run (`graph-<season>-w<NN>`, `-backtest` for past weeks built by `nfl graph build --backtest`; backtest digests build their graph without a separate W&B run and log `graph_status` / `graph_items` in their digest run). Each build also logs the artifact `graph-results` (type `graph`, the run's `graph_results.json`: counts, timings, every query's rows, all candidates and the picks), aliased `<season>-w<NN>` (backtests `-backtest`); P06 reads Q2 / Q3 rows from it. The digest runs carry the tag `graph:<status>`, and their summary has `graph_status` and `graph_items`. The full walkthrough of every run, chart and artifact is the [W&B guide](guides/weights-and-biases.md); its §9 lists where the as-built logging differs from this spec (groups and tags actually used, plots not built yet, the season dashboard and accuracy scoreboard still to come in P06/P07) and §10 the known gaps to settle in P07 (artifact lineage from the digest to its inputs, who moves the `production` alias).


## As built in P07: the whole weekly run, the season dashboard, drift checks

P07 is **manual-first** (D71): the run is started by hand, but every run now records itself. The walkthrough is the [weekly operations guide](guides/weekly-operations.md); every chart is in the [W&B guide](guides/weights-and-biases.md) §4.9, §4.10 and §7.

- **The whole weekly run is now one W&B run**, `weekly-pipeline` / `pipeline`, `pipeline-<season>-w<NN>` (`ops/summary.py`), logged after every `nfl weekly run` (failed and "not ready" ones too; a failed run ends with exit code 1). It covers what this doc's "What each weekly pipeline run logs" asked for and P04 left out: **step timings** (`step/<name>_seconds`, `_status`), **data freshness** (`freshness` table: newest snapshot and age per ingested dataset, newest week of each weekly source vs the week before the target; `stale_sources`), **ingest row counts and failures** (`ingest/*`), **quality checks** (`quality/*`), the digest's check results, **drift** (`drift/<signal>` + `drift` table) and the deadline (`on_time`, `hours_before_deadline`). The same numbers are in `runs/<season>/week<NN>/run_summary.json`, and one row per run in `runs/<season>/pipeline_history.parquet`. The digest run stays `weekly-pipeline` / `main`.
- **Lineage:** the pipeline run `use_artifact`s the week's `game-model`, `graph-results`, `player-model` and `digest` (by their `<season>-w<NN>` alias), so the W&B lineage view links a published digest to its model and graph (W&B guide §10, gap 3).
- **`production` alias:** `--auto` moves it to each live fit (`ops.promote_auto`, D72): it records which fit the published digest used. Manual runs move it only with `--promote`. `candidate` stays for P08.
- **Simulations** (`--as-of` in the past) log `weekly-pipeline` / `simulation`, `sim-<season>-w<NN>`, and never send alerts.
- **The Saturday injury update** logs `weekly-pipeline` / `injury-update` with the artifact `injury-update` (type `digest`).
- **Season dashboard** (`ops/dashboard.py`): after each published weekly run, one `season-dashboard` / `dashboard` run (`season-<season>-w<NN>`) logs the whole season so far as line series (game Brier vs Elo vs market, weekly and cumulative; the cumulative calibration curve and season ECE; watch-list hit rate; player-model MAE improvement per position group; pipeline health: on time, hours before kickoff, run minutes, check pass rate) and takes the tag `dashboard-current` from the previous one. The W&B Report **"2026 Season Dashboard"** (`nfl dashboard build`) only reads the run with that tag, so it updates by itself after every run.
- **Drift signals** (`ops/drift.py`, thresholds in `config/settings.yaml` → `drift`) are evaluated after every run and only alert (console, `run_summary.json`, W&B alerts): `game_vs_elo` (rolling 4 graded weeks, game-weighted Brier, behind Elo in 3 windows in a row), `calibration` (season ECE above the higher of 0.05 and the 90th percentile of a perfectly calibrated model's ECE on the same games, from 64 games on), `player_vs_baseline` per position group (rolling 4-week MAE vs the baseline, counts vs its median per D65, behind 3 windows in a row), `data_freshness` (a dataset more than 7 days old or a weekly source more than one week behind) and `checks` (more than 20% of the last 4 digests failed their final checks). **Replayed over 2019–2025** before trusting them (`drift.replay_drift`): doc 08's flat ECE > 0.05 would have alerted in **85%** of weeks, because a perfectly calibrated model averages about 0.08 on one season's games; hence the noise-adjusted limit (D75). On the probabilities the digest shows, the as-built rules fired in 2% (game vs Elo) and 5% (calibration) of weeks; the player rules never fired. The doc's response "refit the calibration layer" becomes "check the walk-forward backtest before turning one on", since v0 has none (D48).

## As built in P08: v1 evaluated, new groups and artifacts

- **Game model v1** (`track1-game`, tags `p08`, `version:v1`): backtests `backtest-v1_<variant>` log v0 as a fifth predictor on the same games (`bt/cum_brier_v0` ...), the tuning sweep `game-v1-<variant>-<stamp>` scores 2013–2017 only. **Not promoted** (D79): the `candidate` alias was not needed, `production` stays on v0's weekly fits; `game_model.version` in `settings.yaml` is the switch, and a promotion stays a ✋ between weeks.
- **New player targets** (`track1-player`, tags `p08`): Brier / ECE / reliability charts for probability targets and event counts; the scoreboard's `brier_model`, `brier_baseline`, `calibration_ece` are now filled (doc 11).
- **Team stat totals** (new group **`track1-team`**): backtest / tune / train / scoreboard runs and the artifact **`team-model`** (aliases `<season>-w<NN>`, `production` with `--auto` like the others, D72).
- **Consistency** (`track1-player` / `eval`, `consistency-2019-2025`) and the weekly `consistency/*` keys on the pipeline run.
- **Drift:** a sixth signal, `player_prob_vs_baseline` (D85): doc 08's "player model worse than baseline" rule on the Brier score of the chances, per group (0 alerts in the 2019–2025 replay). CB/S and TEAM rows join `player_vs_baseline` as extra groups.
- Every chart and key: the [W&B guide](guides/weights-and-biases.md) §4.7, §4.9, §6.3, §6.3b, §6.3c.
