# Model card: game model v0 (P03)

**Family:** Track 1 model B ([04 → B](../04-track1-models.md#b-game-model-win-probability)) · **Chosen:** 2026-10-03 (D48; P03's 🧑/✋ steps waived by Rishi for this phase) · **Production:** `game-model:2026-w04` (W&B version v1, alias `production`) · **Code:** `features/game.py`, `features/qb.py`, `features/venues.py`, `models/game_model.py`, `models/backtest.py`, `models/metrics.py`, `models/game_runs.py` · **Tables:** `features/game_features` (`nfl features game`), `runs/<season>/week<NN>/predictions_games.parquet` (`nfl train game`), `runs/backtests/game/<variant>/` (`nfl backtest game`).

## What it is

For every game: a **home win probability**, an **expected margin** and a **predicted score**, in two versions:

| Version | Uses | Purpose |
|---|---|---|
| **model-only** | Football features only: team rating, Elo, home field, QB status | Shows what our own analysis knows; the "model vs consensus" comparison in the digest |
| **market** | The same + the current spread and total | The most accurate numbers; **the one the digest shows**. Falls back to model-only (flagged) when a game has no complete line |

Both are linear models refit every week on all earlier games (2011 on), with current-season games weighted 3× and last season's 1.5× (D28).

## How it works, in plain language

**Three small regressions, refit from scratch every Tuesday.**

1. **Margin.** "How many points will the home team win by?" is a weighted sum of four numbers (below). The weights are learned from every game since 2011.
2. **Total.** "How many points will both teams score?" is another weighted sum, of scoring-related numbers.
3. **Score.** The two are split so they always agree: home = (total + margin) / 2, away = (total − margin) / 2. A 3-point margin and a 45-point total give 24–21.
4. **Win probability.** NFL margins scatter around the prediction with a spread of about 13 points (a bell curve). The chance the home team wins is the share of that curve above zero. Expected margin +3 → about 59%; +7 → about 70%; +14 → about 86%. The 13 points is measured on the model's own earlier walk-forward misses, never on the games it was fitted to.

**The four margin inputs (model-only), all "home minus away":**

| Input | Meaning | Learned weight (2026 week 4 fit) |
|---|---|---|
| `net_diff` | Net EPA/play rating difference (P02 ratings, as of this week) | 9.8 points per 1.0 EPA/play (net differences are usually within ±0.3) |
| `elo_diff` | Elo difference, per 100 Elo points | 3.2 points per 100 Elo |
| `hfa` | 1 at the home team's stadium, 0 at neutral sites | 2.1 points of home field |
| `qb_adj_diff` | QB status: how much better or worse each team's expected starter is than the QBs its ratings were built on (EPA/dropback) | 24.2 points per 1.0 EPA/dropback (a backup at −0.15 costs ~3.6 points) |

Because every input is a difference (and there's no intercept), two equal teams at a neutral site get a margin of exactly 0 and a 50% probability. The rating and Elo overlap (both measure team strength), so their weights share the work: Elo's margin of victory carries information the EPA ratings don't, and vice versa.

**QB status, the one input Elo can't see** (`features/qb.py`, D49):
- **QB value:** a QB's EPA per dropback over his whole career so far, with recent games counting more (24-week half-life) and pulled toward a replacement level of −0.05 by 250 dropbacks. The −0.05 was measured on 2011–2017 only, before the reporting window. A rookie with no history gets −0.05.
- **Expected starter on Tuesday:** whoever took most of the team's dropbacks in its last game. Exceptions: week 1 uses the week-1 depth chart if it was published by Tuesday (else last season's main starter), and a team's first playoff game uses its most frequent starter of the last 3 games (catches rested week-18 starters).
- **Baseline:** the recency-weighted value of the QBs who actually played the team's recent games (12-week half-life, like the ratings).
- **qb_adj = expected starter − baseline.** Same QB all season → 0. Backup in → negative. Star back from injury → positive.
- **Live runs know more:** for the week being predicted, the order is the schedule's projected starter → the latest depth chart → the Tuesday starter. Whoever is picked, if he is listed Out or Doubtful, the next healthy QB on the chart replaces him.

**What was chosen (D48), and how.** Every option was tried walk-forward on **2013–2017** (the tuning window). The **2018–2025** results below were only used for reporting:
- **Margin → probability** beat a direct logistic regression.
- **QB status** was the one addition that clearly helped in both windows.
- **The pass/rush matchup features** (pass offense vs pass defense, rush vs rush) added nothing beyond the net rating, so they are left out (simplest model that's as good).
- **Also left out:** rest days, byes, short weeks, divisional games, travel, time zones, success-rate ratings, and a Platt or isotonic calibration layer. None helped on 2013–2017 (calibration made Brier worse). They stay in the feature table for P08's LightGBM.
- **Current-season weights** 1×/2×/3×/5× are within 0.0001 Brier of each other, so D28's 3× stays.

**How it's applied each week (`nfl train game --season S --week N`):**
1. Build the feature table as of week N (ratings, Elo and QB status from earlier weeks; the live QB resolver for week N's unplayed games; current lines).
2. Re-run the walk-forward for the last 5 seasons through week N. This is what gives sigma (and any calibration) from earlier weeks' misses, and the last step is the week-N fit itself.
3. Predict week N in both variants. Write `predictions_games.parquet`, save both fitted models under `models/game-model/<S>-w<NN>/` and log them as the W&B artifact `game-model:<S>-w<NN>`.

## Results: walk-forward 2018–2025 (2,227 games, regular season + playoffs)

Every week of every season was predicted by a model trained only on earlier weeks. Model and baselines are scored on exactly the same games (a game missing any baseline would be left out of every metric; none were). Ties count 0.5 in Brier and log loss and are left out of accuracy.

**Win probability (lower Brier / log loss / ECE is better):**

| Predictor | Brier | Log loss | Accuracy | ECE |
|---|---|---|---|---|
| **Model-only** | **0.2199** | 0.6321 | 64.0% | 0.032 |
| Elo (as of Tuesday) | 0.2221 | 0.6372 | 63.5% | 0.032 |
| Home team always (walk-forward home win rate) | 0.2477 | 0.6904 | 54.3% | 0.021 |
| **Market-informed** | **0.2102** | 0.6098 | 66.3% | 0.023 |
| Market (closing spread → probability) | 0.2104 | 0.6102 | 66.2% | 0.029 |
| Market (closing moneyline, vig removed; reference) | 0.2101 | | | |

- **Ship bar met:** model-only beats Elo by 0.0023 Brier pooled.
  - It wins in every part of the season (weeks 1–4, 5–9, 10+ and playoffs).
  - It wins in 5 of 8 seasons: Elo is slightly better in 2019 (+0.0004), 2022 (+0.0008) and 2024 (+0.0007), all well inside the noise.
- **Stretch goal matched:** market-informed is level with the closing market (0.2102 vs 0.2104; the gap is noise), with better calibration.
- **ESPN FPI** isn't a baseline: only the current snapshot is collected, so there's no history to backtest against (D50).

**By season (Brier):**

| Season | Games | Model-only | Elo | Market-informed | Market |
|---|---|---|---|---|---|
| 2018 | 267 | 0.2189 | 0.2222 | 0.2118 | 0.2108 |
| 2019 | 267 | 0.2221 | 0.2217 | 0.2133 | 0.2128 |
| 2020 | 269 | 0.2102 | 0.2144 | 0.2019 | 0.2026 |
| 2021 | 285 | 0.2238 | 0.2310 | 0.2166 | 0.2171 |
| 2022 | 284 | 0.2223 | 0.2215 | 0.2081 | 0.2084 |
| 2023 | 285 | 0.2301 | 0.2326 | 0.2167 | 0.2171 |
| 2024 | 285 | 0.2114 | 0.2107 | 0.2020 | 0.2033 |
| 2025 | 285 | 0.2195 | 0.2223 | 0.2114 | 0.2108 |

**By part of the season (model-only / Elo / market-informed / market):**

| Weeks | Model-only | Elo | Market-informed | Market |
|---|---|---|---|---|
| 1–4 | 0.2262 | 0.2274 | 0.2190 | 0.2185 |
| 5–9 | 0.2175 | 0.2203 | 0.2107 | 0.2127 |
| 10+ | 0.2183 | 0.2209 | 0.2052 | 0.2048 |
| Playoffs | 0.2168 | 0.2190 | 0.2153 | 0.2146 |

**Calibration.**
- **Pooled ECE is 0.0315**, just above the 0.03 target, **and explained:**
  - Noise sets the floor. A *perfectly* calibrated model with these same probabilities would show ECE 0.021 on average, 0.029 at the 90th percentile and 0.031 at the 95th (2,000 simulations). Elo shows 0.032 and the closing market 0.029.
  - The one systematic part is **home field shrinking since 2020**. In non-neutral games the model predicted a 57.4% home win rate for 2018–2019 against 57.3% actual, but 55.9% for 2020–2025 against 53.9% actual. The single learned home-field weight (fit on 2011 on, current season weighted 3×) lags the decline. That shows up as the 0.30–0.60 bins running 2–6 points high for the home team (0.357 predicted → 0.294 won; 0.552 → 0.510).
  - A Platt or isotonic layer didn't fix it on the tuning window (Brier got worse).
  - **Follow-up for P08/P10:** a trailing home-field feature.
- **Market-informed** ECE is 0.023: the spread already carries the current home edge.
- Single seasons show ECE 0.03–0.09, which is mostly noise at ~270 games per season (the market shows the same range).

**Margin and scores:**

| Quantity | Model-only | Market-informed | Baselines |
|---|---|---|---|
| Margin MAE (points) | 10.19 | 9.86 | Elo margin 10.22, closing spread 9.83 |
| Margin RMSE = walk-forward sigma | 13.1 | 12.8 | |
| Total points MAE | 10.63 | 10.44 | closing total 10.42, rolling team average 10.86 |
| Points MAE per team | 7.46 | 7.28 | rolling team average 7.65, market-implied team total 7.27 |
| Score/margin consistency | exact (max gap 0.0) | exact | |

**What better QB information would add (research, `--qb-mode actual`).** If the model knew each game's **listed starter** (post-Tuesday information, what a game-day injury report reveals), model-only Brier would drop to **0.2183**, another −0.0016. The Tuesday guess misses the listed starter in 8.1% of team-games. That is the case for the Saturday injury update (P07). An oracle that also knew in-game replacements (the QB with most dropbacks) would be an upper bound, not a realistic target.

**W&B (group `track1-game`):** model-only backtest [5x33rk56](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/5x33rk56) · market backtest [ekz4277b](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/ekz4277b) · weight sweep [aoflnafa](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/aoflnafa) · listed-starter oracle [3ots68nz](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/3ots68nz) · live 2026 week 4 fit [ump6sftd](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/ump6sftd). The first round of runs (r9gmo5rd, rkv43aot, goh98ltt, l5dmbew2, yv2rntfq) used a configuration with a sign bug the code review found (see below) and is superseded.

## Is 64% accuracy good? (Why NFL winners top out around 65–70%)

Short answer: **yes. 64–66% is close to the ceiling for picking NFL winners, and no honest model reaches 82%.** The 80–90% numbers common in machine learning come from tasks where the inputs nearly determine the answer (spam filters, image recognition). An NFL game is decided by one noisy afternoon: fumbles, tipped passes, injuries during the game, officiating, a missed field goal.

**The evidence, from our own data (2018–2025, 2,227 games):**

| Predictor | Accuracy | Notes |
|---|---|---|
| Home team always | 54.3% | The floor |
| Elo | 63.5% | |
| **Our model-only** | **64.0%** | Football data only |
| **Our market-informed** | **66.3%** | |
| Closing betting market | 66.2% | The best public predictor. It knows every injury and moves with millions of dollars of informed money. Its best season was 70.5% (2024), its worst 62.3% (2021) |

**Why the ceiling is so low:**
- **Many games are near coin flips.** 37% of games had a closing spread of 3 points or less: even the market thought they were close to 50/50. Nobody can call those at 82%.
- **The randomness is large.** Final margins scatter about 13 points around the best available prediction (that is the sigma in the math below). If the market's probabilities are honest, its expected accuracy is just **65%**, and that's what it gets.
- **Even a perfect model can't escape it.** Suppose the randomness were cut from 13 to 8 points, far beyond anything achievable. Accuracy would still only reach about **73%**.
- **A full-season accuracy above ~72% almost always means leakage** (the model saw something from after kickoff).

**Where high hit rates do show up: confident picks.** Accuracy depends on how sure the model is. The share of games is how many fall in each band:

| Model's confidence | Model-only: share of games | Model-only: right | Market-informed: share | Market-informed: right |
|---|---|---|---|---|
| Toss-up (< 55%) | 21% | 51% | 10% | 50% |
| Lean (55–65%) | 38% | 60% | 45% | 59% |
| Solid (65–80%) | 34% | 73% | 36% | 75% |
| Strong (≥ 80%) | 7% | **81%** | 10% | **87%** |

That's what a *calibrated* model looks like: it says 60% and is right about 60% of the time. It says 85% and is right about 85% of the time. If a "toss-up" pick were right 80% of the time, the model would be under-confident. That would be a bug too.

**This is why accuracy isn't the main metric.** Brier score and log loss reward honest probabilities. A model that says 55% on a toss-up and loses hasn't made a mistake. One that says 95% and loses has made a big one.

**Will it improve as we iterate?** A little, in accuracy terms. Expected gains, roughly:
- **Saturday QB / injury update (P07):** about +0.0016 Brier, worth roughly +0.5–1 point of accuracy.
- **LightGBM with injuries, weather and the unused features, plus a trailing home-field term (P08):** maybe another point.
- **Realistic target for model-only:** 65–66%. Market-informed should stay at or near the market (~66%) and be better calibrated.

The real gains we can still make are in **probability quality** (Brier, calibration), margins and scores, and in the parts the market doesn't price for us: player projections (P06), trends and the graph insights.

## Reading the W&B charts

All game-model runs are in W&B group **`track1-game`**. Open a run and the **Charts** tab shows the curves below. The **Overview** tab's summary holds the pooled numbers, and the **Tables** / media panels hold the end-of-run tables and plots.

### The metrics in one minute

| Metric | What it measures | Better | Our 2018–2025 values |
|---|---|---|---|
| **Brier score** | Mean of (predicted probability − outcome)², with the outcome 1 for a home win, 0 for a loss, 0.5 for a tie. Saying 70% and winning scores (0.3)² = 0.09; saying 70% and losing scores (0.7)² = 0.49 | Lower. 0.25 = always saying 50% | Model-only 0.2199, Elo 0.2221, market 0.2104 |
| **Log loss** | Mean of −log(probability given to what actually happened). It punishes confident wrong calls much harder than Brier (95% and losing costs 3.0; 60% and losing costs 0.92) | Lower. 0.693 = always 50% | 0.632, Elo 0.637, market 0.610 |
| **Accuracy** | Share of games where the side given > 50% won (ties excluded) | Higher | 64.0%, Elo 63.5%, market 66.2% |
| **ECE** (expected calibration error) | Group games by predicted probability (10 bins), compare each bin's average prediction with how often the home team actually won, and average the gaps (weighted by games) | Lower. About 0.02–0.03 is the noise floor at ~2,000 games | 0.032, Elo 0.032, market 0.029 |
| **MAE** (mean absolute error) | Average miss in points (margin, total, or each team's points) | Lower | Margin 10.2 points, total 10.6, per team 7.5 |
| **Sigma** | Spread of margin misses (their RMSE), used to turn a margin into a win probability | Stable | ≈ 13.1–13.4 |

**"Model", "elo", "market", "home"** in a chart name are the four predictors, always scored on the same games: our model, Elo as of Tuesday, the closing spread turned into a probability, and "home team always" (the historical home win rate).

### Backtest runs (`backtest-model_only`, `backtest-market`; job type `backtest`)

The x-axis of every `bt/*` curve is **`bt/step`**: the count of reported weeks predicted so far. Step 1 is 2018 week 1, and the last step (about 170) is the 2025 Super Bowl. Use the `bt/season` and `bt/week` charts to translate a step into a week.

| Chart | What it shows | How to read it |
|---|---|---|
| `bt/brier_model`, `bt/brier_elo`, `bt/brier_market`, `bt/brier_home` | Brier score of **that week's games only** (about 13–16 games) | Very jumpy: one upset-heavy week spikes everyone. Compare the lines week by week (the model line should usually sit near or below Elo), but don't read anything into one week |
| `bt/cum_brier_model` (and `_elo`, `_market`, `_home`) | Brier score of **every game so far**, from 2018 week 1 to this step | **The main chart.** It smooths out as games accumulate. The final value is the pooled result. Good: the model line ends below Elo (0.2199 < 0.2221) and above the market (0.2104). Home-always sits far above at ~0.248 |
| `bt/cum_log_loss_model` | Cumulative log loss of the model | Should settle around 0.63. A sudden jump means confident wrong calls (a 90% favorite losing) |
| `bt/cum_accuracy_model` | Cumulative share of winners picked | Settles around 64%; early steps swing because there are few games. See "Is 64% accuracy good?" |
| `bt/cum_margin_mae_model` vs `bt/cum_margin_mae_market` | Cumulative average miss on the final margin, model vs the closing spread | Model ≈ 10.2 points, market ≈ 9.8. The gap is how much the market knows that we don't |
| `bt/cum_total_mae_model` | Cumulative average miss on total points | ≈ 10.6 (closing total ≈ 10.4) |
| `bt/sigma` | The sigma used that week (RMSE of all earlier walk-forward margin misses) | Should be flat around 13. A drift up means margins are getting harder to predict |
| `bt/games` | Games scored that week | Sanity check: about 13–16 in the regular season, fewer in the playoffs |
| `bt/season`, `bt/week` | Which season and week each step is | Lookup only |

**End-of-run panels** (logged once, after the last week):

| Panel | What it shows | How to read it |
|---|---|---|
| `reliability_diagram` | The model's predicted probability (x) vs how often the home team actually won (y), in 10 bins. The second line is the perfect-calibration diagonal | Points on the diagonal = honest probabilities. Points below it = over-confident for the home team (ours sit 2–6 points low in the 0.3–0.6 range: shrinking home field since 2020). The end bins have few games, so they wobble |
| `reliability_table` | The numbers behind the diagram for all four predictors (`n`, `mean_prob`, `mean_outcome` per bin) | Compare a bin's `mean_prob` with its `mean_outcome` |
| `brier_by_season` | Brier per season for each predictor | Look for one bad season dragging the average. The model beats Elo in 5 of 8 seasons |
| `by_season` | Table: per season, games and Brier / log loss / accuracy / ECE for every predictor, plus margin and total MAE | The source for the by-season table above |
| `by_week_bucket` | Table: the same metrics for weeks 1–4, 5–9, 10+ and playoffs | Weeks 1–4 are hardest for everyone (preseason uncertainty). The model should still beat Elo there |
| `predictions` | Every game: probabilities, expected margin, spread, actual margin, predicted and actual scores | For digging into specific games |

**Run summary (Overview tab):**
- **Pooled metrics:** `brier_<p>`, `log_loss_<p>`, `accuracy_<p>`, `ece_<p>` for p = model, elo, market, home.
- **Gains:** `brier_gain_vs_elo` / `_market` / `_home` (the baseline's Brier minus ours: positive = we're better). `beats_elo` is 1 when the model wins.
- **Season counts:** `seasons_beating_elo` out of `seasons`.
- **Margin and points:** `mae_margin_model` / `_elo` / `_market`, `mae_total_model` / `_market` / `_rolling`, `mae_points_model` / `_rolling` / `_market` (per-team points), `margin_rmse_model`.
- **Checks:** `score_margin_max_gap` should be 0, meaning the predicted scores always match the margin.
- **Cohort:** `games` and `games_dropped` (games left out because a baseline was missing; 0 in backtests). `reg_brier_<p>`, `reg_brier_gain_vs_<p>` and `reg_games` are the Brier scores for regular-season games only. `market_ml_brier` is the vig-free moneyline (reference).

### Weight sweep (`nfl backtest game-weights`; job type `tune`)

- **One run per current-season weight** (1×, 2×, 3×, 5×). Each has the same `bt/*` curves as a backtest.
- **On the sweep page**, the parallel-coordinates and scatter charts plot `current_season_weight` against `brier_model`. A flat line means the weight doesn't matter, which is what we saw: 0.21992 / 0.21984 / 0.21985 / 0.21989.
- **The summary adds `brier_model_w01-04`, `brier_model_w05-09`, `brier_model_w10+` and `brier_model_post`.** Use them to check whether heavier current-season weight helps early weeks and hurts late ones. It did, very slightly, in both directions (−0.0008 and +0.0004), which is noise.

### Weekly fit (`train-<season>-w<NN>`; job type `train`)

- **`predictions_games` table:** the week's predictions, both variants (the same rows as the parquet file).
- **Summary:**
  - `games` (games predicted);
  - `market_fallback_games` (games without a complete line, so model-only is shown);
  - `sigma_model_only` / `sigma_market` (the sigma used this week).
- **Artifacts tab:** the `game-model` artifact, with version aliases `<season>-w<NN>` and, when promoted, `production`. Its description is this model card.

## Code review

Sol (Codex `gpt-6.1-sol`) reviewed the code before commit. **No leakage was found.** Every finding was fixed:
- **Defense signs in the pass/rush matchup features.** They were reversed: defense ratings are EPA *allowed*, so a weak defense must add to the opponent's offense.
  - After the fix, the features were re-selected on the tuning window, and the matchups dropped out.
  - The earlier (buggy) configuration had looked marginally better on 2018–2025 (0.2195). That was noise around a mis-signed feature, and the honest number is 0.2199.
- **Canonical backtest folder.** A research run had overwritten it before `backtest_label()` existed; it was regenerated.
- **Incomplete market rows.** A market row with a spread but no total could become the digest row. It now falls back to model-only.
- **Injured Tuesday starter.** The live resolver now replaces him when neither the schedule nor the chart names someone.
- **Common cohort.** Baselines are scored on exactly the model's games.
- **QB prior.** Re-measured on pre-2018 seasons only (still −0.05).
- **Oracle.** It now uses the listed starter.
- **Tests.** A mocked test now covers the weekly train path.

## The math

- **Margin head:** ridge regression, no intercept, features scaled (not centered) so `hfa` keeps its meaning, `alpha = 1` (`settings.yaml` → `game_model`). Fitted with sample weights `w = 3` (current season), `1.5` (last season), `1` (older).
- **Win probability:** `p = Φ(m / σ)`, where `m` is the expected margin and `σ` is the RMSE of all earlier walk-forward margin residuals (13.5 until 400 exist). Fitted walk-forward σ ran 13.2–13.4 in 2018–2025.
- **Total head:** ridge with intercept on:
  - `off_sum`: home + away offense EPA rating;
  - `def_sum`: both defenses' EPA allowed;
  - `qb_adj_sum`;
  - `league_ppg`: league points per team-game over the last 16 weeks with games;
  - `pts_base_total`: each team's last-8-games points for/against, combined;
  - `roof_dome`: 1 dome, 0.5 retractable, 0 open air.

  Market variant: + `total_line`.
- **Market variant margin:** + `spread_line`; the spread does almost all the work and the football features add small corrections.
- **Matchup features (in the table, not used in v0):** `pass_matchup = (home_off_pass + away_def_pass) − (away_off_pass + home_def_pass)`, the expected home pass EPA minus the expected away pass EPA. Same for rush.
- **Baselines** (`fit_game_model`, same rows):
  - home team: the training rows' home win rate (0.5 at neutral sites);
  - Elo: `1 / (1 + 10^(−(elo_h − elo_a + 48·hfa)/400))` from `team_elo` as of the game's week;
  - market: `Φ(spread / σ_m)`, σ_m = RMSE of the margin around the spread in the training rows.

## Outputs: `predictions_games.parquet` (handoff to P04)

One row per game per variant. `is_primary` marks the row the digest uses: the market row when the market prediction is complete (it needs a spread **and** a total), otherwise the model-only row with `market_fallback = true`.

`season, week, game_id, game_type, kickoff_utc, home_team, away_team, neutral_site, variant, is_primary, market_fallback, home_win_prob, away_win_prob, expected_margin, pred_home_points, pred_away_points, pred_total, sigma, confidence (max of the two probabilities), confidence_label (toss-up < 0.55 ≤ lean < 0.65 ≤ solid < 0.80 ≤ strong), elo_prob, market_prob, spread_line, total_line, market_source, home_qb_name, away_qb_name, home_qb_source, away_qb_source, model_version, feature_hash, trained_through, created_at`

The digest never prints the line or market numbers (D03). `market_prob` / `spread_line` are there for "where the model disagrees with consensus", described in words.

## Known biases and limits

- **Home field since 2020** is over-estimated by about 2 points of win probability (see Calibration). Follow-up: a trailing home-field feature (P08/P10).
- **Closing-line optimism.** Historical market features are closing lines, which already know late injury news. Live Tuesday runs use earlier lines, so the market variant's live accuracy will be somewhat worse than 0.2102. Model-only uses no market data and has no such bias.
- **Tuesday QB information.** Backtests use only what a Tuesday run knows. Live runs use the schedule's projected starter, which is better, so live model-only numbers should be a little better than backtested (toward the listed-starter oracle's 0.2183).
- **Weather and injuries beyond the QB** aren't in v0 (P08).
- **Small, noisy data.** About 280 games per season; differences under ~0.002 Brier are noise. Elo was slightly better in 3 seasons.
- **Training starts in 2011** because 2010's ratings have no preseason prior.

## Versioning

- **Hyperparameters are fixed for the 2026 season** (D17). Retune before 2027 (P10).
- **Weekly fits:** W&B artifact `game-model:<season>-w<NN>`. The `production` alias was set on `2026-w04` (v1, after the review fixes) at P03 close (D51). From P07, the weekly pipeline moves it each week.
- **Promote a new configuration** (LightGBM in P08) only if it beats this one walk-forward; that is a ✋ checkpoint.
