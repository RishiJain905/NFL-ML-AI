# CR02: Run control and the live pipeline

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Control room rows)

- **Depends on:** CR01 (the views and the week readers)
- **Unlocks:** the Tuesday routine from the app
- **Read first:**
  - [Control room README](README.md) §2 (the Run button, repairs, Saturday), §4 (runner, events) and §5 (safety rules, all of them);
  - the mockup's week-5 states (Not ready / Ready / Running / Failed / Published; source `mockup/app.js`: `preflight`, `confirmRun`, `startRun`, `nowbar`, `driveTick`, `mapTick`, `timelineTick`, `week5Pipeline`);
  - the `weekly-ops` skill (exit codes, the lock, `run_pipeline`, rehearsals);
  - [weekly operations guide](../guides/weekly-operations.md) §1–§5, §9;
  - [runbook](../runbook.md).

## Goal

On a Tuesday, Rishi opens the app:
- the pre-flight panel says whether the week can run, and if not, why (the Run button is greyed out with the reason);
- he presses **Run week N**, confirms the week, and watches every step live in his chosen view, with the log streaming;
- if a step fails, the app shows why and offers **Resume from <step>** for that week only;
- on Saturday he presses **Run injury update**.

Everything runs through the same `nfl` commands as the terminal, with the same lock and records. The phase ends with **the first real Tuesday run launched from the app**.

## Scope

- **In:**
  - two pipeline additions: `--expect-week` and progress events (`events/*.jsonl`);
  - pre-flight;
  - the runner (allowlist, detached processes, re-attach);
  - server-sent events;
  - Run / confirm / Resume / injury update in the UI;
  - the views' live mode;
  - toasts and browser notifications;
  - `nfl app --rehearsal`;
  - runbook and guide sections.
- **Out:** scheduling (still manual-first, D71); any button for `--force`, `--as-of`, re-running a published week, graph rebuilds or deletes; MLOps and season pages (CR03).

## Tasks

### Probe first
- [x] 🤖 **Where each step can report progress, and how to report it without changing results:** ✅ (README §9 "Checked in CR02")
  - ingest: loop over datasets;
  - curate: tables, then quality checks;
  - ratings;
  - game: fit, predict, log;
  - graph: wipe, load, queries, GDS;
  - player: scoreboard, then the 23 target refits, team totals, consistency, graph projections;
  - digest: payload, the LLM call and any rewrite, checks, render;
  - records.

  Follow how `log=` flows through `run_weekly` today.
- [x] 🤖 **The LLM step:** what can be known while the GLM call runs. ✅ streaming works (a probe: 167 reasoning chunks on Novita) but isn't used: the call stays as it is (D103)
  - The provider and token counts arrive with the response (`raw_llm_output.json` → `calls[]`).
  - Check whether OpenRouter streaming works with reasoning on the current BaseTen-first routing (D88), without changing what's written.
  - If it doesn't, the live view shows the elapsed time, the route order and "waiting for the model", and fills in the provider, time, tokens and cost when the call returns. The mockup's live token counter is then a deliberate difference (README §10).
  - **Don't change the LLM call just for the UI** (P09's rules: a payload or call change needs a real-LLM re-check).
