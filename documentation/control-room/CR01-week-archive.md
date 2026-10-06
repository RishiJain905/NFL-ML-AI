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
- [ ] 🤖 **List every file a week has**, and record the list in README §9:
  - week 4: no `run_summary.json`;
  - week 5: the full set, plus P08's files (`predictions_teams.parquet`, `consistency.json`);
  - what an injury-update week adds (`injury_update.json`, `*_update.parquet`, the addendum).
- [ ] 🤖 **Find where Results come from without re-implementing grading.** Week N+1's `payload.json` → `report_card` grades week N (picks, Brier vs Elo, points error, biggest miss, watch-list hits, calibration, season to date). `digest/report_card.py` (`grade_games`, `week_metrics`, `calibration`, `pre_kickoff`) builds it from the saved predictions and curated finals, and `accuracy_scoreboard.parquet` holds the player numbers by target and group. Decide which to read for each Results element, and write it down.
- [ ] 🤖 **Find the per-game and per-player numbers the Results tab needs** beyond the report card: per-game Brier, the market Brier, margin error, and each watch-list player's actual. Check whether `grade_games` and `player_runs.score_week` expose them. If not, compute them in the reader from the same inputs, using the same code paths.
- [ ] 🤖 Confirm the payload fields used for QB changes (`qb_changes`), tough spots (`tough_spots`) and starters out (`starters_out`) (finding: the prediction files can't tell a QB change).

### Backend readers (`src/nflengine/app/readers/`)
- [ ] 🤖 `weeks.get_week(season, week)`, for the header:
  - the calendar plan for that week (`plan_for`), with slate tags, deadline and retry window;
  - status and publish time;
  - `on_time` from `run_summary.json` when present.
- [ ] 🤖 `pipeline.get_pipeline(season, week)`:
  - steps (status, seconds, detail scrubbed and shortened) from `run_summary.json` → `steps`, or from `weekly_run.json` when there's no summary;
  - for the current unrun week, the plan: the step list with expected times, read from the last published week's real times, with defaults for a first run;
  - the rebuilt log lines (the mockup's `w4Log` format).
- [ ] 🤖 `digest.get_digest`: the Markdown; `checks.json` (passed, regenerated, banner, writers, final checks, word counts); `raw_llm_output.json` (**metadata fields only:** provider, model, and per call the provider, latency, token counts, cost, regeneration, finish reason; never the prompt or the raw text); the injury-update addendum if present.
- [ ] 🤖 `games.get_games`:
  - the primary and `model_only` rows joined per game, with the Elo and market probabilities, spread, total and QBs;
  - QB changes from `payload.json`;
  - finals from curated `games` through a **read-only** DuckDB connection;
  - "biggest model vs market gaps".

  For the current unrun week: the slate from curated `games` (or the schedule snapshot), with kickoff, stadium and market.
- [ ] 🤖 `players.get_players`: the watch list, tough spots (payload), and the projections (main stat per group by default, all stats on request) with filters, team rows (P08) and injury status.
- [ ] 🤖 `results.get_results(season, week)`:
  - when week N+1's run exists, the graded view from the probe's sources;
  - otherwise `{"status": "not_graded", "graded_by_week": N+1}`.
- [ ] 🤖 `graph.get_graph`: from `graph_results.json`:
  - counts by label;
  - timings by stage;
  - rows per query;
  - the insights used (section, query, strength, confidence, text);
  - candidates not used, with the skip reason when known (`skipped`: started / novelty / duplicate player);
  - GDS status (P08).
- [ ] 🤖 Endpoints `GET /api/weeks/{season}/{week}/{pipeline,digest,games,players,results,graph}`.
- [ ] 🤖 Tests:
  - every reader on small fixture run folders (`tmp_path`), including a week-4-like folder with no `run_summary.json`, a week with a failed step, and the current week with no folder;
  - NaN → null;
  - every text scrubbed.
- [ ] 🤖 **Parity tests** (marked `integration`): the Games endpoint for 2026 week 4 matches `predictions_games.parquet` (16 games, every probability and score); the Players endpoint has 1,860 projections; the Graph endpoint's counts sum to the build's totals (15,522 nodes); the Digest Markdown is byte-identical to `reports/2026/week04-digest.md`.

### Web: the tabs
- [ ] 🤖 **Week header** as in the mockup:
  - status chips and slate tags;
  - for the current week, the countdown to first kickoff and the retry window, a live clock that updates every minute;
  - for past weeks, the publish time and the late / on-time chip;
  - "Open in W&B" filtered to `season:S` + `week:NN`;
  - the Saturday injury-update button shown greyed out with its reason (enabled in CR02).
- [ ] 🤖 **The three pipeline views**, ported from the mockup. One component per view, sharing one step model `{key, status, seconds, detail, progress}`:
  - **Drive chart:** an SVG field with each step at a yard line (ingest 30 … game 60, graph 72, player 85, digest = touchdown), the line of scrimmage (blue) and first-down line (yellow), arcs for finished steps, FUMBLE on a failure, goalposts for the run records (the extra point), the scorebug, and the drive log with down and distance, detail and time;
  - **Pipeline map:** the snake layout with sources → 9 steps → Published; progress rings; flowing edges into the running step; artifact chips appearing as each step finishes (W&B ones highlighted);
  - **Timeline:** a row per step with a bar on a time axis (striped = running, dashed = expected) and the digest's share of the run.

  In CR01 they render the **finished** state (real step times) and the **plan** state (expected times). Live animation is CR02, so build the components to take progress updates without re-mounting.
- [ ] 🤖 **View picker** on the Pipeline card: Drive chart · Pipeline map · Timeline · Surprise me. "Surprise me" picks a different view at random and shows a "surprise pick" chip. The choice is remembered per week (`localStorage` → `vizByWeek`, keyed by season and week), and the default is the Drive chart.
- [ ] 🤖 **Log panel:** the console styling, rebuilt lines for finished weeks, auto-scroll.
- [ ] 🤖 **Digest tab:** the Markdown rendered with `react-markdown` + `remark-gfm`, tables in scroll boxes. The right-hand rail: checks, words per section (bars), the writer (calls table), files, Saturday's addendum.
- [ ] 🤖 **Games tab:** tiles; the "disagrees with the market" card; game cards with the dot strip (shown model filled `--s1`, model only as a `--s1` ring, Elo `--s2`, market `--s3`, a tooltip on each dot) and the finals (hit / miss chips) when played. The current, unrun week shows the slate table with "after the run" placeholders.
- [ ] 🤖 **Players tab:** watch-list cards (projection, range bar with the baseline marker, the main driver, injury), tough spots, the projections table with group filters (QB, RB, WR/TE, EDGE/DL, LB/S, CB/S, TEAM from week 5) and mini range bars.
- [ ] 🤖 **Results tab:** tiles (picks, Brier model / Elo / market, watch list); the game-by-game table; player model vs baseline by group (bars); the watch-list table with the actual marked on the range (◆). When not graded yet: an empty state naming the run that grades it and its date.
- [ ] 🤖 **Graph tab:** tiles; the insights used (section chip, query chip, strength meter, confidence); rows per query (bars); "found but not used"; nodes by label; the Neo4j Browser link (`http://localhost:7474/browser/`).
- [ ] 🤖 **The current week before its run:** every tab except Pipeline and Games shows the mockup's empty state, with a link to the same tab of the last published week.
- [ ] 🤖 Component tests for each tab (fixture JSON), including the empty states and a week without run records.

### Close
- [ ] 🤖 **Mockup parity:** screenshots of week 4's six tabs and week 5's Pipeline / Games tabs, next to the mockup; differences listed in README §10.
- [ ] 🤖 Guide (`guides/control-room.md`): each tab, what it reads, how to read it. Skill: the reader patterns and the partial-week rule. PROGRESS.
- [ ] 🤖 Sol review of the readers (read-only guarantees, scrubbing, the raw-LLM metadata filter).
- [ ] ✋ **Checkpoint:** Rishi reads week 4 and week 5 in the app next to the published digests and W&B, and approves CR01.

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

## Pitfalls / notes

- **"Published" uses one rule everywhere:** `weekly.already_published` (the digest step ok + the report file). Don't invent a second one.
- **A week can have several runs** (not ready, then the run, then a resume). The records take the last run that published (`latest_runs`). The Pipeline tab shows the steps' latest state (`this_run` tells which steps ran in the last invocation).
- **The current week's slate before Tuesday comes from the schedule snapshot**, which can be stale. The calendar refreshes it under the lock during the run. Show the snapshot's date.
- **Kickoff times:** the files store UTC. Show US Eastern with daylight saving handled (`Intl.DateTimeFormat` with `America/New_York`), as the calendar does.
- **Playoff weeks (19–22):** they use the same files. The round's name comes from `ops.calendar.round_name`. Team totals have 0 rows in the playoffs (expected).
