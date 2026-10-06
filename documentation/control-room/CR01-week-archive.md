# CR01: The week archive (read-only)

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Control room rows)

- **Depends on:** CR00
- **Unlocks:** CR02, CR03
- **Read first:**
  - [Control room README](README.md) §3 (the week-tab rows) and §9 (findings);
  - the mockup's week 4 and week 5 tabs ([artifact](https://claude.ai/artifact/1z7AapJzJwKusNtWzXYfQw); source `mockup/app.js`: `week4View`, `week5View`, `driveHTML`, `mapHTML`, `timelineHTML`, `w4Games`, `w4Players`, `w4Results`, `w4Graph`, `w4Digest`);
  - the `digest-checks` skill (payload, report card);
  - the `curated-data` skill (games and box scores);
  - [W&B guide §5](../guides/weights-and-biases.md) (one weekly cycle at a glance).

## Goal

Every week's tabs show real data from the files the pipeline writes, except MLOps (CR03):
- Pipeline (finished run or the plan, in all three views with the per-week picker)
- Digest
- Games
- Players
- Results
- Graph

Week 4 (a partial week, before run records) and week 5 (the first full week, after Tuesday 2026-10-06) both render. The current week, before its run, shows its slate and designed empty states.

Still read-only: no button runs anything yet.

## Scope

- **In:** the readers and endpoints for the six tabs; the three pipeline views in their **finished** and **plan** states; the view picker per week with "Surprise me"; the week header; the current week's slate; the parity tests.
- **Out:** live runs, pre-flight, Run / Resume / injury update (CR02); MLOps and the season pages (CR03).

## Tasks

### Probe first
- [x] 🤖 **List every file a week has**, and record the list in README §9:
  - week 4: no `run_summary.json`;
  - week 5: the full set, plus P08's files (`predictions_teams.parquet`, `consistency.json`);
  - what an injury-update week adds (`injury_update.json`, `*_update.parquet`, the addendum).
