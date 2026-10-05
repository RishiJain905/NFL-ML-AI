# NFL Analytics Engine

A personal ML system that produces a **weekly NFL digest** during the season: calibrated win probabilities and predicted scores, team trends, a tracking-stats look back at last week, offense and defense player projections, and multi-hop insights from a Neo4j knowledge graph. It grades its own accuracy every week in Weights & Biases. A separate research track models player movement on NFL Big Data Bowl tracking data.

- **Design docs:** [`documentation/`](documentation/README.md)
- **Build plan and progress:** [`documentation/plans/`](documentation/plans/README.md) · [`PROGRESS.md`](documentation/plans/PROGRESS.md)
- **Agent rules (incl. security):** [`CLAUDE.md`](CLAUDE.md)

## Setup

Requirements: Windows with [uv](https://docs.astral.sh/uv/), Docker Desktop (WSL2), and the D: data drive connected.

1. **Env file.** Copy `.env.example`, rename the copy to drop `.example`, and fill in `NFL_DATA_ROOT` (default `D:/nfl-ml-data`), `NEO4J_PASSWORD` (8+ characters) and `WANDB_API_KEY`. Format: `NAME=value`, no spaces around `=`, forward slashes in paths.
2. **Python environment.** Run `uv sync`. Python 3.12 is installed automatically. The uv package cache lives on D: (`[tool.uv] cache-dir` in `pyproject.toml`).
3. **Neo4j.** Run `docker compose up -d`. The first start downloads the APOC and GDS plugins. The Browser is at http://localhost:7474 (user `neo4j`), and database files live in `D:/nfl-ml-data/neo4j/`.
4. **Check everything.** Run `uv run nfl doctor`. It reports data drive, env vars (set / not set, never values), Neo4j + plugins, W&B, nflreadpy and the LLM provider.
5. **Optional W&B smoke test.** Run `uv run nfl wandb-smoke` and watch a live curve appear in the `nfl-analytics-engine` project.

## Where things live

| What | Where |
|---|---|
| Code, config, docs | this repo (F:) |
| All data: raw snapshots, curated tables, runs, reports, Neo4j files, W&B files, caches | `NFL_DATA_ROOT` on D: (see [documentation/02 → Storage](documentation/02-system-architecture.md#storage-data-lives-on-d)) |

Tool caches are pointed at the data root automatically: `WANDB_DIR` → `D:/nfl-ml-data/wandb`, nflreadpy → `D:/nfl-ml-data/cache/nflreadpy`.

## Commands

```
uv run nfl --help                  # all commands
uv run nfl doctor                  # health check
uv run nfl wandb-smoke             # W&B smoke test

uv run nfl ingest                  # pull every source into dated snapshots on D: (P01)
uv run nfl curate                  # curated tables + DuckDB views + quality checks (P01)
uv run nfl data-status             # newest week per source, row counts, join rates (P01)
uv run nfl ingest --check-ready --week N   # are week N's games final and in play-by-play?

uv run nfl ratings build           # team_ratings / team_elo / team_trends tables (P02)
uv run nfl ratings tune            # W&B grid sweep over the rating parameters (P02)
uv run nfl ratings eval            # walk-forward check vs baselines + Elo Brier (P02)
uv run nfl ratings validate-trend  # does the trend predict beyond the rating? (P02)

uv run nfl features game           # features/game_features: one row per game, as of its week (P03)
uv run nfl backtest game --variant model-only --seasons 2018-2025   # walk-forward vs Elo/market, live W&B (P03)
uv run nfl backtest game --variant market --seasons 2018-2025
uv run nfl backtest game-weights   # W&B sweep over the current-season sample weight (P03)
uv run nfl train game --season 2026 --week N   # weekly fit -> runs/<season>/week<NN>/predictions_games.parquet (P03)
uv run nfl tune game                            # game model v1: W&B grid sweep scored on 2013-2017 only (P08)
uv run nfl backtest game --version v1 --variant model-only   # v1 (LightGBM) vs v0 on the same games (P08; not promoted, D79)

uv run nfl features player          # features/player_features: one row per player-game, as of its week (P06)
uv run nfl backtest player --target rec_yds   # walk-forward 2019-2025 vs the rolling baseline, live W&B (P06)
uv run nfl backtest player --target all       # every target x position group (11 models)
uv run nfl tune player --target rec_yds       # W&B grid sweep, scored on 2017-2018 only (P06)
uv run nfl train player --season 2026 --week N   # weekly refit -> runs/<season>/week<NN>/predictions_players.parquet
uv run nfl scoreboard --season 2026 --week N     # score a played week's projections -> accuracy scoreboard
uv run nfl backtest player --target td --group RB   # P08 targets too (TDs, INTs, sacks, CB/S coverage ...)
uv run nfl backtest team --target all            # team stat totals vs their baselines (P08)
uv run nfl tune team --target pass_yds           # team grid, scored on 2017-2018 only (P08)
uv run nfl train team --season 2026 --week N     # shipped team targets -> predictions_teams.parquet (the weekly player step does this)
uv run nfl consistency                           # receptions vs targets, receivers vs QB vs team yards on the backtests (P08)

uv run nfl weekly run --auto                    # THE weekly command (P07): calendar picks the week; exit 3 = not ready yet
uv run nfl weekly run --auto --dry-run          # show the calendar plan, run nothing
uv run nfl weekly status                        # calendar now, step states, lock, recent runs
uv run nfl weekly injury-update --auto          # Saturday: re-pull injuries / lines, addendum only if material
uv run nfl weekly run --auto --as-of 2025-11-04T10:00   # simulate a past week (ET; writes only backtest folders)
uv run nfl weekly run --season 2026 --week N   # ingest -> ready -> curate -> ratings -> game -> graph -> player -> digest
uv run nfl weekly run --season 2026 --week N --from-step digest   # resume from a failed step
uv run nfl dashboard build --season 2026        # create / update the W&B "2026 Season Dashboard" report (P07)
# runbook: documentation/runbook.md
uv run nfl digest --season 2026 --week N       # payload -> LLM -> checks -> reports/<season>/week<NN>-digest.md (P04)
uv run nfl digest --season 2025 --weeks 8-9 --backtest   # past weeks as if live on their Tuesday (P04)

uv run nfl graph build --season 2026 --week N  # rebuild Neo4j as of week N + query library -> graph_results.json (P05)
uv run nfl graph build --season 2025 --week 8 --backtest   # the graph a past week's Tuesday saw
uv run nfl graph query q2_injury_ripple --season 2026 --week N   # one library query, read-only
uv run nfl graph coaching-seed --refresh   # rebuild config/coaching_seed.csv from Wikipedia (each preseason; P08, D86)
# Neo4j Browser: http://localhost:7474 (queries to try: .claude/skills/neo4j-graph/SKILL.md)

uv run pytest                      # tests (integration tests excluded by default)
uv run pytest -m integration       # Neo4j golden / load / performance tests (~10 min; rebuilds the graph)
uv run ruff check .                # lint
```
