---
name: live-decisions
description: How the live 3rd- and 4th-down bot is built, trained, backtested, scored and tested (LD00+, documentation/live-decisions/). Use when touching src/nflengine/live/ or tests/live/, the six decision models (wp, gain, fg, punt, kickoff, pass), the decision engine (go / field goal / punt, the 3rd-down if-stopped table, confidence labels and the bootstrap gate), ESPN's live feed (LD01: the client, the state parser and its quirks, replay, latency, the parity check vs nflverse), the control room's Game day tab (LD02: the live endpoints and `LiveService`, the team context `live/context.py`, `nfl app --live-replay` and the replay feed), `nfl live train | backtest | call | games | replay | latency | parity`, the `live-decision-models` W&B artifact and its own production alias, the `live:` block of settings.yaml, or when a call looks wrong. Also covers the no-touch rule (D107) and the production-unchanged check every LD / PC / AE phase closes with.
---

# Live decisions (the 3rd / 4th-down bot)

Spec: `documentation/live-decisions/README.md` (+ phase files LD00-LD03). Plain-language guide: `documentation/guides/live-decisions.md`. Model card: `documentation/model_cards/live-decision-models.md`. Decisions: D107-D109, D113-D119.

## 1. The no-touch rule first (D107)

- New code lives in `src/nflengine/live/`; it **never imports** from `nflengine.models` or `nflengine.features` (copy small helpers instead) and never writes to production folders, aliases or the weekly run.
- It may **read** curated data and published outputs (`features/team_ratings.parquet`, market lines).
- Shared files change only by adding: a CLI group (`nfl live`), the `live:` block in `config/settings.yaml`, `AppConfig.live`.
- `tests/test_production_untouched.py` hashes every protected file (`src/nflengine/{models,features,digest,graph,ingest,curate,ops}/**`, `weekly.py`, `schedule.py`) and the parsed pre-existing settings blocks against `tests/fixtures/production_manifest.json`. A failure reads "a protected file changed: production models are off limits (D107)". **Regenerating the manifest needs Rishi's OK** and its own commit (`uv run python tests/test_production_untouched.py --write-manifest`).
- Every LD / PC / AE phase closes with the **production-unchanged check**: that test passes + a rehearsal of the newest published week reproduces its predictions (`weekly-ops` skill §5c; see §7 below for how LD00 did it).

## 2. Map of the package

