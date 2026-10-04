---
name: model-experiment
description: Recipe for any model training, tuning, backtest or evaluation run in this project (team ratings, game model, player models, Track 2 movement models). Use when building features for a model, writing a walk-forward backtest, logging to Weights & Biases, saving/promoting model artifacts, writing model cards, or preparing a 🧑 Rishi-runs handoff with "what to look for" notes.
---

# Model experiment recipe

Applies to P02 (ratings/Elo/trend), P03 and P08 (game model), P06 and P08 (player models) and T00–T04 (Big Data Bowl). The specs live in `documentation/04-track1-models.md`, `11-prediction-targets.md`, `07-track2-big-data-bowl.md` and `08-experiment-tracking.md`. This skill is **how** to run an experiment so results are honest, comparable and reproducible.

> Some building blocks are created by the phases themselves. **When you build or change one, update this skill** with its real function names and usage.

**Building blocks that exist (P02):**
- **As-of framework** (`nflengine.features.asof`):
  - `AsOf(season, week)` means "Tuesday before week N": only weeks strictly before it are visible.
  - `before_expr(key)` is a Polars filter for that.
  - `asof_keys(games, seasons)` lists every key; an in-progress season stops at its first unfinished week, and cancelled games are ignored.
  - `as_of(season, week, record=True)` returns an `AsOfView` whose `games()` / `plays()` / `table(name)` reads are pre-filtered and audited.
- **Leakage tests** (`nflengine.features.leakage`):
  - `assert_inputs_before(frame, key, games)` checks input rows by season/week and kickoff.
  - `assert_view_clean(view)` checks everything an audited view handed out.
  - `assert_future_invariant(build, inputs, key, protect={...ids...})` scrambles every numeric input from the key's week onward, rebuilds, and checks that outputs up to the key didn't move. Use it for bulk builders.
  - `tests/conftest.py::make_league()` is a synthetic 4-team league with known true effects, for fixtures.
- **Tracking** (`nflengine.tracking`):
  - `git_commit()` gives the short hash, with `-dirty` when the tree has changes.
  - `dataset_version()` gives the pbp snapshot date and the curation `run_at`.
  - `run_sweep(sweep_config, fn)` runs a W&B grid sweep in-process; `fn` calls `init_run`, and its params arrive in `run.config`. Cost is about 10–15 s per run on this machine.
- **Ratings features:**
  - P03+ joins `features/team_ratings`, `team_elo` and `team_trends` on (season, week, team). A row for week w is built from weeks before w (D44).
  - Recompute with `nfl ratings build`.

**Building blocks that exist (P03):**
- **Walk-forward harness** (`nflengine.models.backtest`), the one to reuse in P06/P08:
  - `walk_forward(frame, keys, model, label=..., weights=SampleWeights(...), min_train_rows=..., on_week=cb)`. For each `AsOf` key in time order:
    - `train` = labelled rows strictly before the key;
    - `test` = the key's week (unplayed rows included; they're predicted but never trained on);
    - `history` = this run's earlier predictions.
  - Then it calls `model(train, weights, test, history)`, which must return one row per test row, with `season`/`week`. Use `history` for anything fitted on walk-forward output (σ, calibration).
  - The harness raises `LeakageError` itself if train or history reach the key, or if test leaves its week.
  - `week_keys(frame, seasons)` lists the keys.
  - `SampleWeights(current_season, last_season, older)`: `.from_config(cfg)`, and `.for_current(w)` for sweeps (last season = min(1.5, w)).
- **Metrics** (`nflengine.models.metrics`): `brier`, `log_loss`, `accuracy` (ties left out), `ece`, `reliability` (bins table), `mae`, `rmse`, `score_frame(df, {name: prob_col}, outcome, amounts={name: (pred, actual)})` and `by_group(...)`. Ties count as 0.5.
- **Game model** (`models/game_model.py`): `GameModelConfig` (variant, win_method, alphas, calibration, feature lists, weights), `fit_game_model`, `GameWeekModel` (a harness model that keeps the last fit in `.last`), `coefficients()`, `feature_hash()`.
- **Runs** (`models/game_runs.py`):
  - `load_frame(qb_mode=..., live_key=...)`, `run_backtest`, `run_weight_sweep`, `run_train`;
  - `LiveLogger`, the `on_week` callback that logs `bt/*` per-week + cumulative curves for every predictor;
  - `pooled_summary`, `by_season`, `by_week_bucket`.
