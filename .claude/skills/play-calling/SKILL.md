---
name: play-calling
description: How the Play calling track's data is built, read and tested (PC00+, documentation/play-calling/). Use when touching src/nflengine/playcalling/ or tests/playcalling/, the play-call labels and definitions (neutral situation, dropback, PROE, deep shot, explosive, run direction, blitz, box, personnel, coverage), the situation buckets, the metric registry (`labels.METRICS`), nflverse participation (research only) and its two eras, the enriched plays and the tendency tables (`team_tendencies`, `team_game_tendencies`, `league_tendencies`, as-of weeks and the season / last4 / last_season windows), `nfl playcalling build`, the `playcall-build` W&B run, the weekly refresh (Tuesday + Wednesday FTN), or the `playcalling:` block of settings.yaml. Later phases add the pages (PC01), the forecast (PC02) and the play diagrams (PC03).
---

# Play calling (team tendencies)

Spec: `documentation/play-calling/README.md` (+ phase files PC00-PC03). Plain-language guide: `documentation/guides/play-calling.md`. Decisions: D107 (no-touch), D108, D110, D122 (PC00 as built).

## 1. The no-touch rule first (D107)

- New code lives in `src/nflengine/playcalling/`; it **never imports** from `nflengine.models` or `nflengine.features` and never writes to production folders, aliases or the weekly run. It may import small infrastructure (`paths`, `fsutil`, `ops.lock.write_parquet_atomic`, `ingest.base.SnapshotStore`, `tracking`) and read curated data.
- Shared files change only by adding: the `nfl playcalling` CLI group, the `playcalling:` settings block, `AppConfig.playcalling`, `DataPaths.playcalling`.
- Every PC phase closes with the production-unchanged check (`tests/test_production_untouched.py` + a week-5 rehearsal into the scratchpad on C:; the `live-decisions` skill §7 has the recipe; PC00: games 30 / players 4,542 / teams 120 identical, max diff 0).
- **The refresh is not a weekly-run step** (D122): a runbook line after Tuesday's run, and a Wednesday FTN refresh. Never add it to `weekly.py`.

## 2. Map of the package

| File | What it holds |
|---|---|
| `labels.py` | **The contract.** Definitions as constants (`NEUTRAL_WP` 0.20-0.80 inclusive, `NEUTRAL_MAX_DOWN` 3, `TWO_MINUTE_SECONDS` 120, `DEEP_SHOT_AIR_YARDS` 20, explosive 20 / 10, box ≤ 6 / ≥ 8, distance 1-3 / 4-6 / 7+, field zones, `BIG_LEAD` 9, `RECENT_GAMES` 4), the era vocabularies (`FORMATIONS_NGS/FTN`, `ROUTES_NGS/FTN`, `COVERAGES`, `PERSONNEL_GROUPS`, `DEF_PACKAGES`), `snake`, the play-label expressions (`scrimmage_filter`, `down_distance`, `field_zone`, `score_state`, `time_bucket`, `two_minute`, `neutral`, `run_direction`, `pass_zone`), `Situation` / `SITUATIONS` (core, down_distance, field_zone, score, time), `Metric` / `METRICS` (den + num expressions over the enriched columns, unit, source, situational, history_only, first / last season), `PFR_BLITZ`, `HEADLINE`, `catalogue()`, `definitions()` |
| `participation.py` | `load_participation(paths, seasons)` (newest file per season from `research/nflverse/participation`), `parse_participation(raw)` → one row per (game_id, play_id): formation, personnel ("11"), extra_ol, def_package, coverage, man_zone, target_route, pressure, time_to_throw, part_box, part_rushers (opus-high, PC00) |
| `build.py` | `load_inputs` (plays of S and S−1 per season file, FTN, participation, `pfr_pass`, games; optional sources fail soft into `Inputs.notes`), `enrich_plays`, `_ftn_labels` (drops FTN placeholder rows), `team_game_sums` (one wide group_by per side → long num / den), `team_index`, `as_of_weeks`, `_cumulative` + `tendencies` (windows, league value, pct), `league_table`, `coverage`, `build_season` (in memory), `write_season`, `run_build` (`nfl playcalling build`), `log_wandb`, `league_trend`, `league_by_week` |

