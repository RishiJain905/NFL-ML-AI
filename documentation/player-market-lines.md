# Player market lines (ESPN): build plan

> **Status:** planned, not started. This file is the only change so far: no code, no config, no pipeline or model changes, no decisions logged yet.
> **Written:** 2026-10-08, from the planning conversation and checks against the project's data.
> **Depends on:** the weekly run ([weekly operations](guides/weekly-operations.md)), the control room ([guide](guides/control-room.md)), and decision D03 ([decisions log](10-decisions-log.md)).
> **Read first:** the [project brief](01-project-brief.md) (non-goals on betting and odds) and the documentation rules in [CLAUDE.md](../CLAUDE.md).

Tags follow the phase files: 🤖 the agent builds it, 🧑 Rishi runs it, ✋ checkpoint.

## 1. What this adds

Each player projection (P10, P50, P90) can sit beside the over/under line a sportsbook posts for the same stat. ESPN's public prop feed carries DraftKings lines, with the opening and current value for each. The line is captured during the weekly run, stored with that run's own projections, shown in the control room, and compared with the actual result after the games.

- It's a benchmark and a fun comparison, not a betting feature. The digest never shows lines (D03; brief non-goal 3). The control room shows line numbers only: no odds, no prices, no book links.
- The data comes from ESPN's public endpoints, not from a sportsbook site. No key, no login, no bot checks worked around.

Terms used here:
- **Line:** the over/under number (for example, 91.5 receiving yards).
- **Open / current:** the first and latest line ESPN shows.
- **P50, P10–P90:** the model's median and its 80% range.
- **Price / odds:** the payout on a side. Never stored or shown.
- **Milestone:** a yes/no threshold ("100+ yards"). Not a line; excluded.

Worked example, this week (real data):
- Jaxon Smith-Njigba, receiving yards, SF at SEA (Sunday 4:25 p.m. Eastern).
- DraftKings line via ESPN: 91.5 (opened at 94.5).
- Our projection from the Tuesday run: P50 80.4, P10 19.5, P90 146.0.

## 2. Decisions so far

| Topic | Decision | Source |
|---|---|---|
| Line source | ESPN public prop feed, DraftKings (provider 100). No key, no credits | Planning, 2026-10-08 |
| Offense markets | Yardage only: passing, rushing, receiving, and rushing + receiving (scrimmage). Scrimmage to confirm (Q1) | Planning |
| Defense markets | Tackles and sacks (sacks to confirm, Q2). ESPN has no pressures market | Planning, plus checks (section 3) |
| Receptions and carries | Back burner (B1): the control room shows those projections first | Planning |
| Capture timing | Inside the weekly run. Runs when Rishi clicks Run, on Tuesday after 10:00 a.m. Eastern | Planning |
| Build order | This document first. No code until it's approved | Planning |
| Prices | Never stored, never shown, never logged | D03, brief |
| Digest | Unchanged. No line numbers in the digest | D03, brief |
| Models | Untouched. No feature, training, or projection change | Frozen-models rule |

Timing note: "10 a.m. EST" means Eastern time. In October the US Eastern clock is on EDT (UTC−4). Code stores UTC; screens show Eastern.

## 3. What the data offers (checked 2026-10-08)

Checked on three games: SF at SEA (upcoming), LAC at SEA (Week 4, finished), and TB at DAL (upcoming; page 1 of 2 only). Counts are feed items per type across those games.

| ESPN type (id) | Items | Our target | Plan |
|---|---|---|---|
| Total Passing Yards (8) | 11 | pass_yds (QB) | V1 |
| Total Rushing Yards (12) | 34 | rush_yds (RB, QB) | V1 |
| Total Receiving Yards (13) | 65 | rec_yds (WR, TE) | V1 |
| Total Rushing Plus Receiving Yards (20) | 18 | scrim_yds (RB) | V1 (Q1) |
| Total Tackles (26) | 28 | tackles (LB, S) | V1; pick 26 or 27 in ML00 |
| Total Tackles Plus Assists (27) | 28 | tackles | see above |
| Total Sacks (28) | 16 | sacks (EDGE, DL) | V1 (Q2) |
| Total Receptions (14) | 66 | receptions | Back burner (B1) |
| Total Carries (11) | 18 | carries | Back burner (B1) |
| Total Passing Touchdowns (10) | 10 | pass_tds | Later (B2) |
| Total Passing Interceptions (15) | 12 | int | Later (B2) |
| Pressures | none found | pressures | No market in ESPN's feed |
| Milestone ladders ("Receiving Yards Milestones" and similar) | many | none | Excluded: threshold prices, not lines |
| Anytime, first, last, or 2+ TD scorer | many | td | Excluded: yes/no prices, not lines |
| First half, first quarter, team totals | many | none | Excluded: partial-game or team lines |

