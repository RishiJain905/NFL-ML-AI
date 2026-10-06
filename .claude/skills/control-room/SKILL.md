---
name: control-room
description: How the control room (the local web app for the weekly pipeline, documentation/control-room/, CR00-CR03) is built, run, tested and extended. Use when changing anything under src/nflengine/app/, web/ or tests/app/, adding an endpoint, reader or screen, touching the safety middleware, the theme tokens, the pipeline views, the Run button / runner (CR02) or the W&B reads (CR03), or when `nfl app` misbehaves.
---

# Control room (CR00+)

The spec is `documentation/control-room/README.md`, the phases are `documentation/control-room/CRxx-*.md`, and the visual spec is the approved mockup (link in the README; source in `documentation/control-room/mockup/`). The plain-language guide is `documentation/guides/control-room.md`. Decisions: D94–D102.

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

## 2. Layout
| Path | What |
|---|---|
| `src/nflengine/app/serve.py` | `nfl app` (`--port`, `--no-browser`, `--dev`, `--rehearsal`); exit 0 / 1 port in use / 2 no build |
| `src/nflengine/app/server.py` | `AppSettings` (every dependency injectable: data root, schedules, clock, season, service status) and `create_app` |
| `src/nflengine/app/security.py` | `LocalOnlyMiddleware` (plain ASGI): Host check, Origin + token, security headers / CSP |
| `src/nflengine/app/jsonsafe.py` | `SafeJSONResponse`: NaN / inf → null, dates ISO, numpy → plain |
| `src/nflengine/app/status.py` | `ServiceStatus`: a Neo4j ping every 30 s on a daemon thread |
| `src/nflengine/app/readers/` | One module per area: `meta.py`, `weeks.py` (week list, header, team info), `common.py` (read helpers + `clean()`), CR01's `pipeline.py`, `digest.py`, `games.py`, `players.py`, `results.py`, `graph.py`; CR03's pages next |
| `web/src/components/pipeline/` | The step model (`model.ts`: `STEP_DEFS`, `toModel`), the three views (`DriveChart`, `PipelineMap`, `Timeline` via `PipelineView`), the picker (`ViewPicker`, `views.ts`: `cr.vizByWeek`), `LogPanel` |
| `web/src/pages/week/` | One component per week tab (`PipelineTab`, `DigestTab`, `GamesTab`, `PlayersTab`, `ResultsTab`, `GraphTab`) and `WeekEmpty` (empty / loading / error states) |
| `web/` | Vite + React + TS. `src/routes.tsx`, `src/components/`, `src/pages/`, `src/charts/`, `src/theme/`, `src/api/`, `src/styles/` |
| `tests/app/` | pytest. Helpers in `tests/app/app_helpers.py` and a complete synthetic week in `tests/app/week_fixtures.py` (`write_full_week`, `write_curated`, `write_scoreboard`; imported directly, no conftest in subfolders). Real-data parity: `tests/app/test_parity.py` (`-m integration`) |

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
- **Every free text through `clean(text, paths, limit)`**: the data root's path becomes the relative path below it (step details name `D:\nfl-ml-data\...`), then `scrub()`; NaN becomes null. W&B run URLs are pulled out with `wandb_urls()` and shortened in logs with `without_wandb()`.
- **Allowlists, not blocklists**, for files with sensitive parts: `raw_llm_output.json` is copied field by field (`readers/digest._call`); `graph_results.json` query rows stay on the server.
- **The partial-week rule:** a week can have `weekly_run.json` without `run_summary.json` (2026 week 4), a summary that covers only some steps (simulations), several sittings, or a step left `running` by a dead run. Readers take each step's latest state from `weekly_run.json`, seconds from the summary when present, and treat a `running` step without the lock as failed ("interrupted"). "Published" is always `weekly.already_published`.
- **Reuse the pipeline's own code for any rule**: hit / miss and Brier via `report_card.grade_games` / `week_metrics`, the shown projection via `digest.players.center` (except `kind == "prob"`: no quantiles, use `p_ge1`), the slate via `ops.calendar`. Read published numbers (the next week's `report_card`) rather than recomputing them; recompute only what isn't stored, and flag disagreement (`consistent`, D101).
- **Schedules in tests have no `kickoff_utc`**: call `ops.calendar.with_kickoffs(sched)` (idempotent) before using it.
- **Field names**: never `key`, `*token*`, `*auth*` (`author`!), `*secret*`, `*password*`, `*credential*` — `scrub()` masks `"<name>": value`. The step's name is `step`; token counts are `usage.{prompt, completion, reasoning}`.
- **Bad path params** come back as a 422 `bad_request` with fixed text (`RequestValidationError` handler): FastAPI's default echoes the input.
- **Test every new reader** on `week_fixtures.write_full_week` (it plants raw LLM text, a credential-shaped detail and the root's own path), add its URL to `all_week_urls()` in `test_app_week.py` (the leak scan and the "nothing written" hash test run over it) and a parity check in `test_parity.py`.

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
- **Playwright screenshots:** files must go under the repo (`.playwright-mcp/`, git-ignored), not the scratchpad. A screenshot right after `navigate` (or a reload started from `browser_evaluate`) can catch the page still loading even when a `wait_for` just passed: the tool races the page load, the app is fine (the DOM had the content before and after). Check with `browser_evaluate` (`document.body.innerText`) and take the shot again. Click by script when a selector races the render.

## Improving this skill
Add new quirks, patterns or test helpers here in the same commit as the work that taught them, and note it in the PROGRESS session log. Rules belong in CLAUDE.md and the spec; the operator's how-to is the guide.
