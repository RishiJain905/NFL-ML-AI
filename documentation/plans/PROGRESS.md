# Progress Tracker

> **Agents: update this file at the end of every session**, even mid-phase. See the protocol in [README.md](README.md).

**Current phase:** P01, Data ingestion and curation (⏸ all exit criteria met; waiting on Rishi's ✋ approval to close)
**Next step:** Rishi approves closing P01 → start P02 (team ratings, Elo, trend) in a new session (kickoff prompt in the `phase-workflow` skill).
**Last updated:** 2026-10-03, env-var cleanup session

Status key: ⬜ not started · 🟨 in progress · ⏸ waiting on Rishi · ⛔ blocked · ✅ done

## Phase status

| Phase | Title | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| P00 | Foundations | ✅ | 2026-10-03 | 2026-10-03 | Closed with Rishi's approval |
| P01 | Data ingestion and curation | ⏸ | 2026-10-03 | | All tasks + exit criteria done; awaiting ✋ approval |
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
| 2026-10-03 | P00 | `docker compose up -d` + plugin version check | agent (delegated by Rishi) | n/a | Neo4j 5.26.31 Community, GDS 2.13.13, APOC 5.26.31; ready in ~60 s; data + plugins (613 MB) on `D:/nfl-ml-data/neo4j` |

## Open blockers

_None._

## Session log (newest first)

### 2026-10-03: The Odds API enabled (D40)
- Rishi added `ODDS_API_KEY` and a backup `ODDS_API_KEY2`. `nfl doctor` shows both set.
- Ingest:
  - key rotation (the backup is used on 401/403/429; failures report only the variable name and status, never the URL)
  - the quota header is logged
  - `sources.odds_api: true`
- First live pull: 223 bookmaker-game rows, 28 games, 9 books, **497 requests left** on the main key (3 per pull).
- Curation: new `curate/lines.py` adds the per-bookmaker rows to `lines` (`source = 'odds_api'`, sign flipped to + = home favored), matched to `game_id` at 100%. The median across books agrees with nflverse/ESPN to within 0.5 points.
- Updates elsewhere:
  - the guard hook covers `ODDS_API_KEY2`
  - `.env.example` and `CLAUDE.md` list the new name
  - the `curated-data` skill has the new sources and a consensus-line recipe
  - doc 03 findings
- 79 tests pass.

### 2026-10-03: Env-var cleanup + project skills
- Rishi renamed the env-file variable to `NEO4J_PASSWORD`. `nfl doctor` confirms it's set directly. Removed the temporary fallback from settings, doctor, tests and compose (D39 supersedes D38). Neo4j stays up with no container recreate needed. 74 tests pass.
- Added project skills `curated-data` (data dictionary + verified recipes) and `model-experiment` (training/backtest/W&B recipe). Both are listed in `CLAUDE.md`. Curation fixes: integer season/week everywhere; old-format depth-chart positions stripped of whitespace.
- The Odds API key is still to come from Rishi (optional source).

### 2026-10-03: P01 build
- **Explored every source live first**, then built to match. Findings are in [03 → Findings from P01](../03-data-sources.md#findings-from-p01-checked-live-on-2026-10-03); decisions D32–D37.
- **Ingest** (`nfl ingest`):
  - snapshot store with per-file manifests (row counts, sha256) and a run manifest per run
  - polite HTTP client (per-host rate limits, retries, cache)
  - 22 nflverse datasets, 2010–2026, about 4.3M rows, ~250 MB. Participation goes to `research/` only.
  - ESPN (scoreboard/odds/weather, news, injuries, QBR, FPI)
  - NGS-site leader boards
  - Open-Meteo forecasts (stadiums matched by name first)
  - The Odds API (wired up, off without a key)
  - every optional source fails soft
- **Snapshot policy:** completed seasons pulled once, current season every run (D32). A full refresh takes ~15 s.
- **Curate** (`nfl curate`):
  - 31 tables plus `nfl.duckdb` views
  - canonical team codes (relocations follow the franchise)
  - `gsis_id` crosswalk: PFR 100%, snaps 99.9%, ESPN injuries 99.9%, QBR 100%
  - unified weekly depth charts across both nflverse formats
  - canonical `lines` (+ = home favored; ESPN sign-flipped; matches nflverse exactly)
  - fantasy news flagged
- **Quality checks:** all pass. 0 duplicates; all codes canonical; all 4,412 completed games have play-by-play; spread sign agrees on 98.7%; freshness reaches Week 3. Results are saved to `curated/_quality/latest.json`.
- **Readiness:** `nfl ingest --check-ready --week 3` → READY; `--week 4` → NOT READY (exit 3, lists missing games).
- **Other commands:** `nfl data-status` (snapshots, seasons, newest week, rows, join rates, disk use).
- **Tests:** 75 pass, ruff clean.
- **Disk on D::** raw 249 MB, research 20 MB, curated 239 MB.
- Nothing needed from Rishi during the build (ingestion is 🤖 by agreement).

### 2026-10-03: P00 Neo4j up
- Rishi (away from the computer) said the env file has `NEO4JS_PASSWORD`. Added a temporary fallback for the misspelled name in settings and compose (the correct name wins if both exist), a doctor WARN, and a test. 51 tests pass.
- The guard hook blocked a `docker inspect` status check during startup monitoring, as designed. Switched to `docker compose ps`.
- `docker compose up -d` → plugins downloaded → "Started." after ~60 s.
- `nfl doctor` exits 0: everything OK, plus one WARN (the fallback reminder).
- **All P00 exit criteria are met.** Waiting on Rishi's ✋ approval to close P00.

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