Findings:
- **Tackles:** our tackles are solo plus assists (`def_tackles_solo + def_tackle_assists`, [player_data.py](../src/nflengine/features/player_data.py), line 658). ML00 decides which ESPN tackle type matches that definition.
- **Pressures:** the dashboard watch list shows pressures, but none of the three games carried a pressures market. Treat as not offered until a full week confirms.
- **Sacks:** ESPN offers them, and the model projects them for eligible players, but the dashboard watch list doesn't show them. The Lines view in section 10 shows our projection for every market that has a line, so sacks don't depend on the watch list.
- **Receptions and carries:** the model projects them, but the Players tab reads only the watch list, which doesn't include them (week 5 watch list: pass_yds, pressures, rec_yds, rush_yds, tackles).

## 4. Source details (verified)

- **Event IDs:** from the curated `espn_scoreboard` table (`espn_event_id`, `game_id`), refreshed by the weekly ingest. No new calls.
- **Prop endpoint, one call per game:**
  `https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{eventId}/competitions/{eventId}/odds/100/propBets?limit=1000`
  - `100` is DraftKings (`.../casinos/100` returns DraftKings).
  - One call returned 630 and 673 items. A 1,645-item game reports `pageCount` 2. The page parameter isn't verified yet (ML00).
  - In the games checked, the competition ID equals the event ID.
- **Item shape** (trimmed from a real item; `...` marks shortened URLs):

  ```json
  {
    "athlete": {"$ref": ".../seasons/2026/athletes/4430878?lang=en&region=us"},
    "team": {"$ref": "..."},
    "type": {"id": "13", "name": "Total Receiving Yards (incl. overtime)"},
    "current": {"target": {"value": 91.5, "displayValue": "91.5"}},
    "open": {"target": {"value": 94.5, "displayValue": "94.5"}},
    "lastUpdated": "2026-10-08T06:14Z",
    "provider": {"$ref": ".../casinos/100?lang=en&region=us"},
    "competition": {"$ref": "..."}
  }
  ```

  No price fields appear in any item checked. `current` and `open` hold only `target`.
- **Duplicates:** the same athlete and type can appear as identical items. Smith-Njigba's receiving line appeared twice with identical values. Deduplicate on event, athlete, type, and value.
- **Team and game props** have no athlete reference. Drop them. Every V1-type item checked had one.
- **Player mapping:** curated `player_ids` maps `espn_id` to `gsis_id`. All 96 player athletes in the three games matched, so no roster calls are needed. Fallback: team rosters at `https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{espnTeamId}/roster`.
- **Athlete record:** its alternate ID is labelled `sdr`, not a GSIS ID. Not useful for mapping.
- **Timestamps:** `lastUpdated` isn't a reliable pre-kickoff time. In the finished LAC at SEA game, most lines were last updated 2–3 hours after kickoff. Our own capture time is the only trustworthy time.
- **Request cost:** one call per game for up to 1,000 items; two for bigger slates. The project's HTTP client already spaces ESPN requests at least 0.5 s apart ([http.py](../src/nflengine/ingest/http.py)).
- **Terms:** undocumented and unofficial, the same posture as the existing ESPN ingest. Personal, private use (D26). Raw feeds are never published.

Coverage in the games checked (distinct players with a total-type line):
- SF at SEA: 8 receiving, 8 receptions, 4 rushing, 2 passing, 1 carries.
- LAC at SEA: 16 athletes with yardage lines; 14 had a Week 4 projection.

## 5. Test runs (2026-10-08)

