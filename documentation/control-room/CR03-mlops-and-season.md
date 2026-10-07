# CR03: MLOps, W&B and the season pages

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Control room rows)

- **Depends on:** CR01 (can be built before or after CR02)
- **Unlocks:** closes the control-room track
- **Read first:**
  - [Control room README](README.md) §3 (the MLOps rows and season pages) and §9 (the live W&B artifacts);
  - the mockup's week-4 MLOps tab (Health · W&B runs · Artifacts) and the season pages (source `mockup/app.js`: `w4MLOps`, `mlHealth`, `mlWandb`, `dumbbell`, `mlArtifacts`, `seasonView`, `calChart`, `teamsView`, `modelsView`, `alertsView`, `healthView`);
  - [W&B guide](../guides/weights-and-biases.md) §2 (groups, job types, tags), §4 (each weekly run's charts), §7 (season views, the dashboard) and §8 (artifacts);
  - [weekly operations guide](../guides/weekly-operations.md) §5–§8 (run records, alerts, drift, the dashboard);
  - the `model-experiment` skill (artifacts and aliases).

## Goal

Each week's **MLOps** tab has its three sections, as in the mockup:
- **Health:** run health, data freshness, ingest dataset by dataset, quality checks, this week vs last week, what ran, drift;
- **W&B runs:** the week's runs with their main charts redrawn and linked;
- **Artifacts:** versions, aliases, where `production` points, lineage.

The season pages (Scorecard, Teams & rankings, Models, Alerts, Health) show real data. The app reads W&B on the server, caches it, and still works (with a banner) when W&B can't be reached. The phase closes the track.

## Scope

- **In:**
  - readers for `run_summary.json`, `pipeline_history.parquet`, the ingest manifest, the quality file and `consistency.json`;
  - a cached, read-only W&B client;
  - the MLOps tab and its switcher;
  - the five season pages;
  - the "secrets never in a response" scan extended to W&B data;
  - the guide, the W&B guide section and the skill;
  - closing the track.
- **Out:** writing anything to W&B from the app; editing alerts or notes in the app (notes stay in PROGRESS's Season log); a Track 2 tab.

## Tasks

### Probe first
- [x] 🤖 **W&B reads through the API**, using the settings helper the pipeline uses, so the key never appears:
  - list the runs for one week by tags (`season:2026` + `week:05`) and job types;
  - read each run's summary and the history keys the charts need (`slate/*`, `scoreboard/*`, `projections_per_target`, `load/*`, `query_seconds`, `words_per_section`, `check_issue_counts`, `step_seconds`);
  - read artifact versions with aliases, `logged_by` and `used_by`.

  Time each call and note any rate limits. **Prefer local files** wherever they hold the same numbers (the W&B guide names the local file behind most charts). W&B is needed for links, aliases, versions and lineage.
- [x] 🤖 **Confirm the week-5 records:** the fields of `run_summary.json` (`freshness`, `ingest`, `quality`, `models`, `digest`, `drift`, `alerts`, `consistency`), the columns of `pipeline_history.parquet`, `consistency.json`, and the dashboard's `dashboard.json`.
- [x] 🤖 **Model cards:** which file under `documentation/model_cards/` goes with each production model, and how the Models page reads the production versions (W&B aliases vs the week's prediction files' `model_version`).

### Backend
- [x] 🤖 **`wandb_api.py`:**
  - read-only;
  - a cache under `{NFL_DATA_ROOT}/cache/control-room/wandb/` (about 10 minutes, a manual refresh, and a longer life for finished weeks);
  - timeouts;
  - when W&B is down or the key isn't set, a `{"available": false, "reason": …}` answer the UI shows as a banner while local data still loads;
  - a test that no response contains the key or any key-shaped string (CR00's scan).
- [x] 🤖 **`mlops.health(season, week)`:**
  - tiles;
  - step timings;
  - **what ran:** model versions, trained-through week, graph build, writer and route, prompt id, promotions, launched by;
  - data freshness (from `run_summary.json` → `freshness`);
  - **this week vs last week** (from `pipeline_history.parquet` plus the week files: time in steps, GLM time, cost, games, projections, graph nodes, datasets, first-pass checks, each with a season trend);
  - **ingest dataset by dataset** (the run's `raw/_runs/ingest-*.json`);
  - **quality checks** (`curated/_quality/latest.json`, or the run's copy if the summary keeps one);
  - **drift signals** (status, value, threshold, the sentence, doc 08's response).

  Weeks without run records (week 4) fall back to what exists, with a notice.
- [x] 🤖 **`mlops.wandb(season, week)`:** the week's runs (job, name, group / job type, id, link, state) and the data for each chart card:
  - game fit: model only vs market per game;
  - player scoreboard: improvement by group;
  - player fit: projections per stat;
  - graph build: time by stage, counts;
  - digest: words per section, check issues;
  - pipeline: `step_seconds`, freshness and drift tables.

  Plus the season-dashboard run and the report link.
- [x] 🤖 **`mlops.artifacts(season, week)`:**
  - for `game-model`, `player-model`, `team-model`, `graph-results`, `digest` and `injury-update`: versions (version, created, aliases, logged by, size);
  - where `production` points;
  - the lineage behind the week's digest (from the pipeline run's used artifacts, week 5 onwards; week 4 from the aliases).
- [x] 🤖 **Season readers:**
  - `scorecard(season, source=live|backtest)`: season Brier for model / Elo / market, pick accuracy, ECE and the calibration bins, player model vs baseline by group, pipeline health rows, LLM spend;
  - `teams(season, week)`: Elo by week, movers, ratings, this week's and next week's games;
  - `models()`: production versions, headline backtest numbers from the model cards and summaries, settings, the card's Markdown;
  - `alerts(season)`: every run's alerts and drift states, and when each signal can first fire (from `settings.yaml` → `drift` and the graded weeks so far);
  - `health()`: services (Neo4j version and plugins, Docker, W&B reachable, OpenRouter routing check), keys by name with set / not set (the doctor's functions), the data drive, the lock, the last runs from `pipeline_history.parquet`.
- [x] 🤖 Endpoints from README §4; tests on fixtures (including W&B unavailable, a week without records, an empty season); parity tests against W&B for one week (marked `integration`).

### Web
- [x] 🤖 **MLOps tab:** the switcher (Health · W&B runs · Artifacts) with a one-line description of the section, remembered while moving between weeks. Each section as in the mockup:
  - Health: tiles; step timings + what ran; freshness + this week vs last week; ingest table + quality checks (scroll boxes); drift signals;
  - W&B runs: the runs list + the season-dashboard card, then one card per run with its redrawn chart, the W&B keys named in the header and an "open in W&B" link (the dumbbell for the game fit; bars for the rest);
  - Artifacts: production tiles, the lineage row, one table per artifact; "no versions yet" with when the first one comes.
- [x] 🤖 **Scorecard:**
  - the 2026 live / 2025 backtest switch;
  - tiles;
  - season-to-date Brier (three lines with a legend and a crosshair tooltip);
  - pick accuracy (area line);
  - calibration (points sized by games, the diagonal);
  - player model vs baseline (bars);
  - the pipeline health table;
  - a link to the W&B Season Dashboard.
- [x] 🤖 **Teams & rankings:** risers and fallers (bars), the power-rankings table with sparklines, the AFC / NFC filter, and a detail row per team.
- [x] 🤖 **Models:** one card per production model (version chip, headline numbers, settings) with a "Model card" button that opens the rendered card.
- [x] 🤖 **Alerts:** the season's alerts (none yet in the mockup's week), the "when each signal can first fire" timeline, and alert cards with the investigation note and decision link.
- [x] 🤖 **Health:** services, keys (names, set / not set, optional ones marked), recent runs.
- [x] 🤖 Component tests, including the W&B-unavailable banner and empty seasons.

### Close the track
- [x] 🤖 **Mockup parity** for the MLOps sections and the five season pages (screenshots, differences in README §10).
- [x] 🤖 **Docs:**
  - `guides/control-room.md` complete: every screen, how to read it, what it reads, how to change or extend it, limits and what's next;
  - the W&B guide: a section on how the app reads W&B (cache, what's local vs from the API) and where each W&B chart appears in the app;
  - the runbook: the app as the first place to look on Tuesday;
  - `documentation/README.md`; CLAUDE.md;
  - the skill finished;
  - README §10 "As built" with every deviation and its decision number.
- [x] 🤖 Sol review (Codex, read-only) of the W&B client, the caching and the secrets scan. Fix the findings with tests.
- [x] ✋ **Checkpoint:** Rishi compares one week's MLOps tab with W&B (runs, charts, artifacts, aliases), and the season pages with the W&B Season Dashboard, then approves closing the control-room track. **Waived by Rishi's kickoff** ("do the final push and commit after updating all docs and marking the phase complete", read as for CR01 and P10); the comparison it asks for is automated in `tests/app/test_parity_cr03.py` (week 5's runs, charts, artifacts, aliases and the Season Dashboard against W&B: all equal), and the look by eye stays open to Rishi (PROGRESS → Next step).

## As built: deviations from the task list

Everything above is built; the details are in the README §10 (CR03) and D105, the Sol review in D106.

- **Probe:** done live on 2026-10-06 after week 5's run (README §9 "Checked in CR03"): W&B calls 0.1–0.25 s, no rate limit; the scoreboard run is tagged with the week it grades; every chart has a local twin; the quality file is replaced by every curate.
- **`wandb_api.py`:** as planned, plus a minute's back-off after a failure and the last answer served as "stale" with the reason; the key goes through `tracking.read_api` (new) rather than `tracking._wandb_env` (which sets a process environment variable and can create folders).
- **`mlops.health`:** freshness, ingest and quality as planned; "this week vs last week" is built from each run week's own files (no week before 5 has a `pipeline_history` row); the quality list is shown only when the quality file is that run's.
- **`mlops.wandb`:** every card drawn from local files (the plan allowed W&B history where needed: never needed); links from W&B, or from the steps' detail lines without it.
- **`mlops.artifacts`:** as planned; week 4's lineage from the `2026-w04` aliases.
- **Season readers:** the live scorecard calls the dashboard's own builder (`ops/dashboard.py` gained a `preds` argument so the app can pass its own reads); Models reads backtest `summary.json` files plus W&B's `ratings-eval`; Alerts reads the notes from PROGRESS's Season log; Health is `app/health.py` (the doctor's checks on a thread), not a reader.
- **Tests:** 30 backend tests on fixture records with a fake W&B; parity against W&B for week 5 (10, `-m integration`); the secrets scan with a leaky fake W&B; web tests for every section and page (326 in all).
- **Web:** the dumbbell is HTML rows (keyboard friendly); the line chart gained keyboard tooltips; the parity differences are listed in README §10.
- **Not built (out of scope as planned):** writing to W&B, editing alerts or notes in the app, a Track 2 tab.

## Rishi-in-the-loop moments: what to look for

- **W&B runs:** every run in the list opens the right W&B page, and the redrawn charts have the same shape and values as W&B's (for example the game fit's model-only vs market gaps, and the digest's words per section).
- **Artifacts:** `production` on the same versions W&B shows, and the week's digest version matches its run.
- **Scorecard (2026 live):** once a few weeks are graded, the same lines as the W&B Season Dashboard.

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| MLOps sections from real data | Week 5 and a later week in the browser; parity tests against W&B for one week |
| Works without W&B | Unset the W&B key in a test environment (never by editing `.env`) or block the network: pages load with local data and a banner |
| No secret in any response | The response scan over every endpoint, W&B ones included, passes |
| Season pages from real data | Scorecard (live + backtest), Teams, Models, Alerts, Health render with no errors on the live data root |
| Track documented | Guide, W&B guide section, runbook, skill, README §10, decisions |
| Gates | pytest, ruff, web lint / typecheck / test / build; Sol review fixed |

## Handoff

- The control room is complete: Tuesday's run, the week's results, the MLOps view and the season view in one local app.
- Later ideas (not planned): a Track 2 tab (once T00 starts); showing scheduled runs if scheduling is ever added (D71); team pages beyond the rankings detail.

## Pitfalls / notes

- **W&B is the record, the app is a window.** Never write to W&B from the app, and never move an alias from it (promotion stays with `--auto` / `--promote`).
- **Local first:**
  - most chart numbers exist in local files (`run_summary.json`, the week's parquet files, `graph_results.json`, `checks.json`);
  - read those, and use W&B for links, versions, aliases and lineage, which keeps the pages fast and working offline;
  - when a number exists in both, compare them in a parity test rather than trusting one silently.
- **Drift signals need several graded weeks** (`insufficient_data` until then: player from the week-7 run, game vs Elo from week 10, calibration after 64 graded games). Show that state plainly; it isn't an error.
- **Keys:** the Health page uses the doctor's set / not set functions. Never read the env file, never print a value (CLAUDE.md Security).
