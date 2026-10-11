# PC00: Tendency data

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Play calling rows)

- **Depends on:** P10 (curated `plays`, `ftn_plays`; research participation on D:)
- **Unlocks:** PC01 (the pages read these tables), PC02 (the forecast's inputs and targets)
- **Read first:**
  - [Play calling README](README.md) §2 (what the data can and can't show) and §4;
  - the `curated-data` skill (FTN join: `nflverse_game_id` / `nflverse_play_id` = `game_id` / `play_id`, cast `play_id`; FTN about a week behind on Tuesdays; participation research only);
  - [04 Track 1 models](../04-track1-models.md) → leakage rules (as-of tables).

## Goal

One reproducible build that turns play-by-play, FTN charting and (for history) participation into:
- **enriched plays**: one row per play with every play-call label the pages and the model need;
- **tendency tables**: rates per team, side, as-of week and situation, with the league's rate next to each.

Everything is as of a week, so the same tables serve the pages, the forecast's features and its walk-forward backtest.

## Scope

- **In:**
  - `playcalling/build.py`;
  - the labels and situation buckets;
  - neutral-situation rules;
  - league baselines;
  - the CLI;
  - the as-of rule and its test;
  - the refresh decision (✋);
  - the guide, skill and W&B section.
- **Out:** UI (PC01), forecasting (PC02), diagrams (PC03).

## Tasks

### Probe first
- [x] 🤖 **Coverage by season and week:**
  - FTN 2022–2026: which 2026 weeks, and the share of plays joined;
  - participation 2016–2025: formation, personnel, coverage, man / zone, route and pressure fill rates by season (2026-10-08: coverage 0% 2016–2017, about 38% 2018–2022, about 49% 2023–2025; routes about 37% then 100% of rows from 2023, but only the target's route);
  - which pbp fields are blank on which play types.
- [x] 🤖 **When FTN lands:** check on a Tuesday after the weekly run whether last week's FTN is complete, and on Wednesday / Thursday. Record it, since it decides when the forecast can run.
- [x] 🤖 **Definitions to settle** (and write in the guide):
  - **neutral situation:** win probability 20–80%, not the last 2 minutes of a half, downs 1–3 (rbsdm's convention);
  - **deep shot:** air yards ≥ 20;
  - **screen:** FTN `is_screen_pass`;
  - **play-action:** FTN;
  - **blitz:** FTN `n_blitzers > 0`. PFR's blitzes disagree (r 0.69 per team-game): FTN is the source here, PFR is shown as a cross-check;
  - **run direction:** `run_location` × `run_gap`.

### Build (`src/nflengine/playcalling/`)
- [x] 🤖 **`plays_enriched`** per season: `game_id`, `play_id`, season, week, offense, defense, down, distance, yards to goal, field zone, score state, quarter, time bucket, neutral flag, `xpass`, `pass_oe`, the play type and its labels:
  - pass: dropback, scramble, short / deep, direction, air yards, completion, EPA, success;
  - run: direction, gap, EPA, success;
  - FTN: play-action, screen, RPO, motion, QB alignment, backfield, box, blitzers, rushers, hash, out of pocket;
  - for 2016–2025 also participation: formation, personnel group (parsed to "11", "12" …), defense personnel, coverage shell, man / zone, target's route, pressure.
- [x] 🤖 **`team_tendencies`:** for each team, side (offense / defense; defense = what offenses do against it), **as-of week** (games strictly before it), season to date, last 4 games and last season. For each metric × situation bucket: n, rate, league rate, difference, percentile among teams.
  - **Metrics (offense):** pass rate over expected (neutral), dropback rate by down and distance, shotgun / under center / pistol share, no-huddle, motion, play-action, screen, RPO, deep-shot share, aDOT, pass direction shares, run direction and gap shares, explosive-play rate.
  - **Metrics (defense):** blitz rate, average rushers, light / heavy box rate, opponent pass rate over expected allowed, opponent play-action / screen / deep-shot rates, explosive plays allowed.
  - **History-only metrics** (2016–2025, labelled): personnel shares, formation shares, coverage-shell shares, man / zone share, target-route shares.
- [x] 🤖 **League baselines** per season, as of each week, by situation bucket.
- [x] 🤖 **CLI:** `nfl playcalling build --season S [--through-week W] [--history 2016-2025]`. It writes `{NFL_DATA_ROOT}/playcalling/<S>/plays_enriched.parquet` and `team_tendencies.parquet` atomically (`fsutil.write_parquet_atomic`), plus a small `build.json` (as-of week, FTN weeks present, row counts). Logs a `playcall-build` W&B run (counts, FTN coverage, a league-trend chart).
- [x] 🤖 **Tests:**
  - **as-of:** a week-W row never counts a week-W play;
  - the FTN join rate;
  - the bucket edges;
  - neutral-situation filtering;
  - **a known value:** 2025 league play-action ≈ 23% of dropbacks and blitz ≈ 29% (the curated-data skill's numbers).

### Decide (✋)
- [x] ✋ **How the tables refresh each week** (README §4). Options:
  - (a) a runbook line after the Tuesday run: `uv run nfl playcalling build --season 2026`;
  - (b) a **Refresh play calling** button in the app: a fourth allowlisted runner kind, built in code like the other three, with its own lock check;
  - (c) a fail-soft step appended to the weekly run (not recommended: it changes the weekly run, D107).

  Also decide whether a Wednesday / Thursday rebuild is worth it to pick up FTN's late games. Log the decision.

  **Decided (Rishi, 2026-10-10): (a), a runbook line**, plus a Wednesday rebuild after 13:00 ET with an FTN-only refresh, because the probe showed Tuesday's FTN misses Monday night's game (D122).

### Docs and close
- [x] 🤖 Guide `documentation/guides/play-calling.md`:
  - what play-calling tendencies are (PROE, play-action, screens, blitz, coverage shells: a primer);
  - which data covers what and when (README §2's table);
  - the definitions;
  - the tables and the command;
  - example numbers from 2025.
- [x] 🤖 Skill `.claude/skills/play-calling/SKILL.md`; CLAUDE.md's skills table; the W&B guide (`playcall-build`).
- [x] 🤖 Production-unchanged check (the D107 test from LD00, or create it here if PC00 runs first).
- [x] ✋ **Close PC00.** Waived by Rishi's kickoff ("marking the phase complete"), after the `/sol-qa` review.

## As built: deviations from the task list (2026-10-10, D122)

- **Probes** (guide §2, README §2 "Checked again in PC00"): FTN joins 99.3-99.9% of 2022-2025 scrimmage plays once FTN's blank placeholder rows are treated as not charted (2024 had 260; 2024_07_BAL_TB alone 97 of 131 plays); 2026 weeks 1-3 complete, 15 of week 4's 16 games on Tuesday. Participation joins 100% (2016-2025); on dropbacks coverage 0% / ~89% / ~99.8% (2016-17 / 2018-22 / 2023-25), routes 85-90%, two vocabularies (NGS to 2022, FTN from 2023). Blanks by play type: pass depth / direction / air yards on every attempt, never on sacks or scrambles; `run_gap` empty on middle runs (27% of runs); `xpass` and `wp` never empty on runs and passes.
- **When FTN lands** was answered from the three raw snapshots and nflverse's live file instead of waiting for a Tuesday: nflverse pulls FTN at least twice a day (~11:00 and ~17:00 UTC); Sunday's games were in by Tuesday 13:00 ET (40-51 h after kickoff), Thursday's TB at DAL in 35 h, week 4's Monday game by Thursday at the latest (Wednesday expected; to confirm 2026-10-14). Hence the Wednesday rebuild.
- **Availability in the as-of rows (Sol's review, D123):** an as-of-W row is what a run on the Tuesday night before week W could see: FTN counts a game 48 h after kickoff (Monday night's from the next week), PFR a week behind, play-by-play everything; `build.json → availability`. A plays file missing a call column (`pass`, `play_type` ...) is refused.
- **Definitions** as the task list, with these settled from the data: dropback = nflverse `pass == 1` (sacks and scrambles in; `pass_oe` = 100 x (pass - xpass) exactly); the pass zones use the gamebook's short / deep (deep ~16+ air yards), deep shots air >= 20; explosive = dropback 20+ / designed run 10+; box from FTN (0 = not charted); `blitz_rate_pfr` from `pfr_pass.times_blitzed` per opponent dropback (summing defenders' `def_times_blitzed` would count a two-blitzer play twice).
- **Two extra files:** `team_game_tendencies.parquet` (per team-game rates: the week-by-week view, PC02's actuals) and `league_tendencies.parquet` (the league baselines task, as its own file). `build.json` is written last and removed first.
- **Windows** `season`, `last4`, `last_season`; `last_season` carries only metrics that exist in the season built (no 2025 participation on 2026 rows). History metrics come for situation `all` only; six key metrics come by down & distance, field zone, score and two-minute.
- **`--through-week W`** writes to `playcalling/<S>/through_weekWW/`, never over the season's tables (opus-high's review).
- **Exit criterion "league PROE near 0 by construction" is false for recent seasons:** nflverse's `xpass` is fixed (fit 2006-2019); league neutral PROE is -0.2 to -1.9 in 2016-2021 and -1.2 to -2.6 since (2025 -2.0; rbsdm's own league mean -1.7). The tables carry `diff` (value - league), the centred number. The rbsdm half of the criterion passes: on 2025's regular season our neutral PROE ranks the 32 teams with Spearman 0.975 against rbsdm (Pearson 0.988), the same top 5 (ARI, KC, LA, CIN, NE) and 4 of the bottom 5 (NYJ, BAL, SEA, DET; rbsdm ATL, ours GB). rbsdm's PROE tab uses a wider filter (every down and second, penalty plays in); with it on our plays the Spearman is 0.988.
- **Known values:** 2025 regular season play-action 23.2% of dropbacks, blitz 29.4% (the whole season incl. playoffs: 23.0%, 29.5%), matching the curated-data skill and FTN's public file.
- **Workers:** opus-high built `participation.py` (49 tests), then reviewed the lead's build read-only (no leakage: 1,080 windows recomputed by brute force, 0 mismatches; 11 findings, 9 fixed, 2 kept and documented) and wrote their regression tests (13); sonnet-xhigh checked public numbers (rbsdm's API, FTN's file, PFF, Sportradar) and wrote the label / build / CLI / real-data tests and the Sol regressions (mutation checks: 18 / 18 and 15 / 16 planted bugs caught; the survivor is an equivalent mutant). **Sol** (D123): 1 high (source availability → the as-of rows are the Tuesday view), 1 medium (required columns), 2 low (guide), all fixed; opus-high re-verified the availability rule by brute force (5,376 windows, 0 mismatches) and its fail-open edge was closed. Final build: 2016-2026 in 22 s, W&B `qpll840n`.

## Exit criteria (+ how to verify)

- The tables exist for 2016–2026 and the as-of test passes.
- 2025 spot checks match public numbers: league PROE near 0 by construction, and the top and bottom PROE teams in line with rbsdm's 2025 page.
- Tests, ruff and the production-unchanged check pass.

## Pitfalls / notes

- **FTN revises past weeks** (the 2025 file was republished on 2026-09-23). Rebuild from the newest snapshot. The as-of rule protects the forecast's backtest, but a page can change slightly after a re-pull.
- **Participation is research only:** never a feature for a 2026 forecast. History pages say "2023–2025" plainly.
- **Coverage is filled mostly on pass plays.** Rates must use pass plays as the denominator, or they're halved.
- **Team codes:** canonical (LV, LAC, LA) everywhere. FTN and participation use nflverse ids, so join on ids, never on parsed teams.