- **SF at SEA (this week):** Smith-Njigba's line 91.5 (opened 94.5; last updated 2026-10-08 06:14 UTC). Our Tuesday projection (created 2026-10-07 00:35 UTC, Tuesday 8:35 p.m. Eastern): P50 80.4. Receptions: line 6.5, our P50 6.0.
- **LAC at SEA (Week 4, finished):** 14 yardage props matched. Our P50 was closer on 11, the line on 3, no ties. Mean absolute error 14.7 (ours) vs 16.6 (line). One game, and most timestamps fall after kickoff, so these may include in-game moves. It's a pipe check, not a result.

These two are the hand checks for the tests in section 14.

## 6. Scope

**In V1:**
- Markets: pass_yds, rush_yds, rec_yds, scrim_yds, tackles, and sacks (subject to Q1–Q3).
- Players: every player with a V1 line and a projection in that run, not only the watch list.
- Week: the target week, every game with an ESPN event. Playoff weeks if lines exist (Q6).
- Outputs: a run-folder file, a block in the run summary, a step detail, a read-only API, a Lines view on the Players tab, and a comparison card on Results.

**Not in V1:** receptions and carries (B1), passing TDs and interceptions (B2), pressures (no market), milestones, yes/no and team props, closing-line capture (B3), the Saturday injury update, Odds API props (B4), multi-book lines, betting features, digest changes, and any model or feature change.

## 7. The capture step

### 7.1 Where it runs
- A new fail-soft step, `market_lines`, in the weekly run, after `player` and before `digest`. The current step tuple is `ingest, ready, curate, ratings, game, graph, player, digest`.
- Failures raise `StepDegraded`, the same pattern the graph step uses. The run continues, and the digest is unaffected.
- It runs with the Run button and with `nfl weekly run --auto`. The Run button's allowlist doesn't change (D95): the step lives inside the run.
- Not in `--as-of` simulations (`SIM_STEPS` is `ready, digest`). Not in rehearsals (D90 runs the live steps on past weeks, where live lines would be wrong). Not in the Saturday injury update.

### 7.2 When it runs
- Rishi clicks Run on Tuesday after 10:00 a.m. Eastern. The step captures what ESPN shows at that moment.
- A warning (not a block) if Run is clicked before 10:00 a.m. Eastern (Q5).
- Expected effect: lines for later games may be thinner on Tuesday. The run summary records coverage per game.

### 7.3 What it does
1. Read the target week's events from `espn_scoreboard`. Skip games already started.
2. For each event, one `propBets?limit=1000` call, plus a second page if `pageCount` says so.
3. Keep items whose type is in the V1 map. Drop team and game props, and drop duplicates.
4. Map each athlete to a `gsis_id` through `player_ids`. Keep unmatched athletes with a reason.
5. Pair each line with the run's own projection for the same player and target (`predictions_players.parquet`): P10, P50, P90.
6. Write raw pages to `raw/espn_props/season=S/week=WW/run=<run_id>/` (kept, never overwritten). Write `market_lines.parquet` into `runs/S/week<NN>/`, next to the run's projections.
7. Write a `market_lines` block to `run_summary.json`: games, lines, matched, unmatched, no projection, calls, seconds, first and last capture times.
8. Emit events (`step_start`, per-game `progress`, `step_end`) so the live views show the step.

### 7.4 Limits and failures
- **Calls:** at most 40 per run. 15-second timeout per call. 180-second budget for the whole step. Past the budget, the step degrades and keeps the games it has.
- **Errors:** the existing client retries transient errors with backoff. A 403, or repeated 429s, stops the step (degraded). No retry loops.
- **Any exception** becomes `StepDegraded` with a scrubbed reason. The run continues, and the UI says "lines not captured this run".
- **Secrets:** none. No key, no login.
- **Allowlist:** the parser stores only the fields in section 8. Any text kept goes through the project's `scrub()`.

### 7.5 Re-runs and pairing
- A re-run writes a new raw folder and overwrites `market_lines.parquet` together with the new `predictions_players.parquet`. Both carry the same run ID and projection timestamp. A check refuses a mismatch.
- A resume from `player` or earlier also runs `market_lines`. A resume from `digest` leaves both files as they are.
- The Saturday update writes `predictions_players_update.parquet`. V1 never reads it.

