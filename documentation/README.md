# NFL Analytics Engine: Documentation

A personal ML system that produces a weekly NFL digest during the season. It shows game win probabilities and predicted scores, team trends, a look back at last week's tracking stats, projections for offensive and defensive players, and insights from a knowledge graph. It tracks its own accuracy every week. A separate research track models player movement on the NFL Big Data Bowl tracking data.

Last updated: 2026-10-01 (start of 2026 Week 4).

## Start here

- **Building something?** Go to **[plans/README.md](plans/README.md)** (how the phases work) and **[plans/PROGRESS.md](plans/PROGRESS.md)** (where the build currently stands).
- **Want to understand the design?** Read the docs below in order.

## Design documents

| # | Document | What it covers |
|---|----------|----------------|
| 01 | [Project brief](01-project-brief.md) | Why the project exists, what it produces, principles, non-goals, success criteria |
| 02 | [System architecture](02-system-architecture.md) | Components, tech stack, repo layout, **data on D:**, weekly schedule, orchestration, failure handling |
| 03 | [Data sources](03-data-sources.md) | nflverse + ESPN + extra free sources, what each is for, when it's available, gaps and what replaces them, storage and quality checks |
| 04 | [Track 1 models](04-track1-models.md) | Team ratings and trend, game model, player model, leakage rules, weekly retraining with current-season weighting |
| 05 | [Knowledge graph](05-knowledge-graph.md) | Neo4j in Docker (files on D:), full schema, how it's built, the query library, GDS |
| 06 | [Weekly digest](06-weekly-digest.md) | Digest sections, JSON payload, LLM provider interface (placeholder first), checks, report card, delivery |
| 07 | [Track 2: Big Data Bowl](07-track2-big-data-bowl.md) | The movement-prediction task, model ladder, evaluation, and how it connects to Track 1 |
| 08 | [Experiment tracking](08-experiment-tracking.md) | W&B project layout, live curves, season scorecard, accuracy scoreboard, drift signals |
| 09 | [Build roadmap](09-build-roadmap.md) | One-screen overview of the phases, rough targets, risks |
| 10 | [Decisions log](10-decisions-log.md) | Every decision so far, why, when to revisit, open questions |
| 11 | [Prediction targets](11-prediction-targets.md) | Every game, team and player (offense + defense) target, the baselines, realistic ceilings, the accuracy scoreboard |

`plans/` is the phase-by-phase build plan. `model_cards/` holds one card per trained or tuned model: what it is, how it was tuned, results vs baselines, known limits. Start with [team ratings](model_cards/team_ratings.md) (P02). `archive/` holds the original starter docs, for reference only.

New to the football terms? **EPA, success rate and net rating** are defined at the top of [04 → A](04-track1-models.md#key-terms).

## One-paragraph summary

nflverse data and other free sources (ESPN, Next Gen Stats, PFR, Open-Meteo, odds) are stored as dated Parquet snapshots on the D: drive. Opponent-adjusted EPA team ratings feed a game model that outputs a calibrated win probability and a predicted score for every game. Player models project the main stats for offense and defense against each player's own baseline, with uncertainty ranges. A local Neo4j graph, rebuilt each week, finds connections that take several hops to see. An LLM (a placeholder until one is connected) turns a structured payload into prose. Automated checks confirm every number and claim it writes comes from the payload. Every week the digest grades its own previous calls, and an accuracy scoreboard in W&B tracks how good every prediction is. Rishi launches the key training runs and watches them live. Track 2 trains movement models on Big Data Bowl 2026 tracking data. It connects to Track 1 by showing which Next Gen Stats measures actually matter.
