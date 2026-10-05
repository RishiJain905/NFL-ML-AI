# 04: Track 1 Models

Track 1 has three model families, each with a **defined target**, a **baseline it must beat** and a **walk-forward evaluation**:

| Model | Output | Target / definition | Must beat |
|---|---|---|---|
| A. Team ratings + trend | Offense and defense strength per team per week; trend direction | Calculation (opponent-adjusted EPA), not a learned label | Validated by how well it predicts the next weeks |
| B. Game model | Win probability and expected margin per game | Home point margin, and home win (0/1) | Elo; market-implied probability (stretch goal) |
| C. Player model | Projected output with an 80% interval; "players to watch" | Per-position stat (see below) | Each player's rolling average |

The models stack: A feeds B, and A and B feed C.

---

## Leakage rules (apply to all models)

1. **As-of rule:** a feature for week N uses only data through week N−1, and only data that would have been available on Tuesday of week N (see the availability rule in [03](03-data-sources.md)).
2. **Walk-forward evaluation only:** to evaluate week *w* of season *s*, train on everything strictly before it. No random splits, ever.
3. **No post-season data in live features:** `load_participation` and any other field filled in after the season are for research only.
4. **Injury status as of the run time:** use the injury report snapshot the live run would have seen, not the final game-day inactives (unless running the Saturday update).
5. **Closing-line caveat:** historical market features are closing lines. Report backtests with and without them, and note the optimistic bias.
6. **Tested:** every feature family has a unit test that builds features for a past week and asserts no input row has a game date on or after that week's games.

---

## A. Team ratings and trend

### Key terms

Short definitions. The formulas, intuition and worked examples (EPA, the ratings, Elo, trend) are in the [model card → The math, step by step](model_cards/team_ratings.md#the-math-step-by-step).

- **Expected points (EP):** for a game situation (down, distance, field position, time, score), the average value of the next score from the offense's point of view. For example, 1st-and-10 at your own 25 is worth 1.21, and 1st-and-goal at the 2 is worth 6.33 (2021–2025 averages).
- **EPA (Expected Points Added):** EP after a play minus EP before it, i.e. how much the play helped. It's the `epa` column in nflverse play-by-play.
- **Success rate:** the share of plays with EPA > 0.
- **Offense / defense / net rating:** opponent-adjusted EPA per play gained, EPA per play **allowed** (lower is better), and `net = offense − defense`. All are relative to the league average (0 = average).

### Ratings

Separate offense and defense ratings for each team, split by pass and rush, measured in **EPA per play**, adjusted for opponent strength. *(This section is the original plan. What was built and tuned, including the final 12-week half-life and 0.1 prior pull, is in "As built in P02" below and in the [model card](model_cards/team_ratings.md).)*

- **Method:** ridge regression on play-level EPA with an offense-team indicator, a defense-team indicator and home field, fit on a recency-weighted window. Each play's weight = exponential decay by weeks ago (half-life tuned, starting point ~4 weeks; tuned to 12, D46). Ridge shrinkage stabilizes small samples.
- **Play filters:** drop kneels, spikes and no-plays. Down-weight garbage time (win probability < 0.05 or > 0.95) instead of dropping it.
- **Variants computed:** overall EPA/play, pass EPA/dropback, rush EPA/carry, success rate. Mostly offense and defense, plus a combined "net" rating.
- **Preseason prior (the early-season fix):** start each season from last season's final rating pulled ~1/3 of the way toward the league average (tune the factor on past seasons). Blend current-season evidence in by plays observed, so by around Week 6 the prior has little weight. Optionally adjust the prior for an offseason QB change (starting QB changed → pull further toward average). *(Tuned: a 0.1 pull from last season's full-season rating, which is already shrunk. The prior fades more slowly than planned, because longer memory predicted better: about 50% of the rating in week 4, 38% in week 6, 24% in week 10. The QB-change pull was set to 0 because it hurt. See D42 and D46.)*
- **Elo** (also used as a baseline): standard NFL Elo with margin-of-victory multiplier, home field, and pull back toward the average between seasons. Computed from 2002 on so it has history.

