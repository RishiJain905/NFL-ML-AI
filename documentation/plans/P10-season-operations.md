# P10: Season Operations and Offseason

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P07 (ongoing through the season; P08 work may overlap)
- **Unlocks:** the 2027 season
- **Read first:** `documentation/runbook.md` (from P07), [08 → Drift signals](../08-experiment-tracking.md#drift-signals-and-responses), [04 → Training and retraining cadence](../04-track1-models.md#training-and-retraining-cadence), [10 Decisions log](../10-decisions-log.md)

## Goal

Keep the system healthy through the rest of the 2026 season and the playoffs, review the season honestly, and get ready for 2027 (re-tuning, data refresh, Big Data Bowl 2027 check).

> **How P10 was closed (D89).** Most tasks below wait for the calendar. Rishi chose at the kickoff (2026-10-05, week 5): **build and rehearse now, then close**. Every tool and procedure the dated tasks need was built and proven on the 2025 playoffs and a 2026 week-1 dry run; the dated work itself (⏭ below) moved to **PROGRESS → Season calendar**, worked through by the weekly sessions. Tags: ✅ done in P10 · ⏭ dated, on the season calendar (tool ready).

## Tasks

### In season (recurring)
- [x] 🤖 Weekly: confirm the run landed (automatic), review any drift alerts, and add one line to the PROGRESS session log per week (`week NN: published ✓ / issues`). ✅ Tool: `nfl season weeks --season 2026` prints the line; PROGRESS has a **Season log** (week 4 entered). ⏭ One line per Tuesday through the Super Bowl.
- [x] ✋ When a drift alert fires: the agent investigates (data freshness, feature distributions, role changes) and proposes a fix. Rishi decides. ✅ Procedure: runbook → Drift alerts + "After each Tuesday run"; ⏭ applied when an alert fires (none yet: the first possible game alert is the week-10 run).
- [x] 🤖 Handle data-source breakages (an unofficial endpoint changes) using the runbook. ✅ Runbook → "An unofficial endpoint changed (P10)": signs, the adapter files, re-running one source, fixtures, switching a source off in `settings.yaml → sources`.

### Playoffs
- [x] 🤖 Check the playoff handling (`game_type != 'REG'`, neutral-site Super Bowl, bracket-sized slates, no byes logic) before Wild Card weekend. ✅ Rehearsed the 2025 playoffs (weeks 19–22) through the live steps (`nfl weekly rehearse`, D90); found and fixed: the player model made **no** playoff projections (0 rows, step degraded), playoff projections couldn't be graded, a check false positive (D91). ⏭ Re-rehearse by Tuesday 2027-01-12.
- [x] 🤖 Digest variants for playoff weeks (fewer games; trend and under-the-hood sections still apply). ✅ The round's name in the title, trends and under-the-hood only for the teams still playing, under-the-hood's "last week" = the previous playoff round (D91).

### End-of-season review
- [x] 🤖 A season report (`documentation/reviews/2026-season-review.md`) built from the W&B scorecards:
  - game model vs Elo vs market
  - calibration
  - the accuracy scoreboard per target
  - watch-list hit rate
  - check failure rate
  - pipeline uptime
  - best and worst calls

  ✅ Generator: `nfl season review --season 2026 --out documentation/reviews/2026-season-review.md` (`ops/season_review.py`), proven on the 2025 simulation records and on live 2026 so far. ⏭ The real 2026 review after the Super Bowl (February 2027).
- [ ] 🧑 **Rishi reviews** the season report. Decide what to keep, cut or rebuild for 2027. ⏭ February 2027 (the review ends with an empty keep / cut / rebuild section for it).
- [ ] 🤖 Update the decisions log with the lessons learned. ⏭ After Rishi's review. (P10's own decisions: D89–D91.)