## 8. Data model

Market map (IDs verified; the tackles choice is pending ML00):

| ESPN type id | Market (our target) |
|---|---|
| 8 | pass_yds |
| 12 | rush_yds |
| 13 | rec_yds |
| 20 | scrim_yds |
| 26 or 27 | tackles |
| 28 | sacks |

`runs/<S>/week<NN>/market_lines.parquet`, one row per line:
season, week, run_id, projection_created_at, captured_at_utc, game_id, espn_event_id, gsis_id (nullable), espn_athlete_id, player_name, team, market, espn_type_id, espn_type_name, line_open, line_current, line_last_update_utc, provider, match_status (`matched`, `no_projection`, `no_player_id`), projection_p10, projection_p50, projection_p90, duplicate_count.

No price, odds, or book-link columns. A test asserts it.

Rows with `no_projection` or `no_player_id` stay in the file. They count toward coverage but not toward the comparison.

Fix file: `config/market_id_fixes.csv` for any athlete the mapping misses, following the pattern of `config/head_coach_fixes.csv` (D92). Empty to start.

## 9. Comparison and scoring

- **Projection:** P50 from the same run's `predictions_players.parquet`. Count targets (tackles, sacks) also have a P50 in the model, so every market uses P50. The watch list's mean for counts isn't used here.
- **Line:** `line_current` at capture. `line_open` is shown too.
- **Outcome:** the actual stat from the box score (`player_games`) after the game. Players who didn't play are excluded (the scoreboard's `played` rule).
- **Per pair:** |actual − P50| against |actual − line|. The closer one wins. Ties are counted apart.
- **Summary, by market and season-to-date:** pairs, closer counts, mean absolute errors, and a bootstrap 95% interval for the mean difference (2,000 resamples, fixed seed).
- **Minimum sample:** "too few yet" until a market has 30 pairs.
- **Counts (tackles, sacks):** ties are common. Report them; don't hide them.
- **Not used:** over/under hit rates (they read like a betting record, which the brief rules out), prices, and anything after the capture.
- **Information timing:** projection and line both reflect Tuesday's information. That's fair, and the UI says so.

## 10. Control room (mockup first)

Control-room design changes follow the project rule: mockup, then spec, then code (D96). The mockup update is a ✋ step.

- **Players tab, new Lines section:** every player with a V1 line in the week, not only the watch list. Columns: market, our P10 / P50 / P90, line (opened → current), capture time, and a gap in words ("line above our 90th percentile"). Tooltip: "DraftKings line via ESPN". No prices, odds, or book buttons.
- **Watch-list rows:** a small badge when a line exists.
- **Results tab, new card after the games:** pairs, closer counts, mean errors, the biggest misses, and "too few yet" wording.
- **API:** `GET /api/weeks/{season}/{week}/market-lines`. Read-only, from the run-folder file, scrubbed, `no-store`. The app never calls ESPN.
- **Pipeline views:** check that the new step and its artifact appear in the drive chart, the pipeline map, and the timeline.
- **Layout:** both themes; phone width (control-room rules).

## 11. Digest, graph, W&B, models

- **Digest:** unchanged. A test asserts the payload and checks are identical with and without the step, on the same inputs.
- **Graph:** unchanged in V1.
- **W&B:** no new runs or charts in V1. The pipeline run's step detail includes the step. If a lines scoreboard is logged later, the W&B guide updates in the same commit (CLAUDE.md).
- **Models and features:** no change. Nothing under `models/` or `features/` is touched.

## 12. Config

A `market_lines` block in `config/settings.yaml`:
- `enabled: true`
- `markets:` the V1 list
- `max_calls: 40`
- `time_budget_s: 180`
- `min_pairs_to_show: 30`
- `include_playoffs: true` (Q6)

No new environment variables. `nfl doctor` gets no new check (it would have to call ESPN).

## 13. Security, compliance, and rate limits

