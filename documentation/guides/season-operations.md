# Season operations: the season calendar, rehearsals, playoffs and the offseason (P10)

How the project gets through a whole season and into the next one: what happens each Tuesday, how the playoffs differ, how a week can be **rehearsed** without touching anything live, how the season is reviewed after the Super Bowl, and what to do before the next season starts. The week-to-week mechanics (the calendar, `--auto`, the lock, run records, alerts, the dashboard) are the [weekly operations guide](weekly-operations.md); the commands are in the [runbook](../runbook.md).

## 1. The idea in two minutes

The weekly run (P07) handles one week. A season needs a little more:

| When | What | Tool (P10) |
|---|---|---|
| Every Tuesday, weeks 5–22 | Run `nfl weekly run --auto`, look at any drift alert, add the week's line to PROGRESS → Season log | `nfl season weeks` |
| Before Wild Card weekend | Re-check that the playoff path still works | `nfl weekly rehearse --season 2025 --weeks 19-22` |
| Wild Card → Super Bowl | Four playoff weeks: fewer games, the round's name, regular-season form for the players | the weekly run itself (D91) |
| After the Super Bowl | The season review, Rishi's keep / cut / rebuild | `nfl season review` |
| Offseason | Last season's final data and participation, roster churn, Big Data Bowl check | `nfl ingest`, the coaching seed |
| Pre-season | Season switch, re-tune, dry run, ✋ ready | runbook → Pre-season checklist, `nfl weekly rehearse --week 1` |

P10 was built in week 5 of the first live season (2026). Rishi chose to **build and rehearse everything now and put the dated steps on a calendar** (D89), so this guide describes tools that were proven on past weeks and a calendar that later sessions work through.

## 2. The 2026–27 season calendar

The working copy with checkboxes is PROGRESS → Season calendar. Dates from the 2026 schedule (week 18 is Sunday 2027-01-10).