### Offseason prep for 2027
- [ ] 🤖 Full data refresh, including **participation data for 2026** (now filled in; still research-only for live features unless the decisions change). ⏭ Spring 2027. ✅ Procedure checked: with `seasons.current` still 2026, a plain `nfl ingest` re-pulls the season and picks up participation once published (runbook → Pre-season checklist, step 1).
- [ ] 🧑 **Rishi runs** the pre-season re-tune (ratings half-life, prior factor, sample weights, model hyperparameters) with walk-forward evaluation that now includes 2026. ⏭ Summer 2027; the exact commands are in the runbook's Pre-season checklist, step 4.
- [x] 🤖 Roster churn: make sure offseason trades, free agency, the draft and coaching changes flow into the graph and the preseason priors (QB-change adjustments). ✅ Checked on the 2026 offseason (opus-high, read-only): findings and limits in the [season operations guide](../guides/season-operations.md#roster-churn).
- [x] 🤖 Check whether **Big Data Bowl 2027** has been announced. If it's relevant, plan a Track 2 extension. ✅ Checked 2026-10-05: see the [season operations guide](../guides/season-operations.md#big-data-bowl-2027). ⏭ Check again with the pre-season checklist.
- [x] 🤖 Dry run of the full pipeline on 2026 Week 1 data, as if live, before 2027 Week 1. ✅ `nfl weekly rehearse --season 2026 --week 1` (rehearsal, D90). ⏭ Repeat in August 2027 (pre-season checklist, step 5).
- [ ] ✋ **Checkpoint:** ready for 2027. ⏭ August 2027.

## Exit criteria

| Criterion | Verify | Status (2026-10-05) |
|---|---|---|
| Every 2026 week (regular season + playoffs) published | PROGRESS weekly lines + reports on D: | ⏭ Season log; `nfl season weeks --season 2026` |
| Season review written and discussed | Review doc + PROGRESS note | ⏭ February 2027; the generator is proven |
| 2027-ready | The re-tune is recorded; the dry run passes | ⏭ August 2027; the dry-run tool passes on 2026 week 1 |

## As built in P10 (2026-10-05)

- **Rehearsals** (`ops/rehearsal.py`, `clock.py`, `paths.redirect_data_root`; `nfl weekly rehearse`; D90): the live steps on any week with the clock pinned to its Tuesday, outputs under `D:\nfl-ml-data\rehearsals\<season>\`, nothing live touched.
- **Playoffs** (D91): player rows for playoff weeks (`with_playoff_week`), playoff grading (`load_inputs(playoffs=True)`), the round name (`ops.calendar.round_name`, `Meta.playoff_round`), trends / under-the-hood on the teams still playing, the surname false positive in `check_unknown_entities`.
- **Season records** (`ops/season_review.py`, sonnet-xhigh; `nfl season weeks`, `nfl season review`).
- **Found by the rehearsals and checks, fixed:** the weekly `game` step now refreshes `features/game_features.parquet` (`game_runs.save_feature_frame`): the player and team models read it for every game's market points / spread / total, and only `nfl features game` wrote it, so tomorrow's week-5 projections would have had none; head-coach corrections (`config/head_coach_fixes.csv`, D92: 2026 ATL / ARI / BUF / LV, 2025 TEN / NYG interims); a week-18 "rested starters" role rule for playoff picks (D91).
- **A one-game slate** (the Super Bowl) no longer ranks one game as both "most lopsided" and "closest": each highlight list uses at most half the slate (found by the GLM test on the rehearsed Super Bowl digest; D91).
- **Sol review** (D93): 6 findings, all fixed (rehearsal folder guard, the end-of-season steps, two season-review fixes, playoff-aware source freshness, the pre-2021 regular-season length).
- **Docs:** [season operations guide](../guides/season-operations.md) (new), runbook (rehearse, playoffs, endpoint breakage, end of season, pre-season checklist, the weekly PROGRESS line), D89–D91, PROGRESS → Season calendar + Season log, skills (`weekly-ops`, `digest-checks`, `curated-data`, `model-experiment`, `phase-workflow`).

### Deviations from the task list
- The phase closed with its dated tasks on a calendar instead of at the end of the 2026–27 offseason (D89, Rishi's choice).
- The dry run is a **rehearsal** (the live steps in a scratch copy with a pinned clock), not a live run of week 1: a live run of a past week would overwrite the published files and the live graph. `ingest`, `curate`, `ratings` and `graph` aren't rehearsed (they rewrite shared files).