| File | What it holds |
|---|---|
| `schema.py` | `GameState` (offense's side; `home` +1/0/−1; `spread` from the offense's side; validation in `check_state`), `era_of` (0 = 2010-14, 1 = 2015-17, 2 = 2018-20, 3 = 2021-23, 4 = 2024+), `kickoff_era` (tb20 / tb25 / tb30 / tb35), `pat_era`, OT rules, feature lists, the 77 gain classes, FG constants (distance = yards to goal + 18; a miss → spot of the kick, 8 yards back, or the 20), `state_features` |
| `data.py` | `load_plays` (REG + POST, 2010+, game order, `neutral_site` from `games`), `with_state` (the offense-side columns, same formulas as `state_features`), the frames `wp_frame` / `gain_frame` / `fg_frame` / `punt_frame` / `kickoff_frame` / `pass_frame` / `fourth_frame`, `with_ratings`, `state_of_row` |
| `models.py` | `settings()` (code `DEFAULTS` < `live.models`), `WPModel` (logistic base on 5 smooth terms + LightGBM boosted from it via `init_score`; `_fit_wp_base` drops a monotone term whose coefficient comes out negative), `fit_wp` / `fit_gain` / `fit_fg` / `fit_punt` / `fit_kickoff` / `fit_pat` / `fit_pass`, `fit_all` (**filters every frame to the given seasons itself**), `LiveModels`, `save` / `load` (LightGBM text + JSON), `legal_gain`, `conversion_prob`, `fg_distance_only` |
| `decide.py` | `Engine(models, cfg, threads=4)`: `fourth_down(state, bootstrap=True|False|"all")` → `Decision`, `third_down(state)` → `ThirdDown` (convert %, pass %, the if-stopped table), `fourth_downs(states)` for backtests; `kneel_out`, `wp_row`, `compress` |
| `train.py` | `run_train` (`nfl live train`), `run_checks` (hand-made calls, timing, the bootstrap gate), `production_folder` / `load_production` |
| `backtest.py` | `run_backtest` (`nfl live backtest`, leave one season out), `ship_verdict` |
| `espn.py` (LD01) | `EspnClient`: `scoreboard(season, week)`, `game(event)`, `summary(event)` -> `Fetched` (`data`, `fetched_at`, `ms`, `cached`, `stale`, `error`, `age_s()`); `check_url` (only `https://site.api.espn.com`), `check_event_id` (digits), `espn_week` (playoffs = type 3, weeks 1/2/3/5); `EspnError` (message safe to show) |
| `state.py` (LD01) | `GameContext` + `load_contexts(season)` -> `Contexts` (`game_id_for`, `context(event, json)`; curated `games`, `lines`, `espn_scoreboard`, `weather_forecasts`, read only); `pre_game_lines`; `build_state(ctx, Snap)` (the one builder for live and replay); `parse_event(event, ctx, fetched_at, summary)` -> `LiveState` (`state` or `reason`, `warnings`, `is_decision`); ESPN helpers `event_teams`, `summary_plays`, `opening_receiver`, `play_times`, `last_real_play`; `plain` (text as data) |
| `replay.py` (LD01) | `replay(summary, ctx)` -> `ReplayPlay` list (state, choice, converted, timeouts from text, score before, scoring-play snap clock), `decision_plays`, `load_summary` (saves finished games under `live/summaries/`), `timeout_window`, `choice_of`, `team_names`, `scoring_start_clock` |
| `latency.py` (LD01) | `run_latency` (`nfl live latency`: game every 2 s, summary every 20 s, JSONL log), `summarize` (lag, lead, 4th-down lead) |
| `parity.py` (LD01) | `run_parity` (ESPN replay vs nflverse on 3rd / 4th downs), `decision_parity` (the same 4th downs scored from both sources) |
| `context.py` (LD02) | "Is this team good at this?": `build_context(season, week, paths=, engine=, model_version=)` (every team, as of the week's start: this season's weeks < week + last season whole), `team_context(ctx, team)`, `ContextCache(folder).get / warm(season, week, paths=, engine_factory=, model_version=)` (memory -> `{folder}/{season}-wNN.json` validated by `source_stamp` -> build; one build per key), `coach_fixes` (a copy of the graph's reader of `config/head_coach_fixes.csv`) |
| `replayfeed.py` (LD02) | `ReplayFeed.from_folder(live/summaries, event, at=, speed=, lag_s=)`: a finished week's saved summaries served as ESPN's scoreboard / game / summary answers at a moving replayed time (`transport()` for `EspnClient`, `now()`); `effective_times` (monotone play times), `parse_at` (`--replay-at`, no zone = Eastern) |
| `show.py` (LD01) | terminal text: `header_lines`, `fourth_lines`, `third_lines`, `replay_row`, `game_rows`, `down_text`, `spot` |

Data on D:: `models/live-decisions/<first>-<last>_<UTC stamp>/` (wp.txt, gain.txt, pass.txt, gain_boot/NN.txt, fg.json with the bootstrap coefficients, punt.json, kickoff.json, pat.json, meta.json, checks.json) and `models/live-decisions/production.json` (the pointer the live code loads; the app reads files, not the alias). Backtests: `runs/backtests/live-decisions/<label>/`. The feed's files (`paths.live_data` = `{root}/live/`, **not** `paths.live`, which rehearsal paths use for the real root): `summaries/<event>.json.gz`, `latency/<event>.jsonl`, `parity/`, `probe/` (ESPN responses + Wayback live scoreboards from 2026-10-10).

## 3. Commands

```bash
uv run nfl live backtest                 # LOSO 2014-2025, W&B group live-decisions / live-backtest (~2.5 min)
uv run nfl live backtest --smoke --no-wandb --no-save   # two seasons, a code check
uv run nfl live train --promote          # fit 2010-2025, checks, artifact live-decision-models:production
uv run nfl live call --state '{"season": 2025, "score_diff": 0, "game_seconds": 2400, "half_seconds": 600, "down": 4, "ydstogo": 1, "yardline_100": 40}'
uv run nfl live games [--week N]         # ESPN's games, event ids, status, down & distance (1 call)
uv run nfl live call --event ID [--json] # a game right now (+ the summary in the first half / on down -1)
uv run nfl live replay --event ID [--downs 4] [--bootstrap] [--refresh]
uv run nfl live latency --event ID --minutes 200   # live lag log -> live/latency/<event>.jsonl
uv run nfl live parity --season 2026 --weeks 1-5 [--decisions ev1,ev2]
uv run nfl app --live-replay 401872966 --replay-at 2026-10-04T15:57:15 [--replay-speed 4] [--replay-lag 30]   # Game day on a replayed week
uv run python documentation/live-decisions/mockup/dump_payloads.py   # the mockup's data: the real API on replayed moments
uv run pytest -q tests/live              # unit tests (stub models, fixtures, fake transport; no data, no network)
uv run pytest -q -m integration tests/live/test_bundle_integration.py   # the promoted bundle
```

## 4. How a call is computed (keep these rules when changing the engine)

- Every state after a choice is a **first down for someone** at a spot, scored by `wp` from that team's side (`1 - p` when the defense has the ball). Each play takes 6 s (`SECONDS_PER_PLAY`).
- Rules before the model (`Engine._rule`, after `_next_period`): time up in the second half or overtime → the score decides (tie = 0.5), except a **tied playoff OT period, which goes on** into a new 15-minute period (`schema.ot_minutes(season, playoffs)`); first half over → the second-half kickoff to the `receive_2h_ko` team with **both teams back to 3 timeouts** (`possession(..., fresh=True)`); **kneel-out** (second half, a lead, a first down, `gs <= 6 + 40 * (3 - other team's timeouts)`) → 1 / 0.
- Touchdown (ours, or a kickoff / punt return touchdown) → +7 with the era's extra-point rate, else +6 (`_try_parts`) → kickoff (`_then_kick` handles time up / halftime first) from the season's kickoff distribution (`kickoff.at(season)`: tb35 from 2025, the receiver's mean start ~ own 30.5). Field goal → +3 → kickoff. Miss → their ball at the spot of the kick. Punt → `punt.at(yard line)`. Safety → their ball at `SAFETY_START` (60 yards to goal).
- Distributions are compressed to 12 receiving spots (`compress`) and all states of a call are deduplicated and scored in **one** `wp` predict (`_Batch`). The go branch is vectorised over the 77 classes (`_go_plan` / `_go_values`).
- **Confidence:** the 20 bootstrap refits of `gain` + `fg` re-score the call; Confident >= 90% wins, Lean 60-90%, Toss-up < 60% or gap < 1 point. **The gate (D113):** only calls closer than `live.decide.boot_gap` (5 points) are bootstrapped; wider ones are Confident because none flipped in 1,498 real 2025 4th downs; `run_checks` re-validates this on ~400 real 4th downs at every train (`checks.json` → `gate`), and `--promote` refuses if it fails.
- **Speed:** a 4th-down call ~1-6 ms; a 3rd-down check (the if-stopped table, bootstraps included) must stay under 20 ms (`TIMING_BUDGET_MS`; checked at train time). The cost is the bootstrap gain predictions (77 classes x 50 rounds = 3,850 trees per model), memory-bound: more threads don't help past 4. **Time it on an idle machine** (a parallel model fit doubled the numbers once).

## 5. Gotchas

- `spread` in a `GameState` is from the **offense's** side (nflverse `spread_line` is home-side, + = home favored). With the spread in the model, `home` has a *negative* effect at a pick'em (the spread already holds home field): don't "fix" it.
- `game_seconds = half_seconds + 1800` in the first half; 1800 / 0 is the end of Q2, 1800 / 1800 the second half's start (`first_half` uses the difference, never `game_seconds > 1800`).
- In overtime nflverse sets both clocks to the OT clock (max 900 to 2016, 600 from 2017).
- `kick_distance` = yards to goal + 18 on 90% of kicks (17 or 19 otherwise); the miss spot matches "+8" not nfl4th's "+7".
- The gain model's ratings variant (`--ratings`) needs `off_epa` / `def_epa_opp`, which a live `GameState` doesn't carry: only a research variant unless shipped with new state fields.
- LightGBM `predict(..., num_threads=1)` for small batches; `deterministic=True` everywhere.
- Bootstrap refits of `fg` share the main model's `wind_fill` (one design matrix for all; `Engine` refuses otherwise).

## 5b. What LD00 shipped (2026-10-10; D113, D114, D115)

- `production` = `2010-2025_20261010T054343Z` (W&B `hxmgioka`): wp 7 leaves x 150 rounds boosted from the logistic base; gain 4 leaves x 50 rounds at lr 0.15; pass 31 x 600; 20 bootstraps.
- Backtest `m70hg3pa` (final, after the Sol fixes; the ship decision was made on `p3y90nh0`, identical model metrics): wp Brier 0.1481 vs `vegas_wp` 0.1469, calibration 0.0048; gain 12 / 12; fg 10 / 12; pass 6 / 12 (loses only inside `xpass`'s 2006-2019 training window; Rishi: ship all six). The ratings variant lost (1 / 12).
- **The bot is aggressive** (go on 51% of 4th downs vs coaches' 17%). It comes from the 4th-down conversion chances (between the 3rd-down rate and the attempted-4th-down rate), not the WP model: an engine valuing the same futures with nflfastR's `ep` was *more* aggressive. Don't "fix" it inside LD01 / LD02; LD03 measures the bot's chances against real attempts.
- Timing on this machine: 3rd & 6 14.6 ms, 3rd & 10 19.6 ms (budget 20): any engine change must re-run `run_checks` on an idle machine.

## 5c. The live feed (LD01): rules that bite

- **Only `site.api.espn.com`**, through `EspnClient` (never `ingest/http.PoliteClient`: `ingest/` is protected, and the live rules differ). 2 s per game (game + summary share it), 0.5 s between any two requests, 5 s timeout, back-off 2 -> 60 s, stale answers instead of errors when there's an earlier one. `follow_redirects=False`.
- **`yardLine` is from ESPN's home team's goal line** (`100 - yardLine` when the offense is ESPN's home team). Never read `yardsToEndzone` (mirrored on home punts, 0 on timeouts). ESPN's home can differ from nflverse's at neutral sites: yard lines use ESPN's, `home` / spread sign use nflverse's.
- **`down = -1`** = no snap pending (TV timeout before a kickoff or try); a team timeout keeps the down. The summary fallback for the next snap is cut at the scoreboard's `lastPlay.id` and refused when the summary doesn't have that play yet (separate requests: Sol review). `End of Half` (0:00 Q2) shows a bogus 1st & 10 with no `possession`: `parse_event` returns "halftime".
- **The situation's `lastPlay` has no `wallclock`**: snap times come from the summary (joined by play id). Summary plays have only `clock.displayValue` (no `value`); it's the **snap** clock except on scoring plays (the clock at the score: `scoring_start_clock` estimates the snap).
- **ESPN play id = event id + nflverse `play_id`** for snaps (timeouts' ids differ). The summary has no timeouts remaining: count "Timeout #N by TEAM" per half (2 in regular-season OT) plus lost challenges ("... challenged ..., and the play was Upheld", "(Timeout #N.)" sometimes missing). Scores move only on `scoringPlay` rows (stray 0-0 on some timeout / two-minute-warning rows). PATs and two-point tries are inside the touchdown play's text; the TD row's score includes the try.
- **ESPN's play type isn't what happened** (`choice_of` reads the text first): "No Play" in the text = a penalty whatever the type (73 snaps typed Rush / Pass / Punt in 2026 weeks 1-5, all nflverse `no_play`); " punts " / "field goal is" = punt / fg whatever the type (a fumbled punt return is typed "Fumble Recovery (Opponent)"); a fake punt is "Rush" + "(Punt formation)" = go.
- **Spread / total are pre-game only** (`pre_game_lines`: nflverse -> Odds API median -> ESPN, the last two only if saved before kickoff day **in Eastern time**: `snapshot_date` is the ingest machine's local date, and a Thursday 20:15 ET game is Friday in UTC; Sol review).
- **Latency:** `pre_snap_penalty(text)` ("No Play" with no play described before "PENALTY") marks false starts / delays of game; they are logged but never counted as snaps or the "next snap". A restart keeps each play's earliest sighting; Ctrl+C still writes the `end` report.
- **Parity gate:** `share_equal` counts states found on one side only as not equal (a feed that drops plays can't pass); `share_equal_overlap` is the share among states both sides have. Never ESPN's live odds (null in game anyway).
- **Roof:** `games.roof` is null at retractable-roof stadiums until nflverse fills it after the game: `load_contexts` uses the stadium's usual roof, then ESPN's `venue.indoor`.
- **ESPN text is data:** `plain()` in the package, `rich.markup.escape` in the CLI, `clean()` in the app (LD02). A test prints `[bold red]` literally.
- **Live fixtures:** our ingest never captured a game in progress; real `situation` blocks came from Wayback captures of the scoreboard (`web.archive.org/cdx/search/cdx?url=site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard&matchType=prefix`, fetched with `.../web/<ts>id_/<url>`). Trimmed copies live in `tests/fixtures/live/`.
- **Rich in tests:** `CliRunner` gives Rich an 80-column console, which wraps table cells and long lines; set `cli.console._width` (monkeypatch) before asserting on text.

## 5d. The Game day tab (LD02): rules that bite

- **The service:** `src/nflengine/app/readers/live.py` `LiveService` (one per app, `AppSettings.live`): one shared `EspnClient` for every browser tab, the engine loaded once (warmed in a thread at the first list; a load failure is remembered 60 s), `load_contexts` per season (re-read when a curated file's stat changes), the predictions per week, the play logs (`_summaries`, at most 16; dropped when a game is post), first sightings of each play id (`_seen`), each game's last check (`_checks`). Routes in `app/server.py` near "LD02"; `LiveRefused(status, code, message)` becomes the error envelope.
- **Game day's week** = the first week whose last kickoff + 6 h is still ahead (`live_week`), never the calendar's week (it moves to N+1 at week N's last kickoff). Replay mode: the replayed week. Past / future weeks never ask ESPN.
- **Nothing is fetched until a click** (an exit criterion, tested by counting the fake transport's requests): the list = one `scoreboard` call kept 30 s (`?refresh=1` re-asks only when over 10 s old); a check = one `scoreboard/{event}`; the summary at once only for a first-half check before the opening receiver is known or on `down = -1`, else one background refresh after a check (at most every 30 s per game; it waits for the 2 s rule *outside* the client's lock). A check validates the event against the newest board of any age (refreshed only when the id is unknown).
- **The client (`espn.py`) checks every rule under its lock (memory, a request in flight, the back-off, the 2 s / 0.5 s spacing from *actual* send times), sleeps outside it and checks again, and claims a send only when it may go now**; the HTTP call is outside the lock; `_InFlight` hands its own answer (or error) to every waiter for the same URL. Keep it that way: holding the lock through a 2 s wait stalled every other request (LD02 review), and reserving future slots let a waiting request ignore a back-off that began meanwhile and pushed other games back (Sol review).
- **Ages:** "last play N s ago" from the play's `wallclock` (the server's summary) when known, else from the first sighting, and only if the app saw an earlier play appear (`len(seen) > 1`). `behind` fires past `BEHIND_S` = 55 s (35 s fired on ordinary snap gaps). The repeat key is (last play id, situation, scores): no clock.
- **What reaches the browser:** every body through `clean_all` (every string through `clean()`); team codes must match `^[A-Z]{2,4}$`, play ids `^[0-9]{1,24}$`, event ids `check_event_id`; unexpected ESPN values -> 502 `espn_unexpected`; `Sec-Fetch-Site` cross-site / same-site -> 403 `not_allowed`.
- **The 4th-down card shows no `wp_now`:** the wp model's smooth read of a 4th-down state averages the options (+0.06 points on 2025's 3,997) but can sit several points above every option late in close games (NYG 80.6 vs 73.3). Keep it in the API; don't draw it as "before the snap".
- **The team context's cache stamp** includes the model version: pass the *loaded* engine's version (`_context_version`), None while loading fails, or a mismatch rebuilds on every request (~5 s).
- **The replay feed** (`nfl app --live-replay`): ESPN's home team = the context's home (ESPN's sides throughout); a play is visible `lag_s` after its effective time (`effective_times`: a wallclock later than the next play's is clamped: one 2026 Official Timeout row is a day late); the next snap = the next non-admin play's `start` (its replay row gives the clock, scores and timeouts); a try or kickoff next -> `down = -1`; "End of Half" -> ESPN's stale 1st & 10. Parity: 99.7% of 9,510 snaps of the 65 saved 2026 games parse to the replay's own state just before they post.
- **Tests:** `tests/app/test_app_live.py` (a `World`: the replay feed over the TB at DAL fixture + a shifted IND at KC, a FakeClock shared by the feed and the client, stub engine, `FakeCache`; it imports `live_fakes` / `live_stubs` via `sys.path`), `tests/app/test_app_live_review.py` (the LD02 review's regressions, real threads, the JSON-vs-TS contract), `tests/app/test_app_live_sol.py` (the Sol review's: overflowing / one-team answers, the client's spacing and in-flight sharing on real threads and clocks, the replay's timeouts), `tests/live/test_live_context.py`, `tests/live/test_live_replayfeed.py`.

## 6. Tests

- `tests/live/test_live_{schema,data,models,decide,train_cli}.py` (unit, stubs in `tests/live/live_stubs.py`): schema types and edges, data frames on hand-made plays, models (monotone wp, legal_gain, leakage in `fit_all`, save / load), the engine's rules worked by hand (game over, halftime, kneel-out, miss spot, safety, the table, labels, the gate), CLI.
- `tests/live/test_live_backtest.py`: the LOSO harness and `ship_verdict` on synthetic frames.
- `tests/live/test_live_sol_fixes.py`: the Sol review's edge cases (playoff OT, halftime timeouts, whole-number floats, zero refits, the try after a return TD; D115).
- **Test basenames must be unique across `tests/`** (no `__init__.py`: two `test_backtest.py` files break collection). Prefix new files `test_live_`.
- `tests/live/test_bundle_integration.py` (`-m integration`): the hand-made calls, timing and meta on the promoted bundle.
- LD01: `test_live_espn.py` (the client on `httpx.MockTransport` + fake clocks: allowlist, redirects, the 2 s / 0.5 s clocks, back-off, stale answers), `test_live_state.py` and `test_live_replay.py` (the parser and replay on `tests/fixtures/live/`, trimmed real ESPN JSON; its README says where each came from), `test_live_parity.py`, `test_live_latency.py` (a scripted game), `test_live_feed_cli.py` (`nfl live call --event / games / replay / latency / parity` with a fake client and the stub engine). **No test may reach the network**: patch `nflengine.live.espn.EspnClient` or pass a `MockTransport`.

## 7. The production-unchanged check (how LD00 ran it)

1. `uv run pytest -q tests/test_production_untouched.py`.
2. `uv run nfl weekly rehearse --season 2026 --week 5` (pinned to the published run's moment, scratch copy, W&B off) and compare its prediction files with the published `runs/2026/week05/` files (join on keys; max abs diff must be 0). LD02 ran `--steps game,player --at 2026-10-07T00:28:30Z`: games 30 / players 4,542 / teams 120 rows identical (`baseline_role` 5e-18, float order). **`--out` takes a path relative to the current folder, not the data root:** `--out rehearsals/x` from the repo root writes ~170 MB into the repo (git-ignored parquet, untracked JSON); give an absolute path under the data root, or leave `--out` out (default `<data root>/rehearsals/<season>`), and delete a stray folder (it's your own output). See the `weekly-ops` skill §5c and the phase-workflow skill's "old code vs new code" notes if a shared input moved after publication.

## Improving this skill

Update it in the same commit as any change to `src/nflengine/live/` that changes how things work, and note it in the PROGRESS session log. Never weaken §1.
