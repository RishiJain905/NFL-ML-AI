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
- [ ] 🤖 **Coverage by season and week:**
  - FTN 2022–2026: which 2026 weeks, and the share of plays joined;
  - participation 2016–2025: formation, personnel, coverage, man / zone, route and pressure fill rates by season (2026-10-08: coverage 0% 2016–2017, about 38% 2018–2022, about 49% 2023–2025; routes about 37% then 100% of rows from 2023, but only the target's route);
  - which pbp fields are blank on which play types.
- [ ] 🤖 **When FTN lands:** check on a Tuesday after the weekly run whether last week's FTN is complete, and on Wednesday / Thursday. Record it, since it decides when the forecast can run.
- [ ] 🤖 **Definitions to settle** (and write in the guide):
  - **neutral situation:** win probability 20–80%, not the last 2 minutes of a half, downs 1–3 (rbsdm's convention);
  - **deep shot:** air yards ≥ 20;
  - **screen:** FTN `is_screen_pass`;
  - **play-action:** FTN;
  - **blitz:** FTN `n_blitzers > 0`. PFR's blitzes disagree (r 0.69 per team-game): FTN is the source here, PFR is shown as a cross-check;
  - **run direction:** `run_location` × `run_gap`.

### Build (`src/nflengine/playcalling/`)
- [ ] 🤖 **`plays_enriched`** per season: `game_id`, `play_id`, season, week, offense, defense, down, distance, yards to goal, field zone, score state, quarter, time bucket, neutral flag, `xpass`, `pass_oe`, the play type and its labels:
  - pass: dropback, scramble, short / deep, direction, air yards, completion, EPA, success;
  - run: direction, gap, EPA, success;
  - FTN: play-action, screen, RPO, motion, QB alignment, backfield, box, blitzers, rushers, hash, out of pocket;
  - for 2016–2025 also participation: formation, personnel group (parsed to "11", "12" …), defense personnel, coverage shell, man / zone, target's route, pressure.
- [ ] 🤖 **`team_tendencies`:** for each team, side (offense / defense; defense = what offenses do against it), **as-of week** (games strictly before it), season to date, last 4 games and last season. For each metric × situation bucket: n, rate, league rate, difference, percentile among teams.
  - **Metrics (offense):** pass rate over expected (neutral), dropback rate by down and distance, shotgun / under center / pistol share, no-huddle, motion, play-action, screen, RPO, deep-shot share, aDOT, pass direction shares, run direction and gap shares, explosive-play rate.
  - **Metrics (defense):** blitz rate, average rushers, light / heavy box rate, opponent pass rate over expected allowed, opponent play-action / screen / deep-shot rates, explosive plays allowed.
  - **History-only metrics** (2016–2025, labelled): personnel shares, formation shares, coverage-shell shares, man / zone share, target-route shares.
- [ ] 🤖 **League baselines** per season, as of each week, by situation bucket.
- [ ] 🤖 **CLI:** `nfl playcalling build --season S [--through-week W] [--history 2016-2025]`. It writes `{NFL_DATA_ROOT}/playcalling/<S>/plays_enriched.parquet` and `team_tendencies.parquet` atomically (`fsutil.write_parquet_atomic`), plus a small `build.json` (as-of week, FTN weeks present, row counts). Logs a `playcall-build` W&B run (counts, FTN coverage, a league-trend chart).
- [ ] 🤖 **Tests:**
  - **as-of:** a week-W row never counts a week-W play;
  - the FTN join rate;
  - the bucket edges;
  - neutral-situation filtering;
  - **a known value:** 2025 league play-action ≈ 23% of dropbacks and blitz ≈ 29% (the curated-data skill's numbers).

### Decide (✋)
- [ ] ✋ **How the tables refresh each week** (README §4). Options:
  - (a) a runbook line after the Tuesday run: `uv run nfl playcalling build --season 2026`;
  - (b) a **Refresh play calling** button in the app: a fourth allowlisted runner kind, built in code like the other three, with its own lock check;
  - (c) a fail-soft step appended to the weekly run (not recommended: it changes the weekly run, D107).

  Also decide whether a Wednesday / Thursday rebuild is worth it to pick up FTN's late games. Log the decision.

### Docs and close
- [ ] 🤖 Guide `documentation/guides/play-calling.md`:
  - what play-calling tendencies are (PROE, play-action, screens, blitz, coverage shells: a primer);
  - which data covers what and when (README §2's table);
  - the definitions;
  - the tables and the command;
  - example numbers from 2025.
- [ ] 🤖 Skill `.claude/skills/play-calling/SKILL.md`; CLAUDE.md's skills table; the W&B guide (`playcall-build`).
- [ ] 🤖 Production-unchanged check (the D107 test from LD00, or create it here if PC00 runs first).
- [ ] ✋ **Close PC00.**

## Exit criteria (+ how to verify)

- The tables exist for 2016–2026 and the as-of test passes.
- 2025 spot checks match public numbers: league PROE near 0 by construction, and the top and bottom PROE teams in line with rbsdm's 2025 page.
- Tests, ruff and the production-unchanged check pass.

## Pitfalls / notes

- **FTN revises past weeks** (the 2025 file was republished on 2026-09-23). Rebuild from the newest snapshot. The as-of rule protects the forecast's backtest, but a page can change slightly after a re-pull.
- **Participation is research only:** never a feature for a 2026 forecast. History pages say "2023–2025" plainly.
- **Coverage is filled mostly on pass plays.** Rates must use pass plays as the denominator, or they're halved.
- **Team codes:** canonical (LV, LAC, LA) everywhere. FTN and participation use nflverse ids, so join on ids, never on parsed teams.
