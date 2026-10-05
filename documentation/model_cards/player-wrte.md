# Model card: player model v1, wide receivers and tight ends (P06)

**Family:** Track 1 model C ([04 → C](../04-track1-models.md#c-player-model)) · **Targets:** receiving yards, targets, receptions · **Chosen:** 2026-10-04 (D64 design; D68 settings and ship decision, ✋ Rishi) · **Production:** the weekly refit, W&B artifact `player-model:<season>-w<NN>` (all 11 targets in one artifact; promotion is described in the overview) · **Last tuned:** 2026-10-04 · **Reported window:** walk-forward 2019–2025

**Overview:** [Player model v1](player-model-v1.md) (all 11 targets, the scoreboard, the digest) · **Guide:** [Player projections guide](../guides/player-projections.md) · **Decisions:** D64 (design), D65 (counts vs the median), D66 (the Friday view), D68 (tuning and the ship decision)

**Code:** `features/player_data.py` (history), `features/player.py` (rows, `own_*` / `use_*` / `team_*` / `rip_*` / `avail_*`, baselines), `features/player_efficiency.py` (`eff_*`), `features/player_opponent.py` (`opp_*`), `models/player_model.py` (the LightGBM models, ranges, SHAP), `models/player_schema.py` (targets and pools), `models/player_runs.py` (backtest, tuning, weekly fit, scoreboard) · **Tables:** `features/player_features.parquet` (`nfl features player`), `runs/backtests/player/<target>-<group>/` (`nfl backtest player`), `runs/<season>/week<NN>/predictions_players.parquet` (`nfl train player`)

**P08 addition (not covered below):** the chance of a touchdown, a calibrated classifier, in the [P08 targets card](player-p08.md): Brier 0.1156 vs the baseline's 0.1256 (+8.0%) and 10% below the base-rate score, ECE 0.003, better in 7 of 7 seasons; the share of his quarterback's targets and of the team's drives it.

## What it is

Three models for the pass catchers a team is expected to use in a game. **Wide receivers and tight ends share each model**, with a tight-end flag (`pos_te`) so the trees can treat them differently:

| Target | Kind | Unit | What it is | Role |
|---|---|---|---|---|
| **Receiving yards** (`rec_yds`) | amount | yards | His receiving yards in the game | The **main stat** for WR/TE: it ranks the pass catchers on the watch list |
| **Targets** (`targets`) | count | targets | Passes thrown his way | Pure opportunity: the most predictable of the three |
| **Receptions** (`receptions`) | count | catches | His catches | Opportunity times catch rate |

**Who is scored (the pool).** Wide receivers and tight ends who took **at least one offensive snap** in the game (a blocking tight end with no target is a row, with 0 yards).
**Who is projected live:** players on the team's roster who played in one of its last 3 games, or who are back on the week's active roster after 3+ games for the team (this or last season) at an average snap share of 40% or more
(a returner from injured reserve), and are not listed Out, Doubtful or on a reserve list. A projected player who then doesn't play is logged "not played" and not graded.
Backtests contain only players who did play: 29,266 WR/TE games over 2019–2025 (3,806–4,331 a season; 18,151 WR and 11,115 TE).

**What the numbers look like.** Most pass-catcher games are small: receiving yards average 26.1 with a median of only 13 (a standard deviation of 33) and 33% of the rows are zero;
targets average 3.34 (median 2, 25% zeros); receptions average 2.17 (median 1, 33% zeros).

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

The conformal shift for receiving yards is **exactly 0.0 in every season**. A third of the rows are zero-yard games, and for those the raw lower edge of the range sits at exactly 0, so the 'distance outside the range' the shift is built from is 0 for them: in the 2025 history the 70th and 80th percentiles of that distance are both exactly 0, so the correction has nothing to add. The range ends up covering 82% of outcomes, a little wide.
The negative-binomial dispersion `r` is 8.5 → 9.8 for targets and
8.0 → 9.7 for receptions (2019 → 2025).

**The baseline it has to beat (D64, `documentation/11`).** *Player rolling*: half his average over his last 4 games this season, half his season-to-date average, pulled toward
last season's average with 3 pseudo-games (so early in the year last season counts a lot). No game yet this season: last season's average. No history at all (a rookie): the
league average for players with his snap share in their previous game, taken from earlier seasons only. `outperformance` = projection − baseline.

For WR/TE: 91.4% of rows use the rolling mix, 6.6% last season's average, 2.0% the role average
(a rookie or a player with no prior game).

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
| `rec_yds-wrte` | 15 | 200 | 150 | [awk1hndy](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/awk1hndy) |
| `targets-wrte` | 31 | 200 | 300 | [3w91p7lu](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/3w91p7lu) |
| `receptions-wrte` | 7 | 50 | 300 | [lhxih9f7](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/lhxih9f7) |

Receiving yards likes mid-size trees with heavy leaves (15 leaves, 200 rows per leaf, 150 rounds); targets the largest trees (31 leaves, 300 rounds); receptions the smallest (7 leaves, 50 rows per leaf, 300 rounds). With 29,000 rows the model has enough data for deeper trees
than the QB models, and the grid is flat, so none of this is fragile.

**Tuning (D68).** A 12-point grid per target (`num_leaves` 7 / 15 / 31 × `min_data_in_leaf` 50 / 200 × `n_estimators` 150 / 300, learning rate 0.05), scored by a walk-forward over
every other week of **2017–2018 only** (pressures: late 2018, its first PFR season), so none of the reported 2019–2025 weeks chose a setting. The grid is flat (best to worst
differs by 0.9–3.1% in MAE), so tuning changed little. Defaults that were not tuned: learning rate 0.05, feature fraction 0.8, bagging fraction 0.8 (every iteration), L2 penalty 1.0, seed 7.

## Results: walk-forward 2019–2025

Every week was projected by a model fitted only on earlier weeks (two burn-in seasons, 2017–2018, calibrate the range). Model and baseline are scored on exactly the same rows. For counts the baseline is its **median** (D65).
"Rank skill" is the rank correlation between *projection − baseline* and *actual − baseline*.

| Target | Rows scored | MAE model | MAE baseline | Better than baseline | vs season-to-date mean | Range coverage | Rank skill | Seasons beating baseline | No-market variant |
|---|---|---|---|---|---|---|---|---|---|
| **receiving yards** (yards) | 29,266 | 16.5 | 18.2 | **+9.2%** | +9.1% | 81.9% | 0.30 | 7 of 7 | +9.2% |
| **targets** (count) | 29,266 | 1.616 | 1.704 (median) | **+5.2%** | +7.5% | 80.8% | 0.30 | 7 of 7 | +5.2% |
| **receptions** (count) | 29,266 | 1.202 | 1.244 (median) | **+3.4%** | +7.7% | 80.1% | 0.27 | 7 of 7 | +3.4% |

*For counts the "vs season-to-date mean" column compares the model's median with a mean, the comparison D65 warns about, so it flatters counts. Use "Better than baseline".*

- **All three WR/TE targets ship (D68):** each beats its baseline pooled and in every one of the 7 seasons, with 80.1%–81.9% range coverage.
- **Market-free check (research, closing-line rule 5 of `documentation/04`).** With the three `team_mkt_*` features removed (123 features): receiving yards +9.2% (the market features add 0.1 points), targets +5.2% (within 0.05 points either way),
  receptions +3.4% (within 0.05 points either way). Market features are almost never top-3 drivers for pass catchers (see below).
- **First round vs final.** Round 1 (before tuning, before D65; counts then measured against the raw rolling mean) vs now:

| Target | First round (pre-tuning, pre-D65) | Final, vs the raw rolling mean | Final, vs the baseline used for shipping |
|---|---|---|---|
| receiving yards | +9.1% | +9.2% | +9.2% |
| targets | +8.0% (vs raw mean) | +7.9% | +5.2% (median) |
| receptions | +7.7% (vs raw mean) | +7.6% | +3.4% (median) |

The counts' raw-mean numbers barely moved with tuning; what changed is the yardstick. Against the baseline's median, targets is +5.2% and receptions +3.4%.

**A second yardstick for counts: is the expected value better?** The ship rule compares the projection's *median* with the baseline's median (D65), which is a hard bar on zero-heavy counts.
But the number a reader sees is the model's **mean**, and the proper score for a mean is the **Poisson deviance** (lower is better; it rewards a mean close to the real rate, not a guess at the commonest value).
Limited to rows where the baseline is at least 0.25 (so a near-zero baseline can't inflate its own score; the share of rows kept is in the table), the model's mean beats the baseline's mean on every count target, and in every one of the 7 seasons:

| Target | Rows used (share of all) | Deviance, model mean | Deviance, baseline mean | Better | Range over the 7 seasons |
|---|---|---|---|---|---|
| targets | 27,616 (94%) | 1.666 | 1.878 | **+11.3%** | +9.9% to +13.2% |
| receptions | 26,594 (91%) | 1.455 | 1.580 | **+7.9%** | +6.6% to +9.2% |

This is a research view, not the ship rule, and its percentages are not comparable with the MAE gains above. It shows that the MAE-against-the-median gap understates how much closer the model's expected value is to the real rates.

**Receiving yards by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 3,806 | 18.0 | 19.7 | +8.9% | 79.7% |
| 2020 | 3,989 | 17.6 | 19.2 | +8.3% | 81.3% |
| 2021 | 4,319 | 16.4 | 18.1 | +9.1% | 83.0% |
| 2022 | 4,231 | 16.4 | 18.1 | +9.5% | 80.5% |
| 2023 | 4,331 | 16.0 | 17.8 | +10.2% | 82.6% |
| 2024 | 4,303 | 15.8 | 17.5 | +9.7% | 82.9% |
| 2025 | 4,287 | 15.5 | 17.0 | +8.9% | 82.9% |
| **2019–2025** | **29,266** | **16.5** | **18.2** | **+9.2%** | **81.9%** |

**Targets by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 3,806 | 1.712 | 1.808 | +5.3% | 81.0% |
| 2020 | 3,989 | 1.720 | 1.808 | +4.9% | 81.8% |
| 2021 | 4,319 | 1.645 | 1.715 | +4.1% | 81.8% |
| 2022 | 4,231 | 1.607 | 1.696 | +5.3% | 81.6% |
| 2023 | 4,331 | 1.547 | 1.624 | +4.7% | 79.8% |
| 2024 | 4,303 | 1.575 | 1.689 | +6.8% | 79.8% |
| 2025 | 4,287 | 1.524 | 1.608 | +5.2% | 79.7% |
| **2019–2025** | **29,266** | **1.616** | **1.704** | **+5.2%** | **80.8%** |

**Receptions by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 3,806 | 1.245 | 1.284 | +3.0% | 79.1% |
| 2020 | 3,989 | 1.281 | 1.324 | +3.3% | 78.9% |
| 2021 | 4,319 | 1.196 | 1.245 | +3.9% | 80.6% |
| 2022 | 4,231 | 1.219 | 1.243 | +2.0% | 79.3% |
| 2023 | 4,331 | 1.175 | 1.215 | +3.3% | 80.6% |
| 2024 | 4,303 | 1.173 | 1.238 | +5.3% | 80.9% |
| 2025 | 4,287 | 1.135 | 1.168 | +2.8% | 81.1% |
| **2019–2025** | **29,266** | **1.202** | **1.244** | **+3.4%** | **80.1%** |

By season: receiving yards: weakest 2020 (+8.3%), strongest 2023 (+10.2%); targets: weakest 2021 (+4.1%), strongest 2024 (+6.8%); receptions: weakest 2022 (+2.0%), strongest 2024 (+5.3%). With 3,806–4,331 games a season, a season's gain moves by well under a point from sampling alone, so the seven seasons are a stable picture.

**WR versus TE**

| Target | WR rows | WR better than baseline | WR coverage | TE rows | TE better than baseline | TE coverage |
|---|---|---|---|---|---|---|
| receiving yards | 18,151 | +8.5% | 80.5% | 11,115 | +11.2% | 84.2% |
| targets | 18,151 | +5.1% | 78.8% | 11,115 | +5.4% | 84.0% |
| receptions | 18,151 | +2.9% | 78.1% | 11,115 | +4.5% | 83.5% |

Tight ends gain more than wide receivers on every target (yardage +11.2% vs +8.5%), but their ranges are too wide
(84.2% coverage for yards, 83.5% for receptions) while wide receivers' count ranges are slightly too narrow
(78.8% for targets, 78.1% for receptions). The shared model's dispersion and shift are one number for both groups.

## Is this number good?

**Yes: receiving yards is the model's best result, and the most consistent.** `documentation/11` calls a 5–15% gain over a good rolling average a solid result for player yardage.

- **Receiving yards (+9.2%) is in the middle of the band and beats the baseline in 100% of the 124 weeks** (its worst week is still +1.8%).
  The model misses a typical pass catcher by 16.5 yards against the baseline's 18.2, on a game with a median of 13 yards: most of the remaining miss is the luck of which passes go where.
- **Part of the headline is the cold start.** Before a player's first game of the season the model is +27.5% better (n = 2,713), and for players with fewer than 3 career games +42.3%.
  For established players mid-season it is +6.9% (3–7 games played) and +5.9% (8+): still inside the band, still every season.
- **Targets (+5.2%) and receptions (+3.4%) are measured against the baseline's median,** a hard bar for zero-heavy counts (D65): the median baseline already beats the raw mean by 2.9% for targets
  and 4.4% for receptions. On the raw mean they would read +7.9% and +7.6%, the Round-1 numbers. Receptions is the thin one: it wins every season but only 85% of weeks.
- **The ordering matters more than the level.** Among pass catchers with a real role (snap share ≥ 50%), the top 10% by `outperf_z` beat their baseline **55.3%** of the time for receiving yards (base rate 42.4%),
  62.8% for targets (base 46.8%) and 57.8% for receptions (base 45.3%).
  The lift over the base rate is +13 points for yards, +16 for targets and +12 for receptions. The digest's actual picks (the few biggest jumps per week, with caps, D69) hit 68.3% against a 40.1% base rate for WR/TE (see the [overview](player-model-v1.md)), a sharper slice than the top 10%. That is the property the "players to watch" list uses.

## Where the gain comes from

*By games already played this season: better than baseline (rows)*

| Target | 0 games this season | 1-2 games | 3-7 games | 8+ games |
|---|---|---|---|---|
| receiving yards | +27.5% (2,713) | +11.0% (4,907) | +6.9% (10,410) | +5.9% (11,236) |
| targets | +20.8% (2,713) | +5.3% (4,907) | +3.0% (10,410) | +2.6% (11,236) |
| receptions | +14.9% (2,713) | +5.0% (4,907) | +1.6% (10,410) | +1.5% (11,236) |

*By career games: better than baseline (rows)*

| Target | <3 career games | 3-16 | 17+ |
|---|---|---|---|
| receiving yards | +42.3% (1,644) | +8.8% (6,170) | +7.0% (21,452) |
| targets | +33.7% (1,644) | +3.6% (6,170) | +3.2% (21,452) |
| receptions | +26.5% (1,644) | +2.7% (6,170) | +2.0% (21,452) |

*By what the baseline rests on: better than baseline (rows)*

| Target | rolling | last_season | role |
|---|---|---|---|
| receiving yards | +7.3% (26,753) | +11.8% (1,929) | +62.5% (584) |
| targets | +3.3% (26,753) | +2.3% (1,929) | +56.3% (584) |
| receptions | +2.2% (26,753) | +3.2% (1,929) | +45.1% (584) |

- **The gain is largest where the baseline knows least** (a first game of the season, a rookie, the role average: receiving yards +62.5% on 584 role-baseline rows),
  and shrinks as the record grows: receiving yards +6.9% → +5.9%, targets +3.0% → +2.6%,
  receptions +1.6% → +1.5%.
- **Weeks 1–4 versus later** (receiving yards): +11.1%, +9.1% (weeks 5–9), +8.3% from week 10; targets +7.0% in weeks 1–4.
- Home and away are alike (receiving yards +8.9% home, +9.6% away).

## Calibration

| Target | 0 games this season | 1-2 games | 3-7 games | 8+ games |
|---|---|---|---|---|
| receiving yards | 83.3% | 83.2% | 81.4% | 81.4% |
| targets | 78.4% | 82.3% | 81.1% | 80.4% |
| receptions | 80.3% | 81.1% | 80.4% | 79.3% |

*(Coverage of the P10–P90 range by games already played this season.)*

- **Pooled coverage** is 81.9% (receiving yards), 80.8% (targets) and 80.1% (receptions), against a target of 80%. By season: receiving yards 79.7%–83.0%,
  targets 79.7%–81.8%, receptions 78.9%–81.1%. Receiving yards is a little wide (the shift stayed at 0, see above).
- **The tails are lopsided in the direction that matters:** 7.1% of actual receiving yards fall below P10 but 11.0% above P90 (targets 6.9% / 12.4%;
  receptions 6.5% / 13.4%). Big games are bigger than a symmetric range allows and the floor can't go below zero: read the top of a range as "a big game is possible", not as a ceiling.
- **WR and TE ranges differ** (see the WR versus TE table): tight ends' ranges are too wide, wide receivers' count ranges slightly too narrow.
- **Confidence labels** track "how much history is behind this", not coverage: for yards the `low` rows cover 81.3% and the `high` rows 83.3%.

## What drives the projections

How this was measured: for every played 2025 WR/TE row (4,287 rows) I counted how often each feature appears among the **top 3 SHAP drivers** of the projection (LightGBM's own `pred_contrib`; for counts on the log scale,
converted to count units with a first-order factor). It shows what the model leaned on in 2025; it is not an average of |SHAP| over all rows (that bar chart, `shap_summary`, is in each W&B run).

**Receiving yards**

| Feature (plain wording) | Internal name | In the top 3 of 2025 projections | Was the #1 driver |
|---|---|---|---|
| his recent form (recent games weighted most) | `own_ewm` | 92% | 3,659 |
| his share of the team's targets lately | `use_tgt_share_l4` | 75% | 156 |
| his targets per game lately | `use_targets_l4` | 59% | 121 |
| his snap share in his last game | `use_snap_l1` | 40% | 278 |
| his share of targets in games with this week's quarterback | `rip_qb_tgt_share` | 12% | 10 |
| his share of the team's targets this season | `use_tgt_share_std` | 8% | 27 |
| targets left open by teammates ruled out this week | `rip_out_tgt` | 6% | 35 |
| his average last season | `own_ls` | 5% | 0 |

Shares of all top-3 slots by family: usage (`use_*`) 61%, his own recent history (`own_*`) 32%, ripple effects (`rip_*`) 6%.

**Targets**

| Feature (plain wording) | Internal name | In the top 3 of 2025 projections | Was the #1 driver |
|---|---|---|---|
| his recent form (recent games weighted most) | `own_ewm` | 94% | 3,645 |
| his snap share in his last game | `use_snap_l1` | 76% | 455 |
| his share of the team's targets lately | `use_tgt_share_l4` | 40% | 1 |
| he is a tight end | `pos_te` | 29% | 8 |
| targets left open by teammates ruled out this week | `rip_out_tgt` | 13% | 118 |
| his share of targets in games with this week's quarterback | `rip_qb_tgt_share` | 11% | 9 |
| his average last season | `own_ls` | 11% | 0 |
| his average in recent games this season | `own_l4_season` | 7% | 14 |

Shares of all top-3 slots by family: usage (`use_*`) 41%, his own recent history (`own_*`) 38%, position flag (`pos_*`) 10%, ripple effects (`rip_*`) 8%, efficiency (`eff_*`) 1%, team context incl. market lines (`team_*`) 1%, the opponent (`opp_*`) 1%.

**Receptions**

| Feature (plain wording) | Internal name | In the top 3 of 2025 projections | Was the #1 driver |
|---|---|---|---|
| his recent form (recent games weighted most) | `own_ewm` | 93% | 3,718 |
| his snap share in his last game | `use_snap_l1` | 72% | 398 |
| his share of the team's targets lately | `use_tgt_share_l4` | 56% | 41 |
| his share of targets in games with this week's quarterback | `rip_qb_tgt_share` | 48% | 42 |
| his targets per game lately | `use_targets_l4` | 11% | 12 |
| targets left open by teammates ruled out this week | `rip_out_tgt` | 11% | 61 |
| how early in the season it is (ratings still lean on last season) | `team_prior_weight` | 3% | 7 |
| his snap share in his last couple of games | `use_snap_l2` | 2% | 0 |

Shares of all top-3 slots by family: usage (`use_*`) 48%, his own recent history (`own_*`) 31%, ripple effects (`rip_*`) 20%.

- **Recent form and a player's share of the offense are the model.** `own_ewm` is a top-3 driver in 92% (yards), 94% (targets) and 93% (receptions) of 2025 rows, with his share of team targets (`use_tgt_share_l4`)
  and his snap share next. The opponent is a top-3 driver in only 0.2% of yards rows, 3.3% of targets rows and 0.2% of receptions rows, and market features hardly ever.
- **The quarterback matters through history, not through the matchup:** his share of targets in games with this week's QB (`rip_qb_tgt_share`) is top-3 in 12% of yards rows and 48% of receptions rows.
- **Teammates being out shows up** (`rip_out_tgt`, the targets left open): top-3 in 6% of yards rows, 13% of targets rows (always pushing up).
- **The tight-end flag is a top-3 driver of 29% of targets rows:** tight ends and wide receivers with the same share of targets get different counts.
- **Proxy:** `team_prior_weight` ("how early in the season it is") is a top-3 driver in a few percent of the count models' rows, always adding a little. It marks the early weeks; do not read it as a football effect.

## Football sense-check

For each fifth of a feature's values (lowest to highest), the average **projected gap** (model P50, or the mean for counts, minus his baseline) and the average **real gap** (actual minus his baseline) as `projected / real`,
for all 2019–2025 WR/TE rows. The levels carry the median-versus-mean offset (the projection is a median, the baseline a mean, yardage is right-skewed: about 5 yards in every fifth); read the change across the fifths.

**Receiving yards** (yards)

| Feature | What it measures | Lowest fifth | 2nd | 3rd | 4th | Highest fifth |
|---|---|---|---|---|---|---|
| `opp_rec_yds_allowed_wr_oe_l8 (WR rows)` | receiving yards the opponent allows to WRs beyond what those offenses usually gain | −8.1 / −2.2 | −7.5 / −1.3 | −7.0 / −1.0 | −6.9 / −0.1 | −7.0 / 0.0 |
| `opp_rec_yds_allowed_te_oe_l8 (TE rows)` | same for tight ends | −5.2 / −1.7 | −4.8 / −0.3 | −4.8 / +0.1 | −4.5 / −0.1 | −4.4 / +0.5 |
| `opp_def_pass_epa` | opponent's pass-defense rating (EPA allowed; higher = weaker) | −7.0 / −1.7 | −6.6 / −1.0 | −6.2 / −0.5 | −6.2 / −0.8 | −5.6 / +0.6 |
| `team_mkt_total` | the market's expected total points in the game | −6.2 / −0.9 | −6.2 / −0.9 | −6.3 / −0.6 | −6.3 / −0.5 | −6.6 / −0.5 |
| `use_tgt_share_l4` | his share of the team's targets over his last 4 | −3.2 / +1.3 | −5.7 / +0.9 | −6.0 / −0.3 | −5.8 / −0.3 | −8.6 / −2.9 |
| `rip_qb_tgt_share` | his share of targets in games with this week's QB | −3.3 / +1.0 | −5.6 / +0.8 | −5.4 / +0.4 | −6.0 / −0.2 | −8.3 / −2.6 |

**Targets** (counts)

| Feature | What it measures | Lowest fifth | 2nd | 3rd | 4th | Highest fifth |
|---|---|---|---|---|---|---|
| `opp_targets_allowed_wr_oe_l8 (WR rows)` | targets the opponent allows to WRs beyond normal | −0.18 / −0.15 | −0.12 / −0.18 | −0.13 / −0.12 | −0.03 / +0.01 | +0.07 / +0.02 |
| `opp_targets_allowed_te_oe_l8 (TE rows)` | same for tight ends | −0.06 / −0.12 | −0.05 / −0.02 | −0.06 / −0.03 | +0.01 / +0.03 | +0.03 / 0.00 |
| `team_mkt_spread` | market spread for his team (higher = bigger favorite) | 0.00 / −0.02 | −0.05 / 0.00 | −0.06 / −0.05 | −0.09 / −0.13 | −0.09 / −0.09 |

- **The opponent is read in the right direction, but the real effect is small.** From the weakest to the strongest opposing pass defense the projected yards gap moves +1.4 while the real one moves +2.4; by the yards the opponent
  allows to wide receivers beyond normal +1.1 projected vs +2.2 real, and to tight ends +0.8 vs +2.2. Two or three yards between the best and worst matchups, on a typical day of 13–26 yards,
  is why opponents seldom reach a top 3: **a pass catcher's own role dwarfs the matchup**. For yardage the model's projected spread is about half the real one; for targets the projected spread is as large as the real one or larger (+0.25 projected vs +0.17 real for WRs, +0.09 vs +0.12 for TEs).
- **Game script through the market barely matters** (total points: flat across the fifths, projected and real; the favorite's spread: no pattern for targets). The model correctly gives it little weight.
- **Workload regresses to the mean.** Players with the highest recent target share, or the highest share of targets with this QB, are projected below their baseline and land below it:
  top fifth of `use_tgt_share_l4` −8.6 projected / −2.9 real (lowest fifth −3.2 / +1.3).
  The direction is right; the projection over-corrects the top fifth by a few yards (the same offset appears in the lowest fifth, so it is mostly the median-versus-mean level).
- **A teammate being ruled out opens targets, and the model sees most of it:**

| Situation | What it means | Share of rows | Model: projected gap vs rows without it | Real gap vs rows without it |
|---|---|---|---|---|
| A teammate ruled out (receiving yards) | a regular pass catcher on the team is listed Out or Doubtful this week | 19% | +2.1 | +3.4 |
| A teammate ruled out (targets) | same | 19% | +0.36 | +0.43 |
| A teammate ruled out (receptions) | same | 19% | +0.20 | +0.26 |
| A regular missed the last game (receiving yards) | recently absent regular: his targets are still up for grabs | 31% | +1.7 | +1.6 |
| A regular missed the last game (targets) | same | 31% | +0.22 | +0.22 |
| He is on the report as Questionable or Doubtful (receiving yards) | his own injury designation for the week (the Friday view); he played anyway | 4% | −3.2 | −6.1 |

  The projection captures about two thirds to four fifths of the real change for teammates being out. For the player's own injury designation it captures only about half (a projected −3.2 yards against a real
  −6.1): players who play through a designation underperform more than the model expects. A candidate improvement.

## Known biases and limits

- **Closing-line market features.** `team_mkt_*` are the *closing* lines in every backtest; a live Tuesday run sees earlier lines. For pass catchers they are almost never top-3 drivers: the market features add 0.1 points for receiving yards, within 0.05 points either way for targets, within 0.05 points either way for receptions.
- **The Friday view (D66).** `avail_*` and `rip_out_*` read the week's own injury report (a teammate ruled out is top-3 in about 5–13% of rows; his own designation matters too, see above). Backtests use the final report; a live run uses whatever the snapshot has,
  and a Tuesday run sees nobody listed. The weekly run must happen late in the week to be like the backtest.
- **No matchup at the player level.** There is no cornerback or coverage data (player-level coverage is research-only), so "this receiver against that corner" is not modeled; the opponent enters only as team-level defense ratings and what it has allowed to WRs / TEs / RBs.
- **WR and TE share one model.** Their ranges are calibrated together, which leaves tight ends' ranges too wide and wide receivers' count ranges slightly too narrow. A split by position is a candidate for the next version.
- **Early season and thin histories.** Rookies and players new to a team rest on the role baseline or last season (2.0% / 6.6% of rows), the model's gain there is large, and `team_prior_weight` shows it compensating for early-season rating uncertainty with a proxy.
- **Pool.** Every WR / TE with an offensive snap is a row, including blocking tight ends and depth receivers, so 33% of rows have 0 receiving yards.
- **Short-history features.** FTN charting starts in 2022 and PFR-based efficiency in 2018, each a week late (D44), so the model learned them from fewer seasons. NGS separation and cushion (`eff_*`) are in the model but do not predict volume: a high-separation tight end is more often a low-volume one.
- **Not modeled here:** touchdown chances and yardage after the catch beyond what shows in efficiency (P08).
- **Sampling noise.** 3,806–4,331 games a season; only the pooled and seven-season picture is claimed.
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
| receiving yards | [xzmsy4ac](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/xzmsy4ac) | [awk1hndy](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/awk1hndy) | [clfrac2z](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/clfrac2z) |
| targets | [q0xk19h9](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/q0xk19h9) | [3w91p7lu](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/3w91p7lu) | [vc1nnqax](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/vc1nnqax) |
| receptions | [es0ls811](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/es0ls811) | [lhxih9f7](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/lhxih9f7) | [ls6t4bxm](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/ls6t4bxm) |

The sweeps (`tune-<target>-<group>`, job type `tune`) hold the 12 grid points. Each point is one small run logging `tune/mae_model`, `tune/mae_baseline`, `tune/improvement_pct` and `tune/n`; on the sweep page the parallel-coordinates chart shows `num_leaves`, `min_data_in_leaf` and `n_estimators` against `tune/mae_model`. A flat picture is the expected one (the grid is flat, D68).

### Backtest runs (`backtest-<target>-wrte`; job type `backtest`)

The x-axis of every `bt/*` curve is **`bt/step`**: one step per reported week (124 steps: 2019 week 1 to 2025 week 18; 17 weeks in 2019 and 2020, 18 after). `bt/season` and `bt/week` translate a step.

| Chart | What it shows | How to read it |
|---|---|---|
| `bt/cum_improvement_pct` | Better-than-baseline over **every week so far** | **The main chart.** It settles as games accumulate and its last value is the pooled result. Good: above 0 and flat or rising |
| `bt/improvement_pct` | Better-than-baseline for **that week only** | Jumpy, because one week has a limited number of pass catchers. Read the share of weeks above 0, not single points |
| `bt/cum_coverage_80` | Share of actuals inside P10–P90 over every week so far | Should hover near 0.80; the first steps swing |
| `bt/coverage_80` | Coverage for that week | Normal weekly noise is several points either way (see the numbers table) |
| `bt/mae_model`, `bt/mae_baseline`, `bt/cum_mae_model`, `bt/cum_mae_baseline` | The raw MAEs behind the two improvement curves, in the target's unit (counts: the baseline is its median, D65) | In the cumulative chart the model line should sit under the baseline line |
| `bt/range_param` | Amounts: the conformal shift used that week (positive = range widened). Counts: the negative-binomial dispersion `r` (smaller = wider spread) | Should be stable, not drifting |
| `bt/n`, `bt/step`, `bt/season`, `bt/week` | Rows scored that week; lookups | `bt/n` is a sanity check |
| `lgb/curve_<season>` | The season's opening fit (train on everything before the previous season, hold the previous season out): the model's loss (the P50 quantile loss for receiving yards, the Poisson negative log-likelihood for the two count models) on the training rows and on the held-out season, after each boosting round. The first available season's curve is logged up front, the others at the end | Training should fall steadily; the held-out line should flatten, not climb. A growing gap is overfitting. Receiving yards runs 150 rounds, targets and receptions 300 (fixed, no early stopping), so a held-out line that turns up late would mean too many rounds |

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


**Real numbers for these three runs**

| Target | `bt/cum_improvement_pct`: season-end values 2019 → 2025 | `bt/improvement_pct`: weekly range, median, share of weeks above 0 | `bt/coverage_80`: weekly 10th–90th percentile (median) | `bt/range_param`: 2019 → 2025 | `bt/n`: rows per week |
|---|---|---|---|---|---|
| receiving yards | 8.9, 8.6, 8.8, 9.0, 9.2, 9.3, 9.2 (ends +9.2%) | +2% to +22%, median +9.3%, 100% | 0.79–0.85 (0.82) | shift 0.0 → 0.0 | 197–264 |
| targets | 5.3, 5.1, 4.7, 4.9, 4.8, 5.2, 5.2 (ends +5.2%) | −3% to +14%, median +5.5%, 94% | 0.76–0.85 (0.81) | r 8.47 → 9.82 | 197–264 |
| receptions | 3.0, 3.1, 3.4, 3.1, 3.1, 3.5, 3.4 (ends +3.4%) | −4% to +10%, median +3.3%, 85% | 0.76–0.84 (0.80) | r 8.00 → 9.68 | 197–264 |

How to use it: receiving yards beats its baseline in every one of the 124 weeks (the weekly line never goes below zero: that is unusual and a sign of how steady the usage signal is), targets in about nine weeks out of ten, receptions in about five out of six. Each week has 197–264 pass catchers, so the weekly lines are much smoother than the QB's.

## Code path and training history

The formulas and worked examples (baseline, pinball loss, conformal shift, Poisson + negative binomial, `baseline_p50`, z-score, SHAP) are in the overview card's [The math, step by step](player-model-v1.md#the-math-step-by-step-with-worked-examples-from-the-live-2026-week-4-fit); every model shares that code. Line numbers are as of commit `2ab11b4` (the function name is the stable pointer).

**What is specific to the WR/TE models.**
- **Targets:** `models/player_schema.py:65` (`rec_yds-wrte`, amount), `:66` (`targets-wrte`, count), `:67` (`receptions-wrte`, count).
- **Pool:** `pool_expr("WR/TE")`: `pgroup` WR or TE with offensive snaps. One model for both positions; the flag `pos_te` (`add_position_flags`, `models/player_model.py:109`) lets the trees treat tight ends differently.
- **Usage features:** target share and air-yards share from the box score (`target_share`, `air_yards_share`), red-zone target share from play-by-play (`play_extras`, `features/player_data.py:389`), all over his earlier games (`usage_features`, `features/player.py:433`).
- **QB history (graph Q3 in Polars):** `rip_qb_new`, `rip_qb_tgt_share`, `rip_qb_games` in `ripple_features` (`features/player.py:657`): his share of the team's targets in earlier games where this week's expected QB was the main QB.
- **Watch-list role change:** `open_tgt` ≥ 15% (`models/player_runs.py:77`).
- **Models:** receiving yards: three quantile boosters + conformal shift; targets and receptions: a Poisson booster + negative binomial. Settings `config/settings.yaml:84–86`. Backtests: `runs/backtests/player/<model>/`.

**Every training round** (improvement over the rolling baseline, W&B run ids):

| Model | Round 1 (default 15 / 100 / 300, raw-mean yardstick) | Final, tuned | Final v2 (published) | No market lines |
|---|---|---|---|---|
| `rec_yds-wrte` | +9.1% `gu5b2wxv` | +9.2% `wpwv6zd2` | +9.2% `xzmsy4ac` | +9.2% `clfrac2z` |
| `targets-wrte` | +8.0%\* `1yq342zo` | +5.2% `hqmugbgz` | +5.2% `q0xk19h9` | +5.2% `vc1nnqax` |
| `receptions-wrte` | +7.7%\* `6vz8zisd` | +3.4% `h1vbygqw` | +3.4% `es0ls811` | +3.4% `ls6t4bxm` |

\* Against the raw rolling mean (replaced by the median yardstick, D65); the models' own MAE barely moved (targets 1.615 → 1.616, receptions 1.201 → 1.202). Tuning (sweeps `awk1hndy`, `3w91p7lu`, `lhxih9f7`): 15 / 200 / 150 for receiving yards, 31 / 200 / 300 for targets, 7 / 50 / 300 for receptions. Live week-4 fit `kl4fzvl8`: receiving-yards shift 0.0 (the raw quantiles were already calibrated), dispersions r = 10.5 (targets) and 11.4 (receptions).

## Versioning

- **Hyperparameters are fixed for the 2026 season.** Retune before 2027 (P10). Never retune mid-season (doc 04: investigate, don't retune, when a target loses to its baseline for 3+ weeks in a row).
- **Weekly fits:** every `nfl weekly run` refits all 11 target models for each week of the season up to the current one, continuing the backtest's walk-forward history, and logs `player-model:<season>-w<NN>`;
  the weekly step scores last week's pre-kickoff projections onto the live scoreboard.
- **Promoting a new configuration** (for example one without market features, or with pruned proxy features) only if it beats this one walk-forward in each season; that is a ✋ checkpoint.
