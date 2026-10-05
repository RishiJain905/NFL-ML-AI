# Model card: team stat totals v1 and the consistency layer (P08)

Five team-level projections for the weekly digest and the accuracy scoreboard (passing yards, rushing yards, sacks made, sacks taken, takeaways), and the **consistency layer** that checks the player and team projections against each other. The player models they sit beside: [player model v1](player-model-v1.md). The design: [documentation/11](../11-prediction-targets.md) (team targets and the consistency rule). **Status:** shipped (D80, D81): **passing yards, rushing yards, sacks made and sacks taken** are refit and projected every week inside the weekly `player` step from 2026 week 5 (`team_model.live_targets`); **takeaways** failed the pre-registered rule and don't ship. The consistency layer runs every week too: the receptions clamp on, the receiving-yards adjustment off (log only).

## What it is

One projection with an 80% range per **team × regular-season game × target**, made on the Tuesday before the game.

| Target (key) | Label (exactly) | Kind | The baseline it must beat | How it is scored |
|---|---|---|---|---|
| Team passing yards (`pass_yds-team`) | `team_games.passing_yards`: **gross** passing yards (sack yards are *not* subtracted; scrambles count as rushing; penalty yards are in neither). Equals the sum of the team's players' `passing_yards` in 100% of team-games | amount (quantile) | (the team's rolling 8-game passing yards + the opponent's rolling 8-game passing yards **allowed**) / 2 | MAE, 80% coverage |
| Team rushing yards (`rush_yds-team`) | `team_games.rushing_yards` (QB runs and scrambles included) | amount (quantile) | the same blend for rushing | MAE, coverage |
| Sacks made (`sacks_made-team`) | the opponent's `sacks_suffered` (whole sacks, one per sack play; `def_sacks` carries half sacks) | count | (own rolling sacks made + the opponent's rolling sacks taken) / 2 | MAE vs the baseline's negative-binomial **median** (D65), Poisson deviance, Brier / ECE of P(≥ 1) |
| Sacks taken (`sacks_taken-team`) | the team's own `sacks_suffered` | count | (own rolling sacks taken + the opponent's rolling sacks made) / 2 | same |
| Takeaways (`takeaways-team`) | the opponent's interceptions thrown + the opponent's fumbles lost in any phase (`fumbles_lost_total`). It equals the team's own `def_interceptions + fumble_recovery_opp` in 99.5% of team-games | count | **the league's rolling per-team-game average** (last 16 weeks with games): takeaways are mostly luck, so the bar is "do you know anything at all" | same, and the deviance must beat the league average |

**Grain and window.** One row per (team, REG game) from 2012 (the rolling windows warm up on 2010–2011 box scores); trained from 2013; refit every week on everything before it (current season weighted 3×, last season 1.5×, D28; `training.sample_weights`). Reported: walk-forward **2019–2025**, 3,742 team-games per target, after two burn-in seasons (2017–2018) whose predictions calibrate the 80% ranges and the count dispersion. Hyperparameters were picked on 2017–2018 only.

## How it works, in plain language

1. **What the teams usually do.** For each stat, a team's last 8 games (any season, playoffs skipped) give what it **produces** and what it **allows**. A game's blend (team's output + opponent's allowed) / 2 is the baseline.
2. **What is different this game.** 54 features: both teams' 8-game form in the stat (and the pace series: plays, dropbacks, rushes, pass rate over expected), the league level, the P02 ratings and the pass / rush matchups, the P03 game model's expected points and margin, the market's spread, total and implied team totals, schedule (home, neutral, division, rest) and roof / wind / temperature. Each target sees only its **own** stat's form (the other four are left out: a choice made on 2017–2018, flat but simpler).
3. **The projection.** LightGBM quantile models (P10 / P50 / P90) for the yards, a Poisson mean + negative binomial for the counts: the same fit as the player model (`fit_player_model`), with settings sized for ~3,000–6,000 training rows.
4. **An honest range.** The range is conformalized on the model's own earlier misses (last 2 seasons), and the counts' dispersion and tail level come from the same history. The probability of at least one sack / takeaway is the negative binomial's `1 − cdf(0)`, recalibrated (Platt) on its own earlier values, and the baseline's goes through the same calibrator.

## Data and label definitions (probed on the real tables, 2026-10-04)

