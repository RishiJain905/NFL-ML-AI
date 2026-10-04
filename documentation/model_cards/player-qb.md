# Model card: player model v1, quarterbacks (P06)

**Family:** Track 1 model C ([04 → C](../04-track1-models.md#c-player-model)) · **Targets:** passing yards, EPA per dropback · **Chosen:** 2026-10-04 (D64 design; D68 settings and ship decision, ✋ Rishi) · **Production:** the weekly refit, W&B artifact `player-model:<season>-w<NN>` (all 11 targets in one artifact; promotion is described in the overview) · **Last tuned:** 2026-10-04 · **Reported window:** walk-forward 2019–2025

**Overview:** [Player model v1](player-model-v1.md) (all 11 targets, the scoreboard, the digest) · **Guide:** [Player projections guide](../guides/player-projections.md) · **Decisions:** D64 (design), D65 (counts vs the median), D66 (the Friday view), D68 (tuning and the ship decision)

**Code:** `features/player_data.py` (history), `features/player.py` (rows, `own_*` / `use_*` / `team_*` / `rip_*` / `avail_*`, baselines), `features/player_efficiency.py` (`eff_*`), `features/player_opponent.py` (`opp_*`), `models/player_model.py` (the LightGBM models, ranges, SHAP), `models/player_schema.py` (targets and pools), `models/player_runs.py` (backtest, tuning, weekly fit, scoreboard) · **Tables:** `features/player_features.parquet` (`nfl features player`), `runs/backtests/player/<target>-<group>/` (`nfl backtest player`), `runs/<season>/week<NN>/predictions_players.parquet` (`nfl train player`)

## What it is

Two models for the quarterback who is expected to start a game, one per number:

| Target | Unit | What it is | Role |
|---|---|---|---|
| **Passing yards** (`pass_yds`) | yards | His passing yards in the game (box score) | The **main stat** for quarterbacks: it ranks the QBs on the watch list |
| **EPA per dropback** (`pass_epa`) | EPA | His average expected points added per dropback (passes, sacks and scrambles): how well the offense moved with him in the game | Efficiency, independent of how many times he dropped back |

**Who is scored (the pool).** The team's **main QB in that game**: the quarterback with the most dropbacks. A backup who takes mop-up snaps is not a QB row, and a starter hurt early may
not be either. **Who is projected live:** the expected starter that our game model's resolver names (the schedule's projected starter, else the depth chart, else last game's QB; replaced by the
next healthy QB if he is listed Out or Doubtful). If that player doesn't end up as the main QB, the projection is logged as "not played" and is not graded. Backtests only contain
games where the QB did play as the main QB: 3,733 QB games over 2019–2025 (507–544 a season; 124 weekly steps).

## How it works, in plain language

**An amount (yards, EPA) is a range, not one number.** Three LightGBM models (decision-tree ensembles) are fitted to the same history, each with a *quantile* objective:
one learns the 10th percentile ("a bad day"), one the median ("a typical day": the **projection**, P50) and one the 90th ("a big day"). P10–P90 is an
**80% range**: about 8 games in 10 should land inside it.

**The conformal shift.** Quantile models are rarely calibrated out of the box, so the range is checked against the model's own earlier misses. At every refit, the
last two seasons of walk-forward predictions are scored: how far outside its range was each actual value (or how far inside)? The 80th percentile of that distance
moves both ends of the range by the same amount (a *conformal* correction), so the range holds about 80% of outcomes. A positive shift widens the range, a negative
one narrows it. It is fitted on predictions made before each week, never on the week being predicted.

**Both QB targets are amounts.** Passing yards: the conformal shift averaged **+18.2 yards in 2019** (the raw quantile range was too narrow, so it was widened) and
**+9.2 yards in 2025**. EPA per dropback: +0.052 in 2019, +0.040 in 2025.

**The baseline it has to beat (D64, `documentation/11`).** *Player rolling*: half his average over his last 4 games this season, half his season-to-date average, pulled toward
last season's average with 3 pseudo-games (so early in the year last season counts a lot). No game yet this season: last season's average. No history at all (a rookie): the
league average for players with his snap share in their previous game, taken from earlier seasons only. `outperformance` = projection − baseline.

For quarterbacks the baseline uses the QB's own games as the main QB: 90.5% of rows use the rolling mix, 7.4% use last season's
average (no game yet this season), 2.1% fall back to the league average for his role (a rookie or a QB new to the starting role).

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
| `pass_yds-qb` | 7 | 200 | 150 | [rcyuv6pl](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/rcyuv6pl) |
| `pass_epa-qb` | 7 | 200 | 150 | [cygm4hbw](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/cygm4hbw) |

Both QB models settled on small trees (7 leaves, 200 rows per leaf, 150 rounds), which fits the thin data: only 507–544 scored main-QB games a season.
Tuning moved passing yards from +4.3% to +5.0% and EPA per dropback from +3.6% to +4.7%.

**Tuning (D68).** A 12-point grid per target (`num_leaves` 7 / 15 / 31 × `min_data_in_leaf` 50 / 200 × `n_estimators` 150 / 300, learning rate 0.05), scored by a walk-forward over
every other week of **2017–2018 only** (pressures: late 2018, its first PFR season), so none of the reported 2019–2025 weeks chose a setting. The grid is flat (best to worst
differs by 0.9–3.1% in MAE), so tuning changed little. Defaults that were not tuned: learning rate 0.05, feature fraction 0.8, bagging fraction 0.8 (every iteration), L2 penalty 1.0, seed 7.

## Results: walk-forward 2019–2025

Every week was projected by a model fitted only on earlier weeks (two burn-in seasons, 2017–2018, calibrate the range). Model and baseline are scored on exactly the same rows.
"Rank skill" is the rank correlation between *projection − baseline* and *actual − baseline*.

| Target | Rows scored | MAE model | MAE baseline | Better than baseline | vs season-to-date mean | Range coverage | Rank skill | Seasons beating baseline | No-market variant |
|---|---|---|---|---|---|---|---|---|---|
| **passing yards** (yards) | 3,733 | 56.6 | 59.5 | **+5.0%** | +10.7% | 80.5% | 0.29 | 7 of 7 | +4.6% |
| **EPA per dropback** (epa) | 3,733 | 0.230 | 0.241 | **+4.7%** | +10.3% | 80.3% | 0.30 | 7 of 7 | +3.9% |

- **Both QB targets ship (D68):** each beats its baseline pooled and in every one of the 7 seasons, with 80.5% / 80.3% range coverage.
- **Market-free check (research, closing-line rule 5 of `documentation/04`).** With the three `team_mkt_*` features removed (123 features), passing yards is +4.6% (the market features add 0.4 points) and EPA per dropback +3.9% (the market features add 0.9 points). The market's implied points carries real information about the offense's quality, but the model still beats its baseline without it. Live lines are earlier
  than closing lines, so the live number will sit between the two. The remaining `team_*` inputs (our own game model's predicted points and expected margin) use no market data.
- **First round vs final.** Round 1 (before tuning, before D65) measured +4.3% and +3.6%; tuning added 0.7 and 1.1 points:

| Target | First round (pre-tuning, pre-D65) | Final, vs the raw rolling mean | Final, vs the baseline used for shipping |
|---|---|---|---|
| passing yards | +4.3% | +5.0% | +5.0% |
| EPA per dropback | +3.6% | +4.7% | +4.7% |

**Passing yards by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 512 | 57.5 | 60.3 | +4.6% | 83.2% |
| 2020 | 507 | 61.4 | 62.5 | +1.7% | 77.1% |
| 2021 | 540 | 57.3 | 61.2 | +6.3% | 84.4% |
| 2022 | 542 | 52.4 | 56.4 | +7.0% | 79.5% |
| 2023 | 544 | 57.2 | 59.3 | +3.6% | 79.6% |
| 2024 | 544 | 55.4 | 58.6 | +5.6% | 78.9% |
| 2025 | 544 | 55.1 | 58.7 | +6.2% | 81.1% |
| **2019–2025** | **3,733** | **56.6** | **59.5** | **+5.0%** | **80.5%** |

**EPA per dropback by season**

| Season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 2019 | 512 | 0.232 | 0.249 | +6.6% | 80.7% |
| 2020 | 507 | 0.227 | 0.236 | +3.9% | 81.1% |
| 2021 | 540 | 0.241 | 0.255 | +5.6% | 79.4% |
| 2022 | 542 | 0.207 | 0.215 | +3.5% | 81.9% |
| 2023 | 544 | 0.232 | 0.242 | +4.1% | 78.1% |
| 2024 | 544 | 0.235 | 0.243 | +3.4% | 81.4% |
| 2025 | 544 | 0.236 | 0.251 | +5.8% | 79.8% |
| **2019–2025** | **3,733** | **0.230** | **0.241** | **+4.7%** | **80.3%** |

Passing yards is weakest in 2020 (+1.7%) and strongest in 2022 (+7.0%); EPA per dropback is weakest in 2024 (+3.4%)
and strongest in 2019 (+6.6%). With 507–544 games a season, a season's gain moves by a point or two from sampling alone, so read the seven seasons together rather than ranking them.

## Is this number good?

**Yes for a quarterback, with honest limits.** `documentation/11` calls a 5–15% gain over a good rolling average a solid result for player yardage. Passing yards (+5.0%) sits at the
bottom edge of that band and EPA per dropback (+4.7%) just under it, for a reason you can see in the data:

- **A QB's game is noisy.** Passing yards have a mean of 234 and a standard deviation of 77 from game to game. The baseline misses by 59.5 yards and the model by
  56.6: the model is about 3 yards closer on a typical start, and most of the remaining miss is game script, weather, turnovers and in-game injuries
  nobody can see on Tuesday. EPA per dropback: mean 0.067, spread 0.31, model miss 0.230 against 0.241.
- **The baseline is already strong.** Against a plain season-to-date mean the model is +10.7% on yards and +10.3% on EPA.
  The rolling baseline (recent form plus a pull toward last season) takes about half of that gain by itself.
- **It is consistent.** It beats the baseline in 7 of 7 seasons and in 73% (yards) / 76% (EPA)
  of the 124 individual weeks. The gain is small but not a fluke of one season.
- **The ordering matters more than the level.** Among QBs with a real role (snap share ≥ 50%), the top 10% by `outperf_z` (the biggest projected jumps) beat their baseline
  **68.4%** of the time for yards and **70.9%** for EPA, against base rates of 47.5% and 50.0%
  (a QB beats his own mean about half the time). The digest's actual picks (the few biggest jumps per week, with team and group caps, D69) do slightly better than this broad slice: 71.5% against the same 47.5% base rate (see the [overview](player-model-v1.md)). That is the property the "players to watch" list uses.
- **What would worry us:** a QB yardage gain far above the 5–15% band would call for a leakage check before any celebration. Ours is at the low end, which is what a noisy target with a strong baseline looks like.

## Where the gain comes from

| QB games already played this season | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| 0 games this season | 438 | 60.4 | 64.2 | +6.0% | 78.5% |
| 1-2 games | 683 | 54.0 | 57.9 | +6.8% | 81.7% |
| 3-7 games | 1,305 | 57.2 | 60.6 | +5.7% | 80.9% |
| 8+ games | 1,307 | 56.0 | 57.7 | +3.0% | 80.3% |

| QB career games (as main QB) | Rows | MAE model | MAE baseline | Better than baseline | Range coverage |
|---|---|---|---|---|---|
| <3 career games | 222 | 58.9 | 65.5 | +10.1% | 78.8% |
| 3-16 | 670 | 54.2 | 58.4 | +7.1% | 82.2% |
| 17+ | 2,841 | 56.9 | 59.3 | +4.1% | 80.3% |

- **The model adds the most where the baseline knows least.** Before a QB's first game of the season the baseline is last year's average: the model is +6.0% better there,
  and +10.1% better for QBs with fewer than 3 career games (n = 222), presumably because the team
  context (market-implied points, opponent, usage) stands in for missing history (not tested separately). The rows that fall back to the role average (80 of them) improve +8.4%.
- **Veterans late in the year are the hardest:** +3.0% with 8+ games played, +4.1% for QBs with 17+ career games.
  His own average is already a good description of him; whatever edge remains presumably comes from the opponent and game context.
- Home and away games improve alike (+4.8% home, +5.2% away).
- The same pattern holds for EPA per dropback (+4.6% at 0 games this season, +3.1% at 8+).

## Calibration

- **Coverage:** 80.5% (yards) and 80.3% (EPA) of actual values fall inside P10–P90 (target 80%). By season it ranges from
  77.1% to 84.4% for yards. One standard deviation of sampling noise is about 1.7 points with 507–544 games a season, so
  the lowest and highest seasons sit about two standard deviations from 80%: slightly noisy, not systematically off. A single week of 26–32 QBs swings 69–91% (10th to 90th percentile of the weekly coverage).
- **Tails are balanced:** 9.9% of actual yards fall below P10 and 9.5% above P90 (a perfect 80% range would show 10% / 10%).
- **Confidence labels** (`low` / `medium` / `high`, set from games of history and the width of the range) are not a coverage guarantee. For EPA the `high` rows cover 76.9%, a little under the target;
  treat `confidence` as "how much history is behind this", not as a calibrated probability.
- The conformal shift fell between 2019 and 2025: from 18.2 to 9.2 yards and from 0.052 to 0.040 EPA, consistent with the raw quantile ranges getting better as QB history accumulated (not tested separately).

## What drives the projections

How this was measured: for every played 2025 QB row (544 rows) I counted how often each feature appears among the **top 3 SHAP drivers** of the projection (LightGBM's own `pred_contrib` on the P50 model).
It shows what the model leaned on in 2025; it is not an average of |SHAP| over all rows (that bar chart, `shap_summary`, is in each W&B run).

**Passing yards**

| Feature (plain wording) | Internal name | In the top 3 of 2025 projections | Was the #1 driver |
|---|---|---|---|
| his recent form (recent games weighted most) | `own_ewm` | 58% | 93 |
| the points the betting market expects his team to score | `team_mkt_points` | 57% | 200 |
| the market's expected total points | `team_mkt_total` | 41% | 82 |
| his average last season | `own_ls` | 39% | 21 |
| how often the opponent blitzes (charting data) | `opp_def_blitz_rate_ftn_l8` | 22% | 46 |
| how often the opponent's defense pressures the quarterback | `opp_def_pressure_rate_l8` | 21% | 32 |
| how long he holds the ball (Next Gen Stats) | `eff_time_to_throw_l4` | 12% | 13 |
| his pass attempts per game lately | `use_attempts_l4` | 11% | 17 |
| carries the opponent allows to running backs, beyond normal | `opp_carries_allowed_rb_oe_l8` | 6% | 10 |

Shares of all top-3 slots by family: team context incl. market lines (`team_*`) 35%, his own recent history (`own_*`) 33%, the opponent (`opp_*`) 21%, efficiency (`eff_*`) 6%, usage (`use_*`) 5%.

**EPA per dropback**

| Feature (plain wording) | Internal name | In the top 3 of 2025 projections | Was the #1 driver |
|---|---|---|---|
| the points the betting market expects his team to score | `team_mkt_points` | 98% | 492 |
| his EPA per dropback lately | `eff_epa_per_db_l8` | 42% | 9 |
| his recent form (recent games weighted most) | `own_ewm` | 21% | 4 |
| the opponent's run-defense rating | `opp_def_rush_epa` | 20% | 14 |
| the points our game model projects for his team | `team_pred_points` | 11% | 0 |
| his completion rate above expected (Next Gen Stats) | `eff_cpoe_ngs_l4` | 10% | 2 |
| his yards after contact per carry | `eff_yds_after_contact_per_att_l8` | 8% | 1 |
| how often his team uses play action | `eff_play_action_rate_l8` | 8% | 1 |
| how many games of history he has | `own_n_career` | 7% | 0 |

Shares of all top-3 slots by family: team context incl. market lines (`team_*`) 42%, efficiency (`eff_*`) 29%, the opponent (`opp_*`) 17%, his own recent history (`own_*`) 12%.

- **Recent form anchors yards; team scoring environment anchors EPA.** `own_ewm` (recent form) and last season's average (`own_ls`) lead for yards; for EPA per dropback the market-implied team points is a top-3 driver in
  **98%** of 2025 rows and the #1 driver in 492 of 544. It acts as a team-level signal: offenses the market expects to score are offenses where EPA per dropback runs high.
  For yards, the market's implied team points and total are top-3 drivers in 57% and 41% of rows.
- **The opponent shows up in 47% of yards rows and 43% of EPA rows**: how often its defense pressures and blitzes
  (`opp_def_pressure_rate_l8`, `opp_def_blitz_rate_ftn_l8`), and how it rates against the pass and run. Opponent features appear in far more QB rows than in the rushing-yards (2.8%) or receiving-yards (0.2%) models: for quarterbacks the opponent does matter.

## Football sense-check

The tables show, for each fifth of a feature's values (lowest to highest), the average **projected gap** (model P50 − his baseline) and the average **real gap** (actual − his baseline) as `projected / real`,
for all 2019–2025 QB rows. Both gaps are negative on average for yards because the projection is a *median* and the baseline is a *mean* (yardage is right-skewed): read the change from the lowest to the highest
fifth, not the level. A feature the model uses well shows a projected change in the same direction, and roughly the same size, as the real one.

| Feature | What it measures | Lowest fifth | 2nd | 3rd | 4th | Highest fifth |
|---|---|---|---|---|---|---|
| `opp_pass_yds_allowed_qb_oe_l8` | passing yards the opponent has allowed to QBs beyond what those offenses usually gain | −16.6 / −20.1 | −6.4 / −8.1 | −4.2 / −2.0 | +0.3 / +0.4 | +4.0 / +9.9 |
| `opp_def_pass_epa` | opponent's pass-defense rating (EPA allowed, higher = weaker defense) | −17.5 / −17.7 | −6.5 / −4.5 | −2.0 / +0.2 | −0.9 / −4.4 | +3.9 / +6.5 |
| `opp_def_pressure_rate_l8` | how often the opponent pressures the QB | −3.6 / −6.7 | −2.3 / +4.9 | −4.1 / −3.8 | −4.4 / −4.0 | −8.5 / −10.2 |
| `opp_def_blitz_rate_ftn_l8` | how often the opponent blitzes (FTN charting, 2022 on) | −12.0 / −10.8 | −8.7 / −5.1 | −4.9 / −8.4 | −1.5 / −0.5 | +3.6 / +4.2 |
| `opp_carries_allowed_rb_oe_l8` | carries the opponent allows to running backs, beyond normal | −4.8 / 0.0 | −7.1 / −7.3 | −5.7 / −5.7 | −3.2 / −3.2 | −2.2 / −3.6 |
| `team_mkt_total` | the market's expected total points in the game | −8.1 / −10.5 | −6.4 / −5.3 | −3.1 / −1.4 | −4.1 / −2.8 | −1.8 / −0.7 |
| `use_attempts_l4` | his pass attempts per game over his last 4 | +4.1 / +3.3 | +2.2 / +3.2 | −3.0 / +0.9 | −6.2 / −7.0 | −17.4 / −17.1 |

- **Opponent pass defense: right direction and about the right size.** From the weakest to the strongest fifth of defenses the projection moves from −17.5 to +3.9 yards (a swing of 21 yards) while the real gap moves
  from −17.7 to +6.5 (24 yards); the opponent's record against QBs (`opp_pass_yds_allowed_qb_oe_l8`) gives 21 vs 30 yards. The model captures roughly 70–90% of the real spread.
- **Pressure and blitz.** The highest-pressure fifth of defenses holds QBs about 4–5 yards below the others in the projection and about 6 in reality. Blitz rate (FTN, 2022 on) moves the projection
  with the real outcome (a swing of about 15.6 projected vs 15.0 real yards): QBs facing blitz-heavy defenses end up above their baseline, not below.
- **Volume regresses to the mean, and the model knows it.** QBs who just threw the most (top fifth of `use_attempts_l4`) land about 17 yards below their baseline in the projection and 17 in reality:
  the baseline over-reacts to a high-volume stretch.
- **One doubtful driver.** `opp_carries_allowed_rb_oe_l8` ("carries the opponent allows to running backs, beyond normal") is a top-3 driver of **6.1%** of 2025
  passing-yards rows, always pushing the projection *up* (+6.4 yards on average when it appears). Across all rows the real gap does not rise with it (rank correlation −0.001;
  highest fifth: −3.6 real vs −2.2 projected). It is a proxy for "defense that offenses run against",
  not a causal run-defense effect, so do not read the driver phrase literally on those rows. A candidate to prune in the next model version.

## Known biases and limits

- **Closing-line market features.** `team_mkt_points`, `team_mkt_spread` and `team_mkt_total` are the *closing* lines in every backtest (late injury news already priced in); a live Tuesday run sees earlier lines.
  They matter somewhat: the market features add 0.4 points on yards and the market features add 0.9 points on EPA per dropback (+4.6% and +3.9% remain). Report the no-market numbers as the cautious ones.
- **The Friday view (D66).** `avail_*` and `rip_out_*` read the week's own injury report. Backtests use the final report; a live run uses whatever the snapshot has, and a Tuesday run sees nobody listed.
  For quarterbacks the effect is small (only 88 of 3733 QB games are listed Questionable or Doubtful, and those players still played), but the weekly run
  must happen late in the week to be like the backtest.
- **Expected QB.** The projection follows the *expected* starter; when he is a late scratch the replacement has no projection and the expected QB's is logged "not played". Backups are never modeled (P08 may).
- **Short histories.** Rookies and QBs new to the starting role get lower confidence (2.1% of rows use the role baseline); their gain over baseline is large partly because that baseline is a position average.
- **Early season.** Week 1–4 ratings and efficiency lean on last season; the model's gain is similar early and late (+6.0% in weeks 1–4,
  +4.8% from week 10), and so is the coverage (81.4% vs 80.0%).
- **Charting and tracking data have short histories.** FTN charting (blitz faced, play action, opponent blitz rate) starts in 2022 and PFR (pressure rates) in 2018, each a week late (D44); earlier seasons have those features empty,
  so the model learned them from fewer seasons than the box-score features.
- **Not modeled here:** touchdown and interception chances and QB rushing yards (P08); game script is only seen through the market and our game model.
- **Sampling noise.** About 507–544 games a season: a one-point swing in a single season is noise, so only the pooled and seven-season picture is claimed.
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
| passing yards | [5t3sgsfb](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/5t3sgsfb) | [rcyuv6pl](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/rcyuv6pl) | [b5ih5plm](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/b5ih5plm) |
| EPA per dropback | [hmeyyis1](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/hmeyyis1) | [cygm4hbw](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/cygm4hbw) | [8bcl12h4](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/8bcl12h4) |

The sweeps (`tune-<target>-<group>`, job type `tune`) hold the 12 grid points. Each point is one tiny run logging `tune/mae_model`, `tune/mae_baseline`, `tune/improvement_pct` and `tune/n`; on the sweep page the
parallel-coordinates chart shows `num_leaves`, `min_data_in_leaf` and `n_estimators` against `tune/mae_model`. A flat picture is the expected one (the grid is flat, D68).

### Backtest run (`backtest-pass_yds-qb`, `backtest-pass_epa-qb`; job type `backtest`)

The x-axis of every `bt/*` curve is **`bt/step`**: one step per reported week. Step 1 is 2019 week 1 and the last step (124) is 2025 week 18 (17 weeks in 2019 and 2020, 18 after). `bt/season` and `bt/week` translate a step.

| Chart | What it shows | How to read it | Real numbers (yards / EPA) |
|---|---|---|---|
| `bt/cum_improvement_pct` | Better-than-baseline over **every week so far** | **The main chart.** It settles as games accumulate; the last value is the pooled result. Good: above 0 and flat | Ends at +5.0% / +4.7%. Season-end values: 4.7, 3.2, 4.3, 4.9, 4.7, 4.8, 5.0 / 6.6, 5.3, 5.4, 5.0, 4.8, 4.5, 4.7 (2019 → 2025) |
| `bt/improvement_pct` | Better-than-baseline for **that week only** (26–32 QBs) | Very jumpy: a handful of QBs. Read the share of weeks above 0, not single points | Yards: −11% to +20%, median +5.4%, above 0 in 73% of weeks. EPA: median +4.7%, above 0 in 76% |
| `bt/cum_coverage_80` | Share inside P10–P90 over every week so far | Should hover near 0.80; early steps swing | Ends at 0.805 / 0.803 |
| `bt/coverage_80` | Coverage for that week | With ~30 QBs a week, 0.69–0.91 is normal (10th to 90th percentile of weeks) | Median 0.81 / 0.80 |
| `bt/mae_model`, `bt/mae_baseline`, `bt/cum_mae_model`, `bt/cum_mae_baseline` | The raw MAEs behind the two improvement curves, in the target's unit | The model line should sit under the baseline line in the cumulative chart | Yards: 56.6 vs 59.5. EPA: 0.230 vs 0.241 |
| `bt/range_param` | The conformal shift used that week | Positive = the range was widened. Should be stable, not drifting | Yards 18.2 (2019) → 9.2 (2025); EPA 0.052 → 0.040 |
| `bt/n`, `bt/step`, `bt/season`, `bt/week` | Rows scored that week; lookups | `bt/n` is a sanity check: about 26–32 QBs | |
| `lgb/curve_<season>` | The season's opening fit (train on everything before the previous season, hold the previous season out): the model's loss after each of the 150 boosting rounds, on the training rows and on the held-out season. The 2019 curve is logged first, the later seasons at the end | The loss of the P50 (quantile) model. Training should fall steadily; the held-out line should flatten, not climb. A growing gap between the lines is overfitting. The models use a fixed 150 rounds (no early stopping), so a held-out line that turns up late would mean too many rounds | Compare the lines' shape, not absolute values |

**End-of-run panels** (logged once, after the last week):

| Panel | What it shows | How to read it |
|---|---|---|
| `by_season` | One row per season: rows, MAEs, improvement, coverage, rank skill | The source of the by-season tables above |
| `mae_by_season` | Line chart: model MAE vs rolling-baseline MAE by season | The model line under the baseline line in all 7 seasons |
| `accuracy_scoreboard` | The weekly scoreboard rows (`season, week, target, position_group, n_scored, mae_model, mae_baseline, improvement_pct, coverage_80`, ...) for the backtest | The same table the live scoreboard fills each week |
| `feature_importance` | Bar chart: top 25 features by LightGBM **gain** in the last (2025 week 18) fit | Gain is what the trees actually split on |
| `shap_summary` | Bar chart: top 20 features by mean absolute SHAP over the 2025 rows, in target units (yards / EPA) | Compare with the driver table above: recent form, team context and the opponent should lead |
| `predictions` | Every 2025 projection with P10 / P50 / P90, baseline, actual and confidence | For digging into a specific game |

**Run summary (Overview tab):** `n_scored`, `mae_model`, `mae_baseline` (and `mae_baseline_mean`, equal to it for amounts), `coverage_80`, `improvement_pct`,
`mae_season_mean_same_rows` / `mae_model_season_mean_rows` / `improvement_vs_season_mean_pct` (the same comparison against the season-to-date mean), `spearman_outperformance`, `share_role_baseline`
(share of rows on the role baseline), `seasons_beating_baseline` out of `seasons`. The config panel lists the 126 features, `feature_hash`, `dataset_version` (pbp snapshot 2026-10-04) and `git_commit`.

## Versioning

- **Hyperparameters are fixed for the 2026 season.** Retune before 2027 (P10). Never retune mid-season (doc 04: investigate, don't retune, when a target loses to its baseline for 3+ weeks in a row).
- **Weekly fits:** every `nfl weekly run` refits all 11 target models for each week of the season up to the current one, continuing the backtest's walk-forward history, and logs `player-model:<season>-w<NN>`;
  the weekly step scores last week's pre-kickoff projections onto the live scoreboard.
- **Promoting a new configuration** (for example a version that drops `opp_carries_allowed_rb_oe_l8`, or one without market features) only if it beats this one walk-forward in each season; that is a ✋ checkpoint.
