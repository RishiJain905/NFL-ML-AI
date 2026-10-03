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
