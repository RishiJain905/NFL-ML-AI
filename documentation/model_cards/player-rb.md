# Model card: player model v1, running backs (P06)

**Family:** Track 1 model C ([04 → C](../04-track1-models.md#c-player-model)) · **Targets:** rushing yards, scrimmage yards, carries, receptions · **Chosen:** 2026-10-04 (D64 design; D68 settings and ship decision, ✋ Rishi) · **Production:** the weekly refit, W&B artifact `player-model:<season>-w<NN>` (all 11 targets in one artifact; promotion is described in the overview) · **Last tuned:** 2026-10-04 · **Reported window:** walk-forward 2019–2025

**Overview:** [Player model v1](player-model-v1.md) (all 11 targets, the scoreboard, the digest) · **Guide:** [Player projections guide](../guides/player-projections.md) · **Decisions:** D64 (design), D65 (counts vs the median), D66 (the Friday view), D68 (tuning and the ship decision)

**Code:** `features/player_data.py` (history), `features/player.py` (rows, `own_*` / `use_*` / `team_*` / `rip_*` / `avail_*`, baselines), `features/player_efficiency.py` (`eff_*`), `features/player_opponent.py` (`opp_*`), `models/player_model.py` (the LightGBM models, ranges, SHAP), `models/player_schema.py` (targets and pools), `models/player_runs.py` (backtest, tuning, weekly fit, scoreboard) · **Tables:** `features/player_features.parquet` (`nfl features player`), `runs/backtests/player/<target>-<group>/` (`nfl backtest player`), `runs/<season>/week<NN>/predictions_players.parquet` (`nfl train player`)

## What it is

Four models for the running backs a team is expected to use in a game:

| Target | Kind | Unit | What it is | Role |
|---|---|---|---|---|
| **Rushing yards** (`rush_yds`) | amount | yards | His rushing yards in the game | The **main stat** for running backs: it ranks the RBs on the watch list |
| **Scrimmage yards** (`scrim_yds`) | amount | yards | Rushing plus receiving yards | The whole-workload view (a pass-catching back's rushing number understates his day) |
| **Carries** (`carries`) | count | carries | His rushing attempts | Pure usage: the part of a back's day his coaches decide |
| **Receptions** (`receptions`) | count | catches | His catches | Receiving usage and efficiency together |

**Who is scored (the pool).** Running backs (halfbacks; fullbacks are not modeled) who took **at least one offensive snap** in the game. A back who is inactive or never gets on the field is not a row.
**Who is projected live:** players on the team's roster who played in one of its last 3 games, or who are back on the week's active roster after 3+ games for the team (this or last season) at an average snap share of 40% or more
(a returner from injured reserve), and are not listed Out, Doubtful or on a reserve list. A projected back who then doesn't play is logged "not played" and not graded.
Backtests contain only backs who did play: 10,382 RB games over 2019–2025 (1,418–1,542 a season).

**What the numbers look like.** The typical RB game is small and skewed: rushing yards average 33.7 with a median of 22 (a standard deviation of 36), and 16% of the rows are zero;
carries average 7.8 (median 6); receptions average 1.66 with a median of 1 and 35% zeros.

## How it works, in plain language

**An amount (yards, EPA) is a range, not one number.** Three LightGBM models (decision-tree ensembles) are fitted to the same history, each with a *quantile* objective:
one learns the 10th percentile ("a bad day"), one the median ("a typical day": the **projection**, P50) and one the 90th ("a big day"). P10–P90 is an
**80% range**: about 8 games in 10 should land inside it.

**The conformal shift.** Quantile models are rarely calibrated out of the box, so the range is checked against the model's own earlier misses. At every refit, the
last two seasons of walk-forward predictions are scored: how far outside its range was each actual value (or how far inside)? The 80th percentile of that distance
moves both ends of the range by the same amount (a *conformal* correction), so the range holds about 80% of outcomes. A positive shift widens the range, a negative
one narrows it. It is fitted on predictions made before each week, never on the week being predicted.

**A count (carries, targets, tackles, pressures) is a distribution.** A LightGBM model with a *Poisson* objective learns the **expected value** (the mean). Real counts
vary more than a Poisson says, so the outcome is modeled as a **negative binomial** with that mean and a dispersion `r` fitted on the model's own earlier misses
(`Var = mean + mean² / r`; a smaller `r` means a wider spread, a very large `r` means plain Poisson). The **projection (P50) is the median** of that distribution, and the
**80% range** runs between its low and high tail quantiles. Counts are whole numbers, so quantiles at exactly 10% / 90% over-cover (88% of tackles in the smoke run),
and the tail level (between 0.10 and 0.25) is picked on the same earlier misses so the range holds about 80%. The number a reader sees as "the projection" is the **mean**;
the scoreboard grades the **median** against the baseline's median (see "Is this number good?").

The conformal shift is small for the RB yardage models (rushing yards 1.4 yards in 2019, 0.7 in 2025; scrimmage yards
2.7 and 1.2): the raw quantile ranges were already close. The negative-binomial dispersion `r` is
6.4 → 7.7 for carries and 3.8 → 7.9 for receptions (receptions are noisier relative to their mean, so a smaller `r`).

**The baseline it has to beat (D64, `documentation/11`).** *Player rolling*: half his average over his last 4 games this season, half his season-to-date average, pulled toward
last season's average with 3 pseudo-games (so early in the year last season counts a lot). No game yet this season: last season's average. No history at all (a rookie): the
league average for players with his snap share in their previous game, taken from earlier seasons only. `outperformance` = projection − baseline.

For running backs: 90.4% of rows use the rolling mix, 7.3% last season's average, 2.3% the role average
(a rookie or a back with no prior game).

**What goes in (126 features, hash `65103c42f0`, the same list for all 11 models).** Seven families, each computed only from games *before* the week being predicted
(PFR and FTN data one week later, because they publish about a week late, D44):
his own recent history (`own_*`: last game, last 4, season to date, last season, an exponentially weighted mean);
usage (`use_*`: snap share, target / carry / dropback / red-zone shares);
team context (`team_*`: pass rate over expected, pace, team ratings, our game model's expected margin and points, and the betting market's implied team points, spread and total);
ripple effects (`rip_*`: usage left open by teammates who missed the last game or are ruled out this week, the expected QB's history with the player);
availability (`avail_*`: his own injury-report status and practice level for the week);
efficiency (`eff_*`: yards per target or carry, EPA, Next Gen Stats, PFR, FTN charting);
opponent (`opp_*`: what the opposing defense or offense has allowed or done lately, adjusted for who it faced).
Three week-N inputs are used on purpose: the week's own injury report (the "Friday view", D66), the betting market's line for the game itself, and our own game model's pre-game predictions and expected starter for it (produced before kickoff). Everything else is Tuesday-as-of.
Training uses every regular-season game from 2013 on (snap counts begin in 2013), current-season games weighted 3× and last season's 1.5× (D28), and the model is refit
every week on all earlier games.

**Tuned settings (D68).**

| Model | `num_leaves` | `min_data_in_leaf` | `n_estimators` | W&B sweep (12-point grid) |
|---|---|---|---|---|
| `rush_yds-rb` | 31 | 200 | 150 | [glu9cmj8](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/glu9cmj8) |
| `scrim_yds-rb` | 31 | 200 | 150 | [ywgzufw3](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/ywgzufw3) |
| `carries-rb` | 15 | 50 | 300 | [4yqdpr6c](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/4yqdpr6c) |
| `receptions-rb` | 15 | 50 | 300 | [0z30t3eb](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/0z30t3eb) |

The yardage models like larger trees (31 leaves, 200 rows per leaf, 150 rounds); the two count models like many small-leaf rounds (15 leaves, 50 rows per leaf, 300 rounds). The grid is flat, so none of this is fragile.

**Tuning (D68).** A 12-point grid per target (`num_leaves` 7 / 15 / 31 × `min_data_in_leaf` 50 / 200 × `n_estimators` 150 / 300, learning rate 0.05), scored by a walk-forward over
every other week of **2017–2018 only** (pressures: late 2018, its first PFR season), so none of the reported 2019–2025 weeks chose a setting. The grid is flat (best to worst
differs by 0.9–3.1% in MAE), so tuning changed little. Defaults that were not tuned: learning rate 0.05, feature fraction 0.8, bagging fraction 0.8 (every iteration), L2 penalty 1.0, seed 7.

## Results: walk-forward 2019–2025

Every week was projected by a model fitted only on earlier weeks (two burn-in seasons, 2017–2018, calibrate the range). Model and baseline are scored on exactly the same rows. For counts the baseline is its **median** (D65, explained below).
"Rank skill" is the rank correlation between *projection − baseline* and *actual − baseline*.

| Target | Rows scored | MAE model | MAE baseline | Better than baseline | vs season-to-date mean | Range coverage | Rank skill | Seasons beating baseline | No-market variant |
|---|---|---|---|---|---|---|---|---|---|
| **rushing yards** (yards) | 10,382 | 19.0 | 20.7 | **+7.9%** | +7.5% | 80.5% | 0.31 | 7 of 7 | +8.1% |
| **scrimmage yards** (yards) | 10,382 | 23.5 | 25.3 | **+7.4%** | +7.4% | 80.5% | 0.30 | 7 of 7 | +7.3% |
| **carries** (count) | 10,382 | 3.390 | 3.643 (median) | **+6.9%** | +7.6% | 79.6% | 0.36 | 7 of 7 | +6.9% |
| **receptions** (count) | 10,382 | 1.090 | 1.121 (median) | **+2.7%** | +9.8% | 81.1% | 0.28 | 7 of 7 | +2.3% |

*For counts the "vs season-to-date mean" column compares the model's median with a mean, the comparison D65 warns about, so it flatters counts. Use "Better than baseline".*

- **All four RB targets ship (D68):** each beats its baseline pooled and in every one of the 7 seasons, with 79.6%–81.1% range coverage.
- **Market-free check (research, closing-line rule 5 of `documentation/04`).** With the three `team_mkt_*` features removed (123 features): rushing yards +8.1% (the market-free model is 0.2 points better), scrimmage yards +7.3% (the market features add 0.1 points),
  carries +6.9% (within 0.05 points either way), receptions +2.3% (the market features add 0.4 points). Market features are rarely top-3 drivers for running backs (see below), so a small change is expected.
- **First round vs final.** Round 1 (before tuning, before D65; counts then measured against the raw rolling mean) vs now:

| Target | First round (pre-tuning, pre-D65) | Final, vs the raw rolling mean | Final, vs the baseline used for shipping |
|---|---|---|---|
| rushing yards | +8.1% | +7.9% | +7.9% |
| carries | +8.4% (vs raw mean) | +8.3% | +6.9% (median) |
| receptions | +8.4% (vs raw mean) | +8.4% | +2.7% (median) |
| scrimmage yards | not recorded | +7.4% | +7.4% |

The counts' raw-mean numbers barely moved with tuning; what changed is the yardstick. Against the baseline's median, carries is +6.9% and receptions +2.7%.

**A second yardstick for counts: is the expected value better?** The ship rule compares the projection's *median* with the baseline's median (D65), which is a hard bar on zero-heavy counts.
But the number a reader sees is the model's **mean**, and the proper score for a mean is the **Poisson deviance** (lower is better; it rewards a mean close to the real rate, not a guess at the commonest value).
Limited to rows where the baseline is at least 0.25 (so a near-zero baseline can't inflate its own score; the share of rows kept is in the table), the model's mean beats the baseline's mean on every count target, and in every one of the 7 seasons:

| Target | Rows used (share of all) | Deviance, model mean | Deviance, baseline mean | Better | Range over the 7 seasons |
|---|---|---|---|---|---|
| carries | 10,085 (97%) | 3.069 | 3.581 | **+14.3%** | +12.2% to +17.0% |
| receptions | 9,648 (93%) | 1.499 | 1.598 | **+6.2%** | +5.1% to +7.6% |

This is a research view, not the ship rule, and its percentages are not comparable with the MAE gains above. It shows that the MAE-against-the-median gap understates how much closer the model's expected value is to the real rates.

**Rushing yards by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 1,418 | 19.2 | 20.7 | +7.3% | 80.6% |
| 2020 | 1,479 | 19.5 | 21.0 | +7.5% | 80.1% |
| 2021 | 1,490 | 19.3 | 21.1 | +8.5% | 79.6% |
| 2022 | 1,542 | 19.1 | 21.0 | +8.9% | 81.3% |
| 2023 | 1,476 | 18.0 | 19.8 | +9.4% | 81.0% |
| 2024 | 1,492 | 19.5 | 20.7 | +6.2% | 78.7% |
| 2025 | 1,485 | 18.8 | 20.4 | +7.6% | 82.2% |
| **2019–2025** | **10,382** | **19.0** | **20.7** | **+7.9%** | **80.5%** |

**Scrimmage yards by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 1,418 | 25.0 | 26.9 | +7.0% | 80.4% |
| 2020 | 1,479 | 23.8 | 25.5 | +6.9% | 80.8% |
| 2021 | 1,490 | 24.3 | 26.2 | +7.0% | 79.9% |
| 2022 | 1,542 | 23.7 | 25.8 | +8.2% | 80.4% |
| 2023 | 1,476 | 21.9 | 24.3 | +9.7% | 81.2% |
| 2024 | 1,492 | 22.7 | 24.3 | +6.5% | 79.1% |
| 2025 | 1,485 | 22.8 | 24.4 | +6.8% | 81.5% |
| **2019–2025** | **10,382** | **23.5** | **25.3** | **+7.4%** | **80.5%** |

**Carries by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 1,418 | 3.241 | 3.469 | +6.6% | 81.7% |
| 2020 | 1,479 | 3.512 | 3.763 | +6.7% | 76.3% |
| 2021 | 1,490 | 3.664 | 3.948 | +7.2% | 77.5% |
| 2022 | 1,542 | 3.384 | 3.591 | +5.8% | 81.1% |
| 2023 | 1,476 | 3.336 | 3.671 | +9.1% | 80.7% |
| 2024 | 1,492 | 3.387 | 3.614 | +6.3% | 79.2% |
| 2025 | 1,485 | 3.200 | 3.435 | +6.8% | 80.5% |
| **2019–2025** | **10,382** | **3.390** | **3.643** | **+6.9%** | **79.6%** |

**Receptions by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 1,418 | 1.197 | 1.215 | +1.5% | 81.0% |
| 2020 | 1,479 | 1.112 | 1.122 | +0.9% | 82.1% |
| 2021 | 1,490 | 1.195 | 1.236 | +3.4% | 79.7% |
| 2022 | 1,542 | 1.079 | 1.111 | +3.0% | 81.5% |
| 2023 | 1,476 | 1.076 | 1.112 | +3.2% | 81.8% |
| 2024 | 1,492 | 0.997 | 1.030 | +3.2% | 81.6% |
| 2025 | 1,485 | 0.982 | 1.024 | +4.1% | 79.7% |
| **2019–2025** | **10,382** | **1.090** | **1.121** | **+2.7%** | **81.1%** |

By season: rushing yards: weakest 2024 (+6.2%), strongest 2023 (+9.4%); scrimmage yards: weakest 2024 (+6.5%), strongest 2023 (+9.7%); carries: weakest 2022 (+5.8%), strongest 2023 (+9.1%); receptions: weakest 2020 (+0.9%), strongest 2025 (+4.1%). With 1,418–1,542 games a season, a season's gain moves by about a point from sampling alone
(a little more for the low-gain targets), so read the seven seasons together.

## Is this number good?

**Yes for yardage and carries; receptions is the weak spot.** `documentation/11` calls a 5–15% gain over a good rolling average a solid result for player yardage, with usage-driven counts the most predictable.

- **Rushing yards (+7.9%) and scrimmage yards (+7.4%) sit in the band.** The model misses a typical back by 19.0 rushing yards against the baseline's 20.7. The rest is what no Tuesday model sees:
  game script, a goal-line vulture, a fumble, an in-game injury.
- **Carries (+6.9%) is measured against a hard bar.** A count's projection is a median, and a median beats *any* mean on MAE for skewed, zero-heavy stats all by itself (D65). Against the baseline's own median,
  carries is +6.9%; against the raw rolling mean it would read +8.3%, about the Round-1 number (+8.4%). We report the like-for-like one.
- **Receptions (+2.7%) barely beats its baseline,** and for a reason in the data: the median baseline already beats the raw mean by 5.8% (it predicts the common 0–1 receptions), so the model's
  +8.4% over the raw mean shrinks to +2.7% on a fair comparison. It still wins every season and 68% of the weeks. Treat the receptions projection as a modest refinement, not a strong call.
  It is the model's standing weak spot on the scoreboard.
- **Consistency.** Weeks beaten: rushing yards 94%, scrimmage yards 92%, carries 90%, receptions 68% (of 124).
- **The ordering matters more than the level.** Among backs with a real role (snap share ≥ 50%), the top 10% by `outperf_z` beat their baseline **66.0%** of the time for rushing yards (base rate 45.2%),
  64.2% for scrimmage yards (base 46.1%), 73.8% for carries (base 49.8%) and 57.0% for receptions (base 43.6%).
  The digest's actual picks (the few biggest jumps per week, with caps, D69) hit 67.7% against a 44.7% base rate for running backs (see the [overview](player-model-v1.md)). That is the property the "players to watch" list uses.

## Where the gain comes from

*By RB games already played this season: better than baseline (rows)*

| Target | 0 games this season | 1-2 games | 3-7 games | 8+ games |
|---|---|---|---|---|
| rushing yards | +23.9% (1,071) | +9.0% (1,868) | +6.2% (3,754) | +4.5% (3,689) |
| scrimmage yards | +22.6% (1,071) | +7.6% (1,868) | +5.9% (3,754) | +4.4% (3,689) |
| carries | +14.7% (1,071) | +5.8% (1,868) | +6.6% (3,754) | +5.5% (3,689) |
| receptions | +7.1% (1,071) | +4.1% (1,868) | +3.1% (3,754) | +0.6% (3,689) |

*By RB career games: better than baseline (rows)*

| Target | <3 career games | 3-16 | 17+ |
|---|---|---|---|
| rushing yards | +27.7% (669) | +7.4% (2,392) | +6.3% (7,321) |
| scrimmage yards | +27.3% (669) | +6.4% (2,392) | +5.9% (7,321) |
| carries | +21.1% (669) | +5.7% (2,392) | +5.9% (7,321) |
| receptions | +13.2% (669) | +2.8% (2,392) | +2.0% (7,321) |

*By what the baseline rests on: better than baseline (rows)*

| Target | rolling | last_season | role |
|---|---|---|---|
| rushing yards | +6.1% (9,385) | +13.5% (755) | +48.8% (242) |
| scrimmage yards | +5.7% (9,385) | +10.8% (755) | +48.7% (242) |
| carries | +6.0% (9,385) | +2.4% (755) | +38.6% (242) |
| receptions | +2.3% (9,385) | +2.4% (755) | +22.1% (242) |

- **Most of the headline gain sits in the first game of a season and among new backs.** Before a back's first game of the season (baseline = last year's average, or the role average) rushing yards is +23.9% better;
  for backs with fewer than 3 career games +27.7%; on the role-average baseline +48.8% (n = 242).
  Those baselines are weak, and the model's team context and usage fill the gap.
- **For an established back mid-season the gain is smaller and honest:** +6.2% with 3–7 games played and +4.5% with 8+ for rushing yards;
  +0.6% for receptions. The longer the record, the better the plain average, and the fewer yards the model can add.
- **Weeks 1–4 versus later:** rushing yards +11.2% in weeks 1–4, +7.2% in weeks 5–9, +6.6% from week 10.
- Home and away are alike (+7.7% home, +8.2% away for rushing yards).

## Calibration

| Target | 0 games this season | 1-2 games | 3-7 games | 8+ games |
|---|---|---|---|---|
| rushing yards | 83.7% | 83.0% | 80.4% | 78.4% |
| scrimmage yards | 84.7% | 81.4% | 80.8% | 78.4% |
| carries | 69.4% | 78.9% | 81.2% | 81.2% |
| receptions | 82.9% | 81.5% | 81.5% | 79.9% |

*(Coverage of the P10–P90 range by RB games already played this season.)*

- **Pooled coverage** is 80.5% (rushing yards), 80.5% (scrimmage yards), 79.6% (carries) and 81.1% (receptions), against a target of 80%.
  By season: rushing yards 78.7%–82.2%, carries 76.3%–81.7%.
- **The tails are lopsided for yardage and receptions, in the direction that matters:** 7.2% of actual rushing yards fall below P10 but 12.3% above P90 (scrimmage 7.4% / 12.1%;
  receptions 4.4% / 14.5%). Big games are bigger than a symmetric range allows, and the floor can't go below zero. Read the high end of an RB range as "a big game is possible", not "this is the ceiling".
- **For the yardage models the pattern runs the other way:** first games of the season over-cover (83.7% for rushing yards) and games late in the season under-cover
  (78.4% with 8+ games played): the conformal shift is one number for the whole week, so it can't tell veterans from first-game rows.
- **Count ranges under-cover the thinnest-history rows.** Carries covers only 69.4% for backs with no game yet this season, 65.6% for those with under 3 career games and
  60.3% on the role baseline (n = 242); the confidence label `low` covers 69.4%. The dispersion is fitted on all rows, so rookies and first games,
  whose usage is genuinely uncertain, get ranges that are too narrow. Treat a "low confidence" RB carries range as wider than printed.

## What drives the projections

How this was measured: for every played 2025 RB row (1,485 rows) I counted how often each feature appears among the **top 3 SHAP drivers** of the projection (LightGBM's own `pred_contrib`; for counts on the log scale,
converted to count units with a first-order factor). It shows what the model leaned on in 2025; it is not an average of |SHAP| over all rows (that bar chart, `shap_summary`, is in each W&B run).

**Rushing yards**

| Feature (plain wording) | Internal name | In the top 3 of 2025 projections | Was the #1 driver |
|---|---|---|---|
| his recent form (recent games weighted most) | `own_ewm` | 91% | 995 |
| his share of the team's carries lately | `use_carry_share_l4` | 90% | 375 |
| his snap share in his last game | `use_snap_l1` | 52% | 70 |
| his share of the team's carries this season | `use_carry_share_std` | 42% | 10 |
| his output in his last game | `own_l1` | 5% | 3 |
| carries left open by teammates ruled out this week | `rip_out_car` | 5% | 29 |
| his average in recent games this season | `own_l4_season` | 4% | 2 |
| his carries per game lately | `use_carries_l4` | 3% | 0 |

Shares of all top-3 slots by family: usage (`use_*`) 63%, his own recent history (`own_*`) 34%, ripple effects (`rip_*`) 2%.

**Carries**

| Feature (plain wording) | Internal name | In the top 3 of 2025 projections | Was the #1 driver |
|---|---|---|---|
| his recent form (recent games weighted most) | `own_ewm` | 95% | 447 |
| his share of the team's carries lately | `use_carry_share_l4` | 84% | 879 |
| his output in his last game | `own_l1` | 54% | 71 |
| his share of the team's carries this season | `use_carry_share_std` | 34% | 14 |
| his snap share in his last game | `use_snap_l1` | 8% | 14 |
| carries left open by teammates ruled out this week | `rip_out_car` | 5% | 52 |
| his carries per game lately | `use_carries_l4` | 4% | 1 |

Shares of all top-3 slots by family: his own recent history (`own_*`) 50%, usage (`use_*`) 45%, ripple effects (`rip_*`) 2%, team context incl. market lines (`team_*`) 1%.

**Receptions**

| Feature (plain wording) | Internal name | In the top 3 of 2025 projections | Was the #1 driver |
|---|---|---|---|
| his recent form (recent games weighted most) | `own_ewm` | 95% | 1,212 |
| his snap share in his last game | `use_snap_l1` | 88% | 230 |
| his share of targets in games with this week's quarterback | `rip_qb_tgt_share` | 32% | 7 |
| his share of the team's targets this season | `use_tgt_share_std` | 23% | 1 |
| how often the opponent blitzes (charting data) | `opp_def_blitz_rate_ftn_l8` | 16% | 4 |
| his targets per game lately | `use_targets_l4` | 8% | 0 |
| his snap share in his last couple of games | `use_snap_l2` | 7% | 0 |
| how early in the season it is (ratings still lean on last season) | `team_prior_weight` | 6% | 4 |
| carries left open by teammates ruled out this week | `rip_out_car` | 5% | 22 |

Shares of all top-3 slots by family: usage (`use_*`) 43%, his own recent history (`own_*`) 33%, ripple effects (`rip_*`) 13%, the opponent (`opp_*`) 7%, team context incl. market lines (`team_*`) 2%, efficiency (`eff_*`) 2%.

**Scrimmage yards:** recent form (`own_ewm`, 91% of rows), his recent average (`own_l4`) and last-game snap share lead; shares by family: his own recent history (`own_*`) 65%, usage (`use_*`) 31%, ripple effects (`rip_*`) 2%, team context incl. market lines (`team_*`) 1%.

- **Usage and recent form drive running-back projections, almost entirely.** `own_ewm` (his recent form) is a top-3 driver in 90–95% of rows for all four models, and the carry share in 90% (rushing yards) and 84% (carries). Opponent features are top-3
  drivers in only 2.8% (rushing yards), 0.4% (carries) and 1.6% (scrimmage yards) of rows, and market and team features almost never
  (0.2% of rushing-yards slots).
- **Receptions are the exception:** 19% of rows have an opponent driver, mostly how often the defense blitzes (FTN charting, 2022 on; 16% of rows, always pulling the projection down),
  and 32% have his share of targets with this week's quarterback.
- **A few features act as proxies.** `team_prior_weight` ("how early in the season it is": the team ratings still lean on last season) is a top-3 driver in a few percent of count rows, always adding a little: it marks the early weeks.
  Do not read it as a football effect.

## Football sense-check

For each fifth of a feature's values (lowest to highest), the average **projected gap** (model P50, or the mean for counts, minus his baseline) and the average **real gap** (actual minus his baseline) as `projected / real`,
for all 2019–2025 RB rows. The gaps' levels carry the median-versus-mean offset; read the change across the fifths. A feature the model uses well shows a projected change in the same direction, and about the same size, as the real one.

**Rushing yards** (yards)

| Feature | What it measures | Lowest fifth | 2nd | 3rd | 4th | Highest fifth |
|---|---|---|---|---|---|---|
| `opp_rush_yds_allowed_rb_oe_l8` | rushing yards the opponent allows to RBs beyond what those offenses usually gain | −9.2 / −4.5 | −7.3 / −1.4 | −5.7 / +0.3 | −4.6 / +0.5 | −3.8 / +2.6 |
| `opp_def_rush_epa` | opponent's run-defense rating (EPA allowed; higher = weaker) | −8.9 / −3.3 | −7.1 / −2.1 | −5.5 / −0.7 | −5.0 / +1.1 | −4.1 / +2.5 |
| `use_carry_share_l4` | his share of the team's carries over his last 4 | −3.3 / +1.7 | −5.1 / +0.7 | −6.2 / −0.9 | −6.7 / −0.5 | −6.9 / −1.3 |
| `team_mkt_spread` | market spread for his team (higher = bigger favorite) | −6.3 / −1.0 | −6.5 / −2.2 | −6.6 / −0.2 | −6.3 / −0.2 | −5.1 / +0.9 |
| `eff_stacked_box_pct_l4` | how often he ran against 8+ defenders in the box lately | −5.1 / +0.6 | −6.1 / −1.8 | −6.2 / +0.3 | −7.1 / −2.1 | −7.0 / −0.1 |

**Carries and receptions** (counts)

| Feature | What it measures | Lowest fifth | 2nd | 3rd | 4th | Highest fifth |
|---|---|---|---|---|---|---|
| `opp_carries_allowed_rb_oe_l8 (carries)` | carries the opponent allows to RBs beyond normal | −0.45 / −0.48 | −0.34 / −0.35 | −0.13 / −0.28 | +0.12 / +0.20 | +0.31 / +0.46 |
| `team_exp_margin (carries)` | our game model's expected margin for his team | −0.17 / −0.09 | −0.18 / −0.21 | −0.14 / −0.07 | −0.14 / −0.01 | +0.14 / −0.06 |
| `opp_targets_allowed_rb_oe_l8 (receptions)` | targets the opponent allows to RBs beyond normal | −0.18 / −0.17 | −0.12 / −0.14 | −0.05 / −0.02 | 0.00 / +0.01 | +0.08 / +0.10 |
| `opp_def_blitz_rate_ftn_l8 (receptions)` | how often the opponent blitzes (FTN, 2022 on) | +0.04 / +0.07 | −0.01 / +0.02 | −0.06 / −0.09 | −0.09 / −0.10 | −0.10 / −0.11 |
| `rip_qb_tgt_share (receptions)` | his share of targets in games with this week's QB | +0.16 / +0.15 | +0.10 / +0.09 | +0.01 / +0.07 | −0.12 / −0.13 | −0.30 / −0.26 |

- **The opponent is read in the right direction, at about three quarters of the real size.** From the weakest to the strongest run defenses (by rushing yards allowed to backs beyond normal) the projection moves by +5.4 yards and
  the real gap by +7.1; by the opponent's run-defense rating +4.8 vs +5.8. For carries the opponent's tendency to let backs carry the ball moves the projection +0.76 carries and reality +0.94;
  for receptions the targets a defense gives backs: +0.26 vs +0.28. These are small effects next to a back's own workload, which is why the opponent seldom reaches a top 3.
- **Blitzing defenses cut a back's receptions** (more blitzes keep backs in to block): projected −0.14 vs real −0.19 from the lowest to the highest blitz fifth.
- **Workload regresses to the mean.** From the lowest to the highest fifth of recent carry share the projected gap changes by −3.6 yards and the real gap by −3.0: the baseline over-reacts to a heavy stretch, and the model corrects it.
  Likewise a high share of targets with this QB (`rip_qb_tgt_share`) predicts fewer receptions than the baseline (lowest to highest fifth: −0.46 projected, −0.41 real).
- **A teammate ruled out opens carries, and the model sees it:**

| Situation | What it means | Share of rows | Model: projected gap vs rows without it | Real gap vs rows without it |
|---|---|---|---|---|
| A teammate RB ruled out (rushing yards) | a regular RB on the team is listed Out or Doubtful this week | 10% | +5.1 | +8.1 |
| A teammate RB ruled out (scrimmage yards) | same | 10% | +6.4 | +10.6 |
| A teammate RB ruled out (carries) | same | 10% | +1.39 | +1.98 |
| A regular RB missed the last game (rushing yards) | recently absent regular: his carries are still up for grabs | 25% | +2.9 | +2.9 |
| A regular RB missed the last game (carries) | same | 25% | +0.69 | +0.72 |

  The projection captures about two thirds of the real jump when a teammate is ruled out (the report is the Friday view, D66) and all of it when a regular merely missed the last game.
- **The market spread barely matters for backs** (favorites run slightly more: a fifth-to-fifth change of +1.3 yards projected, +1.9 real).

## Known biases and limits

- **Closing-line market features.** `team_mkt_*` are the *closing* lines in every backtest; a live Tuesday run sees earlier lines. For backs they are almost never top-3 drivers, and removing them changes little:
  rushing yards: the market-free model is 0.2 points better; scrimmage yards: the market features add 0.1 points; carries: within 0.05 points either way; receptions: the market features add 0.4 points.
- **The Friday view (D66).** `avail_*` and `rip_out_*` read the week's own injury report (a teammate ruled out is a top-3 driver in about 5% of rows). Backtests use the final report; a live run uses whatever the snapshot has,
  and a Tuesday run sees nobody listed. The weekly run must happen late in the week to be like the backtest.
- **Thin-history ranges are too narrow for counts** (see Calibration): rookies, first games of the season and role-baseline rows.
- **Committees and game script.** Usage can swing on a coach's decision or a blowout; the model sees the market spread and our game model's expected margin but they hardly move a back's projection. Goal-line and touchdown chances are not modeled here (P08).
- **Pool and rows.** Only backs with an offensive snap; fullbacks and quarterbacks' rushing are not in this model (QB rushing is P08). Backs with tiny roles are in the pool and pull the zero share up (16% of rows have 0 rushing yards).
- **Receptions are weak** (see above); `team_prior_weight` shows the model compensating for early-season rating uncertainty with a proxy.
- **Short-history features.** FTN charting (`opp_def_blitz_rate_ftn_l8`) starts in 2022 and PFR-based efficiency in 2018, each a week late (D44), so the model learned them from fewer seasons.
- **Sampling noise.** 1,418–1,542 games a season; a one-point swing in a single season is noise, so only the pooled and seven-season picture is claimed.
- **No live weeks scored yet.** The numbers above are walk-forward backtests; the first live scoreboard rows arrive with the first weekly run's scoring step.

## Reading the W&B charts

All player-model runs are in W&B group **`track1-player`**, tagged `p06`, `player`, `group:<qb|rb|wrte|edge|lbs>` and `target:<name>`. Open a backtest run: the **Charts** tab holds the
curves below, the **Overview** tab's summary holds the pooled numbers, and the tables and bar charts logged at the end are in the run's media / tables panels.

**The metrics in one minute**

| Metric | What it measures | Better |
|---|---|---|
| **MAE** (mean absolute error) | Average miss of the projection (P50) in the target's own unit | Lower |
| **Better than baseline** (`improvement_pct`) | 100 × (baseline MAE − model MAE) / baseline MAE, on exactly the same rows | Higher; ≥0 in every season is the ship bar (D68) |
| **Range coverage** (`coverage_80`) | Share of actual values between P10 and P90 | Close to 80% (the ship bar is 72–88%) |
| **Rank skill** (`spearman_outperformance`) | Rank correlation between *projection − baseline* and *actual − baseline*: does the model order players by how far above their norm they will land? | Higher; 0 = no ordering skill |
| **`range_param`** | Amounts: the conformal shift (target units). Counts: the negative-binomial dispersion `r` | Stable over time |

**The runs**

| Target | Backtest run (`backtest-<target>-<group>`) | Tuning sweep | No-market run |
|---|---|---|---|
| rushing yards | [sbvbkqcj](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/sbvbkqcj) | [glu9cmj8](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/glu9cmj8) | [crppcfkc](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/crppcfkc) |
| scrimmage yards | [l9oko9q2](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/l9oko9q2) | [ywgzufw3](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/ywgzufw3) | [kvg3x7g1](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/kvg3x7g1) |
| carries | [sp9b4l9w](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/sp9b4l9w) | [4yqdpr6c](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/4yqdpr6c) | [ea8gjgan](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/ea8gjgan) |
| receptions | [xjgpuxqf](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/xjgpuxqf) | [0z30t3eb](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/0z30t3eb) | [ultxr45c](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/ultxr45c) |

The sweeps (`tune-<target>-<group>`, job type `tune`) hold the 12 grid points. Each point is one small run logging `tune/mae_model`, `tune/mae_baseline`, `tune/improvement_pct` and `tune/n`; on the sweep page the parallel-coordinates chart shows `num_leaves`, `min_data_in_leaf` and `n_estimators` against `tune/mae_model`. A flat picture is the expected one (the grid is flat, D68).

### Backtest runs (`backtest-<target>-rb`; job type `backtest`)

The x-axis of every `bt/*` curve is **`bt/step`**: one step per reported week (124 steps: 2019 week 1 to 2025 week 18; 17 weeks in 2019 and 2020, 18 after). `bt/season` and `bt/week` translate a step.

| Chart | What it shows | How to read it |
|---|---|---|
| `bt/cum_improvement_pct` | Better-than-baseline over **every week so far** | **The main chart.** It settles as games accumulate and its last value is the pooled result. Good: above 0 and flat or rising |
| `bt/improvement_pct` | Better-than-baseline for **that week only** | Jumpy, because one week has a limited number of backs. Read the share of weeks above 0, not single points |
| `bt/cum_coverage_80` | Share of actuals inside P10–P90 over every week so far | Should hover near 0.80; the first steps swing |
| `bt/coverage_80` | Coverage for that week | Normal weekly noise is several points either way (see the numbers table) |
| `bt/mae_model`, `bt/mae_baseline`, `bt/cum_mae_model`, `bt/cum_mae_baseline` | The raw MAEs behind the two improvement curves, in the target's unit (counts: the baseline is its median, D65) | In the cumulative chart the model line should sit under the baseline line |
| `bt/range_param` | Amounts: the conformal shift used that week (positive = range widened). Counts: the negative-binomial dispersion `r` (smaller = wider spread) | Should be stable, not drifting |
| `bt/n`, `bt/step`, `bt/season`, `bt/week` | Rows scored that week; lookups | `bt/n` is a sanity check |
| `lgb/curve_<season>` | The season's opening fit (train on everything before the previous season, hold the previous season out): the model's loss (the P50 quantile loss for the yardage models, the Poisson negative log-likelihood for the count models) on the training rows and on the held-out season, after each boosting round. The first available season's curve is logged up front, the others at the end | Training should fall steadily; the held-out line should flatten, not climb. A growing gap is overfitting. The yardage models run 150 rounds, carries and receptions 300 (fixed, no early stopping), so a held-out line that turns up late would mean too many rounds |

**End-of-run panels** (logged once, after the last week):

| Panel | What it shows | How to read it |
|---|---|---|
| `by_season` | One row per season: rows, MAEs, improvement, coverage, rank skill | The source of the by-season tables above |
| `mae_by_season` | Line chart: model MAE vs rolling-baseline MAE by season | The model line under the baseline line in all 7 seasons |
| `accuracy_scoreboard` | The weekly scoreboard rows (`season, week, target, position_group, n_scored, mae_model, mae_baseline, improvement_pct, coverage_80`, ...) for the backtest | The same table the live scoreboard fills each week |
| `feature_importance` | Bar chart: top 25 features by LightGBM **gain** in the last (2025 week 18) fit | Gain is what the trees actually split on |
| `shap_summary` | Bar chart: top 20 features by mean absolute SHAP over the 2025 rows, in target units | Compare with the driver table above |
| `predictions` | Every 2025 projection with P10 / P50 / P90, baseline, actual and confidence | For digging into a specific game |

**Run summary (Overview tab):** `n_scored`, `mae_model`, `mae_baseline`, `mae_baseline_mean` (the raw rolling mean; equal to `mae_baseline` for amounts), `coverage_80`, `improvement_pct`,
`mae_season_mean_same_rows` / `mae_model_season_mean_rows` / `improvement_vs_season_mean_pct` (the same comparison against the season-to-date mean), `spearman_outperformance`, `share_role_baseline`
(share of rows on the role baseline), `seasons_beating_baseline` out of `seasons`. The config panel lists the 126 features, `feature_hash`, `dataset_version` (pbp snapshot 2026-10-04) and `git_commit`.


**Real numbers for these four runs**

| Target | `bt/cum_improvement_pct`: season-end values 2019 → 2025 | `bt/improvement_pct`: weekly range, median, share of weeks above 0 | `bt/coverage_80`: weekly 10th–90th percentile (median) | `bt/range_param`: 2019 → 2025 | `bt/n`: rows per week |
|---|---|---|---|---|---|
| rushing yards | 7.3, 7.4, 7.8, 8.1, 8.3, 8.0, 7.9 (ends +7.9%) | −5% to +22%, median +7.8%, 94% | 0.75–0.86 (0.81) | shift 1.4 → 0.7 | 68–100 |
| scrimmage yards | 7.0, 7.0, 7.0, 7.3, 7.7, 7.5, 7.4 (ends +7.4%) | −5% to +19%, median +7.6%, 92% | 0.74–0.86 (0.81) | shift 2.7 → 1.2 | 68–100 |
| carries | 6.6, 6.6, 6.8, 6.6, 7.1, 6.9, 6.9 (ends +6.9%) | −3% to +17%, median +7.4%, 90% | 0.74–0.85 (0.79) | r 6.44 → 7.69 | 68–100 |
| receptions | 1.5, 1.2, 2.0, 2.2, 2.4, 2.5, 2.7 (ends +2.7%) | −14% to +22%, median +2.7%, 68% | 0.75–0.86 (0.82) | r 3.81 → 7.95 | 68–100 |

How to use it: carries and rushing yards beat their baseline in about nine weeks out of ten, receptions in about two out of three, which is exactly what the pooled numbers say (receptions is the thin one). A weekly value of
−5% to +20% on 68–100 backs is normal. Compare the `bt/cum_improvement_pct` lines of the four runs: rushing yards and scrimmage yards sit close together (they share most of their information), receptions stays far below.

## Code path and training history

The formulas and worked examples (baseline, pinball loss, conformal shift, Poisson + negative binomial, `baseline_p50`, z-score, SHAP) are in the overview card's [The math, step by step](player-model-v1.md#the-math-step-by-step-with-worked-examples-from-the-live-2026-week-4-fit); every model shares that code. Line numbers are as of commit `2ab11b4` (the function name is the stable pointer).

**What is specific to the RB models.**
- **Targets:** `models/player_schema.py:61` (`rush_yds-rb`, amount), `:62` (`carries-rb`, count), `:63` (`receptions-rb`, count), `:64` (`scrim_yds-rb`, amount; label = rushing + receiving yards, `features/player_data.py:598`).
- **Pool:** `pool_expr("RB")`: `pgroup == "RB"` (fullbacks are `FB`, excluded) and offensive snaps > 0.
- **Carry share** (a key usage feature and the ripple "carries left open"): rush plays by `rusher_player_id` over the team's rush plays (`features/player_data.py:607`; `play_extras`, line 389), not the box score's carries, which include kneels.
- **Ripple:** `ripple_features` (`features/player.py:657`): `rip_vacated_car` / `rip_out_car` (season-to-date carry share of regular teammates who missed the last game / are ruled out; regulars = share ≥ 10%, 2+ games, gone within `RECENT_WEEKS` = 4) and `open_car` (their union, each counted once) for the watch list's role change (≥ 25% of the carries, `models/player_runs.py:78`).
- **Models:** rushing and scrimmage yards: three quantile boosters + conformal shift; carries and receptions: a Poisson booster (`-mean.txt`) + negative binomial. Settings `config/settings.yaml:80–83`. Backtests: `runs/backtests/player/<model>/`.

**Every training round** (improvement over the rolling baseline, W&B run ids):

| Model | Round 1 (default 15 / 100 / 300, raw-mean yardstick) | Final, tuned | Final v2 (published) | No market lines |
|---|---|---|---|---|
| `rush_yds-rb` | +8.1% `l8ackyb0` | +7.9% `sygypd87` | +7.9% `sbvbkqcj` | +8.1% `crppcfkc` |
| `carries-rb` | +8.4%\* `86mpdcx2` | +6.9% `qtrkdlyq` | +6.9% `sp9b4l9w` | +6.9% `ea8gjgan` |
| `receptions-rb` | +8.4%\* `ytzjlzxr` | +2.7% `93ov3uz8` | +2.7% `xjgpuxqf` | +2.3% `ultxr45c` |
| `scrim_yds-rb` | +7.3% `znunyf6c` | +7.4% `cwhxrmev` | +7.4% `l9oko9q2` | +7.3% `kvg3x7g1` |

\* Against the raw rolling mean (replaced by the median yardstick, D65); the models' own MAE barely moved (carries 3.387 → 3.390, receptions 1.090 → 1.090). Tuning (sweeps `glu9cmj8`, `ywgzufw3`, `4yqdpr6c`, `0z30t3eb`) chose deeper trees for the yardage models (31 leaves / 200 rows / 150 trees) and 15 / 50 / 300 for the counts. Live week-4 fit `kl4fzvl8`: shifts 0.2 (rushing) and 0.8 yards (scrimmage); dispersions r = 8.1 (carries) and 8.1 (receptions).

## Versioning

- **Hyperparameters are fixed for the 2026 season.** Retune before 2027 (P10). Never retune mid-season (doc 04: investigate, don't retune, when a target loses to its baseline for 3+ weeks in a row).
- **Weekly fits:** every `nfl weekly run` refits all 11 target models for each week of the season up to the current one, continuing the backtest's walk-forward history, and logs `player-model:<season>-w<NN>`;
  the weekly step scores last week's pre-kickoff projections onto the live scoreboard.
- **Promoting a new configuration** (for example one without market features, or with pruned proxy features) only if it beats this one walk-forward in each season; that is a ✋ checkpoint.
