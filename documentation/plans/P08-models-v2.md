# P08: Models v2 and the Advanced Graph

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P07
- **Unlocks:** P10 (and the T04 graph additions)
- **Read first:** [04 → B. Game model](../04-track1-models.md#b-game-model-win-probability), [11](../11-prediction-targets.md) (P08 items), [05 → Q5–Q7, Q9, Q10](../05-knowledge-graph.md#query-library), [08 → Model registry](../08-experiment-tracking.md#model-registry)

## Goal

Push accuracy and insight further, without breaking production:
- **Game model v1:** LightGBM with the full feature set
- **More prediction targets:** TD / sack / INT probabilities, DB coverage stats, team stat totals, the consistency layer
- **Advanced graph:** coaching links, former teammates, style matchups, officiating crews, and **Graph Data Science** algorithms

## Scope

- **In:** everything above, each shipped only if it beats what's in production (walk-forward), promoted between weeks through the `candidate` → `production` alias.
- **Out:** a real LLM (P09), Track 2 (T-phases).

## Tasks

### Game model v1
- [ ] 🤖 Full features: snap-weighted injury load by position group, Open-Meteo forecast weather (training uses actual game weather; note the mismatch), travel, ESPN FPI / QBR if collected, monotonic constraints.
- [ ] 🤖 LightGBM win / margin / points models + calibration; backtest with the P03 harness, logged next to v0.
- [ ] 🧑 **Rishi runs** the v1 backtests and the tuning sweep.
- [ ] ✋ **Checkpoint:** promote to `production` only if it beats v0 on pooled walk-forward Brier **and** calibration. Otherwise keep v0 and record why.

### More prediction targets ([11](../11-prediction-targets.md))
- [ ] 🤖 QB: pass TDs, INTs (count models → expected value + P(≥1), P(≥2)), rushing yards.
- [ ] 🤖 Chance of scoring a TD for all skill players (calibrated classifier).
- [ ] 🤖 EDGE/DL: sacks (expected value + P(≥1)), QB hits.
- [ ] 🤖 CB/S: coverage targets, completions and yards allowed (from P01's PFR data); P(INT), P(pass defended).
- [ ] 🤖 Team stats: passing and rushing yards, sacks made and taken, takeaways.
- [ ] 🤖 Consistency layer: receptions ≤ targets; team passing yards ≈ sum of player receiving yards. Log the inconsistency metric.
- [ ] 🧑 **Rishi runs** the backtests for the new target families, watching calibration for the probability targets.
- [ ] ✋ **Checkpoint:** choose which new targets ship (each must beat its baseline); add them to the scoreboard and, where useful, the digest.

### Advanced graph
- [ ] 🤖 Q5 coaching connections (head coaches from nflverse; optional coordinator / coaching-tree seed CSV if Rishi wants to hand-curate it; see decisions log Q06).
- [ ] 🤖 Q6 former teammates on opposite sides; Q7 style matchups (mobile QBs, deep passers, play-action-heavy offenses via FTN); Q9 officiating crew tendencies.
- [ ] 🤖 Q10 GDS: per-team `THREW_TO` projection → PageRank / degree centrality (who the offense runs through, and how that changes with an absence). Node similarity / k-nearest-neighbors on usage vectors → `SIMILAR_TO` ("usage looks like X's breakout year").
- [ ] 🧑 **Rishi runs** the GDS algorithms interactively in Neo4j Browser first (projection → stream → write), as a learning step, before the agent wires them into the pipeline.
- [ ] 🤖 Golden tests for the new queries; insight ranking updated.

### Wrap-up
- [ ] 🤖 Model cards updated; the decisions log records any promotions.
- [ ] ✋ **Checkpoint:** close P08.

## Rishi-in-the-loop moments: what to look for

- **v0 vs v1 overlay:** the cumulative Brier curves for both, by season. A gain under ~0.002 Brier is probably noise. Look for it to hold across most seasons.
- **Probability targets:** reliability diagrams matter more than Brier score here. A well-calibrated 30% TD chance should score about 30% of the time.
- **GDS:** look at the PageRank results for a team you know well. Does the "center" of the passing network match what you see watching the games?

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| A v1 decision is made and recorded | A promoted model, or a documented reason to keep v0 |
| New targets on the scoreboard | `accuracy_scoreboard` rows for each shipped new target |
| At least one GDS-driven insight in a published digest | Digest + `graph_results.json` |
| Production never broken | Promotions happened between weeks; no failed weekly runs caused by P08 |

## Handoff to P10

- The final model lineup for the 2026 season. Everything versioned and documented.

## Pitfalls / notes

- Low-count events (INTs, sacks) need a lot of data, so pool across seasons and don't overfit. Calibration first.
- Training on actual weather but predicting from forecast weather causes a train/live mismatch. Consider adding noise to the training weather, or only using coarse buckets (windy / not windy).