- **Feature tables:** `features/game_features` (`nfl features game`), built by `features.game.build_game_features` from the P02 tables plus `features.qb.team_qb_features` (QB status) and `features.venues.game_travel` (travel).
- **Outputs:** backtest predictions for every week go to `runs/backtests/game/<label>/`; the weekly `predictions_games.parquet` goes to the run folder; models go to `models/game-model/<season>-w<NN>/` and the W&B artifact `game-model`.

**Building blocks that exist (P04):**
- **Report card** (`nflengine.digest.report_card`): `grade_games(preds, games)` grades saved `predictions_games` rows (`is_primary`; ungraded when made at or after kickoff), `week_metrics`, `calibration`, and `build_report_card(run_root, season, week, games, score_watch=...)`. Reuse it in P06 for the player scoreboard: grade only what was saved before kickoff.
- **Season scorecard**: `digest.run.update_scorecard(path, row)` upserts one graded week into `runs/<season>/season_scorecard.parquet` (`SCORECARD_SCHEMA`); every digest run logs the whole table to W&B as `season_scorecard`. P06 fills `player_mae_vs_baseline`.
- **Watch-list scoring**: `digest.watchlist.score_watchlist(df)` adds `actual` / `played` / `hit` (actual > baseline). The P04 heuristic hits 40% vs a 43% base rate for all eligible players (2024–2025): **a hit rate means nothing without the base rate**, because yardage is right-skewed.
- **W&B groups**: `weekly-pipeline` / `main` (live digests), `digest-dev` / `backtest` (past weeks; `nfl digest --backtest`).

**Building blocks that exist (P05):**
- **Graph outputs as features (for P06).** Every graph build writes `graph_results.json` to the run folder (live `runs/<S>/week<NN>/`, backtests `runs/digest-backtests/<S>/week<NN>/`): `queries.q2_injury_ripple.results` (starter out, the teammate who stepped in, his usage with / without, the team's EPA with / without, sample size) and `queries.q3_qb_change.results` (expected QB vs main starter, each current receiver's targets from both). Rows are as of the run (a backtest build is as of its Tuesday), so they are leakage-safe for a walk-forward over weeks that have a build. To get them for many past weeks without Neo4j round trips, reuse `graph/tables.py` (`GraphKey`, `load_inputs`, `build_tables`) and compute the same aggregates in Polars, or run `nfl graph build --backtest` per week (~1.5 min each).
- **`GraphKey(season, week, run_time, mode)`** is the as-of key for anything that must look like a Tuesday (backtest) or the moment of a live run; its `before()` / `through()` are Polars filters.
- **W&B group `track1-graph` / `build`:** the pattern for a one-shot job with a live curve (`define_metric("load/*", step_metric="load/step")`, one point per loaded table).

## 1. Define before you code
Write these down (in the phase file or model card) before any training code:
- **Target:** exact column and grain (for example `receiving_yards` per player-game, regular season, only players who played).
- **Baseline(s)** to beat: home-team-always, Elo, market, player rolling average, constant velocity, and so on (see `documentation/11`).
- **Metrics:**
  - probabilities: Brier, log loss, ECE + reliability diagram
  - amounts: MAE (+ improvement over the baseline), P10–P90 coverage
  - counts: MAE / Poisson deviance
  - Track 2: RMSE (by horizon)
- **Evaluation window:** which seasons and weeks are walk-forward tested; which are only for tuning.