### Trend

"Trend" means **a change in underlying strength**, not win-loss record.

- `trend_delta` = net rating now minus net rating 3 weeks ago (both computed the same way).
- `perf_vs_expected` = rolling mean over the last 3 games of (actual EPA margin minus the EPA margin the ratings expected before the game).
- **Direction:** `up` / `down` when `trend_delta` is outside a band set from its historical week-to-week spread (start at the top and bottom ~20% of historical deltas). Otherwise `stable`.
- **Drivers:** the 2–3 parts that moved most (pass offense, rush defense, and so on), plus supporting evidence (QB change, injuries, NGS or PFR shifts). These go to the payload as structured "drivers".
- **Validation (keep it honest):** test whether `trend_delta` predicts next-3-week performance *beyond* the current rating. If it adds nothing, the digest presents trends as **descriptive** ("form has improved") rather than predictive. The digest never implies more than the evidence shows.

### As built in P02 (decisions D41–D45)

Code: `features/asof.py`, `features/leakage.py`, `models/ratings.py`, `models/elo.py`, `models/trend.py`, `models/trend_evidence.py`, `models/ratings_eval.py`, `models/ratings_runs.py`, `models/ratings_build.py`. Commands: `nfl ratings build | tune | eval | validate-trend`.

**Model.** For each split (all plays; dropbacks incl. sacks and scrambles; designed runs) and metric (EPA, success), one weighted ridge:
`y = mu + off[posteam] + def[defteam] + hfa · home`.
- **Play filter:** pass and run play types with an EPA. That drops kneels, spikes, no-plays and two-point tries.
- **Play weight:** garbage time × 0.25, multiplied by the recency weight `d^(k-1)` for a play k weeks before the as-of week, where `d = 0.5^(1/half_life)`.
- **Shrinkage:** every coefficient is pulled toward a target with strength `ridge_alpha`, counted in weighted plays.
  - A team's target at week 1 is last season's **full-season** rating pulled `prior_regression` toward average. Offense can be pulled further after a week-1 QB1 change.
  - The target fades with the same `d^(w-1)`. Week 1 equals the prior, and late in the season it is a plain ridge toward average.
- **Home field** is held at the mean of the previous 3 seasons' full-season estimates.
- `def` is EPA (or success) **allowed**, so lower is better, and `net = off − def`. Team effects sum to zero every week.

**Objective (tuning).** Ratings as of week w predict week w's per-play EPA margin as `net[home] − net[away] + home field`, where home field is 0 at neutral sites. The score is the MSE on regular-season games, walk-forward over 2015–2025 (2,895 games). Two fixed baselines:
- last season's raw net EPA/play;
- this season's raw net EPA/play so far.

**Outputs** (in `features/`, one row per team per as-of week; week w = built from weeks before w):
- `team_ratings`
- `team_elo`
- `team_trends` (with evidence fields)
- `team_trend_drivers`

**Trend.**
- `trend_delta` exists from week 4.
- `perf_vs_expected` uses up to 3 games and starts at week 2.
- `direction` bands use the 20th/80th percentiles of earlier seasons' regular-season deltas, which needs at least 3 seasons, so it is null for 2010–2012.
- Drivers rank the 4 parts (pass/rush × offense/defense) by change × the league's as-of share of plays.

**Availability (D44).**
- A game counts from the week after its scheduled week. Live runs wait for that week to finish (readiness check), so a postponed game counts once it is played.
- PFR evidence lags one week for every key, historical too.
- Week-0 NGS season totals of the key's own season are never visible.
- Dated depth charts count for the week-1 QB check only if published by the Tuesday of week 1.

**Tuned and validated (D46, D47; model card [team_ratings](model_cards/team_ratings.md)).**
- Settings: `half_life_weeks` 12, `prior_regression` 0.1, `ridge_alpha` 250, no extra QB-change pull.
- Objective MSE 0.1041 vs 0.1194 (last season) and 0.1249 (season to date). It also beats both baselines in weeks 1–3.
- Elo walk-forward Brier for 2015–2025: 0.2214.
- **Trends are descriptive:** `trend_delta` adds no predictive value beyond the rating (ΔR² −0.0002).

