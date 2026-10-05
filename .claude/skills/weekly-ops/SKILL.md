---
name: weekly-ops
description: How the weekly pipeline is operated (P07+): `nfl weekly run --auto` and its exit codes, the calendar (target week, deadline, special weeks), the run lock, Neo4j / Docker start-up, run records (run_summary.json, pipeline_history.parquet, the `weekly-pipeline` / `pipeline` W&B run), alerts, drift checks, the season dashboard and its W&B Report, the Saturday injury update, `--as-of` time-travel simulations, and how to test or extend any of it. Use when running, resuming, debugging or changing the weekly run, adding a pipeline step, reading an alert, or preparing to schedule the pipeline.
---

# Weekly operations (P07+)

The operator's page is `documentation/runbook.md`; how each piece works is `documentation/guides/weekly-operations.md`; the decisions are D71–D77 in `documentation/10-decisions-log.md`. Code: `src/nflengine/ops/` and `src/nflengine/weekly.py` (`run_pipeline`).

## 1. Ground rules
- **Manual-first (D71).** Nothing is scheduled. Don't add Task Scheduler entries, cron jobs, SMTP or push notifications unless Rishi asks; the runbook's "Scheduling later" lists what it would take.
- **A live weekly run publishes a digest and moves W&B aliases.** Agents run `nfl weekly run --auto` (live) or `nfl weekly injury-update` (live) only when Rishi asks in the current session, with `--launched-by agent`, and log it in PROGRESS's Rishi-run steps log. Dry runs (`--dry-run`), `nfl weekly status` and past-week simulations (`--as-of <past>`) are always fine.
- **Never re-run a published week to "update" it** without being asked (digest-checks skill §8): `--auto` refuses (`already_done`); `--force` and `--from-step` exist for repairs Rishi wants.

## 2. Commands and exit codes
| Command | Use |
|---|---|
| `uv run nfl weekly run --auto` | The weekly run. Works out season / week; skips a published week |
| `uv run nfl weekly run --auto --dry-run` | Plan only; exit 3 if last week isn't final |
| `uv run nfl weekly status [--as-of T]` | Calendar plan, step states for weeks N−1 and N, the lock, the last 5 runs |
| `uv run nfl weekly run --season S --week N --from-step <step>` | Resume / repair a specific week |
| `uv run nfl weekly run --auto --as-of 2025-11-04T10:00` | Simulate a past moment (ET without an offset) |
| `uv run nfl weekly injury-update --auto [--no-ingest] [--no-players] [--no-wandb]` | Saturday update |
| `uv run nfl dashboard update --season S --week N` / `build --season S` | Re-log the dashboard run / create-or-update the W&B Report |

Exit codes of `nfl weekly run`: **0** ok, degraded, already_done, idle, planned · **1** failed · **2** usage error · **3** not ready (retry later) · **4** another run holds the lock · **5** data drive missing (nothing written). `injury-update`: 0 / 1 / 4 / 5.

