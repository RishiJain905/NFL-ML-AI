---
name: model-experiment
description: Recipe for any model training, tuning, backtest or evaluation run in this project (team ratings, game model, player models, Track 2 movement models). Use when building features for a model, writing a walk-forward backtest, logging to Weights & Biases, saving/promoting model artifacts, writing model cards, or preparing a 🧑 Rishi-runs handoff with "what to look for" notes.
---

# Model experiment recipe

Applies to P02 (ratings/Elo/trend), P03 and P08 (game model), P06 and P08 (player models) and T00–T04 (Big Data Bowl). The specs live in `documentation/04-track1-models.md`, `11-prediction-targets.md`, `07-track2-big-data-bowl.md` and `08-experiment-tracking.md`. This skill is **how** to run an experiment so results are honest, comparable and reproducible.

> Some building blocks are created by the phases themselves: the as-of framework (`features/asof.py`, P02) and the walk-forward harness (`models/backtest.py`, P03). **When you build or change them, update this skill** with their real function names and usage.

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
- `config`: model type, hyperparameters, `feature_hash`, feature list, training window, `dataset_version` (the nflverse pbp snapshot date + `curated/_quality/latest.json` `run_at`, until a helper exists), git commit (`git rev-parse --short HEAD`), sample weights. **`init_run` refuses secret-looking keys; never pass credentials.**
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
Runtime:   ~N minutes; W&B group track1-game, job_type backtest
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

## 8. Honesty rules
- Report results that lose to the baseline as well. A model that doesn't beat its baseline doesn't ship (`documentation/11`).
- A metric that looks too good usually means leakage. Check the as-of logic and feature timing before celebrating.
- Small differences are probably noise. A gain under ~0.002 Brier, or one that doesn't hold across most seasons, isn't a win.
- Never tune on the weeks you report as evaluation results.

## Improving this skill
Update this file when a phase adds real helpers (as-of API, backtest harness, dataset-version helper, CLI commands), or when an experiment teaches a reusable lesson. Do it in the same commit, and note it in the `PROGRESS.md` session log.