---

## B. Game model (win probability)

### Targets

- `home_margin` (regression) → turned into a win probability with a fitted normal error distribution (σ estimated from walk-forward residuals, ≈ 13–14 points historically).
- `home_win` (classification, ties dropped or counted as 0.5) → a direct logistic model.
- Keep whichever version has the better walk-forward Brier score and calibration. Both produce `home_win_prob` and `expected_margin`.

### Two versions

| Version | Features | Purpose |
|---|---|---|
| **Model-only** | Football features only (below) | Measures whether our analysis adds anything, and is used for "where the model disagrees with consensus" |
| **Market-informed** | Model-only features + market spread and total | The most accurate probability, **shown in the digest** |

If current lines are missing at run time, the digest falls back to model-only and says so in the footer.

### Predicted score

Alongside win probability, the game model predicts **each team's points** (two regression heads, or a margin plus total split back into two scores). That gives a predicted score (for example "KC 24 – DEN 21"), consistent with the margin and win probability. Scored with points MAE per team and total-points MAE. Full details in [11](11-prediction-targets.md).

### Features

- Rating differences: net, pass offense vs pass defense, rush offense vs rush defense (home and away combinations)
- Elo difference
- Home field (0 for neutral sites, including international games), travel distance and time zones crossed
- Rest difference (`home_rest - away_rest`), short week, coming off a bye
- **QB status:** expected starter vs the QB the ratings were built on. A backup starting is a large effect, estimated from historical QB-change games
- Injury load: snap-weighted absence of key starters by position group (QB, OL, WR/TE, pass rush, secondary)
- Weather: wind, temperature, precipitation (live: Open-Meteo **forecast** at run time; training: actual game weather, with a note on the mismatch); dome / closed roof
- External ratings: ESPN FPI (feature and benchmark) and ESPN QBR, if collected
- Divisional game; week of season (to reflect uncertainty early on)
- Market (market-informed version only): spread-implied probability, total

### Algorithms (in order)

1. Logistic regression or ridge on a small feature set: rating differences, home field, rest, QB status.
2. LightGBM on the full feature set, with monotonic constraints where the direction is known (for example, a higher rating difference → a higher win probability).
3. Keep the simplest model that's statistically as good. Data is small (~285 games/season; train on 2010–2025 ≈ 4,500 games), so gradient boosting may not beat logistic regression, and that's an acceptable result.

### Calibration

Check probabilities with reliability diagrams and expected calibration error. If the boosted version is miscalibrated, fit isotonic or Platt scaling on walk-forward predictions.

### Evaluation

Walk-forward by week across 2018–2025 (each week predicted with a model trained only on earlier data):

| Metric | Use |
|---|---|
| Brier score, log loss | Main metrics |
| Accuracy (pick = probability > 0.5) | Easy to read; shown in the report card |
| Calibration curve + ECE | Probability quality |
| Margin MAE | Expected-margin quality |
| Comparisons | Home team always (≈57%), Elo, market-implied (closing) |

**Bar to ship:** the model-only version beats Elo on Brier score across the walk-forward seasons. **Stretch goal:** the market-informed version matches or beats the market alone. Reported per season and pooled.

### As built in P03 (decisions D48–D51)

Code: `features/game.py` (feature table), `features/qb.py` (QB status), `features/venues.py` (travel), `models/backtest.py` (walk-forward harness, reused by P06/P08), `models/metrics.py`, `models/game_model.py`, `models/game_runs.py`. Commands: `nfl features game`, `nfl backtest game | game-weights`, `nfl train game`. Full details and numbers: [model card](model_cards/game-model-v0.md).

