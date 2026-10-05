# 01: Project Brief

Owner: Rishi Jain · Last updated: 2026-09-30

## Vision

This project is meant to be **genuinely useful, not a resume piece**. It should produce something Rishi opens once or twice a week during the season and actually reads, not a training run that ends in a single metric.

NFL analytics fits because Rishi already follows the league closely, so the digest has a reader of one who really will read it. It also brings several of Rishi's interests into one system:

- classical ML and deep learning
- graph databases (Neo4j, Cypher, Graph Data Science), a deliberate learning goal
- LLM synthesis, with RAG and agents as later stretch goals
- experiment tracking (Weights & Biases / CoreWeave Forge)

It doubles as a serious applied-ML portfolio project because it wasn't built to be one.

## What the system produces

### Track 1: the weekly digest (primary deliverable)

A one-page report generated every week during the season, before the next slate starts (in practice, before Thursday Night Football). Sections are in order; full spec in [06-weekly-digest.md](06-weekly-digest.md).

1. **Report card:** how last week's calls turned out (win-probability accuracy, watch-list hits and misses).
2. **Game outlook:** a calibrated **win probability for every game**, plus the games where the model most disagrees with consensus.
3. **Team trend shifts:** which teams' underlying form is rising or falling, and why.
4. **Last week under the hood:** a look back at the previous week using tracking-based stats (receiver separation, rush yards over expected, time to throw, pressure).
5. **Players to watch:** players projected to beat their own baseline this week, with the reason (matchup, role change, injury ripple).
6. **Matchup / risk to watch:** one tactical angle found by the knowledge graph.
7. **Non-obvious insights:** one or two connections across several hops in the graph that a stats table wouldn't show.

Tone: *a smart friend who watches more film than you, texting you what actually matters.* Not an essay, not a stat dump.

### Track 2: Big Data Bowl movement model (research track)

A model trained from scratch on NFL Big Data Bowl 2026 tracking data. It predicts where players move while the ball is in the air, using tracking frames from before the throw. It is historical, has its own workstream, and isn't on the weekly critical path. Full spec in [07-track2-big-data-bowl.md](07-track2-big-data-bowl.md).

### How the two tracks connect

They are **connected but not intertwined**. Frame-level tracking for the current season isn't public, so the Track 2 model can't run on this week's games. Instead:

- Track 2 answers research questions about which movement measures (separation, closing speed, cushion) actually drive outcomes.
- Track 1 uses the **weekly Next Gen Stats summaries** for those same measures, which are public for the current season. They feed the "Last week under the hood" section and serve as player-model features.
- Historical movement profiles for players who appear in the Big Data Bowl data can become labeled, static attributes in the graph.

## Design principles

1. **Models predict, the LLM writes.** Every number comes from trained models, deterministic code or source data. The LLM does no prediction and no math, and automated checks enforce it.
2. **Win probabilities, not odds.** The digest shows win probabilities as percentages. It never shows spreads, totals, moneylines or betting language, and never gives wagering advice.
3. **Betting-market data is a tool, not an output.** Market lines can be used as model features and as the benchmark to beat. The digest can say the model is notably higher or lower on a team than consensus, but it never quotes a line.
4. **No fantasy scoring.** Rishi runs his fantasy team separately. No fantasy points, rankings or start/sit language.
5. **Long-term signal over noise.** Underlying form (EPA, success rate, usage) matters more than final scores or single-game results.
6. **Be honest about uncertainty.** When the evidence is thin, the digest says so. Every projection carries a confidence level.
7. **The graph must earn its place.** Neo4j is a deliberate learning goal, so graph queries should use real multi-hop relationships, not joins that pandas could do.
8. **Simple first, then deeper.** Ship a thin version that works end to end, then improve each layer. Every model has to beat a simple baseline before it ships.
9. **Reproducible.** Each weekly run can be reproduced from saved data snapshots, model versions and a run log.

## Non-goals

- Betting picks, odds, against-the-spread records or wagering advice in the output.
- Fantasy scoring, rankings or advice.
- Real-time or in-game updates. The cadence is weekly, plus an optional Saturday injury update.
- Running the Track 2 model on live current-season tracking data (not available).
- A public product or multi-user service.

## Constraints

| Constraint | Detail |
|---|---|
| Data budget | Free sources by default. Up to **$15 total** if a paid dataset is ever clearly worth it (none is planned). |
| LLM / W&B cost | Already covered; not a constraint. |
| Compute | Laptop for Track 1 and Track 2 baselines. Rishi's local GPU machine (or Colab) for Track 2 deep models. |
| Graph DB | Neo4j **running locally in Docker** (no cloud tier). |
| Storage | Code on F: (SSD); **all data on D:** (HDD, ~500 GB free) under one data root. See [02](02-system-architecture.md#storage-data-lives-on-d). |
| LLM | A real model since P04: `z-ai/glm-5.3-flash` through OpenRouter (D56), re-checked on the full P08 digest and closed in [P09](plans/P09-llm-connection.md) (D87). Any OpenRouter model, Claude included, is a config change; the **placeholder** template writer stays as the fallback. |
| Working style | **Rishi in the loop:** agents build; Rishi launches the first training runs, tuning and backtests and watches them live in W&B. See [plans/README](plans/README.md#working-model-rishi-in-the-loop). |
| Timing | The 2026 season is underway (Week 4 starts 2026-10-01). The first real digest should ship within about two weeks. |

## Success criteria

| Area | Criterion |
|---|---|
| Usefulness | Rishi reads the digest most weeks and finds at least one thing he didn't already know. |
| Reliability | The digest arrives before the first game of the next slate with no manual steps, for 4+ consecutive weeks. |
| Game model | The model-only version beats an Elo baseline on Brier score in walk-forward evaluation over past seasons. The market-informed version is roughly as good as the market's implied probabilities. |
| Player model | Beats each player's rolling average on MAE, and its watch list beats baseline more often than chance. |
| Truthfulness | 100% of numbers in published prose trace back to the payload (automated check), and any failure is flagged. |
| Track 2 | Beats the constant-velocity baseline on the competition metric (RMSE), and produces written findings that inform which Track 1 features to use. |
| Learning | The graph powers at least 3 recurring multi-hop insight queries, and at least one uses a Graph Data Science algorithm. |
