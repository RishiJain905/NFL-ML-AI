# Progress Tracker

> **Agents: update this file at the end of every session**, even mid-phase. See the protocol in [README.md](README.md).

**Current phase:** P03, Game model v0 (⬜ not started; P02 closed ✅)
**Next step:** Start P03 (game model v0) in a new session: read `P03-game-model.md`, then use `features/team_ratings`, `team_elo` and `team_trends` (as-of keyed, D44) and the as-of / leakage helpers (kickoff prompt in the `phase-workflow` skill).
**Last updated:** 2026-10-03, P02 session

Status key: ⬜ not started · 🟨 in progress · ⏸ waiting on Rishi · ⛔ blocked · ✅ done

## Phase status

| Phase | Title | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| P00 | Foundations | ✅ | 2026-10-03 | 2026-10-03 | Closed with Rishi's approval |
| P01 | Data ingestion and curation | ✅ | 2026-10-03 | 2026-10-03 | Closed with Rishi's approval |
| P02 | Team ratings, Elo, trend | ✅ | 2026-10-03 | 2026-10-03 | Closed with Rishi's approval (params D46, trends descriptive D47) |
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
| 2026-10-03 | P02 | `uv run nfl ratings tune` (smoke, 2 configs) | agent (delegated by Rishi) | [sweep 3koh08dr](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/3koh08dr) | Checked that in-process W&B sweeps work on Windows |
| 2026-10-03 | P02 | `uv run nfl ratings tune` (half-life × prior × alpha, 175 configs) | agent (delegated by Rishi) | [sweep dwyj31wk](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/dwyj31wk) | Best: half-life 12, prior 0.1, alpha 250 → MSE 0.1041 (vs 0.1194 / 0.1249 baselines). Optimum interior on every axis; top 10 within 0.00005 |
| 2026-10-03 | P02 | `uv run nfl ratings tune --qb-change-regressions 0,0.1,0.2,0.35,0.5` (at the best point) | agent (delegated by Rishi) | [sweep pbpcouy1](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/sweeps/pbpcouy1) | Every extra QB pull hurts (0 → 0.1041, 0.5 → 0.1043): set to 0 |
| 2026-10-03 | P02 | `uv run nfl ratings eval` (approved params) | agent (delegated by Rishi) | [ratings-eval hcir0po5](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/hcir0po5) | Beats both baselines in all 11 seasons; Elo walk-forward Brier 0.2214 |
| 2026-10-03 | P02 | `uv run nfl ratings validate-trend` | agent (delegated by Rishi) | [validate-trend ae30tzkf](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/ae30tzkf) | Final run (3-week target after the code review): ΔR² −0.0002, CI [−0.0013, 0.0008], 5/11 seasons → descriptive. First run [twins18a](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/twins18a) (3-game target) gave the same answer |

## Open blockers

_None._

## Session log (newest first)

### 2026-10-03: P01 closed, P02 built, tuned and closed
- **Approvals and waivers.**
  - Rishi approved closing P01 in the kickoff prompt.
  - For P02, Rishi chose: "run the 🧑 sweeps yourself, pause at the ✋". The sweeps ran with `launched-by:agent` and are logged above.
  - At the ✋, Rishi approved the parameters, the descriptive trend presentation and closing P02 (D46, D47).
- **Built.** `features/asof.py` and `features/leakage.py` are the as-of framework and leakage helpers every later phase uses. In `models/`:
  - `ratings.py`: weighted ridge from per-week sufficient statistics (~0.2 s for 2010–2026), fading preseason prior, home field pinned to a trailing 3 seasons (D41, D42).
  - `elo.py`: 538-style (D43).
  - `trend.py`, `trend_evidence.py`: delta, perf vs expected, direction bands, drivers, evidence (D45).
  - `ratings_eval.py`, `ratings_runs.py`: objective, baselines, W&B sweep / eval / validate-trend.
  - `ratings_build.py`: writes `features/team_ratings | team_elo | team_trends | team_trend_drivers` (keyed by as-of week, D44) plus DuckDB views and a sanity report.
  - CLI: `nfl ratings build | tune | eval | validate-trend`.
- **Probes that changed the design (local scans before any W&B run).**
  - The first prior (a recency-weighted end-of-season rating) lost to "raw last season" in weeks 1–3, so the prior now comes from a full-season fit.
  - The in-season home-field estimate swung ±0.07 EPA/play, which is noise, so it is pinned.
  - The sweep grid was widened after the first scan put the optimum at half-life 12–16, not 4.
- **Results.**
  - Ratings MSE 0.1041 vs 0.1194 (last season raw) and 0.1249 (season to date), better in every season 2015–2025, including weeks 1–3.
  - Elo Brier 0.2214.
  - Trends add nothing beyond the rating, so they are descriptive.
  - Sanity: 2025 final top 5 LA, SEA, NE, BUF, HOU; bottom NYJ, TEN, LV, CLE, CAR.
- **Subagents (named by Rishi):** sonnet-xhigh built `elo.py` + 17 tests; opus-high built `trend_evidence.py` + tests (QB fallback refined to last season's main starter).
- **Sol code review** (Codex `gpt-6.1-sol`, xhigh, read-only, via `/sol-qa`). The first call was blocked by the guard hook because the task text named the env file; I reworded it and noted this in the `sol-qa` skill. 8 findings; 7 fixed:
  - drivers' pass share is now as-of;
  - PFR lags one week for every key;
  - current-season NGS week-0 totals are hidden;
  - week-1 QB charts are only used if published by Tuesday (`depth_charts.snap_date`, curate re-run);
  - the trend target covers 3 weeks, not 3 games;
  - the bootstrap uses local cluster ids;
  - a test that couldn't fail now can.
  
  Declined: excluding postponed games played after Tuesday. Live runs wait for the week to finish, so week-based counting matches live behavior; this is documented in D44.
- **Data on D:.** Curated tables rebuilt (32 tables, every quality check passes). `features/` has 4 tables (11,040 rows each for ratings, Elo and trends; 28,224 drivers). Sanity report in `runs/2026/week04/ratings_sanity.md`. W&B: 3 sweeps (182 runs) and 3 eval runs in group `track1-ratings`.
- **Exit criteria.**
  - 11,040 rows = 345 as-of keys × 32 teams, with 0 nulls in core columns.
  - Parameters are in `settings.yaml` and the [model card](../model_cards/team_ratings.md).
  - Ratings beat the last-season baseline (W&B `hcir0po5`).
  - Elo Brier 0.2214 is logged.
  - The trend decision is D47.
  - Leakage tests pass.
- **Tests:** 146 pass; ruff clean. Skills updated: `model-experiment` (real as-of, leakage and tracking APIs, P02 lessons), `curated-data` (feature tables, P02 quirks, availability rule), `phase-workflow` (sweep timing, conftest), `sol-qa` (hook note).

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