**Model (D48).** Win probability comes from the **margin** route: ridge margin (no intercept) → `Φ(margin / σ)`, σ = RMSE of earlier walk-forward residuals (≈ 13). A logistic head exists (`win_method: logistic`) but was worse on the tuning window.
- **Margin features:** net rating difference, Elo difference, home field (0 at neutral sites) and **QB status** (`qb_adj_diff`).
- **Scores:** a ridge **total** head (offense and defense rating sums, QB status, league scoring level, rolling team points, roof), split as home = (total + margin)/2 and away = (total − margin)/2, so score, margin and probability always agree.
- **Training:** walk-forward from 2011 (2010 ratings have no prior), current-season weights 3× / 1.5× / 1× (D28 kept: the sweep was flat), no calibration layer (it didn't help).
- **Feature selection:** chosen on the 2013–2017 tuning window. Pass/rush matchups (`(home_off + away_def) − (away_off + home_def)`, since `def` is EPA allowed), rest, bye, short week, divisional game, travel distance, time zones and success-rate ratings are built but **not used in v0**: they didn't help on 2013–2017 (P08's LightGBM gets them).
- **Week of season:** not a feature in v0. The ratings' own preseason prior already carries the early-season uncertainty.

**QB status (D49)** = value of the expected starter − value of the QBs the team's recent games were played with, in EPA per dropback.
- **Value:** career dropbacks, 24-week half-life, shrunk to −0.05 by 250 dropbacks.
- **Expected starter on Tuesday:** the QB with most dropbacks in the team's last game. Week 1: the depth chart published by Tuesday, else last season's main starter. First playoff game: most frequent starter of the last 3 games.
- **Live runs:** the schedule's projected starter → the depth chart → the Tuesday starter; whoever is picked, an Out/Doubtful QB is replaced by the next one on the chart.
- **Backtests use the Tuesday rule only** (leakage rule 4). Knowing each game's listed starter would add another −0.0016 Brier, the case for the Saturday injury update.

**Market variant:** + spread and total. Current lines: nflverse first, then the Odds API median, then ESPN (D40). Games without a line fall back to model-only and are flagged (`market_fallback`).

**Baselines (D50):**
- home team: the walk-forward home win rate;
- Elo: as of the game's week (the Tuesday view; P02's 0.2214 was game-by-game, so it saw same-week earlier games);
- market: `Φ(spread / σ_m)`, with the vig-free moneyline as a reference;
- ESPN FPI isn't a backtest baseline: only the current snapshot is collected.

**Results (walk-forward 2018–2025, 2,227 games, Brier):**

| Predictor | Brier |
|---|---|
| Model-only | **0.2199** |
| Elo | 0.2221 |
| Market-informed | **0.2102** |
| Closing market | 0.2104 |
| Home team always | 0.2477 |

- Model-only beats Elo in every part of the season and in 5 of 8 seasons; Elo was ahead by ≤ 0.0008 in the other 3.
- ECE 0.0315: at the noise floor (a perfectly calibrated model would show 0.031 at the 95th percentile), plus home field shrinking since 2020 (P08 follow-up).
- Margin MAE 10.19 (spread 9.83); points MAE per team 7.46 (rolling average 7.65); total MAE 10.63 (closing total 10.42).

**Outputs:** `runs/<season>/week<NN>/predictions_games.parquet`, one row per game per variant, with `is_primary` marking the digest row (schema in the model card). Fitted models go to `models/game-model/<season>-w<NN>/` and the W&B artifact `game-model:<season>-w<NN>`. Backtest predictions for every week go to `runs/backtests/game/<variant>/`.

### As built in P08: game model v1, evaluated, v0 kept (decisions D78, D79)

Full details, numbers and charts: [game model v1 card](model_cards/game-model-v1.md).

