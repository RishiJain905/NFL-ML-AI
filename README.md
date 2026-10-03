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
uv run nfl --help        # all commands (most are placeholders until their phase)
uv run nfl doctor        # health check
uv run nfl wandb-smoke   # W&B smoke test
uv run pytest            # tests (integration tests excluded by default)
uv run ruff check .      # lint
```