- `team_games` has one row per (team, game) **including playoffs** (534 rows a season to 2020, about 570 from 2021): filter `season_type = 'REG'`. REG 2012–2026 has no nulls in any column used, and 0 games without exactly two rows.
- Passing / rushing yards equal the sum of `player_games` over the team in 100% of team-games. They do **not** equal play-by-play `yards_gained` sums: scrambles are rushing in the box score (about +11 rushing yards a game) but `pass = 1` in play-by-play, and accepted-penalty yards sit in `yards_gained` (about −12 passing yards a game).
- `sacks_suffered` equals the number of sack plays in 100% of team-games; the opponent's `def_sacks` agrees in only 98.5% (half sacks).
- Takeaways: the offense-only fumbles (`rushing + receiving + sack fumbles_lost`) miss special-teams fumbles and match the defense's recoveries in only 91.6% of team-games; `fumbles_lost_total` matches in 99.5%.
- League levels drift: passing yards per team-game 259 (2015) to 225 (2025), takeaways 1.56 (2012) to 1.16 (2025). Any baseline must track the level (a rolling window does; a fixed mean would not).
- The distributions (2019–2025): passing yards 240 ± 74 (variance 23× the mean), rushing 117 ± 51, sacks 2.40 (variance 1.29× the mean), takeaways 1.29 (variance 1.07×: almost exactly Poisson, i.e. luck).
- Weather: `games.temp` / `wind` are observed (null indoors and for ~10% of outdoor games); `weather_forecasts` has `temp_f` / `wind_mph` about a week ahead.

## Features (`features/team_stats.py`; built in about a second, never persisted)

| Family (prefix) | Columns | Timing |
|---|---|---|
| Form (`tm_`, `op_`) | `*_for_l8` / `*_ag_l8` rolling 8-game means of the five stats and of plays, dropbacks, rushes, pass rate over expected, for the team and its opponent | strictly before the game's week |
| League (`lg_`) | the league's per-team-game average of each stat over the last 16 weeks | strictly before |
| Ratings (`rt_`) | P02 off / def pass / rush EPA of both teams, `prior_weight`, the pass / rush matchups in both directions. `def_*` is EPA **allowed**, so a matchup is `off + opponent def` (a test with an asymmetric defense guards the sign) | the as-of row for the week (D44) |
| Context (`ctx_`) | the P03 model-only game model's expected points for / against, margin, total | the walk-forward prediction for that game |
| Market (`mkt_`) | the team's spread (+ = favored), total, implied team totals | closing lines in history, the current line live (leakage rule 5: the `no_market` variant drops the family) |
| Schedule (`sch_`) | home, neutral site, division game, rest days, rest difference | known before the season |
| Weather (`wx_`) | dome flag, temperature, wind: observed in history, forecast for an unplayed game | **mismatch**: training sees the weather that happened, a live run sees a forecast |