## 2. Features (as-of, leakage-tested)
- Read data via the `curated-data` skill's tables. Never hard-code paths.
- Every feature builder takes an **as-of key** (`season`, `week`) and only sees games strictly before that week.
- **Write the leakage test with the feature:** build features for a past week and assert that no input row has a game date on or after that week's games.
- Handle the late sources: PFR and FTN may be missing for the latest week, so fall back instead of crashing.
- **No research-only data** (participation) in live features.
- Hash the sorted feature list (`feature_hash`) and log it.

## 3. Walk-forward evaluation (never random splits)
- For each evaluated (season, week): fit on all rows strictly before it, predict that week, **save the predictions**.
- **Current-season sample weights** (config `training.sample_weights`, D28).
- **Hyperparameters are fixed per season.** Tune them with walk-forward over *earlier* seasons only, never on the weeks you report.
- Compare model and baselines **on exactly the same games/rows**.
- Track 2: split **by week** (no game in two splits); the test split is touched once per model family.

## 4. Weights & Biases
Always go through `nflengine.tracking.init_run(group, job_type, config, tags, launched_by)`:
- `group` from `documentation/08` (`track1-ratings`, `track1-game`, `track1-player`, `track2-bdb`, `weekly-pipeline` ...); `job_type` ∈ `tune` / `train` / `eval` / `backtest`
- `config`: model type, hyperparameters, `feature_hash`, feature list, training window, `dataset_version()` and `git_commit()` (both in `nflengine.tracking`), sample weights. **`init_run` refuses secret-looking keys; never pass credentials.**
- tags: `season:YYYY`, target, position group, `p0x`

Log **live**, not only at the end:
- LightGBM: `from wandb.integration.lightgbm import wandb_callback, log_summary`, then pass `callbacks=[wandb_callback()]` to `lgb.train(...)` and call `log_summary(booster)`.
- Walk-forward:
  - call `run.define_metric("bt/*", step_metric="bt/step")`
  - log per-week and cumulative metrics with an increasing `bt/step` (one per evaluated week), so the charts fill in as the backtest runs
  - always log the same metrics for every baseline (`bt/brier_model`, `bt/brier_elo`, ...)
- PyTorch (Track 2): loss per step, validation metrics per epoch, trajectory images for the fixed 20 plays every N epochs.

At the end:
- `run.summary[...]` holds the pooled metrics and improvement over each baseline.
- Plots:
  - reliability diagram (probabilities)
  - feature importance + SHAP summary (trees)
  - residuals by week/season
- A `wandb.Table` of predictions (sampled if large).

## 5. Artifacts and promotion
- Save model files under `paths.models / <family> / <season>-w<NN>/`, and log them as `wandb.Artifact(name="<family>", type="model")` with aliases `<season>-w<NN>`.
- **`candidate`** = beats production in walk-forward evaluation. **`production`** = what the weekly pipeline uses.
- Promotion to `production` is a ✋ checkpoint (Rishi), between weeks, never mid-run. Record it in the decisions log.
- Reproducibility: set seeds (`numpy`, `lightgbm` `seed`, `torch.manual_seed`), and log the config and dataset version.

## 6. 🧑 Rishi-runs handoff (unless waived)
Give Rishi a short block like this:
```
Command:   uv run nfl backtest game --variant model-only --seasons 2018-2025
Runtime:   ~30 s (P03); W&B group track1-game, job_type backtest
Watch:     <link to the W&B project/run>
What to look for:
 - cumulative Brier (bt/brier_model vs bt/brier_elo): model line should end BELOW Elo
 - reliability diagram: points near the diagonal; 70% calls should win ~70%
 - by-season table: one bad season (e.g. 2020) can drag the average
 - red flags: validation loss rising while training loss falls (overfit); a "too good" metric (leakage)
```
- If waived: run it with `--launched-by agent`, log it in the `PROGRESS.md` Rishi-run steps log, and paste the same "what to look for" notes plus what you actually saw.

