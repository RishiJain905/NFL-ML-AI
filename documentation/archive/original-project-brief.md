# NFL Analytics Engine - Project Brief

Sep 30, 2026 · @Rishi Jain

## Vision and goals

This project exists to be genuinely useful, not to pad a resume. Rishi builds machine learning and AI systems constantly, at work and independently, but wanted one project whose output he would actually open once or twice a week and use, not just a training run that ends in a metric.

The domain is NFL analytics. The reasoning: Rishi already follows the NFL closely for personal interest, so a weekly digest has a built-in audience of one who will actually read it. It also lets him combine several core interests in one system: classical ML and deep learning, agentic AI and RAG, graph databases (Neo4j and Cypher), and experiment tracking (Weights & Biases / CoreWeave Forge).

Success looks like a recurring, low-maintenance pipeline that refreshes weekly during the season, produces a short readable digest Rishi actually wants to check, and doubles as a serious applied ML portfolio piece, specifically because it was not built to be one.

## System overview: two tracks

The system splits cleanly into two tracks that share infrastructure (Neo4j graph, W&B tracking, LLM synthesis layer) but run on different cadences.

Track 1, the weekly digest, is the primary deliverable. It refreshes every week during the season, pulls current team and player stats plus news, runs them through trained classical ML models and the knowledge graph, and produces a short readable report.

Track 2, the Big Data Bowl model, is a deeper, slower-moving side project. It trains on the NFL's 2026 Big Data Bowl tracking dataset (player x/y position, speed, direction, per frame, pre- and post-pass) to predict player movement after the ball is thrown. Its outputs, like predicted separation or yards after catch, can eventually feed as a signal into Track 1's "player to watch" section, but it is scoped as its own model and its own workstream, not a dependency Track 1 needs on day one.

## Track 1: the weekly digest

Cadence: refreshes once a week during the NFL season (an overnight or early-morning job ahead of Rishi's coffee).

What it outputs, roughly one page, four sections:

1. Team trend shifts: which teams are trending up or down in form, not a single-game pick, backed by reasoning pulled from stats and recent news.
2. Players likely to perform well this week: for example, a receiver projected for a strong yardage game because of a favorable cornerback matchup, or a pass rusher likely to get multiple sacks because the opposing offensive line is struggling. These are framed as analysis, not betting lines, and explicitly not tied to fantasy scoring.
3. A matchup or risk to watch: a specific tactical angle the graph surfaces, for example a defense that historically struggles against mobile quarterbacks facing one this week.
4. One or two non-obvious insights: relationships the graph uncovers that a surface-level stats read would miss.

The tone is "a smart friend who watches more film than you, texting you what actually matters," not a ten-page essay and not a stat dump.

## Track 2: Big Data Bowl prediction model

Based on the NFL Big Data Bowl 2026 Prediction competition on Kaggle, which asks participants to predict player movement after the ball is thrown, using pre-pass tracking data (player x/y coordinates, speed, direction, per frame).

This is a from-scratch trained model, not a fine-tuned LLM, since it is structured numeric prediction: likely a gradient-boosted tree baseline first, then a sequence or graph neural network if Rishi wants to push further, given the spatial and temporal nature of player tracking data.

This track is free to download (the dataset is public on Kaggle) and runs on a one-time historical dataset rather than a weekly refresh, though it can optionally feed predicted metrics like separation or yards after catch into Track 1's player-to-watch section later on.

## Tech stack and architecture

&#91;embedded content: architecture · data sources to graph to LLM to report\]

Data layer: nflverse and ESPN's public API for weekly stats, schedules and scores; the Kaggle Big Data Bowl dataset for tracking data. All free.

Model layer: classical ML (gradient-boosted trees to start) for trend and matchup prediction; a separate model, likely starting with gradient-boosted trees and potentially extending to a sequence or graph neural network, for the Big Data Bowl movement prediction task. Both are trained and re-evaluated by Rishi, not the LLM.

Graph layer: Neo4j (AuraDB Free tier) with Cypher queries to model relationships like team-versus-team history, player-versus-defense matchup history, and injury ripple effects. This is where the non-obvious insights in the report come from.

Synthesis layer: an LLM's only job is turning model outputs plus graph query results into the readable weekly prose. It does no numeric prediction itself.

Experiment tracking: Weights & Biases (recently rebranded CoreWeave Forge) logs ROC, precision, recall and F1 for both model tracks as persistent, visual, longitudinal charts rather than terminal output, letting Rishi watch model performance evolve across the season.

## Cost boundaries

LLM inference costs and Weights & Biases (CoreWeave Forge) usage are already covered and are not a constraint on this project.

The only real budget line is data: the Big Data Bowl dataset is free on Kaggle, and nflverse and ESPN's public API cover weekly stats at no cost. If a better or more specialized dataset is ever worth purchasing, Rishi's ceiling is $15 total.

Neo4j AuraDB Free covers the graph layer at no cost for a project at this scale.

## Design principles and non-goals

Long-horizon over short-term signal: trend and form over time matter more than any single game call. This mirrors Rishi's own long-term investing mindset carried into a sports analytics context.

No betting lines: the report reasons about which teams and players are trending well and why, but never frames output as odds or wagering advice.

No fantasy scoring integration: Rishi already manages his fantasy team separately and does not want this system tied to it.

Models predict, the LLM writes: numeric prediction is never delegated to the LLM. Classical ML and trained models own the math; the LLM's only job is turning structured outputs and graph query results into readable prose.

Keep scope contained: Track 2 (Big Data Bowl) stays a bounded, separate workstream rather than something that balloons the weekly pipeline's complexity.

## Open questions and next steps for the planning agent

This brief is meant to be handed to an Opus agent to produce a phased implementation plan. Open questions worth resolving in that phase:

How granular should Track 1's graph schema be at launch, full league-wide relationships, or a smaller slice (Rishi's followed teams and players) that expands over time?

What is the right update cadence for the Neo4j graph itself, rebuilt weekly alongside the report, or incrementally updated?

Should the classical ML models for Track 1 be retrained weekly on a rolling window, or trained once per season and only re-evaluated weekly?

What does a minimal but real Phase 1 look like, likely: free data ingestion working end to end, a basic Neo4j schema, one trained trend model, and a bare-bones LLM-written report, before Track 2 or deeper graph relationships are added.

See the companion architecture and technical specification document for implementation-level detail.
