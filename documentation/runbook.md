# Runbook: weekly operations

How to run the weekly NFL digest, what normal looks like, and what to do when something isn't normal. Written in P07. The background (how each piece works and why) is in the [weekly operations guide](guides/weekly-operations.md).

> **Manual-first (D71).** Nothing runs on a schedule. Each week someone (Rishi, or an agent he asks) runs **one command on Tuesday** and, optionally, **one on Saturday**. Everything a scheduler would need is already built (the calendar decides the week, a "not ready" run exits 3 so it can be retried, a lock stops overlaps, and every run records itself), so scheduling it later only means wrapping these commands ([Scheduling later](#scheduling-later-not-now)).

## The weekly routine

| When | Command | What it does | Normal result |
|---|---|---|---|
| **Tuesday, 10:00 ET or later** (after Monday Night Football; nflverse updates overnight) | `uv run nfl weekly run --auto`, or **Run week N** in the control room (`uv run nfl app`; [Run the week from the control room](#run-the-week-from-the-control-room-cr02), the same command with `--expect-week N`) | Works out the season and week from the calendar, then ingest → ready → curate → ratings → game → graph → player → digest, then the run records (summary, history, drift checks, W&B pipeline run, season dashboard) | Exit 0, last line `ok: 2026 week 05: ok`, the digest at `D:\nfl-ml-data\reports\2026\week05-digest.md`, about 5 minutes plus the LLM (2–40 minutes) |
| Any time | `uv run nfl weekly status` | What the calendar says now, each step's state for this week and last, the lock, the last 5 runs | Read only |
| **Saturday, 10:00 ET or later** (final injury designations come out Friday) | `uv run nfl weekly injury-update --auto`, or **Saturday injury update** in the control room's week header | Re-pulls injuries, news, lines and depth charts, re-predicts the week's games and players, compares with Tuesday | "nothing material changed" or "addendum published → `reports\2026\week05-injury-update.md`". Material = a win probability moved ≥ 5 points, or a watch-list player's status changed to or from Doubtful / Out / IR (a plain Questionable tag doesn't count, D73). One `pipeline_history` row per update (`ok`, `degraded` or `failed`) |
| After either run | W&B: the newest `weekly-pipeline` run and the **2026 Season Dashboard** report (every chart explained: [W&B guide](guides/weights-and-biases.md#the-season-dashboard-p07-every-chart-explained)) | Step times, freshness, checks, drift; the season charts | The 2-minute checklist in the [W&B guide §5](guides/weights-and-biases.md#what-to-check-each-week-in-2-minutes) |

Before the first run of a session it doesn't hurt to run `uv run nfl doctor` (every line OK).

**After each Tuesday run (P10):** look at any drift alert it printed ([Drift alerts](#drift-alerts): an agent investigates and proposes, Rishi decides), then add the week's line to `documentation/plans/PROGRESS.md` → **Season log**. `uv run nfl season weeks --season 2026` prints it, e.g. `week 05: published ✓ on time (58.2 h before kickoff), checks passed first time, 0 drift alerts`.

### Exit codes of `nfl weekly run`

| Code | Status | Meaning | What to do |
|---|---|---|---|
| 0 | `ok` / `degraded` | Published. `degraded` means a fail-soft step couldn't do its job (Neo4j, the player refit) and the digest says so in a banner or footer | Nothing; read the alert lines it printed |
| 0 | `already_done` | `--auto` found this week already published | Nothing. `--force` runs it again |
| 0 | `idle` | Offseason: no game left to predict | Nothing |
| 1 | `failed` | A step failed; nothing was published | See [Rerun a failed week](#rerun-a-failed-week-or-resume-from-a-step) |
| 2 | — | A usage error (for example neither `--auto` nor `--season` / `--week`) | Fix the command |
| 3 | `not_ready` | Last week's games aren't all final and in nflverse's play-by-play yet | Run again later (every few hours). Before a full ingest, `--auto` refreshes only the schedule; if last week still has games without a final score it stops at once (no full ingest, no Odds API calls). After the retry window (Wednesday 18:00 ET, or the first kickoff if earlier) it raises an alert |
| 4 | `locked` | Another weekly run or injury update is running | Wait for it ([The lock](#the-lock)) |
| 5 | — | The data drive isn't connected (or `NFL_DATA_ROOT` is wrong); nothing was written | Connect D:, run `uv run nfl doctor`, run again |
| 6 | `week_mismatch` | `--expect-week N` was given (the control room always gives it) and the calendar's week isn't N: it stopped before any step, wrote no records and released the lock (CR02, D103) | Nothing ran. Reload the control room (its week comes from the calendar) or check `uv run nfl weekly status` |

## What `--auto` decides

`--auto` reads the newest schedule snapshot and the clock (`src/nflengine/ops/calendar.py`):

- **Which week:** the earliest week that still has a game kicking off after now. On Tuesday that's the next slate; on a Sunday afternoon it's the week being played.
- **Is last week done?** The `ready` step checks that every game of week N−1 has a final score and is in play-by-play (after a fresh ingest).
- **The deadline:** the first kickoff of the week (Thursday 20:15 ET most weeks; Thanksgiving 12:30 ET; a Wednesday on Christmas 2024; an international morning game). It is printed, and the run summary records whether the digest went out before it.
- **Special weeks** it names (printed on the `slate:` line): `thursday`, `thanksgiving`, `black_friday`, `christmas`, `friday`, `saturday`, `midweek`, `monday_doubleheader`, `neutral_site`, `international`, `morning_kickoff`, `byes` (with the teams), `early_season` (weeks 1–3), `final_week` (week 18), `playoffs`.
- **Already published?** Then it stops with `already_done` (exit 0), so running it twice is harmless.

What it printed on Sunday 2026-10-04 (week 4 already published):

```
Sun 2026-10-04 15:55 ET: 2026 week 4 (regular)
previous week 3: 16/16 final, complete
deadline (first kickoff): Thu 2026-10-01 20:15 ET, passed 67.7 h ago
10 game(s) of week 4 already kicked off
slate: 16 games on thu, sun, mon; special: thursday, neutral_site, international, morning_kickoff
neutral site: IND@WAS (abroad)
late run: week 4's first game kicked off already; games that started keep their earlier predictions (or get none)
already_done: 2026 week 04 is already published; nothing to do
```

Add `--dry-run` to see the plan without running anything (it exits 3 if last week isn't final yet). Add `--expect-week N` to make the run stop (exit 6, nothing written) unless the calendar's week is N; it only works with `--auto`.

## How to ...

### Rerun a failed week, or resume from a step

The failed step and its reason are printed, and recorded in `runs\<season>\week<NN>\weekly_run.json` and `run_summary.json`. Fix the cause, then resume from that step (earlier steps are kept):

```powershell
uv run nfl weekly run --season 2026 --week 5 --from-step game --promote
```

`--promote` moves W&B's `production` alias to this resume's fits, as the `--auto` run being finished would have done (a manual run doesn't promote without it). The control room's **Resume week N from <step>** button runs exactly this command, for the current week's failed run only (D103).

Steps, in order: `ingest`, `ready`, `curate`, `ratings`, `game`, `graph`, `player`, `digest`. Every step is idempotent, so re-running an earlier one is safe; games that already kicked off keep their earlier predictions (D55).

**Since P08** the `player` step refits 23 player targets (the 11 P06 ones plus TDs, interceptions, QB rushing, sacks, QB hits and CB/S coverage, D82), then the shipped team stat totals into `predictions_teams.parquet` (D80), then the consistency layer (receptions ≤ targets; the yards gaps go to `consistency.json`, D81). The team fit and the consistency layer never degrade the step: a failure shows as "team fit skipped (…)" / "consistency skipped (…)" in its detail line. The game step still fits v0 (`game_model.version`; v1 evaluated, not promoted, D79).

### Run the week from the control room (CR02)

The control room (`uv run nfl app`, the [guide](guides/control-room.md)) runs the same commands as above, with the same lock and records. The terminal commands keep working as before.

1. **Start it:** `uv run nfl app` (opens `http://127.0.0.1:8765/` on the current week's Pipeline tab). Leave its terminal open.
2. **Pre-flight** (the panel at the top of the Pipeline tab) checks the calendar week, last week final, not published yet, the run lock, the data drive, Neo4j, the keys (by name) and `seasons.current`.
   - **Run week N** is greyed out with the reason while a blocking check fails.
   - "Last week isn't final" from an old schedule snapshot is only a **warning**: the run refreshes the schedule first and stops with exit 3 if it really isn't final. After such a not-ready run the button stays greyed for 30 minutes ("Try again after …"), then **Check again** turns it back on.
   - Neo4j down is a warning: the run starts it.
3. **Press Run week N, read the confirm dialog** (week from the calendar, last week, deadline, steps, what gets published, the command `nfl weekly run --auto --expect-week N`) and confirm.
4. **Watch it:** the now-bar (step x of 9, what it's doing, elapsed, about how long is left), the run in the drive chart / pipeline map / timeline, and the log streaming. The digest's GLM step shows "waiting for the model" with the elapsed time and the route for its whole length (3–40 minutes); the provider, time, reasoning count and cost appear when the call returns. **Closing the tab or the browser doesn't stop the run**; reopen the page and it picks the run up again (even one started from a terminal).
5. **When it ends:** a toast and a browser notification ("Week 5 published · …" or the failure), the sidebar and tabs refresh. Then do the usual checks (the digest's footer, `pipeline-2026-w05` in W&B, the Season log line).

| What you see | What it means | What to do |
|---|---|---|
| Greyed **Run week N**, "Week N is already published" | `--auto` would only say `already_done` | Nothing. A re-run of a published week stays in the terminal (`--force`) |
| Greyed, "Another run holds the lock" | A run (or an injury update) is going, maybe from a terminal | Watch it in the app; wait |
| Greyed, "Try again after 10:32 AM" | The last run stopped as not ready a few minutes ago | Wait, then **Check again** |
| Greyed, a key "not set" | A required variable (by name) isn't in the env file | Rishi fixes the env file; `uv run nfl doctor` |
| Red banner "The player step failed." + **Resume week N from player** | A step failed; nothing was published | Fix the cause, then press Resume (it runs `--from-step player --promote` for the current week only) |
| Toast "The calendar's week changed: nothing ran" | Exit 6: the week moved between the page and the run | Reload the page |
| Toast "Week N−1 isn't final yet: try again later" | Exit 3 | Wait and try again (the retry window is in the header) |

**Saturday:** the week header's **Saturday injury update** button opens on Saturday (ET) once the week's Tuesday run is published, until the week's last kickoff; otherwise it's greyed out with the reason. It runs `nfl weekly injury-update --auto --expect-week N` (exit 6, nothing written, if the calendar's week moved on before it started), shows its own live log under the header, and its toast says whether an addendum was published (the Digest tab then shows it).

**What stays in the terminal:** re-running a published week (`--force`), `--as-of` simulations, graph rebuilds, resumes of older weeks, anything that deletes. The app runs exactly three commands (D103).

**Rehearsal mode:** `uv run nfl app --rehearsal` points the Run button at `nfl weekly rehearse` of the newest published week, into `rehearsals/control-room` (nothing live is touched: no W&B, no graph write, no records; a banner says so). Use it to try the live views. `--fail-at <step>` (a hidden option, rehearsal only) makes the first rehearsal fail at that step on purpose, to try the failure and Resume buttons.

### Re-publish only the digest

After a prompt or check fix, or to change the writer:

```powershell
uv run nfl weekly run --season 2026 --week 5 --from-step digest
uv run nfl weekly run --season 2026 --week 5 --from-step digest --llm placeholder   # if the LLM is down
```

The report file is overwritten; the W&B `digest` artifact gets a new version. A live digest leaves out games kicking off within 90 minutes.

### Rebuild the knowledge graph

```powershell
uv run nfl graph build --season 2026 --week 5              # graph only
uv run nfl weekly run --season 2026 --week 5 --from-step graph   # graph, then player and digest
```

The graph is derived from the Parquet files, so it can always be wiped and rebuilt (about 1.5–2.5 minutes).

### Roll back a model alias

The weekly pipeline refits every week and reads the files on D:, not the W&B alias. `production` records which fit the published digest used: `--auto` moves it to each live fit (D72), and a manual run moves it only with `--promote`. To point it back at an older version (for the record, or before P08 starts reading it):

```python
import wandb

api = wandb.Api()
art = api.artifact("models-ontario-tech-university/nfl-analytics-engine/game-model:2026-w04")
art.aliases.append("production")  # W&B moves the alias off the version that had it
art.save()
```

Do it between weeks, never during a run. The same works for `player-model`.

### Run late (after the first kickoff)

Just run it. Games that already kicked off keep their earlier predictions or get none, and the rest are predicted as usual. The run summary records `on_time: false`.

### The lock

The lock is an operating-system lock on `D:\nfl-ml-data\runs\.weekly.lock`, held while a weekly run or an injury update runs. Windows releases it the moment that process ends, even if it crashed, so a lock is never left stuck and there is nothing to delete by hand. The file's text says who holds it (pid, computer, command, start time) and is emptied when the run ends; `uv run nfl weekly status` says `lock: free` or who holds it. Exit 4 therefore always means a run is really still going (Task Manager → Details → the pid in the file). If a run crashed, the next run reports the note it left behind as an `info` alert.

### Simulate a past week (time travel)

```powershell
uv run nfl weekly run --auto --as-of 2025-11-03T22:00 --dry-run   # Monday night: "not ready", exit 3
uv run nfl weekly run --auto --as-of 2025-11-04T10:00             # Tuesday: runs the week
```

A time without an offset is US Eastern. In the past, `--as-of` counts a game as final once `kickoff + ops.data_lag_hours` (10 hours) has passed, then produces that week's digest as a backtest (as-of data, placeholder writer unless `--llm`, **no graph sections**: a backtest graph build would wipe the live graph). It writes only under `runs\digest-backtests\` and `reports\backtests\`, logs a `weekly-pipeline` / `simulation` W&B run (`--no-wandb` skips it), sends no alerts, and never touches live files or the live graph.

### Neo4j or Docker is down

Nothing to do by hand: the `graph` step runs `docker compose up -d` once (and starts Docker Desktop first if the engine isn't running), then waits up to 3 minutes. Checked live on 2026-10-04: with the container stopped, Neo4j answered again after 50 seconds. If it still fails, the step is `degraded` and the digest goes out without its graph sections, with an ℹ️ banner. To retry the graph sections later: [Rebuild the knowledge graph](#rebuild-the-knowledge-graph), then re-publish the digest.

### The data drive is missing

Exit 5 and "Data drive D:\ is not connected ... Nothing was written." Connect it (it should keep the letter D:, set in Disk Management), run `uv run nfl doctor`, run again.

### nflverse is late (exit 3)

Usually play-by-play lands overnight after Monday Night Football. Run again every few hours. After Wednesday 18:00 ET (or the week's first kickoff, if that comes first: Christmas 2024 kicked off Wednesday 13:00) the run raises an error alert ("week N data still missing"): check nflverse's status (its GitHub releases page) before Thursday's kickoff. The digest can't go out until the week is final, because the ratings, report card and player scoring need it.

### A source is down (ESPN, odds, NGS site, weather)

The ingest step lists optional failures ("30 datasets (2 optional failures)") and the run goes on: no news section, model-only probabilities where lines are missing (the consensus item is skipped), weather absent. `run_summary.json` → `freshness` marks the dataset `stale` once its newest snapshot is more than 7 days old, and the drift check `data_freshness` alerts.

### An unofficial endpoint changed (P10)

ESPN, the NGS site and Open-Meteo are unofficial or free endpoints that can change shape without notice. Signs: the same optional failure on every run ("30 datasets (1 optional failures)"), a `failed` or `partial` result for that source in the run's ingest manifest (`D:\nfl-ml-data\raw\_runs\ingest-<time>.json`, with the error type in `detail`), its latest snapshot date falling behind in `uv run nfl data-status`, and after 7 days the `stale data source` drift alert.

1. **Nothing is urgent.** Every optional source fails soft; the digest goes out (no news section, model-only probabilities where lines are missing, no weather). nflverse is the one required source: if it fails, the run stops at `ingest`.
2. **Find the adapter and the error:** `src/nflengine/ingest/espn.py`, `ngs_site.py`, `weather.py`, `odds_api.py`. Re-run just that source: `uv run nfl ingest --sources espn` (or `ngs_site`, `weather`, `odds_api`).
3. **Look at what the endpoint returns now** (a browser or a scratch script through `ingest/http.PoliteClient`), fix the parser, and add the new response shape as a test fixture under `tests/fixtures/` (no network in tests).
4. **If it can't be fixed quickly,** switch the source off in `config/settings.yaml` → `sources` (`espn: false`, `ngs_site: false`, `open_meteo: false`, `odds_api: false`). The footer and `freshness` then say it's missing; switch it back on after the fix.
5. Log it in the decisions log if the source changes for good (doc 03 lists every source).

`uv run nfl doctor` says whether the Odds API keys (`ODDS_API_KEY`, backup `ODDS_API_KEY2`) are set; it never shows them. ESPN's per-week scoreboard and QBR pulls are regular season only (`seasontype=2`); nothing downstream reads them, and the news headlines the digest uses aren't per-week, so the playoffs don't need them.

### The digest failed its checks

It regenerates once with the list of failures; if that fails too, it publishes with the ⚠️ banner, and the pipeline prints a warn alert "digest checks failed" naming the checks. Look at `runs\<season>\week<NN>\checks.json`, then usually fix and [re-publish the digest](#re-publish-only-the-digest). The `digest-checks` skill covers known false positives.

### Week 1, playoffs, the offseason, a new season

- **Week 1:** nothing to wait for; ratings lean on last season (the digest says so).
- **Playoffs:** `--auto` targets weeks 19–22 like any other week (details: [Playoffs](#playoffs-p10)). Between the end of week 18 and nflverse adding the wild-card games, it refreshes only the schedule and, if the games still aren't there, records a not-ready run (exit 3); after the retry window it raises the error alert "games still not in the schedule".
- **Offseason:** `idle`, exit 0.
- **New season:** `--auto` refuses to run when the calendar's season differs from `seasons.current` in `config/settings.yaml`. Update it as part of the [pre-season checklist](#pre-season-checklist-p10), then rebuild the coaching seed with the new staffs: `uv run nfl graph coaching-seed --refresh` (also after a mid-season coordinator change; read its name-check lines; [knowledge-graph guide → Q5b](guides/knowledge-graph.md)).

### Rehearse a week (P10)

A **rehearsal** runs the real Tuesday steps (`ready`, `game`, `player`, `digest`) on a past or current week, with the clock pinned to 10:00 ET on the Tuesday before it, into a scratch folder. Nothing live is touched: no W&B, no graph write or graph sections, no run records, no alias moves, and not the weekly-run lock (since CR02 a rehearsal holds its own lock on its folder, `.<folder>.rehearsal.lock` beside it, so two rehearsals can't share a folder). Use it before anything risky (a dependency upgrade, a new step), for the playoff check every January and for the pre-season dry run.

```powershell
uv run nfl weekly rehearse --season 2025 --weeks 19-22 --fresh   # last season's playoffs, round by round
uv run nfl weekly rehearse --season 2026 --week 1 --fresh       # the pre-season dry run
uv run nfl weekly rehearse --season 2025 --week 19 --steps digest --llm openrouter   # one real-LLM digest
```

- Output: `D:\nfl-ml-data\rehearsals\<season>\` (its own `runs\`, `models\`, `reports\`, seeded with copies of the feature tables and the canonical backtests; `rehearsal.json` per week says what each step did). Weeks rehearsed into the same folder chain like live weeks (week 20's report card grades week 19's rehearsed picks). `--fresh` wipes the folder first (only a folder with its `REHEARSAL.md` marker).
- Exit 0 when every week ran (`ok` or `degraded`), 1 if a week failed, 4 if another rehearsal is using the folder.
- Since CR02 it also writes progress events (`runs\<season>\week<NN>\events\<run-id>.jsonl` under the rehearsal folder), which is how the control room's rehearsal mode draws it live.
- **What it proves, and what it doesn't:** the code path, end to end. Not the accuracy: it reads today's data, so closing lines and final injury reports leak into a past week's numbers. `ingest`, `curate`, `ratings` and `graph` aren't rehearsed (they rewrite shared files); it reads their current output.
- How long: a regular-season week takes about the live time without ingest (the player step dominates: about 95 s in week 4, longer each week); a playoff week about 10 minutes (the player refits walk through weeks 1–18 first).

### Playoffs (P10)

Checked on the 2025 playoffs with `nfl weekly rehearse --season 2025 --weeks 19-22` (the [season operations guide](guides/season-operations.md) has the results). What changes in a playoff week:

- **Calendar:** weeks 19–22; the `slate:` line says `playoffs`; the deadline is the round's first kickoff (a Saturday). The Super Bowl comes after a week off: the Tuesday after the conference games targets it.
- **Game model:** unchanged (it has always included playoff games; the Super Bowl is a neutral site).
- **Player model:** projects the round's games from each player's **regular-season** form (training stays regular season; the round's injury report and roster decide who plays, D91). Saved playoff projections are graded the next week like any other.
- **Team stat totals:** regular season only (the step notes 0 team projections; nothing reads them).
- **Digest:** titled by the round ("2026 Wild Card round (week 19)"); trends and "Last week under the hood" only for the teams still playing, and from the Divisional round on "last week" is the previous playoff round.
- **Before the first live playoff run** (by Tuesday 2027-01-12): rehearse last season's playoffs again (`nfl weekly rehearse --season 2025 --weeks 19-22 --fresh`) to be sure nothing broke since; every week should end `ok`.

### End of the season (P10)

No weekly run follows the Super Bowl (`--auto` says `idle`), so nothing ingests its result or grades its saved projections by itself (Sol review, P10). About a week after the game (PFR publishes a week late):

```powershell
uv run nfl ingest                          # the Super Bowl's score, box score, PFR
uv run nfl curate
uv run nfl scoreboard --season 2026 --week 21   # the conference games again (late PFR pressures)
uv run nfl scoreboard --season 2026 --week 22   # the Super Bowl's player projections
uv run nfl season weeks --season 2026     # one line per week: published, on time, checks, alerts
uv run nfl season review --season 2026 --out documentation/reviews/2026-season-review.md
```

(`nfl scoreboard` also logs the season's `scoreboard-2026-wNN` W&B run, as each weekly run does.) The review grades every game from the saved predictions and the final scores, so the Super Bowl is in it even though no digest's report card ever grades it.

The review is built from the run records and scorecards (game model vs Elo vs market, calibration, the accuracy scoreboard per target, the watch-list hit rate, the digest checks, pipeline uptime, best and worst calls) and ends with an empty "keep / cut / rebuild" section for Rishi's review (🧑). Lessons go into the decisions log.

### Pre-season checklist (P10)

Each summer, before week 1 (for 2027: August 2027). The dated plan is in `documentation/plans/PROGRESS.md` → Season calendar.

1. **Data refresh, before the season switch:** with `seasons.current` still on the finished season, `uv run nfl ingest` re-pulls that whole season (final stat corrections) and its **participation** data once nflverse publishes it (it says "not published yet" until then; research only, never a live feature: doc 03's availability rule). Then `uv run nfl curate` and `uv run nfl data-status`. Older seasons are pulled once (D32); `--refresh-history` re-pulls them if nflverse corrected history.
2. **Season switch:** `seasons.current` in `config/settings.yaml` → the new season; `uv run nfl ingest` (the new season's schedule and rosters); `uv run nfl graph coaching-seed --refresh` (new coordinators); check the schedule is in (`uv run nfl weekly status`).
3. **Roster churn:** trades, free agency, the draft and coaching changes reach the models through the weekly rosters, depth charts and schedules; nothing to do by hand. What is and isn't covered: [season operations guide → roster churn](guides/season-operations.md).
4. **Re-tune (🧑 Rishi runs it):** with last season now in the walk-forward window: `uv run nfl ratings tune` then `uv run nfl ratings eval`; `uv run nfl backtest game --variant model-only` and `--variant market` (and `nfl backtest game-weights`); `uv run nfl tune player --target <each>` then `uv run nfl backtest player --target <each>`; `uv run nfl tune team` / `uv run nfl backtest team`. Change a setting only when the walk-forward result improves by the rule written down before the run (the P08 habit). Record what changed in the decisions log.
5. **Dry run:** `uv run nfl weekly rehearse --season <last season> --week 1 --fresh` (every step `ok`), then `uv run nfl weekly run --auto --dry-run` once the new schedule is in.
6. **Big Data Bowl:** check whether the next edition is announced (usually in the fall); see the [season operations guide](guides/season-operations.md).
7. ✋ **Ready for the season:** Rishi signs off.

## What each alert means

Alerts are printed at the end of a run (`ALERT (level) title: text`), stored in `run_summary.json` → `alerts`, and sent as W&B alerts (W&B's own email or app notifications, set in your W&B user settings; `ops.wandb_alerts`). None of them changes anything automatically.

| Alert | Level | Meaning | What to do |
|---|---|---|---|
| `weekly run failed at <step>` | error | A step failed; nothing published | [Rerun / resume](#rerun-a-failed-week-or-resume-from-a-step) |
| `games still not in the schedule` | error | The next playoff round still isn't in nflverse's schedule after the retry window | Check nflverse's status; run again once the games are listed |
| `week N data still missing` | error | Not ready after the retry window (Wednesday 18:00 ET, or the first kickoff if earlier) | [nflverse is late](#nflverse-is-late-exit-3) |
| `<step> step degraded` | warn | Neo4j or the player refit couldn't run; the digest says so | Check the detail; rebuild the graph or re-run from `player` if time allows |
| `digest checks failed` | warn | Published with the ⚠️ banner | [The digest failed its checks](#the-digest-failed-its-checks) |
| `stale lock taken over` | info | The previous run ended without cleaning up its note (it crashed; the OS had already released its lock) | Look at why it crashed (its `run_summary.json`) |
| Drift alerts (`game model behind Elo`, `calibration`, `<group> worse than baseline`, `stale data`, `check failures`) | warn | A doc-08 drift signal crossed its threshold | See [Drift alerts](#drift-alerts) |

### Drift alerts

Doc 08's drift signals (`src/nflengine/ops/drift.py`, thresholds in `config/settings.yaml` → `drift`). Each needs enough graded weeks first (6 for the rolling ones: the first possible game alert in 2026 is the week-10 run); until then it says `insufficient_data`. **None of them retunes anything.** Over 2019–2025 these rules, on the probabilities the digest shows, fired in 2% (game) and 5% (calibration) of weeks, and the player rules never did, so an alert is worth a look.

| Alert title | Meaning | What to do |
|---|---|---|
| `game model behind Elo for 3 weeks` | The model's game-weighted Brier over the last 4 graded weeks is worse than Elo's, in 3 windows in a row | Look for a data or feature problem: a QB change the features missed, stale ratings, a source that stopped updating (`nfl data-status`). Single weeks swing (0.17–0.32 Brier); the season line on the dashboard matters more |
| `game probabilities poorly calibrated (ECE x)` | The season's ECE is above both 0.05 and the 90th percentile of what a perfectly calibrated model shows on the same number of games (about 0.16 after 5 weeks, 0.08 by week 18) | Check calibration on the walk-forward backtest (`uv run nfl backtest game`) before switching `game_model.calibration` to `platt` or `isotonic` (v0 has no calibration layer, D48). Look at the dashboard's calibration curve: which probability band is off? |
| `<group> projections behind the baseline for 3 weeks` | That position group's projections missed by more than the rolling baseline's over the last 4 scored weeks, 3 windows in a row | Check feature freshness (snap counts, NGS and PFR lag a week) and role changes in that group (`player-projections` guide); compare with the accuracy scoreboard run (`scoreboard-S-wNN`) |
| `<group> chances (TD, sack, interception ...) behind the baseline for 3 weeks` (P08, D85) | The group's chances (TD, sack, interception, pass defended; passing TDs / interceptions ≥ 1) scored worse on Brier than the players' rolling rates over the last 4 scored weeks, 3 windows in a row (never happened in 2019–2025) | Look at the target's reliability diagram on its walk-forward backtest (P08 player targets card) and the `scoreboard/brier_skill_*` curves; check the group's feature freshness |
| `stale data source` | A dataset's newest snapshot is more than 7 days old, or a weekly source is more than one week behind | `uv run nfl data-status`; re-ingest (`--from-step ingest`); if the source itself is down, the digest footer already says so |
| `digest checks failing (x% of recent runs)` | More than 20% of the last 4 digests went out with the ⚠️ banner | Look at those weeks' `checks.json`; revisit the prompt or check rules (`digest-checks` skill) |

Every alert's full text (with the numbers) is in that week's `run_summary.json` → `alerts` and `drift`, and the W&B pipeline run's `drift` table.

## Where everything is

| What | Where |
|---|---|
| The digest | `D:\nfl-ml-data\reports\<season>\week<NN>-digest.md` |
| The Saturday addendum (only if material) | `D:\nfl-ml-data\reports\<season>\week<NN>-injury-update.md` |
| Step states (resume) | `runs\<season>\week<NN>\weekly_run.json` |
| The run's full record | `runs\<season>\week<NN>\run_summary.json` |
| One row per run | `runs\<season>\pipeline_history.parquet` |
| The injury update's comparison | `runs\<season>\week<NN>\injury_update.json` |
| Simulations | `runs\digest-backtests\<season>\...` (same file names) |
| The lock | `runs\.weekly.lock` |
| A run's progress events (CR02): every step, progress, the GLM call, each log line | `runs\<season>\week<NN>\events\<run-id>.jsonl` (one file per run, resume or injury update) |
| What the control room launched, and each command's output | `cache\control-room\runs\<run-id>.json`, `cache\control-room\logs\<run-id>.log` |
| W&B | group `weekly-pipeline` (`pipeline`, `injury-update`, `simulation`, `main` = the digest), group `season-dashboard`, the report "2026 Season Dashboard"; runs launched from the control room carry the tag `via:control-room` |

## Scheduling later (not now)

Rishi chose manual-first for P07 (D71): the PC isn't a server and every manual run so far has worked. When the project is deployed, scheduling is a wrapper around the commands above:

1. Windows Task Scheduler (or cron / GitHub Actions, doc 02 → Orchestration): `uv run nfl weekly run --auto` on Tuesday 10:00 ET, again every 3 hours until Wednesday 18:00 (exit 3 = try again; exit 0 `already_done` = nothing to do), and `uv run nfl weekly injury-update --auto` on Saturday 10:00.
2. Power settings: "wake the computer to run this task", "run whether the user is logged on or not"; Docker Desktop set to start at login (the graph step starts it otherwise); D: kept connected with a fixed letter.
3. A notification channel beyond W&B alerts if wanted (SMTP or a push service; decisions log Q02).

The open question Q07 (can the PC stay on and keep D: connected?) is answered then.