Files on D: `playcalling/<S>/plays_enriched.parquet`, `team_game_tendencies.parquet`, `team_tendencies.parquet`, `league_tendencies.parquet`, `build.json` (written last; the old one is removed first). A `--through-week W` build goes to `playcalling/<S>/through_weekWW/` and never replaces the season's tables.

## 3. Commands

```bash
uv run nfl playcalling build --season 2026                     # weekly (Tuesday after the run; Wednesday after the FTN refresh)
uv run nfl playcalling build --season 2026 --history all       # + playcalling.history_seasons (2016-2025)
uv run nfl playcalling build --season 2025 --through-week 10   # a simulation folder
uv run nfl playcalling build --season 2026 --no-wandb | --smoke
# Wednesday after 13:00 ET, Monday night's FTN:
uv run nfl ingest --sources nflverse --datasets ftn_charting && uv run nfl curate
```

**In memory, no writes** (probes, reviews): `inp = B.load_inputs(paths, [2025]); e = B.enrich_plays(inp.plays, inp.ftn, inp.part); sb = B.build_season(inp, e, 2025)` → `sb.tend`, `sb.league`, `sb.tg`, `sb.meta`. Never call `run_build` / `write_season` / the CLI from a probe or a test that isn't on a tmp `DataPaths`.

## 4. Rules that bite