- [x] 🤖 **`--launched-by`:** how it's validated today ("rishi | agent", auto-detected). A run started from the app is Rishi's (`rishi`). Decide whether to add a W&B tag such as `via:control-room` so app runs can be told apart; log the decision. ✅ app runs pass `rishi`; W&B tag `via:control-room` (D103)
- [x] 🤖 **Windows process handling:** ✅ probed: the child outlived its parent and held the OS lock (D103)
  - start `uv run nfl …` so it **outlives the app and the browser** (`CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`, output to a log file);
  - check that the OS run lock still behaves (it's held by the child process);
  - check what the lock note holds, for re-attaching.

### Pipeline additions (CLI; they help the terminal too)
- [x] 🤖 **`nfl weekly run --auto --expect-week N`:** ✅ exit 6 (D103)
  - after the calendar has resolved the week (including any schedule-only refresh under the lock), if the week isn't N: stop before any step, write no records, release the lock, and exit with **a new exit code 6, "week mismatch"** (or another distinct code; log the decision);
  - `--expect-week` without `--auto` is a usage error;
  - tests in `tests/ops/test_pipeline.py`;
  - update the exit-code tables (the `weekly-ops` skill, the weekly operations guide, the runbook).
- [x] 🤖 **Progress events:** ✅ `ops/events.py` (+ `log` events and `run_start.earlier`, D103)
  - a small `ops/events.py` with an `EventWriter` that `run_weekly` / `run_pipeline` set in a context variable;
  - steps call `events.progress(step, done, total, label)`, which does nothing when no writer is set, so tests, rehearsals and old callers work unchanged.

  File: one per invocation, `runs/<S>/week<NN>/events/<start-time>.jsonl` (under the redirected root in rehearsals). One JSON object per line, each with `seq`, `t` (ISO with offset), `run_id` and `type`:
  - `run_start`: command, season, week, plan summary;
  - `step_start`, `progress` (step, done, total, label), `step_end` (status, seconds, detail);
  - `llm`: phase `start` / `rewrite` / `done`, route, provider when known, seconds, tokens, cost;
  - `run_end`: status, exit code.

  Rules:
  - every text through `scrub()`;
  - each line written whole and flushed;
  - **a failure to write an event never changes the run** (fail-soft, tested);
  - the Saturday injury update writes events too.
- [x] 🤖 **Progress calls** in ingest, curate, graph, player and digest at the points the probe found. ✅
- [x] 🤖 **Prove nothing else changed:** after these changes, a rehearsal of 2026 week 5 (`nfl weekly rehearse --season 2026 --week 5 --steps game,player --at <published created_at> --fresh`) reproduces the published predictions, as in P10 (`weekly-ops` skill §5c). ✅ on week 4 (week 5 isn't published yet): old code vs new code identical (README §9)

### Backend
- [x] 🤖 **`preflight.py` → `GET /api/preflight`.** Checks, each with ok / fail / warn, a short value and a reason: ✅
  - calendar week (`run_pipeline(..., dry_run=True)` is read-only);
  - last week final (games and play-by-play, as the dry run reports it);
  - not published yet;
  - run lock free;
  - data drive found;
  - Neo4j up (warn only: the run starts it);
  - keys set (`NEO4J_PASSWORD`, `WANDB_API_KEY`, `OPENROUTER_API_KEY`; names only, the doctor's functions);
  - `seasons.current` matches the calendar.

  Run is allowed only when every blocking check passes. The reason strings match the mockup (for example "Week 4 isn't final in the data yet … Try again after 6:00 AM ET; the retry window runs to Wed 6:00 PM ET").
- [x] 🤖 **`runner.py`:** builds argv only from the three allowed kinds (README §5.3) and refuses anything else. ✅
  - `weekly`: `expect_week` must equal the calendar's week.
  - `resume`: only if the calendar's current week's last run failed, from that run's failed step.
  - `injury_update`: only if the week's main run exists and it's Saturday (ET) before the week's last kickoff.
  - `409 Conflict` if the lock is held.
  - The process is detached, with output to `{NFL_DATA_ROOT}/cache/control-room/logs/<run-id>.log`.
  - `GET /api/run/current` finds a run in progress after a page reload (from the lock note and the newest events file).
- [x] 🤖 **`events.py` → `GET /api/run/stream`:** server-sent events that tail the events file and the output log (scrubbed). They resume from `Last-Event-ID` (the `seq`) and send a heartbeat every 15 s. ✅ (`app/stream.py`)
- [x] 🤖 **`POST /api/run`:** needs the launch token and `Origin` (CR00 middleware). ✅
- [x] 🤖 **Tests:** ✅ `tests/app/test_app_run.py`, `tests/ops/test_events.py`; `tests/conftest.py` makes the real launcher refuse
  - the allowlist: every other kind or shape is refused, and a browser-sent week that differs from the calendar's is refused;
  - lock held → 409;
  - resume is only offered for the failed step;
  - injury-update timing;
  - re-attach;
  - SSE resume;
  - **the runner never starts a real process in pytest** (a fake launcher records the argv).

### Web
- [x] 🤖 **Pre-flight panel** as in the mockup: ✅
  - the checks list;
  - the big **Run week N** button, greyed out with the reason while blocked, and a "Check again" button;
  - the command line shown;
  - "Publishes the digest and moves W&B's production alias. Closing this tab doesn't stop a run."
- [x] 🤖 **Confirm dialog:** week (from the calendar), last week's status, the deadline, the steps, what gets published, the command, the `--expect-week` sentence. Focus starts on the confirm button; Escape cancels. ✅
- [x] 🤖 **Live mode:** ✅
  - the now-bar: step x of 9, what it's doing, progress, elapsed, about how long is left (from the last published week's step times);
  - the three views driven by the events: the ball moves within a step by its `progress`; map rings fill; timeline bars grow;
  - the log streams;
  - changing the view mid-run keeps the run's state.
- [x] 🤖 **Failure:** the failure banner (the step and its reason, "the digest didn't run, so nothing was published"), **Resume week N from <step>** with its command, and the fumble / red node / red bar in the views. ✅
- [x] 🤖 **Done:** ✅
  - a toast ("Week N published · checks passed …" or the failure);
  - a browser notification (permission asked once, on the first Run click);
  - the sidebar badge and header update;
  - the other tabs refresh (TanStack Query invalidation).
- [x] 🤖 **Saturday injury update:** ✅
  - the header button is enabled on Saturday (ET) once the main run exists, otherwise greyed out with the reason;
  - it has its own confirm dialog and live log;
  - when it finishes, a toast says whether it was material, and the Digest tab shows the addendum (CR01 already reads it).
- [x] 🤖 **`nfl app --rehearsal`:** ✅ (the newest published week; D103)
  - a banner says "Rehearsal mode";
  - the Run button runs `nfl weekly rehearse --season S --week N --out <rehearsals/control-room> --fresh` (only the rehearsal steps `ready`, `game`, `player`, `digest` run; the others show as skipped);
  - nothing live is touched (`ops/rehearsal.check_root` already refuses live folders).
- [x] 🤖 **Component tests** with a recorded events fixture (a real rehearsal's events file), including a failure and a resume. ✅ (`web/src/test/fixtures/*.jsonl`, checked by `liveInvariants.test.tsx`)

### Proving it
- [x] 🤖 **Rehearsal mode end to end** on 2026 week 5 (or the latest past week): every rehearsal step shows live in all three views, the events file lands under the rehearsal root, and the live files' sha256 hashes are unchanged (the same check as P10). ✅ 2026 week 4 in a real browser (as built below); the 50 live files' hashes unchanged
- [x] 🤖 **Failure and resume in rehearsal mode:** inject a failure in one step (a test hook that only works under `--rehearsal`); the app shows the failure and resumes from that step. ✅ `nfl app --rehearsal --fail-at player`: banner → Resume → finished
- [x] 🤖 **`--expect-week` live check** with `--dry-run` (exit 0) and with a deliberately wrong week (exit 6, nothing written). ✅ dry run exit 3 on the stale snapshot (expected), wrong week exit 6 dry and live, nothing written
- [x] 🤖 Runbook: a "Run the week from the control room" section (start the app, pre-flight, Run, what each state means, resume, Saturday, what stays in the terminal). Guide: the Run button and its safety, live views, events. Skill: the runner rules and events. ✅
- [x] 🤖 Sol review (Codex, read-only) of the runner, the allowlist, the token / Origin handling and the event writer. Fix the findings with tests. ✅ 10 findings (0 high), all fixed with tests; Sol's verification pass: 5 confirmed, 5 completed, 1 new, all fixed with tests (D104)
- [x] 🧑 **Rishi runs the first live Tuesday run from the app** (the first Tuesday after the steps above): start `uv run nfl app`, check pre-flight, press **Run week N**, confirm, and watch. Then check as usual: the digest's footer, `pipeline-<S>-wNN` in W&B, the Season log line (`uv run nfl season weeks --season 2026`). Log it in PROGRESS → Rishi-run steps (launched by Rishi, from the app). ✅ 2026-10-06, 20:27–20:44 ET: week 5 published from the app, every step ok, checks passed first time (PROGRESS → Rishi-run steps)
- [x] ✋ **Checkpoint:** close CR02. ✅ Rishi: "Mark CR02 as complete" (2026-10-06, after the week-5 run)

## As built (2026-10-06): deviations from the task list

Decisions: D103 (as built), D104 (Sol review: 10 findings, 0 high, all fixed; among them the injury update's own `--expect-week`, run ids in the lock note and SSE ids, a launch that survives an app restart, the Saturday before a Sunday-only slate). The spec's §9 has what the probe found, §10 the summary.

- **Events** add a `log` type (each console line: the app's log has times and survives a reload) and `run_start.earlier` (a resume's finished steps) to the planned types; the run records are a `records` step. Progress is mapped through `portion`s and throttled, never dropping a new kind of label.
- **The GLM call isn't streamed.** OpenRouter does stream reasoning on the current route (probe), but the call can't change for the UI (P09); the live view waits with the elapsed time and the route, and fills in the provider, time, reasoning count and cost on return. No live token counter (the mockup's is a deliberate difference).
- **Pre-flight's "last week final"** warns on a stale snapshot (the run's own exit 3 decides) and blocks only for 30 minutes after a not-ready run, with `retry_after` (the pitfall below, decided).
- **Resume adds `--promote`** (it finishes an `--auto` run); the runbook says so for terminal resumes too.
- **The runner** runs `python -m nflengine.cli` with the app's interpreter (not `uv run`), so the lock note's pid is the child's. Records and logs under `cache/control-room/`.
- **Rehearsal mode rehearses the newest published week** (the current week can't be rehearsed before its previous week is in the data); rehearsals now take their own lock, write events, and have a hidden `--fail-at STEP` (`nfl app --rehearsal --fail-at STEP` uses it once per app session) for the failure proof.
- **The replace retry** (the "Reading files during a run" pitfall): the pipeline's atomic writers retry `os.replace` for ~2 s (`fsutil.replace_file`).
- **A finished week's log** is the runs' own console from the events files when every sitting has one.
- **The SSE module is `app/stream.py`** (the spec said `events.py`).
- **The proof of "pipeline unchanged"** used week 4 (week 5 isn't published yet): old code (`git archive HEAD`) vs new code on the same inputs and pinned clock, identical predictions (README §9).
- **The 🧑 first live Tuesday run from the app** (Rishi at the kickoff: "close; live run on calendar") ran the same evening: Rishi pressed **Run week 5** on 2026-10-06 at 20:27 ET; every step ok, the digest published 47.5 h before kickoff with its checks passed first time, every W&B run tagged `launched-by:rishi` + `via:control-room`, the production aliases moved to week 5. One cosmetic fault found and fixed afterwards: the Pipeline tab showed the records step as 0 s (the run summary is stamped before the W&B logging); it now takes that step's time from the events file (8.7 s). The ✋ close: Rishi, "Mark CR02 as complete".

**The browser proof** (`nfl app --rehearsal --fail-at player` on port 8766, Playwright; screenshots in `.playwright-mcp/cr02/`, git-ignored):
1. The pre-flight in rehearsal mode (week 4, the folder free) → **Rehearse week 4** → the confirm dialog → "Run in progress", the toast, the now-bar "Starting the run".
2. Ready and game ran, then the hook failed the player step: the red banner with **Resume week 4 from player** and its command, the now-bar "Stopped · Failed · player · exit 1", the drive chart's FUMBLE at the player step, ingest / curate / ratings / graph / records "not in a rehearsal", the streamed log.
3. **Resume** → its confirm dialog → live: "Step 3 of 4 · Player model · refitting 10/23 · pressures-edge", the map's ring filling, the timeline's bar growing, the log streaming.
4. **The browser closed mid-run:** `/api/run/current` still said running and the events kept growing; the reopened page found the run again and followed it to the end.
5. "Final · Rehearsal done · digest written to the rehearsal folder · 1m 48s · exit 0"; ready and game "done in an earlier sitting".
6. The 50 live files under `runs/2026`, `reports/2026`, `features`, `models` had the same sha256 before and after.
7. Live mode (port 8767, nothing pressed): week 5's pre-flight on the real data root: calendar ✓ (15 games, Thu Oct 8 → Mon Oct 12), "Week 4 is final" a warning (1/16 in the snapshot from Sunday 3:18 AM), not published / lock / data drive / keys / season ✓, Neo4j a warning (recovering after the PC crash), **Run week 5** enabled, the dry run's log, the Saturday button greyed ("Needs this week's Tuesday run first").

**Mockup parity**, differences kept on purpose:
- no live token count in the GLM step (above); its line reads "GLM writing · waiting for the model · <elapsed> · route …";
- "Data drive: found" (the mockup showed the path, which is an environment value and never leaves the server);
- "Week 4 is final" can be a warning (the mockup had only ok and ✕);
- the mockup's simulation controls (1× / 4×, Skip to end) and its "Mockup controls" strip don't exist;
- a "Run in progress" chip replaces the Run button during a run; rehearsal mode says "Rehearse week N" and counts its own steps ("Step 3 of 4");
- a finished rehearsal shows in its own card, "The rehearsal", above the archive's run.

## Rishi-in-the-loop moments: what to look for

- **The first live run from the app:**
  - the week in the confirm dialog matches the calendar;
  - the views move step by step;
  - the GLM step keeps showing progress (elapsed) for its whole length;
  - when it finishes, the digest, W&B runs and records are exactly what a terminal run produces (it *is* the terminal command).
- **A blocked Tuesday:** if last week isn't final, the button is greyed out with the reason, and "Check again" later turns it on.

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| The week can't be wrong | `--expect-week` tests; a browser-sent week ≠ the calendar's is refused (test); the confirm dialog shows the server's week |
| Only the allowlisted commands can run | Runner tests (every other kind or argv refused); no endpoint accepts a command or a path |
| Live view works | Rehearsal-mode run shows every step in all three views; events file present; live files unchanged (hashes) |
| Failure and resume work | Rehearsal-mode injected failure → banner → resume from that step → finishes |
| Runs survive the browser | Close the tab mid-rehearsal and reopen: the app re-attaches and the run finishes |
| Pipeline unchanged | The week-5 rehearsal reproduces the published predictions after the event changes |
| First live run from the app | Week N published from the app; PROGRESS Rishi-run log; W&B `pipeline-2026-wNN` with `launched-by:rishi` |
| Gates | pytest, ruff, web lint / typecheck / test / build; Sol review fixed |

## Handoff

- Tuesdays (and Saturdays) can be run from the app. The terminal commands stay documented and work as before.
- CR03 adds the MLOps sections and season pages on top of the same records.

## Pitfalls / notes

- **The local schedule snapshot is stale on a Tuesday morning** (found in CR00: "1/16 final" for week 4 at 9 PM Monday, and still before the run). `--auto` refreshes it under the lock before deciding. Pre-flight must not report "last week isn't final" as a blocking fact from a stale snapshot: show the snapshot's date and either let "Check again" run a schedule-only refresh under the lock, or mark the check "unknown until the run refreshes it" and let the run's own exit 3 decide. Decide in the probe and log it.
- **Never let the browser choose the week or the command.** The server computes both. The browser's `expect_week` is only a cross-check, and a mismatch is refused.
- **The lock is the truth for "is a run going?".** Don't keep a second "running" flag that can go stale. Use `ops.lock.is_locked` and the lock note.
- **A live `--auto` run publishes a digest and moves W&B aliases.** Agents only press Run (or call `POST /api/run` for real) when Rishi asks in the current session, exactly as for the CLI (`weekly-ops` skill §1). Rehearsal mode is always fine.
- **The GLM call can take 3–40 minutes.** The SSE heartbeat and the elapsed timer keep the page alive. Don't time out the stream.
- **Keep the CLI's own output readable.** Progress events go to the events file, not as extra console lines.
- **Reading files during a run (found in CR01, D100).** The run writes `weekly_run.json` and its outputs as `tmp` → `replace(path)`. On Windows that replace fails (`PermissionError`) while another process has the target open without delete sharing or memory-mapped. CR01's readers keep every read short and never memory-map, which makes a clash very unlikely at today's 30–60 s polling. CR02 polls much harder while a run is live (`events.jsonl`, the step states), so either have the pipeline's `_write_state` and output writers retry the replace briefly on `PermissionError`, or read through a share-delete handle. Prove it with a rehearsal while the app polls.
