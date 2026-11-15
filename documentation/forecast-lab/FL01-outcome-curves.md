# FL01: outcome curves

**Track:** [Forecast lab](README.md) · **Status:** see [PROGRESS](../plans/PROGRESS.md) · **Decision:** D158

**Read first:** the track [README](README.md) §2.2, [player projections guide](../guides/player-projections.md), `src/nflengine/models/player_schema.py` (read only: the published file's columns), the `model-experiment` skill, `src/nflengine/forecastlab/schema.py` (the contract).

## What it delivers

A new model family, `curves`, that turns each published player projection (P10, P50, P90, or a count's mean) into the whole distribution: the chance of clearing any number and of landing in any band. It reads the player model's published files only (D107) and never enters the digest.

## The method (plan)

**Analog curves.** For a new projection, find the past walk-forward projections of the same stat that looked most like it (same size, same range width), and look at where the actual results landed relative to those projections. That set of real outcomes, rescaled to this projection, is the curve.

- **Amounts (yards):** each analog's miss is standardized, `z = (actual − P50) / (P90 − P10)`; the curve is `P50 + z × (P90 − P10)` over the analogs, on whole yards, clipped at the stat's floor.
- **Counts (receptions, carries, tackles ...):** a negative binomial at the published mean, with its spread fit on the analogs.
- History is always strictly before the week being predicted.

**Baselines:**

| Baseline | What it is |
|---|---|
| Bell curve | A normal distribution centred on P50 with the spread implied by P10–P90 (counts: a Poisson at the mean). What you'd compute by hand today |
| Own history | The player's own earlier games as the distribution (at least 6 games; otherwise the bell curve) |

## Tasks

- [x] 🤖 Probe: the shape of the misses by projection size (done in the planning probe: see Findings), by range width and by season. Re-checked in the build by role: no effect (see "Findings from the build").
- [x] 🤖 `forecastlab/curves.py`: the analog curve, the two baselines, the scores (CRPS, threshold Brier, band calibration, PIT). Pure functions on arrays and frames.
- [x] 🤖 `forecastlab/curves_runs.py`: `run_backtest`, `run_predict`, `run_grade`, files per `schema.py`, W&B through `tracking.init_run`.
- [x] 🧑 → 🤖 (waived, D158) The tuning-window run, then the reported backtest, `--launched-by agent`.
- [x] ✋ → rule (waived, D158) The ship decision by the rule below: all ten targets ship (see "Reported results").
- [ ] 🤖 The model card (written with the other Forecast lab docs, README §1).

## Windows (fixed before any run)

| Window | Seasons | Used for |
|---|---|---|
| History only | 2017–2018 | The first analogs (the player model's own burn-in seasons) |
| Tuning | 2019–2020 | Number of analogs, what "similar" means, how far back history goes |
| Reported | 2021–2025 | The numbers in the model card; touched once the settings are fixed |

## The ship rule (pre-registered 2026-11-14, before any reported run)

Each target ships on its own. A target that misses is not shown in the tab and goes to Rishi. On 2021–2025, for rows where the player played:

1. **Closer than the bell curve:** CRPS below the bell curve's pooled **and** in at least 4 of 5 seasons.
2. **Closer than his own history:** CRPS below the own-history baseline's pooled.
3. **Thresholds:** the mean Brier score over the target's standard marks (`schema.CURVE_TARGETS`) below the bell curve's pooled.
4. **Honest bands:** the predicted chance of landing in the band around the projection (± 7 yards; ± 15 for passing yards; ± 1 or 2 for counts) is within 2 percentage points of how often it happened, pooled.

Model and baselines are scored on exactly the same rows.

## Findings from the probe (checked live 2026-11-14)

Receiving yards (WR / TE), walk-forward 2019–2025, 29,266 scored rows:

- The published 80% range covers 81.9%.
- The misses are not bell-shaped, and the shape depends on the projection's size. With `z` as above, a bell curve would put the 10th and 90th percentiles at −0.50 and +0.50 everywhere. Real values:

| Projection (P50) | Rows | Share with 0 yards or fewer | z at 10% | z at 50% | z at 90% | z at 95% |
|---|---|---|---|---|---|---|
| up to 10 | 13,192 | 60% | −0.14 | 0.00 | +0.95 | +1.45 |
| 10–20 | 4,140 | 25% | −0.30 | −0.02 | +0.72 | +0.98 |
| 20–35 | 4,639 | 11% | −0.36 | 0.00 | +0.65 | +0.87 |
| 35–50 | 3,600 | 6% | −0.42 | −0.01 | +0.64 | +0.84 |
| 50–70 | 2,988 | 2% | −0.42 | 0.00 | +0.61 | +0.83 |
| over 70 | 707 | 1% | −0.41 | +0.02 | +0.59 | +0.77 |

- The median is right at every size (z at 50% ≈ 0): the curve can keep the published projection as its centre.
- Projections over 70 yards are rare (707 rows in 7 seasons), so the curve for a star receiver must borrow shape from the next sizes down: standardized misses do that, raw outcomes of "similar" players would not.
- Backtest rows exist only for players who played, so a curve is "if he plays". The chance he plays is FL00's.

## Findings from the build (2026-11-14)

**Shape by role (a re-check, no change).** Standardized misses (`z`, receiving yards, 2019-2025) by tight end / wide receiver, the model's confidence label, `role_ok`, `role_change`, home / away and "fewer than 6 games of history", each inside the projection sizes of the table above: the quantiles agree to within the sampling noise of the cell (for example the 10th / 90th percentile of `z` for a 35-50 yard projection: TE -0.42 / +0.61, WR -0.42 / +0.66). The shape depends on the size of the projection, not on who the player is, so the similarity space stays (P50) or (P50, width) with no role columns. (This look used 2019-2025 rows; it was descriptive and changed nothing.)

**Tuning table (2019-2020, history 2017-2018 first; CRPS pooled over the rows, lower is better).** 16 candidates for an amount (K in 500 / 1000 / 2000 / 4000; similarity on P50 or on (P50, width); history from every earlier season or the last 4) and 32 for a count (the same K and history windows for a negative binomial at the mean with its spread fit on the nearest analogs, `nb`; the same with the mean scaled by what those analogs did, `nb_cal`; and the analogs' raw results, on the mean or on (mean, width)). The best of each:

| Target | Rows | Bell | Own | Chosen | CRPS | vs bell | K 500 / 1000 / 2000 / 4000 (vs bell, same form) |
|---|---|---|---|---|---|---|---|
| passing yards (QB) | 1,019 | 42.196 | 44.244 | analogs, K 1000, (P50, width) | 42.285 | -0.21% | -0.22 / -0.21 / -0.28 / -0.28 |
| rushing yards (QB) | 1,019 | 8.123 | 7.920 | analogs, K 500, (P50, width) | 7.933 | +2.34% | +2.34 / +2.07 / +1.90 / +1.90 |
| rushing yards (RB) | 2,897 | 14.275 | 15.030 | analogs, K 500, (P50, width) | 13.869 | +2.84% | +2.84 / +2.76 / +2.52 / +2.01 |
| scrimmage yards (RB) | 2,897 | 17.826 | 18.988 | analogs, K 500, (P50, width) | 17.478 | +1.95% | +1.95 / +1.80 / +1.65 / +1.32 |
| receiving yards (WR/TE) | 7,795 | 13.352 | 13.783 | analogs, K 1000, P50 | 12.769 | +4.36% | +4.28 / +4.36 / +4.30 / +3.92 |
| receptions (WR/TE) | 7,795 | 0.8970 | 0.9629 | `nb`, K 2000 | 0.8886 | +0.93% | +0.91 / +0.92 / +0.93 / +0.93 |
| targets (WR/TE) | 7,795 | 1.2317 | 1.3337 | `nb_cal`, K 1000 | 1.2111 | +1.68% | +1.59 / +1.68 / +1.67 / +1.63 |
| receptions (RB) | 2,897 | 0.8180 | 0.8676 | `nb_cal`, K 1000 | 0.8087 | +1.14% | +1.10 / +1.14 / +1.09 / +0.91 |
| carries (RB) | 2,897 | 2.5227 | 2.7550 | `nb`, K 500 | 2.3841 | +5.49% | +5.49 / +5.47 / +5.20 / +3.58 |
| tackles (LB/S) | 9,481 | 1.2708 | 1.3640 | `nb_cal`, K 1000 | 1.2538 | +1.34% | +1.31 / +1.34 / +1.34 / +1.31 |

- **Flat in K**, as the P06 lessons predict: for most targets the top candidates sit within 0.1% of each other. The choice is the lowest CRPS, no judgment on top. For counts a negative binomial beats the analogs' raw results (they pile on whatever the nearest rows did: at K 4000 a raw-outcome carries curve is 42% worse than the Poisson), and `nb` vs `nb_cal` is a wash (0.0-0.1%).
- **"All earlier seasons" vs "the last 4" cannot be told apart on this window.** With history from 2017 and tuning in 2019-2020, "the last 4 seasons" reaches back past the first season for every row, so both give identical numbers for every candidate (checked on all of them). The default is "all earlier seasons" (more analogs, nothing to tune). The question becomes real from 2022 on: revisit it at the pre-season retune, on a window that can show it.
- **Tried on the tuning window and dropped (no gain):** Gaussian smoothing of the curve (0.03-0.10 of its width): 0 to -0.2%; a blend with a normal curve fit to the analogs' mean and spread of `z` (weights 0.25-0.75; the pure normal fit on passing yards: -0.04% vs the bell curve); the width floor (1, 2, 5, 10, 20 yards: within 0.05% of each other except 20, about 0.1% worse). The code keeps only the width floor (5 yards).
- **Passing yards is the one target with nothing to gain:** the published P10-P90 range of a QB is already well calibrated, so a bell curve through it is close to the right shape (tuning window -0.2%).

## Reported results (2021-2025, one run, settings fixed by the tuning table)

> The amount targets were re-run once under the addendum below (`analog_zero`); the table here is the first run, which stays in the record. Count targets are unchanged.

Run `curves-backtest-reported-2021-2025` (W&B `rai6vnyq`), once; promoted as `curves-model` version `2021-2025_20261114T220939Z`. Scored on the walk-forward rows of players who played; the curve and both baselines on exactly the same rows and the same whole-number grid. CRPS is in the stat's own unit (lower is better); Brier is the mean over the target's marks.

| Target | Rows | CRPS curve | bell | own | Seasons won vs bell | Brier curve | bell | Band: predicted / happened (bell's prediction) | Ships |
|---|---|---|---|---|---|---|---|---|---|
| passing yards (QB) | 2,714 | 39.290 | 39.329 | 41.826 | 4 of 5 | 0.18890 | 0.18893 | 17.4% / 17.2% (17.3%) | yes, by a hair |
| rushing yards (QB) | 2,714 | 8.456 | 8.660 | 8.574 | 5 of 5 | 0.1286 | 0.1304 | 47.4% / 48.1% (48.8%) | yes |
| rushing yards (RB) | 7,485 | 13.615 | 13.991 | 14.821 | 5 of 5 | 0.1186 | 0.1201 | 35.1% / 36.1% (33.2%) | yes |
| scrimmage yards (RB) | 7,485 | 16.504 | 16.846 | 18.042 | 5 of 5 | 0.1128 | 0.1140 | 26.6% / 27.2% (25.4%) | yes |
| receiving yards (WR/TE) | 21,471 | 11.488 | 12.010 | 12.346 | 5 of 5 | 0.1050 | 0.1064 | 44.9% / 44.6% (41.0%) | yes |
| receptions (WR/TE) | 21,471 | 0.8281 | 0.8358 | 0.8989 | 5 of 5 | 0.1011 | 0.1016 | 70.7% / 70.7% (75.6%) | yes |
| targets (WR/TE) | 21,471 | 1.1093 | 1.1245 | 1.2265 | 5 of 5 | 0.1014 | 0.1023 | 59.9% / 60.6% (67.8%) | yes |
| receptions (RB) | 7,485 | 0.7536 | 0.7604 | 0.8087 | 5 of 5 | 0.1348 | 0.1361 | 73.8% / 74.7% (80.3%) | yes |
| carries (RB) | 7,485 | 2.3859 | 2.5225 | 2.7425 | 5 of 5 | 0.1134 | 0.1183 | 47.6% / 48.9% (68.7%) | yes |
| tackles (LB/S) | 24,468 | 1.2750 | 1.2915 | 1.3965 | 5 of 5 | 0.1418 | 0.1428 | 50.0% / 51.3% (59.3%) | yes |

- **The gain over the bell curve is real but small, except where the bell curve is plainly the wrong shape:** receiving yards +4.4%, carries +5.4%, rushing yards +2.4-2.7%, scrimmage yards +2.0%, targets +1.4%, tackles +1.3%, receptions +0.9% (CRPS). What the bell curve gets wrong is its calibration: for carries it says 68.7% for "within 2 of the projection" and it happens 48.9% of the time (the curve says 47.6%). PIT calibration error, curve vs bell: receiving yards 0.003 vs 0.046, carries 0.005 vs 0.049, tackles 0.003 vs 0.020.
- **Passing yards ships only by the letter of the rule:** CRPS 0.10% below the bell curve, 4 of 5 seasons (2023 lost), Brier equal to four decimals. Treat it as "the same as a bell curve through the range": no worse, and no better in a way a reader would notice.
- **Honest bands:** all ten are within 2 points (largest gap: carries, 1.3). Counts are slightly conservative: what happened ran a point above what the curve predicted for carries, tackles and RB receptions.
- **Out of sample, live:** the production projections of 2026 weeks 4, 5, 6, 8 and 9, curved with history strictly before each week and graded by `run_grade` from the production scoreboard's own graded files (a scratch run): the curve's CRPS is below the bell curve's for all ten targets (receiving yards 11.76 vs 12.35 on 1,077 rows; carries 2.465 vs 2.508; passing yards 40.82 vs 40.98 on 130 rows). Receptions (WR/TE) Brier is a tie (0.10023 vs 0.10021).

## Addendum, pre-registered 2026-11-14 22:27 UTC

Written before the variant was built or scored on any season. The first reported run (`rai6vnyq`) stays in the record unchanged.

**The defect.** For a low projection the "no yards" mass is smeared across neighbouring whole numbers. Will Kacmarek (TE, `rec_yds-wrte`, P50 2.3, 2026 week 10): P(exactly 0) = 0.200, P(exactly 1 yard) = 0.292, P(exactly 2) = 0.011. The cause: an analog who gained 0 yards, but whose own P50 was a little different, is mapped to `round(P50_row + z * w_row)`, which is 1 (or 2, or -1), not 0. A receiver with no catch has exactly 0 yards, and the probe shows 60% of results at 0 or less for projections up to 10 yards. The standard marks start at 14.5, so the scores barely saw it, but "1 or more yards" and "within 7 of the projection" read wrong for every low-projection player, and both are on the tab.

**The variant, `analog_zero` (amount targets only; counts are untouched).** Among a row's analogs: the share whose actual is exactly 0 is the mass at 0 (an atom, nothing smeared into 1 or -1); an analog with a negative actual keeps its own value (clipped at the floor); an analog with a positive actual maps as today, `round(P50 + z * w)` with `w` floored at 5 yards, but never below 1.

**How it is chosen:** on the tuning window only (2019-2020), per amount target, with the K, the similarity space and the history window as already chosen (not re-tuned). `analog_zero` replaces the current method (`analog`) for a target if both hold: (1) its pooled CRPS there is no worse than the current method's by more than 0.1%; (2) its Brier score for "1 or more yards" (P(Y >= 1) against 1[y >= 1], on the same rows; a diagnostic added for this purpose, also reported for the bell curve and the own-history curve) is lower than the current method's. A target that does not meet both keeps its current method.

**Then:** the reported backtest (2021-2025) is re-run once for the amount targets, with the chosen methods, and the original four-part ship rule is re-applied unchanged. Both reported run ids are kept (`rai6vnyq` and the new one). A target that passed before and fails now is not shown and goes to Rishi. Count targets keep their parameters and shipped status. The "1 or more" Brier is a diagnostic only: it is not added to the ship rule.

### Addendum results (2026-11-14, 22:30-22:35 UTC)

**Tuning comparison (2019-2020 only; W&B `w6beygoe`).** Each amount target with its chosen K, space and history window; CRPS pooled over the rows, Brier for "1 or more yards" (lower is better). The rule: replace if CRPS is not worse by more than 0.1% and the "1 or more" Brier is lower than the current method's.

| Target | Rows | CRPS `analog` | CRPS `analog_zero` | Change | Brier(1+) `analog` | `analog_zero` | bell | Replaces |
|---|---|---|---|---|---|---|---|---|
| passing yards (QB) | 1,019 | 42.2847 | 42.2846 | -0.000% | 0.00001 | 0.00000 | 0.00002 | yes (no zeros in the data: no real change) |
| rushing yards (QB) | 1,019 | 7.9330 | 7.8669 | -0.834% | 0.15840 | 0.15482 | 0.15588 | yes |
| rushing yards (RB) | 2,897 | 13.8695 | 13.8202 | -0.355% | 0.11391 | 0.11111 | 0.11260 | yes |
| scrimmage yards (RB) | 2,897 | 17.4775 | 17.4187 | -0.336% | 0.08402 | 0.08207 | 0.08450 | yes |
| receiving yards (WR/TE) | 7,795 | 12.7692 | 12.7289 | -0.315% | 0.15993 | 0.14940 | 0.15812 | yes |

The variant was not worse on CRPS anywhere; it is better by 0.3-0.8% where zeros are common, and it is the only version of the curve whose "1 or more" Brier beats the bell curve's on every target that has zeros (the old curve lost to the bell curve on receiving and rushing yards of QBs and RBs).

**The reported backtest, re-run once for the amount targets (W&B `6glg980f`; the first run, `rai6vnyq`, stays in the record, and its files are kept in `backtests/curves/_reported_first_run/`).** The first run's method, re-scored on the same rows, reproduces its CRPS to 15 digits. Every amount target passes the same four-part rule, so all ten targets still ship. "Brier(1+)" is a diagnostic only (not in the rule); the first-run column is the first run's method scored on it after the fact.

| Target | CRPS first run | CRPS re-run | vs bell, first / re-run | Seasons won (first / re-run) | Marks Brier first / re-run (bell) | Band predicted first / re-run, happened (gap pp first / re-run) | Brier(1+) first / re-run (bell) | P(1+) predicted first / re-run, happened |
|---|---|---|---|---|---|---|---|---|
| passing yards (QB) | 39.2902 | 39.2902 | +0.10% / +0.10% | 4 / 4 | 0.18890 / 0.18890 (0.18893) | 17.4% / 17.4%, 17.2% (0.12 / 0.12) | 0.00000 / 0.00000 (0.00002) | 99.95% / 100.00%, 100.00% |
| rushing yards (QB) | 8.4562 | 8.4095 | +2.36% / +2.90% | 5 / 5 | 0.12865 / 0.12861 (0.13041) | 47.4% / 48.2%, 48.1% (0.65 / 0.10) | 0.14337 / 0.13720 (0.14284) | 81.9% / 79.2%, 80.1% |
| rushing yards (RB) | 13.6147 | 13.5852 | +2.69% / +2.90% | 5 / 5 | 0.11862 / 0.11862 (0.12015) | 35.1% / 35.1%, 36.1% (1.02 / 1.02) | 0.10826 / 0.10198 (0.10607) | 87.1% / 82.7%, 83.0% |
| scrimmage yards (RB) | 16.5044 | 16.4642 | +2.03% / +2.27% | 5 / 5 | 0.11284 / 0.11284 (0.11399) | 26.6% / 26.8%, 27.2% (0.65 / 0.46) | 0.08684 / 0.08302 (0.08620) | 91.3% / 87.6%, 88.2% |
| receiving yards (WR/TE) | 11.4878 | 11.4535 | +4.35% / +4.64% | 5 / 5 | 0.10497 / 0.10497 (0.10643) | 44.9% / 45.1%, 44.6% (0.23 / 0.41) | 0.16116 / 0.15084 (0.16207) | 71.2% / 65.3%, 65.8% |

- **What changed is the low end.** The old curve over-predicted "1 or more yards" by 2-5 points for the targets with many zeros (receiving yards 71.2% vs 65.8% happened; rushing yards of RBs 87.1% vs 83.0%; scrimmage yards 91.3% vs 88.2%); the variant is within 1 point of what happened for all four (the largest: rushing yards of QBs, 79.2% vs 80.1%). The standard marks (14.5 and up) and the band are almost unchanged, which is why the first scores did not see it. PIT calibration also improves (receiving yards 0.0034 to 0.0021, rushing yards of RBs 0.0056 to 0.0024).
- **Passing yards is unchanged** (a QB never has zero passing yards in the data; the variant changes the CRPS in the sixth digit). The rule still passes by the same hair as before (+0.10%, 4 of 5 seasons).
- **The "1 or more" Brier shows the lesson:** with the old mapping the curve was barely better than the bell curve for P(1+) on receiving yards (0.1612 vs 0.1621 on the reported rows; it lost to it on the tuning rows); `analog_zero` is 0.1508.
- **Week 10:** the real curves were re-made from the saved projections (no game had kicked off): Will Kacmarek (TE, P50 2.3): P(0 yards) 0.200 to 0.520, P(exactly 1) 0.292 to 0.007, P(1 or more) 0.758 to 0.473, median 1 to 0, everything from 14.5 up unchanged (over 14.5: 18.6%; the bell curve says 7.3%). Jaxon Smith-Njigba (P50 81.6): unchanged to three decimals at every standard mark (over 49.5: 77.0%; over 74.5: 58.3%; over 99.5: 40.1%) and the "within 7" chance (11.5%); P(1 or more) 98.5% to 98.8%.

## As built: deviations from the task list

- **Files:** `src/nflengine/forecastlab/curves.py` (pure), `curves_runs.py` (files, W&B, `run_backtest` / `run_predict` / `run_grade`), `tests/forecastlab/test_fl_curves_core.py`, `test_fl_curves_runs.py`, `fl_curves_fixtures.py`.
- **Addendum code:** `Settings.method == "analog_zero"` (`curves._amount_cdf`); the "1 or more" diagnostic (`curves.p_ge1`, `RowScores.p_ge1`; `brier_ge1_model` / `_bell` / `_own`, `ge1_pred`, `ge1_hit` in every pooled and by-season summary); `curves_runs.run_variant_check` (the tuning-window comparison and the replace rule; writes `backtests/curves/_tuning/zero_variant.json`); `_promote` merges a partial run into the previous pointer. Tests: 52 in `-k fl_curves` (the atom equals the zero share, a zero analog can never put mass on 1, nothing positive below 1, the cdf stays monotone and ends at 1, equality with `analog` where nothing is zero or negative, the replace rule, the merged promotion).
- **The model is its settings.** A curve is built at run time from the player model's own walk-forward predictions, so `models/forecastlab/curves/<version>/params.json` and `production.json` hold only the per-target settings (`method`, `k`, `space`, `hist_seasons`, `w_floor`), the grid bounds, the shipped list and the backtest verdicts. The settings chosen on the tuning window are `CHOSEN` in `curves_runs.py` (config `forecastlab.curves.settings.<key>` overrides one).
- **History used for a week's curves:** the player model's backtest (`runs/backtests/player/<key>/`, 2017-2025) plus, for later seasons, each season's `runs/<S>/player_walkforward.parquet` (the weekly run keeps the season's earlier weeks there, with results), always strictly before the week. A projection that has no result (the player did not play) is not history. The analogs come from the same walk-forward process as a live projection, so a backtest P50 and a live P50 mean the same thing.
- **Grading source:** `runs/<S>/week<NN>/predictions_players_scored.parquet` (written by the production scoreboard: every projected player, `played` false for a player who did not play), so a curve's actual is exactly the production scoreboard's. If that file does not exist, the season's `player_walkforward.parquet` (players who played only: a game with rows there is done, so a projected player missing from it did not play). No curated-table fallback was built: both files are written by the weekly run, before the curves of the next week are made. A game with neither stays `played` null ("waiting").
- **Curve form:** amounts: the analog curve on `z = (actual - P50) / max(P90 - P10, 5)`, and since the addendum `analog_zero` for every amount target (a zero stays an atom at 0; a negative result keeps its value; a positive result maps as above but never below 1; so the mass at 0 is exactly the analogs' zero share, and nothing positive lands below 1); counts: a negative binomial at the published mean, its spread fit on the nearest analogs by the method of moments (`var = mu + alpha mu^2`; `nb_cal` also scales the mean by the analogs' results-over-means ratio, clipped to 0.25-4). With fewer than 100 analogs the curve is the bell curve (`n_analogs` 0).
- **Whole-number grids:** from the stat's floor to a fixed top (`GRID_HI` in `curves.py`: 700 for passing yards, 350 for receiving yards ... 40 for tackles); mass above lands on the last value. Stored `cdf` lists stop at the first value that reaches 1 (readers treat everything after as 1).
- **The band** is centred on the rounded projection (amounts: P50; counts: the mean) and counts the whole numbers within the half-width: `F(c + b) - F(c - b - 1)`.
- **PIT** is the expected histogram of the randomized PIT (each whole-number outcome spreads uniformly over `(F(y-1), F(y)]`; no random draws); `pit_ece` is the mean absolute deviation of the 10 bins from 10%.
- **Own history** is his last 16 results at most, 6 at least, else the bell curve (about 89% of the reported rows have at least 6).
- **Ship rule applied as written;** "4 of 5 seasons" is `ceil(0.8 x seasons)`, so a different window needs the same share.
- **Kickoff rule (the lead's, for the weekly command):** `run_predict` never re-makes a curve whose game has kicked off. Curves already in the week's file for such a game stay exactly as they are (`games_kept` in `curves.json`); a started game with none gets curves anyway and is listed in `games_after_kickoff`. `curves.json` also holds `made_at`, `rows`, `games`, `version`, `shipped`, `source_version` / `source_created_at` (the player model's), `rows_by_target`, `history_rows` and `through`.
- **Review fixes (2026-11-14):** (1) `run_predict` always writes `actual` / `played` as null on the curves it makes (whatever the projection file holds); a started game's saved rows are kept exactly as they are, results included. `games_after_kickoff` is read off the rows (`created_at >= kickoff_utc`), so a game made late stays flagged on every later run. `run_grade` fills results for every row it can but leaves the games of `games_after_kickoff` out of the scoreboard rows (summary: `excluded_after_kickoff` = graded rows left out, `scored` = rows scored, `games_after_kickoff`). (2) A week can be graded in pieces: what the source does not know stays null (`summary["waiting"]` = rows still null), a result already filled is kept, and a later grade fills the rest and replaces the week's scoreboard rows. (3) `atomic_json` uses `fsutil.replace_file` (retried); `run_predict` and `run_grade` read, merge and write the week's `curves.parquet` under `file_lock`. (4) `curves.json` `through` is the newest (season, week) the history really holds (null when it holds nothing), e.g. `2025-w18` in week 1.
- **W&B runs:** addendum: tuning-window comparison `w6beygoe`, amount re-run `6glg980f` (promoted `2021-2025_20261114T223208Z`), week-10 predict `szulddac`; first build: smoke `cisoa4b6` (group `forecast-lab-smoke`), tuning `6hpvwrnb`, reported `rai6vnyq` (which also promoted and logged the `curves-model` artifact, aliases version + `production`), and one `curves-predict` run for 2026 week 10 (`59nmn18u`). Live `bt/*` curves per target (cumulative CRPS and Brier of the curve, the bell curve and the own-history curve, one point per scored week; `bt/step` counts them across targets); tables for pooled, by-season, verdict, PIT and the marks' reliability (the tuning run: every candidate too); the charts `reliability/<target>` (chance of going over a mark, 10 bins, vs how often it did) and `crps_by_season/<target>`.
- **Versions after the addendum:** production is `2021-2025_20261114T223208Z` (the amount-only re-run). `production.json` and `params.json` carry all ten targets: the five amount targets with `analog_zero`, the five count targets with the parameters, backtest numbers and shipped status of the first run (`2021-2025_20261114T220939Z`, W&B `rai6vnyq`). The version string is the latest promotion; `sources` (target to the version whose backtest produced its parameters and verdict) and `wandb_runs` (target to the W&B run) say which target comes from which run. The `curves-model` artifact (aliases version + `production`) holds the new folder; its copy of `params.json` was uploaded before the count targets' run URLs were added to `wandb_runs` (a one-line bookkeeping fix, on disk now).
- **Research folders:** `forecastlab/backtests/curves/_tuning/summary.json` (every candidate's scores) and `_smoke/<target>/` hold the tuning and smoke runs (and `_tuning/zero_variant.json` the addendum's comparison; `_reported_first_run/<amount target>/` the first reported run's files, which the re-run overwrote in `<target key>/`), so they never overwrite a canonical `backtests/curves/<target key>/`.
- **Written on the real data root during the build:** the backtests, the production pointer and model folder, and `forecastlab/2026/week10/curves.parquet` + `curves.json` (the real week-10 curves, made 2026-11-14 22:10 UTC from the saved week-10 projections and re-made at 22:33 UTC with the version above). The live-grade check of weeks 4-9 wrote only to a scratch folder.
- **Speed:** the reported backtest of all ten targets, 27 s; the tuning run (about 280 candidate-target pairs), 82 s; `run_predict` for 2026 week 10 (1,381 curves, 13 games), 1.1 s of work, about 17 s with W&B.
- **Left for later:** the model card; a tuning window that can tell "all earlier seasons" from "the last 4" (the pre-season retune). The digest never reads these files (D158).
