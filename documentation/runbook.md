# Runbook: weekly operations

How to run the weekly NFL digest, what normal looks like, and what to do when something isn't normal. Written in P07. The background (how each piece works and why) is in the [weekly operations guide](guides/weekly-operations.md).

> **Manual-first (D71).** Nothing runs on a schedule. Each week someone (Rishi, or an agent he asks) runs **one command on Tuesday** and, optionally, **one on Saturday**. Everything a scheduler would need is already built (the calendar decides the week, a "not ready" run exits 3 so it can be retried, a lock stops overlaps, and every run records itself), so scheduling it later only means wrapping these commands ([Scheduling later](#scheduling-later-not-now)).

## The weekly routine

| When | Command | What it does | Normal result |
|---|---|---|---|
| **Tuesday, 10:00 ET or later** (after Monday Night Football; nflverse updates overnight) | `uv run nfl weekly run --auto` | Works out the season and week from the calendar, then ingest → ready → curate → ratings → game → graph → player → digest, then the run records (summary, history, drift checks, W&B pipeline run, season dashboard) | Exit 0, last line `ok: 2026 week 05: ok`, the digest at `D:\nfl-ml-data\reports\2026\week05-digest.md`, about 5 minutes plus the LLM (2–40 minutes) |
| Any time | `uv run nfl weekly status` | What the calendar says now, each step's state for this week and last, the lock, the last 5 runs | Read only |
| **Saturday, 10:00 ET or later** (final injury designations come out Friday) | `uv run nfl weekly injury-update --auto` | Re-pulls injuries, news, lines and depth charts, re-predicts the week's games and players, compares with Tuesday | "nothing material changed" or "addendum published → `reports\2026\week05-injury-update.md`". Material = a win probability moved ≥ 5 points, or a watch-list player's status changed to or from Doubtful / Out / IR (a plain Questionable tag doesn't count, D73). One `pipeline_history` row per update (`ok`, `degraded` or `failed`) |
| After either run | W&B: the newest `weekly-pipeline` run and the **2026 Season Dashboard** report (every chart explained: [W&B guide](guides/weights-and-biases.md#the-season-dashboard-p07-every-chart-explained)) | Step times, freshness, checks, drift; the season charts | The 2-minute checklist in the [W&B guide §5](guides/weights-and-biases.md#what-to-check-each-week-in-2-minutes) |

Before the first run of a session it doesn't hurt to run `uv run nfl doctor` (every line OK).

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

Add `--dry-run` to see the plan without running anything (it exits 3 if last week isn't final yet).

## How to ...

### Rerun a failed week, or resume from a step

The failed step and its reason are printed, and recorded in `runs\<season>\week<NN>\weekly_run.json` and `run_summary.json`. Fix the cause, then resume from that step (earlier steps are kept):

```powershell
uv run nfl weekly run --season 2026 --week 5 --from-step game
```

Steps, in order: `ingest`, `ready`, `curate`, `ratings`, `game`, `graph`, `player`, `digest`. Every step is idempotent, so re-running an earlier one is safe; games that already kicked off keep their earlier predictions (D55).

**Since P08** the `player` step refits 23 player targets (the 11 P06 ones plus TDs, interceptions, QB rushing, sacks, QB hits and CB/S coverage, D82), then the shipped team stat totals into `predictions_teams.parquet` (D80), then the consistency layer (receptions ≤ targets; the yards gaps go to `consistency.json`, D81). The team fit and the consistency layer never degrade the step: a failure shows as "team fit skipped (…)" / "consistency skipped (…)" in its detail line. The game step still fits v0 (`game_model.version`; v1 evaluated, not promoted, D79).

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

### The digest failed its checks

It regenerates once with the list of failures; if that fails too, it publishes with the ⚠️ banner, and the pipeline prints a warn alert "digest checks failed" naming the checks. Look at `runs\<season>\week<NN>\checks.json`, then usually fix and [re-publish the digest](#re-publish-only-the-digest). The `digest-checks` skill covers known false positives.

### Week 1, playoffs, the offseason, a new season

- **Week 1:** nothing to wait for; ratings lean on last season (the digest says so).
- **Playoffs:** `--auto` targets weeks 19–22 like any other week. Between the end of week 18 and nflverse adding the wild-card games, it refreshes only the schedule and, if the games still aren't there, records a not-ready run (exit 3); after the retry window it raises the error alert "games still not in the schedule". Running the full pipeline on playoff weeks is P10 work: check the first one by hand.
- **Offseason:** `idle`, exit 0.
- **New season:** `--auto` refuses to run when the calendar's season differs from `seasons.current` in `config/settings.yaml`. Update it as part of the P10 pre-season work, then rebuild the coaching seed with the new staffs: `uv run nfl graph coaching-seed --refresh` (also after a mid-season coordinator change; read its name-check lines; [knowledge-graph guide → Q5b](guides/knowledge-graph.md)).

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
| W&B | group `weekly-pipeline` (`pipeline`, `injury-update`, `simulation`, `main` = the digest), group `season-dashboard`, the report "2026 Season Dashboard" |

## Scheduling later (not now)

Rishi chose manual-first for P07 (D71): the PC isn't a server and every manual run so far has worked. When the project is deployed, scheduling is a wrapper around the commands above:

1. Windows Task Scheduler (or cron / GitHub Actions, doc 02 → Orchestration): `uv run nfl weekly run --auto` on Tuesday 10:00 ET, again every 3 hours until Wednesday 18:00 (exit 3 = try again; exit 0 `already_done` = nothing to do), and `uv run nfl weekly injury-update --auto` on Saturday 10:00.
2. Power settings: "wake the computer to run this task", "run whether the user is logged on or not"; Docker Desktop set to start at login (the graph step starts it otherwise); D: kept connected with a fixed letter.
3. A notification channel beyond W&B alerts if wanted (SMTP or a push service; decisions log Q02).

The open question Q07 (can the PC stay on and keep D: connected?) is answered then.
