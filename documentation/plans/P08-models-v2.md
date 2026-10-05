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
- [x] 🤖 Full features: snap-weighted injury load by position group, Open-Meteo forecast weather (training uses actual game weather; note the mismatch), travel, ESPN FPI / QBR if collected, monotonic constraints. *(`features/game_extra.py`: Tuesday-view injury load, coarse weather buckets, trailing home edge; FPI / QBR not usable: no history, D50.)*
- [x] 🤖 LightGBM win / margin / points models + calibration; backtest with the P03 harness, logged next to v0. *(`models/game_model_v1.py`: boosted from v0's ridge, monotone; v0 carried on the same rows in every v1 run.)*
- [x] 🧑 **Rishi runs** the v1 backtests and the tuning sweep. *(Waived for P08; agent ran sweep `47vq67gw` and backtests `5jcz8pn4`, `7abl18yf`, calibration checks `n642qpdo`, `i0dqfmvy`.)*
- [x] ✋ **Checkpoint:** promote to `production` only if it beats v0 on pooled walk-forward Brier **and** calibration. Otherwise keep v0 and record why. *(Waived; decided by the rule written before the reported runs: **v0 kept**, D79. v1 −0.0005 Brier, 95% −0.0012 to +0.0002, worse ECE.)*

### More prediction targets ([11](../11-prediction-targets.md))
- [x] 🤖 QB: pass TDs, INTs (count models → expected value + P(≥1), P(≥2)), rushing yards. *(sonnet-xhigh; `pass_tds-qb`, `ints-qb`, `rush_yds-qb`.)*
- [x] 🤖 Chance of scoring a TD for all skill players (calibrated classifier). *(`td-rb`, `td-wrte`: RB and WR/TE; QBs' rushing TDs aren't a target.)*
- [x] 🤖 EDGE/DL: sacks (expected value + P(≥1)), QB hits. *(`sacks-edge`, half sacks count, D82; `qb_hits-edge`.)*
- [x] 🤖 CB/S: coverage targets, completions and yards allowed (from P01's PFR data); P(INT), P(pass defended). *(New CB/S pool; `features/player_coverage.py`; `cov_tgt-cbs`, `cov_cmp-cbs`, `cov_yds-cbs`, `int-cbs`, `pd-cbs`.)*
- [x] 🤖 Team stats: passing and rushing yards, sacks made and taken, takeaways. *(sonnet-xhigh; `models/team_runs.py`; D80: 4 shipped, takeaways not.)*
- [x] 🤖 Consistency layer: receptions ≤ targets; team passing yards ≈ sum of player receiving yards. Log the inconsistency metric. *(`models/consistency.py`; D81: the receptions clamp is on, the yards gaps are logged to `consistency.json` and W&B, the yards adjustment stays off: it hurt the WR/TE watch picks.)*
- [x] 🧑 **Rishi runs** the backtests for the new target families, watching calibration for the probability targets. *(Waived; the agents ran them: 12 player + 5 team backtests with no-market variants, reliability diagrams per probability target; run ids in PROGRESS and the cards.)*
- [x] ✋ **Checkpoint:** choose which new targets ship (each must beat its baseline); add them to the scoreboard and, where useful, the digest. *(Waived; by the pre-registered rules: all 12 player targets (D82) and 4 of 5 team targets (D80) ship; on the scoreboard; the digest's watch tables show TD and sack chances.)*

### Advanced graph
- [x] 🤖 Q5 coaching connections (head coaches from nflverse; optional coordinator / coaching-tree seed CSV if Rishi wants to hand-curate it; see decisions log Q06). *(opus-high; head-coach reunions from P05 + `q5_coaching_tree` on the coaching seed; loader built in P08 (D83), then the seed itself built from Wikipedia after the close at Rishi's request (`nfl graph coaching-seed`, 1,684 coordinator rows 2006–2026, D86; Q06 answered).)*
- [x] 🤖 Q6 former teammates on opposite sides; Q7 style matchups (mobile QBs, deep passers, play-action-heavy offenses via FTN); Q9 officiating crew tendencies. *(Q9: week-W crews only live and only when in the data, D83.)*
- [x] 🤖 Q10 GDS: per-team `THREW_TO` projection → PageRank / degree centrality (who the offense runs through, and how that changes with an absence). Node similarity / k-nearest-neighbors on usage vectors → `SIMILAR_TO` ("usage looks like X's breakout year"). *(`graph/gds.py`; KNN, not Jaccard similarity, D84.)*
- [x] 🧑 **Rishi runs** the GDS algorithms interactively in Neo4j Browser first (projection → stream → write), as a learning step, before the agent wires them into the pipeline. *(Waived; the agent ran them and wrote the step-by-step Browser walkthrough with real results for Rishi to repeat: `documentation/guides/graph-data-science.md`.)*
- [x] 🤖 Golden tests for the new queries; insight ranking updated. *(38 unit tests + golden weeks in the integration suite, 11/11 passed.)*

### Wrap-up
- [x] 🤖 Model cards updated; the decisions log records any promotions. *(New cards: game model v1, P08 player targets, team stats; D78–D84; no promotion: v0 stays, D79.)*
- [x] ✋ **Checkpoint:** close P08. *(Waived: Rishi's kickoff asked to mark the phase complete.)*

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

## As built: deviations from the task list

Waivers (Rishi, at the kickoff): every 🧑 step run by the agents, both ✋ decisions made by rules written down before the reported runs, and the ✋ close waived ("mark the phase complete").

- **Game model v1 not promoted** (D79): v1 −0.0005 Brier (95% −0.0012 to +0.0002) with worse calibration; v0 stays (`game_model.version: v0`). v1 boosts from v0's ridge (trees from scratch lost by 0.004–0.009 on 2013–2017).
- **Injury load is a Tuesday view** (regulars missing from the team's last game), not the week's injury report: the live game run is on Tuesday (D78). **ESPN FPI / QBR** aren't features: only current snapshots exist (D50). **Weather** uses coarse buckets (the pitfall).
- **Chance of a TD** for RBs and WR/TE only (QB rushing TDs aren't a target).
- **Team takeaways** didn't beat the league average and don't ship (D80).
- **Consistency:** the yards gaps are logged, not adjusted: the adjustment helped MAE but hurt the WR/TE watch picks (D81).
- **Team totals and the consistency layer run inside the weekly `player` step** (fail-soft), not as a new pipeline step.
- **Q9 officiating** only fires in a live run whose data has the week's crew (crews arrive weeks late; D83). **The coaching seed** is built from Wikipedia, not hand-curated (D86, Q06); coordinators only.
- **GDS exit criterion** shown on 2025 weeks 13 / 14 backtest digests (Rishi chose this at the kickoff); the first live GDS item is expected from 2026 week 5.

## Exit criteria: verified

| Criterion | Evidence |
|---|---|
| A v1 decision is made and recorded | D79 (keep v0, with the pre-registered rule and its numbers); [v1 card](../model_cards/game-model-v1.md) |
| New targets on the scoreboard | `runs/backtests/player/scoreboard.parquet`: 23 targets × 124 weeks (the 12 new ones with Brier / ECE where they apply); `runs/backtests/team/scoreboard.parquet`: 5 × 124. The 2026 season file gets them from the week-5 run (walk-forward rows for weeks 1–4, live ones from week 6) |
| At least one GDS-driven insight in a published digest | 2025 week 13 backtest digest (W&B `9fmebmll`, every check passed): the Ja'Marr Chase passing-network item; week 14 (`n5ky2d59`): a usage comparison |
| Production never broken | Nothing was promoted mid-week; live files untouched (week-4 `predictions_*` timestamps unchanged; the live graph restored with `graph_results.json` byte-identical); a scratch rehearsal of the weekly path (game → player with team + consistency → digest) reproduced the published week-4 game and player numbers exactly with the P06 targets and ran clean with all 23 + 4 targets; full test suite and ruff clean |
