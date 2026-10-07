---
name: control-room
description: How the control room (the local web app for the weekly pipeline, documentation/control-room/, CR00-CR03, complete) is built, run, tested and extended. Use when changing anything under src/nflengine/app/, web/ or tests/app/, adding an endpoint, reader or screen, touching the safety middleware, the theme tokens, the pipeline views, the Run button / runner (CR02), the MLOps tab, the season pages or the cached W&B reads (CR03), or when `nfl app` misbehaves.
---

# Control room (CR00+)

The spec is `documentation/control-room/README.md`, the phases are `documentation/control-room/CRxx-*.md`, and the visual spec is the approved mockup (link in the README; source in `documentation/control-room/mockup/`). The plain-language guide is `documentation/guides/control-room.md`. Decisions: D94–D106. The track is complete (CR03 closed it); later ideas are in the guide §8.

## 1. Ground rules
- **Design changes go mockup first:** rebuild and republish the mockup, update the spec, then the code. Keep `web/src/styles/tokens.css` and `app.css` in step with `mockup/styles.css`.
- **Readers only read.**
  - Everything under the data root is read-only to `src/nflengine/app/readers/`.
  - Paths come from `ensure_data_root()` (which also honours `redirect_data_root` in rehearsals), never a hard-coded `D:/`.
  - DuckDB connections are `read_only=True`.
  - Free text passes through `ops.summary.scrub()`.
- **The server binds 127.0.0.1 only.** Every request needs `Host: 127.0.0.1:<port>` / `localhost:<port>`, and every state-changing request needs a same-origin `Origin` plus `X-CR-Token` (`security.py`).
  - No CORS, ever. Dev mode uses Vite's proxy (`changeOrigin: true`).
  - No endpoint takes a file path or a command.
  - Keys never reach the browser. Health-style checks use the doctor's set / not set functions, and the env file is never opened.
- **Running the pipeline from the app (CR02) follows the `weekly-ops` skill §1.** Agents press Run, or call `POST /api/run` for real, only when Rishi asks in the current session. `--rehearsal` is always fine.
- **The browser sends a kind and its expected week, never a week to run or a command** (README §5.3). The server builds the argv in `runner.build_launch` from three kinds and its **own** pre-flight (week, failed step); nothing from the request body goes into it. New kinds need Rishi, a decision and a Sol review.

