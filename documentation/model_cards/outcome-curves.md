# Model card: outcome curves (FL01)

For each player projection the player model publishes, the **whole curve behind it**: the chance of reaching any whole number and of landing in any band. Ten stats. Built in FL01 (2026-11-14); the W&B artifact `curves-model` (its own `production` alias), version **`2021-2025_20261114T223208Z`**. Spec: [FL01 phase file](../forecast-lab/FL01-outcome-curves.md) (it holds the probe, the tuning table and the pre-registered addendum), [Forecast lab README](../forecast-lab/README.md); plain-language guide: [guides/forecast-lab.md §4](../guides/forecast-lab.md#4-outcome-curves-fl01); decision D158; the W&B runs in [W&B guide §6.15](../guides/weights-and-biases.md#615-the-forecast-lab-group-forecast-lab-fl00-fl01). The sibling model is [availability and role](availability-and-role.md).

## In one paragraph

The player model publishes a median (P50) and an 80% range (P10 to P90) for yardage, and a mean for counts. To turn that into a whole distribution without assuming a bell curve, the model finds the past walk-forward projections of the same stat that looked most like the new one (the **analogs**), standardizes where their results landed (z = (actual - P50) / (P90 - P10)) and rescales those misses to the new projection; counts use a negative binomial at the published mean with its spread fitted on the analogs. In a walk-forward backtest over 2021-2025 (settings chosen on 2019-2020 only) all ten stats beat a bell curve (a Poisson for counts), the player's own last games and passed a band check, so **all ten ship**. The gain over the bell curve is real but small, except where the bell curve is the wrong shape: carries +5.4%, receiving yards +4.6% on the whole-curve score (CRPS). **Passing yards ships by the letter of its rule and no more: 0.10% better, in 4 of 5 seasons.** Two things the curve is not: it does **not** make ranges tighter (a band of plus or minus 7 yards holds about 1 game in 9 for a typical receiving projection), and it is **"if he plays"** (the chance he plays is the availability model's).

## The target

