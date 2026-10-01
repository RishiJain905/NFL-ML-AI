# P00: Foundations

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** nothing
- **Unlocks:** P01 (and T00)
- **Read first:** [02 System architecture](../02-system-architecture.md) (repo layout, storage, secrets), [05 Knowledge graph](../05-knowledge-graph.md) (Docker runtime section), [08 Experiment tracking](../08-experiment-tracking.md) (project layout), [plans/README](README.md)

## Goal

An empty but fully working skeleton: the project installs, all data paths point to D:, Neo4j runs in Docker with its files on D:, W&B logs, and one health-check command confirms everything is connected. No NFL data, models or digest yet.

## Scope

- **In:** repo structure, Python environment, config and secrets templates, data-root handling, Docker Neo4j, W&B project, CLI skeleton, test and lint setup, `.gitignore`.
- **Out:** any data download (P01), any modeling.

## Prerequisites from Rishi

- Docker Desktop installed and running (WSL2 backend).
- The D: drive connected; it's fine for the agent to create `D:\nfl-ml-data`.
- `.env` filled in by Rishi (the agent never needs to see the values): `NFL_DATA_ROOT`, `NEO4J_PASSWORD`, `WANDB_API_KEY`.

## Tasks

### Repo and environment
- [ ] 🤖 Create the repo layout from [02](../02-system-architecture.md#proposed-repository-layout): `src/nflengine/{ingest,curate,features,models,graph,digest,track2}`, `config/`, `tests/`, `notebooks/`, plus `__init__.py` files.
- [ ] 🤖 `uv init` a package project (Python 3.12) with a `nfl` console script → `nflengine.cli:app`.
- [ ] 🤖 Add core dependencies: `nflreadpy`, `polars`, `pyarrow`, `duckdb`, `pydantic`, `pydantic-settings`, `pyyaml`, `typer`, `rich`, `neo4j`, `wandb`, `scikit-learn`, `lightgbm`, `shap`, `httpx`, `python-dotenv`. Dev: `pytest`, `ruff`. **Don't** add `torch` yet (T02 adds it).
- [ ] 🤖 `ruff` config in `pyproject.toml`; `pytest` config; one trivial passing test.

### Config, secrets and data root
- [ ] 🤖 `.env.example` with every variable from [02 → Secrets](../02-system-architecture.md#secrets-and-config) and comments.
- [ ] 🤖 `.gitignore`: `.env`, `.venv/`, `__pycache__/`, `*.parquet`, `*.duckdb`, `wandb/`, `notebooks/.ipynb_checkpoints/`, any local `data/`.
- [ ] 🤖 `config/settings.yaml` (seasons, `llm.provider: placeholder`, feature flags, thresholds as placeholders) and `config/followed_teams.yaml` (empty list).
- [ ] 🤖 `nflengine/settings.py`: load `.env` + YAML into a typed Pydantic settings object.
- [ ] 🤖 `nflengine/paths.py`: every data path built from `NFL_DATA_ROOT` (`raw`, `curated`, `features`, `models`, `runs`, `reports`, `bdb`, `neo4j`, `wandb`, `cache`). Includes `ensure_data_root()`, which **fails clearly if D: is missing** and creates subfolders if the root exists.
- [ ] 🤖 Set `WANDB_DIR`, `UV_CACHE_DIR` and the nflreadpy cache dir to point under the data root (document how in the repo README).

### Neo4j in Docker
- [ ] 🤖 `docker-compose.yml` per [05 → Runtime](../05-knowledge-graph.md#runtime-neo4j-in-docker-local): pinned `neo4j:5.26-community` (or a newer pinned tag), APOC + GDS plugins, a **bind mount to `${NFL_DATA_ROOT}/neo4j/{data,logs}`**, and memory settings.
- [ ] 🤖 `nflengine/graph/client.py`: a driver factory from settings, plus `ping()`.
- [ ] 🧑 **Rishi runs** `docker compose up -d`, opens `http://localhost:7474`, logs in, and runs `RETURN gds.version(), apoc.version()` in Neo4j Browser.

### W&B
- [ ] 🤖 `nflengine/tracking.py`: `init_run(group, job_type, config, tags)` that sets the project `nfl-analytics-engine` and the `launched-by` tag, with `WANDB_DIR` on D:.
- [ ] 🤖 A `nfl wandb-smoke` command that logs a 50-step fake curve plus a small table.
- [ ] 🧑 **Rishi runs** `uv run nfl wandb-smoke` and checks the run shows up in the W&B UI with a live-updating chart.

### CLI skeleton
- [ ] 🤖 Typer app with placeholder subcommands: `ingest`, `curate`, `features`, `ratings`, `train`, `backtest`, `graph`, `digest`, `weekly`, `doctor`, `wandb-smoke`.
- [ ] 🤖 `nfl doctor` checks and prints a green/red table for:
  - data root present and writable
  - `.env` loaded
  - Neo4j reachable with plugins loaded
  - W&B authenticated
  - nflreadpy importable
  - LLM provider = placeholder (OK) or a configured provider reachable
- [ ] 🤖 Short repo `README.md` (root): what the project is, link to `documentation/`, setup steps (uv, Docker, `.env`, `nfl doctor`).

### Wrap-up
- [ ] 🤖 `uv run pytest` and `uv run ruff check` are clean.
- [ ] ✋ **Checkpoint:** show Rishi the `nfl doctor` output and the repo tree, and get approval to close P00.

## Rishi-in-the-loop moments

- **Neo4j Browser first look:** confirm the plugins load and get familiar with the Browser UI (query editor, graph view, `:sysinfo`). Check that `D:\nfl-ml-data\neo4j\data` now has database files, which proves the bind mount works.
- **W&B smoke run:** look for the run under the `nfl-analytics-engine` project, the `launched-by:rishi` tag, and a curve that updates while the command runs.

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| Project installs from scratch | `uv sync` on a clean checkout succeeds |
| Data root works and is enforced | `uv run nfl doctor` shows the data root ✅; temporarily setting `NFL_DATA_ROOT` to a missing path gives a clear error |
| Neo4j up, files on D: | `nfl doctor` shows Neo4j ✅ with GDS and APOC versions; files exist under `D:\nfl-ml-data\neo4j\data` |
| W&B works | The smoke run is visible in W&B |
| Clean quality gates | `uv run pytest` passes; `uv run ruff check` is clean |

## Handoff to P01

- `paths`, `settings`, `tracking` and `graph.client` modules exist and are tested.
- The CLI has `ingest` and `curate` stubs ready to fill in.
- Neo4j is running (P01 doesn't need it; P05 does).

## Pitfalls / notes

- Docker bind mounts from Windows paths: in `docker-compose.yml` use forward slashes (`D:/nfl-ml-data/neo4j/data`) or the `${NFL_DATA_ROOT}` variable with forward slashes. Make sure Docker Desktop has file-sharing access to D:.
- If Neo4j fails to start on the bind mount (permission or locking errors in `docker compose logs neo4j`), first check that Docker Desktop's file sharing includes D:. If it still fails, switch to a named Docker volume. The graph is rebuilt from Parquet every week anyway, so nothing is lost. Record the switch in the decisions log.
- Don't put real secrets in `.env.example`.
