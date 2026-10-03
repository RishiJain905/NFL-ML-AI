# P02: Team Ratings, Elo and Trend

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P01
- **Unlocks:** P03
- **Read first:** [04 → A. Team ratings and trend](../04-track1-models.md#a-team-ratings-and-trend), [04 → Leakage rules](../04-track1-models.md#leakage-rules-apply-to-all-models), [08 Experiment tracking](../08-experiment-tracking.md)

## Goal

Opponent-adjusted EPA ratings (offense and defense, pass and rush splits) for every team as of every week from 2010 to now, plus Elo and a trend measure with drivers. These are the foundation for the game model, and for the "Team trend shifts" section.

## Scope

- **In:** the as-of feature framework (shared by every later model), ratings, preseason prior, Elo, trend + drivers, tuning, validation.
- **Out:** win probabilities (P03).

## Tasks

### As-of framework (used by every later phase)
- [x] 🤖 `features/asof.py`: `as_of(season, week)` gives a data view containing only games strictly before that week. Every feature builder takes an as-of key.
- [x] 🤖 **Leakage test helper:** builds features for a past week and asserts no input row has a game date on or after that week's games. Used in the tests of every later phase.

### Ratings
- [x] 🤖 Play filters (drop kneels, spikes, no-plays) and garbage-time down-weighting (win probability < 0.05 or > 0.95).
- [x] 🤖 Ridge regression on play-level EPA with offense-team, defense-team and home indicators, recency weighting (half-life parameter), for overall, pass and rush, plus success rate.
- [x] 🤖 Preseason prior: last season's final rating pulled toward the mean by a factor (tunable), with an optional extra pull when the starting QB changed. Blended by plays observed.
- [x] 🤖 Output table `team_ratings` (team, season, week as-of, off/def/net × overall/pass/rush, success rates, plays_observed) for 2010–2026.

### Elo
- [x] 🤖 NFL Elo (K, home field, margin-of-victory multiplier, pull back toward average between seasons) from 2002. Output `team_elo` as of each week.

### Trend
- [x] 🤖 `trend_delta` (net rating now minus 3 weeks ago), `perf_vs_expected` (rolling 3-game actual minus expected EPA margin), and the direction band from the historical spread of deltas.
- [x] 🤖 Drivers: the 2–3 rating parts that moved most + supporting evidence fields (QB change, key injuries, NGS/PFR shifts), as structured rows.

### Tuning and validation
- [x] 🤖 `nfl ratings tune` script: grid over half-life × prior pull-back × ridge alpha. Objective = how well ratings as of week w predict week w's game EPA margin, walk-forward over 2015–2025. Logs to W&B group `track1-ratings` with live per-config curves.
- [x] 🧑 **Rishi runs** `uv run nfl ratings tune` and watches the sweep in W&B. *(Run by the agent, delegated 2026-10-03: sweep [`dwyj31wk`](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/dwyj31wk), 175 runs, plus QB follow-up `pbpcouy1`.)*
- [x] 🤖 `nfl ratings validate-trend`: does `trend_delta` predict next-3-week performance beyond the rating? Logs to W&B. Decides whether trends are presented as "descriptive" or "predictive" ([D06](../10-decisions-log.md)).
- [x] 🧑 **Rishi runs** the trend validation and reviews the result. *(Run by the agent, delegated 2026-10-03; Rishi reviews it at the ✋ below.)*
- [x] ✋ **Checkpoint:** agree on the chosen half-life, prior factor and trend presentation. Write them into `settings.yaml` and a ratings model card. *(Approved by Rishi 2026-10-03: half-life 12, prior regression 0.1, ridge alpha 250, no QB pull; trends descriptive. D46, D47, [model card](../model_cards/team_ratings.md).)*

### Wrap-up
- [x] 🤖 Sanity report: top/bottom 5 teams by net rating for 2025 final and 2026 current, written to the run folder. Quick look for obviously wrong results.
- [x] 🤖 Tests: leakage test, ridge reproducibility on a fixture, Elo on a tiny hand-checked example.
- [x] ✋ **Checkpoint:** close P02. *(Approved by Rishi 2026-10-03, together with the parameters, once the code-review fixes were in and tests passed.)*

### As built: deviations from the task list (all logged in the decisions log)

- **Prior blend (D41).** The blend "by plays observed" is a ridge **shrinkage target**. Last season's rating, pulled toward average, acts as `ridge_alpha` pseudo-plays and fades at the data's half-life.
- **Prior source and home field (D42).** The prior comes from last season's **full-season** fit, not the recency-weighted end-of-season rating. Home field is held at the mean of the last 3 seasons' full-season estimates. Both were tested on real data before building.
- **QB-change pull** is tunable (`qb_change_regression`). The follow-up sweep showed that any extra pull hurts, so it is set to 0 (D46).
- **Code-review fixes (Sol, D44/D45):**
  - Drivers use the league pass share as of each key.
  - PFR evidence lags one week for every key.
  - Current-season week-0 (NGS season-total) rows are never visible as of a key.
  - Curated depth charts keep `snap_date`, and the week-1 QB check only uses charts published by the Tuesday of week 1.
  - The trend-validation target covers the next 3 **weeks**, not the next 3 games.
  - The bootstrap uses local cluster ids.
  - Postponed games stay week-based on purpose: live runs wait for the week to finish.
- **Output tables (D44).** Tables are keyed by **as-of week**; week-1 depth charts count as preseason information. They live in `features/` with DuckDB views. There are 4 tables: `team_ratings`, `team_elo`, `team_trends` (evidence fields merged in) and `team_trend_drivers`.
- **Trend (D45).** Direction bands use earlier seasons only (null for 2010–2012). Drivers are weighted by play share.
- **Extra commands.** `nfl ratings eval` is a single W&B run for the chosen parameters: by season, by week, Elo Brier. `nfl ratings build` writes the tables.
- **The tune is a W&B grid sweep.** Each configuration is one run with live per-season curves, so the sweep page shows parallel coordinates and parameter importance.
- **`nfl features`** moved to P03, where the game-model features are built.
- **Shared building blocks:**
  - `tracking.git_commit()`, `dataset_version()` and `run_sweep()`;
  - `curate.build.write_duckdb_views()`, which adds the feature tables to `nfl.duckdb`;
  - `tests/conftest.py::make_league()`, a synthetic league for tests.

## Rishi-in-the-loop moments: what to look for

- **Ratings sweep:** a parallel-coordinates or scatter chart of half-life vs the objective. Expect a sweet spot (very short half-lives are noisy, very long ones are stale). If the best is at the edge of the grid, widen the grid.
- **Prior factor:** early-season weeks (1–4) should benefit most. Compare objective by week of season: the prior should clearly help Weeks 1–3 and matter little after Week 8.
- **Trend validation:** if the incremental R² or correlation is about 0, trends are *descriptive* only. That's a normal and fine outcome.

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| Ratings exist for every team-week 2010–2026 | Row count = teams × weeks; no nulls in core columns |
| Tuned parameters recorded | `settings.yaml` + model card; W&B sweep run linked in PROGRESS |
| Ratings predict | The walk-forward objective beats a "last season's final rating" baseline (logged in W&B) |
| Elo sane | Elo-only walk-forward Brier for 2015–2025 in the usual range (~0.22–0.23); logged |
| Trend decision made | Recorded in the decisions log |
| No leakage | Leakage tests pass |

## Handoff to P03

- `team_ratings`, `team_elo` and `team_trends` tables as of every week.
- The as-of framework and the leakage test helper.

## Pitfalls / notes

- Make sure the EPA you regress on matches the play type (pass EPA uses dropbacks, including sacks and scrambles).
- Teams that relocated: ratings follow the **franchise** across the move.
- Keep ratings computation fast (vectorized / sparse ridge). The weekly pipeline recomputes the current week, and backtests recompute many weeks.
