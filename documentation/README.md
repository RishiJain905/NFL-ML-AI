# NFL Analytics Engine: Documentation

A personal ML system that produces a weekly NFL digest during the season. It shows game win probabilities and predicted scores, team trends, a look back at last week's tracking stats, projections for offensive and defensive players, and insights from a knowledge graph. It tracks its own accuracy every week. A separate research track models player movement on the NFL Big Data Bowl tracking data.

Last updated: 2026-10-08 (three new feature tracks planned: Live decisions, Play calling, Ask the Engine; D107–D112). Before that 2026-10-06 (CR03: the control room's MLOps tab and season pages; the control-room track is complete).

## Start here

- **Building something?** Go to **[plans/README.md](plans/README.md)** (how the phases work) and **[plans/PROGRESS.md](plans/PROGRESS.md)** (where the build currently stands).
- **Want to understand the design?** Read the docs below in order.
- **Running the weekly digest?** Use the **[runbook](runbook.md)**: the weekly commands, exit codes, what each alert means, how to rerun, resume, roll back, simulate or rehearse a week, the playoffs, the end of the season and the pre-season checklist.

## Control room (the local web app)

[`control-room/`](control-room/README.md) is its own track, CR00–CR03 (D94–D106). **The track is complete** (`uv run nfl app`, the app shell, both themes, the safety rules; every week tab from the real files with the three pipeline views; the Run button with pre-flight, the live views, resume and the Saturday update; the MLOps tab (Health · W&B runs · Artifacts) and the season pages (Scorecard, Teams & rankings, Models, Alerts, Health), with W&B read on the server and cached): see the [control room guide](guides/control-room.md). It's a localhost web app to run the week with one button, watch every step live, read the digest and results, and see the MLOps side (run health, W&B runs and charts, artifacts) and season views. The folder holds the spec, the four phase files and the source of the [approved mockup](https://claude.ai/artifact/1z7AapJzJwKusNtWzXYfQw) (its visual spec).

## New feature tracks (planned 2026-10-08)

Three tracks added after the control room (D107–D112), each with a spec and phase files in its own folder. They follow one rule: **new models only**. The production models, the digest, the weekly graph and the weekly run stay exactly as they are (D107). New screens go in a new **Explore** sidebar group (general pages) or new week tabs after Graph (weekly views) (D108).

| Track | Folder | What it adds |
|---|---|---|
| Live decisions (LD00–LD03; **LD00 done**: the six models, the engine, `nfl live train / backtest / call`; [guide](guides/live-decisions.md)) | [`live-decisions/`](live-decisions/README.md) | A live 3rd- and 4th-down bot: pick a game in progress, press **Check this play**, get go / kick / punt with win probabilities, a confidence label and "is this team good at this?"; a weekly decision review. New win-probability, yards-gained, field-goal, punt and pass models; ESPN's live feed |
| Play calling (PC00–PC03) | [`play-calling/`](play-calling/README.md) | Team tendency pages (offense and defense, drawn like a playbook), a weekly forecast of each team's calls against its next opponent, and a play browser with reconstructed play animations |
| Ask the Engine (AE00–AE04) | [`ask-the-engine/`](ask-the-engine/README.md) | Questions in plain English answered from the graph or the curated data, with the query shown; a second **history graph** with every play since 2018; "six degrees" connections; graph experiments; a shortest-path benchmark |

## Design documents

| # | Document | What it covers |
|---|----------|----------------|
| 01 | [Project brief](01-project-brief.md) | Why the project exists, what it produces, principles, non-goals, success criteria |
| 02 | [System architecture](02-system-architecture.md) | Components, tech stack, repo layout, **data on D:**, weekly schedule, orchestration, failure handling |
| 03 | [Data sources](03-data-sources.md) | nflverse + ESPN + extra free sources, what each is for, when it's available, gaps and what replaces them, storage and quality checks |
| 04 | [Track 1 models](04-track1-models.md) | Team ratings and trend, game model, player model, leakage rules, weekly retraining with current-season weighting |
| 05 | [Knowledge graph](05-knowledge-graph.md) | Neo4j in Docker (files on D:), full schema, how it's built, the query library, GDS |
| 06 | [Weekly digest](06-weekly-digest.md) | Digest sections, JSON payload, LLM provider interface (GLM via OpenRouter, placeholder fallback), checks, report card, delivery |
| 07 | [Track 2: Big Data Bowl](07-track2-big-data-bowl.md) | The movement-prediction task, model ladder, evaluation, and how it connects to Track 1 |
| 08 | [Experiment tracking](08-experiment-tracking.md) | W&B project layout, live curves, season scorecard, accuracy scoreboard, drift signals |
| 09 | [Build roadmap](09-build-roadmap.md) | One-screen overview of the phases, rough targets, risks |
| 10 | [Decisions log](10-decisions-log.md) | Every decision so far, why, when to revisit, open questions |
| 11 | [Prediction targets](11-prediction-targets.md) | Every game, team and player (offense + defense) target, the baselines, realistic ceilings, the accuracy scoreboard |

## Guides: how each part works, in plain language

One guide per major technology or component, written for a reader who's new to it: what it is, how it's wired here, what it produces, how to look at it, how to change it.

| Guide | What it explains |
|---|---|
| [Knowledge graph (Neo4j)](guides/knowledge-graph.md) | Neo4j in two minutes, what our graph holds, a Neo4j Browser walkthrough, each Cypher query in the library, how graph findings reach the digest (P05; P08 queries Q5-Q7, Q9) |
| [Graph Data Science](guides/graph-data-science.md) | GDS primer (projections, PageRank, degree, KNN vs node similarity, stream / write / mutate), the two weekly GDS jobs, and a Neo4j Browser walkthrough with real results (P08) |
| [Weights & Biases](guides/weights-and-biases.md) | Every W&B run, chart, table and artifact, step by step through the weekly run and the research runs, what to check each week, and the **2026 Season Dashboard** chart by chart (what each shows, whether higher or lower is better, normal ranges) |
| [The LLM digest writer](guides/llm-digest-writer.md) | What the LLM does and doesn't do, the prompt, the checks it must pass, cost and latency, how to switch the model or provider safely, and the P09 re-check on today's digest |
| [Player projections](guides/player-projections.md) | The player model (P06): LightGBM, quantile ranges, count distributions, SHAP drivers in a few minutes; how projections become the watch list, tough spots, the look-back and the accuracy scoreboard; files, W&B runs, commands, how to change it safely; P08: chances of a TD / sack / interception (calibrated), QB TDs and interceptions, CB/S coverage |
| [Season operations](guides/season-operations.md) | The season beyond one week (P10): the 2026–27 calendar, the Tuesday season log, **rehearsals** (the live steps on any week in a sandbox), what playoff weeks do, the season review, roster churn, the pre-season checklist, Big Data Bowl 2027 |
| [The control room](guides/control-room.md) | The local web app (CR00–CR03, complete): starting it (`uv run nfl app`), what each part of the screen shows and the week-status rules, each week tab and the files it reads (CR01), the three pipeline views, running the week from it (pre-flight, Run, the live views, resume, Saturday, rehearsal mode; CR02), the MLOps tab and the season pages and what each reads (CR03), how the app reads W&B (local first, the cache, working offline), how a run is launched, themes and modes, how it's wired (FastAPI + React), the safety rules in plain words, troubleshooting, how to extend it safely |
| [Live decisions](guides/live-decisions.md) | The 3rd- and 4th-down bot (LD00+): what a 4th-down model is, the six models, how a call is computed (worked through on a real 2025 4th down), the confidence labels and the bootstrap gate, the 3rd-down check, commands, files, limits |
| [Weekly operations](guides/weekly-operations.md) | How the weekly run is operated (P07, manual-first): the calendar (which week, the deadline, special weeks), `nfl weekly run --auto` and its exit codes, the lock, Neo4j start-up, the run records and W&B pipeline run, alerts, drift checks and their 2019–2025 replay, the season dashboard, the Saturday injury update, time-travel simulations |

## Weekly graph queries

[`queries/`](queries/README.md) holds one file per live week (`<season>-week<NN>-queries.md`): Neo4j Browser queries that trace that week's digest back to the graph, the findings it didn't use, and anything else worth a look, each with the result it returned. Start with [2026 week 4](queries/2026-week04-queries.md).

## Model cards

`model_cards/` holds one card per trained or tuned model: what it is, how it was tuned, results vs baselines, how to read its W&B charts, known limits.

| Card | Phase |
|---|---|
| [Team ratings, Elo, trend](model_cards/team_ratings.md) | P02 |
| [Game model v0](model_cards/game-model-v0.md) | P03 |
| [Player model v1](model_cards/player-model-v1.md): overview of all 11 target models, plus one card per target family: [QB](model_cards/player-qb.md), [RB](model_cards/player-rb.md), [WR/TE](model_cards/player-wrte.md), [defense](model_cards/player-defense.md) | P06 |
| [Player model, P08 targets](model_cards/player-p08.md): chance of a touchdown, sacks, interceptions, QB TDs and rushing, QB hits, coverage stats for CBs and safeties | P08 |
| [Game model v1](model_cards/game-model-v1.md): LightGBM with injury load, weather and a trailing home edge; evaluated and **not promoted** (v0 stays in production, D79) | P08 |
| [Team stat totals + consistency layer](model_cards/team-stats-v1.md): team passing / rushing yards, sacks made / taken (takeaways tried, not shipped); receptions vs targets and receivers vs QB vs team yards | P08 |
| [Live-decision models](model_cards/live-decision-models.md): the 3rd / 4th-down bot's win probability, yards gained, field goal, punt, kickoff and pass models and the decision engine; leave-one-season-out backtest 2014–2025 | LD00 |

`plans/` is the phase-by-phase build plan. `archive/` holds the original starter docs, for reference only.

New to the football terms? **EPA, success rate and net rating** are defined at the top of [04 → A](04-track1-models.md#key-terms).

## One-paragraph summary

nflverse data and other free sources (ESPN, Next Gen Stats, PFR, Open-Meteo, odds) are stored as dated Parquet snapshots on the D: drive. Opponent-adjusted EPA team ratings feed a game model that outputs a calibrated win probability and a predicted score for every game. Player models project the main stats for offense and defense against each player's own baseline, with uncertainty ranges. A local Neo4j graph, rebuilt each week, finds connections that take several hops to see. An LLM (a placeholder until one is connected) turns a structured payload into prose. Automated checks confirm every number and claim it writes comes from the payload. Every week the digest grades its own previous calls, and an accuracy scoreboard in W&B tracks how good every prediction is. Rishi launches the key training runs and watches them live. Track 2 trains movement models on Big Data Bowl 2026 tracking data. It connects to Track 1 by showing which Next Gen Stats measures actually matter.
