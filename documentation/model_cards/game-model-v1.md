# Model card: game model v1 (P08), evaluated and **not promoted**

**Family:** Track 1 model B ([04 → B](../04-track1-models.md#b-game-model-win-probability)), step 2 of "Algorithms" (LightGBM on the full feature set with monotonic constraints) · **Built and decided:** 2026-10-04 (D78, D79; P08's 🧑 runs and ✋ decisions waived by Rishi, decided by a rule written down before the reported runs) · **Status:** research option. **Production stays [v0](game-model-v0.md)** (`settings.yaml` → `game_model.version: v0`) · **Code:** `features/game_extra.py` (new features), `features/game.py` (joins them), `models/game_model_v1.py` (the model), `models/game_runs.py` (`--version v1` backtests, `run_tune_v1`, `run_train(version=)`) · **Commands:** `nfl tune game`, `nfl backtest game --version v1`, `nfl train game --version v1` · **Files:** `runs/backtests/game/v1_model_only/`, `v1_market/` (predictions + summary), `runs/backtests/game/v1-tune-<time>/results.csv` (the sweep).

## In one paragraph

v1 asked whether gradient-boosted trees, given everything v0 leaves out (pass/rush matchups, success rate, rest, byes, short weeks, travel, time zones, divisional games, **who is injured**, **weather**, **a trailing home edge**), predict games better than v0's four-input ridge. They don't, measurably: on the 2018–2025 walk-forward v1's model-only Brier is **0.2193 vs v0's 0.2199** (−0.0005, with a 95% interval from −0.0012 to +0.0002), better in 5 of 8 seasons, but **worse calibrated** (ECE 0.035 vs 0.032), and on the 2013–2017 tuning window every one of 24 settings was *worse* than v0. The rule written before the reported runs (below) keeps v0. The new features stay in the feature table and the code stays runnable, so P10's pre-season retune can try again with a new season of data.

## How it works, in plain language

**v0 first, then small corrections.** v1 fits v0's ridge exactly as v0 does (same four margin inputs, same weights, same games), then grows **50 shallow trees** that learn only what the ridge got wrong, from the wider feature list. The final margin is "v0's margin + the trees' correction". The same happens for the total. Everything after that is v0's machinery: win probability = `Φ(margin / σ)` with σ from v1's own earlier misses, and the score split `home = (total + margin) / 2`.

**Why boost from the ridge instead of letting the trees do everything?** With ~285 games a season, trees on their own can't relearn the smooth "better team wins by more" relationship the ridge captures with four numbers. In the sweep, trees from scratch were 0.004–0.009 Brier worse than v0; trees boosted from the ridge were 0.0008–0.0055 worse. Starting from v0 keeps its strength and asks a narrower question: is there anything left?

**Monotonic constraints.** A correction can never say "a better rating, Elo, QB, injury edge or spread makes the home team *less* likely to win" (`MONOTONE` in `models/game_model_v1.py`; for the total: better offenses, weaker defenses, more league scoring and the line can only raise it; wind and offensive injuries can only lower it). Tests check the margin never falls as `net_diff` or the injury edge rises.

**The three new feature families** (`features/game_extra.py`, all as of the game's week):

| Family | Definition | Real numbers |
|---|---|---|
| **Injury load** (Tuesday view) | Regulars of this season (≥ 50% of their side's snaps in at least 2 of the team's games before its last one, and at least once in the 3 before that) who have **no snaps in the team's last game**, weighted by their average snap share, by group: `ol`, `skill` (RB/WR/TE/FB), `front` (DL/LB), `secondary` (CB/S). QBs are left out (QB status covers them). Game columns: `inj_off_edge` / `inj_def_edge` (away load − home load: + = good for home), `inj_off_sum` / `inj_def_sum` (both sides, for totals) | Per team-game (2013–2025) the load averages 0.49 regular-equivalents on the offensive line, 0.30 skill, 0.32 front, 0.44 secondary; all four together 1.2 (2013) to 1.9 (2021). Correlation with the home margin: +0.09 (offense edge), +0.08 (defense edge) |
| **Weather** (coarse) | `wind_15` = outdoors and wind ≥ 15 mph (the 90th percentile of outdoor games), `cold_32` = outdoors and ≤ 32 °F; 0 under a roof; null outdoors without a reading. Played games use the actual reading; a live run's unplayed games use the Open-Meteo forecast pulled before the run | 8% of 2013–2025 games are windy, 5% cold (6–11% and 2–10% by season). 2022 has half its outdoor readings missing |
| **Trailing home edge** | The league's mean home margin over non-neutral regular-season games of the previous 3 seasons, × the home-field flag (`home_edge_trailing`) | 2.4–3.0 points for 2013–2016, **0.57 in 2022**, 2.2 for 2025–2026 |

**Why the injury load is a Tuesday view.** The live weekly run happens on Tuesday (`--auto`, D71), before any injury report for the week exists. P06's player model uses the week's report (the "Friday view", D66) because its live run happens later in the week and the Saturday update re-runs it; the game model keeps P03's Tuesday rule (leakage rule 4), so backtests and live runs see the same kind of information. "Missed the last game" is what Tuesday knows.

**Weather mismatch (the phase's pitfall).** Training uses observed weather, a live run uses a forecast up to 6 days out. Only coarse buckets are used, so a forecast of 17 mph and an actual 14 mph disagree only at the edge. Backtests use actual weather for the predicted week too, which flatters them slightly; it didn't matter here (see the results).

**What was not built.** ESPN FPI and QBR: only the current snapshot is collected (no history to train on, D50). They stay available for the digest's context.

## How it was tuned (W&B sweep [47vq67gw](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/47vq67gw), 24 runs, `nfl tune game`)

A grid over `base` (ridge / none) × `num_leaves` (4, 7) × `n_estimators` (50, 150, 400) × `min_data_in_leaf` (100, 200) (learning rate 0.02, L2 10), each scored by walk-forward Brier on **2013–2017 only** (P03's tuning window; 1,335 games), with v0 scored on the same games in the same run.

| Setting | Brier 2013–2017 | v1 − v0 (95%) | ECE |
|---|---|---|---|
| v0 (reference) | 0.2149 | | 0.022 |
| **ridge base, 4 leaves, 50 trees, min leaf 100 (best, now in `settings.yaml`)** | **0.2157** | +0.0008 (−0.0001 to +0.0016) | 0.016 |
| ridge base, 4 leaves, 150 trees | 0.2169–0.2170 | +0.0020 | 0.018–0.023 |
| ridge base, 400 trees | 0.2184–0.2204 | +0.0035 to +0.0055 | 0.030–0.038 |
| trees from scratch (`none`) | 0.2193–0.2241 | +0.0044 to +0.0092 | 0.024–0.060 |

**Every setting lost to v0; more trees always lost more.** A calibration layer on the best setting (fitted on its own walk-forward history, never in-sample) made it worse again: Platt 0.2160 ([n642qpdo](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/n642qpdo)), isotonic 0.2167 ([i0dqfmvy](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/i0dqfmvy)), as P03 found for v0.

## Results: walk-forward 2018–2025 (2,227 games; v0 on the same games in the same run)

| Variant | Brier v1 | Brier v0 | v1 − v0 (95%, whole weeks resampled) | Seasons v1 better | Log loss v1 / v0 | Accuracy v1 / v0 | ECE v1 / v0 |
|---|---|---|---|---|---|---|---|
| **Model-only** ([5jcz8pn4](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/5jcz8pn4)) | 0.2193 | 0.2199 | −0.0005 (−0.0012 to +0.0002) | 5 of 8 | 0.6311 / 0.6321 | 64.3% / 64.0% | **0.0354 / 0.0315** |
| **Market-informed** ([7abl18yf](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/7abl18yf)) | 0.2101 | 0.2102 | −0.0002 (−0.0007 to +0.0005) | 3 of 8 | 0.6094 / 0.6098 | 66.2% / 66.3% | 0.0255 / 0.0226 |

Elo 0.2221, closing market 0.2104, home team always 0.2477 (identical to the v0 card: the carried v0 reproduces it exactly).

**By season (model-only Brier):**

| Season | Games | v1 | v0 | Elo | Market |
|---|---|---|---|---|---|
| 2018 | 267 | 0.2176 | 0.2189 | 0.2222 | 0.2108 |
| 2019 | 267 | 0.2218 | 0.2221 | 0.2217 | 0.2128 |
| 2020 | 269 | 0.2088 | 0.2102 | 0.2144 | 0.2026 |
| 2021 | 285 | 0.2238 | 0.2238 | 0.2310 | 0.2171 |
| 2022 | 284 | 0.2218 | 0.2223 | 0.2215 | 0.2084 |
| 2023 | 285 | 0.2304 | 0.2301 | 0.2326 | 0.2171 |
| 2024 | 285 | 0.2096 | 0.2114 | 0.2107 | 0.2033 |
| 2025 | 285 | 0.2203 | 0.2195 | 0.2223 | 0.2108 |

**By part of the season (model-only, v1 / v0):** weeks 1–4 0.2256 / 0.2262 · weeks 5–9 0.2165 / 0.2175 · weeks 10+ 0.2181 / 0.2183 · playoffs 0.2161 / 0.2168. Market-informed: 0.2189 / 0.2190 · 0.2099 / 0.2107 · 0.2053 / 0.2052 · 0.2157 / 0.2153.

**Margin and scores (model-only, v1 / v0):** margin MAE 10.186 / 10.190; total MAE 10.631 / 10.635; points MAE per team 7.45 / 7.46. Market-informed: margin 9.870 / 9.862, total 10.455 / 10.444.

## The ✋ decision: keep v0 (D79)

The rule was written down before any 2018–2025 v1 run (the P08 phase file asks to "promote only if it beats v0 on pooled walk-forward Brier **and** calibration"):

| Criterion | Needed | v1 | Pass? |
|---|---|---|---|
| a. Model-only Brier gain | ≥ 0.002, or a 95% interval entirely below 0 | −0.0005, interval −0.0012 to +0.0002 | **No** |
| b. Seasons better | ≥ 5 of 8 | 5 of 8 | Yes |
| c. Calibration | ECE ≤ v0's + 0.005 (and the phase: better) | 0.0354 vs 0.0315: worse | **No** (worse; within the +0.005 allowance, not "better") |
| d. Market variant | not worse than v0 market by > 0.0005 | −0.0002 | Yes |

**Kept v0.** v1's half-thousandth of Brier is the size of the noise (the tuning window went the other way, +0.0008), and its probabilities are less calibrated. Doc 04's step 3 applies: keep the simplest model that's statistically as good.

## Why it didn't win (the evidence)

- **Little is left to learn.** Correlations of each new feature with v0's walk-forward margin misses (2013–2017 / 2018–2025): injury offense edge +0.022 / +0.027, injury defense edge +0.003 / +0.026, rest +0.016 / +0.024, travel +0.001 / −0.037, time zones −0.001 / −0.059, rush matchup −0.044 / +0.054, trailing home edge −0.026 / −0.039. All |r| ≤ 0.06, and most flip sign between the windows. A residual correlation of 0.03 explains about 0.1% of the miss variance: worth about 0.0001 Brier.
- **The ratings already carry injuries.** Adding the two injury edges to v0's own ridge made 2013–2017 Brier *worse* (0.2166 vs 0.2149): the injured players' absence is already in the team's recent EPA, Elo moves after the games they missed, and QB status covers the biggest one.
- **The trees mostly chased noise.** In a fit on all games through 2025 the margin trees split mostly on early-season prior weight, its interaction with the rating, travel and the rush matchup (the same features P03 dropped as noise); the injury edges got 7% and 2% of the gain, weather none. The correction is small (mean |v1 − v0| margin 0.33 points, max 2.0).
- **One real signal: wind lowers scoring.** `wind_15` is the total head's top split (23% of its gain) and has the most consistent residual correlation with totals (−0.03 / −0.075). It moves the predicted total, not who wins, so it can't move the Brier score. If a future version predicts totals separately (P10, or the team stat totals), wind is worth keeping.
- **Home field is already handled.** v0's walk-forward misses for home teams average −0.95 to +0.72 points per season (2013–2025): the 3× current-season weight lets the single home-field weight follow the decline well enough. The trailing edge added nothing.

**Is "no better than v0" a good result?** Yes, in the sense that matters: it was found honestly (tuning on 2013–2017, reporting on 2018–2025, v0 on the same games, a rule fixed in advance), and it tells us where the ceiling is with Tuesday information. The market-informed v0 is already level with the closing line (0.2102 vs 0.2104), and the P03 oracle showed the next real gain is better *QB* information on game day (−0.0016 with the listed starter), not more features on Tuesday.

## Reading the W&B charts

All runs are in group **`track1-game`**, tagged `p08`, `version:v1`. Everything in the v0 card's "Reading the W&B charts" applies; v1 runs add **v0 as a fifth predictor** on the same games.

### Backtest runs (`backtest-v1_model_only`, `backtest-v1_market`; job type `backtest`)

| Chart / key | What it shows | How to read it |
|---|---|---|
| `bt/cum_brier_model` vs **`bt/cum_brier_v0`** | Cumulative Brier of v1 and v0 over every reported game so far (x = `bt/step`, 2018 w1 → 2025 SB) | **The overlay the phase asked for.** The two lines run on top of each other all the way (final 0.2193 vs 0.2199). A real improvement would open a visible gap that keeps widening |
| `bt/brier_v0` | v0's Brier on that week's games | Week-to-week noise; compare with `bt/brier_model` |
| `bt/cum_log_loss_v0`, `bt/cum_margin_mae_v0`, `bt/cum_total_mae_v0` | v0's cumulative log loss and margin / total MAE, next to the model's `bt/cum_*_model` | Same story: within a hundredth of a point |
| `bt/cum_brier_elo` / `_market` / `_home`, `bt/sigma`, ... | As in the v0 card | Unchanged |
| `reliability_v0_vs_v1` | v1's actual home win rate per probability bin, v0's (interpolated to the same bins) and the diagonal | Both curves hug the diagonal in the middle; v1 is a little further off in the 0.3–0.6 range (ECE 0.035 vs 0.032) |
| `importance_margin`, `importance_total` | Bar charts: gain of each feature in the last fit's margin and total trees | Which features the corrections use. Gain says "used", not "helped": the margin's top features are the ones P03 found noisy |
| `feature_importance` | The same as a table (head, feature, gain) | |
| `by_season`, `by_week_bucket`, `reliability_table`, `brier_by_season` | As in the v0 card, now with a `v0` predictor column / series | The by-season table above comes from here |

**Run summary:** `brier_v0`, `log_loss_v0`, `accuracy_v0`, `ece_v0`, `mae_margin_v0`, `mae_total_v0` (v0 on the same cohort); `brier_gain_vs_v0` (v0 − v1: positive = v1 better) and `beats_v0`; `seasons_beating_v0`; **`brier_diff_vs_v0`** with **`brier_diff_vs_v0_lo95` / `_hi95`** (v1 − v0 and its 95% interval from resampling whole weeks: an interval that includes 0 means "can't tell them apart").

### Tuning sweep (`nfl tune game`; job type `tune`; sweep 47vq67gw)

Each run is a 2013–2017 walk-forward with the same `bt/*` curves (including `bt/cum_brier_v0`). On the sweep page, the parallel-coordinates chart of `base`, `num_leaves`, `n_estimators`, `min_data_in_leaf` → `brier_model` shows the pattern at a glance: every line ends above v0's 0.2149, and the lines with more trees end highest. Summary keys as in the backtests, plus `brier_diff_vs_v0_lo95` / `_hi95`. The results are also in `runs/backtests/game/v1-tune-20261004-201416/results.csv`.

## The math

- **Base:** v0's ridge heads `m₀(x)`, `t₀(x)` fitted on the week's training rows with the same weights (3× / 1.5× / 1×).
- **Margin:** `m(x) = m₀(x) + Σₖ η·fₖ(x)`, trees `fₖ` fitted by LightGBM on squared error with `init_score = m₀` (so they model the ridge's residuals), learning rate η = 0.02, 50 trees of ≤ 4 leaves, ≥ 100 games per leaf, L2 10, 80% feature / row sampling, monotone constraints (+1 / −1 per feature, `monotone_constraints_method: advanced`).
- **Total:** `t(x) = t₀(x) + Σₖ η·gₖ(x)`, the same way.
- **Win probability:** `p = Φ(m / σ)`, σ = RMSE of v1's earlier walk-forward margin misses (13.5 until 400 exist). Optional `calibration: platt | isotonic` on earlier walk-forward probabilities (tried, worse).
- **v0 on the same rows:** the base's own prediction, with its σ from v0's earlier misses (carried in `v0_margin` / `v0_prob` history columns), equals a standalone v0 walk-forward to 1e-9 (a test checks it).
- **Bootstrap:** per-game Brier difference `d = (p₁ − y)² − (p₀ − y)²`, summed per (season, week); 2,000 resamples of weeks with replacement; the 2.5% / 97.5% quantiles of `Σd / Σn`.

## Known limits

- **Tuesday information only** (like v0). The Saturday injury update re-predicts with the week's report through the QB resolver, not through the injury load.
- **Weather forecast vs actual** (above); **2022 weather readings** half missing (nulls; LightGBM routes them).
- **Snap counts start in 2013**, so the injury load is 0 for 2011–2012 training rows.
- **Benched vs injured** look the same in snap counts; the "regular in the last 3 games" rule limits how long a benched starter counts.

## Versioning

- **Production:** v0 (`game_model.version: v0`). v1's settings live in `settings.yaml` → `game_model.v1`.
- **To promote v1 later** (a ✋ decision, between weeks): set `game_model.version: v1`. The weekly `game` step, the Saturday update and `nfl train game` then fit v1 (`game-model-v1:<season>-w<NN>`, same artifact family `game-model`, model files with `<variant>_importance.csv` next to the coefficients). The player model's game context keeps reading v0's canonical backtest (it was trained on it).
- **Retune** before 2027 (P10) with the 2026 season added to the tuning window.
