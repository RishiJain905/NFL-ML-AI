# Model card: player model, the P08 targets (touchdown, sack and interception chances; QB TDs; coverage for defensive backs)

**Status:** backtested 2019–2025 (P08); a target is **live in the weekly run only once it is listed in `config/settings.yaml` → `player_model.live_targets`** (the default is the 11 P06 targets). All 12 clear the pre-registered ship rule in the walk-forward backtests ([Results](#results-walk-forward-20192025-every-regular-season-week-refit-weekly)); the ship decision is the lead's (P08 phase file). This card is how the models work and how to read them.

Companion docs: the P06 models and the shared machinery are in [player-model-v1](player-model-v1.md) (read its "The math, step by step" first: the baseline, sample weights, quantile and Poisson models, the walk-forward); a plain-language walkthrough is the [player projections guide](../guides/player-projections.md) (§2b); the spec is [documentation/11](../11-prediction-targets.md).

## What it is

Twelve more models, one per target × position group, on the same recipe as P06 (a LightGBM model per target, refit every week of a walk-forward, compared with the player's own rolling baseline):

| Key | Pool | Kind | Predicts | Label (`player_history` column) |
|---|---|---|---|---|
| `pass_tds-qb` | the game's main QB | event count | passing TDs, P(≥1), P(≥2) | `passing_tds` |
| `ints-qb` | main QB | event count | interceptions thrown, P(≥1), P(≥2) | `passing_interceptions` |
| `rush_yds-qb` | main QB | amount | rushing yards (median, 80% range) | `rushing_yards` |
| `td-rb` | RBs with offensive snaps | probability | chance of a rushing or receiving TD | `any_td` |
| `td-wrte` | WRs and TEs with offensive snaps | probability | chance of a receiving or rushing TD | `any_td` |
| `sacks-edge` | DL and LB with defensive snaps | event count | sacks, P(≥1 credited sack), P(≥2) | `def_sacks` (halves) |
| `qb_hits-edge` | DL and LB with defensive snaps | count | QB hits | `def_qb_hits` |
| `cov_tgt-cbs` | CBs and safeties with defensive snaps | count | targets in his coverage (PFR, 2018+, a week late) | `pfr_targets_allowed` |
| `cov_cmp-cbs` | CB/S | count | completions allowed in coverage (PFR) | `pfr_completions_allowed` |
| `cov_yds-cbs` | CB/S | amount | yards allowed in coverage (PFR) | `pfr_yards_allowed` |
| `int-cbs` | CB/S | probability | chance of an interception | `def_int_any` |
| `pd-cbs` | CB/S | probability | chance of a pass defended | `pd_any` |

Where it lives: targets and the prediction-file contract `models/player_schema.py`; the model `models/player_model.py`; runs, scoring and the ship rule `models/player_runs.py`; the CB/S feature family `features/player_coverage.py`; labels `features/player_data.py`; the digest columns `digest/players.py` + `digest/render.py`.

## How it works, in plain language

**Amounts and counts** (`rush_yds-qb`, `qb_hits-edge`, `cov_*`): exactly as in P06. Yards: three quantile models (P10 / P50 / P90) with a conformal shift fitted on the model's own earlier misses; counts: a Poisson model for the mean plus a negative binomial for the range, scored against the baseline's *median* (D65).

**Probability targets** (a TD, an interception, a pass defended): *did it happen in this game, yes or no*. A LightGBM **binary** model outputs a raw probability. A **calibration layer** then fixes any systematic over- or under-confidence: Platt scaling, a logistic regression of what really happened on the raw probability (on the log-odds scale), fitted **only on the model's own earlier walk-forward predictions of the last 3 seasons**, never on the rows it was trained on. Until 1,000 earlier predictions (with at least 40 events and 40 non-events) exist, the raw probability is used. The result is `p_ge1` = `mean` = the chance shown; there is no median and no range (`p10` / `p50` / `p90` are empty). The baseline is the player's own rolling rate of the event, built with the same mix as every P06 baseline (last 4 games + season to date, pulled toward last season, the role average for newcomers), and it is *the* bar: a model is only useful if its stated chances are closer to what happens than "he scores in about 1 game in 4".

**Event counts** (passing TDs, interceptions, sacks) are counts first and chances second. The count model is the P06 recipe. The chance of at least one is the negative binomial's `1 − P(0)` at the model's mean and fitted dispersion, and the chance of at least two is `1 − P(0) − P(1)`. A distribution fitted for the mean, the median and the range isn't exactly right about zero (a QB's TD count is more regular than a Poisson; sacks are lumpier than the negative binomial), so `p_ge1` goes through the same Platt layer as a probability target (fitted on its own earlier raw values); `p_ge2` is left as the negative binomial gives it and capped at `p_ge1`. The **baseline's** chance is the same function at the baseline's mean (same dispersion, same layer), so model and baseline differ only in the mean.

**Half-sacks.** nflverse credits shared sacks as 0.5. "At least one sack" means *any* sack credit (`def_sacks > 0`): the negative binomial's `1 − P(0)` at the mean matches that event (16.6% of EDGE/DL player-games, against 13.9% for a full sack or more), so a half-sack counts as a sack (`Target.event_min = 0.5`).

**Defensive backs.** CB/S rows get one more feature family, `cvg_*`: how many passes the opposing offense throws per game, how deep (air yards per target), how often it completes them and for how many yards per target (last 8 games), the same four numbers for the passes against *his own defense*, and that defense's pressure rate (PFR, a week late), sacks per dropback and pass-defense rating. Everything else a back needs comes from the existing families (his snap share, his own history, the opponent's pass rate, plays per game and pass EPA rating, the expected game script). NGS cushion and separation measure receivers, carry no link to the defender covering them, and P06 found separation barely predicts volume, so they are left out. Safeties are in this pool *and* the LB/S tackles pool; a `pos_s` flag tells the models a safety from a corner.

**The coverage labels** come from PFR, which lists only defenders with a stat: when PFR has published a team's game, a defender with snaps and no row was targeted 0 times (zero-filled, as for pressures); a team-game PFR hasn't published yet stays empty and is neither trained on nor scored. Like pressures these labels arrive a week late, so their features and the training rows lag one more week (`Target.label_lag = 1`).

**Drivers (SHAP).** LightGBM's exact tree SHAP of the main model, top 3 per projection, as in P06. A binary model's contributions are on the log-odds scale; they are converted to probability points with a first-order step, `contribution × p × (1 − p)` (p the shown chance), the same trick P06 uses for counts (`× mean`). Like every SHAP driver here they explain the projection against the *model's average player in the pool*, not against his own baseline.

## The math, step by step

**Platt scaling.** With raw probability `q`, the shown chance is `p = 1 / (1 + exp(−(a · logit(q) + b)))`, `logit(q) = ln(q / (1 − q))`. `a` and `b` are the two numbers a logistic regression of the real 0/1 outcomes on `logit(q)` gives for the model's own earlier predictions (a Newton fit; no regularization needed at 1,000+ rows). `a = 1, b = 0` is "already calibrated"; `a < 1` says the model's raw probabilities were too spread out. The fitted pairs and a worked mapping are in [Calibration](#calibration).

**Choosing the layer.** Platt vs isotonic regression (a monotone step function) vs nothing, chosen on the 2017–2018 burn-in only (for every 2018 week, a calibrator fitted on the earlier weeks' raw predictions): isotonic had the worst Brier and log loss on all five targets that could be evaluated, Platt was within 0.0005 Brier of "no layer" on the four classifiers and better than it on sacks (where the raw negative binomial is biased low). So **Platt**. The raw LightGBM binary probabilities were already close to calibrated; the layer is cheap insurance, not a rescue, for the classifiers, and a real fix for the event counts. The QB event counts (passing TDs, interceptions) can't be evaluated on 2018 alone (a QB target has 32 rows a week, so 1,000 earlier rows only exist from 2019), so they use the same choice.

**Brier score.** The average of `(p − outcome)²` over player-games, outcome 1 or 0. Always saying the base rate `r` scores `r (1 − r)` (the *climatology* score: 0.25 for a coin flip, 0.075 for an event that happens 8% of the time); a model is only skilled where it scores below that. We also compare it with the baseline's chance: `brier_improvement_pct = 100 × (baseline − model) / baseline`.

**ECE (expected calibration error).** Sort the predictions into 10 equal-width probability bins (0–0.1, 0.1–0.2, …); in each bin compare the average stated chance with how often it happened; ECE is the average gap weighted by the bin's share of rows. A perfectly calibrated model has ECE near 0 (with 10,000+ rows, about 0.01 by chance). Because rare events sit in the first one or two equal-width bins, we also report `ece_q10`: the same with ten equal-count bins (deciles of the predicted chance), which shows the shape.

**The ship rule** (pre-registered in the P08 task; `player_runs.ship_rule`; the lead makes the final call):
- amounts and counts: pooled MAE better than the baseline's over 2019–2025 (counts against the baseline's median, D65), better in at least 5 of 7 seasons, 80% range coverage between 0.75 and 0.88;
- event counts (passing TDs, interceptions, sacks): Brier of `p_ge1` below the baseline's, pooled and in at least 5 of 7 seasons; ECE ≤ max(0.02, the baseline's ECE); MAE not worse than the baseline's by more than 0.5%;
- probability targets: the same Brier and ECE conditions.

## Results: walk-forward 2019–2025 (every regular-season week, refit weekly)

Same protocol as P06: each week is predicted by a model fitted only on earlier weeks (2017–2018 are burn-in; the PFR targets start in 2018, so their first reported season, 2019, has one training year), only games the player played are scored, and every number compares the model with the player's rolling baseline **on the same rows**. All 12 targets clear the pre-registered ship rule; the lead makes the final call (see the P08 phase file).

### Amounts, counts and the count side of the event targets (MAE)

For counts the baseline is its negative-binomial **median** (D65). "vs season mean" is the improvement over the plain season-to-date average, a mean, so count targets look better than they are against it (starred): the "Improvement" column is the fair comparison. "No market" is the improvement without the closing-line features (leakage rule 5).

| Model | Player-games | MAE model | MAE baseline | Improvement | vs season mean | 80% range held | Seasons better | No market | W&B (canonical / no market) |
|---|---|---|---|---|---|---|---|---|---|
| `pass_tds-qb` | 3,733 | 0.862 | 0.892 | **+3.4%** | +10.1%\* | 80% | 6 / 7 | +2.5% | `9or7h566` / `t7cit9bf` |
| `ints-qb` | 3,733 | 0.680 | 0.720 | **+5.5%** | +9.9%\* | 84% | 7 / 7 | +6.2% | `ru1xvy5b` / `xbjid57t` |
| `rush_yds-qb` | 3,733 | 11.63 | 11.87 | **+2.0%** | +4.7% | 81% | 7 / 7 | +2.1% | `bailwzq5` / `yzk7w0hb` |
| `sacks-edge` | 45,146 | 0.181 | 0.186 | **+2.6%** | +30.5%\* | 89% | 7 / 7 | +2.6% | `evqs4h3s` / `32ztofmo` |
| `qb_hits-edge` | 45,146 | 0.384 | 0.395 | **+2.7%** | +20.9%\* | 86% | 7 / 7 | +2.5% | `teyf8oee` / `t75b6a2b` |
| `cov_tgt-cbs` | 26,757 | 1.837 | 1.934 | **+5.0%** | +10.3%\* | 80% | 7 / 7 | +4.9% | `setpshvo` / `w9mhlb3n` |
| `cov_cmp-cbs` | 26,757 | 1.331 | 1.393 | **+4.4%** | +10.7%\* | 82% | 7 / 7 | +4.4% | `lletyrqj` / `97fzwl3w` |
| `cov_yds-cbs` | 26,757 | 18.58 | 20.58 | **+9.7%** | +12.4% | 84% | 7 / 7 | +9.7% | `v44u7xoi` / `htxpgfhc` |

By season (improvement %, 2019 → 2025):

| Model | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|
| `pass_tds-qb` | +4.5 | +0.2 | +2.6 | +5.6 | +3.8 | −0.4 | +7.1 |
| `ints-qb` | +0.3 | +2.8 | +9.4 | +7.3 | +0.2 | +8.0 | +9.9 |
| `rush_yds-qb` | +1.2 | +2.2 | +2.6 | +2.2 | +4.3 | +1.3 | +0.2 |
| `sacks-edge` | +2.1 | +2.9 | +2.0 | +1.8 | +3.5 | +3.1 | +2.6 |
| `qb_hits-edge` | +1.0 | +3.0 | +3.9 | +1.5 | +3.8 | +4.0 | +1.7 |
| `cov_tgt-cbs` | +2.8 | +5.3 | +5.5 | +5.7 | +6.9 | +5.6 | +2.9 |
| `cov_cmp-cbs` | +1.8 | +3.9 | +5.4 | +4.7 | +5.7 | +4.8 | +4.3 |
| `cov_yds-cbs` | +10.2 | +7.8 | +10.0 | +9.6 | +11.3 | +9.8 | +9.4 |

The PFR counts' weakest seasons are 2019 (one training year) and 2025 (`cov_tgt` +2.9%). Passing TDs lose in 2024 by 0.4% (the event-count rule only asks that the pooled MAE is not worse by more than 0.5%; its pooled value is +3.4%).

### The chance of at least one (Brier and calibration)

Brier: the average squared miss of the stated chance (0 perfect); **base-rate score**: always saying the event's overall rate, `r (1 − r)`; **ECE**: stated vs real rate over 10 equal-width bins (`ECE q10`: 10 equal-count bins). The baseline for probability targets is his rolling rate of the event; for event counts it is the same function as the model's chance at the baseline's mean.

| Model (event) | Rate / model's average chance | Brier model | Brier baseline | Base-rate score | vs baseline | vs base rate | ECE model / baseline | ECE q10 | Seasons better | No market (Brier vs baseline) |
|---|---|---|---|---|---|---|---|---|---|---|
| `td-rb` (a TD) | 24.5% / 24.6% | **0.1611** | 0.1759 | 0.1851 | +8.4% | +13.0% | 0.009 / 0.067 | 0.010 | 7 / 7 | +8.3% |
| `td-wrte` (a TD) | 15.1% / 15.1% | **0.1156** | 0.1256 | 0.1280 | +8.0% | +9.7% | 0.003 / 0.055 | 0.004 | 7 / 7 | +7.9% |
| `int-cbs` (an interception) | 7.8% / 7.9% | **0.0706** | 0.0771 | 0.0721 | +8.5% | +2.1% | 0.003 / 0.056 | 0.004 | 7 / 7 | +8.5% |
| `pd-cbs` (a pass defended) | 30.7% / 30.5% | **0.1985** | 0.2177 | 0.2128 | +8.8% | +6.7% | 0.005 / 0.096 | 0.007 | 7 / 7 | +8.8% |
| `pass_tds-qb` (≥ 1 passing TD) | 78.3% / 78.1% | **0.1643** | 0.1740 | 0.1701 | +5.5% | +3.4% | 0.016 / 0.050 | 0.010 | 7 / 7 | +4.9% |
| `ints-qb` (≥ 1 interception) | 50.6% / 51.2% | **0.2481** | 0.2548 | 0.2500 | +2.6% | +0.8% | 0.016 / 0.050 | 0.026 | 7 / 7 | +2.4% |
| `sacks-edge` (a credited sack) | 16.7% / 16.9% | **0.1249** | 0.1328 | 0.1393 | +6.0% | +10.3% | 0.003 / 0.052 | 0.004 | 7 / 7 | +5.9% |

By season (Brier improvement over the baseline, %, 2019 → 2025): `td-rb` 7.2, 7.8, 8.5, 10.9, 8.2, 6.8, 9.2; `td-wrte` 8.3, 7.3, 7.0, 9.3, 8.4, 7.1, 8.5; `int-cbs` 8.8, 8.2, 8.2, 8.0, 9.2, 8.0, 9.2; `pd-cbs` 10.1, 8.2, 8.1, 9.1, 8.7, 8.8, 8.7; `pass_tds-qb` 2.7, 1.4, 4.5, 6.3, 4.7, 10.0, 7.6; `ints-qb` 0.9, 3.3, 4.3, 3.0, 1.8, 0.4, 4.5; `sacks-edge` 4.5, 7.3, 5.2, 5.3, 6.2, 7.6, 5.5. Every target beats its baseline in every season.

### The ship rule, applied

| Target | Condition (pre-registered) | Value | Met |
|---|---|---|---|
| amounts / counts (`rush_yds-qb`, `qb_hits-edge`, `cov_*`) | pooled MAE below baseline; ≥ 5 of 7 seasons; 80% range coverage in 0.75–0.88 | MAE better in all 5; seasons 7 / 7 each; coverage 0.805, 0.861, 0.796, 0.815, 0.839 | yes |
| event counts (`pass_tds-qb`, `ints-qb`, `sacks-edge`) | Brier below baseline, pooled and ≥ 5 of 7 seasons; ECE ≤ max(0.02, baseline's); MAE not worse by > 0.5% | Brier 0.1643 / 0.2481 / 0.1249 (baselines 0.1740 / 0.2548 / 0.1328), 7 / 7 seasons each; ECE 0.016 / 0.016 / 0.003 (limit 0.050 / 0.050 / 0.052); MAE +3.4% / +5.5% / +2.6% | yes |
| probability targets (`td-rb`, `td-wrte`, `int-cbs`, `pd-cbs`) | Brier below baseline, pooled and ≥ 5 of 7 seasons; ECE ≤ max(0.02, baseline's) | Brier 0.1611 / 0.1156 / 0.0706 / 0.1985 (baselines 0.1759 / 0.1256 / 0.0771 / 0.2177), 7 / 7 seasons each; ECE 0.009 / 0.003 / 0.003 / 0.005 (limit 0.067 / 0.055 / 0.056 / 0.096) | yes |

The baseline's ECE sets the ECE limit whenever it is above 0.02, and every baseline here is far above it (0.05–0.10): the ECE condition is loose. The tighter evidence is the model's own ECE (0.003–0.016) and the reliability table below.

### The closing-line effect (leakage rule 5)

Historical market lines are closing lines, better informed than a Tuesday run's. Without the market features (`--no-market`) every target still beats its baseline in every season and still clears the ship rule. The market matters for **passing TDs** (the team's market-implied points is a top-3 driver in 91% of 2025 rows): MAE improvement +3.4% → +2.5%, Brier improvement +5.5% → +4.9%. Every other target moves by under 0.3 points in its improvement, except the interceptions' MAE improvement, which is *higher* without the market (+5.5% → +6.2%). The live run uses the current lines, so its true gain sits between the two columns.

## Calibration

**Reliability** (the point of the probability targets: when it says 30%, does it happen 30% of the time?). Deciles of the model's chance over 2019–2025, mean stated chance → how often it happened:

| Target | Lowest decile | Middle (5th, 6th) | Highest decile |
|---|---|---|---|
| `td-rb` | 6.7% → 6.7% | 17.6% → 19.2%, 23.0% → 22.3% | 55.5% → 53.8% |
| `td-wrte` | 3.0% → 2.5% | 10.2% → 9.5%, 13.6% → 13.9% | 38.0% → 39.1% |
| `int-cbs` | 2.7% → 2.7% | 7.0% → 6.9%, 8.2% → 8.5% | 15.0% → 15.4% |
| `pd-cbs` | 11.5% → 12.1% | 29.4% → 28.6%, 32.3% → 31.4% | 50.3% → 50.0% |
| `sacks-edge` | 5.0% → 4.6% | 11.1% → 11.1%, 13.6% → 13.6% | 44.9% → 43.7% |
| `pass_tds-qb` | 62.5% → 63.4% | 78.0% → 81.8%, 80.3% → 80.2% | 90.4% → 89.3% |
| `ints-qb` | 43.2% → 42.8% | 50.8% → 50.4%, 51.7% → 55.8% | 59.3% → 52.0% |

The baseline's deciles are far off the diagonal where it matters: its lowest decile says 0% and the touchdown happened 11% of the time (RBs; 5.8% for WR/TE; 4.9% for interceptions; 6.9% for sacks), and its highest says 67% for RBs against 49% real. The model's interception chances for QBs only span 43–59%, because there is little to separate quarterbacks on (Platt slope 0.44 below).

**The calibration layer.** Platt fits on the three seasons before 2025 (the layer a 2025 week used; `a` = slope, `b` = intercept on the log-odds scale; a map of raw → shown chance):

| Target | Rows fitted on | `a` | `b` | 5% → | 10% → | 30% → | 50% → | 80% → |
|---|---|---|---|---|---|---|---|---|
| `td-rb` | 4,510 | 1.17 | 0.14 | 3.6% | 8.1% | 30.0% | 53.6% | 85.4% |
| `td-wrte` | 12,865 | 1.11 | 0.14 | 4.2% | 9.2% | 31.1% | 53.6% | 84.3% |
| `int-cbs` | 11,699 | 1.06 | 0.10 | 4.7% | 9.8% | 31.2% | 52.6% | 82.7% |
| `pd-cbs` | 11,699 | 1.06 | 0.05 | 4.4% | 9.2% | 30.0% | 51.3% | 82.1% |
| `sacks-edge` (the NB's P(≥ 1)) | 19,781 | 1.04 | 0.17 | 5.3% | 10.8% | 32.9% | 54.2% | 83.4% |
| `pass_tds-qb` (the NB's P(≥ 1)) | 1,630 | 1.13 | 0.12 | 3.9% | 8.6% | 30.2% | 52.9% | 84.3% |
| `ints-qb` (the NB's P(≥ 1)) | 1,630 | **0.44** | 0.01 | 21.7% | 27.8% | 41.0% | 50.2% | 64.9% |

What the layer is worth (before → after, 2019–2025):

| Target | Brier raw → shown | ECE raw → shown |
|---|---|---|
| `td-rb` | 0.1609 → 0.1611 | 0.008 → 0.009 |
| `td-wrte` | 0.1156 → 0.1156 | 0.006 → 0.003 |
| `int-cbs` | 0.0706 → 0.0706 | 0.003 → 0.003 |
| `pd-cbs` | 0.1985 → 0.1985 | 0.008 → 0.005 |
| `sacks-edge` | 0.1251 → 0.1249 | 0.012 → 0.003 |
| `pass_tds-qb` | 0.1649 → 0.1643 | 0.031 → 0.016 |
| `ints-qb` | 0.2486 → 0.2481 | 0.029 → 0.016 |

For the four classifiers the raw LightGBM probabilities were already calibrated: the layer is worth nothing in Brier (a hair worse for `td-rb`) and is kept as cheap insurance against drift. For the event counts it is a real fix: the negative binomial's P(≥ 1) is biased (passing TDs 75% predicted vs 78% seen: a QB's TD count is more regular than a Poisson; sacks 15.5% vs 16.7%) and, for interceptions, far too steep in the mean (`a = 0.44`: a mean of 0.4 and one of 1.0 differ by 20 points in the raw chance but by about 10 once calibrated).

**Which layer** (chosen on 2017–2018 only; Brier of every 2018 week, calibrator fitted on the weeks before it): Platt vs isotonic vs none. `td-rb` 0.1660 / 0.1676 / 0.1655, `td-wrte` 0.1258 / 0.1261 / 0.1256, `int-cbs` 0.0767 / 0.0771 / 0.0767, `pd-cbs` 0.2016 / 0.2022 / 0.2014, `sacks-edge` 0.1261 / 0.1268 / 0.1263. Isotonic is worst everywhere; Platt equals "none" for the classifiers and beats it for sacks, so Platt. The QB event counts couldn't be evaluated on 2018 alone (a QB target adds 32 rows a week, so 1,000 earlier rows exist only from 2019) and reuse the choice.

## Is this number good? (Brier is a weak yardstick, so read it against the base rate)

- **Doc 11's expectation held:** "TD / sack / INT probabilities: small gains in Brier score; calibration is the main goal." Calibration is where the models are strongest (ECE 0.003–0.016 against baselines at 0.05–0.10).
- **Beating the baseline is easier than it looks.** A rolling rate can say "never": 16% of RB rows, 25% of WR/TE rows, 22% of EDGE/DL rows and 34% of CB/S interception rows have a baseline of exactly 0, and the event still happened in 9.7%, 5.5%, 7.1% and 5.1% of them. One such miss costs a full 1.0 in Brier. The honest yardstick is the **base rate**: touchdowns beat it by 13% (RB) and 10% (WR/TE), sacks by 10%, passes defended by 7%, passing TDs by 3%, interceptions by corners and safeties by 2%, and **quarterback interceptions by 0.8%**: given that he plays, whether a quarterback throws at least one interception is close to a coin flip (50.6%), and almost nothing separates one passer from another. The expected interceptions (a count, +5.5% MAE) are more useful than the chance.
- **The chance of at least one is a coarse number for QBs.** Passing TDs happen in 78% of games, so the chance lives between 62% and 90%; the count projection carries more information.
- **Ordering is real.** Rank correlation of the projected and real gap to baseline for the count targets: 0.16 (QB rushing yards) to 0.46 (sacks); football stats stay noisy.

## Where the gain comes from

- **Early season is not the weak spot** (weeks 1–4 / 5–9 / 10+, improvement over the baseline): `td-rb` Brier +9.0 / +7.7 / +8.5%, `td-wrte` +9.4 / +9.0 / +6.7%, `sacks-edge` +6.5 / +6.2 / +5.6%, `pass_tds-qb` +7.5 / +5.3 / +4.8%, `int-cbs` +9.0 / +9.0 / +8.0%, `pd-cbs` +9.2 / +9.8 / +8.1%; MAE: `rush_yds-qb` +4.2 / +1.4 / +1.2%, `cov_yds-cbs` +8.7 / +10.0 / +10.1%. As in P06 the model helps most where the baseline is thin.
- **Corners vs safeties:** coverage targets gain more for corners on targets and completions (`cov_tgt` +5.7% CB, +4.0% S; `cov_cmp` +5.6% / +2.9%), about the same on yards (`cov_yds` +9.2% / +10.4%). Interception chances: Brier +9.2% (CB) and +7.9% (S) over the baseline. Passes defended calibrate a little off by position (corners 35.5% stated vs 36.9% real, safeties 25.9% vs 25.0%): one model with a safety flag.
- **Wide receivers vs tight ends:** touchdown chances calibrate within 0.1 point for both (WR 17.2% stated vs 17.1% real; TE 11.8% vs 11.7%); Brier 0.1282 vs 0.1391 (WR), 0.0950 vs 0.1036 (TE).
- **Coverage features (`cvg_*`) are worth a little.** Without them (same settings, same rows) `cov_tgt` gains +4.8% instead of +5.0%, `cov_cmp` +4.3% vs +4.4%, `cov_yds` +9.72% vs +9.74%, interceptions +8.40% vs +8.51% Brier, passes defended +8.80% vs +8.82%: 0.02–0.16 points, never negative, so they stay. Snap share, position flag and his own recent form carry these models (below).

## What drives the projections

Share of 2025 rows whose top-3 SHAP drivers contain the feature:

| Model | Leading drivers |
|---|---|
| `pass_tds-qb` | the team's market-implied points (91%), his recent form (44%), career games (30%), pass rate over expected (26%) |
| `ints-qb` | how often blitzes face him (43%), aggressiveness (42%), time to throw (26%), the market spread (20%) |
| `rush_yds-qb` | his recent form (96%), last season's average (57%), time to throw (19%) |
| `td-rb` | his share of the team's carries lately (90%), snap share in his last game (62%), carries per game (34%) |
| `td-wrte` | his share of the quarterback's targets (68%), his share of the team's targets (56%), recent form (45%), snap share (43%) |
| `sacks-edge` | recent form (86%), tackles per snap (50%), snap share (45%), last season's average (38%), pressures per snap (29%) |
| `qb_hits-edge` | recent form (90%), snap share (64%), last season (54%), tackles per snap (47%) |
| `cov_tgt-cbs`, `cov_cmp-cbs`, `cov_yds-cbs` | snap share (87–91%), a safety flag (85–91%), his recent form (26–84%); the opposing offense's passes per game in 3–4% of rows |
| `int-cbs` | snap share (77–91%), defensive snaps per game (49%), recent form (27%); the opposing offense's passes per game in 13% |
| `pd-cbs` | a safety flag (83%), snap share (80%), recent form (62%) |

Usage is the story for touchdowns (carry and target share, not efficiency), for coverage (how many snaps he plays and whether he is a corner or a safety) and for sacks (how often he rushes). Interception chances for quarterbacks ride on how aggressively and how often under pressure he plays, which is a weak signal.

## Football sense-check

- **Touchdowns:** the highest 2025 RB chances are Jonathan Taylor (about 70% in three games), Javonte Williams and Jahmyr Gibbs; the highest WR/TE chances are Amon-Ra St. Brown and Puka Nacua (53–55%; four of the top five rows scored). By stated chance for RBs in 2025: 0–10% scored 5.4%, 10–20% 18.2%, 20–30% 27.3%, 30–50% 35.5%, 50%+ 56.3%.
- **Sacks:** the highest chances are the league's pass rushers: Myles Garrett (78% in week 12: 3 sacks), Will Anderson Jr. (77%), Nik Bonitto (77%), Brian Burns, Danielle Hunter. The baseline put Garrett at 87%, higher than the model's 78%: stars are where a rolling rate overshoots.
- **Coverage:** the highest 2025 average chances to defend a pass belong to Pat Surtain II (51%; he did it in 10 of 14 games), Mike Jackson, Nate Wiggins, Quinyon Mitchell and Renardo Green: corners who are targeted a lot and good at it. The highest interception chances are safeties (Xavier McKinney 14.6%, Calen Bullock 13.4%) and Marlon Humphrey (12.8%; he intercepted in 27% of his games).
- **Quarterbacks:** the highest expected passing TDs are Jared Goff, Matthew Stafford, Patrick Mahomes and Lamar Jackson (1.8–2.0); the lowest are young passers (Bryce Young 1.2, Jaxson Dart 1.1, Cam Ward 1.0). Expected interceptions run from 0.4 (Lamar Jackson) to 0.8 (Geno Smith, Trevor Lawrence, Tua Tagovailoa).

## Settings (tuned on 2017–2018 only)

A 12-point grid per target scored by a walk-forward over every other week of 2017–2018 (never the reported weeks), with learning rate 0.05, subsampling 0.8, L2 1.0. Amounts and counts use the P06 grid (`num_leaves` 7 / 15 / 31, `min_data_in_leaf` 50 / 200, `n_estimators` 150 / 300; score MAE); probability targets use a grid built for rare events (`num_leaves` 4 / 8 / 16, `min_data_in_leaf` 100 / 300, `n_estimators` 100 / 250; score Brier). The landscape is flat (best to worst 0.8–7.4%), so the choice matters little.

| Model | Leaves | Rows per leaf | Trees | Sweep | Tuning rows | Best-to-worst |
|---|---|---|---|---|---|---|
| `pass_tds-qb` | 15 | 200 | 150 | `ct2dr946` | 540 | 4.7% |
| `ints-qb` | 7 | 200 | 300 | `pus3fn5t` | 540 | 7.4% |
| `rush_yds-qb` | 15 | 50 | 300 | `ud7xp3xt` | 540 | 0.8% |
| `td-rb` | 4 | 300 | 100 | `zv4j4md1` | 1,464 | 6.1% |
| `td-wrte` | 4 | 100 | 100 | `coyymj3b` | 4,072 | 2.9% |
| `sacks-edge` | 31 | 50 | 150 | `1wdr6q3m` | 6,397 | 1.5% |
| `qb_hits-edge` | 15 | 50 | 300 | `bu4pv1s8` | 6,397 | 1.1% |
| `cov_tgt-cbs` | 7 | 50 | 300 | `zgwo3kuu` | 671 | 2.8% |
| `cov_cmp-cbs` | 15 | 200 | 150 | `smgtm7m6` | 671 | 3.5% |
| `cov_yds-cbs` | 7 | 200 | 150 | `y4yt569d` | 671 | 2.0% |
| `int-cbs` | 4 | 300 | 100 | `eweebi9l` | 3,799 | 2.1% |
| `pd-cbs` | 4 | 300 | 100 | `9figg6pe` | 3,799 | 1.7% |

They live in `player_runs.TARGET_DEFAULTS` (`config/settings.yaml` → `player_model.per_target` wins when set). Last tuned 2026-10-04; fixed for the season. The three PFR targets were tuned on only the last ~7 weeks of 2018 (671 player-games: no earlier training year) and the QB targets on 540 rows, so their picks are barely better than a guess; a first untuned round (global settings, run before tuning) scored `cov_tgt` +5.1%, `cov_cmp` +5.0%, `cov_yds` +9.4%: the tuned `cov_cmp` setting is 0.6 points worse than the untuned one on the reported seasons, which is not a reason to change it (never tune on reported weeks).

## How the 12 models were built (rounds)

1. **Data and probes.** Labels (`any_td`, `def_int_any`, `pd_any`, `pfr_completions_allowed`) and the half-sack decision from the real data (above). PFR coverage rows: 97–99% of corners with at least half the snaps have a PFR row; the missing ones are mostly low-snap players, so zero-fill is right.
2. **First round, untuned and with the raw negative binomial for event counts** (global settings): every target already beat its baseline in 5 or more seasons on its measure, but the event counts' chances were biased (passing TDs ECE 0.043, interceptions 0.055).
3. **Second round, calibrated and tuned:** a Platt layer for the event counts (ECE 0.016, 0.016, 0.003), the 12 sweeps, the layer choice on 2017–2018, then the canonical backtests and the `no_market` variants (24 W&B runs), run in four parallel processes.
4. **Regression checks against P06** (below), a live-path rehearsal for week 4 of 2026 (twelve targets, about 2,600 projections, every output in a scratch folder), and the CB/S feature ablation.

## Code review and verification

- **No P06 target changes.** (1) The rebuilt feature table has CB rows and the `cvg_*` family on top of the old one; every P06 row and column equals the saved P06 table (203,088 rows × 126 features; the six ripple columns differ by at most 1.1e-16: float accumulation order, which was non-deterministic between two identical P06 builds and is now fixed by sorting before summing). (2) A weekly run of the P06 targets builds only the P06 rows and families (`build_player_data(targets=...)`). (3) **A/B of the weekly step:** the 11 P06 targets for 2026 week 4 (clock pinned before kickoff, outputs in a scratch folder), HEAD code vs this code: 1,972 projections, every column identical, including the SHAP drivers, feature hashes and confidence; only the three new columns are added (null). (4) **P06 backtests re-run** on the rebuilt table (no W&B, nothing saved): 10 of 11 summaries bit-identical to the saved ones; `receptions-rb` moves in the fourth decimal (MAE 1.0903 → 1.0901, improvement +2.73% → +2.75%), and on the old saved table the same code reproduces it exactly, so the only cause is the 1e-16 ripple noise above.
- **Leakage:** `tests/test_player_p08.py` holds a future-invariance test for `cvg_*` on the synthetic league at three cuts (with teeth), the PFR week-late rule for the pressure-rate feature, a naive re-implementation of every `cvg_*` column, and a test that a week's calibration can't see that week's outcomes. A truncation check on the real data (every game from week T on dropped, 2022 w14 / 2024 w10 / 2025 w6): no `cvg_*` feature of any earlier row moved (167,000–211,000 rows each).
- **Scoring paths:** `score_predictions` on every new kind, `scoreboard_rows` for probability targets and event counts, a probability target's empty MAE columns, the ship rule's every branch, and the digest's table columns are tested; `ops.drift.prepare_scoreboard` already ignores rows without an MAE.
## Reading the W&B charts

All runs are in W&B group **`track1-player`**, tagged `p08`, `group:<qb|rb|wrte|edge|cbs>` and `target:<name>`; the canonical backtests also carry `agent-run`. The research variants are named `backtest-<key>_nomarket` and have config `market_features: false` (`run_backtest` now adds the `no-market` tag itself; the 12 runs behind this card were launched before that and carry only `agent-run`, so find them by name). (A sweep per target is `tune-<key>`; the weekly fit and scoreboard runs are as in [player-model-v1](player-model-v1.md#reading-the-wb-charts).)

### The metrics in one minute

| Metric | What it measures | Better | Our values |
|---|---|---|---|
| **Brier** (`brier_model`) | Average of (stated chance − what happened)² over player-games; 0 is perfect | Lower | 0.0706 (an 8% event) to 0.2481 (a coin flip) |
| **Brier vs the baseline** (`brier_improvement_pct`) | 100 × (baseline − model) / baseline | Higher; > 0 = beats the baseline | +2.6% to +8.8% |
| **Brier of the base rate** (`brier_climatology`) | What "always say the average rate" scores: `r (1 − r)` | The model should sit below | 0.0721 / 0.2500, the model 0.0706 / 0.2481 |
| **ECE** (`ece_model`, `ece_baseline`, `ece_q10_model`) | Gap between stated chance and real rate, averaged over 10 bins (equal-width; `ece_q10`: equal-count) | Lower; near 0 | 0.003–0.016 (baselines 0.05–0.10) |
| **`brier_raw`, `ece_raw`** | The same before the calibration layer | The layer is worth `brier_raw − brier_model` | 0.0002 or less for classifiers; ECE 0.031 → 0.016 for passing TDs |
| **`event_rate`, `event_mean_p`** | How often the event happened; the model's average stated chance | Equal | 24.5% vs 24.6% (RB touchdowns) |
| **`baseline_zero_share`**, **`baseline_zero_event_rate`** | Share of rows where the baseline says "never" (a rolling rate below 0.5%), and how often those rows still had the event | — | RB touchdowns: 16% of rows, 9.7% scored |
| **`ship_*`** | One 1.0 / 0.0 per ship-rule condition and `ship_pass` | 1.0 | all 1.0 |

Amounts and counts keep the P06 metrics (MAE, improvement, coverage, Spearman).

### Backtest runs (`backtest-<key>`, `backtest-<key>_nomarket`; job type `backtest`)

The x-axis of every `bt/*` curve is `bt/step`, the count of reported weeks so far (1 = 2019 week 1, about 124 = 2025 week 18).

| Chart | Which runs | What it shows | How to read it |
|---|---|---|---|
| `bt/cum_brier_model`, `bt/cum_brier_baseline` | probability targets, event counts | Brier of every row so far | **The main chart.** The model line should end below the baseline's, and below the base-rate score |
| `bt/brier_model`, `bt/brier_baseline` | same | That week's Brier | Jumpy (a few hundred rows, a handful of events); the model line is usually below |
| `bt/cum_brier_improvement_pct` | same | Cumulative improvement over the baseline | Settles within a few weeks |
| `bt/cum_mae_model`, `bt/cum_improvement_pct`, `bt/cum_coverage_80` and the weekly versions | amounts, counts, event counts | as in P06 | as in P06 |
| `bt/range_param` | all | amounts: conformal shift; counts: NB dispersion r; probability targets: the Platt slope `a` (1 = the raw probability was already right; below 1 = it was too spread out); absent while the calibration layer is not yet fitted | Should drift slowly |
| `lgb/curve_<season>` (web only) | all | Training vs validation loss per boosting round for one season's opening fit (binary log loss for probability targets) | Validation should flatten without turning up |
| `reliability_diagram` (web only) | probability targets, event counts | Deciles of the model's chance: actual rate vs predicted chance, with the perfect diagonal | **Points on the diagonal = calibrated.** Read the extreme deciles: they are the picks that get shown |
| `reliability_table` | same | The same 10 bins for the model and for the rolling baseline | The baseline's bins are far off the diagonal where it says 0% or 100% |
| `brier_by_season`, `mae_by_season` | per kind | model vs baseline by season | The model line below the baseline in at least 5 of 7 seasons |

**End-of-run panels:** `by_season`, `accuracy_scoreboard` (one row per week: `brier_model`, `brier_baseline`, `calibration_ece` for the event kinds), `feature_importance`, `shap_summary` (mean |SHAP| in 2025, in probability points for probability targets), `predictions` (2025 rows, with `p_ge1`).

**Summary keys:** the P06 ones for amounts and counts; for probability targets and event counts also `brier_model`, `brier_baseline`, `brier_improvement_pct`, `brier_climatology`, `brier_raw`, `ece_model`, `ece_baseline`, `ece_raw`, `ece_q10_model`, `ece_q10_baseline`, `log_loss_model`, `event_n`, `event_rate`, `event_mean_p`, `event_mean_p_baseline`, `event_mean_p_raw`, `baseline_zero_share`, `baseline_zero_event_rate`, `seasons_beating_brier` (and `seasons_beating_baseline`, on Brier for probability targets), then the `ship_*` keys.

### Sweeps (`tune-<key>`; job type `tune`)

One run per grid point. Probability targets: summary `tune/brier_model` (the objective), `tune/brier_baseline`, `tune/improvement_pct`, `tune/n`, on their own grid (`TUNE_GRID_PROB`); the others `tune/mae_model` as in P06.

## Outputs

The same `predictions_players.parquet` as P06 (see [player-model-v1](player-model-v1.md#outputs-predictions_playersparquet)), with three more columns: `p_ge1` (probability targets: the chance; event counts: the chance of at least one; null otherwise), `p_ge2` (event counts: at least two) and `baseline_p_ge1` (the baseline's chance, the Brier bar). A probability target's row has `p10` / `p50` / `p90` / `baseline_p50` empty and `mean` = `p_ge1`. The scoreboard gains no columns (it already had `brier_model`, `brier_baseline`, `calibration_ece`); a probability target's MAE, improvement and coverage cells are empty. Backtest folders also keep `_p_raw` (the raw probability, what the calibration layer is fitted on) so a weekly refit can continue from them.

In the digest, only three numbers reach the tables, as extra columns that appear only when a shipped target projects some pick on that side: **TD chance** (Offense: RB and WR/TE picks' chance of a touchdown; a QB pick shows his expected passing TDs, e.g. "1.6 passing TDs") and **Sack chance** (Defense: EDGE/DL picks). They are kept out of the saved payload and the LLM's input (`WatchItem` fields with `exclude=True`), so the prose can't quote them and the fact checks have nothing to bind. A week with only P06 projections renders exactly the P06 tables.

## Known biases and limits

- **Only games he played.** As in P06: no availability model, so "chance of a touchdown" is *given he plays*.
- **Touchdowns are rushing and receiving TDs.** Passing, return and defensive TDs don't count; a QB's rushing TD isn't projected (no QB `td` model).
- **The baseline can say "never".** A rolling rate of exactly 0 (no event in his last several games and last season) gives a 0% chance; those rows are the baseline's main weakness (see "Is this number good?"). The comparison with the base rate (`brier_climatology`) is the harsher test.
- **Sacks are credits, halves included.** Two half-sacks in a game make 1.0.
- **Coverage stats are PFR's** (2018+, one week late): the models start with 2018 as their only training year, so 2019 is the thinnest season (see Results), and a team-game PFR didn't publish is neither trained on nor scored. a few team-games have no PFR rows (2023 week 12, 2024 weeks 13 and 17, 2025 week 13: two or three teams each).
- **The tuning window for the PFR targets is tiny** (late 2018 only: 671 player-games), so their tuned settings are barely better than a guess; the untuned defaults scored a little *better* on the reported seasons, which is not a reason to change them (never tune on reported weeks).
- **CB and safety share one model** with a safety flag; slot corners, nickel packages and shadow coverage aren't visible, only snaps, team context and his own history.
- **Calibration of rare events** is only as good as the history behind it: a probability target fits its Platt layer on the last 3 seasons (RB about 4,500 rows, WR/TE 12,900, CB/S 11,700, EDGE 19,800), a QB event count on about 1,600 rows.
- **SHAP drivers** explain the projection against the average player in the pool, not his own baseline, and for probability targets are a first-order probability-point conversion.
- **No consistency layer yet:** passing TDs aren't tied to receivers' TD chances, and a team's TD chances don't sum to its scoring.

## Versioning

- Artifact and version strings are the P06 ones (`player-model` artifact, `player-model-v1:<season>-w<NN>`): a P08 target joins the weekly fit by being listed in `player_model.live_targets`, and its boosters are saved next to the others.
- Per-target tree settings: `player_runs.TARGET_DEFAULTS` (tuned on 2017–2018); `config/settings.yaml` → `player_model.per_target` wins when set. Calibration: `player_model.calibration` (`platt`), `calibration_seasons` (3).
- Code: `models/player_schema.py`, `models/player_model.py`, `models/player_runs.py`, `features/player_coverage.py`, `features/player_data.py`.
