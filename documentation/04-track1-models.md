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

### Ratings

Separate offense and defense ratings for each team, split by pass and rush, measured in **EPA per play**, adjusted for opponent strength.

- **Method:** ridge regression on play-level EPA with an offense-team indicator, a defense-team indicator and home field, fit on a recency-weighted window. Each play's weight = exponential decay by weeks ago (half-life tuned, starting point ~4 weeks). Ridge shrinkage stabilizes small samples.
- **Play filters:** drop kneels, spikes and no-plays. Down-weight garbage time (win probability < 0.05 or > 0.95) instead of dropping it.
- **Variants computed:** overall EPA/play, pass EPA/dropback, rush EPA/carry, success rate. Mostly offense and defense, plus a combined "net" rating.
- **Preseason prior (the early-season fix):** start each season from last season's final rating pulled ~1/3 of the way toward the league average (tune the factor on past seasons). Blend current-season evidence in by plays observed, so by around Week 6 the prior has little weight. Optionally adjust the prior for an offseason QB change (starting QB changed → pull further toward average).
- **Elo** (also used as a baseline): standard NFL Elo with margin-of-victory multiplier, home field, and pull back toward the average between seasons. Computed from 2002 on so it has history.

### Trend

"Trend" means **a change in underlying strength**, not win-loss record.

- `trend_delta` = net rating now minus net rating 3 weeks ago (both computed the same way).
- `perf_vs_expected` = rolling mean over the last 3 games of (actual EPA margin minus the EPA margin the ratings expected before the game).
- **Direction:** `up` / `down` when `trend_delta` is outside a band set from its historical week-to-week spread (start at the top and bottom ~20% of historical deltas). Otherwise `stable`.
- **Drivers:** the 2–3 parts that moved most (pass offense, rush defense, and so on), plus supporting evidence (QB change, injuries, NGS or PFR shifts). These go to the payload as structured "drivers".
- **Validation (keep it honest):** test whether `trend_delta` predicts next-3-week performance *beyond* the current rating. If it adds nothing, the digest presents trends as **descriptive** ("form has improved") rather than predictive. The digest never implies more than the evidence shows.

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
- Watch list = the largest positive outperformance among players with a real role (snap share ≥ 50% over the last 2 games, or a clear role change, such as a starter ruled out ahead of them). Capped at 2 per team, 6–8 total in the payload. The digest shows 4–6.
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

---

## Training and retraining cadence

This answers the brief's open question.

- **Before the season (once):** tune hyperparameters, ratings half-life and the prior pull-back factor using walk-forward evaluation over past seasons. These stay **fixed for the season**.
- **Every week:** **refit from scratch** on an expanding window (all training seasons plus current-season weeks so far). Boosted trees aren't fine-tuned; refitting takes seconds at this data size.
- **Current-season weighting (explicit):** training rows get sample weights that favor recent data. Current-season rows get the highest weight, last season less, older seasons less again (start: current 3×, last season 1.5×, older 1×; tuned before the season with walk-forward evaluation). Historical seasons teach the model *how much* form, matchups and injuries matter; current-season rows and features provide *the form itself*.
- **How a week's new data reaches the next prediction.** For example, on the Tuesday after Week 4:
  1. Ingest Week 4.
  2. Ratings update, with recency weighting (half-life ~4 weeks) and the preseason prior fading out by about Week 6.
  3. Player baselines and rolling features update (last 4 games plus season to date).
  4. Every model is refit, including all 2026 Weeks 1–4 rows.
  5. Predict Week 5.
- **Versioning:** every weekly fit is a versioned W&B artifact tagged with `{season}-w{NN}`. The production model is marked with the `production` alias.
- **Drift response:** if rolling 4-week Brier score (game) or MAE (player) is worse than the baseline for 3+ weeks in a row, open an investigation. Don't automatically retune mid-season.

## Model cards

Each model keeps a short card in `documentation/model_cards/` (or as the W&B artifact description). It records: target, features, training window, evaluation results vs baselines, known biases (the closing-line optimism), and the date it was last tuned.
