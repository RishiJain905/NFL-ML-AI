# Progress Tracker

> **Agents: update this file at the end of every session**, even mid-phase. See the protocol in [README.md](README.md).

**Current phase:** P00, Foundations (⏸ waiting on Rishi: `NEO4J_PASSWORD`)
**Next step:** once Rishi fixes the `NEO4J_PASSWORD` line → `docker compose up -d` → `uv run nfl doctor` all green → ✋ close P00.
**Last updated:** 2026-10-03, P00 build session

Status key: ⬜ not started · 🟨 in progress · ⏸ waiting on Rishi · ⛔ blocked · ✅ done

## Phase status

| Phase | Title | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| P00 | Foundations | ⏸ | 2026-10-03 | | All tasks done except Neo4j start (needs `NEO4J_PASSWORD`) + checkpoint |
| P01 | Data ingestion and curation | ⬜ | | | |
| P02 | Team ratings, Elo, trend | ⬜ | | | |
| P03 | Game model v0 | ⬜ | | | |
| P04 | Digest v0 (first live digest) | ⬜ | | | |
| P05 | Knowledge graph v1 | ⬜ | | | |
| P06 | Player model v1 + accuracy scoreboard | ⬜ | | | |
| P07 | Automation and weekly operations | ⬜ | | | |
| P08 | Models v2 + advanced graph | ⬜ | | | |
| P09 | Connect a real LLM | ⬜ | | | Any time after P04 |
| P10 | Season operations and offseason | ⬜ | | | |
| T00 | BDB data + baselines | ⬜ | | | Recommended after P04 |
| T01 | BDB gradient-boosted model | ⬜ | | | |
| T02 | BDB sequence model | ⬜ | | | |
| T03 | BDB interaction model | ⬜ | | | |
| T04 | Track 2 → Track 1 bridge | ⬜ | | | |

## Rishi-run steps log

Every 🧑 step goes here, whether Rishi ran it or delegated it.

| Date | Phase | Step | Run by | W&B run / link | Notes |
|---|---|---|---|---|---|
| 2026-10-03 | P00 | `uv run nfl wandb-smoke` | agent (delegated by Rishi) | [restful-serenity-1](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/pfcqqjfy) | 50 live steps + summary table; local files on D: |
| 2026-10-03 | P00 | `docker compose up -d` + Neo4j Browser check | agent (delegated by Rishi) | n/a | **Blocked:** `NEO4J_PASSWORD` has no value in the env file (doctor + compose both report it) |

## Open blockers

- **`NEO4J_PASSWORD` not set** (P00). Rishi needs the env file line to read exactly `NEO4J_PASSWORD=<8+ chars>`. Agents can't check this beyond `nfl doctor`.

## Session log (newest first)

### 2026-10-03: P00 build
- Rishi delegated every 🧑 step in P00 and asked for subagent help where useful.
- Built:
  - repo skeleton
  - `uv` project (Python 3.12; package cache on D:, `link-mode = "copy"`)
  - `settings.py` (secrets as `SecretStr`, `is_set()` without revealing values)
  - `paths.py` (data root on D:, fails on a missing drive; the root is created only with `nfl doctor --init-data-root`; nflreadpy cache configured explicitly)
  - `tracking.py` (`init_run` with `launched-by` auto-detection, refuses secret-looking config keys)
  - `graph/client.py`, `doctor.py`, `cli.py` (doctor, wandb-smoke, placeholder commands)
  - `docker-compose.yml` (Neo4j 5.26 Community + APOC + GDS, data/logs/plugins bind-mounted on D:)
  - config files, `.env.example`, root README, full `.gitignore`
- **opus-high reviewed P00** (read-only): 12 findings, no live leaks. All addressed:
  - hook strengthened (dotenv loaders, `set` / `export -p` / `declare -x` / `cmd /c set`, `Get-Item env:`, `[Environment]::`, any `environ` reference, `get_secret_value`; now blocks unparseable calls instead of allowing them)
  - sanitized Neo4j errors in doctor
  - `pretty_exceptions_show_locals=False`
  - nflreadpy `update_config`
  - plugins mount; `NEO4J_USER` in compose
  - `--steps` min 1
  - `.gitignore`
  - missing plugins = FAIL
  - no silent root creation on a typo
  - Grep finding tested empirically with a decoy: Grep never reaches env files
- Tests: **50 passed** (paths, settings, CLI, tracking, the guard hook including the new cases). `ruff check` and `ruff format` are clean.
- `nfl doctor`: everything OK except `NEO4J_PASSWORD` (NOT SET), and therefore Neo4j.
- W&B runs go to the account's default entity `models-ontario-tech-university`. Set `WANDB_ENTITY` in the env file to use another entity.
- Committed and pushed.

### 2026-10-03: P00 started, security guardrails
- Rishi confirmed the prerequisites: Docker Desktop running, D: connected, W&B key and Neo4j password saved by Rishi in the env file (agents never read it).
- Added `CLAUDE.md` with a top-priority **Security** section (never read or reveal credentials).
- Enforcement:
  - `.claude/settings.json` deny rules (Read/Edit of env and credential files, env-dumping and secret-printing commands)
  - a PreToolUse hook, `.claude/hooks/block_secrets.py`, that guards Bash, PowerShell, Read, Grep and Glob
  - the hook passed 26 synthetic cases; live tests against a decoy file confirmed Read, Bash and PowerShell are all blocked; decoy removed
- `.gitignore`: env files stay ignored, with an exception for the example template.
- Note: the hook also blocks any shell command whose *text* names an env file, including commit messages. Use the Edit tool for doc changes that mention it, and phrase commit messages as "env file".
- Not committed yet.

### 2026-10-01: Planning
- Reviewed the original brief and spec, and rewrote them as `documentation/01`–`11` (originals in `documentation/archive/`).
- Decisions recorded in `documentation/10-decisions-log.md` (D01–D31).
- Created `documentation/plans/` with phases P00–P10, T00–T04 and STRETCH.
- **No phase started.** Next: P00 Foundations, after Rishi's go-ahead.
- Rishi's prerequisites for P00: Docker Desktop installed and running; the D: drive connected (data root `D:\nfl-ml-data`); a W&B API key and a Neo4j password ready to put in `.env`.