| Date | Step | Who |
|---|---|---|
| Tue 2026-10-06 → Tue 2027-01-05 | Weekly runs for weeks 5–18 (+ the Saturday injury update); one Season-log line each | agent or Rishi (manual-first, D71) |
| by Tue 2027-01-12 | Playoff check: `nfl weekly rehearse --season 2025 --weeks 19-22 --fresh`, every week `ok` | agent |
| Tue 2027-01-12, 01-19, 01-26, 02-02 (expected) | Live runs: Wild Card, Divisional, Conference championships, Super Bowl (expected Sun 2027-02-14). The playoff games aren't in nflverse's schedule until each round is set; `--auto` waits (exit 3) until they are | agent or Rishi |
| After the Super Bowl | `--auto` says `idle` (offseason); the review grades the Super Bowl from the saved predictions | — |
| February 2027 (a week after the Super Bowl) | Finish the season by hand: `nfl ingest` + `nfl curate` (the Super Bowl's result), `nfl scoreboard --week 21` / `--week 22` (no weekly run follows the Super Bowl, so nothing grades it otherwise), then `nfl season review --season 2026 --out documentation/reviews/2026-season-review.md`; 🧑 Rishi's review; lessons into the decisions log | agent, then Rishi |
| Spring 2027 | Data refresh with 2026 participation (runbook → Pre-season checklist, step 1) | agent |
| Summer 2027 | Season switch, coaching seed, 🧑 re-tune, dry run, Big Data Bowl check, ✋ ready for 2027 | Rishi + agent |

## 3. Each Tuesday: the season log and drift alerts

After the run, `uv run nfl season weeks --season 2026` prints one line per week from the run records (`pipeline_history.parquet` and each week's `run_summary.json`):

```
week 04: published ✓ (digest found, no run record), checks passed after one regeneration
```

(week 4 ran before P07's run records existed.) A normal line from a recorded run reads like this one from the 2025 simulations:

```
week 10: published ✓ on time (58.2 h before kickoff), checks passed first time, 1 drift alert (game probabilities poorly calibrated [ECE 0.053])
```

That line goes into PROGRESS → Season log. A **drift alert** is never acted on automatically (doc 08): the agent looks at data freshness (`nfl data-status`), the feature distributions behind the signal and role changes, writes what it found under the week's line, and proposes a fix; Rishi decides (✋). What each alert means: [runbook → Drift alerts](../runbook.md#drift-alerts). The first possible game-model alert is the week-10 run (it needs 6 graded weeks).

## 4. Rehearsals: the real Tuesday, on any week, in a sandbox

### What it is

A **rehearsal** runs the live steps `ready → game → player → digest` (the same functions as `nfl weekly run`) on a past or current week, as if it were 10:00 ET on the Tuesday before that week, and writes everything into a scratch folder:

```powershell
uv run nfl weekly rehearse --season 2025 --weeks 19-22 --fresh   # last season's playoffs, round by round
uv run nfl weekly rehearse --season 2026 --week 1 --fresh       # the pre-season dry run
```

Two things make it work (`src/nflengine/ops/rehearsal.py`, D90):

- **A pinned clock** (`src/nflengine/clock.py`). The steps ask "which games have kicked off?" to decide what to predict (a started game keeps its saved prediction). During a rehearsal `clock.utc_now()` answers with the pinned Tuesday, so a January 2026 game still counts as upcoming in October 2026.
- **A redirect of the data root** (`paths.redirect_data_root` + `RehearsalPaths`). Raw and curated data, research data and caches are read from `D:\nfl-ml-data` as usual; features, models, runs and reports are written under `D:\nfl-ml-data\rehearsals\<season>\`. The first rehearsal into a folder copies in what the steps read from the written side: the feature tables (34 MB) and the canonical backtests the weekly refits continue from (147 MB).

Nothing shared is touched: W&B is off, the player step doesn't write projections into Neo4j, the digest has no graph sections, there is no lock, no run records and no alias moves. The digest uses the template writer unless `--llm openrouter` asks for GLM.

### What it produces

```
D:\nfl-ml-data\rehearsals\2025\
  REHEARSAL.md                       the marker (--fresh only wipes a folder that has it)
  features\  runs\backtests\         the seeded copies
  runs\2025\week19\                  the week's files, as live: predictions_games / _players / _teams,
                                     watchlist.parquet, payload.json, checks.json, digest.md,
                                     weekly_run.json, and rehearsal.json (what each step did)
  runs\2025\accuracy_scoreboard.parquet, season_scorecard.parquet   graded like live weeks
  reports\2025\week19-digest.md      the digest
  models\game-model\2025-w19\ ...    the week's fitted models
```

Weeks rehearsed into the same folder chain like live ones: week 20's report card grades week 19's rehearsed picks, and the scoreboard grows week by week.

### What it proves and what it doesn't

It proves the **code path**: every step runs, the files look right, the digest passes its checks. It doesn't measure **accuracy**: a rehearsal reads today's data, so a past week sees its closing lines and its final injury report, which the real Tuesday didn't have. `ingest`, `curate`, `ratings` and `graph` are not rehearsed because they rewrite shared files (the ratings build also rebuilds the DuckDB views in `curated/nfl.duckdb`); a rehearsal uses their current output.

### The proof that it's faithful

A rehearsal of 2026 week 4 with the clock pinned to the published run's save time (2026-10-04 08:00:33 UTC), compared with the published files: the **game predictions were identical** (the 15 games not yet started, both variants: 30 rows, max difference 0, the same feature hashes). The player projections differed by up to 6 passing yards for 13 of 30 QBs, but not because of P10: P08's `nfl features game` rewrote `features/game_features.parquet` (which the player model reads) 17 hours after that run, so the published file can't be reproduced by any code. On the same inputs, the player step of the P10 code and of the code before P10 (commit `7f50c0f`, run from a `git archive` copy with the same pinned moment) produced **identical projections**: 1,860 rows, max difference 0, the same confidence labels and drivers. Getting there caught one subtlety: a rehearsal first dropped *all* of the replayed week's box-score rows, including Thursday's game, already played by the pinned Sunday morning; a live run keeps those rows, and a week-level median moved enough to flip one confidence label. Now only the games still to come are replaced. (The one intended change to the live path is new in P10: the game step now rewrites `game_features.parquet` each week, so from week 5 the player and team models see the current week's market numbers; before, nothing refreshed that file between manual `nfl features game` runs.). (The data on D: hadn't changed since that run.)

## 5. Playoff weeks

### What the 2025 rehearsal found

The first rehearsal of the 2025 Wild Card week, before any P10 change, ran `ready`, `game` and `digest` fine, but:

1. **The player model projected nobody.** Its data is regular season only, so the playoff games weren't there: 0 rows, the step degraded, and the digest fell back to the heuristic watch list (also regular season only: empty) with a misleading "the refit failed" note.
2. **Saved playoff projections could never be graded** (the scoring read regular-season box scores only).
3. **The digest featured eliminated teams**: "trending" Bengals and Chiefs, "under the hood" Giants and Saints players, and a title "2025 week 19".
4. **A check false positive**: "For Washington, that was the biggest drop ..." (Parker Washington, Jaguars) failed `unknown_entities` because the Commanders weren't playing. In the regular season nearly every team is in the payload, so it never showed.
5. **Week-18 fill-ins looked like risers.** Once the player model had playoff rows, 5 of the 20 Wild Card picks were backups who had started only in week 18, when their teams rested starters (Jeremiah Trotter Jr.: about 0% of the Eagles' defensive snaps in weeks 14–17, 100% in week 18, 0% in the Wild Card game).

### What a playoff week does now (D91)

- **Calendar and game model:** unchanged. The calendar already knew weeks 19–22 (`playoffs`, the round's first kickoff as the deadline, the week off before the Super Bowl), and the game model has always trained on and predicted playoff games (the Super Bowl as a neutral site).
- **Player model:** the round's games, injury report and rosters join the live inputs (`features/player_data.with_playoff_week`); history, features and training stay regular season, so **a playoff projection is the player's regular-season form against this opponent**. A playoff pick's "real role" needs a 50% snap share over his last **3** games (not 2) and 3+ games this season, and the "a regular teammate missed the last game" route is off: the final week's rested starters can't make a backup look like a regular. Saved playoff projections are graded with the playoff box scores (`load_inputs(playoffs=True)`, scoring only).
- **Team stat totals:** regular season only (0 rows and a note; nothing reads them).
- **Digest:** the title names the round ("2025 Wild Card round (week 19)"); the outlook's "most lopsided" / "closest" lists use at most half the slate (the one-game Super Bowl has none); team trends and "Last week under the hood" only cover the teams still playing, and from the Divisional round on, "last week" is the previous playoff round (this season's playoff rows count; earlier seasons stay regular season).

### The 2025 playoffs, rehearsed (final code)

Final code, `nfl weekly rehearse --season 2025 --weeks 19-22 --fresh` (2026-10-05, about 45 minutes; template writer). Each round's digest graded the round before it, as live:

| Week | Round | Games | Expected players | Player projections | Digest checks | Graded by the next week's report card |
|---|---|---|---|---|---|---|
| 19 | Wild Card | 6 | 451 | 1,927 | passed | picks 3 of 6, Brier 0.242 (Elo 0.196); watch list 9 of 20 above baseline, 16 inside their range |
| 20 | Divisional | 4 | 305 | 1,298 | passed | picks 4 of 4, Brier 0.152 (Elo 0.180); watch list 5 of 12 |
| 21 | Conference championships | 2 | 155 | 662 | passed | picks 2 of 2, Brier 0.165 (Elo 0.249); watch list 3 of 8 |
| 22 | Super Bowl (neutral site, marked "(n)") | 1 | 77 | 331 | passed | none: no weekly run follows the Super Bowl ([runbook → End of the season](../runbook.md#end-of-the-season-p10)) |

- **All 20 Wild Card picks played** (a snap share above 20% in the game), against 14 of 20 before the playoff role rule.
- The lists shrink with the slate (2 picks per team per side: 20, 12, 8, then the Super Bowl's 2 teams).
- **With GLM** (`nfl weekly rehearse --season 2025 --week 22 --steps digest --llm openrouter`): the Super Bowl digest passed every check first time (BaseTen, 2.3 minutes, 1.1 cents); the prose speaks of the game, never of "week 22". A first run had written "the same game is the most lopsided call and the closest game of the week", which is why a small slate's highlight lists now use at most half its games.
- **Not an accuracy measure:** a rehearsal reads today's data, so these weeks saw their closing lines and final injury reports. The live playoff grades start in January 2027.
- The team stat totals step projects 0 rows in the playoffs and its scorer logs "0 scoreboard rows" next to the player scorer's 23: expected (regular season only).

### Limits

- **The QB on Tuesday.** The Wild Card digest listed Malik Willis for the Packers: Jordan Love was in the concussion protocol on Tuesday and started on Saturday. That's the Tuesday view (P03); the Saturday injury update is the place to catch it.
- **Regular-season form only.** A player's earlier playoff games don't feed his next playoff projection. If live grading over a few seasons shows playoff projections clearly worse, train on playoff rows too (D91).
- **ESPN per-week data** (scoreboard, QBR) is pulled for regular-season weeks only; nothing reads it, and the news headlines the digest uses aren't per week.

## 6. The season review

`uv run nfl season review --season 2026` builds a Markdown review from the run records (`src/nflengine/ops/season_review.py`; written by sonnet-xhigh in P10). It reads what the weekly runs saved: each week's `predictions_games.parquet` (graded against the final scores), the season scorecard, the accuracy scoreboard, the watch lists, `pipeline_history.parquet` and each `run_summary.json`. Default output `D:\nfl-ml-data\reports\<season>\season-review.md`; at the end of the season, `--out documentation/reviews/2026-season-review.md`.

Its ten sections:

1. **How it was made:** the command, the files read, what was missing, which rows are live and which are labelled backtest (2026 weeks 1–3 are walk-forward rows: the project went live in week 4).
2. **At a glance:** season Brier for the model, Elo and the market; pick accuracy; points error; ECE next to its chance level; watch-list hit rate; player projections vs baseline; weeks published on time; the digest's first-try pass rate.
3. **Game model, Elo and the market**, with a week-by-week table (and regular season vs playoffs once there are playoff weeks).
4. **Calibration:** the season ECE against what a perfectly calibrated model would show on the same games, and the reliability bins.
5. **The accuracy scoreboard per target:** MAE vs the rolling baseline, how often the 80% range held, Brier for the chances; per position group; live rows apart from backtest rows; team stat totals in their own table.
6. **The watch list:** hit rate per week and per side.
7. **Digest checks:** passed first time / after a regeneration / with the banner.
8. **Pipeline uptime:** weeks published, on time, hours before kickoff, degraded or failed runs, every alert.
9. **Best and worst calls:** the most confident right and wrong picks, the biggest margin misses, the biggest watch-list hits and misses.
10. **Keep / cut / rebuild:** empty, with six prompting questions, for Rishi's review.

Every section says "no graded weeks yet" instead of failing when its input is missing. Tried on the 2025 simulation records (`--simulations`, the `--as-of` runs and backtest digests from P04–P09): 208 games graded through week 14, model Brier 0.2110 vs Elo 0.2201 vs market 0.2109, pick accuracy 66.7%, ECE 0.027 (chance 0.071), watch list 65.0% (182 of 280), 11 of 11 digests passed first time. On live 2026 so far it shows only week 4 (published; checks passed after one regeneration) and the labelled backtest rows of weeks 1–3.

`uv run nfl season weeks --season 2026` prints the one-line-per-week summary that also closes the review's uptime section.

## 7. Roster churn

Checked on the 2026 offseason, which had already happened (opus-high, read-only, 2026-10-05): do trades, free agency, the draft and coaching changes reach the graph, the projections and the preseason priors?

**What works**

- **The graph:** players' team stints come from the weekly rosters, so movers have their 2026 stints with the new team (Geno Smith with the Jets, Kyler Murray with the Vikings); `TRADED_TO` holds the season's 26 trades (Geno Smith → NYJ from LV, 2026-03-10), `DRAFTED_BY` 255 draft picks.
- **Projections:** all 60 movers projected in week 4 (of 107 with real 2025 usage) carry their new team (Walker KC, Waddle DEN, DJ Moore BUF, Cousins LV).
- **Baselines:** a player's rolling baseline carries his own 2025 mean even if it was for another team (3 pseudo-games of last season, doc 04 C); the model also knows `use_new_team`. Intended, and now written down here.
- **Game QBs** in the live weeks are right (they come from the schedule).
- **Priors:** the ratings start each season at 0.9 × last season's fit, fading week by week; Elo reverts a third of the way to 1505 (the Jets 1,295.5 → 1,365.4). Both follow the franchise, not the roster; roster churn reaches the game model only through the QB adjustment.

**Fixed in P10: head coaches.** nflverse's 2026 schedule still lists last season's coach for three new hires (ATL Raheem Morris for Kevin Stefanski, ARI Jonathan Gannon for Mike LaFleur, BUF Sean McDermott for Joe Brady), misspells a fourth (LV "Klint Kubliak", which split him from the coaching seed's Klint Kubiak), and has no 2025 interims (Mike McCoy, TEN, from week 7; Mike Kafka, NYG, from week 11). The graph's coach stories (Q5, the Q5b coaching tree) would have used the wrong coaches. `config/head_coach_fixes.csv` corrects them from a given week on, applied when the graph loads the games (D92); add a row whenever a coach changes mid-season or the schedule is wrong.

**Left for the 2027 pre-season** (each matters most in week 1, and the first two change model inputs, so they belong with the walk-forward re-tune, not mid-season):

1. **The week-1 QB rule** picks last season's main starter even when he has left: 8 of 32 teams wrong for 2026 week 1 (LV → Geno Smith, who was on the Jets; NYJ → Justin Fields, on the Chiefs; MIA → Tua, on the Falcons; plus ATL, CLE, MIN, SEA, WAS). Live weeks use the schedule's QB, so only training rows, backtests and the week-1 dry run see it. Fix: check last season's starter against the week-1 roster (`features/qb.py`).
2. **The QB-change flag of the ratings prior** is false for every 2025–26 team-season (8–15 a season before): harmless today (the QB pull is 0, D46) but needed before the re-tune evaluates it again.
3. **Movers and rookies in week 1:** a player who changed teams isn't projected until he has played for the new team (2026 week 1: 0 of 95 movers, 0 of 32 rookies; 542 of 545 regulars by week 2). A mid-season trade costs that player one week too. Fix: re-home active-roster regulars instead of dropping them (`features/player.upcoming_rows`).
4. **The week-1 graph** has no current-season team stints yet, so former-team stories (Q1, Q6) can't fire in week 1.
5. The nflverse trades feed stopped on 2026-04-17 (two later trades missing); a roster-continuity term for the ratings prior is an idea for the re-tune.

The full list with `file:line` references is in the P10 session log in PROGRESS.

## 8. The pre-season checklist

The steps and commands are in the [runbook](../runbook.md#pre-season-checklist-p10). Why each one is there:

- **Data refresh before the season switch.** While `seasons.current` is still the finished season, `nfl ingest` re-pulls that whole season (nflverse keeps correcting stats for weeks) and picks up its participation data once nflverse publishes it. Older seasons are pulled once (D32); after the switch, the finished season counts as history. Participation stays research only: it isn't known on a Tuesday (doc 03's availability rule).
- **The season switch.** `--auto` refuses to run while the calendar's season and `seasons.current` disagree, so the switch can't be forgotten silently. The coaching seed is rebuilt from Wikipedia with the new coordinators.
- **The re-tune (🧑).** The settings were tuned once before 2026 and stay fixed for a season (doc 04). With 2026 in the walk-forward window, Rishi re-runs the tuning and backtests, with a rule written down before the runs that count (P08's habit) and a decisions-log entry for every setting that changes.
- **The dry run.** `nfl weekly rehearse --season 2026 --week 1` runs the week-1 code path (ratings leaning on the prior, players with no games this season) after the offseason's upgrades. Tried on 2026 week 1 (2026-10-05): every step `ok`, 865 expected players, 2,586 player projections, 128 team-total rows, the digest passed its checks. It also showed the week-1 QB limit of §7: for already-played games the expected QBs come from last season's starters (`last_season_main`), so the rehearsal gave the Jets Justin Fields, the Raiders Geno Smith and the Dolphins Tua Tagovailoa, all of whom had left. A live week 1 takes the schedule's projected starters; the fix belongs to the 2027 pre-season.
- **✋ Ready.** Rishi signs off before week 1.

## 9. Big Data Bowl 2027

**Not announced as of 2026-10-05** (opus-high's web check): `kaggle.com/competitions/nfl-big-data-bowl-2027` returns 404, and the NFL's Big Data Bowl page still describes the 2026 edition. Past launches: BDB 2026 on 2025-09-25 (a movement-prediction task: players' paths while the ball is in the air, scored on live 2025 weeks 14–18), BDB 2025 on 2024-10-10 (pre-snap), BDB 2024 around 2023-10-16 (tackling). So 2027 is later than last year but still inside the usual mid-October window.

**What to check:** Kaggle's competition search and the NFL Football Operations / Next Gen Stats accounts, weekly until about November 1 (a mentorship call usually comes first); if nothing by mid-November, again in the spring.

**Recommendation: no Track 2 extension yet.** Track 2 (T00–T04, not started) works on the BDB 2026 data. When T00 starts, make it edition-agnostic (data under `bdb\<edition>\raw`, a dataset tag per edition, a loader keyed by edition with a schema check, the week split in config), so a 2027 movement task with newer seasons becomes a second dataset rather than a rewrite. A mid-October launch puts the deadline in early January, during the playoffs: entering is Rishi's call.

## 10. What changed in the code (P10)

| File | What |
|---|---|
| `src/nflengine/clock.py` | `utc_now()` / `pinned()`: the steps' clock |
| `src/nflengine/paths.py` | `redirect_data_root()`: `ensure_data_root()` returns a redirected `DataPaths` inside the block |
| `src/nflengine/ops/rehearsal.py` | `RehearsalPaths`, `run_rehearsal`, seeding and the wipe guard |
| `src/nflengine/ops/season_review.py` | `week_lines`, `build_season_review`, `render_review`, `write_review` |
| `src/nflengine/cli.py` | `nfl weekly rehearse`, `nfl season weeks`, `nfl season review` |
| `src/nflengine/weekly.py` | `WeeklyOptions.graph` / `use_wandb` (rehearsals switch them off); the consistency step's clock |
| `src/nflengine/models/game_runs.py`, `player_runs.py`, `team_runs.py`, `digest/run.py` | "now" from `clock.utc_now()`; `_seed_history` cut to earlier seasons; playoff rows and grading; the playoff role rule (`_role_ok`) |
| `src/nflengine/features/player_data.py`, `features/player.py` | `with_playoff_week`, `load_inputs(playoffs=)`, `upcoming_rows(now=)` |
| `src/nflengine/digest/under_hood.py`, `build.py`, `payload.py`, `render.py`, `checks.py`, `players.py`; `ops/calendar.py` | the playoff digest (round name, slate teams, the previous round as last week), the surname check fix, playoff grading of watch picks |

Tests: `tests/ops/test_rehearsal.py`, `tests/ops/test_season_cli.py`, `tests/ops/test_season_review.py`, `tests/test_playoffs.py`, `tests/digest/test_playoff_digest.py`, one new case in `tests/digest/test_checks.py`.

## 11. Limits and what's next

- A rehearsal proves the code path, not accuracy (today's data on a past week).
- Runs stay manual (D71); scheduling later wraps the same commands (runbook → Scheduling later).
- The season review is generated, not written: the judgement (keep / cut / rebuild) is Rishi's, in February.
- Next on the plan: the weekly runs on the calendar above, and Track 2 (T00) whenever Rishi starts it.