## 2. Layout
| Path | What |
|---|---|
| `src/nflengine/app/serve.py` | `nfl app` (`--port`, `--no-browser`, `--dev`, `--rehearsal`); exit 0 / 1 port in use / 2 no build |
| `src/nflengine/app/server.py` | `AppSettings` (every dependency injectable: data root, schedules, clock, season, service status) and `create_app` |
| `src/nflengine/app/security.py` | `LocalOnlyMiddleware` (plain ASGI): Host check, Origin + token, security headers / CSP |
| `src/nflengine/app/jsonsafe.py` | `SafeJSONResponse`: NaN / inf → null, dates ISO, numpy → plain |
| `src/nflengine/app/status.py` | `ServiceStatus`: a Neo4j ping every 30 s on a daemon thread |
| `src/nflengine/app/readers/` | One module per area: `meta.py`, `weeks.py` (week list, header, team info), `common.py` (read helpers + `clean()`), CR01's `pipeline.py` (+ CR02's `events_log`), `digest.py`, `games.py`, `players.py`, `results.py`, `graph.py`; CR03's `mlops.py` (Health, the W&B chart cards from local files, Artifacts), `season.py` (Scorecard, Teams), `models.py` (Models, `CARDS` id → file), `alerts.py` (Alerts, the Season log's notes) |
| `src/nflengine/app/wandb_api.py` | CR03: `WandbReader.get(name, fetch, ttl, refresh)` (memory + `cache/control-room/wandb/<name>.json`, back-off, stale fallback, `WandbState`), `peek`; fetchers `fetch_week_runs`, `fetch_artifacts`, `fetch_used`, `fetch_run_summary`; `reason_for`, `job_of` |
| `src/nflengine/app/health.py` | CR03: `SystemChecks` (the doctor's checks on a daemon thread, 5-minute life, `snapshot(refresh, wait)`), `get_health` |
| `src/nflengine/app/preflight.py` | CR02: `get_preflight` (eight checks, the Run / Resume / injury-update gates, the confirm facts, the dry run's log built from the plan), `recent_not_ready`, `saturday_window`, rehearsal mode's target and failed step |
| `src/nflengine/app/runner.py` | CR02: `build_launch` (the allowlist), `Runner.start` (409 / 403 / argv / detached launch / record), `Runner.current` (the lock → the record or a terminal run → the events file), `default_launcher` (Windows flags), `read_events`, `public()` (drops `_events`) |
| `src/nflengine/app/stream.py` | CR02: `run_stream` (tails the events file: `run` messages with `id: seq`, heartbeats, `end`), `clean_event`, `Tail` (complete lines only) |
| `web/src/api/stream.ts` | `useRunStream(active, runKey)` (EventSource, dedup by seq, batched), `eventsToLog`, `eventClock` |
| `web/src/components/run/` | CR02 controls: `PreflightPanel`, `RunConfirm`, `FailureBanner` (+ Resume), `InjuryUpdate` (button + live log card), `RunUiProvider` (toasts, session state), `RunWatcher` (end toasts / notifications / `invalidateAfterRun`), `RehearsalBanner`, `notify.ts`, `runUi.ts` |
| `web/src/components/pipeline/` | The step model (`model.ts`: `STEP_DEFS`, `toModel`), the three views (`DriveChart`, `PipelineMap`, `Timeline` via `PipelineView`), the picker (`ViewPicker`, `views.ts`: `cr.vizByWeek`), `LogPanel`; CR02's live mode: `live.ts` (`foldRun` events → per-step fold, `projectRun` fold + clock → `PipelineModel`), `NowBar`, `LiveRun` (the live block; owns the stream), `liveFixtures.ts` (synthetic runs, `parseEventsJsonl`) |
| `web/src/pages/week/` | One component per week tab (`PipelineTab`, `DigestTab`, `GamesTab`, `PlayersTab`, `ResultsTab`, `GraphTab`) and `WeekEmpty` (empty / loading / error states) |
| `web/` | Vite + React + TS. `src/routes.tsx`, `src/components/`, `src/pages/`, `src/charts/`, `src/theme/`, `src/api/`, `src/styles/` |
| `tests/app/` | pytest. Helpers in `tests/app/app_helpers.py` (`make_client(..., **AppSettings overrides)`: keys are reported set by default, the stream polls every 10 ms, **W&B is offline** (`offline_wandb()`) and the Health checks are fakes (`fake_checks()`) unless a test passes its own) and a complete synthetic week in `tests/app/week_fixtures.py` (`write_full_week`, `write_curated`, `write_scoreboard`; imported directly, no conftest in subfolders). Real-data parity: `tests/app/test_parity.py` (`-m integration`). CR02: `test_app_run.py` (`FakeLauncher`, `write_events`, `story`, `parse_sse`). CR03: `test_app_cr03.py` on `cr03_fixtures.py` (`write_records`: week 5-style run summary + manifest + quality file + events + history + dashboard.json; `write_season_scorecard`, `write_ratings`, `write_backtests`; `FakeApi` / `fake_wandb_world()` answering the W&B calls), parity vs W&B in `test_parity_cr03.py` (`-m integration`) |

## 3. Commands
```powershell
uv run nfl app                       # serves web/dist + API on 127.0.0.1:8765
uv run nfl app --dev; npm --prefix web run dev   # API + Vite (http://localhost:5173)
npm --prefix web ci; npm --prefix web run build  # install exactly / build web/dist
uv run pytest tests/app -q           # backend; -m integration reads the real data root
npm --prefix web run lint; npm --prefix web run typecheck; npm --prefix web test
```

## 4. Adding an endpoint
1. A reader in `readers/<area>.py` that takes `paths` (and `plan` / `now` when needed) as arguments. Keep it pure enough to test on fixture folders.
2. The route in `server.py`. Return `SafeJSONResponse`. A `DataRootError` becomes a 503 `data_root_missing` by itself.
3. Tests in `tests/app/test_app_<area>.py` with `make_client(tmp_path, ...)` and `write_week(...)`, plus a real-data parity test marked `integration`.
4. Add the GET path to `SCANNED_GETS` in `test_app_security.py`. The scan fails if a response contains a sentinel key or anything `scrub()` would change, so don't name JSON fields `*token*`, `*key*`, `*secret*`, `*password*` or `*auth*`.
5. Add the TypeScript type in `web/src/api/types.ts` and a `useQuery` hook in `web/src/api/client.ts`.

## 4a. Reader patterns (CR01)
- **One short read per file, never a held handle** (`readers/common.py`): `read_json`, `read_text` (bytes decoded as UTF-8, **no newline translation**: the published digest has CRLF and the Digest tab must be byte-identical), `read_parquet(..., memory_map=False)`, `curated(paths, table)`. On Windows the run's `tmp.replace(path)` fails while another process has the target open or memory-mapped. **No DuckDB connection to `nfl.duckdb`** from the app (the curate step replaces it): read `curated/<table>.parquet` (D100).
- **The app's data root never creates folders** (`server._read_only_root` = `ensure_data_root(create_dirs=False)`): readers treat a missing folder as "no data". The server registers the root on every request (`_Context.paths` → `use_data_root`), so `clean()` strips it even when a reader passes `paths=None`, **before** capping the text; every week answer also gets a final `strip_root` pass (D102).
- **A run summary counts only if it's current** (`common.summary_is_current`: no step started or finished after it). A retry that dies before its records leaves an earlier sitting's summary behind. A step left `running` without the lock is interrupted (failed), in the sidebar and the Pipeline tab alike.
- **Watch lists can be heuristic** (P04's fallback, `source == "heuristic"`): no quantiles, no `kind`; never call `center()` on them unguarded.
- **A game predicted after its kickoff is never graded** (the report card's rule): no hit / miss, label "predicted after kickoff" (D102).
- **Every free text through `clean(text, paths, limit)`** (or `paths.relative_paths`): the data root's path becomes the relative path below it, and a **partial** root right before a cap's ellipsis is dropped (`paths.strip_partial_root`: the pipeline caps step details at 400 characters before they're stored, CR02's Sol verification) (step details name `D:\nfl-ml-data\...`), then `scrub()`; NaN becomes null. W&B run URLs are pulled out with `wandb_urls()` and shortened in logs with `without_wandb()`.
- **Allowlists, not blocklists**, for files with sensitive parts: `raw_llm_output.json` is copied field by field (`readers/digest._call`); `graph_results.json` query rows stay on the server.
- **The partial-week rule:** a week can have `weekly_run.json` without `run_summary.json` (2026 week 4), a summary that covers only some steps (simulations), several sittings, or a step left `running` by a dead run. Readers take each step's latest state from `weekly_run.json`, seconds from the summary when present, and treat a `running` step without the lock as failed ("interrupted"). "Published" is always `weekly.already_published`.
- **Reuse the pipeline's own code for any rule**: hit / miss and Brier via `report_card.grade_games` / `week_metrics`, the shown projection via `digest.players.center` (except `kind == "prob"`: no quantiles, use `p_ge1`), the slate via `ops.calendar`. Read published numbers (the next week's `report_card`) rather than recomputing them; recompute only what isn't stored, and flag disagreement (`consistent`, D101).
- **Schedules in tests have no `kickoff_utc`**: call `ops.calendar.with_kickoffs(sched)` (idempotent) before using it.
- **Field names**: never `key`, `*token*`, `*auth*` (`author`!), `*secret*`, `*password*`, `*credential*` — `scrub()` masks `"<name>": value`. The step's name is `step`; token counts are `usage.{prompt, completion, reasoning}`.
- **Bad path params** come back as a 422 `bad_request` with fixed text (`RequestValidationError` handler): FastAPI's default echoes the input.
- **Test every new reader** on `week_fixtures.write_full_week` (it plants raw LLM text, a credential-shaped detail and the root's own path), add its URL to `all_week_urls()` in `test_app_week.py` (the leak scan and the "nothing written" hash test run over it) and a parity check in `test_parity.py`.

## 4b. Run control (CR02)
- **The lock is the truth for "is a run going?"** (`ops.lock`; rehearsal mode: `ops.rehearsal.lock_file(<data root>/rehearsals/control-room)`). The note carries the run's `run_id`: a held lock is an app launch if one of the app's records (`cache/control-room/runs/<run-id>.json`) has that id, else a terminal run whose events file is `<week>/events/<run_id>.jsonl`. **Never match by pid** (Windows reuses them; Sol review). Before the child takes the lock, the in-memory child (or, after an app restart, `Runner._starting`: the newest launch whose process is alive, by pid **and** creation time, within 5 minutes) counts as running.
- **The child is `python -m nflengine.cli …` with the app's interpreter**, so the lock note's pid is the child's own (with `uv run` it would be uv's). Windows flags: `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, plus `CREATE_BREAKAWAY_FROM_JOB` when the job allows (fallback without). Env additions are display-only (`PYTHONIOENCODING`, `PYTHONUNBUFFERED`, `COLUMNS`).
- **Statuses** (`RunInfo.status`): `run_end`'s, else from the exit code (`EXIT_STATUS`; 0 → `already_done` for `weekly`), `interrupted` (events without a `run_end`), `unknown`. `output_tail` (scrubbed, root removed) only when the run ended without a `run_end`.
- **Errors:** 403 `not_allowed` (a closed gate, or `expect_week` ≠ the server's week), 409 `locked` / `running`, 422 for any other body (`RunBody`, `extra="forbid"`; the handler's text names the shape, never the input).
- **SSE:** ids are `<run id>:<seq>`; a new EventSource replays from seq 1; `Last-Event-ID` resumes only for the same run (another run's cursor replays from the start; `parse_cursor`), `?after=N` resumes the current run; the client (`useRunStream`) keeps one run's events only (the key's run id, or the first one seen); the generator ends after `run_end`, or two quiet polls after the run is no longer going, or when another run takes over (`end` with the run as first seen, completed from its own `run_end`). It must end for `TestClient` (a test with a run that never ends hangs).
- **Pre-flight "last week final" never blocks on a stale snapshot** (D103): warn, except 30 minutes after a not-ready run. Neo4j down warns. Keys by name only (`keys_set` is injectable; tests never read the env file).
- **Tests:** `tests/conftest.py` replaces `runner.default_launcher` with one that raises: give `Runner(launcher=FakeLauncher())`. Same-process `run_lock` makes `is_locked` true (the OS refuses a second handle), so a test can hold the lock in a thread to play a terminal run.
- **Web:** `useRunStream` + `FakeEventSource` (`web/src/test/fakeEventSource.ts`: `emitRuns`, `emitEnd`; the hook batches every 50 ms); `mockApi` takes `'POST /api/run'` keys, a function of the body, and `withStatus(status, body)`. A recorded events file dropped into `web/src/test/fixtures/*.jsonl` is checked automatically by `liveInvariants.test.tsx` (order, no NaN, ball / time / steps only move forward, all three views render at every prefix).

## 4c. MLOps and the season pages (CR03)
- **Local first, W&B for links / versions / aliases / lineage only.** Every chart number comes from the file W&B was fed from (`readers/mlops.py` cards, the Scorecard); `test_parity_cr03.py` compares each with W&B for 2026 week 5. A new W&B-backed number needs a local source or a stated reason, and a parity test.
- **W&B reads** go through `ctx.wandb.get(name, fetch, ttl=…, refresh=…)` (`app/wandb_api.py`): fetchers take `(api, "<entity>/<project>")`, return plain JSON, pass every string through `_s()` (scrub) **before** it's cached, and never write to W&B. The client is `tracking.read_api` (the key goes from the settings into the client, never into the process environment). A failure → `reason_for(exc)` (fixed text + the type name, never the message), a 60 s back-off, the last cache marked `stale`, or `available: false`. Missing artifact collections ("can't find …") are "no versions yet", not a failure.
- **TTLs:** live week + artifacts 10 min; a published past week's run list a day; a finished run's lineage and `ratings-eval` a week. `?refresh=1` skips once (the UI's Try again / Refresh: `refreshWandb(path)` + `refetch()`).
- **The week's runs** = tags `season:S` + `week:NN`, **except the player scoreboard**, tagged with the week it grades: week N takes `scoreboard-S-w(N−1)` (`fetch_week_runs`). The newest run per job is `current`; re-runs fold away.
- **The live Scorecard is the dashboard's own builder** (`ops.dashboard.build_dashboard_data`) for `dashboard.json`'s `last_run_week`, with **every input passed in** (scorecard, scoreboard, history, games, `preds`) so nothing is read with a memory map (D100, D105). Pass empty frames, never `None`, or it reads from disk itself.
- **Prediction files' player group column is `group`** (`pgroup` = position); the scoreboard's is `position_group`. The scoreboard card keeps only the graded week's **live** rows once any exist (P08's new targets have walk-forward rows only until their first live week), matching the W&B scoreboard run.
- **The quality file is overwritten by every curate**: show its checks only when its `run_at` equals the run summary's `quality.run_at` (or falls in the curate step's window for weeks before records); otherwise `match: "later"` and the summary's counts.
- **Health** uses the doctor's check functions (never `check_data_root`, whose detail names the root's path) on `SystemChecks`' thread; tests inject `fake_checks()`. Variables come from `settings.REQUIRED_ENV_VARS` / `OPTIONAL_ENV_VARS` through `AppSettings.keys_set` (names only).
- **Model cards** are served by id from `readers/models.CARDS` (a path under `documentation/`), `kind` card / guide; the route's id pattern is `^[a-z0-9-]{1,40}$`.
- **Alerts' notes** are the indented lines under `- week NN:` in PROGRESS.md's `## Season log (<season>)`; the app never writes them.
- **"First can fire" weeks come from the evaluator's own inputs and rules** (`readers.alerts.first_weeks`, Sol review): run week W sees graded weeks < W; the checks window counts the run's own verdict; calibration counts graded games, then the schedule's slates; player signals per group and metric. Never a flat "first run + n".
- **Sol's CR03 rules for W&B reads:** strings lose the data root before caching (`_s` → `clean`); a cache envelope is validated (`_valid_entry`) or treated as a miss; a failure is remembered per answer until a good fetch (`_failed_names`), and the back-off runs from the failure's time; an answer built from several reads reports one merged state (`merge_states`), so a fallback is never shown as fresh; a refresh reaches every read behind the answer. The `wandb` library itself writes scratch folders in the OS temp folder and a debug log under `%LOCALAPPDATA%\wandb\logs` (outside the data root, like `nfl doctor`): don't redirect it through environment variables, which the runner's children inherit (D106).
- **The scans:** `test_cr03_endpoints_scrubbed_with_a_leaky_wandb` (a key-shaped W&B tag) and `test_cr03_reads_write_only_the_wandb_cache`. A masked value at the very end of a JSON string isn't scrub-idempotent (`api_key=***"` becomes `***`): plant leaky fixture text with a word after the value.

## 5. Adding a screen
- Port the markup and classes from the mockup's `app.js` function for that screen. The classes already exist in `web/src/styles/app.css`.
- Charts: use or extend `web/src/charts/` (dataviz rules: series colours `--s1`–`--s3`, text in text tokens, a legend for 2+ series, hover and focus tooltips).
- Tests: Vitest + Testing Library, using `mockApi()` and `renderApp()` from `web/src/test/utils.tsx`. jsdom lacks `matchMedia` and `ResizeObserver`; `src/test/setup.ts` provides them, and `setSystemDark()` flips the OS preference. Tab fixtures are small and synthetic (`web/src/pages/week/weekFixtures.ts`): never paste real API dumps into the repo.
- A week tab is `XTab({ season, week, isCurrent, lastPublishedWeek })` with its `useX(season, week)` hook; `status: 'none'` (or `not_graded`, `slate`) renders `WeekEmpty`, which links the same tab of the last published week for the current week.
- The pipeline views take a `PipelineModel` and must update in place (no `key` that changes with the model): CR02 feeds the same model from the live stream.

## 6. Known quirks
- **Starlette 1.x:** `TestClient` wants `httpx2` (a dev dependency); routes registered after the `/api/{rest:path}` catch-all are shadowed, so register real routes before it.
- **`TestClient` must use `base_url="http://127.0.0.1:8765"`**, or every request fails the Host check (`testserver`).
- **A "not ready" run records its `ready` step as `failed`** (`weekly.run_weekly`), but so does a real failure inside the step (exit 1). `readers/weeks.py` trusts `run_summary.status` when it exists; for older weeks only the not-ready wordings count (`NOT_READY_TEXT`). A current week without a slate is "Waiting on the schedule", never "Ready" (D98).
- **The schedule snapshot can be stale on a Tuesday morning** (1/16 final before the run refreshes it). The sidebar says "Waiting"; CR02's pre-flight must not treat that as final.
- **Step times before P07 are naive local time** (`readers/meta.to_local_iso` adds the machine's offset).
- **Unexpected exceptions are handled in `LocalOnlyMiddleware`** (scrubbed log line, 500 envelope with headers, never re-raised). Don't add a FastAPI `exception_handler(Exception)`: Starlette runs it in `ServerErrorMiddleware`, outside the safety layer (Sol review).
- **Never return `paths.root` or any env-derived value** (the data root path is NFL_DATA_ROOT's value). Messages about a missing drive are fixed text (`server.NO_DRIVE`).
- **The lock check reads the note before probing** (`readers/meta.lock_dict`): probing takes the lock for an instant and could make a starting run exit 4.
- **Week tabs are route links** (`NavLink` → `aria-current="page"`), not ARIA tabs.
- **The browser-preview tool writes `.playwright-mcp/`** in the repo root (git-ignored). Use `127.0.0.1` URLs; `file://` is blocked.
- **React Router's `NavLink` sets `aria-current="page"`.** The CSS matches both `"page"` and the mockup's `"true"`.
- **Radix popover content is positioned by a wrapper.** The mockup's `.popover` absolute positioning is overridden with `position: static` on the content.
- **Looking at a graded Results tab before the next live week exists:** serve the app over P07's simulations with a `DataPaths` subclass whose `runs` is `runs/digest-backtests` (a scratch script; `AppSettings(data_root=..., now=..., port=8766)` + `uvicorn.run`). Read-only, nothing to clean up.
- **The calendar moves to week N+1 at week N's last kickoff**, so "the injury update's window closed" is effectively "Needs this week's Tuesday run first" for the new week (the test pins that).
- **Rehearsal mode rehearses the newest published week**, not the calendar's: the current week can't be rehearsed before its previous week is in the data (`ready` would fail). Its result lives in `rehearsals/control-room`, never in the archive; the Pipeline tab shows it in its own "The rehearsal" card from the stream replay.
- **Playwright screenshots:** files must go under the repo (`.playwright-mcp/`, git-ignored), not the scratchpad. A screenshot right after `navigate` (or a reload started from `browser_evaluate`) can catch the page still loading even when a `wait_for` just passed: the tool races the page load, the app is fine (the DOM had the content before and after). Check with `browser_evaluate` (`document.body.innerText`) and take the shot again. Click by script when a selector races the render.

- **Real JSON for UI workers:** dump every endpoint on the real data root with a scratch script (`TestClient(create_app(AppSettings()))`, `app.state.ctx.checks.snapshot(wait=60)` first) into the scratchpad and point the workers at it (read only, never committed); in CR03 the real data changed four UI decisions (a 0–1 `pct`, long tooltip text, grouped drift chips, card-to-card links).

## Improving this skill
Add new quirks, patterns or test helpers here in the same commit as the work that taught them, and note it in the PROGRESS session log. Rules belong in CLAUDE.md and the spec; the operator's how-to is the guide.