## 7. Model card
Write one card per model family at `documentation/model_cards/<family>.md` (also set it as the W&B artifact description):
- target, grain, training window, features (with hash)
- metrics vs every baseline (pooled + by season)
- calibration notes
- known biases (closing-line optimism, early-season cold start, small samples)
- the date it was last tuned, and the current `production` version
- **"Reading the W&B charts"** (Rishi asked for this in every card): a one-minute table of the metrics, then every chart / panel / summary key the run logs, with what it shows, how to read it, and what good looks like with this model's real numbers. Check each name against the code that logs it. Examples: `documentation/model_cards/game-model-v0.md` and `team_ratings.md`
- **an honest "is this number good?" note** whenever the headline metric looks low or high to a newcomer, with our own evidence (P03: 64% accuracy vs the market's 66%)

## 8. Lessons from P02
- **Probe the objective locally before a W&B sweep.** A cheap scan of hundreds of configurations, in a scratchpad loop without W&B, shows where the optimum sits. Set the sweep grid so it isn't on an edge. P02's first guess (half-life about 4) was far from the optimum (12–16).
- **Check each piece of a model against a simple baseline on its own.** The P02 prior lost to "raw last season" in weeks 1–3 until it was seeded from a full-season fit. Its in-season home-field estimate was pure noise until it was pinned.
- **Make float accumulation order deterministic.** Polars `group_by` output order varies between runs, so sort before summing into matrices. Otherwise outputs differ at about 1e-18, and the future-invariance test flags them.

## 9. Lessons from P03
- **No intercept → don't center.** With a home-minus-away design and a home-field flag instead of an intercept, `StandardScaler()` centers the flag and erases home advantage for every non-neutral game. The prototype then lost to Elo for no real reason. Use `StandardScaler(with_mean=False)` when `fit_intercept=False`.
- **Pick on a tuning window, report on another.** P03 chose features on 2013–2017 and reported 2018–2025. Travel and success-rate ratings looked good on 2018–2025 alone but hurt on 2013–2017, so they stayed out. Keep the pre-registered choice, and show both windows in the card.
- **Fit σ and calibration on `history`, never in-sample.** The harness's `history` is exactly the earlier walk-forward predictions, so a model's σ for week w is the RMSE of its own misses before w.
- **Build the oracle once, and make it a realistic one.** Running the same backtest with post-Tuesday information (`--qb-mode actual`) measures what better data would be worth (0.0016 Brier for each game's *listed* starting QB), without letting it into reported numbers. Use information a later run could really have: "the QB with most dropbacks" also foresees in-game injuries, so it is only an upper bound.
- **Research runs must not overwrite canonical outputs.** A non-default run once overwrote the P04-facing backtest predictions; `backtest_label()` now gives every non-default run its own folder and W&B name. Re-run the canonical backtests last, after code changes.
- **Sign conventions in matchup features.** `def_*` ratings are EPA **allowed**, so the expected offense EPA in a matchup is `off + opponent def`, never `off - opponent def`. A matchup feature with the wrong sign still gets a fitted weight and can even look fine on one window. Write a test with an asymmetric defense (Sol's review caught this in P03).
- **Score every predictor on one cohort.** Metric helpers drop missing values per column, so a baseline with gaps silently gets scored on fewer games. `game_runs.scored()` keeps only rows where the model and every baseline exist, and reports `games_dropped`.
- **Strong baselines can be hard to beat for a reason.** EPA ratings alone predicted game results *worse* than Elo (0.2233 vs 0.2221): margin of victory carries information EPA lacks. Combining both, plus the one thing neither sees (QB changes), is what won.

## 10. Honesty rules
- Report results that lose to the baseline as well. A model that doesn't beat its baseline doesn't ship (`documentation/11`).
- A metric that looks too good usually means leakage. Check the as-of logic and feature timing before celebrating.
- Small differences are probably noise. A gain under ~0.002 Brier, or one that doesn't hold across most seasons, isn't a win.
- Never tune on the weeks you report as evaluation results.

## Improving this skill
Update this file when a phase adds real helpers (as-of API, backtest harness, dataset-version helper, CLI commands), or when an experiment teaches a reusable lesson. Do it in the same commit, and note it in the `PROGRESS.md` session log.
