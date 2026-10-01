# P07: Automation and Weekly Operations

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P06
- **Unlocks:** P08, P10
- **Read first:** [02 → Weekly schedule, Orchestration, Failure handling](../02-system-architecture.md#weekly-schedule), [08 → Weekly pipeline logging, Season scorecard, Drift signals](../08-experiment-tracking.md#what-each-weekly-pipeline-run-logs)

## Goal

The full weekly pipeline runs **with no manual steps**: scheduled, calendar-aware, with retries, fail-soft enrichment, notifications and complete W&B logging. Rishi just reads the digest and watches W&B.

## Scope

- **In:** calendar-driven scheduling, Windows Task Scheduler setup, retry logic, the optional Saturday injury update, notifications, full weekly-pipeline logging, the season dashboard, drift alerts, a runbook.
- **Out:** model improvements (P08).

## Tasks

### Calendar logic
- [ ] 🤖 `nflengine/calendar.py`: from the schedule, work out the target week, whether the previous week is complete, the **deadline** (first kickoff of the next slate), and special cases (Thursday / Thanksgiving / Christmas / Saturday games, international games, byes, Week 18, playoffs).
- [ ] 🤖 Tests over the real 2024–2026 schedules for every edge case.

### Pipeline hardening
- [ ] 🤖 `nfl weekly run --auto`: works out season and week itself, runs the readiness check, and **exits cleanly with a "not ready" status** (for the retry scheduler) when data is missing.
- [ ] 🤖 Every step from [02 → Failure handling](../02-system-architecture.md#failure-handling) implemented and tested: data drive missing, nflverse not ready, ESPN / extra sources down, lines missing, Neo4j down (try `docker compose up -d` once), checks failing.
- [ ] 🤖 A lock file so two runs never overlap.
- [ ] 🤖 Run summary JSON + full W&B logging for the weekly pipeline ([08](../08-experiment-tracking.md#what-each-weekly-pipeline-run-logs)).

### Saturday injury update
- [ ] 🤖 `nfl weekly injury-update --auto`: re-ingest injuries + news + lines, rerun the game and player predictions, compare with the main run, and publish an addendum **only if** the materiality rule fires ([06](../06-weekly-digest.md#digest-layout)). Behind the `injury_update_enabled` flag.

### Scheduling (Windows Task Scheduler)
- [ ] 🤖 `scripts/register_tasks.ps1` creates the tasks: **Tuesday 10:00 main run**, a retry every 3 h until Wednesday 18:00 (each retry exits right away if the week is already done), and the optional Saturday 10:00 update. Settings: "wake the computer to run", "run whether user is logged on or not", start Docker Desktop if needed.
- [ ] 🧑 **Rishi runs** the registration script (it needs Rishi's account and permissions), and checks the tasks in Task Scheduler.
- [ ] ✋ **Checkpoint:** decide whether the PC can stay on / wake reliably, and whether the D: drive stays connected. If not, plan the GitHub Actions fallback ([02 → Orchestration](../02-system-architecture.md#orchestration)) as a separate task.

### Notifications and dashboard
- [ ] 🤖 Notifications (email over SMTP, or a push service, per Rishi's choice in the decisions log Q02): digest published (with the file path), failure (with the failed step), drift alert.
- [ ] 🤖 W&B Report "2026 Season Dashboard" ([08](../08-experiment-tracking.md#season-scorecard-the-long-term-view)): season scorecard, accuracy scoreboard, calibration, pipeline health.
- [ ] 🤖 Drift checks from [08 → Drift signals](../08-experiment-tracking.md#drift-signals-and-responses) evaluated each run; they send an alert and never retune automatically.

### Runbook
- [ ] 🤖 `documentation/runbook.md`: how to rerun a failed week, resume from a step, publish manually, rebuild the graph, roll back a model alias, and what each alert means.

### Proving it
- [ ] 🤖 Dry run: simulate a full week with `--auto` against a past week (time travel via `--as-of`).
- [ ] 🧑 **Rishi watches** the first two scheduled weeks land (digest + W&B + notification) without touching anything.
- [ ] ✋ **Checkpoint:** close P07.

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
