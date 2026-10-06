# CR00: Foundations

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Control room rows)

- **Depends on:** P10 (the pipeline and its run records are stable)
- **Unlocks:** CR01
- **Read first:**
  - [Control room README](README.md): all of it, especially §1 (the mockup), §2 (what was agreed), §4 (architecture) and §5 (safety rules);
  - the **mockup** ([artifact](https://claude.ai/artifact/1z7AapJzJwKusNtWzXYfQw), source in [`mockup/`](mockup/README.md));
  - the Security section of `CLAUDE.md`;
  - [weekly operations guide §3 and §5](../guides/weekly-operations.md) (the lock, the run records).

## Goal

`uv run nfl app` opens the control room on `http://127.0.0.1:8765`. The app shell matches the mockup:
- the sidebar lists the real 2026 weeks from D: with their status;
- the header and week tabs are in place (empty states for now);
- both themes switch and persist in dark, light and system mode;
- the backend's safety rules are in place and tested.

Nothing reads a whole week yet (CR01) and nothing runs (CR02).

## Scope

- **In:**
  - the backend skeleton (FastAPI, JSON rules, security middleware, the first two readers);
  - the `nfl app` command;
  - the `web/` app (Vite + React + TS, Tailwind with the mockup tokens, fonts, routing);
  - the app shell, shared components and chart primitives;
  - repo hygiene, the guide and the skill.
- **Out:** tab contents (CR01), running anything (CR02), W&B and season pages (CR03).

## Tasks

### Probe first
- [x] 🤖 Check the toolchain:
  - Node and npm versions (Node 24.15 was installed on 2026-10-05);
  - that `uv add fastapi uvicorn` works with the D: package cache (`link-mode = "copy"`);
  - that port 8765 is free.

  Record the versions in the README's §10.
- [x] 🤖 Check the in-process calls the backend will make, and that each one is read-only and fast:
  - `ensure_data_root()`;
  - `ops.calendar.load_schedules` + `plan_week`;
  - `ops.lock.is_locked`;
  - `weekly.read_state`;
  - `weekly.already_published`;
  - the doctor's check functions (set / not set; never values).
- [x] 🤖 Read the mockup source end to end: `styles.css` tokens, the shell markup in `app.js` (`renderSide`, `renderTop`, `renderTabs`, `settingsPop`).

### Backend skeleton (`src/nflengine/app/`)
- [x] 🤖 Dependencies: `fastapi` and `uvicorn` in `pyproject.toml` (with `uv add`), plus `httpx` (already present) for the test client.
- [x] 🤖 `server.py`: an app factory `create_app(paths, token, dev)`, with:
  - a JSON response class that turns NaN and infinity into `null` and writes dates as ISO strings with an offset;
  - one error envelope (`{"error": {"code", "message"}}`), with messages scrubbed;
  - static serving of `web/dist/` with a single-page fallback.
- [x] 🤖 Security middleware (README §5), each with tests (`tests/app/test_security.py`, FastAPI `TestClient`):
  - `Host` must be `127.0.0.1:<port>` or `localhost:<port>`;
  - `POST` needs the launch token header and a same-origin `Origin`;
  - no CORS except the Vite dev origin under `--dev`;
  - an endpoint list that grows with each phase, with a test that every response is scanned for key-shaped strings (reuse the patterns behind `scrub()`).
- [x] 🤖 First readers and endpoints:
  - `GET /api/meta`: app version, data root found yes / no, lock free / held, Neo4j up / down, the calendar's season and week;
  - `GET /api/weeks`: every `runs/<S>/week<NN>/` folder, plus the calendar's current week even when it has no folder yet.

  Status per week:
  - Published: the same rule as `already_published`;
  - Failed / Running: the lock is held by a run of that week;
  - Ready / Waiting for the current week: from the calendar's `previous_complete`.

  Tests run on fixture folders under `tmp_path`. A real-data test marked `integration` reads week 4.
- [x] 🤖 `nfl app` (Typer) with `--port 8765`, `--no-browser`, `--dev` and `--rehearsal` (the flag is wired here and used in CR02):
  - binds `127.0.0.1` only;
  - generates the launch token;
  - opens the browser;
  - if `web/dist/` is missing, stops with a clear message (`npm --prefix web ci && npm --prefix web run build`);
  - a CLI test that stubs uvicorn (never starts a real server in pytest).

### Web app (`web/`)
- [x] 🤖 Scaffold Vite + React + TypeScript:
  - ESLint (typescript-eslint, react-hooks) and Prettier;
  - Vitest + Testing Library;
  - scripts `dev`, `build`, `lint`, `typecheck`, `test`;
  - commit `package-lock.json`;
  - the dev server proxies `/api` to `127.0.0.1:8765`.
- [x] 🤖 Styling and fonts:
  - Tailwind CSS, with the mockup's tokens as CSS variables: `--bg`, `--panel`, `--raised`, `--line`, `--line-2`, `--ink`, `--ink-2`, `--ink-3`, `--accent`, `--on-accent`, `--accent-ink`, `--accent-wash`, `--s1`–`--s3`, `--ok`, `--warn`, `--err`, `--field`, `--field-2`, `--chalk`, `--ball`, `--console-*`;
  - for `[data-pal="turf"|"playbook"]` × `[data-mode="dark"|"light"]`, with Turf & Pylon as the default;
  - "System" follows `prefers-color-scheme` and changes live;
  - fonts from `@fontsource` (Barlow, Barlow Condensed, JetBrains Mono), so nothing loads from the internet.
- [x] 🤖 App shell, as in the mockup:
  - **sidebar:** brand, the gear, weeks from `/api/weeks` with badges, Season and System links, the footer (lock, Neo4j, data drive);
  - **Appearance popover:** theme cards and the System / Dark / Light switch, saved in `localStorage`, with every read and write wrapped in try / catch;
  - **header** per week;
  - **tabs:** Pipeline, Digest, Games, Players, Results, MLOps, Graph;
  - **routes:** `/week/:season/:week/:tab`, `/season/:season/scorecard`, `/teams`, `/models`, `/alerts`, `/health`. `/` goes to the calendar's current week, Pipeline tab;
  - designed empty states on every tab and page, saying what will appear and which phase brings it.
- [x] 🤖 Shared components ported from the mockup:
  - status chips (ok / warn / err / run / ghost / flat, always with an icon or label, never colour alone);
  - tiles, cards with headers, the notice, tables (sticky header, tabular numbers);
  - the segmented control, the tooltip, the confirm dialog (Radix).
- [x] 🤖 Chart primitives (SVG, following the dataviz rules: hover tooltip or crosshair, a legend for 2+ series, text in text colours, the series colours `--s1`–`--s3`): line chart, horizontal bars, sparkline. The rest come with the screens that use them (CR01, CR03). Component tests for the tooltip and the legend.
- [x] 🤖 Responsive behaviour as in the mockup:
  - the sidebar becomes a top bar under 900 px;
  - the tabs scroll sideways;
  - wide tables and diagrams scroll inside their own box;
  - no sideways scrolling of the page at 400 px.

### Repo hygiene and docs
- [x] 🤖 `.gitignore`: `web/node_modules/`, `web/dist/` (`.playwright-mcp/`, the browser-preview tool's output, was already added on 2026-10-05).
- [x] 🤖 Start `documentation/guides/control-room.md` (CLAUDE.md → Documentation): what the app is, how to start it (`uv run nfl app`), the safety rules in plain words, the screens built so far, how to change the theme, troubleshooting (port in use, `web/dist` missing, the data drive missing). List it in `documentation/README.md` and in CLAUDE.md's guide list.
- [x] 🤖 Create `.claude/skills/control-room/SKILL.md`: the layout, how to add a reader + endpoint + test, how to add a screen, the safety rules and their tests, the quality gates, known quirks. Add it to CLAUDE.md's skills table.
- [x] 🤖 README §10 "As built" for CR00; decisions for any deviation; PROGRESS.
- [x] 🤖 Sol review (Codex, read-only) of the security middleware and `nfl app`. Fix the findings with tests. *(8 findings, 0 high, all valid, all fixed with tests: D98.)*
- [ ] ✋ **Checkpoint:** Rishi runs `uv run nfl app`, sees the real weeks, switches themes and modes, and approves CR00.

## Rishi-in-the-loop moments: what to look for

- **The ✋ check:** the sidebar shows week 4 as Published and week 5 with its real state. Both themes look like the mockup in dark and light. The gear popover remembers the choice after a browser reload.

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| `nfl app` serves on localhost only | `uv run nfl app --no-browser`; `curl http://127.0.0.1:8765/api/meta` works; a request with `Host: example.com` gets 400 / 403; `--host` doesn't exist |
| Safety tests pass | `uv run pytest tests/app -q` (Host, Origin, token, no CORS, NaN → null, key-shaped strings) |
| Weeks list is real | `/api/weeks` lists 2026 week 4 (Published) and the calendar's current week; the sidebar shows the same |
| Shell matches the mockup | Side-by-side screenshots (shell, gear popover, both themes × both modes, phone width) with differences listed in §10 |
| Gates | `uv run pytest`, `uv run ruff check`, `npm --prefix web run lint`, `typecheck`, `test`, `build` all pass |
| Docs | The guide and the skill exist and are linked from `documentation/README.md` and CLAUDE.md |

## Handoff to CR01

- A running shell with routing and empty tabs, plus a backend pattern (reader → endpoint → test) to copy for every tab.

## Pitfalls / notes

- **Never start the real server or the real pipeline in pytest.** Stub uvicorn, and use fixture data folders (the P07 lesson: a CLI test that reached the real pipeline wrote records to D: and logged junk W&B runs). `tests/conftest.py` already sets `WANDB_MODE=disabled`.
- **Windows:**
  - use `127.0.0.1`, not `localhost`, for the bind address (IPv6 `::1` surprises);
  - npm scripts run under PowerShell in this repo's tools, so keep them cross-platform (no `rm -rf`; use `rimraf` or Node scripts).
- **Data rules:** the readers must use `ensure_data_root()` (it also respects `redirect_data_root` for rehearsals). No hard-coded `D:/` paths.
- **Don't import heavy modules** (LightGBM, SHAP, the graph driver) at app start-up. Import lazily inside the readers that need them, so the page opens fast.

## As built (2026-10-05)

**Built:**
- `src/nflengine/app/`: `serve.py`, `server.py`, `security.py`, `jsonsafe.py`, `status.py`, `readers/meta.py`, `readers/weeks.py`;
- `nfl app` in `cli.py`;
- `tests/app/` (41 tests, plus 1 integration test);
- `web/` (Vite 8, React 19, TS 6, Tailwind 4, Vitest 5, ESLint 10; 20 tests);
- the guide `documentation/guides/control-room.md`;
- the skill `.claude/skills/control-room/SKILL.md`;
- D97.

Versions: FastAPI 0.142, Starlette 1.7, uvicorn 0.54, Node 24.15, npm 11.7.

**Differences from the task list** (D97):
- **No CORS headers at all**, even under `--dev`: Vite's proxy forwards `/api` with `changeOrigin`, so the page and the API share an origin. The Vite origin is accepted only as the `Origin` of a state-changing request under `--dev`.
- **The launch token comes from `GET /api/session`** (same-origin only), not from injected HTML.
- **Extra security headers** (CSP with `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, same-origin opener and resource policies, `no-store` on the API). `/docs` and `/openapi.json` are off.
- **The Neo4j status** comes from a 30-s background ping (4.5 s per ping when Docker is down, measured).
- **The sidebar shows short badges** (Ready, Waiting, Incomplete …) with the full label in the tooltip and header ("Waiting on week 4" crushed the week name at 236 px).
- **The Saturday injury-update button** shows on the current week only (as in the mockup). The header's "Open in W&B" link moves to CR03, which reads W&B.
- **A not-ready run** (a lone failed `ready` step, or `run_summary.status == "not_ready"`) reads as not ready, not failed.
- **The task said "responses are scanned for key-shaped strings"**: built as a test over every GET endpoint, with sentinel keys set in the environment, that also asserts `scrub()` would change nothing.

**Verified:**
- `uv run pytest` 1,524 passed (41 new);
- `uv run ruff check` and `ruff format --check` clean;
- in `web/`, lint, typecheck, 20 tests and the build all pass;
- **the live server on the real data root:**
  - `/api/meta` gave week 5, the lock free, Neo4j down (Docker Desktop wasn't running), the data root found;
  - `/api/weeks` gave week 5 "Waiting on week 4" (the snapshot said 1/16 final at 9 PM Monday) and week 4 "Published · Checks passed after a rewrite";
  - `Host: evil.example` got 400, and a `POST` without the token got 403;
- **in the browser (Playwright):** `/` redirects to week 5's Pipeline tab; Turf & Pylon dark and Playbook light both render, and the gear remembers the choice; at 400 px the sidebar becomes a top bar with no sideways scroll; the only console error was a missing favicon (added).
- **Live files untouched** (`runs/2026/week04/` listing unchanged).

**Sol review** (`/sol-qa`, Codex `gpt-6.1-sol` xhigh, read-only, job `task-muw0gyvh-3xmmhh`): 8 findings, 0 high, all valid, all fixed with tests (D98).
- **Medium:**
  - unexpected exceptions logged raw and their 500 without headers;
  - a real `ready` failure read as "not ready";
  - a week without a slate read as "Ready".
- **Low:**
  - the data root's path in `/api/meta`;
  - a simulation's lock marked the live week as running;
  - Ctrl+C didn't exit 0;
  - the line-chart tooltip could run off-screen;
  - the tabs claimed ARIA tab behaviour.

Plus my own fix: the lock check reads the note before probing, so the status line can't make a starting run exit 4. After the fixes: `uv run pytest` 1,533 passed (50 app tests); ruff clean; web lint, typecheck, 20 tests and the build pass; the live server re-checked.

**Found:**
- **The schedule snapshot is stale before the Tuesday run.** That's a pre-flight design point for CR02, added to its pitfalls.
- **Week 5's Eagles at Jaguars at Tottenham** is listed by nflverse as a Jaguars home game, not a neutral site. The calendar doesn't tag it international, and the game model gives the Jaguars home-field advantage. This is a modelling question for Rishi, outside CR00.
