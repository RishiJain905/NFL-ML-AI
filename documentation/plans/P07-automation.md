# P07: Automation and Weekly Operations

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P06
- **Unlocks:** P08, P10
- **Read first:** [02 → Weekly schedule, Orchestration, Failure handling](../02-system-architecture.md#weekly-schedule), [08 → Weekly pipeline logging, Season scorecard, Drift signals](../08-experiment-tracking.md#what-each-weekly-pipeline-run-logs)

> **Re-scoped at kickoff (2026-10-04): manual-first (D71).** Rishi: "we likely do not need to automate anything right now since it isn't deployed and everything is working fairly well". Everything that makes a hands-on run safe, observable and self-checking was built; the Task Scheduler, retries-on-a-timer and SMTP / push pieces are deferred (marked ⏭ below), and the exit criteria were adapted (see "As built").

## Goal

The full weekly pipeline runs **with no manual steps**: scheduled, calendar-aware, with retries, fail-soft enrichment, notifications and complete W&B logging. Rishi just reads the digest and watches W&B.

## Scope

- **In:** calendar-driven scheduling, Windows Task Scheduler setup, retry logic, the optional Saturday injury update, notifications, full weekly-pipeline logging, the season dashboard, drift alerts, a runbook.
- **Out:** model improvements (P08).

## Tasks

### Calendar logic
- [x] 🤖 `nflengine/calendar.py` (as built: `nflengine/ops/calendar.py`): from the schedule, work out the target week, whether the previous week is complete, the **deadline** (first kickoff of the next slate), and special cases (Thursday / Thanksgiving / Christmas / Saturday games, international games, byes, Week 18, playoffs).
- [x] 🤖 Tests over the real 2024–2026 schedules for every edge case.

### Pipeline hardening
- [x] 🤖 `nfl weekly run --auto`: works out season and week itself, runs the readiness check, and **exits cleanly with a "not ready" status** (for the retry scheduler) when data is missing.
- [x] 🤖 Every step from [02 → Failure handling](../02-system-architecture.md#failure-handling) implemented and tested: data drive missing, nflverse not ready, ESPN / extra sources down, lines missing, Neo4j down (try `docker compose up -d` once), checks failing.
- [x] 🤖 A lock file so two runs never overlap.
- [x] 🤖 Run summary JSON + full W&B logging for the weekly pipeline ([08](../08-experiment-tracking.md#what-each-weekly-pipeline-run-logs)).

### Saturday injury update
- [x] 🤖 `nfl weekly injury-update --auto`: re-ingest injuries + news + lines, rerun the game and player predictions, compare with the main run, and publish an addendum **only if** the materiality rule fires ([06](../06-weekly-digest.md#digest-layout)). Behind the `injury_update_enabled` flag.

### Scheduling (Windows Task Scheduler)
- [ ] ⏭ **Deferred (D71)** 🤖 `scripts/register_tasks.ps1` creates the tasks: **Tuesday 10:00 main run**, a retry every 3 h until Wednesday 18:00 (each retry exits right away if the week is already done), and the optional Saturday 10:00 update. Settings: "wake the computer to run", "run whether user is logged on or not", start Docker Desktop if needed.
- [ ] ⏭ **Deferred (D71)** 🧑 **Rishi runs** the registration script (it needs Rishi's account and permissions), and checks the tasks in Task Scheduler.
- [x] ✋ **Checkpoint (answered at kickoff, D71; Q07):** decide whether the PC can stay on / wake reliably, and whether the D: drive stays connected. If not, plan the GitHub Actions fallback ([02 → Orchestration](../02-system-architecture.md#orchestration)) as a separate task.

### Notifications and dashboard
- [x] 🤖 Notifications (email over SMTP, or a push service, per Rishi's choice in the decisions log Q02): digest published (with the file path), failure (with the failed step), drift alert. *As built (D76): alerts at the end of the run, in `run_summary.json`, and as W&B alerts; no SMTP (Q02 answered).*
- [x] 🤖 W&B Report "2026 Season Dashboard" ([08](../08-experiment-tracking.md#season-scorecard-the-long-term-view)): season scorecard, accuracy scoreboard, calibration, pipeline health.
- [x] 🤖 Drift checks from [08 → Drift signals](../08-experiment-tracking.md#drift-signals-and-responses) evaluated each run; they send an alert and never retune automatically.

### Runbook
- [x] 🤖 `documentation/runbook.md`: how to rerun a failed week, resume from a step, publish manually, rebuild the graph, roll back a model alias, and what each alert means.

### Proving it
- [x] 🤖 Dry run: simulate a full week with `--auto` against a past week (time travel via `--as-of`).
- [ ] ⏭ **Dropped with scheduling (D71)** 🧑 **Rishi watches** the first two scheduled weeks land (digest + W&B + notification) without touching anything.
- [x] ✋ **Checkpoint:** close P07. *(Waived in Rishi's kickoff: "marking the phase complete".)*

## Rishi-in-the-loop moments: what to look for

- **The first scheduled run:** in the W&B `weekly-pipeline` group, check step timings, data freshness and check results.
- **The season dashboard:** confirm it updates by itself after each run.
- From here on, weekly retraining is automatic. Rishi watches the charts and steps in only when a drift alert fires.

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| **Two consecutive weeks published with no manual steps** | Two digests on D:, two `weekly-pipeline` runs in W&B, two notifications; PROGRESS notes no manual steps were needed |
| Retries work | A simulated "not ready" exits cleanly and the retry succeeds (tested with `--as-of`) |
| Fail-soft works | Tests for each failure mode pass |
| Dashboard live | The W&B report updates automatically |
| Runbook exists | `documentation/runbook.md` |

## Handoff to P08 / P10

- A stable production pipeline. Model upgrades in P08 are swapped in **only through the `production` alias**, between weeks.

## Pitfalls / notes

- Task Scheduler + Docker Desktop: Docker must be running before the Neo4j step. The pipeline should start it and wait, or fail soft.
- Sleep or hibernate can skip tasks. "Wake to run" needs the right power settings.
- Time zones: kickoff times in the schedule are US Eastern. Convert them carefully.

## As built: deviations from the task list

**Scope (D71, manual-first).** Built: the calendar, `--auto`, failure handling, the lock, run records and W&B pipeline logging, the injury update (by hand), alerts, the season dashboard, drift checks, the runbook, time-travel simulations. Not built: `scripts/register_tasks.ps1`, timed retries, SMTP / push. Everything a scheduler would need is in place: exit 3 = retry later, exit 0 `already_done` = nothing to do, the lock prevents overlaps (runbook → "Scheduling later").

**Exit criteria as verified (2026-10-04):**

| Criterion (as written) | As verified |
|---|---|
| Two consecutive weeks published with no manual steps | **Adapted (D71):** two consecutive **simulated** weeks published end to end by `nfl weekly run --auto --as-of`: 2025 week 10 (Monday 22:00 ET → `not_ready`, exit 3, `jxqxazom`; Tuesday 10:00 → `ok`, 95 s, published 58.2 h before the simulated kickoff, checks passed, `59p8uw4z`; the same moment again → `already_done`) and week 11 (`ok`, 86 s, `nywth4ts`). Live: `nfl weekly run --auto` on Sunday 2026-10-04 found week 4 already published (exit 0). The first live `--auto` run is Tuesday's week 5 |
| Retries work | The simulated "not ready" exits 3 cleanly and the retry succeeds (above); unit tests `test_not_ready_exits_3_cleanly_and_the_retry_succeeds`, `test_not_ready_after_wednesday_evening_alerts` |
| Fail-soft works | `tests/ops/test_failure_modes.py`: one test per doc-02 row (drive missing, nflverse not ready, ESPN / extra source down, lines missing, Neo4j down, checks failing, unhandled exception). Neo4j checked live: container stopped → `ensure_neo4j` ran `docker compose up -d` → up in 50 s |
| Dashboard live | W&B Report "2026 Season Dashboard" built; it reads the `season-dashboard` run tagged `dashboard-current`, which moves to each new run (`ugjybk9h` now); read back with `check_report` |
| Runbook exists | `documentation/runbook.md`, plus the guide `guides/weekly-operations.md` and the `weekly-ops` skill |

**Other deviations:**
- **Module location:** `nflengine/ops/` (`calendar.py`, `lock.py`, `services.py`, `records.py`, `summary.py`, `drift.py`, `dashboard.py`, `injury_update.py`) instead of `nflengine/calendar.py`.
- **`--as-of` in the past runs a simulation** (readiness from `kickoff + 10 h`, the digest as a backtest at that moment, backtest folders only, no alerts, no dashboard), not the live steps, which can't time-travel (D74).
- **Drift thresholds replayed on 2019–2025 before use** (D75): doc 08's flat "season ECE above 0.05" fired in 85% of weeks (a perfect model averages ~0.08 on a season's games), so the limit is the higher of 0.05 and the chance level's 90th percentile; on the shown probabilities the rules then fire in 5% (calibration) and 2% (game vs Elo) of weeks, the player rules never.
- **The injury-update addendum is code-written** (no LLM), and Tuesday's prediction files stay the graded ones (D73).
- **`--auto` promotes** the live fits to `production` (D72).
- **The W&B pipeline run** (`weekly-pipeline` / `pipeline`) is new; it carries lineage to the week's artifacts (closes the W&B guide's gap 3).
- **Unit tests run with `WANDB_MODE=disabled`** after a CLI test reached the real pipeline (4 junk `pipeline-2026-w05` W&B runs, records on D: deleted).