No week-late source is used, so no extra lag. `base_<stat>` is the baseline, also a feature. A **future-invariance test per family** scrambles every input from a week onward and checks the earlier rows do not move (`tests/test_team_stats.py`; two deliberate leaks, a same-week join and a league window that includes the game's own week, are both caught).

## The math, with a worked example (2025 week 1, DAL at PHI, PHI's side)

*Passing yards.* PHI's last 8 games (the end of 2024) averaged 192.0 passing yards; DAL's defense allowed 249.6 over its last 8. Baseline = (192.0 + 249.6) / 2 = **220.8**. The model's P50 is **223.7** (the market's implied PHI total of 28.0 and 47.5 points overall, and PHI's pass-light tendency, net out slightly up); the raw quantiles moved by the conformal shift of +5.9 yards each side give a range of **147 – 368**. PHI threw for **152**: inside the range, below the baseline.

*Sacks made.* Baseline mean 1.875 (median 2). Model mean 2.28 (negative binomial with dispersion r = 9.9), median 2, 80% range 1 – 4 (tail 0.15), P(≥ 1) = 0.85 against the baseline's 0.75. PHI's defense got **0**.

*Takeaways.* League average 1.21, model mean 1.47, P(≥ 1) = 0.75 against 0.68. PHI took the ball away **once**.

One game says nothing about accuracy; it shows what a row means. The scoreboard is the evidence.

## Training and tuning (every round, honestly)

All tuning used 2017–2018 walk-forward weeks only (never 2019–2025).

1. **First pass (scratch):** does anchoring the boosters to the baseline (LightGBM `init_score` = baseline, so the trees only learn a correction) or giving each target all five stats' form beat plain trees on its own stat's form? No: plain own-stat trees were as good or better on yards (pass +5.0% vs +4.1% anchored) and the sack / takeaway differences were inside noise, so the simpler `fit_player_model` was kept.
2. **Grid scans (scratch):** 18 + 12 configurations per target over leaves {4, 8, 15}, min leaf {40, 100, 200}, rounds {30 … 600}: the landscape was flat (best to worst within ~1–2% of MAE) and the player model's defaults (15 leaves, 300 rounds) overfit the counts (rounds 60–100 beat 300).
3. **The recorded sweep** (`TUNE_GRID`, 16 configurations, W&B `tune-<key>`, one run per target): leaves {4, 8} × min leaf {100, 200} × rounds {60, 100, 200, 400}; an amount is picked by MAE, a count by Poisson deviance (a count's median-MAE is quantized). Winners (`team_model.TEAM_PARAMS`):

| Target | Leaves | Min leaf | Rounds | Tune MAE / deviance | Baseline | W&B |
|---|---|---|---|---|---|---|
| pass_yds | 4 | 100 | 200 | 57.71 (+5.2%) | 60.88 | `gaxfnz4s` |
| rush_yds | 4 | 200 | 100 | 38.55 (+2.4%) | 39.48 | `q8r27f02` |
| sacks_made | 8 | 200 | 60 | dev 1.2916 | 1.3271 | `s95wxvv2` |
| sacks_taken | 8 | 200 | 60 | dev 1.2900 | 1.3271 | `sry9mcem` |
| takeaways | 8 | 200 | 60 | dev 1.2457 | 1.2588 | `plmhdt1v` |

The 2017–2018 gains (+5.2% on passing yards) shrank on 2019–2025 (+2.5%): a tuning window always flatters a model, and the reported window is the honest one. Other settings: learning rate 0.05, feature fraction 0.8, bagging 0.8, L2 1.0, conformal seasons 2, seed 7 (`PlayerModelConfig` defaults).

## Results: walk-forward 2019–2025 (3,742 team-games per target, refit every week)

"Improvement" is over the baseline on the same rows; a count's baseline is its negative-binomial **median** (D65). "No market" = the same run without the closing-line features.

| Target | MAE model | MAE baseline | Improvement | No market | 80% range held | Seasons better |
|---|---|---|---|---|---|---|
| Passing yards | 55.23 | 56.65 | **+2.5%** | +1.6% | 80.8% | 7 / 7 |
| Rushing yards | 38.19 | 38.81 | **+1.6%** | +1.3% | 80.8% | 6 / 7 |
| Sacks made | 1.3397 | 1.3429 | +0.2% | +0.06% | 80.0% | 6 / 7 (no market 4 / 7) |
| Sacks taken | 1.3423 | 1.3431 | +0.06% | −0.04% | 79.8% | 5 / 7 (no market 3 / 7) |
| Takeaways | 0.8699 | 0.8688 | −0.1% | +0.09% | 82.2% | 2 / 7 |

The count targets on the metrics that can tell a mean from a median:

| Target | Poisson deviance model | baseline | Brier of P(≥ 1) model | baseline | ECE of P(≥ 1) model | baseline |
|---|---|---|---|---|---|---|
| Sacks made | 1.346 | 1.369 (−1.7%) | 0.1070 | 0.1105 | 0.005 | 0.031 |
| Sacks taken | 1.346 | 1.369 (−1.7%) | 0.1070 | 0.1104 | 0.004 | 0.030 |
| Takeaways | 1.218 | 1.229 (−0.9%) | 0.2050 | 0.2064 | 0.008 | 0.006 |

By season, passing yards: +1.5, +3.1, +4.1, +2.5, +1.5, +3.1, +1.6% (2019 … 2025); rushing: +0.5, +2.0, +2.6, −0.5, +4.0, +0.7, +1.8%. Counts' MAE by season swings by a few percent either way (2020 is the bad year for sacks: −4.8% and −4.3%) because a whole-number median flips at the half-sack.

### The pre-registered ship rule, applied

*Ship if the pooled MAE beats the baseline over 2019–2025 (a count's baseline = its median), in at least 5 of 7 seasons, with 80%-range coverage between 0.75 and 0.88; takeaways also need a lower Poisson deviance than the league average.* (`ship/*` summary keys; `team_runs.ship_rule`.)

| Target | Verdict | Why |
|---|---|---|
| `pass_yds-team` | **passes** | 7/7 seasons, +2.5%, coverage 0.81; still passes without market lines (6/7, +1.6%) |
| `rush_yds-team` | **passes** | 6/7, +1.6%, 0.81; passes without lines |
| `sacks_made-team` | passes by the letter, thin | +0.24% MAE (a tie in practice), 6/7, 0.80. The case for it is the deviance (−1.7%) and the P(≥ 1) calibration, not the MAE. **Fails without market lines** (4/7, +0.06%) |
| `sacks_taken-team` | passes by the letter, thinnest | +0.06% MAE, exactly 5/7, 0.80; deviance −1.7%. **Fails without market lines** (3/7) |
| `takeaways-team` | **does not ship** | 2/7 seasons, MAE −0.1% (the model's median is 1 almost always, like the baseline's); deviance is 0.9% lower and the Brier a hair better, but the model over-projects the mean (1.345 vs 1.286 actual; the league rate is falling about 0.03 a year and the 16-week window lags) and its P(≥ 1) is slightly *less* calibrated than the league average's (ECE 0.008 vs 0.006) |

## Is this good? (Why team stats barely beat a rolling average)

- **The noise floor is huge.** A team's passing yards in one game have a standard deviation of 74 on a mean of 240, and almost all of it is game script (who is ahead, turnovers, the weather on one drive). Even a *cheating* predictor that knew each team's whole-season average for passing yards (computed including the game itself) would still miss by **53.1** yards a game. The ladder, 2019–2025 passing yards MAE: the league average **58.6** → the rolling baseline **56.65** → this model **55.23** → the cheating team-season average **53.1**. The model closes about 60% of the gap between "know nothing about the team" and "know its true level". Rushing is the same story (40.5 → 38.8 → 38.2 → 36.7, also about 60%).
- **doc 11's rule of thumb** (5–15% over a rolling average for player yardage) doesn't transfer: a team's yards add up 60-odd plays, so the *average* is what is predictable and a good rolling average already has it. +2.5% on passing yards is a real, season-after-season result, not a big one.
- **Counts are luck.** With variance barely above the mean (sacks 1.29×, takeaways 1.07×) the outcome is nearly Poisson: **even if our mean were exactly right**, the median would miss by 1.32 sacks a game (computed from the model's own distribution); the model misses by 1.34 and the baseline by 1.34. There is only ~0.02 of MAE to win, which is why deviance and the probability of at least one are the honest scores here, and why a takeaway projection is mostly the league average ("takeaways are mostly noise, so be honest").
- **The lines carry the gain.** The market's implied team total is the model's biggest driver for passing yards (25% of the gain in the live fit), sacks (26–27%) and a top driver for takeaways (spread, 13%). Without lines passing yards drops from +2.5% to +1.6% and the sack models stop beating their baseline. Historical lines are closing lines (leakage rule 5), so the reported numbers are a little more optimistic than a Tuesday run sees.

## Reading the W&B charts

All team-model runs are in W&B group **`track1-team`**, tagged `p08`, `team` and `target:<name>` (`no-market` on the variant, `smoke` on test runs).

### The metrics in one minute

| Metric | What it measures | Better | Our 2019–2025 values |
|---|---|---|---|
| **MAE** | Average absolute miss of the P50 (counts: the negative-binomial median) | Lower | Passing yards 55.2 (baseline 56.7) |
| **Improvement %** | 100 × (baseline MAE − model MAE) / baseline MAE, same rows | Higher; > 0 beats it | −0.1% to +2.5% |
| **Coverage (80% range)** | Share of results inside P10–P90 | ≈ 0.80 (ship bar 0.75–0.88) | 0.80–0.82 |
| **Poisson deviance** (counts) | `2 (y ln(y/μ) − (y − μ))`, averaged; the proper score of a count's mean | Lower | Sacks 1.346 (baseline 1.369) |
| **Brier / ECE of P(≥ 1)** (counts) | Squared miss of the probability of at least one; its calibration gap | Lower | Takeaways Brier 0.205 (0.206) |
| **Range parameter** | Amounts: the conformal shift in yards (can be negative); counts: the dispersion r (10,000 = Poisson) | Stable | Passing yards ≈ +6; sacks r ≈ 10 |

### Backtest runs (`backtest-<key>`, `backtest-<key>_nomarket`; job type `backtest`)

The x-axis of every `bt/*` curve is **`bt/step`**: reported weeks so far (1 = 2019 week 1, 124 = 2025 week 18).

| Chart | What it shows | How to read it |
|---|---|---|
| `bt/mae_model`, `bt/mae_baseline` | That week's MAE (about 32 team-games) | Jumpy: one week is 32 rows |
| `bt/cum_mae_model`, `bt/cum_mae_baseline` | MAE of every row so far | **The main chart.** The two lines sit within 1–3% of each other; the model line should end below. For takeaways they coincide |
| `bt/improvement_pct`, `bt/cum_improvement_pct` | Weekly and cumulative improvement | Settles around the table's value; weekly values below 0 are normal |
| `bt/coverage_80`, `bt/cum_coverage_80` | Share inside the range | Cumulative ≈ 0.80 |
| `bt/deviance_model`, `bt/deviance_baseline`, `bt/cum_deviance_model`, `bt/cum_deviance_baseline`, `bt/cum_deviance_improvement_pct` (counts) | Poisson deviance of the mean | **The main chart for counts**: the model's cumulative line should sit below the baseline's (about 1–2% for sacks, 1% for takeaways) |
| `bt/range_param`, `bt/n`, `bt/season`, `bt/week` | The week's conformal shift / dispersion; rows; lookups | `range_param` drifts slowly |
| `lgb/curve_<season>` (2020–2025, end of run) | The season's opening fit replayed with the previous season held out: training vs validation loss per boosting round | Validation should flatten; with 60–200 rounds it does |

**End-of-run panels:** `by_season` (every metric per season), `mae_by_season`, `coverage_by_season` (vs 0.80), `deviance_by_season` (counts), `accuracy_scoreboard` (one row per week), `feature_importance` (gain of the last fit, top 25), `reliability_p_ge1` + `reliability_table` (counts: predicted P(≥ 1) vs how often it happened, by decile; on the diagonal = calibrated), `predictions` (2025 rows).

**Summary keys:** `n_scored`, `mae_model`, `mae_baseline` (counts: the median baseline), `mae_baseline_mean` (the raw rolling mean), `improvement_pct`, `coverage_80`, `mean_actual` / `mean_projection` / `mean_baseline`, `spearman_outperformance`; counts add `deviance_model`, `deviance_baseline`, `deviance_improvement_pct`, `brier_p_ge1`, `brier_baseline_p_ge1`, `ece_p_ge1`, `ece_baseline_p_ge1`, `share_ge1`; the rule: `seasons_beating_baseline`, `seasons`, `ship/beats_baseline`, `ship/seasons_ok`, `ship/coverage_ok`, `ship/deviance_ok` (takeaways), `ship/pass` (1.0 / 0.0).

**Final runs:** `g9vi0jrn` (pass_yds), `67epjugt` (rush_yds), `sk2m9odq` (sacks_made), `c2eify8g` (sacks_taken), `oq6gwrf5` (takeaways); no market `soldi15b`, `gnu0s5b2`, `o10oxqks`, `aty6osc0`, `we19zino`.

### Sweeps (`tune-<key>`; job type `tune`)

One run per target: a point per configuration (`tune/step`; the settings as `tune/num_leaves`, `tune/min_data_in_leaf`, `tune/n_estimators`; the scores `tune/mae_model` / `tune/deviance_model` and the baseline's), the full `tune_results` table and `best/*` in the summary. The landscape is flat.

### Weekly fit (`train-<season>-w<NN>`, job type `train`) and scoreboard (`scoreboard-team-<season>-w<NN>`, job type `eval`)

`projections_teams` (the week's projections), summary `projections`, `teams`; the `team-model` artifact (boosters + `meta.json`, description = this card, alias `<season>-w<NN>`; `production` only with `promote=True`). The scoreboard run logs `scoreboard/improvement_<target>_team` and `scoreboard/coverage_<target>_team` by week and the `accuracy_scoreboard_team` table. **Not scheduled yet** (see Wiring).

## Outputs

- `predictions_teams.parquet` (one row per team-game × target, `team_runs.TEAM_PRED_SCHEMA`): identity (season, week, game, kickoff, team, opponent, home), the target (name, label, kind, unit), the projection (`p10`, `p50`, `p90`, `mean`, counts also `p_ge1`), the baselines (`baseline`, `baseline_p50`, `baseline_p_ge1`), `outperformance` (amounts: P50 − baseline; counts: mean − baseline), versioning (`model_version`, `feature_hash`, `trained_through`, `created_at`) and, once played, `actual` / `played`. Backtests (`runs/backtests/team/<key>/`) add `_p10_raw`, `_p90_raw` (amounts) or `_p_raw` (counts), `range_param`, `range_tail`, `scale`, `n_train`: what a weekly refit seeds its calibration from.
- Scoreboard rows (`SCOREBOARD_SCHEMA`, `position_group = "TEAM"`, mode `backtest` / `live`): `runs/backtests/team/scoreboard.parquet`; counts fill `brier_model`, `brier_baseline`, `calibration_ece` for P(≥ 1).

## Known biases and limits

- **Closing lines** in the market features (above); they drive most of the gain.
- **The weather mismatch.** Wind is a top-6 driver of passing yards in the live fit; in training it is what happened, live it is a forecast.
- **Takeaways drift.** The league rate falls about 0.03 a year; the 16-week league window and the model both lag it (projection 1.35 vs 1.29 actual).
- **Whole-number medians.** A count's P50 is a median, so its MAE moves in steps; judge counts on deviance and P(≥ 1).
- **No QB / injury features of its own.** A change of quarterback reaches the team model only through the P03 context (expected points) and the ratings; the player models carry the QB.
- **Playoffs are skipped**, so a team's week-1 form is its last regular-season games.
- **A yards P50 is a median.** Rushing is right-skewed: its projections average 109 against a 117 actual mean (−6.6%); passing yards are nearly symmetric (239 vs 240). Fine for MAE, but **do not sum medians** (see the consistency section).
- **A tuning window flatters**: passing yards +5.2% on 2017–2018 became +2.5% on 2019–2025.

## Consistency layer (`models/consistency.py`)

Projections made by separate models should agree where football says they must. The layer **checks** three identities on prediction frames and **lightly adjusts** where that is safe; every check is logged as an `inconsistency/*` summary.

### 1. Receptions ≤ targets (WR/TE, the same player-game)

If a player can't catch more than he is thrown, each quantile of his receptions can't exceed the same quantile of his targets (nor the mean). `reconcile_receptions` compares P10, P50, P90 and mean and clamps a violating reception level to the targets level, counting the changes. Over 29,266 WR/TE player-games (2019–2025): **0.01%** of P50s violate it (3 rows), 0.08% of P10s, **1.1% of means** (median excess 0.02 receptions). 337 rows were adjusted. Adjusting changes the receptions MAE by **−0.009%** (1.2019 → 1.2018; no season worse, three better): it is free, so it is **on**.

### 2. Receivers ≈ QB ≈ team passing yards

Per team-game: WR/TE `rec_yds` + RB receiving (`scrim_yds − rush_yds` per back, floored at 0) vs the main QB's `pass_yds` vs the team model's `pass_yds`. For **results** the identity is exact for the team (receivers' yards = the team's passing yards within 10% in 98.4% of team-games; the rest are laterals and non-QB throws) and looser for the main QB (90.3%: when a backup plays, the starter's yards are only part of the receivers' total).

**Compare means, never medians.** Each player's yardage is right-skewed, so his median sits well below his mean, and the sum of medians is biased low: the WR/TE P50s add to **160 yards** a team-game against an actual **204**. The amount models have no mean (`mean` is the P50), so the layer estimates one with **Swanson's rule, `0.3 × P10 + 0.4 × P50 + 0.3 × P90`**, which lands within 1–4% of the actual mean of every P06 yardage target (receiving yards 25.6 vs 26.1 actual, passing 235.7 vs 233.8, scrimmage 45.0 vs 46.1, rushing 32.3 vs 33.7; the median says 20.4, 233.2, 40.4, 28.1).

Backtest 2019–2025, 3,742 team-games (relative gap `(a − b) / b`; W&B run `4861i1dg`):

| Gap | On medians: bias / median \|gap\| / share beyond ±10% | On means (Swanson): bias / median \|gap\| / share beyond ±10% |
|---|---|---|
| receivers vs QB | −16.6% / 16.4% / 73% | **+0.3%** / 7.2% / 36% |
| receivers vs team model | −18.9% / 18.2% / 77% | **−2.8%** / 7.1% / 35% |
| QB vs team model | −2.4% / 4.9% / 19% | −2.9% / 4.1% / 13% |

On means the projections agree on average (bias under 3%); the remaining 4–7% median gap is the real, independent noise of separate models. On medians they look 17–19% inconsistent, which is a measurement artefact, so the layer reports means. By season the median |gap| on means (receivers vs QB) is 6.2–7.9% and the bias within ±1%.

### Adjust or log only? (the team-level adjustment)

`reconcile_rec_yds` scales a team-game's WR/TE `rec_yds` (P10, P50, P90, mean together) so the receivers' expected total meets the anchor's: `k = (anchor mean − RB receiving) / WR/TE mean`, clipped to [0.8, 1.25] and shrunk by `strength` (`1 + strength × (k − 1)`). The pre-registered rule: adjust only if it lowers the pooled WR/TE receiving-yards MAE, in at least 5 of 7 seasons, with the 80% coverage moving by at most 1 point.

| Anchor | Strength | MAE before → after | Change | Seasons better | Coverage before → after |
|---|---|---|---|---|---|
| team model | 0.25 | 16.488 → 16.440 | −0.29% | 7 / 7 | 81.9% → 82.2% |
| team model | **0.5** | 16.488 → 16.411 | **−0.47%** | **7 / 7** | 81.9% → 82.4% |
| team model | 1.0 | 16.488 → 16.407 | −0.49% | 7 / 7 | 81.9% → 82.8% |
| main QB | 0.5 | 16.488 → 16.407 | −0.49% | 7 / 7 | 81.9% → 82.1% |
| main QB | 1.0 | 16.488 → 16.399 | −0.54% | 7 / 7 | 81.9% → 82.1% |

**Rule on player MAE alone: adjust (team anchor, strength 0.5)**, the lightest setting with 95% of the full gain (`decide` prefers the team anchor because the QB anchor leans on who turned out to be the main QB, which a live run doesn't know). It lowers the receiving-yards MAE by 0.47% in all seven seasons (−0.2% to −0.7%) and halves the receivers-vs-team gap (7.1% → 3.6% median, share beyond 10% 35% → 10%). It touches only WR/TE `rec_yds`.

**But the final decision is LOG ONLY, because of the players-to-watch list** (a second pre-registered check, run before switching it on: the adjustment changes a shipped P06 projection, and the watch list ranks players by `outperf_z`, so it changes who is picked). Rule: adjust live only if the overall 2019–2025 watch-list hit rate falls by at most 0.5 points and the WR/TE picks' rate by at most 1 point. `player_watch.watchlist_backtest` on the P06 backtest projections, as they are vs after `apply_consistency(..., rec_yds_anchor=<this team pass_yds backtest>, strength=0.5)` (2,480 picks each, base rate 42.2% unchanged, 0 short weeks):

| | Hit rate as is | After the adjustment | Change |
|---|---|---|---|
| All picks (offense + defense) | 65.28% (1,619 / 2,480) | 64.92% (1,610 / 2,480) | **−0.36** (passes, limit 0.5) |
| Offense (1,240) | 65.00% | 64.27% | −0.73 |
| Defense (1,240) | 65.56% | 65.56% | 0 (never touched) |
| WR/TE picks | 63.26% (272 / 430) | 61.23% (278 / 454) | **−2.03** (fails, limit 1.0) |
| QB picks / RB picks | 70.65% / 61.99% | 71.43% / 61.70% | +0.8 / −0.3 |

177 picks swap places: the 177 dropped hit 58.8%, the 177 added 53.7% (WR/TE gain 24 picks from the QB and RB slots, since the soft group caps relax). A bootstrap over weeks puts the WR/TE change at −2.0 points with a 95% interval of [−5.9, +1.8], so it may be noise, but the rule is the rule. Other settings (post hoc, for information only): team anchor 0.25 → WR/TE −2.21; team anchor 1.0 → overall −1.29, WR/TE −5.32; QB anchor 0.5 → overall −0.24, WR/TE −1.29. None meets the WR/TE limit. Why: scaling toward a team total that is a few percent above the players' means lifts most WR/TE gaps to baseline together, which moves the marginal picks, not the good ones; the ranking by gap to baseline is a different job from matching a team total.

So `apply_consistency(..., rec_yds_anchor=None)` (the default) **only logs the team-level gaps**; the receptions ≤ targets clamp stays on (it changes no main-target row and no watch-list pick). The team-level adjustment code stays available (`rec_yds_anchor="team"`), and it refreshes `outperformance` and `outperf_z` of the rows it changes (the scale is recovered from the frame), so anyone who does switch it on gets a consistent watch list.

### The summary keys and the function

`inconsistency_summary` gives, per gap (`gap_recv_qb`, `gap_qb_team`, `gap_recv_team`, each also `_p50`, and `gap_act_*` for the identity on actual results): `inconsistency/<gap>_median_abs`, `_bias`, `_share_gt10`; and `inconsistency/rec_gt_tgt_share_<p10|p50|p90|mean>`, `_median_excess_<level>`, `_pairs`, `_adjusted_rows`; `inconsistency/team_games`; after the default adjustment, the same gap keys under `inconsistency/after/`. The evaluation run (`run_eval`, W&B group `track1-player`, job `eval`, tags `p08`, `consistency`) also logs `cons/<gap>` by season (step `cons/season`), the tables `by_season`, `adjustment_effects`, `adjustment_by_season`, `receptions_by_season`, histograms `hist/gap_*` and the bar `adjustment_mae_change`, and `decision/*` in the summary.

## Wiring (as built, D80 / D81)

- **CLI:** `nfl backtest team --target <name|all> [--no-market] [--seasons 2019-2025] [--smoke]` (`team_runs.run_backtest`), `nfl tune team --target <name|all>` (`run_tune`, scored on 2017–2018), `nfl train team --season S --week N [--promote]` (`run_train`), `nfl consistency` (`consistency.run_eval`: measures the layer on the backtests, changes nothing).
- **Weekly run:** inside the `player` step (`weekly._team_fit`, fail-soft: any error is a note in the step's detail, never `degraded`): first `team_runs.score_weeks(season, 1..N-1)` (the saved pre-kickoff team projections, upserted as `mode = live` rows, W&B `scoreboard-team-<season>-w<NN>`), then `run_train(season, N, promote=…)` for the shipped targets → `runs/<season>/week<NN>/predictions_teams.parquet`, `team_walkforward.parquet` and `mode = backtest` scoreboard rows next to the week folder, boosters + `meta.json` (with each target's fitted shift / dispersion / tail / calibration under `fitted`) in `models/team-model/<season>-w<NN>/`, and the `team-model` artifact (`production` with `--auto`, D72). Then `weekly._consistency` runs `apply_consistency(players, teams, receptions=True, rec_yds_anchor=None)` on the week's unstarted player rows (D81): the receptions clamp is applied and the file rewritten atomically only if a row changed; the `inconsistency/*` numbers go to `consistency.json`, `run_summary.json` and the pipeline W&B run (`consistency/*`).
- **Settings** (`config/settings.yaml`; `AppConfig.team_model` / `consistency`):
  ```yaml
  team_model:
    live_targets: [pass_yds-team, rush_yds-team, sacks_made-team, sacks_taken-team]
  consistency:
    receptions: true
    rec_yds_anchor: null   # log only (D81); team | qb to scale WR/TE receiving yards
    strength: 0.5
  ```
  Tree settings stay in code (`team_model.TEAM_PARAMS`); `team_model.per_target.<key>` overrides them.
- **Shared scoreboard:** team rows share `runs/<season>/accuracy_scoreboard.parquet` with the players' (`position_group = "TEAM"`). The digest's player report card and scorecard drop them (`digest.players._mode_rows`); the season dashboard gives them their own `player/*_team` series and keeps them out of `player/*_all`; drift treats TEAM as one more group (`player_vs_baseline`, and `player_prob_vs_baseline` for the sacks' P(≥ 1)). Every upsert runs under an OS lock and is written atomically (D85).

## Verification

- **Leakage:** a future-invariance test per feature family on a synthetic league (`tests/test_team_stats.py`, with mutation checks that the test catches a same-week join and a league window including the game's week); the walk-forward harness's own boundary check; `tests/test_team_runs.py` scrambles every feature and label after a week and checks the earlier projections do not move.
- **Contracts:** labels equal the box score and the opponent's numbers; rolling form equals an independent mean over the previous 8 games across the season boundary; the matchup sign test; market orientation; weather observed vs forecast; weekly fit never projects after kickoff and keeps saved pre-kickoff rows of started games, and never promotes unless asked; the live scoreboard scores only rows saved before kickoff.
- **Consistency:** `tests/test_consistency.py` checks every number on hand-built frames, including that an adjusted row's `outperf_z` follows its new P50 and that `run_eval` saves and logs one run.

## Versioning

- Artifact `team-model` (type `model`), alias `<season>-w<NN>` per weekly fit; `production` only with `promote=True` (never set by a backtest or a smoke run).
- Model version string in every projection: `team-model-v1:<season>-w<NN>` (backtests: `team-model-v1:backtest`).
- Settings: `config/settings.yaml` → `team_model` (`live_targets`: the 4 shipped targets) and `consistency`; the feature hash is in each run's config.