- **Polars scalar trap:** `pl.lit(True) & pl.lit(True)` is one scalar, and its `.sum()` inside a `group_by` is 1, not the group's row count (`dropback_rate` × `all` came out as n = games). `team_game_sums` ANDs every condition with a per-row `True`; keep it when adding a metric whose `den` is a literal.
- **Plays files differ in dtypes by season** (`goal_to_go` f64 vs i32): `load_inputs` reads each `plays/season=YYYY.parquet` separately and casts numerics to f64 (`INT_COLS` excepted) before a diagonal-relaxed concat. A multi-file `scan_parquet` raises a SchemaError.
- **FTN placeholder rows:** FTN keeps rows for plays it didn't chart (`qb_location` '0', box 0, every flag False; 2024_07_BAL_TB 97 of 131 plays; 2023 65, 2024 260, 2025 47, 2026 5 scrimmage plays). They're dropped, so the play is `ftn_charted` False; otherwise they count as "no play-action / no blitz". One 2024 row has `qb_location` ' S' (strip first).
- **FTN timing:** nflverse pulls FTN at least twice a day (~11:00 and ~17:00 UTC); FTN lands ~35-51 h after a game. Tuesday evening has every game but Monday night's; Wednesday afternoon completes the week. `build.json → coverage.games_without_ftn` lists what's missing. FTN metrics count charted plays only, so a missing game shrinks `n`, never biases a rate. FTN re-stamps the whole season every pull.
- **Availability (D123, Sol):** an as-of-W row is what a run on the Tuesday night before week W could see. `as_of_cutoffs` = end of the Tuesday (US Eastern) after the last game before W, capped at week W's first kickoff; `held_back` takes out FTN rows of a game until kickoff + `FTN_LAG_HOURS` (48) and the PFR metric for `PFR_LAG_WEEKS` (1); `tendencies(held=)` subtracts them from `season` and `last4` (`games` unchanged). Monday night's FTN counts from the next week, so Tuesday and Wednesday builds give the same as-of tables; `build.json → availability` names the held games. `games` needs `kickoff_utc`: without it (or with a null kickoff) the rule **fails closed** and holds the previous week's FTN for a week. Brute-force check (opus-high, after the fix): 5,376 windows, 0 mismatches; capped cutoffs only in 2020 W6 / W13 / W14 and 2021 W16 (Tuesday / Wednesday games).
- **Required columns:** `load_inputs` refuses a plays file missing any of `REQUIRED_COLUMNS` (a missing `pass` once read every play as a run); other missing columns become null with a note.
- **As of a week:** `as_of_week` W sums games with `week < W` (season, last4) or every game of S−1 (`last_season`). As-of weeks stop at the first week not complete in `games` **and** in the curated plays. Brute-force check (opus-high, PC00): 1,080 windows recomputed from plays, 0 mismatches.
- **`last_season` keeps only metrics that exist in season S** (`Metric.exists_in`): no 2025 participation on 2026 rows, no NGS-era formation on 2023 rows.
- **Participation is research only** (`history_only` rows). Never a feature for a forecast (PC02 must filter `~history_only`); pages label it "History". Two vocabularies (NGS ≤ 2022, FTN ≥ 2023); `formation_shotgun` changes meaning (NGS splits EMPTY out). Coverage 0% in 2016-17. FTN-era `was_pressure` is False on runs too (filter to dropbacks).
- **PROE isn't 0 for the league:** `xpass` (nflverse, fit 2006-2019) isn't re-centred; league neutral PROE −0.2 to −1.9 points in 2016-2021, −1.2 to −2.6 since (2025 −2.0, rbsdm −1.7). Compare teams with `diff`. Our neutral rule (downs 1-3, not the last 2 min) is narrower than rbsdm's PROE tab (all downs, all clock, penalty plays in): Spearman 0.975 on 2025's 32 teams, same top 5, 4 of the bottom 5.
- **Definitions from the data:** dropback = nflverse `pass == 1` (`pass_oe` = 100 × (pass − xpass) exactly); pbp `shotgun` includes pistol; the gamebook's `pass_length` "deep" starts ~16 air yards (the pass zones use it; deep shots use air ≥ 20); `run_gap` is null on middle runs; PFR blitz = `pfr_pass.times_blitzed` per opponent dropback (team = offense, opponent = defense), r 0.72 per team-game / 0.86 per team-season vs FTN.
- **Defense rows** are what offenses do against the team; their situations are from the offense's side (a defense row's `lead_9plus` means the opponent led). Playoff games count in every window.
- **Percentiles** rank every team with `n > 0`, however small: read `n`.
- **Sizes:** 2025 `team_tendencies` ~944k rows (~95 MB in memory); a history build keeps only slimmed builds for W&B. The data drive is slow for small writes; the four files per season are fine.

## 5. Tests

`tests/playcalling/` (no `conftest.py` / `__init__.py`; basenames `test_playcall_*`): `test_playcall_participation.py` (both eras' strings, blanks, special teams, the fake research tree), `test_playcall_labels.py` (bucket edges, neutral, the registry), `test_playcall_build.py` (a synthetic league written as a fake curated tree: a pure-Python reference recount of every team-game, hand-counted windows around a bye, the as-of future-invariance check for W = 1..6, league / pct, FTN-missing games, PFR with a decoy, `as_of_weeks`, writes on a tmp root, fail-soft inputs), `test_playcall_real.py` (`@pytest.mark.integration`, in-memory on the real 2025 data: play-action / blitz known values, FTN ≥ 0.99, 17 games per team, rbsdm top / bottom 5, league PROE in (−0.04, 0), the through-week-10 truncation check, `last_season` = all of 2024), `test_playcall_review.py` (opus-high's review regressions), `test_playcall_cli.py` (run_build stubbed). A mutation run of 18 planted bugs on a scratch copy of `src` was caught 18 / 18 (PC00). Not covered by design: `log_wandb` (smoke-test it with `--smoke`).

## Improving this skill

Add a rule here when a session learns something reusable about the play-calling data or build (a new quirk, a definition change, a test pattern), in the same commit, and note it in the PROGRESS session log. Never weaken the no-touch rule or the participation research-only rule.
