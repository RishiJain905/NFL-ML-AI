# Model card: team ratings, Elo and trend (P02)

**Family:** Track 1 model A ([04 → A](../04-track1-models.md#a-team-ratings-and-trend)) · **Last tuned:** 2026-10-03 (approved by Rishi, D46) · **Code:** `models/ratings.py`, `elo.py`, `trend.py`, `trend_evidence.py` · **Tables:** `features/team_ratings`, `team_elo`, `team_trends`, `team_trend_drivers` (rebuilt by `nfl ratings build`; `_meta/team_ratings.json` holds the parameters, git commit and data version of the last build).

## What it is

Opponent-adjusted team strength in **EPA per play** (and success rate), for offense and defense, split into all plays / dropbacks / designed runs. Every row is **as of** a (season, week): built only from games in earlier weeks.

- **Method:** weighted ridge `y = mu + off[team] + def[opponent] + hfa·home` (D41).
  - Plays are recency-weighted with a 12-week half-life, and garbage time weighs 0.25.
  - Coefficients shrink toward a target with strength 250 weighted plays. The target starts each season at last season's full-season rating × 0.9 and fades at the same half-life.
  - Home field is held at the trailing 3-season estimate (D42).
- **Not a learned label:** it's a calculation, validated by how well it predicts the next games.
- **Elo** (baseline for P03): 538-style, K 20, home field 48, 1/3 reversion, margin-of-victory multiplier, from 2002 (D43).

## How it works, in plain language

New to EPA? See [04 → Key terms](../04-track1-models.md#key-terms).

**Nothing here is "trained" like a neural net or LightGBM.** There are no saved weights and no model file. Every `nfl ratings build` recomputes all of history from the data and three fixed settings, in about 0.2 seconds.

- **Ratings: a regression solved fresh for every week.**
  - The equation: `EPA of a play = league average + offense strength + defense strength + home field`. It is solved over this season's plays from earlier weeks, for each of 3 play types × 2 metrics.
  - Solving it across every play untangles schedule strength. Gaining 0.2 EPA/play against a great defense earns more credit than doing it against a bad one.
  - Each week's plays are summarized once into a 66×66 matrix, so any week's ratings are a single small solve.
- **Elo: a running score, updated game by game from 2002.**
  - Before a game it gives a win probability.
  - After the game, the winner takes points from the loser. Upsets and blowouts move it more.
  - Each offseason every team is pulled 1/3 back toward average.
- **Trend: arithmetic on the ratings.**
  - The change over 3 weeks, plus performance vs what the ratings expected over the last 3 games.
  - Which part moved most (pass/rush, offense/defense), and evidence: QB change, key injuries, pressure-rate shifts.

**What was actually learned: three settings, chosen once by a backtest.** For every week of 2015–2025, the backtest took the ratings as of that week, predicted that week's games, and measured the error (sweep `dwyj31wk`, 175 combinations). The settings stay fixed for the season (D17) and are retuned before 2027 (P10).

| Setting | Value | Plain meaning |
|---|---|---|
| Half-life | 12 weeks | How fast old games stop counting: a game 12 weeks older than the latest one counts half as much, 24 weeks older a quarter. Within a ~17-week season, early games keep real weight. Short half-lives (4) chase noise; anything 8+ was about equally good |
| Prior pull | 0.1 (10%) | In week 1, every team starts at last season's rating moved 10% toward average, a "regression to the mean" for the offseason. 0–20% were about equal; 50% threw away useful information and hurt weeks 1–3 |
| Shrinkage | 250 plays | How much evidence it takes to move a team away from its starting point: the prior counts like 250 plays. Too little (100) chases noise; too much (650) reacts too slowly |

**How fast the preseason prior fades** (average share of last season's rating in the current rating, 2015–2025):

| Week | 1 | 2 | 4 | 6 | 10 | 18 |
|---|---|---|---|---|---|---|
| Prior share | 100% | 78% | 52% | 38% | 24% | 11% |

**How it's applied each week.** Every table row is keyed "as of week w": it is built only from games before week w. A week-5 game therefore joins its week-5 rows directly, with no look-ahead (D44, covered by tests). From P07 on, each Tuesday runs ingest → curate → `nfl ratings build` with the fixed settings, and the current week's rows are the ratings going into this week's games.

**Where the outputs are used:**
- **P03 game model:** rating differences are the core features (net, pass offense vs pass defense, rush offense vs rush defense), with the Elo difference and home field. For example, net +0.10 vs −0.02 plus home field 0.01 gives an expected margin of +0.13 EPA/play, which the game model turns into a win probability and score. Elo's Brier score (0.2214) is the bar it has to beat.
- **P04 digest:**
  - "Team trend shifts" reads `team_trends` (direction, drivers, evidence), worded as form, never as a forecast (D47).
  - `prior_weight` flags low confidence early in the season.
- **P05 graph:** ratings and trends become weekly attributes of team nodes.
- **P06 player model:** opponent pass and rush defense ratings become matchup features.

## Parameters (`config/settings.yaml` → `ratings`)

| Parameter | Value | How chosen |
|---|---|---|
| `half_life_weeks` | 12 | W&B sweep `dwyj31wk`: 4 → MSE 0.1051, 12 → 0.1041, flat from 8 to 52 |
| `prior_regression` | 0.1 | 0.0–0.2 about equal; 0.5 hurts weeks 1–3 (0.1109 vs 0.1079) |
| `ridge_alpha` | 250 | 100 → 0.1063, 250 → 0.1041, 650 → 0.1048 |
| `qb_change_regression` | 0 | Sweep `pbpcouy1`: every extra pull is worse (0.5 → 0.1043) |
| `garbage_weight` / `garbage_wp` | 0.25 / 0.05 | Fixed, not tuned |

## Evaluation (walk-forward 2015–2025, 2,895 regular-season games)

Target: week-w per-play EPA margin (home offense − away offense), predicted from ratings as of week w as `net[home] − net[away] + home field`. Lower MSE is better. Eval run: [`hcir0po5`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/hcir0po5).

| | Ratings | Last season's raw net (baseline) | Season-to-date raw net (baseline) |
|---|---|---|---|
| MSE, pooled | **0.1041** | 0.1194 (−12.9%) | 0.1249 (−16.7%) |
| MSE, weeks 1–3 | **0.1079** | 0.1094 | 0.1909 |
| MSE, weeks 4–8 | **0.1042** | 0.1171 | 0.1195 |
| MSE, weeks 9+ | **0.1027** | 0.1240 | 0.1054 |
| Correlation with final point margin | **0.366** | 0.239 | 0.301 |

The ratings beat both baselines in **all 11 seasons**:

| Season | Games | Ratings MSE | Last-season MSE | To-date MSE | Elo Brier |
|---|---|---|---|---|---|
| 2015 | 256 | 0.1050 | 0.1274 | 0.1385 | 0.2242 |
| 2016 | 256 | 0.0943 | 0.1050 | 0.1079 | 0.2173 |
| 2017 | 256 | 0.0993 | 0.1132 | 0.1280 | 0.2168 |
| 2018 | 256 | 0.1244 | 0.1364 | 0.1452 | 0.2222 |
| 2019 | 256 | 0.1131 | 0.1296 | 0.1481 | 0.2217 |
| 2020 | 256 | 0.0950 | 0.1052 | 0.1101 | 0.2144 |
| 2021 | 272 | 0.1131 | 0.1232 | 0.1334 | 0.2310 |
| 2022 | 271 | 0.0817 | 0.0976 | 0.0933 | 0.2215 |
| 2023 | 272 | 0.1117 | 0.1184 | 0.1296 | 0.2326 |
| 2024 | 272 | 0.1081 | 0.1305 | 0.1239 | 0.2107 |
| 2025 | 272 | 0.0993 | 0.1272 | 0.1171 | 0.2223 |

**Elo:** walk-forward Brier **0.2214** over all game types (0.2217 regular season), log loss 0.635, accuracy 64%. It slightly over-rates home teams: mean probability 0.562 vs a 0.550 home win rate.

## Trend (D47: descriptive)

Validation run [`ae30tzkf`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/ae30tzkf) asks whether `trend_delta` predicts a team's opponent-adjusted play over the next 3 weeks beyond its current rating. The answer is no:
- ΔR² is −0.0002 (90% CI −0.0013 to +0.0008), and it helps in only 5 of 11 seasons.
- `perf_vs_expected` is the same (ΔR² −0.0002).

So the digest describes form ("has improved") and never forecasts it.

## Known limits and biases

- **Early season:** weeks 1–3 are mostly the prior (`prior_weight` ≈ 1 in week 1). The digest shows lower confidence there.
- **Home field in EPA terms is small and noisy.** It is held at a trailing 3-season value, so a sudden change (2020's empty stadiums) is adopted with a lag.
- **QB change:**
  - The week-1 QB-change flag uses depth charts published by Tuesday of week 1.
  - The curated 2025+ week-1 charts are game-day snapshots, so the flag is unknown (False) for 2025–2026. That doesn't matter while `qb_change_regression` is 0. P03's QB-status feature needs Tuesday-dated depth charts.
- **In-sample tuning:** the parameters were tuned on 2015–2025 walk-forward, the same seasons P03 evaluates on. That's 3 tuned numbers on a flat surface, so the optimism is small but real.
- **Playoff weeks:** as-of rows exist for all 32 teams. Eliminated teams keep their last ratings, drifting slowly toward average as their data ages.
- **Postponed games** count from the week after their scheduled week, matching live runs, which wait for the week to finish (D44).
