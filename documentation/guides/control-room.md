# The control room: a local web app for the weekly pipeline

The control room is a small website that runs only on this computer. On Tuesdays it will be where you press **Run**, watch the pipeline work, then read the digest and check last week's results. It also shows the MLOps side (run health, W&B runs, artifacts) and season-long views.

It's being built in four phases, CR00–CR03. The spec, the phases and the approved mockup are in [`documentation/control-room/`](../control-room/README.md). This guide explains what's built so far and how to use it. It grows with each phase.

**Built so far: CR00 (foundations) and CR01 (the week archive).**
- The app starts with one command.
- The sidebar shows the season's weeks with their real status.
- Each week has its header and seven tabs. **Six of them read the week's real files** (Pipeline, Digest, Games, Players, Results, Graph; §2a). MLOps comes in CR03.
- The Pipeline tab draws the run in three views (drive chart, pipeline map, timeline), picked per week.
- Both themes work in dark and light mode.
- The safety rules are in place and tested. Nothing in the app runs a command yet (CR02).

The season pages still show placeholders that say which phase fills them.

## 1. Start it

**Once, after cloning or after a web change:** build the web app. Node.js 24 and npm 11 are installed on this machine.

```powershell
npm --prefix web ci          # install the exact package versions from web/package-lock.json
npm --prefix web run build   # writes web/dist/ (about 140 KB of script, gzipped, plus the fonts)
```

**Every time:**

```powershell
uv run nfl app               # opens http://127.0.0.1:8765/ in your browser; Ctrl+C stops it
uv run nfl app --no-browser  # same, without opening a tab
uv run nfl app --port 8800   # another port (1024-65535)
```

- `/` takes you to the calendar's current week, Pipeline tab.
- Leave the terminal open while you use the app. Closing the browser tab doesn't stop anything.

| Exit code | Meaning |
|---|---|
| 0 | Stopped normally (Ctrl+C) |
| 1 | The port is already in use (is another control room running?) |
| 2 | `web/dist` is missing: run the two build commands above, or use `--dev` |

**Working on the web code:** run the API and the Vite dev server side by side. Vite reloads the page as you edit.

```powershell
uv run nfl app --dev           # API only, on 127.0.0.1:8765
npm --prefix web run dev       # the page on http://localhost:5173/ (Vite forwards /api to 8765)
```

`--rehearsal` is wired but does nothing yet. From CR02 it will point the Run button at `nfl weekly rehearse`, never the live week. While it's on, a banner says so.

## 2. What you see

**Sidebar:**
- The brand and the **gear** (appearance, §3).
- The **season's weeks**, newest first. The calendar's current week is always listed, even before it has a run folder.
- **"Before go-live"**: the weeks before the first live run (2026: weeks 1–3, which only have walk-forward rows), shown greyed out.
- Links to the season pages (Scorecard, Teams & rankings, Models, Alerts) and Health.
- A status footer:
  - **Run lock:** free / held; hover to see the command holding it;
  - **Neo4j:** up / down / checking;
  - **Data drive:** free space;
  - **Now:** the time in ET.

**A week's status** follows the pipeline's own rules (`src/nflengine/app/readers/weeks.py`):