## 3. How `run_pipeline` is put together
1. `ensure_data_root()` (exit 5 on `DataRootError`; 2 is click's usage error), schedules from the newest raw snapshot (`ops.calendar.load_schedules`).
2. `plan_week(schedules, now, data_lag_hours=...)` → `WeekPlan`; a manual run of another week uses `plan_for(season, week)` so deadline / readiness are that week's.
3. `--auto` only: a missing slate (playoff round not added yet) → refresh just the schedule (`refresh_schedule`), else `not_ready`; offseason → `idle`; season ≠ `seasons.current` → error; already published (`weekly_run.json` digest ok + report file) → `already_done`.
4. `--dry-run` returns here.
5. The lock (`ops.lock.run_lock`, an OS byte-range lock on `runs/.weekly.lock`; released by the OS when the process ends) is taken **before** any schedule refresh (refreshes write snapshots), then `run_weekly(opts, steps=STEPS | SIM_STEPS, funcs={defaults, **overrides})`.
6. `_post_run` (wrapped so it can never change the result): freshness → drift → alerts → `run_summary.json` → `pipeline_history.parquet` → W&B pipeline run → dashboard (main runs that published).
- `--auto` promotes (`ops.promote_auto`, D72); manual runs only with `--promote`.
- **The `--auto` pre-check:** if the schedule says week N−1 isn't final, refresh only the schedule (`refresh_schedule`), re-plan, and if it's still not final run just a calendar `ready` step (`_calendar_ready`): a recorded `not_ready` with no full ingest.
- Simulations (`--as-of` more than an hour in the past): steps `ready` (calendar time travel: `kickoff + ops.data_lag_hours`) and `digest` (backtest mode, `run_time = as_of`, placeholder writer, `graph="off"` so the live graph is never wiped, no digest W&B run); files only under `runs/digest-backtests/` and `reports/backtests/`; W&B `weekly-pipeline` / `simulation`; no dashboard.

## 4. Testing it
- `tests/ops/test_pipeline.py` is the pattern: `run_pipeline(schedules=<fixture>, now=<pinned clock>, funcs={name: fake}, use_wandb=False, log=...)` with `monkeypatch.setattr(nflengine.weekly, "ensure_data_root", lambda *a, **k: DataPaths(tmp_path))`. The schedule fixture is `tests/fixtures/schedules_2024_2026.csv` (a column subset of nflverse's schedule as of 2026-10-04).
- `tests/conftest.py` stubs `weekly.refresh_schedule` for every test (autouse `_no_real_schedule_refresh`) (it would run a real nflverse ingest). Live `--auto` tests need a schedule whose week N−1 has results, or the pre-check stops them (`test_pipeline.py::sched` fills 2026 weeks 1–4).
- **CLI tests must stub `nflengine.weekly.run_pipeline`**, never `run_weekly`: a CLI test that reached the real pipeline wrote records to D: and logged failed W&B runs (P07). `tests/conftest.py` now sets `WANDB_MODE=disabled` for every non-integration test as a safety net; a real W&B smoke belongs in a scratchpad script, not pytest.
- `ops.services.ensure_neo4j(ping=, run=, start_desktop=, sleep=)` takes fakes; never stop Docker Desktop itself in a test (other containers may run there). Stopping the `nfl-neo4j` container to test the real path is allowed (CLAUDE.md), and it comes back in about 50 s.
- The lock is an OS lock (`msvcrt.locking` on a byte past the note / `fcntl.flock`): a second handle in the **same** process is refused too, so a test can hold `run_lock` and assert a run gets `locked`; a crash test uses a child process. The file persists (an empty note = free): check `ops.lock.is_locked`, not `path.exists()`. Never use `os.kill(pid, 0)` on Windows (it terminates the process).
- `weekly_run.json` is written atomically (`_write_state`); `read_state` returns None for a missing or unreadable file.

## 5. Extending it
- **A new pipeline step:** add it to `weekly.STEPS` and `STEP_FUNCS` (raise `StepFailed` to stop, `StepDegraded` to fail soft), decide whether simulations need it (`SIM_STEPS`, `_sim_funcs`), add its artifact to `summary._use_artifacts` if it logs one, and document it in the W&B guide §4 and the runbook's step list.
- **A new drift signal:** a function in `ops/drift.py` returning `DriftSignal`, wired into `evaluate_drift`, a threshold in `settings.yaml` → `drift`, a row in the runbook's alert table, a replay over past seasons before trusting it.
- **A new record field:** `ops/summary.build_summary` (+ `history_row` and `records.HISTORY_SCHEMA` if it belongs in the history; `append_history` back-fills missing columns as null).
- **Scheduling later:** wrap `nfl weekly run --auto` (retry on exit 3, stop on 0) and `nfl weekly injury-update --auto`; nothing in the pipeline needs to change.

## 5b. What P08 added to the steps (no new step)
- **`game`** fits the production version (`game_model.version`: v0; v1 was evaluated and not promoted, D79). `load_frame` now also computes the v1 extras (injury load, weather, home edge); v0 ignores them.
- **`player`** (`weekly._player`): scores the season (players, then team rows), refits the 23 `player_model.live_targets` (needs `runs/backtests/player/<key>/` for every live P08 target: its calibration seed with `_p_raw`), then **`_team_fit`** (the shipped `team_model.live_targets` → `predictions_teams.parquet`; any error is a note, never `degraded`), then **`_consistency`** (on rows not kicked off yet: receptions ≤ targets; the file is rewritten atomically only if a row changed; `consistency.json` always), then the graph write with the final table. Measured in a scratch rehearsal of 2026 week 4: ~95 s for the whole step; the refits (23 targets × the season's weeks so far) dominate, so it grows by about a week's worth of refits each week, not with the slate's size.
- **Records:** `run_summary.json` gains `models.team` and `consistency`; the pipeline W&B run gets `consistency/*` keys and `use_artifact`s `team-model` when the week has team projections; `--auto` promotes `team-model` too.
- **The scoreboard file is shared:** player rows (now also CB/S and probability targets with null MAE) and TEAM rows. The MAE drift signal drops null-MAE rows and treats CB/S and TEAM as extra groups; **`player_prob_vs_baseline`** (D85) runs the same streak rule on the Brier score of the chances (`prepare_scoreboard(..., metric="brier")`; 0 alerts in the 2019–2025 replay); the digest's player report card drops TEAM rows. Every upsert runs under `ops.lock.file_lock` (an OS lock: never broken by a timeout) and writes atomically (`ops.lock.write_parquet_atomic`); so do the live prediction files (Sol review, P08).
- **Rehearse the Tuesday path without touching live files:** a `DataPaths` subclass whose `runs` / `models` / `reports` / `wandb` point to scratch, patched into every loaded `nflengine` module's `ensure_data_root`, `WANDB_MODE=disabled`, `graph.projections.write_projections` stubbed, then `weekly._player(WeeklyOptions(S, W), log)` and `run_digest(..., graph="off", provider="placeholder", use_wandb=False)`. Copy the canonical backtest folders (exactly the `TARGET_BY_KEY` names: a filter like `"_" not in name` silently drops `pass_yds-qb` and the ranges change) and `runs/<S>/` into scratch first. With unchanged data, the fresh rows must equal the published file exactly (they did, P08).

## 6. Known quirks
- `weekly_run.json` timestamps are local time with an offset since P07 (naive local before); `_ran_since` handles both.
- `run_summary.json` → `steps[*].this_run` tells which steps ran in this invocation; a resumed run keeps earlier steps' rows.
- nflverse lists some neutral-site games under the home team's stadium (2025: all international games); `stadiums.yaml` → `game_venues` fixes them, and the calendar reads it first.
- The live graph is replaced by any backtest graph build (simulations, `nfl digest --backtest`, integration tests). Rebuild the live week afterwards (`nfl graph build --season S --week N`) and re-write its projections (`graph.projections.write_projections` on the week's `predictions_players.parquet`).
- A Saturday update with unchanged data reproduces Tuesday's numbers exactly (0.0 move), so any move in a real update comes from new data.

## Improving this skill
Add new failure patterns, alert meanings or test patterns here in the same commit as the fix, and note it in the PROGRESS session log. Rules belong in CLAUDE.md; the operator's how-to in the runbook.