| Item | Value |
|---|---|
| Grain | One row per player x game x stat for each shipped stat (a curve), from the week's `predictions_players.parquet` |
| Stats | Passing yards (QB), rushing yards (QB), rushing yards (RB), scrimmage yards (RB), receiving yards (WR, TE), receptions (WR, TE), targets (WR, TE), receptions (RB), carries (RB), tackles (LB, S): the player model's own keys (`pass_yds-qb`, `rush_yds-qb`, `rush_yds-rb`, `scrim_yds-rb`, `rec_yds-wrte`, `receptions-wrte`, `targets-wrte`, `receptions-rb`, `carries-rb`, `tackles-lbs`) |
| Scored on | Rows where the player played (the player model's backtest only has those): a curve is "if he plays" |
| History | The player model's walk-forward predictions 2017-2025 (`runs/backtests/player/<stat>/`) plus each later season's `player_walkforward.parquet`; a week's curves use rows **strictly before the week** |
| Windows | History only 2017-2018 (the first analogs); tuning 2019-2020 (the number of analogs, what "similar" means, how far back history goes); reported 2021-2025 |
| The model is its settings | A curve is built at run time from the history, so `models/forecastlab/curves/<version>/params.json` holds only the per-stat settings, the grid bounds, the shipped list and the backtest verdicts |

## What it reads, and what it never touches

- **Reads:** the player model's published projection file for the week; for history the player model's backtest predictions and each season's walk-forward file; for grading the production scoreboard's `predictions_players_scored.parquet` (every projected player, `played` false for a player who didn't play), else the season's walk-forward file (a game with rows there is done, so a projected player missing from it didn't play).
- **Never changes** any feature, setting, training run, artifact or alias of the player model (D107). It copies P10, P50, P90 and the mean into its own file.
- **Writes:** only under `forecastlab/` and `models/forecastlab/curves/`.
- **Never enters the digest.**

## The method

**Amounts (yards).** For a projection with median P50 and range P10 to P90, width w = max(P90 - P10, 5):

1. Take the K nearest analogs among earlier projections of the same stat that have a result. "Nearest" is the projection's size (P50), or its size and width (P50, w) standardized.
2. Each analog's standardized miss is z = (actual - P50) / w.
3. The curve is the empirical distribution of `round(P50 + z x w)` over the analogs, on whole numbers, clipped at the stat's floor.
4. **`analog_zero`** (the addendum below): an analog whose actual is exactly 0 stays at 0 (an atom, never smeared onto 1 or -1); an analog with a negative actual keeps its own value (clipped at the floor); an analog with a positive actual maps as above but never below 1. So the mass at 0 is exactly the analogs' zero share.

**Counts.** A **negative binomial** at the published mean mu, variance mu + alpha x mu^2, with alpha fit by the method of moments on the nearest K analogs (`nb`); `nb_cal` also scales the mean by what those analogs did relative to their means (clipped to 0.25-4). Standardized misses can't be used for counts: they are small whole numbers, and the analogs' raw outcomes (`analog`) pile onto whatever the nearest rows did (at K = 4,000 a raw-outcome carries curve is 42% worse than the Poisson), so `analog` is only a tuning candidate.

With fewer than 100 analogs the curve is the bell curve (`n_analogs` 0). Every curve lives on whole numbers from the stat's floor to a fixed top (`GRID_HI`: 700 passing yards, 350 receiving yards, 40 tackles, ...); mass above lands on the last value. The stored `cdf` list stops at the first value that reaches 1.0.

**The settings** (chosen on the tuning window; `curves_runs.CHOSEN`; history = every earlier season, width floor 5 yards):

| Stat | Method | K | "Similar" means |
|---|---|---|---|
| Passing yards (QB) | `analog_zero` | 1,000 | size and width |
| Rushing yards (QB) | `analog_zero` | 500 | size and width |
| Rushing yards (RB) | `analog_zero` | 500 | size and width |
| Scrimmage yards (RB) | `analog_zero` | 500 | size and width |
| Receiving yards (WR, TE) | `analog_zero` | 1,000 | size |
| Receptions (WR, TE) | `nb` | 2,000 | size |
| Targets (WR, TE) | `nb_cal` | 1,000 | size |
| Receptions (RB) | `nb_cal` | 1,000 | size |
| Carries (RB) | `nb` | 500 | size |
| Tackles (LB, S) | `nb_cal` | 1,000 | size |

**Baselines** (on the same whole-number grid and the same rows):

| Baseline | What it is |
|---|---|
| **Bell curve** | A normal distribution centred on P50 with sigma = (P90 - P10) / 2.563 (amounts; all mass below the floor lands on the floor); for counts a Poisson at the mean. What you'd compute by hand |
| **Own history** | The player's own last 16 results at most, at least 6 (otherwise the bell curve): about 89% of the reported rows have at least 6 |

**The scores** (all on the whole-number grid): **CRPS** = the sum over every whole number k of (F(k) - 1[y <= k])^2, in the stat's own unit; the mean **Brier score of the chance of "more than a mark"** over the stat's standard marks (below); the chance of landing in the **band** around the projection (centred on round(P50), or round(mean) for counts) against how often it happened; the **PIT** calibration error (the expected histogram of the randomized probability integral transform in 10 bins, the mean absolute deviation from 10%: 0 is perfect). A diagnostic added for the addendum: the Brier score of "1 or more yards".

**The standard marks and bands** (`schema.CURVE_TARGETS`):

| Stat | Marks | Band |
|---|---|---|
| Passing yards (QB) | 174.5, 199.5, 224.5, 249.5, 274.5, 299.5 | plus or minus 15 yards |
| Rushing yards (QB) | 9.5, 19.5, 29.5, 39.5, 49.5 | plus or minus 7 |
| Rushing yards (RB) | 24.5, 39.5, 49.5, 59.5, 74.5, 99.5 | plus or minus 7 |
| Scrimmage yards (RB) | 24.5, 49.5, 74.5, 99.5, 124.5 | plus or minus 7 |
| Receiving yards (WR, TE) | 14.5, 24.5, 39.5, 49.5, 59.5, 74.5, 99.5 | plus or minus 7 |
| Receptions (WR, TE) | 1.5, 2.5, 3.5, 4.5, 5.5, 6.5 | plus or minus 1 |
| Targets (WR, TE) | 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5 | plus or minus 1 |
| Receptions (RB) | 0.5, 1.5, 2.5, 3.5, 4.5 | plus or minus 1 |
| Carries (RB) | 4.5, 7.5, 9.5, 12.5, 14.5, 17.5 | plus or minus 2 |
| Tackles (LB, S) | 2.5, 3.5, 4.5, 5.5, 6.5, 7.5 | plus or minus 1 |

## The probe (before building; receiving yards, 2019-2025, 29,266 rows)

The published 80% range covers 81.9%. The misses are not bell-shaped, and their shape depends on the size of the projection. With z as above, a bell curve would put the 10th and 90th percentiles at -0.50 and +0.50 everywhere:

| Projection (P50) | Rows | Share with 0 yards or fewer | z at 10% | z at 50% | z at 90% | z at 95% |
|---|---|---|---|---|---|---|
| up to 10 | 13,192 | 60% | -0.14 | 0.00 | +0.95 | +1.45 |
| 10-20 | 4,140 | 25% | -0.30 | -0.02 | +0.72 | +0.98 |
| 20-35 | 4,639 | 11% | -0.36 | 0.00 | +0.65 | +0.87 |
| 35-50 | 3,600 | 6% | -0.42 | -0.01 | +0.64 | +0.84 |
| 50-70 | 2,988 | 2% | -0.42 | 0.00 | +0.61 | +0.83 |
| over 70 | 707 | 1% | -0.41 | +0.02 | +0.59 | +0.77 |

The median is right at every size (z at 50% is about 0): the curve keeps the published projection as its centre. A re-check by tight end or wide receiver, the confidence label, `role_ok`, `role_change`, home or away and fewer than 6 games of history, each inside the sizes above, found the quantiles agree within the sampling noise (the 10th / 90th percentile of z for a 35-50 yard projection: TE -0.42 / +0.61, WR -0.42 / +0.66): the shape depends on the size of the projection, not on who the player is, so the similarity space has no role columns.

## Tuning (2019-2020, history 2017-2018 first)

16 candidates for an amount (K in 500 / 1,000 / 2,000 / 4,000; similarity on P50 or on (P50, width); history from every earlier season or the last 4) and 32 for a count (the same K and history windows for `nb`, for `nb_cal`, and for the analogs' raw results on the mean or on (mean, width)). CRPS pooled over the rows; the best of each (W&B tuning run `6hpvwrnb`; `backtests/curves/_tuning/summary.json`):

| Stat | Rows | Bell | Own | Chosen | CRPS | vs bell | K 500 / 1000 / 2000 / 4000 (vs bell, same form) |
|---|---|---|---|---|---|---|---|
| Passing yards (QB) | 1,019 | 42.196 | 44.244 | analogs, K 1000, (P50, width) | 42.285 | -0.21% | -0.22 / -0.21 / -0.28 / -0.28 |
| Rushing yards (QB) | 1,019 | 8.123 | 7.920 | analogs, K 500, (P50, width) | 7.933 | +2.34% | +2.34 / +2.07 / +1.90 / +1.90 |
| Rushing yards (RB) | 2,897 | 14.275 | 15.030 | analogs, K 500, (P50, width) | 13.869 | +2.84% | +2.84 / +2.76 / +2.52 / +2.01 |
| Scrimmage yards (RB) | 2,897 | 17.826 | 18.988 | analogs, K 500, (P50, width) | 17.478 | +1.95% | +1.95 / +1.80 / +1.65 / +1.32 |
| Receiving yards (WR, TE) | 7,795 | 13.352 | 13.783 | analogs, K 1000, P50 | 12.769 | +4.36% | +4.28 / +4.36 / +4.30 / +3.92 |
| Receptions (WR, TE) | 7,795 | 0.8970 | 0.9629 | `nb`, K 2000 | 0.8886 | +0.93% | +0.91 / +0.92 / +0.93 / +0.93 |
| Targets (WR, TE) | 7,795 | 1.2317 | 1.3337 | `nb_cal`, K 1000 | 1.2111 | +1.68% | +1.59 / +1.68 / +1.67 / +1.63 |
| Receptions (RB) | 2,897 | 0.8180 | 0.8676 | `nb_cal`, K 1000 | 0.8087 | +1.14% | +1.10 / +1.14 / +1.09 / +0.91 |
| Carries (RB) | 2,897 | 2.5227 | 2.7550 | `nb`, K 500 | 2.3841 | +5.49% | +5.49 / +5.47 / +5.20 / +3.58 |
| Tackles (LB, S) | 9,481 | 1.2708 | 1.3640 | `nb_cal`, K 1000 | 1.2538 | +1.34% | +1.31 / +1.34 / +1.34 / +1.31 |

- **The choice of K is flat:** for most stats the top candidates sit within 0.1% of each other. The choice is the lowest CRPS, with no judgment on top. For counts a negative binomial beats the analogs' raw results, and `nb` against `nb_cal` is a wash (0.0-0.1%).
- **"All earlier seasons" against "the last 4" can't be told apart on this window.** With history from 2017 and tuning in 2019-2020, "the last 4 seasons" reaches back past the first season for every row, so both give identical numbers for every candidate (checked on all of them). The default is every earlier season (more analogs, nothing to tune). The question becomes real from 2022 on: **revisit it at the pre-season retune, on a window that can show it.**
- **Tried on the tuning window and dropped (no gain):** Gaussian smoothing of the curve (0.03-0.10 of its width): 0 to -0.2%; a blend with a normal curve fit to the analogs' mean and spread of z (weights 0.25-0.75; the pure normal fit on passing yards: -0.04% against the bell curve); the width floor (1, 2, 5, 10, 20 yards: within 0.05% of each other except 20, about 0.1% worse). The code keeps only the width floor (5 yards).
- **Passing yards is the one stat with nothing to gain:** the published P10-P90 range of a quarterback is already well calibrated, so a bell curve through it is close to the right shape (tuning window -0.2%).
- **A tuning window usually flatters a model** (the P08 lesson). Here the reported gains match the tuning window's: carries +5.5% (tuning) against +5.4% (reported), receiving yards +4.4% against +4.6%, receptions of WR and TE +0.9% against +0.9%, passing yards -0.2% against +0.1%.

## The pre-registered ship rule and its verdict

Written 2026-11-14, before any reported run. Each stat ships on its own; a stat that misses is not shown in the tab and goes to Rishi. On 2021-2025, rows where the player played, the model and the baselines on exactly the same rows:

1. **Closer than the bell curve:** CRPS below the bell curve's pooled **and** in at least 4 of 5 seasons.
2. **Closer than his own history:** CRPS below the own-history baseline's pooled.
3. **Thresholds:** the mean Brier score over the stat's standard marks below the bell curve's pooled.
4. **Honest bands:** the predicted chance of landing in the band around the projection within 2 percentage points of how often it happened, pooled.

**Verdict: all ten stats ship**, in the first reported run and again after the addendum's re-run of the five amount stats. Passing yards passes rule 1 by 0.10% and 4 of 5 seasons (2023 lost), and rule 3 by 0.00002 (0.18890 against 0.18893).

## Results: walk-forward 2021-2025

Final numbers, after the addendum. The five amount stats are from the re-run (W&B **`6glg980f`**, version `2021-2025_20261114T223208Z`); the five count stats from the first reported run (W&B **`rai6vnyq`**, version `2021-2025_20261114T220939Z`, which stays in the record and whose count parameters, verdicts and numbers are unchanged). Source: `backtests/curves/<stat>/summary.json`. CRPS is in the stat's own unit; lower is better. Brier is the mean over the stat's marks.

| Stat | Rows | CRPS: curve | bell | own games | Gain vs bell | vs own | Seasons won vs bell | Brier at the marks: curve / bell | Band: said / happened (bell said) | PIT error: curve / bell | Ships |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Passing yards (QB) | 2,714 | 39.290 | 39.329 | 41.826 | **+0.10%** | +6.1% | **4 of 5** | 0.18890 / 0.18893 | 17.4% / 17.2% (17.3%) | 0.0057 / 0.0060 | yes, by a hair |
| Rushing yards (QB) | 2,714 | 8.410 | 8.660 | 8.574 | +2.90% | +1.9% | 5 of 5 | 0.12861 / 0.13041 | 48.2% / 48.1% (48.8%) | 0.0038 / 0.0288 | yes |
| Rushing yards (RB) | 7,485 | 13.585 | 13.991 | 14.821 | +2.90% | +8.3% | 5 of 5 | 0.11862 / 0.12015 | 35.1% / 36.1% (33.2%) | 0.0024 / 0.0327 | yes |
| Scrimmage yards (RB) | 7,485 | 16.464 | 16.846 | 18.043 | +2.27% | +8.8% | 5 of 5 | 0.11284 / 0.11399 | 26.8% / 27.2% (25.4%) | 0.0026 / 0.0292 | yes |
| Receiving yards (WR, TE) | 21,471 | 11.454 | 12.010 | 12.346 | +4.64% | +7.2% | 5 of 5 | 0.10497 / 0.10643 | 45.1% / 44.6% (41.0%) | 0.0021 / 0.0455 | yes |
| Receptions (WR, TE) | 21,471 | 0.8281 | 0.8358 | 0.8989 | +0.92% | +7.9% | 5 of 5 | 0.10111 / 0.10156 | 70.7% / 70.7% (75.6%) | 0.0022 / 0.0140 | yes |
| Targets (WR, TE) | 21,471 | 1.1093 | 1.1245 | 1.2265 | +1.35% | +9.6% | 5 of 5 | 0.10142 / 0.10233 | 59.9% / 60.6% (67.8%) | 0.0028 / 0.0197 | yes |
| Receptions (RB) | 7,485 | 0.7536 | 0.7604 | 0.8087 | +0.90% | +6.8% | 5 of 5 | 0.13484 / 0.13612 | 73.8% / 74.7% (80.3%) | 0.0023 / 0.0152 | yes |
| Carries (RB) | 7,485 | 2.3859 | 2.5225 | 2.7425 | **+5.41%** | +13.0% | 5 of 5 | 0.11341 / 0.11831 | 47.6% / 48.9% (68.7%) | 0.0055 / 0.0486 | yes |
| Tackles (LB, S) | 24,468 | 1.2750 | 1.2915 | 1.3965 | +1.28% | +8.7% | 5 of 5 | 0.14182 / 0.14284 | 50.0% / 51.3% (59.3%) | 0.0028 / 0.0199 | yes |

**By season: CRPS of the curve / the bell curve** (2021, 2022, 2023, 2024, 2025):

| Stat | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|
| Passing yards (QB) | 40.998 / 41.027 | 37.653 / 37.731 | **40.134 / 40.112** | 39.186 / 39.232 | 38.485 / 38.547 |
| Rushing yards (QB) | 7.888 / 8.122 | 8.545 / 8.838 | 8.119 / 8.306 | 9.285 / 9.612 | 8.209 / 8.420 |
| Rushing yards (RB) | 13.855 / 14.312 | 13.665 / 14.108 | 12.984 / 13.311 | 13.695 / 14.113 | 13.719 / 14.099 |
| Scrimmage yards (RB) | 17.256 / 17.709 | 16.789 / 17.177 | 15.605 / 15.954 | 16.244 / 16.599 | 16.407 / 16.772 |
| Receiving yards (WR, TE) | 11.750 / 12.323 | 11.705 / 12.288 | 11.418 / 11.987 | 11.297 / 11.829 | 11.098 / 11.627 |
| Receptions (WR, TE) | 0.841 / 0.850 | 0.848 / 0.857 | 0.819 / 0.825 | 0.834 / 0.842 | 0.799 / 0.805 |
| Targets (WR, TE) | 1.146 / 1.167 | 1.129 / 1.146 | 1.089 / 1.099 | 1.109 / 1.125 | 1.074 / 1.086 |
| Receptions (RB) | 0.850 / 0.862 | 0.771 / 0.780 | 0.754 / 0.759 | 0.702 / 0.706 | 0.690 / 0.695 |
| Carries (RB) | 2.587 / 2.763 | 2.366 / 2.509 | 2.343 / 2.473 | 2.370 / 2.492 | 2.263 / 2.375 |
| Tackles (LB, S) | 1.270 / 1.283 | 1.264 / 1.284 | 1.254 / 1.276 | 1.281 / 1.297 | 1.307 / 1.318 |

(The bold cell is the only season-and-stat where the bell curve was closer: passing yards, 2023.)

**How to read it.**

- **The gain over the bell curve is real but small, except where the bell curve is plainly the wrong shape:** carries +5.4%, receiving yards +4.6%, rushing yards +2.3% to +2.9%, scrimmage yards +2.3%, targets +1.4%, tackles +1.3%, receptions +0.9%. What the bell curve gets wrong is its honesty: for carries it says 68.7% for "within 2 of the projection" and it happens 48.9% of the time (the curve says 47.6%). The PIT error, curve against bell: receiving yards 0.0021 against 0.0455, carries 0.0055 against 0.0486, rushing yards (RB) 0.0024 against 0.0327.
- **Every curve beats the player's own last games** by 1.9% to 13%.
- **All ten bands are within 2 points** (the widest gap: carries, 1.3). Counts run slightly conservative: what happened ran about a point above what the curve predicted for carries, tackles and RB receptions.
- **Receiving yards, the whole range** (ten groups of "chance of going past a mark", pooled over the seven marks; the counts of group members are 2,700 to 72,700 pairs):

| Chance said | Curve: said / happened | Bell: said / happened |
|---|---|---|
| 0% to 10% | 3.0% / 2.8% | 1.2% / 4.6% |
| 10% to 20% | 14.7% / 13.9% | 14.7% / 19.7% |
| 20% to 30% | 25.0% / 24.0% | 24.9% / 28.0% |
| 30% to 40% | 34.5% / 34.3% | 34.9% / 35.8% |
| 40% to 50% | 44.8% / 44.8% | 45.0% / 45.2% |
| 50% to 60% | 54.6% / 54.5% | 54.9% / 53.8% |
| 60% to 70% | 64.8% / 64.8% | 64.9% / 65.6% |
| 70% to 80% | 75.1% / 75.7% | 74.8% / 77.1% |
| 80% to 90% | 84.4% / 85.3% | 84.8% / 89.0% |
| 90% to 100% | 92.9% / 93.2% | 91.3% / 94.3% |

  The curve sits on the diagonal; the bell curve says 1.2% for a mark that is passed 4.6% of the time and 84.8% for one passed 89%: too sure at both ends. For carries the same table has the curve within 3 points in every group (the top group says 96.0%, happened 94.6%) and the Poisson up to 12 points out (the 8th group says 75.0%, happened 63.0%).
- **Out of sample, live.** A scratch run curved the production projections of 2026 weeks 4, 5, 6, 8 and 9 with history strictly before each week, and graded them with the production scoreboard's own results (written only to a scratch folder). The curve's CRPS was below the bell curve's for all ten stats (receiving yards 11.76 against 12.35 on 1,077 rows; carries 2.465 against 2.508; passing yards 40.82 against 40.98 on 130 rows); receptions (WR, TE) was a tie on the threshold Brier (0.10023 against 0.10021). This is the FL01 build's own check, not a live grade: **no live grade has run yet.**

## The addendum: zero is its own outcome (pre-registered 2026-11-14 22:27 UTC)

**The defect.** In the first reported run (`rai6vnyq`) the "no yards" mass was smeared across neighbouring whole numbers for a low projection: an analog who gained 0 yards but whose own P50 was a little different was mapped to `round(P50_row + z x w_row)`, which is 1 (or 2, or -1), not 0. Will Kacmarek (TE, P50 2.3, 2026 week 10): P(exactly 0) = 0.200, P(exactly 1 yard) = 0.292, P(exactly 2) = 0.011. A receiver with no catch has exactly 0 yards; the probe shows 60% of results at 0 or less for projections up to 10 yards. The standard marks start at 14.5, so the scores barely saw it, but "1 or more yards" and "within 7 of the projection" read wrong for every low-projection player, and both are on the tab.

**The rule for the fix, written before it was built or scored:** the variant `analog_zero` (amount stats only) replaces `analog` for a stat if both hold **on the tuning window only (2019-2020)**: (1) its pooled CRPS is no worse than the current method's by more than 0.1%; (2) its Brier score for "1 or more yards" (P(Y >= 1) against 1[y >= 1], on the same rows; a diagnostic added for the purpose, also reported for the bell and own-history curves) is lower than the current method's. The K, the similarity space and the history window were not re-tuned. Then the reported backtest was re-run once for the amount stats and the original four-part rule re-applied unchanged; both run ids are kept. The "1 or more" Brier is a diagnostic only (not in the ship rule).

**The tuning comparison (2019-2020; W&B `w6beygoe`):**

| Stat | Rows | CRPS `analog` | CRPS `analog_zero` | Change | Brier(1+) `analog` | `analog_zero` | bell | Replaces |
|---|---|---|---|---|---|---|---|---|
| Passing yards (QB) | 1,019 | 42.2847 | 42.2846 | -0.000% | 0.00001 | 0.00000 | 0.00002 | yes (no zeros in the data: no real change) |
| Rushing yards (QB) | 1,019 | 7.9330 | 7.8669 | -0.834% | 0.15840 | 0.15482 | 0.15588 | yes |
| Rushing yards (RB) | 2,897 | 13.8695 | 13.8202 | -0.355% | 0.11391 | 0.11111 | 0.11260 | yes |
| Scrimmage yards (RB) | 2,897 | 17.4775 | 17.4187 | -0.336% | 0.08402 | 0.08207 | 0.08450 | yes |
| Receiving yards (WR, TE) | 7,795 | 12.7692 | 12.7289 | -0.315% | 0.15993 | 0.14940 | 0.15812 | yes |

**The reported re-run (W&B `6glg980f`)** against the first run (`rai6vnyq`, its files kept in `backtests/curves/_reported_first_run/`; the first run's method, re-scored on the same rows, reproduces its CRPS to 15 digits). Every amount stat passes the same four-part rule, so all ten still ship:

| Stat | CRPS first run / re-run | Gain vs bell, first / re-run | Marks Brier, first / re-run (bell) | Band said, first / re-run, happened (gap pp first / re-run) | Brier(1+) first / re-run (bell) | P(1+) said first / re-run, happened |
|---|---|---|---|---|---|---|
| Passing yards (QB) | 39.2902 / 39.2902 | +0.10% / +0.10% | 0.18890 / 0.18890 (0.18893) | 17.4% / 17.4%, 17.2% (0.12 / 0.12) | 0.00000 / 0.00000 (0.00002) | 99.95% / 100.00%, 100.00% |
| Rushing yards (QB) | 8.4562 / 8.4095 | +2.36% / +2.90% | 0.12865 / 0.12861 (0.13041) | 47.4% / 48.2%, 48.1% (0.65 / 0.10) | 0.14337 / 0.13720 (0.14284) | 81.9% / 79.2%, 80.1% |
| Rushing yards (RB) | 13.6147 / 13.5852 | +2.69% / +2.90% | 0.11862 / 0.11862 (0.12015) | 35.1% / 35.1%, 36.1% (1.02 / 1.02) | 0.10826 / 0.10198 (0.10607) | 87.1% / 82.7%, 83.0% |
| Scrimmage yards (RB) | 16.5044 / 16.4642 | +2.03% / +2.27% | 0.11284 / 0.11284 (0.11399) | 26.6% / 26.8%, 27.2% (0.65 / 0.46) | 0.08684 / 0.08302 (0.08620) | 91.3% / 87.6%, 88.2% |
| Receiving yards (WR, TE) | 11.4878 / 11.4535 | +4.35% / +4.64% | 0.10497 / 0.10497 (0.10643) | 44.9% / 45.1%, 44.6% (0.23 / 0.41) | 0.16116 / 0.15084 (0.16207) | 71.2% / 65.3%, 65.8% |

**What changed is the low end.** The old curve over-stated "1 or more yards" by 2-5 points for the stats with many zeros (receiving yards 71.2% against 65.8% happened; rushing yards of RBs 87.1% against 83.0%; scrimmage yards 91.3% against 88.2%); the variant is within 1 point of what happened for all four (the largest: rushing yards of QBs, 79.2% against 80.1%). The standard marks and the band hardly moved, which is why the first scores didn't see it. PIT calibration also improves (receiving yards 0.0034 to 0.0021, rushing yards of RBs 0.0056 to 0.0024). **Passing yards is unchanged** (a quarterback never has zero passing yards in the data): the rule still passes by the same hair. With the old mapping the curve was barely better than the bell curve for P(1+) on receiving yards (0.1612 against 0.1621 on the reported rows; it lost to it on the tuning rows); `analog_zero` is 0.1508.

**Week 10, after the fix:** the real curves were re-made from the saved projections (no game had kicked off): Will Kacmarek (TE, P50 2.3): P(exactly 0) 0.200 to 0.520, P(exactly 1) 0.292 to 0.007, P(1 or more) 0.758 to 0.473, median 1 to 0, everything from 14.5 up unchanged (more than 14.5: 18.6%; the bell curve says 7.3%). Jaxon Smith-Njigba (P50 81.6) is unchanged to three decimals at every standard mark (more than 49.5: 77.0%; more than 74.5: 58.3%; more than 99.5: 40.1%), and the chance of "within 7" (11.5%); P(1 or more) 98.5% to 98.8%.

## Is this number good? (honest notes)

- **A gain of 1% to 5% in CRPS sounds small, because the bar is already a good one.** The bell curve is built from the player model's own well-calibrated 80% range, so it gets the middle right. The curve's win is in the shape: the floor, the right tail and the honesty of the thresholds (PIT error 0.002 against 0.046 for receiving yards).
- **Passing yards ships by the letter of the rule and no more.** CRPS 0.10% better than the bell curve (4 of 5 seasons, 2023 lost) and equal on the threshold Brier to four decimals. Treat a quarterback's curve as "the same as a bell curve through the range": no worse, and no better in a way a reader would notice.
- **The curve does not make ranges tighter.** A band of plus or minus 7 yards around a typical receiving projection holds about 1 game in 9 (Jaxon Smith-Njigba, week 10: 11.5%). That is the sport, not a model weakness: the published 80% ranges cover 80% to 85%. What the curve adds is a chance for any number or band that has been checked against what happened.
- **A curve is "if he plays".** The chance he plays is the availability model's (a separate output); no combined number is published.
- **The comparison with the player's own games** (1.9% to 13% better on the reported window) shows the curve beats the simplest honest distribution of his own past. It is a close call for quarterbacks' rushing yards: on the tuning window the player's own games scored 7.920 against the first curve's 7.933 (the zero variant then gave 7.867); on the reported window the curve is ahead, 8.410 against 8.574.
- **No live grade has run yet.** The out-of-sample check above is a scratch run on 2026 weeks 4-9; the first real grade comes with Tuesday's run (week 10).

## Reading the W&B charts

**Honest status:** every name below was checked against the code that logs it (`curves_runs.py`: `define_metric`, `run.log`, `wandb.Table`, `wandb.plot.line_series`, `run.summary`, the `wandb.Artifact`), not against the W&B pages: **nobody has opened these runs' charts yet** (agent processes can't use the W&B API). The keys and the numbers should match the pages; expect small differences in wording.

All runs are in W&B project `nfl-analytics-engine`, group **`forecast-lab`** (smoke runs: **`forecast-lab-smoke`**), tagged `fl01`, `curves` and `launched-by:<who>`.

### The metrics in one minute

| Metric | What it measures | Better | Our 2021-2025 values |
|---|---|---|---|
| **CRPS** | The whole-curve miss, in the stat's unit | Lower; below the bell curve in at least 4 of 5 seasons | Receiving yards 11.454 (bell 12.010); carries 2.386 (2.523); passing yards 39.290 (39.329) |
| **Brier at the marks** | The mean squared miss of "chance of more than each standard mark" | Lower | Receiving yards 0.10497 (bell 0.10643) |
| **Band said and happened** | The curve's chance of landing within the band, and how often it did | Within 2 points | Receiving yards 45.1% / 44.6% |
| **PIT error** (`pit_ece`) | How far the whole curve is from calibrated (0 = perfect) | Lower; about 0.002-0.006 here | Receiving yards 0.0021 (bell 0.0455) |
| **Seasons won** | Seasons the curve's CRPS beat the bell curve's | At least 4 of 5 | 5 of 5 (passing yards 4 of 5) |

### `curves-backtest` runs (run name `curves-backtest-<kind>-<first>-<last>`, kind `reported`, `tune` or `smoke`; the zero comparison is `curves-variant-analog_zero-tune-2019-2020`)

- **Config:** `mode`, `seasons`, `targets`, `settings` (per stat), `history_from`, `tune_seasons`, `report_seasons`, `ship_rule`, `dataset_version`, `git_commit`.
- **Live curves** (x-axis `bt/step`: one point per scored week of each stat, the stats one after another, so each stat's curves occupy their own stretch of the axis): `bt/<stat>/crps_model`, `crps_bell`, `crps_own` and `brier_model`, `brier_bell` (cumulative over the weeks so far; **the main charts**: `crps_model` should sit below `crps_bell` from the first weeks and end at the pooled number, for example 11.4535 against 12.0102 for receiving yards), and `week_crps_model`, `week_crps_bell` (one week: noisy). In a tuning run the live curves are the chosen settings'.
- **Charts** (reported runs only): `reliability/<stat>` (a line chart: "chance of going over a mark (10 bins)", x = the predicted chance, series: the curve, the bell curve, "perfect"; **good:** the curve's line on the diagonal, the bell curve's off it at both ends) and `crps_by_season/<stat>` (CRPS by season: curve, bell curve, own history; **good:** the curve's line under the bell curve's in every season; passing yards' two lines lie on each other, and 2023's cross).
- **Tables:** `backtest/pooled_rows` (every pooled number per stat), `backtest/by_season_rows`, `backtest/verdict_rows` (`ships`, the four parts' `_ok` flags, `seasons_won`, `band_gap_pp`; **good:** `ships` true on all ten), `backtest/pit_rows` (the PIT histogram: `target`, `source` = model, bell or own, `bin`, `share`, `uniform` = 0.1; **good:** shares near 10% in every bin, the bell curve's visibly off), `backtest/reliability_rows` (reported runs: the table behind the reliability chart) or `backtest/tuning_rows` (tuning runs: every candidate with its `rank`, label, CRPS, `crps_vs_bell_pct`, Brier and `band_gap_pp`).
- **Summary:** per stat `<stat>/crps_model`, `crps_bell`, `crps_own`, `crps_vs_bell_pct`, `brier_model`, `brier_bell`, `band_pred`, `band_hit`, `pit_ece`, `pit_ece_bell`, `rows`, `ships`; tuning runs add `<stat>/best` (the best candidate's label); reported runs add `shipped` (the comma-separated stats that shipped).
- **Artifact** `curves-model` (type `model`; only on a promoted run): the version folder (`params.json`); aliases `<version>` and `production`; metadata `version`, `shipped`, `seasons`, `sources` (which version's backtest produced each stat's parameters).
- **The zero comparison** (`curves-variant-analog_zero-tune-2019-2020`, **`w6beygoe`**): table `variant/comparison_rows` (per amount stat: the two CRPS, the change in %, the "1 or more" Brier of the current method, the variant and the bell curve, the chance of "1 or more" said and happened, `ok_crps`, `ok_brier_ge1`, `replace`); summary `<stat>/crps_change_pct`, `brier_ge1_current`, `brier_ge1_variant`, `brier_ge1_bell`, `replace`. **Good:** `replace` true on all five.
- **The runs:** smoke **`cisoa4b6`** (group `forecast-lab-smoke`, 2 stats, a few weeks); tuning **`6hpvwrnb`** (2019-2020, every candidate); first reported run and promotion **`rai6vnyq`** (`curves-model:2021-2025_20261114T220939Z`, superseded for the five amount stats); the zero comparison **`w6beygoe`**; the amount stats' re-run and promotion **`6glg980f`** (`curves-model:2021-2025_20261114T223208Z`, `production`).

### `curves-predict` runs (`curves-predict-<season>-w<NN>`; tags `season:S`, `week:NN`)

Table `curves/biggest_gaps` (the 50 curves where the curve and the bell curve disagree most: player, team, stat, the projection, the standard mark with the biggest gap, the curve's chance of more than it, the bell curve's, the gap); summary `rows`, `games`, `seconds`, `rows/<stat>`. **Good:** `rows` equal to the sum of `rows/<stat>`; carries and low-projection receivers at the top of the gaps. Week 10: **`szulddac`** (1,381 curves, 13 games; made after the zero fix); an earlier run before the fix: `59nmn18u`.

### `curves-grade` runs (`curves-grade-<season>-w<NN>`)

Table `grade/targets_rows` (per stat: `n`, the three CRPS, the three threshold Brier scores, the band said and happened, `pit_ece`); summary `graded`, `rows`, `not_played`, `waiting`. **Good:** the curve's CRPS below the bell curve's for most stats in most weeks; `waiting` 0 once the week is final. **None has run yet on a real graded week.**

## Known biases and limits

- **Passing yards is practically the bell curve** (above).
- **The curve doesn't tighten anything**, and it is **"if he plays"**.
- **Tuning could not tell "all earlier seasons" from "the last 4"** (history starts in 2017, the tuning window is 2019-2020). Revisit at the pre-season retune, on a window that can show it.
- **A star receiver's curve borrows from the next sizes down.** Projections over 70 yards are rare (707 rows in 7 seasons); standardizing the misses is what lets the analogs be shared.
- **The curve inherits the player model's projection.** A projection that is wrong because a player's role changed gives a curve that is wrong in the same way.
- **The first live curves were made from one run's projections:** week 10's file holds 1,381 curves for 631 players in 13 games (the Thursday game had been played when the projections were made, so it has none).
- **Counts' bands are conservative by about a point** (carries, tackles, RB receptions).
- **Own history needs 6 games** (else the bell curve): the comparison is a little kinder to the curve for players with short histories.
- **A threshold chance is a property of the projection, not advice.** It is shown in the control room only; the digest never reads these files.

## Outputs

`{NFL_DATA_ROOT}/forecastlab/<S>/week<NN>/curves.parquet` (one row per player x game x stat, `CURVE_SCHEMA`: the copied projection, `grid_lo` and `cdf`, `q05`-`q95`, `marks`, `band`, `p_band`, `bell_p_band`, `n_analogs`, and `actual` / `played` once graded), `curves.json` (when it was made, the versions, rows by stat, the history rows used, any games made after kickoff or kept as saved); `<S>/curves_scoreboard.parquet` (the live grades, `CURVE_SCORE_SCHEMA`); `backtests/curves/<stat>/predictions.parquet` (without `cdf`) + `summary.json`, with `_tuning/` (every candidate, and `zero_variant.json`), `_smoke/` and `_reported_first_run/`; `models/forecastlab/curves/<version>/params.json` and `production.json`. **The kickoff rule:** `predict` never re-makes a curve whose game has kicked off; curves already in the file for it stay exactly as they are (`games_kept`), and a started game with none gets curves anyway and is listed in `games_after_kickoff`.

## Verification

- `uv run pytest -q tests/forecastlab -k fl_curves` (52 tests): the atom equals the zero share; a zero analog can never put mass on 1; nothing positive below 1; the cdf stays monotone and ends at 1; equality with `analog` where nothing is zero or negative; the replace rule; the merged promotion; leakage (a test scrambles actuals from a week on and checks that nothing at or before it moves).
- The lead recomputed the threshold Brier scores, the band calibration and a pinball-loss comparison independently from the saved backtest rows: they matched.
- The production-unchanged check (D107): `tests/test_production_untouched.py`.

## Versioning

- **`2021-2025_20261114T223208Z`** (2026-11-14 22:32 UTC, after the addendum): all ten stats shipped; `production`. Its `params.json` and `production.json` carry all ten stats: the five amount stats with `analog_zero` from this run (`6glg980f`), the five count stats with the parameters, backtest numbers and shipped status of the first run (`sources` and `wandb_runs` say which stat comes from which version and run).
- `2021-2025_20261114T220939Z` (2026-11-14 22:09 UTC): the first reported run (`rai6vnyq`); all ten shipped; the five amount stats superseded.
- **Refit after each season:** add the finished season to `forecastlab.curves.report_seasons`, write the ship rule down first, re-run `uv run nfl forecastlab backtest --model curves --promote` (a run of only some stats is merged into the previous pointer), update this card. Use a tuning window that can tell the history windows apart.
