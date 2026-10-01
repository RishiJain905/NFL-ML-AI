# NFL Analytics Engine - Architecture and Technical Spec

Sep 30, 2026 · @Rishi Jain

Companion to the NFL Analytics Engine project brief; this document covers implementation-level detail for the planning agent to work from.

## Data pipeline

Three sources cover everything the system needs, all free.

nflverse (GitHub-hosted, R and Python packages available): play-by-play data, team and player weekly stats, schedules, rosters, injury reports. This is the backbone for Track 1's trend and matchup models.

ESPN public API (unofficial but widely used and stable): live scores, news headlines, and injury updates, useful for the LLM synthesis layer's contextual color and for catching news between nflverse's update cycles.

NFL Big Data Bowl 2026 dataset (Kaggle, one-time download): pre-pass player tracking frames, x and y coordinates, speed, direction, orientation. Used only by Track 2.

Ingestion cadence: nflverse and ESPN data refresh weekly, timed to run after the prior week's games are final and box scores are settled, typically early in the week ahead of the next slate. The Big Data Bowl dataset is downloaded once and versioned locally rather than re-pulled.

Orchestration: a simple scheduled job (cron, or a lightweight workflow tool like Prefect or a GitHub Action on a schedule) triggers, in order: data pull, feature refresh, Neo4j graph update, model inference, LLM synthesis, report output. Given the weekly cadence and small data volume, a heavyweight orchestrator is not needed, the phased plan should default to the simplest option that reliably runs once a week.

## Neo4j graph schema

&#91;embedded content: graph schema · core node and relationship types\]