- **Features added** (`features/game_extra.py`, joined by `features/game.py`; v0 ignores them): a **Tuesday-view injury load** (snap-weighted regulars missing from the team's last game, by OL / skill / front / secondary; QBs left out; not the week's injury report, because the live run is on Tuesday), **coarse weather** (`wind_15`, `cold_32`: actual readings in history, the Open-Meteo forecast live), a **trailing home edge** (the league's home margin over the previous 3 seasons). ESPN FPI / QBR aren't used: only current snapshots exist (D50). Travel, rest, byes, matchups and success rate were already in the table.
- **Model** (`models/game_model_v1.py`): LightGBM margin and total heads **boosted from v0's ridge prediction** (`init_score`), shallow and few (4 leaves, 50 trees), with **monotonic constraints**; probability `Φ(margin / σ)` with σ from its own walk-forward misses; optional Platt / isotonic calibration. Every v1 run also carries **v0's exact walk-forward predictions on the same games** (`v0_prob`, `v0_margin`, `v0_total`), so W&B shows the two side by side.
- **Tuning** on 2013–2017 only (W&B sweep `47vq67gw`, 24 settings): every setting lost to v0 (best +0.0008 Brier); trees from scratch lost by 0.004–0.009; calibration made it worse.
- **Reported 2018–2025:** model-only 0.2193 vs v0 0.2199 (−0.0005, 95% −0.0012 to +0.0002; 5 of 8 seasons; ECE 0.035 vs 0.032); market 0.2101 vs 0.2102. **The ✋ rule (written before these runs) keeps v0** (D79): the gain is inside the noise and calibration is worse. The new features barely relate to v0's misses (|r| ≤ 0.06); wind is the one consistent signal, and it moves totals, not winners.
- **Switch:** `settings.yaml` → `game_model.version` (`v0`) selects the model the weekly run fits; `nfl backtest game --version v1`, `nfl tune game` and `nfl train game --version v1` run v1. v1 backtests are saved as `runs/backtests/game/v1_<variant>/`, never over the canonical v0 folders the digest backtests, the player model's game context and the drift replay read.

---

## C. Player model

### Targets by position group

The **full list of offense and defense targets**, their baselines, metrics and realistic accuracy ceilings is in **[11-prediction-targets.md](11-prediction-targets.md)**. That's the authoritative list. In short:

| Group | Main target(s) |
|---|---|
| QB | Passing yards, pass TDs, INTs, EPA per dropback, rushing yards |
| RB | Carries, rushing yards, receptions, scrimmage yards |
| WR / TE | Targets, receptions, receiving yards |
| All skill players | Chance of scoring a TD |
| EDGE / DL | **Pressures** (main), sacks (expected and chance of 1+) |
| LB / S | Tackles |
| CB / S | Targets, completions and yards allowed in coverage; chance of an INT or pass defended |

The **"players to watch"** list is built from the main target for each position group (the first one listed in 11).

### "Players to watch" definition

- Compute a **baseline** = a weighted mix of the player's last 4 games and season to date, pulled toward their prior-season rate early in the year.
- `outperformance = projection_p50 - baseline`.
- Watch list = the largest positive outperformance among players with a real role (snap share ≥ 50% over the last 2 games, or a clear role change, such as a starter ruled out ahead of them). Capped at 2 per team, 6–8 total in the payload. The digest shows 4–6. *(As built since 2026 week 5, D70: 10 offense + 10 defense in two tables, the prose on the most notable few.)*
- Also flag notable **negative** outperformance (a tough spot) as candidates for "Matchup / risk to watch".

### Features

- **Usage:** snap share and its trend, target share, share of team air yards, carry share, red-zone share, average depth of target (from play-by-play and snap counts)
- **Efficiency:** yards per target, EPA per target or carry, **NGS separation, cushion, yards after catch above expected, rush yards over expected**, PFR broken tackles and drops
- **Team context:** team pass rate over expected, pace, the game model's expected margin (likely game script), market-implied team total
- **Opponent:** opponent-adjusted yards and EPA allowed to the position group; opponent pressure rate vs own team's pressure-allowed rate (pass rushers and QBs); opponent blitz rate (FTN)
- **Ripple effects:** teammates ruled out (targets left open), QB change (from the graph's QB–receiver history), returning starters
- **Availability:** the player's own injury report status and practice participation

### Model

- LightGBM with **quantile objectives** (P10 / P50 / P90) per position group → a projection plus an 80% interval.
- A wide interval or a small sample → `confidence: low` in the payload → the LLM must hedge.
- **Drivers:** per-prediction SHAP values; the top 3 features are mapped to readable phrases (for example, `opp_def_rec_yds_allowed_wr_adj` → "opponent allows the most receiving yards to WRs after adjusting for schedule") and passed in the payload with their values.

### Evaluation (walk-forward, 2019–2025)

| Metric | Use |
|---|---|
| MAE vs baseline MAE | Must beat the rolling-average baseline per position group |
| Spearman rank correlation (projected vs actual outperformance) | Does the ordering mean anything? |
| Interval coverage | P10–P90 should cover ~80% of outcomes |
| Watch-list hit rate | % of watch-list players who beat their baseline (vs ~50% by chance); also shown in the report card |

### As built in P06 (decisions D64–D68)

Results, settings and how to read the charts: [player model card](model_cards/player-model-v1.md) (overview) and the family cards ([QB](model_cards/player-qb.md), [RB](model_cards/player-rb.md), [WR/TE](model_cards/player-wrte.md), [defense](model_cards/player-defense.md)). A plain-language walkthrough: [player projections guide](guides/player-projections.md).

- **11 models, one per target × position group** (D64): QB passing yards and EPA per dropback; RB rushing yards, carries, receptions, scrimmage yards; WR/TE receiving yards, targets, receptions; EDGE/DL pressures; LB/S tackles. WR and TE share a model (with a TE flag), as do LB and S. **EDGE/DL = DL + LB**, because nflverse labels many edge rushers LB.
- **Who is projected and scored:** only games a player took part in on his side of the ball; for QB targets only the team's main QB in that game (most dropbacks). Live, the expected players: on the team, active in one of its last 3 games or back on the active roster after 3+ games for it, not Out / Doubtful / reserve; QBs = the P03 expected starter.
- **Amounts** (yards, EPA): LightGBM quantile models P10 / P50 / P90 plus a conformal shift fitted on the model's own walk-forward misses of the last 2 seasons. **Counts:** a Poisson LightGBM for the mean, a negative binomial with a dispersion and a range tail fitted on the same history; P50 is its median. Both hold ~80% of outcomes in the range.
- **Baseline ("player rolling"):** 0.5 × last 4 + 0.5 × season to date, pulled toward last season's mean with 3 pseudo-games; last season when he has no game this season; the position average for his snap-share role when he has no history. **Count targets are scored against the baseline's median** (D65): a median beats any mean on MAE for skewed counts by itself.
- **Features** (126, all as of the row's week; the PFR / FTN ones and the pressures label lag one more week): own recent output (`own_*`), usage (`use_*`), team context including the P03 expected margin and points and the market-implied team total (`team_*`), the graph's ripple effects recomputed in Polars (`rip_*`), availability from the week's injury report (`avail_*`, the "Friday view", D66), efficiency incl. NGS / PFR / FTN (`eff_*`), opponent incl. production allowed to the position group over expectation (`opp_*`). Leakage tests per family (future-invariance on a synthetic league; a truncation check on the real data).
- **Walk-forward 2019–2025**, weekly refits after 2 burn-in seasons; current-season sample weights (D28); hyperparameters from a 12-point grid per target scored on 2017–2018 only (D68).
- **Drivers:** LightGBM's own SHAP values (`pred_contrib`), top 3 per projection, phrased from `features/descriptions.yaml`. SHAP explains the projection against the model's average, not against the player's own baseline, so the digest lists the drivers that agree with the projection's direction first and adds a note when the baseline rests on little history.
- **Watch list** (doc 04 rule): the main stat per group, a real role (snap share ≥ 50% over the last 2 games, or vacated usage), ranked by (projection − baseline) / the target's typical baseline miss; **10 offense + 10 defense** chosen separately (D70; Rishi: more players to watch and to grade the model on), at most 2 per team per side, soft per-group caps (QB ≤ 3, RB ≤ 4, WR/TE ≤ 4; EDGE/DL ≤ 6, LB/S ≤ 6). Before D70: 8 picks with at most 3 defenders (D69). The **5** most negative gaps among regular starters are **tough spots** for *Matchup / risk*.
- **Weekly path** (D67): the `player` step between `graph` and `digest`.

### As built in P08: more targets, team totals, consistency (decisions D80–D82)

Details and numbers: [P08 player targets card](model_cards/player-p08.md), [team stats card](model_cards/team-stats-v1.md), doc 11 "Measured in P08".

- **12 new player targets, all shipped** (D82): QB passing TDs, interceptions (counts with P(≥ 1), P(≥ 2)) and rushing yards; the chance of a TD for RBs and WR/TE; EDGE/DL sacks (count + chance; a half sack counts) and QB hits; a new **CB/S** pool with coverage targets / completions / yards allowed (PFR, a week late) and the chance of an interception / pass defended. A third model kind, `prob`: LightGBM binary + a Platt layer fitted on the model's own earlier walk-forward probabilities; event counts' P(≥ 1) is recalibrated the same way. CB/S has its own feature family (`cvg_*`, `features/player_coverage.py`, last 8 games: the opposing offense's targets per game, air yards per target, completion rate and yards per target; the defender's own defense's targets faced, depth, completion rate and yards allowed, pressure rate and pass-defense rating), used by CB/S models only (`player_model.GROUP_PREFIXES`). The weekly run refits the 23 `player_model.live_targets`; a run that projects no CB/S target builds no CB rows.
- **Team stat totals** (D80; `models/team_runs.py`): passing / rushing yards, sacks made / taken shipped (takeaways not), refit fail-soft inside the weekly `player` step into `predictions_teams.parquet`; scored in the season scoreboard as `position_group = "TEAM"` (never in the player report card).
- **Consistency layer** (D81; `models/consistency.py`): receptions ≤ targets clamped on the week's unstarted rows; the receivers-vs-QB-vs-team passing-yards gaps (on means) logged to `consistency.json` and W&B; no yards adjustment (it hurt the WR/TE watch picks).
- **Digest:** the watch tables gain code-written "TD chance" and "Sack chance" columns (table-only, never in the payload or the LLM input); the scorecard's `player_mae_vs_baseline` stays on the 11 P06 targets so the season series stays comparable.
- **Determinism:** the P06 ripple sums are now sorted before summing (two builds used to differ at 1e-16, enough to flip one LightGBM split in `receptions-rb`: −0.018% MAE).

---

## Training and retraining cadence

This answers the brief's open question.

- **Before the season (once):** tune hyperparameters, ratings half-life and the prior pull-back factor using walk-forward evaluation over past seasons. These stay **fixed for the season**.
- **Every week:** **refit from scratch** on an expanding window (all training seasons plus current-season weeks so far). Boosted trees aren't fine-tuned; refitting takes seconds at this data size.
- **Current-season weighting (explicit):** training rows get sample weights that favor recent data. Current-season rows get the highest weight, last season less, older seasons less again (start: current 3×, last season 1.5×, older 1×; tuned before the season with walk-forward evaluation). Historical seasons teach the model *how much* form, matchups and injuries matter; current-season rows and features provide *the form itself*.
- **How a week's new data reaches the next prediction.** For example, on the Tuesday after Week 4:
  1. Ingest Week 4.
  2. Ratings update (`nfl ratings build`): every week's ratings are recomputed from scratch with the season's fixed settings (12-week recency half-life; the preseason prior is about half the rating by week 4 and a quarter by week 10; D46).
  3. Player baselines and rolling features update (last 4 games plus season to date).
  4. Every model is refit, including all 2026 Weeks 1–4 rows.
  5. Predict Week 5.
- **Versioning:** every weekly fit is a versioned W&B artifact tagged with `{season}-w{NN}`. The production model is marked with the `production` alias.
- **Drift response:** if rolling 4-week Brier score (game) or MAE (player) is worse than the baseline for 3+ weeks in a row, open an investigation. Don't automatically retune mid-season.

## Model cards

Each model keeps a short card in `documentation/model_cards/` (or as the W&B artifact description). It records: target, features, training window, evaluation results vs baselines, known biases (the closing-line optimism), and the date it was last tuned.