- [x] 🤖 **Find where Results come from without re-implementing grading.** Week N+1's `payload.json` → `report_card` grades week N (picks, Brier vs Elo, points error, biggest miss, watch-list hits, calibration, season to date). `digest/report_card.py` (`grade_games`, `week_metrics`, `calibration`, `pre_kickoff`) builds it from the saved predictions and curated finals, and `accuracy_scoreboard.parquet` holds the player numbers by target and group. Decide which to read for each Results element, and write it down.
- [x] 🤖 **Find the per-game and per-player numbers the Results tab needs** beyond the report card: per-game Brier, the market Brier, margin error, and each watch-list player's actual. Check whether `grade_games` and `player_runs.score_week` expose them. If not, compute them in the reader from the same inputs, using the same code paths.
- [x] 🤖 Confirm the payload fields used for QB changes (`qb_changes`), tough spots (`tough_spots`) and starters out (`starters_out`) (finding: the prediction files can't tell a QB change).

### Backend readers (`src/nflengine/app/readers/`)
- [x] 🤖 `weeks.get_week(season, week)`, for the header:
  - the calendar plan for that week (`plan_for`), with slate tags, deadline and retry window;
  - status and publish time;
  - `on_time` from `run_summary.json` when present.
- [x] 🤖 `pipeline.get_pipeline(season, week)`:
  - steps (status, seconds, detail scrubbed and shortened) from `run_summary.json` → `steps`, or from `weekly_run.json` when there's no summary;
  - for the current unrun week, the plan: the step list with expected times, read from the last published week's real times, with defaults for a first run;
  - the rebuilt log lines (the mockup's `w4Log` format).
- [x] 🤖 `digest.get_digest`: the Markdown; `checks.json` (passed, regenerated, banner, writers, final checks, word counts); `raw_llm_output.json` (**metadata fields only:** provider, model, and per call the provider, latency, token counts, cost, regeneration, finish reason; never the prompt or the raw text); the injury-update addendum if present.
- [x] 🤖 `games.get_games`:
  - the primary and `model_only` rows joined per game, with the Elo and market probabilities, spread, total and QBs;
  - QB changes from `payload.json`;
  - finals from curated `games` through a **read-only** DuckDB connection;
  - "biggest model vs market gaps".

  For the current unrun week: the slate from curated `games` (or the schedule snapshot), with kickoff, stadium and market.
- [x] 🤖 `players.get_players`: the watch list, tough spots (payload), and the projections (main stat per group by default, all stats on request) with filters, team rows (P08) and injury status.
- [x] 🤖 `results.get_results(season, week)`:
  - when week N+1's run exists, the graded view from the probe's sources;
  - otherwise `{"status": "not_graded", "graded_by_week": N+1}`.
- [x] 🤖 `graph.get_graph`: from `graph_results.json`:
  - counts by label;
  - timings by stage;
  - rows per query;
  - the insights used (section, query, strength, confidence, text);
  - candidates not used, with the skip reason when known (`skipped`: started / novelty / duplicate player);
  - GDS status (P08).
- [x] 🤖 Endpoints `GET /api/weeks/{season}/{week}/{pipeline,digest,games,players,results,graph}`.
- [x] 🤖 Tests:
  - every reader on small fixture run folders (`tmp_path`), including a week-4-like folder with no `run_summary.json`, a week with a failed step, and the current week with no folder;
  - NaN → null;
  - every text scrubbed.
- [x] 🤖 **Parity tests** (marked `integration`): the Games endpoint for 2026 week 4 matches `predictions_games.parquet` (16 games, every probability and score); the Players endpoint has 1,860 projections; the Graph endpoint's counts sum to the build's totals (15,522 nodes); the Digest Markdown is byte-identical to `reports/2026/week04-digest.md`.

### Web: the tabs
- [x] 🤖 **Week header** as in the mockup:
  - status chips and slate tags;
  - for the current week, the countdown to first kickoff and the retry window, a live clock that updates every minute;
  - for past weeks, the publish time and the late / on-time chip;
  - "Open in W&B" filtered to `season:S` + `week:NN`;
  - the Saturday injury-update button shown greyed out with its reason (enabled in CR02).
- [x] 🤖 **The three pipeline views**, ported from the mockup. One component per view, sharing one step model `{key, status, seconds, detail, progress}`:
  - **Drive chart:** an SVG field with each step at a yard line (ingest 30 … game 60, graph 72, player 85, digest = touchdown), the line of scrimmage (blue) and first-down line (yellow), arcs for finished steps, FUMBLE on a failure, goalposts for the run records (the extra point), the scorebug, and the drive log with down and distance, detail and time;
  - **Pipeline map:** the snake layout with sources → 9 steps → Published; progress rings; flowing edges into the running step; artifact chips appearing as each step finishes (W&B ones highlighted);
  - **Timeline:** a row per step with a bar on a time axis (striped = running, dashed = expected) and the digest's share of the run.

  In CR01 they render the **finished** state (real step times) and the **plan** state (expected times). Live animation is CR02, so build the components to take progress updates without re-mounting.
- [x] 🤖 **View picker** on the Pipeline card: Drive chart · Pipeline map · Timeline · Surprise me. "Surprise me" picks a different view at random and shows a "surprise pick" chip. The choice is remembered per week (`localStorage` → `vizByWeek`, keyed by season and week), and the default is the Drive chart.
- [x] 🤖 **Log panel:** the console styling, rebuilt lines for finished weeks, auto-scroll.
- [x] 🤖 **Digest tab:** the Markdown rendered with `react-markdown` + `remark-gfm`, tables in scroll boxes. The right-hand rail: checks, words per section (bars), the writer (calls table), files, Saturday's addendum.
- [x] 🤖 **Games tab:** tiles; the "disagrees with the market" card; game cards with the dot strip (shown model filled `--s1`, model only as a `--s1` ring, Elo `--s2`, market `--s3`, a tooltip on each dot) and the finals (hit / miss chips) when played. The current, unrun week shows the slate table with "after the run" placeholders.
- [x] 🤖 **Players tab:** watch-list cards (projection, range bar with the baseline marker, the main driver, injury), tough spots, the projections table with group filters (QB, RB, WR/TE, EDGE/DL, LB/S, CB/S, TEAM from week 5) and mini range bars.
- [x] 🤖 **Results tab:** tiles (picks, Brier model / Elo / market, watch list); the game-by-game table; player model vs baseline by group (bars); the watch-list table with the actual marked on the range (◆). When not graded yet: an empty state naming the run that grades it and its date.
- [x] 🤖 **Graph tab:** tiles; the insights used (section chip, query chip, strength meter, confidence); rows per query (bars); "found but not used"; nodes by label; the Neo4j Browser link (`http://localhost:7474/browser/`).
- [x] 🤖 **The current week before its run:** every tab except Pipeline and Games shows the mockup's empty state, with a link to the same tab of the last published week.
- [x] 🤖 Component tests for each tab (fixture JSON), including the empty states and a week without run records.

### Close
- [x] 🤖 **Mockup parity:** screenshots of week 4's six tabs and week 5's Pipeline / Games tabs, next to the mockup; differences listed in README §10.
- [x] 🤖 Guide (`guides/control-room.md`): each tab, what it reads, how to read it. Skill: the reader patterns and the partial-week rule. PROGRESS.
- [x] 🤖 Sol review of the readers (read-only guarantees, scrubbing, the raw-LLM metadata filter). 7 findings (0 high), all fixed with tests (D102).
- [x] ✋ **Checkpoint:** Rishi reads week 4 and week 5 in the app next to the published digests and W&B, and approves CR01. **Waived by Rishi** at the kickoff ("marking the phase complete"); the week-5 look stays on PROGRESS's Next step for after Tuesday's run.

## Rishi-in-the-loop moments: what to look for

- **Week 5 in the app vs the published digest:** same win %, same predicted scores, same watch list, same graph insights. Results for week 4 now filled in, and they agree with week 5's report card.
- **The three views** look like the mockup and agree with each other on step times.

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| All six tabs render from real files | Week 4 and week 5 in the browser; `uv run pytest -m integration tests/app/test_parity.py` |
| Week without run records handled | Week 4's Pipeline tab shows the steps from `weekly_run.json` and a notice; no errors |
| Results are correct | Week 4's Results (graded by week 5's run) match week 5's `payload.json` → `report_card` |
| Nothing written | The readers open DuckDB read-only; a test confirms the data root's file hashes are unchanged after calling every endpoint on a fixture copy |
| Views and picker | All three views render finished and plan states; the per-week choice survives a reload; Surprise me never picks the current view |
| Parity with the mockup | Screenshots and the §10 list |
| Gates | pytest, ruff, web lint / typecheck / test / build |

## Handoff to CR02 / CR03

- The step model and the three view components take progress updates (for CR02's live mode).
- The readers' patterns are reused by the MLOps and season readers (CR03).

## As built: deviations from the task list (2026-10-05/06, D100–D102)

- **Curated finals without DuckDB.** The task said "a read-only DuckDB connection". The readers read `curated/games.parquet` (and `teams.parquet`) directly, with `memory_map=False`, in one short read: on Windows a process holding `nfl.duckdb` open (or a file memory-mapped) can make the run's `tmp.replace(path)` fail (D100).
- **The step model's field is `step`, not `key`**, and LLM token counts are `usage.{prompt, completion, reasoning}`: `scrub()` treats `"…key…": value` / `"…token…": value` as a leaked credential, so the security scan would fail (D100). The web model (`components/pipeline/model.ts`) renames it to `key` on the client.
- **"Open in W&B"** links the week's digest run (from the digest step's detail), not a tag-filtered runs page: a filtered W&B URL can't be built reliably without the W&B API (CR03). D97 had deferred the link; it's back in CR01.
- **The current week's log is the calendar's plan** (`plan_log`), worded like the dry run, with no command run. The real `run_pipeline(dry_run=True)` belongs to CR02's pre-flight.
- **The rebuilt log shows the inferred commands**: `nfl weekly run --season S --week N` for the first sitting and `--from-step <step>` for each resume (a gap over 2 minutes). The original flags (`--promote`, `--auto`) aren't recorded anywhere, so they aren't shown (the mockup's `--promote` was hand-written).
- **A `running` state** (not in the task list): when the lock is held by a run of this week the Pipeline tab shows the steps as `weekly_run.json` has them, with a notice; a step left `running` without the lock shows as failed ("interrupted").
- **Yes/no stats** (TD, interception: `kind == "prob"`, no quantiles) show their chance; `digest.players.center` would fail on them.
- **Results are graded only from the next week's payload** (D101). Week 4's Results stay "not graded" until Tuesday 2026-10-06's week-5 run; the graded view was checked on P07's simulated 2025 weeks (9, 10, 13: recomputed = published) and in the browser on 2025 week 10 (a scratch server over `runs/digest-backtests`).
- **Parity with the mockup** (screenshots of week 4's Pipeline (all three views), Digest, Games, Players, Results, Graph and week 5's Pipeline and Games, real data, Turf & Pylon dark; the graded Results on 2025 week 10):
  - fixed while building: the pipeline map's vertical edge (Game model → Knowledge graph) crossed the "Game model" label and time (the mockup has the same flaw), now it starts below the label; the Results watch list (20 picks from week 5) was cut off in the mockup's two-column layout, now the group bars and the watch list are full width;
  - kept on purpose: the international chip reads "Colts at Commanders abroad" (the plan has no city; the mockup's "in London" was hand-written); "Thursday game" (CR00's label) for the mockup's "Thursday opener"; week 5 shows "Waiting on week 4" while the local schedule snapshot is stale (CR00's rule; the run refreshes it); no pre-flight panel or Run button (CR02), a notice with the terminal command instead; the Results tab shows real grading or its empty state, never the mockup's sample numbers; Players adds "Show every stat" and a "Show all N" button (1,069 main rows in week 4); "Found but not used" adds a "why not" column (game started / used recently / same player / not picked) and shows 15 rows before "Show all".
- **Sol review fixes (D102):** the root's path removed from every text before capping (registered per request), a data root that never creates folders, heuristic watch lists, stale run summaries set aside, "predicted after kickoff" with no hit / miss (the mockup's "kept pick" was wrong), missing grades shown as "—", the Super Bowl graded after the season. 7 regression tests in `tests/app/test_app_sol_cr01.py`.
- **Tests:** 35 new backend tests (`tests/app/test_app_week.py`, fixtures in `tests/app/week_fixtures.py`; the new endpoints also joined the CR00 secrets scan) and 10 real-data parity tests (`tests/app/test_parity.py`, `-m integration`; the week-5 grading test skips until week 5 runs). Web: 74 new Vitest tests (94 in all): the three views, the picker and the log panel (46), the five data tabs (21), the Pipeline tab, the header and the tab counts (7).

## Pitfalls / notes

- **"Published" uses one rule everywhere:** `weekly.already_published` (the digest step ok + the report file). Don't invent a second one.
- **A week can have several runs** (not ready, then the run, then a resume). The records take the last run that published (`latest_runs`). The Pipeline tab shows the steps' latest state (`this_run` tells which steps ran in the last invocation).
- **The current week's slate before Tuesday comes from the schedule snapshot**, which can be stale. The calendar refreshes it under the lock during the run. Show the snapshot's date.
- **Kickoff times:** the files store UTC. Show US Eastern with daylight saving handled (`Intl.DateTimeFormat` with `America/New_York`), as the calendar does.
- **Playoff weeks (19–22):** they use the same files. The round's name comes from `ops.calendar.round_name`. Team totals have 0 rows in the playoffs (expected).