Core node types: Team, Player, Game, Injury, Week, WeeklyStat, and TrendScore (the model's own output, written back into the graph so the LLM can query it alongside raw stats).

Core relationship types: PLAYS\_FOR (Player to Team), COMPETES\_IN (Team to Game), SUFFERED (Player to Injury), SCHEDULED\_IN (Game to Week), RECORDED\_IN (WeeklyStat to Game), FEEDS (Injury and WeeklyStat into a Game's context), and HAS\_TREND (Team or Player to TrendScore).

Example Cypher query for the "risk to watch" section: matching a Team competing in this Week's Game where the opposing Team's players have SUFFERED an Injury at a key position, surfaced alongside that team's historical performance without that position filled.

Example query for a non-obvious insight: matching Teams with a HAS\_TREND relationship showing a downward TrendScore over the last three weeks, filtered to those about to face a Team on an upward trend, a mismatch a simple stats table would not surface directly.

The schema is intentionally small to start. It can grow to include coaching staff, weather, and venue nodes later without breaking the core shape.

## Track 1 model spec

Model type: gradient-boosted trees (XGBoost or LightGBM) as the starting point for both the team trend model and the player performance model. Tabular, small-parameter, fast to train and retrain, well suited to weekly cadence.

Team trend model features: rolling performance metrics (points scored and allowed, yardage, turnover margin) over the last three to five weeks, strength of schedule adjustment, and home or away split. Output: a trend score and direction (up, down, stable) per team.

Player performance model features: recent per-game stats, opposing defense's relevant weakness (for example, yards allowed to the position), snap count trends, and injury status. Output: a ranked list of players likely to perform well that week, with the driving feature surfaced for the LLM to explain in prose.

Training and retraining cadence: an initial train on several seasons of historical nflverse data, then retrained or fine-tuned weekly as new weeks of data arrive, keeping a rolling window rather than retraining from scratch each time.

Evaluation metrics: precision and recall on "trending up" and "trending down" classifications, plus calibration checks on the player performance rankings against actual outcomes the following week. All logged to W&B / CoreWeave Forge.

## Track 2 model spec: Big Data Bowl

Task: predict where players move after the ball is thrown, using tracking data from before the throw. The exact column names, frame rates and scoring metric must be confirmed from the competition's Data and Evaluation tabs on Kaggle before any code is written. This spec only describes the shape of the problem.

Data shape: tracking data is a time series per play, one row per player per frame, with position (x, y), speed, acceleration, direction and orientation, plus play-level context such as the passer, target and coverage. Expect the data to be large relative to Track 1, so plan for chunked loading and a local Parquet copy rather than repeated CSV reads.

Feature engineering ideas:

- Relative features: each player's position and velocity relative to the ball, the target receiver and the nearest defenders
- Separation and leverage between receiver and closest defender at the moment of the throw
- Short history windows of the last several frames (velocity and acceleration changes) rather than a single snapshot
- Field-normalized coordinates, with play direction standardized so every play runs the same way

Candidate models, in order of ambition:

1. Baseline: constant-velocity extrapolation from the last pre-throw frame. Any real model has to beat this.
2. Gradient-boosted trees predicting displacement at each future step from engineered features. Fast to train and easy to debug.
3. Sequence model (GRU or a small Transformer encoder) over the pre-throw frames, predicting the future trajectory directly.
4. Graph neural network with players as nodes and proximity or coverage assignments as edges, since player movement is driven by interactions.

Training environment: the baseline and tree models run on a laptop. The sequence and graph models benefit from a GPU, for example Colab or Rishi's local fine-tuning machine.

Evaluation: use the competition's official metric, plus a hold-out split by game or week so plays from the same game never appear in both train and validation. Log everything to W&B so the models can be compared against the baseline on one chart.

Integration with Track 1 (later, optional): derive summary signals such as expected separation or yards after catch per receiver, and write them into the graph as additional properties the digest can reference.

## LLM synthesis layer

Role: the LLM turns structured results into the weekly digest. It does no prediction and no arithmetic. Every number in the report must come from the inputs it is handed.

Inputs, assembled as one structured JSON payload per run:

- Team trend table: each team, trend direction, score and the top drivers from the model
- Player watch list: ranked players, model probability, the driving feature, and the opposing matchup context
- Graph results: the output rows of the Cypher queries behind the matchup risk and non-obvious insight sections
- Recent news snippets from ESPN, limited to items that touch the teams and players in the payload

Prompt structure:

1. System prompt: persona (a smart friend who watches more film than you), tone rules, length cap, and hard constraints
2. Payload: the JSON above, clearly delimited
3. Output spec: the four digest sections in a fixed order, each with a word budget

Hard constraints to put in the system prompt: use only numbers present in the payload, never mention betting lines, odds or spreads, never reference fantasy scoring, and say so plainly when the evidence is thin instead of overstating confidence.

Output format: one page of Markdown, roughly 300 to 450 words, with these sections: Team trend shifts, Players to watch, Matchup to watch, and Non-obvious insights. Save it to disk with a date-stamped filename, and optionally deliver it by email or a notification.

Quality check: add a small automated step that verifies every number in the report appears in the input payload, and flag the run if one does not. This catches the most common failure, an LLM inventing a plausible statistic.

Model choice is a configuration setting rather than a design decision, so the synthesis model can be swapped without touching the rest of the pipeline.

## Experiment tracking

Tool: Weights & Biases (now CoreWeave Forge), which is already set up and paid for. The goal is a persistent, visual record of how every model behaves over the season, instead of numbers that scroll past in a terminal.

Organization: one W&B project for this system, with runs grouped by purpose.

- Group track1-team-trend: the team trend model
- Group track1-player: the player performance model
- Group track2-bdb: Big Data Bowl experiments
- Group weekly-pipeline: the production weekly run, kept separate from experiments so research runs never clutter the record of what actually shipped

What each training run logs:

- Config: model type, hyperparameters, feature list, training window and the dataset version
- Metrics: ROC AUC, precision, recall, F1 and Brier score, plus the naive baseline's numbers for comparison
- Curves and plots: ROC curve, precision-recall curve, calibration plot and feature importance
- Artifacts: the trained model file and a snapshot reference to the training data, so any past result can be reproduced

What each weekly pipeline run logs: data freshness (latest week ingested), row counts, model versions used, graph update counts, report word count and the result of the number-check step from the synthesis layer.

Longitudinal view: log one evaluation row per week as the season progresses, so a dashboard shows whether the models are getting better or drifting. Drift in a weekly metric is the signal to retrain or revisit features.

Track 2 specifics: log the validation metric per epoch, example predicted versus actual trajectories as images, and the gap to the constant-velocity baseline.

## Suggested phased build order

This is a starting proposal for the planning agent to challenge and refine. The principle is to get a thin, working end-to-end digest first, then deepen each layer, so there is something real to read every week from early on.

1. Phase 0, setup: repository, environment, secrets handling, a W&B project, and a Neo4j AuraDB Free instance. Exit criterion: each service can be reached from a test script.
2. Phase 1, data ingestion: pull nflverse and ESPN data into local Parquet files with a repeatable script. Exit criterion: one command refreshes all data for the current week.
3. Phase 2, baseline models: build features, train the naive baseline and the first gradient-boosted models for team trend and player performance with time-based validation. Exit criterion: the model beats the baseline on walk-forward evaluation and the runs show up in W&B.
4. Phase 3, graph layer: load teams, players, games, injuries and trend scores into Neo4j, then write the Cypher queries behind the matchup risk and non-obvious insight sections. Exit criterion: the queries return sensible results for a known past week.
5. Phase 4, synthesis and first digest: assemble the JSON payload, write the prompt, add the number-check step and generate a full digest for a past week. Exit criterion: a one-page report Rishi would genuinely want to read.
6. Phase 5, automation: schedule the weekly run, add logging and failure alerts. Exit criterion: the digest arrives without manual steps for two consecutive weeks.
7. Phase 6, Track 2: download the Big Data Bowl data, build the baseline, tree model and then the sequence or graph model. This phase can run in parallel with Phase 5 once Phase 2 is done.
8. Phase 7, integration: feed selected Track 2 signals into the graph and the player watch list, and re-evaluate Track 1 with them included.

Biggest risks to plan around: data leakage in validation (use time-based splits only), the LLM inventing statistics (the number-check step), and scope creep in Track 2 (keep it independent until Track 1 is shipping weekly).