| Badge (sidebar) | Header label | When |
|---|---|---|
| Running | Running | The weekly-run lock is held by a run of this week (`--season S --week N`, or an `--auto` run of the calendar's week) |
| Published | Published | The digest step finished `ok` **and** the report file exists (the same rule `--auto` uses to skip a published week, `weekly.already_published`) |
| Failed | Failed | A step's last status is `failed`. A "not ready" run doesn't count: the run summary's status decides, and for weeks before run summaries only the not-ready wordings of the `ready` step count |
| Incomplete | Incomplete | Steps ran but no digest was published, or the last run stopped because the week before wasn't final |
| Ready | Ready to run | The current week, and last week is final in the local schedule snapshot |
| Waiting | Waiting on week N | The current week, and last week isn't final in the local schedule snapshot |
| Waiting | Waiting on the schedule | The current week has no games in the schedule yet (the next playoff round before nflverse adds it); the run would stop with exit 3 |
| No run | No run | A past week with no run |

**"Waiting" can be out of date on a Tuesday morning.**
- The app reads the newest schedule snapshot on D:, which only refreshes when something ingests.
- `nfl weekly run --auto` refreshes it first, under the lock, before deciding.
- So "Waiting on week 4" with "1/16 final" before the Tuesday run doesn't mean the run would stop.
- CR02's pre-flight panel will handle this properly.

**The week header:**
- the season and "this week", and the title (a playoff week shows its round's name);
- the status, the slate (games and days), the calendar's tags for that week (Thursday game, Thanksgiving, morning kickoff, games abroad by team name, other neutral sites, byes). Past weeks get their tags from the calendar too;
- **for the current week:** the first game (team chips), the countdown to its kickoff (updated every 30 s), the kickoff time in ET, the end of the "not ready" retry window, and the Saturday injury-update button, greyed out with its reason (it works from CR02);
- **for a past week:** when it was published, an **On time** or **Late run** chip (published before or after the week's first kickoff: the run summary's `on_time`, or worked out from the slate for weeks before run summaries), and **Open week N in W&B ↗**, the week's digest run. 2026 week 4 shows "Late run · first live week" (published on Sunday, after Thursday's game) and "No run records".

**Tabs:** Pipeline, Digest, Games, Players, Results, MLOps, Graph, with counts on Games (games predicted), Players (projections) and Graph (insights used). Each tab is a link, so a tab can be bookmarked (`/week/2026/4/games`).

## 2a. The week tabs (CR01)

Every tab reads its own endpoint (`/api/weeks/<season>/<week>/<tab>`), which reads the files the weekly run wrote. Nothing the pipeline already decided is recomputed, and nothing is written. A tab with no data shows a designed empty state; for the **current week before its run**, Digest, Players, Results and Graph say what will appear and link to the same tab of the last published week.

### Pipeline
- **Reads:** `runs/<S>/week<NN>/weekly_run.json` (each step's latest state, times and detail), `run_summary.json` when it exists (from week 5: seconds per step, `this_run`, the run records), `checks.json` and `raw_llm_output.json` (for the tiles).
- **Shows, for a finished week:**
  - tiles: status and publish time, steps ok, time in steps and the digest's share of it, digest checks passed, LLM cost and provider;
  - a notice when something is special: a week without run records (week 4), a failed step (with the exact resume command for the terminal), a run that stopped before the digest, a run in progress;
  - **the run** in the view you pick (below), with the real step times;
  - **the log**, rebuilt from the step details: one line per step with its start time, a `$ nfl weekly run … --from-step X   (resumed)` line wherever the week ran in several sittings, and the publish line. Paths are relative to the data drive and W&B runs appear as `W&B <run id>`. The original flags (`--promote`, `--auto`) aren't recorded anywhere, so they aren't shown.
- **Shows, for the current week before its run:** "The plan for this run", every step waiting with its **expected time** (from the newest published week's real times; estimates before the first one), and the calendar's plan as the log (last week final or not, deadline, retry window, slate, byes, lock). The real dry run and the Run button are CR02's.
- **The three views.** Pick one per week; the choice is saved in this browser (`cr.vizByWeek`, keyed `"2026-4"`), the default is the drive chart, and **Surprise me** picks one of the other two and shows a "surprise pick" chip.
  - **Drive chart:** the run as a football drive from your own 20. Each step is a play that ends at its yard line (ingest 30, ready 35, curate 45, ratings 50, game model 60, knowledge graph 72, player model 85; the digest is the touchdown). Finished plays are chalk arcs; the blue line of scrimmage and the yellow first-down line show where a running step is and where it ends; a failure is a **FUMBLE** mark; the run records are the extra point (green goalposts when recorded, grey before P07). The scorebug says Final, Turnover, Stopped or Waiting, and the drive log under the field lists each step as "1st & 10 at OWN 20" with its detail and time.
  - **Pipeline map:** sources → nine steps → Published, in a snake. A finished step gets a green ring and its file or W&B artifact as a chip (W&B ones highlighted); the Published circle reads LIVE once the digest is out.
  - **Timeline:** a bar per step on a time axis in minutes, striped while running, dashed when only expected. Below it: the digest's share of the run, and how many sittings the week took (the gaps between sittings are left out). Hover or focus a bar for its time.
- **Why a summary can be ignored:** `run_summary.json` describes the week's last run only if no step started after it was written. If a retry dies before writing its records, the earlier summary (a "not ready" one, say) is set aside and the steps speak for themselves.
- **Week 4, read off the tab:** 8/8 steps ok in three sittings (two resumes); 43m 30s in steps, 89% of it the digest (two GLM calls on DeepInfra, $0.026); no run records.

### Digest
- **Reads:** `reports/<S>/week<NN>-digest.md` byte for byte (a run folder's `digest.md` shows as an unpublished draft when there's no report), `checks.json`, `raw_llm_output.json` (**metadata only**: provider, model and, per call, the upstream provider, time, prompt / completion / reasoning counts, cost and finish reason; never the prompt or the LLM's text), and Saturday's addendum (`reports/<S>/week<NN>-injury-update.md`, `injury_update.json`).
- **Shows:** the digest rendered (tables scroll sideways); on the right, the checks (passed first time or after a rewrite, the banner, each check with its level), words per section, the writer (model, route, the prompt id from the digest's footer, total cost, a row per call), the file paths, Saturday's addendum.

### Games
- **Reads:** `predictions_games.parquet` (the row the digest shows, and the model-only row), `payload.json` → `qb_changes`, and the curated `games` table for finals and stadiums (corrected by `config/stadiums.yaml` → `game_venues`).
- **Shows:** tiles (games predicted, lines used, model version); **where the model disagrees with the market** (the model-only chance against the market's for the home team, the three biggest gaps); a card per game: kickoff in ET, neutral site, confidence, both teams with their QB (⚠ on a QB change, the digest's sentence on hover), win % and predicted score (the final in bold once played), a dot strip of the home team's chance from four sources (model as shown, model only, Elo, market; hover a dot), the market line and total, and ✓ hit / ✕ miss once final. A game first predicted after its kickoff (week 4's Thursday game: the first live run was on Saturday) says "predicted after kickoff" and "final · not graded": the report card never grades it, so it gets no hit or miss.
- **The current week before its run** shows the slate instead: kickoff, game, stadium, market line and total from the schedule snapshot, with the snapshot's date (it can be days old) and "after the run" in the win % and score columns.

### Players
- **Reads:** `predictions_players.parquet`, `predictions_teams.parquet` (from week 5), `watchlist.parquet`, and `payload.json` → `players_to_watch` (the digest's own words) and `tough_spots`.
- **Shows:** the watch-list cards (rank, team, position, opponent, injury, the projection, the 80% range with his baseline marked, how far above his baseline, the main driver or "no single factor stands out"), offense and defense apart from week 5; tough spots; **all projections**: the main stat per group by default (QB, RB, WR/TE, EDGE/DL, LB/S, CB/S, and TEAM from week 5), sorted by how far above the baseline, each with a small range bar. **Show every stat** switches to every stat; yes/no stats (touchdown, interception) show a chance instead of a range. The table shows 40 rows, then "Show all N".
- **The projection** is the digest's: the median for yards, the expected count for counts.

### Results
- **Week N is graded by week N+1's run.** Until then the tab says so, with the Tuesday the grading run is due.
- **Reads** (D101): week N+1's `payload.json` → `report_card` for the published numbers; week N's saved predictions and the curated finals, through the report card's own grading code, for game by game; the report card's watch-list look-back with week N's `watchlist.parquet`; the season's `accuracy_scoreboard.parquet` for the player groups.
- **Shows:** tiles (picks right, Brier for the model / Elo / market, watch-list picks that beat their baseline); game by game (the pick and its chance, the final, ✓ / ✕, each source's Brier for that game, the margin error); player model vs baseline by group (MAE improvement; hover for the stats behind it); the watch list with each pick's range and a ◆ at the actual. If a recomputed number disagrees with the published report card (a final score corrected later), a notice says so and the published number is shown.
- **On Tuesday 2026-10-06** week 4's tiles should match week 5's digest report card (the parity test checks that too). Before that, the graded view was checked on 2025 week 10 (P07's simulation): 8/14 picks, Brier 0.221 vs Elo 0.223 vs market 0.219, 15 of 20 watch picks beat their baseline.

### Graph
- **Reads:** `graph_results.json`, the weekly graph build's record (the query rows themselves stay on the server).
- **Shows:** tiles (nodes, relationships and count mismatches, build seconds split into wipe / load / queries, insights used of candidates); the insights the digest used (section, query, strength, confidence, the text); rows per query; **found but not used**, with why (game already started, used in a recent digest, same player as another pick, or simply not picked); nodes by label; GDS status from week 5; **Neo4j Browser ↗**, which opens `http://localhost:7474/browser/` on this machine.
- **Week 4:** 15,522 nodes and 588,330 relationships, built in 144 s (wipe 48 s, load 88 s), 3 of 57 candidates used, 2 skipped because their game had started.

## 3. Themes and modes

The gear next to "Control Room" opens **Appearance**:
- **Turf & Pylon:** field green with end-zone pylon orange. The default.
- **Playbook:** a whiteboard in light mode, a chalkboard in dark mode.
- **Mode:** System (follows Windows' light / dark setting, live), Dark or Light.

The choice is saved in this browser (`localStorage`, key `cr.appearance`) and painted before the page draws, so there's no flash of the default theme. If the browser blocks storage, the choice lasts until you close the tab.

All colours come from one set of tokens, ported unchanged from the approved mockup into `web/src/styles/tokens.css`: backgrounds, ink, accent, the three chart series colours, ok / warn / err, and the football-field colours for CR01's drive chart. The chart colours of both themes passed the colour-blindness checks in both modes (D96).

## 4. How it's wired

```mermaid
flowchart LR
    B[Browser: React app<br/>web/dist] -- "GET /api/*" --> A[FastAPI on 127.0.0.1:8765<br/>src/nflengine/app]
    A --> R[Readers, read-only]
    R --> D[(D:\nfl-ml-data<br/>runs, reports, raw schedule snapshot)]
    A --> L[The run lock<br/>runs/.weekly.lock]
    A -. every 30 s .-> N[Neo4j ping]
```

**Backend** (`src/nflengine/app/`, FastAPI 0.142, Starlette 1.7, uvicorn 0.54):

| File | Role |
|---|---|
| `serve.py` | `nfl app`: checks the build and the port, binds `127.0.0.1` only, opens the browser |
| `server.py` | `create_app(settings)`: the routes, the error format `{"error": {"code", "message"}}` (messages scrubbed), the built React app with client-side routes |
| `security.py` | The safety rules (§5) as plain ASGI middleware |
| `jsonsafe.py` | Every response goes through it: NaN and infinity → `null` (a browser rejects the whole response otherwise), dates as ISO strings, numpy numbers as plain numbers |
| `status.py` | A background thread that pings Neo4j every 30 s. A ping takes about 4.5 s when the container is down, too slow for every page load |
| `readers/meta.py` | The calendar's plan (`ops.calendar.plan_week`) and the lock (`ops.lock`), as plain data. Only the lock's command and start time leave the server |
| `readers/weeks.py` | The week list and the status rules in §2; one week's header (`get_week`); team names and colours (`team_info`) |
| `readers/common.py` | The read helpers every week reader uses: one short read per file, Parquet never memory-mapped, text without newline translation; `clean()` (data-root paths made relative, then `scrub()`) |
| `readers/pipeline.py` | The Pipeline tab: steps, sittings, the rebuilt log, the plan and its expected times |
| `readers/digest.py` | The Digest tab, with the allowlist of LLM metadata |
| `readers/games.py` | The Games tab, and the current week's slate |
| `readers/players.py` | The Players tab |
| `readers/results.py` | The Results tab (D101) |
| `readers/graph.py` | The Graph tab |

| Endpoint | Gives |
|---|---|
| `GET /api/meta` | App version, mode (dev / rehearsal), data drive (found, free space; never its path, which comes from an environment variable), lock, Neo4j, the calendar's plan (week, deadline, retry window, slate tags, byes), `seasons.current` |
| `GET /api/weeks` | The season, the current week, the first live week, and one entry per week (status, label, detail, published time, on time, step statuses) |
| `GET /api/session` | The per-launch token (§5) |
| `GET /api/team-info` | Team nicknames, names, colours, conference and division (curated `teams`) |
| `GET /api/weeks/<S>/<N>` | One week's header: title, status, the calendar's plan for that week, publish time, on time, first live week, the digest's W&B run, tab counts, the newest published week |
| `GET /api/weeks/<S>/<N>/pipeline` · `/digest` · `/games` · `/players[?stats=all]` · `/results` · `/graph` | One per tab (§2a). Season 1999–2100 and week 1–22 are checked; anything else gets a 422 that doesn't echo the input |

The readers only read:
- `runs/<season>/week<NN>/` (every file of the week; §2a says which tab reads which);
- `runs/<season>/accuracy_scoreboard.parquet`;
- `reports/<season>/`;
- `curated/games.parquet` and `curated/teams.parquet`;
- the newest schedule snapshot (cached for 60 s);
- the lock file.

They never write to the data root (a test hashes every file before and after calling every endpoint). They also never keep a file open or memory-mapped: the weekly run replaces its files in place, and on Windows an open or mapped file makes that fail. That's why curated tables are read from their Parquet files and not through a DuckDB connection to `nfl.duckdb` (D100).

**Frontend** (`web/`):
- **Stack:** Vite 8, React 19, TypeScript 6.
- **Routing:** React Router 7 (`/week/:season/:week/:tab`, `/season/:season/scorecard`, `/teams`, `/models`, `/alerts`, `/health`).
- **Data:** TanStack Query; `/api/meta` and `/api/weeks` refresh every 30 s and when you come back to the tab.
- **Widgets:** Radix for the gear popover and the confirm dialog (focus handling, Escape).
- **Styling:** Tailwind 4, with the mockup's tokens as colours; the mockup's component styles ported as they are (`web/src/styles/app.css`).
- **Fonts:** Barlow, Barlow Condensed and JetBrains Mono, bundled with `@fontsource`, so nothing loads from the internet.
- **Charts:** small in-house SVG components (`web/src/charts/`): a line chart with a legend and crosshair tooltip, horizontal bars with tooltips, a sparkline. They follow the project's chart rules: text in text colours, a legend for two or more series, hover and focus tooltips. CR01 and CR03 use them.

## 5. The safety rules, in plain words

The app can't be reached from other computers, and other websites open in your browser can't use it:

1. **It only listens on 127.0.0.1.** There's no `--host` option.
2. **It only answers requests addressed to `127.0.0.1:<port>` or `localhost:<port>`.** That stops a trick where a malicious site points its own domain name at your machine ("DNS rebinding").
3. **Anything that changes something needs the launch token and must come from the app's own page.** The token is a random string made each time `nfl app` starts. The page reads it from `/api/session`, which other sites can't read. In CR00 nothing changes anything yet; CR02's Run button will be the first.
4. **No cross-site access headers are ever sent.** The page can't be framed by another site either (it can't be tricked into clicking Run inside a hidden frame). The other security headers: content security policy, `nosniff`, `no-referrer`, `no-store` on API answers.
5. **Secrets stay on the server.**
   - No endpoint returns a key or an environment value.
   - Every text that reaches the browser passes through the pipeline's `scrub()`.
   - A test sets fake keys and checks that no response contains them, or anything that looks like a key.
6. **No API docs page** (`/docs`, `/openapi.json` are off), and unknown `/api/...` paths return a JSON 404.
7. **A crash never leaks.** An unexpected error is caught inside the safety layer: the console gets one scrubbed line, the browser gets `server_error` with the error's type only, and the response still carries the security headers.
8. **The status line never gets in a run's way.** Checking the lock means taking it for an instant, so the app reads the lock's note first and only checks when a run has written one (a run starting at that instant would otherwise stop with exit 4).

**Tests** (`tests/app/`, 50): Host and Origin and token checks, every state-changing method, the dev-mode origin, security headers, no CORS, the secrets scan, a missing data drive, path-traversal attempts on the static files, the week-status rules on fixture run folders (published, failed, partial, not ready vs a real `ready` failure, no slate, running from the lock, a simulation holding the lock), the lock check that doesn't probe without a note, a crash's scrubbed log and headers, no data-root path in any response, the CLI's flags, the missing-build and port-in-use paths, the 127.0.0.1 bind, and Ctrl+C as a normal stop. They never start a real server or touch D:. One integration test (`-m integration`) reads the real data root and expects 2026 week 4 to be Published.

**CR01's backend tests** (35 more in `tests/app/test_app_week.py`, on a complete synthetic week built by `tests/app/week_fixtures.py`): every tab's reader on a week like week 4 (no run records, three sittings), a week with records, a failed week, a step left running by a dead run, a "not ready" stop, the current week before its run, a past week with no run; the digest byte for byte, the LLM metadata allowlist, the addendum, an unpublished draft; games with finals, a late prediction (no hit or miss), a NaN Elo value, a QB change, the slate; players' main and all stats, a yes/no stat, team rows, the watch list's own words; results not graded, graded, disagreeing with the report card, and a week that graded nothing; the graph's counts, skip reasons and GDS; **nothing written** (every file hashed before and after calling every endpoint); **no leak** (fake keys in the environment, the LLM's raw text, a credential-shaped detail and the data root's own path planted in the files, then every response scanned); bad seasons, weeks and options. Seven more (`tests/app/test_app_sol_cr01.py`) pin the Sol review's fixes: the root's path planted in names, driver phrases and writer metadata; an empty data root that stays empty after every GET through the real resolver; a heuristic watch list; a stale run summary; a late prediction; the Super Bowl.

**Real-data parity** (`uv run pytest -m integration tests/app/test_parity.py`, 10): 2026 week 4's games match `predictions_games.parquet` (16 games, every probability and score), the QB changes match the payload, 1,860 projections, the watch list matches the digest's, the graph's counts sum to 15,522 nodes and its relationships to the build's, the digest is byte for byte the published file, week 4's pipeline is three sittings without records; the graded Results agree with the report cards of P07's simulated 2025 weeks 9, 10 and 13; reading the real week changes no file. Week 4's own grading test runs once week 5 exists (it skips until then).

**Web tests** (`npm --prefix web test`, Vitest, 94): formatting (ET with daylight saving, the countdown, slate tags), theme persistence and junk in storage, the chart scales, legend and tooltips, the sidebar's week rows and footer, theme switching from the gear, the week header (current and past weeks), the seven tabs with their counts, the confirm dialog; the three pipeline views in their finished, plan, failed, stopped and live states, the view picker (per week, across a reload, "Surprise me" never repeats, blocked storage), the log panel; the Pipeline tab's tiles, notices and plan; and each data tab's main and empty states.

## 6. Troubleshooting

| What you see | What to do |
|---|---|
| "Port 8765 … is in use" | Another control room is running (check your terminals), or pass `--port` |
| "The web app isn't built yet" | `npm --prefix web ci` then `npm --prefix web run build` |
| Red banner "The data drive isn't connected" | Connect D:. The page still loads; the week list returns a `data_root_missing` error until the drive is back |
| Red banner "The control room's API isn't answering" | The `nfl app` terminal was closed or crashed: start it again |
| Neo4j "down" in the footer, Health "Check" | Normal when Docker Desktop isn't running. The weekly run starts Neo4j by itself. "checking" shows for the first few seconds after start-up |
| Week shows "Waiting" on Tuesday | The local schedule snapshot is from before Monday night's game (§2). The run refreshes it |
| The page looks unstyled after a code change | Rebuild (`npm --prefix web run build`) and reload |
| A tab says "Loading…" for long, or "Couldn't load this tab" | The API answers in well under a second; check the `nfl app` terminal for an error line (scrubbed), then reload |
| Results says "graded by week N+1's run" | Normal until the next Tuesday's run; the tab names the day |
| Results shows "the recomputed numbers differ from the report card" | A final score was corrected after the grading run. The published numbers are shown; nothing to fix |
| The current week's Games tab shows an old line | The slate comes from the schedule snapshot (its date is on the card); Tuesday's run refreshes it |
| The pipeline view keeps coming back as the drive chart | The browser blocks storage (private window): the choice lasts until you close the tab |

## 7. Changing it safely

- **Design changes start with the mockup** (`documentation/control-room/mockup/`, republished to the same link), then the spec, then the code. Keep `web/src/styles/` in step with the mockup's `styles.css`.
- **Adding data to the page:**
  1. write a reader under `src/nflengine/app/readers/` that only reads, takes `paths` from `ensure_data_root()` (never a hard-coded `D:/`), uses the helpers in `readers/common.py` (one short read per file, Parquet not memory-mapped, no DuckDB connection to `nfl.duckdb`) and passes free text through `clean()`;
  2. add the endpoint in `server.py` (validated integers, never a path);
  3. add tests on fixture folders (`tests/app/app_helpers.py` builds a fake data root, a pinned Tuesday clock and the schedule fixture; `tests/app/week_fixtures.py` a complete week) and a parity check on the real data in `tests/app/test_parity.py`;
  4. add the endpoint to the secrets scan in `test_app_security.py` and to `all_week_urls()` in `test_app_week.py`;
  5. never name a JSON field `key`, `*token*`, `*auth*`, `*secret*` or `*password*`: the scan reads those as leaked credentials.
- **Adding a screen:** a component under `web/src/components/` or a page under `web/src/pages/`, its route in `routes.tsx`, and a Vitest test with the fake API (`web/src/test/utils.tsx`).
- **Reviews:** CR00 had a Sol review (8 findings, all fixed, D98), CR01 too (7 findings, all fixed, D102). Each CR phase gets one before its close.
- **Gates before a commit:** `uv run pytest`, `uv run ruff check`, and in `web/`: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`.
- **Never** add an endpoint that takes a file path or a command, never send a key or an environment value to the browser, and never make the server listen on anything but 127.0.0.1 (README §5).

## 8. Limits and what's next

- **CR01 (done):** every week tab from the real files, the three pipeline views for finished runs and the plan, the week header for any week.
- **Limits now:** a week's Results appear only after the next Tuesday's run; the current week's log is the calendar's plan, not a real dry run; the rebuilt log can't show the original command's flags; MLOps waits for CR03.
- **CR02:** the Run button (`nfl weekly run --auto --expect-week N`), pre-flight checks, the live view and log, resume after a failure, the Saturday injury update, notifications. It ends with the first real Tuesday run from the app.
- **CR03:** MLOps (Health · W&B runs · Artifacts) and the season pages.
- **Not planned:** remote access, user accounts, scheduling (runs stay manual, D71).
