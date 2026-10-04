# Model card: player model v1, defenders: pass rushers and tacklers (P06)

**Family:** Track 1 model C ([04 → C](../04-track1-models.md#c-player-model)) · **Targets:** pressures (EDGE / DL group), tackles (LB / S group) · **Chosen:** 2026-10-04 (D64 design; D68 settings and ship decision, ✋ Rishi) · **Production:** the weekly refit, W&B artifact `player-model:<season>-w<NN>` (all 11 targets in one artifact; promotion is described in the overview) · **Last tuned:** 2026-10-04 · **Reported window:** walk-forward 2019–2025

**Overview:** [Player model v1](player-model-v1.md) (all 11 targets, the scoreboard, the digest) · **Guide:** [Player projections guide](../guides/player-projections.md) · **Decisions:** D64 (design), D65 (counts vs the median), D66 (the Friday view), D68 (tuning and the ship decision)

**Code:** `features/player_data.py` (history), `features/player.py` (rows, `own_*` / `use_*` / `team_*` / `rip_*` / `avail_*`, baselines), `features/player_efficiency.py` (`eff_*`), `features/player_opponent.py` (`opp_*`), `models/player_model.py` (the LightGBM models, ranges, SHAP), `models/player_schema.py` (targets and pools), `models/player_runs.py` (backtest, tuning, weekly fit, scoreboard) · **Tables:** `features/player_features.parquet` (`nfl features player`), `runs/backtests/player/<target>-<group>/` (`nfl backtest player`), `runs/<season>/week<NN>/predictions_players.parquet` (`nfl train player`)


## What it is

Two count models for defensive players:

| Target | Group | Unit | What it is | Role |
|---|---|---|---|---|
| **Pressures** (`pressures`) | EDGE / DL | pressures | The times he pressured the quarterback (PFR's charted count: sacks, hits and hurries) | The **main stat** for the pass-rush group: it ranks defenders on the watch list |
| **Tackles** (`tackles`) | LB / S | tackles | Solo plus assisted tackles (box score) | The **main stat** for linebackers and safeties |

**Who is scored (the pool).** Players with **at least one defensive snap** in the game.
- *Pressures* (the "EDGE / DL" group): the **defensive linemen and the linebackers**. nflverse labels many edge rushers as linebackers, so there is no clean edge group: off-ball linebackers are in the pool too, and their history says they rarely pressure
  (the model learns to tell them apart, see below). Pressures come from PFR, so this model starts in **2018**: 45,041 player-games over 2019–2025 (5,988–6,604 a season; 24,984 DL and 20,057 LB).
- *Tackles* (the "LB / S" group): **linebackers and safeties** (cornerbacks are not modeled here; coverage stats are P08). Trained from 2013: 33,949 player-games over 2019–2025 (4,698–4,981 a season; 20,101 LB and 13,848 S).

Linebackers appear in both pools, so each linebacker gets a pressure projection and a tackle projection.
**Who is projected live:** players on the team's roster who played in one of its last 3 games, or who are back on the week's active roster after 3+ games for the team (this or last season) at an average snap share of 40% or more
(a returner from injured reserve), and are not listed Out, Doubtful or on a reserve list. A projected player who then doesn't play is logged "not played" and not graded.

**What the numbers look like.** Pressures are rare and lumpy: the average is 0.67 a game, the median is **0**, **61% of the rows are zero**, and the 90th percentile is 2.
Tackles are more regular: mean 3.79, median 3, 12% zeros, 90th percentile 8.

## How it works, in plain language

**A count (carries, targets, tackles, pressures) is a distribution.** A LightGBM model with a *Poisson* objective learns the **expected value** (the mean). Real counts
vary more than a Poisson says, so the outcome is modeled as a **negative binomial** with that mean and a dispersion `r` fitted on the model's own earlier misses
(`Var = mean + mean² / r`; a smaller `r` means a wider spread, a very large `r` means plain Poisson). The **projection (P50) is the median** of that distribution, and the
**80% range** runs between its low and high tail quantiles. Counts are whole numbers, so quantiles at exactly 10% / 90% over-cover (88% of tackles in the smoke run),
and the tail level (between 0.10 and 0.25) is picked on the same earlier misses so the range holds about 80%. The number a reader sees as "the projection" is the **mean**;
the scoreboard grades the **median** against the baseline's median (see "Is this number good?").

The negative-binomial dispersion `r` was 3.2 → 4.0 for pressures (a wide spread around a small mean) and 8.9 → 8.8 for tackles (2019 → 2025).

**The baseline it has to beat (D64, `documentation/11`).** *Player rolling*: half his average over his last 4 games this season, half his season-to-date average, pulled toward
last season's average with 3 pseudo-games (so early in the year last season counts a lot). No game yet this season: last season's average. No history at all (a rookie): the
league average for players with his snap share in their previous game, taken from earlier seasons only. `outperformance` = projection − baseline.

For defenders: pressures 84.3% rolling mix, 12.7% last season's average, 3.0% the role average
(the last-season share is higher than for offense because, with the PFR lag, weeks 1 and 2 both have no usable game this season); tackles 90.3% / 7.4% / 2.3%.

**The PFR lag (D44) and the label lag.** PFR publishes about a week late. Every PFR-based feature uses games at least two weeks back, and for the pressures target the **label itself** (what we train and calibrate on) is held back too: at each weekly refit the previous week's pressure labels
are left out of training and out of the range / dispersion history, because a Tuesday run wouldn't have them. A game whose PFR rows haven't published can't be scored yet. Tackles come from the nflverse box score (no lag).

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
| `pressures-edge` | 7 | 200 | 150 | [upmglalz](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/upmglalz) |
| `tackles-lbs` | 15 | 50 | 150 | [uvajbbn7](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/uvajbbn7) |

Pressures settled on a shallow tree (7 leaves, 200 rows per leaf, 150 rounds) and tackles on a mid-size one (15 leaves, 50 rows per leaf, 150 rounds). The grid is flat, so none of this is fragile.

**The pressures sweep was run twice.** A code review after the first backtests found that the first sweep (`qnn35nz6`) trained on the previous week's pressure labels, which a Tuesday run wouldn't have (the PFR label lag, D44). It was re-run with the lag respected: the final sweep is [`upmglalz`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/upmglalz). Both picked the same point, so the settings and every pressures backtest number are unchanged.

**Tuning (D68).** A 12-point grid per target (`num_leaves` 7 / 15 / 31 × `min_data_in_leaf` 50 / 200 × `n_estimators` 150 / 300, learning rate 0.05), scored by a walk-forward over
every other week of **2017–2018 only** (pressures: late 2018, its first PFR season), so none of the reported 2019–2025 weeks chose a setting. The grid is flat (best to worst
differs by 0.9–3.1% in MAE), so tuning changed little. Defaults that were not tuned: learning rate 0.05, feature fraction 0.8, bagging fraction 0.8 (every iteration), L2 penalty 1.0, seed 7.

## Results: walk-forward 2019–2025

Every week was projected by a model fitted only on earlier weeks (two burn-in seasons calibrate the range; pressures start in 2018, so its burn-in is 2018 only). Model and baseline are scored on exactly the same rows, and the baseline is its **median** (D65, explained below).
"Rank skill" is the rank correlation between *projection − baseline* and *actual − baseline*.

| Target | Rows scored | MAE model | MAE baseline | Better than baseline | vs season-to-date mean | Range coverage | Rank skill | Seasons beating baseline | No-market variant |
|---|---|---|---|---|---|---|---|---|---|
| **pressures** (count) | 45,041 | 0.556 | 0.573 (median) | **+3.0%** | +17.0% | 85.2% | 0.33 | 7 of 7 | +3.0% |
| **tackles** (count) | 33,949 | 1.796 | 1.886 (median) | **+4.8%** | +7.4% | 80.6% | 0.31 | 7 of 7 | +4.7% |

*For counts the "vs season-to-date mean" column compares the model's median with a mean, the comparison D65 warns about, so it flatters counts. Use "Better than baseline".*

- **Both defense targets ship (D68):** each beats its baseline pooled and in every one of the 7 seasons, with 80.6% (tackles) and 85.2% (pressures) range coverage.
- **Market-free check (research, closing-line rule 5 of `documentation/04`).** With the three `team_mkt_*` features removed (123 features): pressures +3.0% (within 0.05 points either way), tackles +4.7% (the market features add 0.1 points).
  Market features are almost never top-3 drivers for defenders, so no change is the expected result.
- **First round vs final.** Round 1 (before tuning, before D65; counts then measured against the raw rolling mean) vs now:

| Target | First round (pre-tuning, pre-D65) | Final, vs the raw rolling mean | Final, vs the baseline used for shipping |
|---|---|---|---|
| pressures | +16.0% (vs raw mean) | +15.8% | +3.0% (median) |
| tackles | +6.8% (vs raw mean) | +6.5% | +4.8% (median) |

The raw-mean numbers barely moved with tuning; what changed is the yardstick. Against the baseline's median, pressures is +3.0% and tackles +4.8%.

**A second yardstick for counts: is the expected value better?** The ship rule compares the projection's *median* with the baseline's median (D65), which is a hard bar on zero-heavy counts.
But the number a reader sees is the model's **mean**, and the proper score for a mean is the **Poisson deviance** (lower is better; it rewards a mean close to the real rate, not a guess at the commonest value).
Limited to rows where the baseline is at least 0.25 (so a near-zero baseline can't inflate its own score; the share of rows kept is in the table), the model's mean beats the baseline's mean on every count target, and in every one of the 7 seasons:

| Target | Rows used (share of all) | Deviance, model mean | Deviance, baseline mean | Better | Range over the 7 seasons |
|---|---|---|---|---|---|
| pressures | 31,818 (71%) | 1.219 | 1.273 | **+4.2%** | +3.7% to +5.0% |
| tackles | 33,567 (99%) | 1.664 | 1.877 | **+11.4%** | +10.1% to +12.7% |

This is a research view, not the ship rule, and its percentages are not comparable with the MAE gains above. It shows that the MAE-against-the-median gap understates how much closer the model's expected value is to the real rates.

**Pressures by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 5,988 | 0.598 | 0.616 | +2.8% | 85.2% |
| 2020 | 6,144 | 0.588 | 0.602 | +2.3% | 84.2% |
| 2021 | 6,604 | 0.580 | 0.598 | +3.1% | 85.0% |
| 2022 | 6,555 | 0.525 | 0.540 | +2.7% | 86.2% |
| 2023 | 6,568 | 0.530 | 0.544 | +2.7% | 85.8% |
| 2024 | 6,585 | 0.536 | 0.556 | +3.6% | 85.2% |
| 2025 | 6,597 | 0.539 | 0.561 | +3.9% | 85.1% |
| **2019–2025** | **45,041** | **0.556** | **0.573** | **+3.0%** | **85.2%** |

**Tackles by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 4,698 | 1.764 | 1.838 | +4.1% | 80.8% |
| 2020 | 4,783 | 1.777 | 1.891 | +6.0% | 80.8% |
| 2021 | 4,981 | 1.800 | 1.872 | +3.9% | 80.0% |
| 2022 | 4,867 | 1.787 | 1.874 | +4.6% | 80.8% |
| 2023 | 4,901 | 1.783 | 1.882 | +5.2% | 80.3% |
| 2024 | 4,791 | 1.802 | 1.898 | +5.0% | 80.8% |
| 2025 | 4,928 | 1.856 | 1.948 | +4.7% | 81.0% |
| **2019–2025** | **33,949** | **1.796** | **1.886** | **+4.8%** | **80.6%** |

By season: pressures: weakest 2020 (+2.3%), strongest 2025 (+3.9%); tackles: weakest 2021 (+3.9%), strongest 2020 (+6.0%). With 5,988–6,604 (pressures) and 4,698–4,981 (tackles) player-games a season, a season's gain moves by well under a point from sampling alone.

**Position splits**

| Target | Group | Rows | Better than baseline | Range coverage |
|---|---|---|---|---|
| Pressures | DL | 24,984 | +3.2% | 84.8% |
| Pressures | LB | 20,057 | +2.8% | 85.8% |
| Tackles | LB | 20,101 | +4.7% | 81.3% |
| Tackles | S | 13,848 | +5.0% | 79.7% |

## Is this number good?

**Tackles: yes, modest and steady. Pressures: honestly small.** `documentation/11` expects counts to gain "similar relative gains" to yardage (5–15%), with usage-driven stats the most predictable.

- **Tackles (+4.8%).** Against the baseline's median it is just under the band; against the raw mean it would read +6.5% (Round 1: +6.8%). It beats the baseline in 95% of the 124 weeks (worst week −0.9%),
  every season, and on the expected-value check the model's mean is better by 11.4%. The miss is a typical 1.80 tackles against 1.89: whether a linebacker is around the ball is largely luck of the play.
- **Pressures (+3.0%) is the second-smallest gain of the 11 targets, and the reason is in the data.** 61% of the rows are zero and the median is 0, so for most players both the baseline's median (68% of rows) and the model's P50 (70%) are 0,
  and MAE can't reward a better estimate of a rate between 0 and 1. Against the raw rolling mean the model reads +15.8% and against the season-to-date mean +17.0%:
  that is the "16%" of Round 1, and it is mostly the gift of a median over a mean (D65); the skill is the 3–4% below. The honest like-for-like numbers are +3.0% on MAE and +4.2% on the deviance of the mean
  (on the 71% of rows whose baseline is at least 0.25).
- **The ordering is useful for tackles and only weakly so for pressures.** Among defenders with a real role (snap share ≥ 50%), the top 10% by `outperf_z` beat their baseline **65.9%** of the time for tackles (base rate 47.8%)
  but only **44.4%** for pressures (base rate 37.3%, top 5%: 50.3%) in this broad slice. Rank skill is similar (0.33 vs 0.31); a pressure is simply a rarer, lumpier event.
  The very top of the ranking does better: the digest's actual picks (the few biggest jumps per week, D69) hit 61.1% for EDGE/DL against a 37.3% base rate and 72.1% against 47.8% for LB/S (see the [overview](player-model-v1.md)). The watch list caps defenders at 3 (D69).
- **Consistency.** Weeks beaten: pressures 77%, tackles 95%.

## Where the gain comes from

*By games already played this season: better than baseline (rows)*

| Target | 0 games this season | 1-2 games | 3-7 games | 8+ games |
|---|---|---|---|---|
| pressures | +1.5% (7,478) | +3.9% (7,349) | +3.3% (15,059) | +3.0% (15,155) |
| tackles | +12.6% (3,545) | +6.5% (6,135) | +3.9% (12,048) | +2.5% (12,221) |

*By career games: better than baseline (rows)*

| Target | <3 career games | 3-16 | 17+ |
|---|---|---|---|
| pressures | +7.1% (2,891) | +3.3% (9,733) | +2.8% (32,417) |
| tackles | +18.5% (2,087) | +5.7% (6,901) | +3.5% (24,961) |

*By what the baseline rests on: better than baseline (rows)*

| Target | rolling | last_season | role |
|---|---|---|---|
| pressures | +3.3% (37,962) | +1.2% (5,717) | +4.4% (1,362) |
| tackles | +3.9% (30,669) | +4.7% (2,507) | +35.3% (773) |

- **Tackles gains the most where the baseline knows least** (a first game of the season +12.6%, fewer than 3 career games +18.5%, the role average +35.3% on 773 rows)
  and falls to +2.5% for players with 8+ games played.
- **Pressures is the only target that gains *less* early than late** (+1.9% in weeks 1–4, +3.3% in weeks 5–9, +3.4% from week 10); before a player's first game of the season it is just +1.5%.
  Every other target gains more early. Plausibly the PFR lag leaves the early-season history thin and the pressure rate is noisy, so there is little for a model to add (not tested separately).
- Home and away are alike (tackles +4.6% home, +5.0% away; pressures +3.3% / +2.7%).

## Calibration

| Target | 0 games this season | 1-2 games | 3-7 games | 8+ games |
|---|---|---|---|---|
| pressures | 86.1% | 84.6% | 86.0% | 84.3% |
| tackles | 76.8% | 81.4% | 80.8% | 81.1% |

*(Coverage of the P10–P90 range by games already played this season.)*

- **Pressures over-covers:** 85.2% of actual pressures fall inside P10–P90 (target 80%; by season 84.2%–86.2%). The reason is arithmetic, not a bug: counts are whole numbers and 61% of rows are 0, so P10 is 0 for most players
  (only 1.3% of actuals fall *below* it: a count can't go below 0) and the tail selection could not get closer to 80% with whole numbers. It is inside the 72–88% ship band, but read a pressure range as "0 up to about the 90th percentile", with misses concentrated on the high side (13.4% above P90).
- **Tackles is calibrated:** 80.6% overall (by season 80.0%–81.0%), 7.6% below P10 and 11.7% above P90 (the upper tail is the heavier one). Players with no game yet this season sit a little low (76.8%):
  the dispersion is fitted on all rows, so a first game's uncertainty is understated.
- **Confidence labels** track "how much history is behind this", not coverage: tackles `high` rows cover 79.6% and `low` rows 80.8%.

## What drives the projections

How this was measured: for every played 2025 row (pressures 6,597, tackles 4,928) I counted how often each feature appears among the **top 3 SHAP drivers** of the projection (LightGBM's own `pred_contrib`, on the log scale for these Poisson models,
converted to count units with a first-order factor). It shows what the model leaned on in 2025; it is not an average of |SHAP| over all rows (that bar chart, `shap_summary`, is in each W&B run).

**Pressures**

| Feature (plain wording) | Internal name | In the top 3 of 2025 projections | Was the #1 driver |
|---|---|---|---|
| his recent form (recent games weighted most) | `own_ewm` | 87% | 5,138 |
| his snap share in his last game | `use_snap_l1` | 74% | 507 |
| his tackles per snap lately | `eff_tackles_per_snap_l8` | 68% | 842 |
| his sacks and tackles for loss per game | `eff_sacks_tfl_per_game_l8` | 42% | 37 |
| his average last season | `own_ls` | 12% | 7 |
| his snap share in his last couple of games | `use_snap_l2` | 10% | 17 |
| how often the opponent's quarterbacks are pressured | `opp_off_pressure_rate_l8` | 3% | 0 |
| how early in the season it is (ratings still lean on last season) | `team_prior_weight` | 3% | 48 |

Shares of all top-3 slots by family: efficiency (`eff_*`) 37%, his own recent history (`own_*`) 33%, usage (`use_*`) 28%, the opponent (`opp_*`) 1%.

**Tackles**

| Feature (plain wording) | Internal name | In the top 3 of 2025 projections | Was the #1 driver |
|---|---|---|---|
| his recent form (recent games weighted most) | `own_ewm` | 95% | 3,832 |
| his snap share in his last game | `use_snap_l1` | 88% | 961 |
| his average in recent games | `own_l4` | 41% | 2 |
| his tackles per snap lately | `eff_tackles_per_snap_l8` | 20% | 13 |
| his season-to-date average | `own_std` | 15% | 3 |
| he missed his team's last game | `avail_missed_last` | 12% | 58 |
| his pressures per snap lately | `eff_pressures_per_snap_l8` | 11% | 30 |
| his snap share in his last couple of games | `use_snap_l2` | 6% | 1 |
| how early in the season it is (ratings still lean on last season) | `team_prior_weight` | 5% | 22 |

Shares of all top-3 slots by family: his own recent history (`own_*`) 50%, usage (`use_*`) 33%, efficiency (`eff_*`) 11%, availability (`avail_*`) 4%, team context incl. market lines (`team_*`) 2%.

- **Both are mostly "what he has been doing, and how much he plays."** `own_ewm` (recent form) is a top-3 driver in 87% of pressure rows and 95% of tackle rows, with last-game snap share next (74% / 88%).
- **For pressures the model first sorts out who a defender is.** His tackles per snap (`eff_tackles_per_snap_l8`) is a top-3 driver of 68% of pressure rows and the #1 driver of 842 of them, with a **negative** relation (a high tackle rate means an off-ball linebacker,
  who pressures rarely); his sacks and tackles for loss per game (42% of rows) mark the edge rushers. This is how the model copes with nflverse's edge/off-ball labeling: it learns the role from the stat line.
- **The opponent barely appears:** 3.4% of pressure rows (mostly how often the opposing quarterback is pressured, `opp_off_pressure_rate_l8`) and 0.4% of tackle rows. Market and team features: 0.9% / 1.8% of the top-3 slots.
- **Availability matters for tackles:** `avail_missed_last` ("he missed his team's last game") is top-3 in 12% of tackle rows, always lowering the projection (presumably because a player coming back from a missed game plays fewer snaps).
- **Proxy:** `team_prior_weight` ("how early in the season it is") is a top-3 driver in 3% (pressures) and 5% (tackles) of rows, always adding a little. It marks the early weeks; do not read it as a football effect.

## Football sense-check

For each fifth of a feature's values (lowest to highest), the average **projected gap** (the mean projection minus his baseline) and the average **real gap** (actual minus his baseline) as `projected / real`, for all 2019–2025 rows. Read the change across the fifths:
a feature the model uses well shows a projected change in the same direction, and about the same size, as the real one.

**Pressures** (counts)

| Feature | What it measures | Lowest fifth | 2nd | 3rd | 4th | Highest fifth |
|---|---|---|---|---|---|---|
| `opp_off_pressure_rate_l8` | how often the opposing quarterbacks are pressured | −0.06 / −0.08 | −0.03 / −0.05 | 0.00 / −0.01 | +0.02 / +0.04 | +0.04 / +0.05 |
| `opp_off_sack_rate_l8` | how often the opposing offense is sacked | −0.04 / −0.05 | −0.01 / −0.03 | −0.01 / −0.01 | +0.01 / 0.00 | +0.03 / +0.05 |
| `opp_off_pass_rate_l8` | how often the opposing offense passes | −0.03 / −0.06 | −0.01 / −0.02 | 0.00 / 0.00 | −0.01 / −0.01 | +0.01 / +0.04 |
| `team_mkt_spread` | market spread for his team (higher = bigger favorite) | −0.01 / −0.03 | −0.01 / −0.03 | −0.02 / −0.01 | 0.00 / 0.00 | +0.01 / +0.02 |
| `eff_pressures_per_snap_l8` | his own pressures per snap lately | +0.15 / +0.12 | +0.06 / +0.04 | +0.02 / +0.02 | −0.02 / +0.01 | −0.27 / −0.26 |

**Tackles** (counts)

| Feature | What it measures | Lowest fifth | 2nd | 3rd | 4th | Highest fifth |
|---|---|---|---|---|---|---|
| `opp_off_plays_per_game_l8` | plays per game the opposing offense runs | −0.03 / −0.02 | −0.02 / +0.05 | −0.01 / +0.04 | +0.01 / +0.08 | +0.02 / +0.05 |
| `opp_off_rush_rate_l8` | how often the opposing offense runs | −0.03 / +0.02 | −0.01 / 0.00 | −0.02 / +0.01 | +0.01 / +0.06 | +0.02 / +0.10 |
| `opp_off_pass_rate_l8` | how often the opposing offense passes | +0.02 / +0.11 | +0.01 / +0.05 | −0.02 / +0.02 | −0.02 / −0.01 | −0.03 / +0.02 |
| `team_mkt_spread` | market spread for his team (higher = bigger favorite) | +0.07 / +0.21 | −0.01 / +0.03 | −0.02 / +0.06 | −0.02 / +0.01 | −0.05 / −0.09 |
| `team_mkt_total` | the market's expected total points in the game | +0.01 / +0.02 | −0.02 / +0.01 | −0.01 / +0.03 | 0.00 / +0.08 | −0.02 / +0.04 |

- **Pressures follow the opponent's protection, in the right direction and at about three quarters of the real size.** From the lowest to the highest fifth of how often the opposing quarterbacks are pressured, the projected gap changes +0.10 pressures and the real one +0.13;
  by how often the offense is sacked +0.07 vs +0.10. These are about a tenth of a pressure between the best and worst matchups against a mean of 0.67: real but small, which is why the opponent seldom reaches a top 3.
- **A hot recent pressure rate regresses to the mean, and the model knows:** the top fifth of `eff_pressures_per_snap_l8` is projected −0.27 below its baseline and lands −0.26 (lowest fifth +0.15 / +0.12).
- **Game script is a small, plausible effect.** Favorites' pass rushers get slightly more pressures (the opponent trails and throws: projected +0.02, real +0.05 from the biggest underdogs to the biggest favorites), and underdogs' linebackers and safeties make more tackles
  (−0.12 projected vs −0.30 real across the same fifths: the model sees about 38% of it).
- **Tackles barely respond to the opponent's tempo or run rate**: plays per game +0.05 projected vs +0.06 real, run rate +0.05 vs +0.08 (more runs, more tackles: the right sign, tiny). A defender's own role dominates.
- **Availability:** a linebacker or safety who missed his team's last game is projected −0.57 tackles below the others and finishes −0.72; one on the report as Questionable or Doubtful −0.23 projected vs −0.37 real (the model sees about 80% and 62% of the real change).
  For pressures the model sees the report designation about 50% of it (−0.054 vs −0.108) and **misses the "returning from a missed game" effect entirely** (projected +0.008, real −0.068): a candidate improvement.

## Known biases and limits

- **PFR lag and label lag (D44).** Pressure features use games at least two weeks back and the previous week's pressure labels are held out of every refit. The live scoreboard can't grade a pressure projection until PFR publishes that game.
- **Pressures start in 2018.** PFR has no earlier data, so the first reported season (2019) is predicted from 2018 alone plus the burn-in; the model sees five seasons fewer than the tackle model.
- **A mostly-zero target.** 61% of pressure rows are 0 and the median is 0, so a pressure projection is best read as a mean (the number shown) with a wide, high-side range, not as a point guess. A like-for-like MAE gain is small (+3.0%).
- **Edge and off-ball linebackers share a pool.** There is no clean edge label, so the model infers the role from the stat line; the 20,057 linebacker rows include players who almost never rush.
- **Closing-line market features.** `team_mkt_*` are the *closing* lines in every backtest; a live Tuesday run sees earlier lines. For defenders they are almost never top-3 drivers: within 0.05 points either way for pressures, the market features add 0.1 points for tackles.
- **The Friday view (D66).** `avail_*` read the week's own injury report (an LB / S who missed the last game is top-3 for 12% of tackle rows). Backtests use the final report; a live run uses whatever the snapshot has,
  and a Tuesday run sees nobody listed. The weekly run must happen late in the week to be like the backtest.
- **No scheme or alignment data.** Who rushes on a given play, snap limits after injury, and a coach's change in role are not observed; the opponent enters only as team-level pressure and sack rates. Opponent offensive-line matchups are not modeled.
- **Thin-history ranges.** Players with no game yet this season sit a little low on coverage for tackles (76.8%).
- **Short-history features.** FTN charting starts in 2022 and PFR-based efficiency in 2018, each a week late.
- **Not modeled here:** sacks and QB hits as separate targets, and coverage stats for cornerbacks and safeties (P08).
- **Sampling noise.** 4,698–4,981 tackle rows and 5,988–6,604 pressure rows a season; only the pooled and seven-season picture is claimed.
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
| pressures | [z2mwdzna](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/z2mwdzna) | [upmglalz](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/upmglalz) | [b1nhvc9a](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/b1nhvc9a) |
| tackles | [m1iw9pyp](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/m1iw9pyp) | [uvajbbn7](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/uvajbbn7) | [xo7ckwaw](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/xo7ckwaw) |

The sweeps (`tune-<target>-<group>`, job type `tune`) hold the 12 grid points. Each point is one small run logging `tune/mae_model`, `tune/mae_baseline`, `tune/improvement_pct` and `tune/n`; on the sweep page the parallel-coordinates chart shows `num_leaves`, `min_data_in_leaf` and `n_estimators` against `tune/mae_model`. A flat picture is the expected one (the grid is flat, D68).

### Backtest runs (`backtest-pressures-edge`, `backtest-tackles-lbs`; job type `backtest`)

The x-axis of every `bt/*` curve is **`bt/step`**: one step per reported week (124 steps: 2019 week 1 to 2025 week 18; 17 weeks in 2019 and 2020, 18 after). `bt/season` and `bt/week` translate a step.

| Chart | What it shows | How to read it |
|---|---|---|
| `bt/cum_improvement_pct` | Better-than-baseline over **every week so far** | **The main chart.** It settles as games accumulate and its last value is the pooled result. Good: above 0 and flat or rising |
| `bt/improvement_pct` | Better-than-baseline for **that week only** | Jumpy, because one week has a limited number of defenders. Read the share of weeks above 0, not single points |
| `bt/cum_coverage_80` | Share of actuals inside P10–P90 over every week so far | Should hover near 0.80; the first steps swing |
| `bt/coverage_80` | Coverage for that week | Normal weekly noise is several points either way (see the numbers table) |
| `bt/mae_model`, `bt/mae_baseline`, `bt/cum_mae_model`, `bt/cum_mae_baseline` | The raw MAEs behind the two improvement curves, in the target's unit (counts: the baseline is its median, D65) | In the cumulative chart the model line should sit under the baseline line |
| `bt/range_param` | Amounts: the conformal shift used that week (positive = range widened). Counts: the negative-binomial dispersion `r` (smaller = wider spread) | Should be stable, not drifting |
| `bt/n`, `bt/step`, `bt/season`, `bt/week` | Rows scored that week; lookups | `bt/n` is a sanity check |
| `lgb/curve_<season>` | The season's opening fit (train on everything before the previous season, hold the previous season out): the model's loss (the Poisson negative log-likelihood) on the training rows and on the held-out season, after each boosting round. The first available season's curve is logged up front, the others at the end | Training should fall steadily; the held-out line should flatten, not climb. A growing gap is overfitting. Both models run 150 rounds (fixed, no early stopping), so a held-out line that turns up late would mean too many rounds |

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


**Real numbers for these two runs**

| Target | `bt/cum_improvement_pct`: season-end values 2019 → 2025 | `bt/improvement_pct`: weekly range, median, share of weeks above 0 | `bt/coverage_80`: weekly 10th–90th percentile (median) | `bt/range_param`: 2019 → 2025 | `bt/n`: rows per week |
|---|---|---|---|---|---|
| pressures | 2.8, 2.6, 2.8, 2.8, 2.7, 2.9, 3.0 (ends +3.0%) | −6% to +11%, median +3.2%, 77% | 0.83–0.88 (0.85) | r 3.19 → 4.03 | 304–402 |
| tackles | 4.0, 5.1, 4.7, 4.7, 4.8, 4.8, 4.8 (ends +4.8%) | −1% to +12%, median +4.8%, 95% | 0.77–0.84 (0.81) | r 8.88 → 8.77 | 226–309 |

How to use it: the tackles line climbs to +4.8% and settles; the pressures line sits between +2.6% and +3.0% at every season end. Pressures' weekly improvement swings from −6% to +11%
(median +3.2%, above 0 in 77% of weeks) with 304–402 defenders a week. Pressures' `bt/coverage_80` runs near 0.85, not 0.80: that is the whole-number effect explained under Calibration, not drift.
`bt/range_param` is the dispersion `r` for both runs; a drifting `r` would mean the spread of outcomes is changing.
For pressures the first `lgb/curve_<season>` is **2020**, not 2019: the 2019 curve would train on seasons before 2018, and PFR has none.

## Versioning

- **Hyperparameters are fixed for the 2026 season.** Retune before 2027 (P10). Never retune mid-season (doc 04: investigate, don't retune, when a target loses to its baseline for 3+ weeks in a row).
- **Weekly fits:** every `nfl weekly run` refits all 11 target models for each week of the season up to the current one, continuing the backtest's walk-forward history, and logs `player-model:<season>-w<NN>`;
  the weekly step scores last week's pre-kickoff projections onto the live scoreboard.
- **Promoting a new configuration** (for example one without market features, or with pruned proxy features) only if it beats this one walk-forward in each season; that is a ✋ checkpoint.