- Public ESPN endpoints only: no key, no login, no bot-check workaround, no sportsbook site.
- Calls happen per run: one per game (two for big slates), capped at 40. No polling, no background jobs.
- Prices are never fetched into storage (the parser's allowlist), never shown, never logged.
- Personal, private use (D26). Nothing published.
- If ESPN starts refusing requests: the step degrades, the run summary says why, and nothing retries in a loop.

## 14. Tests

Fixtures, trimmed copies saved under `tests/fixtures/market_lines/`: the three game pages (TB at DAL page 1, SF at SEA, LAC at SEA), the LAC at SEA Week 4 projection rows, and the matching box-score rows.

- **Parser:** LAC at SEA gives 16 athletes with yardage lines and 22 athlete-type lines after dedupe, 14 matched to Week 4 projections. Duplicates dedupe. Milestones, yes/no, team, and first-half items are dropped. `lastUpdated` parses.
- **Mapping:** every athlete reference in the three games maps through `player_ids`. Unmatched rows report a reason.
- **Step:** a timeout, 500, 403, malformed JSON, or budget overrun each gives `StepDegraded`. The run continues and the digest payload is unchanged. The call cap holds. Simulations and rehearsals skip the step.
- **No prices:** an assertion that no output file or key contains odds or price fields, raw pages included.
- **Pairing:** `market_lines` and `predictions_players` carry the same run ID and projection timestamp. A resume from the digest keeps them as they are. The Saturday update file is never read.
- **Metrics:** the LAC at SEA check reproduces exactly (14 pairs, 11 model, 3 line, 0 ties, 14.7 vs 16.6). The bootstrap is deterministic under a fixed seed. Ties and not-played exclusions behave.
- **Control room:** reader output matches the run file (parity test). The API returns `no-store`. Both themes render. The "not captured" state renders.
- **Live:** 🧑 the first Tuesday run (section 16).

## 15. Phases and tasks (proposed)

**ML00: Probe and fixtures**
- 🤖 Verify the paging parameter on TB at DAL (event 401872980). Page 2 should hold the remaining 645 items.
- 🤖 Verify tackles: compare ESPN's types 26 and 27 with `def_tackles_solo + def_tackle_assists` for about ten defenders in one finished game. Pick the matching type (Q3).
- 🤖 Check playoff weeks for lines when they exist (Q6). If none exist yet, mark as unknown.
- 🤖 Save the fixtures in section 14.
- ✋ Rishi confirms the market list (Q1, Q2) and the tackles type.

**ML01: Capture step**
- 🤖 ESPN client on the project's `PoliteClient` (pacing, retries, caps, timeouts). Parser with an allowlist. Dedupe. Mapping through `player_ids` and the fix file. Writers for `market_lines.parquet` and the raw pages. The `market_lines` summary block. Events. `StepDegraded`. The step order (after `player`). Simulation and rehearsal guards.
- 🤖 Config block and tests (section 14).
- ✋ Rishi reviews the step code and a dry run on a finished week (writes to a scratch folder, no run lock).

**ML02: Comparison**
- 🤖 Reader (run folder, projections, box scores). Metrics (section 9). Summary tables. Tests, including the hand check.
- ✋ Rishi reviews the metric definitions before anything is displayed.

**ML03: Control room**
- 🤖 Mockup update (Lines section, Results card, watch-list badge).
- ✋ Rishi approves the mockup. Then the spec, then the code: the API route, the pages, and tests (both themes, phone width, parity).

**ML04: Docs and first live week**
- 🤖 New guide `documentation/guides/market-lines.md`. Updates to: the weekly-operations guide, the control-room guide, the runbook (Tuesday's click now also captures lines), `03-data-sources.md`, `02-system-architecture.md`, `11-prediction-targets.md`, the brief (lines in the control room, never prices), and the decisions log (new entries from D107, including the D03 revisit). Skills: `weekly-ops`, `control-room`, `curated-data`, and the planned `adding-a-data-source`. Also `documentation/README.md` and `plans/PROGRESS.md` when the phase starts.
- 🧑 Click Run on the first Tuesday after 10:00 a.m. Eastern with the feature on. Work through the checklist in section 16.
- ✋ Rishi closes ML04 once the first live week reads correctly.

**Back burner (after ML04)**
- **B1: receptions and carries.** First, the control room shows our receptions and carries projections (the Players tab only lists the watch list today). Then add markets 14 (receptions) and 11 (carries).
- **B2: passing touchdowns (10) and interceptions (15)**, if wanted.
- **B3: closing-line capture.** A second capture before kickoff, using the same step with a window argument. Costs about the same calls again.
- **B4: Odds API props.** Only if a free-tier props test passes and Rishi OKs one credit. Not needed for V1.
- **B5: Odds API client fix (adjacent; do anytime).** `src/nflengine/ingest/odds_api.py` rotates keys on any 429. The Odds API docs describe 429 as a burst rate limit, so a burst could burn the backup key. Fix before any further Odds API work.

## 16. Acceptance and first-week checklist

**Acceptance**
1. A Tuesday run (clicked after 10:00 a.m. Eastern) captures lines for every game with an ESPN event. The summary shows each game's count.
2. At least 95% of captured V1 player lines map to a `gsis_id`. Every unmatched line is listed.
3. The digest payload and checks match with and without the step.
4. An ESPN outage or malformed feed: the run completes, the step is degraded, and the UI says "not captured".
5. No price fields anywhere on disk or in the API.
6. ESPN calls per run stay within 40. The step adds about a minute on a normal Tuesday.
7. The LAC at SEA check reproduces from fixtures.
8. The Lines section and Results card match the run file in both themes.

**First live week (🧑)**
- The events file shows `market_lines` starting and ending.
- The summary's `market_lines` block shows counts per game, unmatched rows, and calls.
- The Lines section shows the pairs with the capture time and the projection time.
- Spot-check a few names by eye for unexpected players.
- Confirm there are no prices anywhere.

## 17. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| ESPN changes or blocks the feed | No lines this week | Degrade, alert, parser fixtures. No V1 fallback source |
| Tuesday coverage is thin for later games | Fewer pairs early | Record coverage; UI says "no line yet"; B3 later |
| Tuesday lines aren't the closing market | A less sharp benchmark | Compare Tuesday with Tuesday (fair) and say so in the UI |
| Small samples early | False verdicts | "Too few yet" under 30 pairs; ties reported |
| ID gaps | Missed pairs | Review list; fix file |
| Tackles definition differs | Wrong comparison | ML00 check before mapping |
| Sacks lines are 0.5 or 1.5 | Coarse comparison | Report separately; ties visible |
| Clicks before 10:00 a.m. Eastern | Thinner lines | Warning (Q5) |
| Timezone confusion | Wrong times shown | UTC in storage; Eastern on screen |
| Scope creep into betting | Brief and D03 violated | Lines only; a test asserts no prices |
| Unofficial API terms | Access could change | Personal use; nothing published (D26) |

## 18. Open questions (answer before ML01)

- **Q1** Scrimmage yards (rush + rec) in V1? Default: yes.
- **Q2** Sacks in V1, reported as coarse? Default: yes.
- **Q3** Tackles: ESPN "Total Tackles" (26) or "Tackles Plus Assists" (27)? Default: whichever ML00 matches to our solo-plus-assists definition.
- **Q4** Lines view on the Players tab (default) or a new week tab?
- **Q5** Warn when Run is clicked before 10:00 a.m. Eastern? Default: warn only.
- **Q6** Playoff weeks in scope if lines exist? Default: yes.
- **Q7** Keep every raw capture? Default: yes (about 6–12 MB per run).
- **Q8** Confirm no capture in the Saturday update? Default: no.

## 19. Files expected to change (proposal)

- New: `src/nflengine/ingest/espn_props.py` (client, parser, allowlist, dedupe).
- New: `src/nflengine/ops/market_lines.py` (step, mapping, writers, metrics).
- Changed: `src/nflengine/weekly.py` (`market_lines` in the step tuple after `player`, degrade handling, simulation guard) and the run-summary builder (the `market_lines` block).
- New: `src/nflengine/app/readers/market_lines.py` and its route; the web Lines view and Results card (after the mockup is approved).
- Changed: `config/settings.yaml` (the `market_lines` block).
- New: `config/market_id_fixes.csv` (empty to start).
- New tests and fixtures under `tests/` (ingest, ops, app).
- Not touched: `src/nflengine/models/`, `src/nflengine/features/`, the digest payload.

Deliberately not done yet: no index line in `documentation/README.md`, no PROGRESS entry, no decisions-log entries, no commit. Those come when the plan is approved.
