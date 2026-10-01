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
- [ ] 🤖 `features/asof.py`: `as_of(season, week)` gives a data view containing only games strictly before that week. Every feature builder takes an as-of key.
- [ ] 🤖 **Leakage test helper:** builds features for a past week and asserts no input row has a game date on or after that week's games. Used in the tests of every later phase.

### Ratings
- [ ] 🤖 Play filters (drop kneels, spikes, no-plays) and garbage-time down-weighting (win probability < 0.05 or > 0.95).
- [ ] 🤖 Ridge regression on play-level EPA with offense-team, defense-team and home indicators, recency weighting (half-life parameter), for overall, pass and rush, plus success rate.
- [ ] 🤖 Preseason prior: last season's final rating pulled toward the mean by a factor (tunable), with an optional extra pull when the starting QB changed. Blended by plays observed.
- [ ] 🤖 Output table `team_ratings` (team, season, week as-of, off/def/net × overall/pass/rush, success rates, plays_observed) for 2010–2026.

### Elo
- [ ] 🤖 NFL Elo (K, home field, margin-of-victory multiplier, pull back toward average between seasons) from 2002. Output `team_elo` as of each week.

### Trend
- [ ] 🤖 `trend_delta` (net rating now minus 3 weeks ago), `perf_vs_expected` (rolling 3-game actual minus expected EPA margin), and the direction band from the historical spread of deltas.
- [ ] 🤖 Drivers: the 2–3 rating parts that moved most + supporting evidence fields (QB change, key injuries, NGS/PFR shifts), as structured rows.

### Tuning and validation
- [ ] 🤖 `nfl ratings tune` script: grid over half-life × prior pull-back × ridge alpha. Objective = how well ratings as of week w predict week w's game EPA margin, walk-forward over 2015–2025. Logs to W&B group `track1-ratings` with live per-config curves.
- [ ] 🧑 **Rishi runs** `uv run nfl ratings tune` and watches the sweep in W&B.
- [ ] 🤖 `nfl ratings validate-trend`: does `trend_delta` predict next-3-week performance beyond the rating? Logs to W&B. Decides whether trends are presented as "descriptive" or "predictive" ([D06](../10-decisions-log.md)).
- [ ] 🧑 **Rishi runs** the trend validation and reviews the result.
- [ ] ✋ **Checkpoint:** agree on the chosen half-life, prior factor and trend presentation. Write them into `settings.yaml` and a ratings model card.

### Wrap-up
- [ ] 🤖 Sanity report: top/bottom 5 teams by net rating for 2025 final and 2026 current, written to the run folder. Quick look for obviously wrong results.
- [ ] 🤖 Tests: leakage test, ridge reproducibility on a fixture, Elo on a tiny hand-checked example.
- [ ] ✋ **Checkpoint:** close P02.

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
