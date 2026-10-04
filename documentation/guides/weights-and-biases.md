# Weights & Biases guide (W&B)

**What this is:** a plain-language guide to everything this project records in Weights & Biases: which commands create a W&B run, what each run's charts, tables and artifacts show, how to read them, and what to check each week. Every run name, tag, metric key, chart and artifact below was checked against the code (as of P05) and, where possible, against the real 2026 week-4 runs.

**Related docs:** the design spec is [08 Experiment tracking](../08-experiment-tracking.md) (§9 below lists where the build differs from it). The deepest per-chart notes for the models are in the model cards' "Reading the W&B charts" sections: [team ratings](../model_cards/team_ratings.md#reading-the-wb-charts) and [game model v0](../model_cards/game-model-v0.md#reading-the-wb-charts). The graph side is in the [knowledge graph guide](knowledge-graph.md), and how the digest is written and checked is in the [LLM writer guide](llm-digest-writer.md). Agents use the `model-experiment` and `digest-checks` skills.

---

## 1. W&B in two minutes

W&B is a website that keeps a permanent record of every training, evaluation and weekly run. Our code sends it four kinds of things:

| Kind | What it is | Example here |
|---|---|---|
| **Config** | The settings a run started with. Written once | `sample_weights`, `git_commit`, `dataset_version` |
| **History** (curves) | Numbers logged again and again during the run, drawn as line charts | `bt/cum_brier_model`, one point per backtest week |
| **Summary** | One final number per key, shown on the run's Overview | `brier_model = 0.2199`, `checks_passed = true` |
| **Tables, plots, artifacts** | Tables of rows, custom charts (bar charts, reliability diagrams), and versioned files | `predictions_games` table, `game-model` artifact |

Most of our runs are **one-shot**: the weekly fit, the graph build and the digest each do their work, log, and finish within seconds to minutes. A run with no curves only shows W&B's own system charts (CPU, memory), so each one-shot run logs a few curves on purpose (`slate/*`, `load/*`, `season/*`). That was added in P04 after Rishi opened a run on his phone and saw nothing but system charts.

A glossary of W&B terms is at the end (§11).

## 2. How it's set up here

| Thing | Value |
|---|---|
| **Project** | `nfl-analytics-engine` (from `config/settings.yaml` → `wandb.project`) |
| **Entity** (account) | The W&B account's default, `models-ontario-tech-university`. The optional env variable `WANDB_ENTITY` overrides it |
| **Run URL** | `https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/<run id>` |
| **Local run files** | `D:/nfl-ml-data/wandb/wandb/run-<date>_<time>-<run id>/` (sweeps: `.../wandb/sweep-<sweep id>/`). W&B adds the inner `wandb/` folder itself |
| **Is W&B working?** | `uv run nfl doctor`: the `wandb` row says "authenticated as …; project …" or why not. It never shows the key |
| **Smoke test** | `uv run nfl wandb-smoke` (§6.1) |

**Every run starts through one function**, `tracking.init_run` (`src/nflengine/tracking.py`). It always sets:
- the project, entity, **group** and **job type**;
- the tag **`launched-by:rishi`** or **`launched-by:agent`**. Every command takes `--launched-by`; without it the code guesses: `agent` inside a Claude Code session, otherwise `rishi`;
- the local folder on D:.

**It also refuses secrets.** Before any run starts, `assert_no_secrets` checks the config (nested too). A key whose name contains `password`, `secret`, `token`, `api_key`, `apikey` or `credential`, or any `SecretStr` value, stops the command with "Refusing to log config key … looks like a secret". The API key itself reaches W&B only through the process environment and is never written to a config, a log or a file. One side effect: a harmless config key such as `max_tokens` is refused too, so rename it before adding it to a config. Summary keys aren't checked, which is why the digest can log `llm/prompt_tokens` as a summary number.

**Two config fields appear in almost every run:**

| Field | What it holds | Week-4 example |
|---|---|---|
| `git_commit` | Short commit hash, with `-dirty` when tracked files had uncommitted changes | `af45b24-dirty` (game fit `1vbkvjdj`) |
| `dataset_version` | `pbp_snapshot` (newest play-by-play snapshot date on D:) and `curated_run_at` (when the curated quality checks last ran) | `{"pbp_snapshot": "2026-10-03", "curated_run_at": "2026-10-03T18:53:00"}` |

W&B also records the exact command line, the full git commit and the machine on each run's Overview page by itself.

### Groups and job types (as built)

| Group | Job type | Run name | Created by |
|---|---|---|---|
| `smoke-test` | `smoke` | random (W&B picks one, e.g. `restful-serenity-1`) | `nfl wandb-smoke` |
| `track1-ratings` | `tune` | random, one per configuration | `nfl ratings tune` (a sweep) |
| `track1-ratings` | `eval` | `ratings-eval` | `nfl ratings eval` |
| `track1-ratings` | `eval` | `validate-trend` | `nfl ratings validate-trend` |
| `track1-game` | `backtest` | `backtest-<label>`, e.g. `backtest-model_only`, `backtest-market`, `backtest-model_only_qb-actual` | `nfl backtest game` |
| `track1-game` | `tune` | random, one per weight | `nfl backtest game-weights` (a sweep) |
| `track1-game` | `train` | `train-<season>-w<NN>` | `nfl train game`, the weekly `game` step |
| `track1-graph` | `build` | `graph-<season>-w<NN>`; `graph-<season>-w<NN>-backtest` | `nfl graph build`, the weekly `graph` step, `nfl digest --graph build` (live) |
| `track1-player` | `backtest` | `backtest-<target>-<group>`, e.g. `backtest-rec_yds-wrte` | `nfl backtest player` (P06; one run per target x position group) |
| `track1-player` | `tune` | random, one per configuration | `nfl tune player` (a sweep per target, `tune-<target>-<group>`) |
| `track1-player` | `train` | `train-<season>-w<NN>` | `nfl train player`, the weekly `player` step |
| `track1-player` | `eval` | `scoreboard-<season>-w<NN>` | `nfl scoreboard`, the weekly `player` step (scores week N−1) |
| `weekly-pipeline` | `main` | `digest-<season>-w<NN>` | `nfl digest` (live), the weekly `digest` step |
| `digest-dev` | `backtest` | `digest-<season>-w<NN>-backtest-<writer>`, e.g. `digest-2025-w09-backtest-openrouter` | `nfl digest --backtest` |

Run names aren't unique: re-running a week makes a second `train-2026-w04`. The **run id** (8 characters, in the URL) is unique. When several runs share a name, the newest one is the one that counts, and PROGRESS.md lists which ids were final.

### Tags (as built)

| Tag | On which runs | Meaning |
|---|---|---|
| `launched-by:rishi` / `launched-by:agent` | Every run | Who started it |
| `season:YYYY`, `week:NN` | Weekly fit, graph builds, digests | The week the run is about. Backtests and tuning runs don't have them |
| `prod` | Live digests only | The run behind a published digest |
| `prod-candidate` | Weekly fits (`train`) | A live fit (promoted or not; see the `production` alias in §8) |
| `live` / `backtest` | Graph builds | Live week or a past week's Tuesday |
| `backtest` | Backtest digests | |
| `p00`, `p02`, `p03`, `p05`, `p06` | Everything | The phase whose code made the run (hard-coded per module: digests say `p05` even though the digest arrived in P04) |
| `ratings`, `elo`, `trend`, `sweep` | Ratings runs; `sweep` also on game-weight runs | Run family |
| `game`, `variant:model_only` / `variant:market`, `qb:tuesday` / `qb:actual` | Game backtests and sweep runs (`game` on the weekly fit too) | Which configuration |
| `graph` | Graph builds | |
| `player`, `group:<qb, rb, wrte, edge or lbs>`, `target:<name>`, `scoreboard`, `smoke` | Player-model runs (P06) | Position group and target of a backtest or sweep; `scoreboard` on the live scoring run; `smoke` on a test backtest (`--smoke`, nothing saved) |
| `digest`, `llm:<writer>`, `graph:<status>` | Digests | Writer (`openrouter` or `placeholder`) and whether the graph sections were in (`ok`, `unavailable`, `off`) |

**Useful filters:** "everything for 2026 week 4" = tags `season:2026` + `week:04`. "Only what was published" = tag `prod` (digests only) or group `weekly-pipeline`. "Only Rishi's runs" = tag `launched-by:rishi`.

### What's in a local run folder

`D:/nfl-ml-data/wandb/wandb/run-20261003_224846-8txvvr92/` (the week-4 graph build) holds:
- `files/config.yaml`: the config, plus W&B's own record of the command line and git commit;
- `files/wandb-summary.json`: the final summary (the quickest way to read a run without the website);
- `files/media/table/*.table.json`: every table and the data behind each bar chart;
- `files/output.log`: the console output;
- `run-<id>.wandb`: the raw run record, which `wandb sync` can upload if a run was offline.

Other folders (one per entity and project, holding run ids) can appear when run files are fetched through the W&B API. They're downloads, not runs.

## 3. Finding your way around the W&B website

W&B redesigns its pages now and then, so the names below are the ideas, not exact button labels.

**The project page** (`nfl-analytics-engine`) has a few views in its side menu:

| View | What it's for |
|---|---|
| **Workspace** | Charts for every visible run, overlaid. Panels are grouped into sections by the start of the key: `bt`, `slate`, `load`, `season`, `count`, … One chart per key, one line per run |
| **Runs** (the runs table) | One row per run, with columns for config and summary values. Filter, group, sort and pick columns here |
| **Sweeps** | One page per sweep, with the parallel-coordinates, parameter-importance and scatter charts |
| **Artifacts** | Every artifact by type (`model`, `graph`, `digest`), then by name, then by version |
| **Reports** | Hand-built dashboards. The code doesn't create any yet (the "2026 Season Dashboard" is P07) |

**Filtering and grouping.** In the runs table (and the run list beside the workspace), filter on **Group**, **Job Type** or **Tags**, for example group = `track1-game` and job type = `backtest`. "Group by" group folds runs together. The eye icon next to a run hides or shows it in the charts. The workspace only draws the runs that are visible, so filter first, then read charts.

**One run's page** has tabs:

| Tab | What you'll find |
|---|---|
| **Overview** | Config, summary, tags, the command line, git commit, start time, run time, run state |
| **Charts** (or the run's workspace) | That run's curves, tables and custom charts |
| **Logs** | The console output (the same lines the terminal printed) |
| **System** | CPU, memory and disk over time. Ignore for our runs |
| **Files** | `config.yaml`, `wandb-summary.json`, `media/` |
| **Artifacts** | Artifacts this run logged (output) or used (input) |

**Comparing runs.** Select two or more runs in the runs table and look at the workspace: every chart overlays them. To compare summaries, add the summary keys as columns (e.g. `brier_model`, `checks_passed`) and read across rows.

**Custom x-axes.** Most of our curves don't use W&B's built-in step. They use their own x-axis key (`bt/step`, `slate/game`, `load/step`, `season/week`, `season`), set with `define_metric` in the code. The workspace picks this up automatically. If a chart ever shows a strange x-axis, edit the panel and set its x-axis to that key.

**Tables and bar charts.** A logged table appears as a table panel. A bar chart (`wandb.plot.bar`) appears as a custom chart, and its data also appears as a table named `<chart>_table` (e.g. `graph_counts_table`). Custom charts and tables only work on the website. **Standard line charts (`bt/*`, `slate/*`, `load/*`, `season/*`) also work in the W&B mobile app.**

**Artifacts.** Open the Artifacts view, pick a type and a name (e.g. `model` → `game-model`), and you see versions `v0`, `v1`, … Each version lists its aliases (`2026-w04`, `production`, `latest`), its files (with a preview), its metadata, the run that logged it, and a lineage graph. An alias points at exactly one version at a time. Logging a new version with the same alias moves the alias to it, and W&B moves `latest` by itself.

## 4. The weekly run, step by step

`uv run nfl weekly run --season S --week N` runs eight steps in this order (`src/nflengine/weekly.py`). Only the last four create W&B runs. Every step's status, start and finish time and a one-line detail (which includes the W&B URL for the last four) go to **`runs/<season>/week<NN>/weekly_run.json`** on D:. That file is the only record of the whole run. There is no W&B run for the pipeline as a whole yet (P07).

| # | Step | Same as | W&B run? | Where its record lives instead |
|---|---|---|---|---|
| 1 | `ingest` | `nfl ingest` | **No** | Run manifest `raw/_runs/ingest-<YYYYMMDDTHHMMSS>.json` (status, rows and parts per dataset); one `manifest.json` per snapshot folder `raw/<source>/<dataset>/snapshot=YYYY-MM-DD/` (rows, columns, sha256 per file) |
| 2 | `ready` | `nfl ingest --check-ready` | **No** | Only the step's detail line in `weekly_run.json`, e.g. "2026 week 3: READY (16/16 games final, 16/16 in play-by-play)". A failure exits with code 3 |
| 3 | `curate` | `nfl curate` | **No** | `curated/_quality/latest.json` (every quality check, `run_at`), `curated/_joins.json` (player-ID join rates). `nfl data-status` shows both plus snapshot dates and row counts |
| 4 | `ratings` | `nfl ratings build` | **No** | Tables `features/team_ratings`, `team_elo`, `team_trends`, `team_trend_drivers` (parquet); `features/_meta/team_ratings.json` (parameters, row counts, git commit, dataset version); `runs/<season>/week<NN>/ratings_sanity.md` |
| 5 | `game` | `nfl train game` | **Yes**: `track1-game` / `train` | Also `runs/<season>/week<NN>/predictions_games.parquet` and `models/game-model/<season>-w<NN>/` |
| 6 | `graph` | `nfl graph build` | **Yes**: `track1-graph` / `build` | Also `runs/<season>/week<NN>/graph_results.json` |
| 7 | `player` (P06) | `nfl scoreboard` (every earlier week) + `nfl train player` | **Yes**: `track1-player` / `eval` (`scoreboard-S-wNN`) and `track1-player` / `train` | Also `runs/<season>/week<NN>/predictions_players.parquet`, `runs/<season>/accuracy_scoreboard.parquet`, `runs/<season>/player_walkforward.parquet`, `models/player-model/<season>-w<NN>/`, `PlayerProjection` nodes in Neo4j |
| 8 | `digest` | `nfl digest` | **Yes**: `weekly-pipeline` / `main` | Also the run folder files and `reports/<season>/week<NN>-digest.md` |

**The first four steps** don't log to W&B because they're data plumbing, not models: there's nothing to chart. Their health shows up indirectly: `dataset_version` in every later run's config says which snapshot and curate run the models saw, and the digest's footer and `payload.json` (`meta.sources`) say how fresh each source was. Sending row counts and freshness to W&B is P07 work.

In week 4, those four steps wrote `raw/_runs/ingest-20261003T185245.json` (30 datasets, 0 failures), passed the readiness check (16/16 week-3 games), curated 32 tables with every quality check passing, and rebuilt the ratings in 3 seconds.

### 4.5 Step `game`: the weekly fit (`train-<season>-w<NN>`)

**What it does:** refits both game models (model-only and market) on every game before week N, predicts week N, writes `predictions_games.parquet` and the fitted models, then opens one W&B run. The run is opened after the work is done, so it has no training curves: everything is logged at the end. Code: `run_train` and `log_slate_charts` in `src/nflengine/models/game_runs.py`.

| | |
|---|---|
| Group / job type | `track1-game` / `train` |
| Name | `train-2026-w04` |
| Tags | `p03`, `game`, `season:2026`, `week:04`, `prod-candidate`, `launched-by:…` |
| Real runs | `ump6sftd` (first live fit, promoted), `hs6am1in`, `1vbkvjdj` (the fit the first live digest used) |

**Config** (the same dict as `models/game-model/<season>-w<NN>/meta.json`, plus season and week):
- `model_version` (`game-model-v0:2026-w04`), `created_at`;
- `configs`: both variants' settings (features, ridge alphas, sigma defaults, calibration `none`, sample weights);
- `sample_weights` (3 / 1.5 / 1), `feature_hashes` (model-only `a5c0938925`, market `a3355b8d8c`);
- `trained_through` (`2026-w03`), `sigma` (13.08 / 12.68), `n_train` (4,144 games each), `calibration`;
- `git_commit`, `dataset_version`.

**Charts.** The x-axis of every `slate/*` line is **`slate/game`**: the week's games in kickoff order (1 = the Thursday game). One point per game, so week 4 has 16 points.

| Chart | What it shows | How to read it |
|---|---|---|
| `slate/home_win_prob_shown` | The home win probability the digest prints (the market variant's, or model-only when a game has no complete line) | The headline line |
| `slate/home_win_prob_model_only` | Our football-only model | Where it separates from the market line is a "model vs consensus" disagreement, which the digest describes in words |
| `slate/home_win_prob_market` | The consensus: the current spread turned into a probability | Week 4's biggest gaps: IND@WAS (model-only 47%, market 36%), PIT@CLE (53% vs 42%), ARI@NYG (52% vs 42%), JAX@CIN (48% vs 58%) |
| `slate/home_win_prob_elo` | Elo's probability | A sanity line: it should broadly agree with the others |
| `slate/expected_margin` | The shown variant's expected home margin, in points | Positive = home favored. Week 4 runs from about −4.7 (IND@WAS) to about +12 for the biggest favorites (TEN@BAL, MIA@MIN) |
| `slate_home_win_pct` (bar chart, web only) | The shown home win % per game, labeled `01 PIT@CLE` … | Week 4: from 35.6% (IND@WAS) to 82.9% (TEN@BAL) |

**Table:** `predictions_games`: every row of the parquet file, both variants (week 4: 32 rows, 33 columns).

**Summary:**

| Key | Meaning | Week 4 (`1vbkvjdj`) |
|---|---|---|
| `games` | Games predicted (digest rows) | 16 |
| `kept_started_games` | Games that had already kicked off, whose earlier saved prediction was kept (the report card only grades predictions made before kickoff, D55) | 1 (Thursday's PIT@CLE) |
| `market_fallback_games` | Games shown as model-only because the line was incomplete | 0 |
| `sigma_model_only`, `sigma_market` | The spread of margin misses used to turn margins into probabilities | 13.08, 12.68 |

**Good looks like:** `games` equals the week's game count, `market_fallback_games` is 0 (or explained by a missing line), sigma is about 12.5–13.5, and no `slate/home_win_prob_shown` point sits at exactly 0.5 or outside 0.05–0.95 without a reason.

**Artifact `game-model`** (type `model`): the folder `models/game-model/<season>-w<NN>/`, i.e. `model_only.joblib`, `market.joblib`, `model_only_coefficients.csv`, `market_coefficients.csv`, `meta.json`. Its description is the full model card, its metadata the config above. Aliases: `<season>-w<NN>` always, plus `production` only with `--promote` (on `nfl train game` or `nfl weekly run`). Today: `v1` (`ump6sftd`) has `production`; `v3` (`1vbkvjdj`) has `2026-w04` and `latest`; `v0` is the superseded first round, `v2` an earlier re-fit. **Nothing reads this artifact yet**: the digest reads `predictions_games.parquet`. It is the record of exactly which model made each week's numbers, and the swap point for P07/P08 promotions.

### 4.6 Step `graph`: the knowledge-graph rebuild (`graph-<season>-w<NN>`)

**What it does:** wipes Neo4j, loads every node and relationship table as of the run, checks the counts, runs the query library, ranks insight candidates and writes `graph_results.json`. The W&B run opens first, so the load curve fills live. The step is fail-soft: if Neo4j is down it records `degraded` in `weekly_run.json` and the digest goes out without its graph sections. Code: `_init_wandb`, `_step_logger`, `_log_wandb` in `src/nflengine/graph/build.py`. The graph itself is explained in the [knowledge graph guide](knowledge-graph.md).

| | |
|---|---|
| Group / job type | `track1-graph` / `build` |
| Name | `graph-2026-w04` (live), `graph-2025-w09-backtest` (backtest) |
| Tags | `p05`, `graph`, `season:…`, `week:…`, `live` or `backtest`, `launched-by:…` |
| Real runs | `zb406f8m`, `pj1ipclt` (first full builds, identical counts), `8txvvr92` (the build the published week-4 digest used), `binb1iho` (a later rebuild, the first with the artifact) |

**Config:** `season`, `week`, `mode` (live / backtest), `run_time` (what "as of" means: now for live, 14:00 UTC on the Tuesday for backtests), `graph_start` (2018), `node_batch` (5,000) / `rel_batch` (10,000), `queries` (the five library queries), `git_commit`, `dataset_version`.

**Charts.** The `load/*` x-axis is **`load/step`**: one point per non-empty table, in load order (node labels first, then relationship types). Week 4 has 23 points: 8 node labels (the published-insight label is skipped while it's empty), then 15 relationship types.

| Chart | What it shows | How to read it |
|---|---|---|
| `load/seconds` | Seconds to write that table | Two spikes: `APPEARED_IN` (step 10, ~23 s for 210k relationships) and `DEPTH_CHART` (step 18, ~22 s for 254k). Everything else is under 4 s |
| `load/rows` | Rows written for that table | The same two tables dominate |
| `load/rows_per_s` | Write speed | Roughly 9,000–15,000 rows/s for the big tables on the D: bind mount (tiny tables look slow because of fixed overhead). A sudden drop on a big table means Neo4j or the disk is struggling |
| `load/elapsed_s` | Running total of load time | Ends near `time/load_s`: 55.9 s in `8txvvr92` |
| `graph_counts` (bar chart, web only) | Nodes per label and relationships per type | Should look the same every week, growing slowly. Week 4: 15,522 nodes, 588,282 relationships |
| `query_seconds` (bar chart, web only) | Seconds per library query | Every bar under 2 s is the target (doc 05). Week 4's slowest: `q1_revenge` 0.6 s |

The load points don't carry the table's name (a W&B curve can only hold numbers). To map a step to a table, use the order in `graph_counts` or the per-table `*_s` timings in `graph_results.json`: 1 Team, 2 Venue, 3 Coach, 4 Official, 5 Player, 6 Game, 7 TeamWeek, 8 GamePrediction, 9 PLAYED_FOR, 10 APPEARED_IN, 11 PLAYED_IN, 12 AT, 13 HEAD_COACH_OF, 14 COACHED_IN, 15 OFFICIATED, 16 THREW_TO, 17 ON_INJURY_REPORT, 18 DEPTH_CHART, 19 DRAFTED_BY, 20 TRADED_TO, 21 HAS_WEEK, 22 NEXT, 23 HAS_PREDICTION (shifts by one once `PublishedInsight` has rows).

**Tables:** `insight_candidates` (every candidate: type, matchup, strength, confidence, headline; 43 in week 4) and `insights_picked` (the picks with their section; 3 in week 4).

**Summary:**

| Key family | Meaning | Week 4 (`8txvvr92`) |
|---|---|---|
| `status` | `ok`, or `unavailable` plus `error` (sanitized: type and a fixed reason) | `ok` |
| `count/node/<Label>`, `count/rel/<TYPE>` | Counts read back from Neo4j | e.g. `count/node/Player` 7,079, `count/rel/APPEARED_IN` 209,733 |
| `count_mismatches` | Labels or types whose count differs from the tables (a mismatch fails the build before the queries run) | 0 |
| `time/<phase>` | Seconds per phase: `inputs_s`, `tables_s`, `wipe_s`, `schema_s`, `load_s`, `counts_s`, `queries_s`, `total_s` | wipe 27.0, load 55.9, queries 1.2, total 85.5 |
| `query/<name>/rows`, `/seconds`, `/error` | Per library query (`q1_revenge`, `q2_injury_ripple`, `q3_qb_change`, `q4_common_opponents`, `q8_trend_mismatch`) | rows 7 / 26 / 5 / 4 / 1; no errors |
| `query/max_seconds` | Slowest query | 0.60 |
| `insights/candidates`, `insights/picked`, `insights/skipped_novelty` | How many candidates, how many picked, how many held back because they ran in the last 3 weeks | 43, 3, 0 |

**Good looks like:** `status` ok, `count_mismatches` 0, counts close to last week's, `query/max_seconds` under 2, every `query/*/error` empty, `insights/picked` 2–4. A failed build (Neo4j down, count mismatch) shows as a **failed** run, with `status: unavailable` and a short `error` in its summary.

**Artifact `graph-results`** (type `graph`, new in P05): one file, `graph_results.json` (counts, expected counts, timings, every query's rows, every candidate, the picks, what was skipped and why). Aliases: `<season>-w<NN>` for live builds, `<season>-w<NN>-backtest` for backtest builds. Metadata: season, week, mode, run time, node and relationship totals, the picked insight ids. Only successful builds log it. It matters because **the copy on D: is overwritten by every rebuild of that week**: the week-4 file on D: now comes from `binb1iho` (03:34 UTC), built after the digest, and the artifact versions keep each build. `graph-results:v0` (`binb1iho`) is the first version; the earlier builds predate the artifact. **Planned reader:** P06 reads Q2 (injury ripple) and Q3 (QB change) rows from it as player-model features (comment in `build.py`).

### 4.7 Step `player`: scoreboard + weekly player fit (P06)

**What it does** (`_player` in `weekly.py`; code in `src/nflengine/models/player_runs.py`; how the model works: [player projections guide](player-projections.md)):
1. **Scores the season so far** (`score_weeks`, skipped in week 1): every earlier week's saved `predictions_players.parquet` (only rows made before kickoff) against the box scores, re-scored each run because PFR pressures arrive a week late, upserts `mode = live` rows into `runs/<season>/accuracy_scoreboard.parquet`, and logs one run `scoreboard-S-wNN` (group `track1-player`, job type `eval`, tags `p06`, `player`, `scoreboard`, `season:…`, `week:…`).
2. **Refits and projects week N** (`run_train`): every one of the 11 target models is refit for each week of the season up to N, continuing the canonical backtest's walk-forward history, then projects week N. One run `train-S-wNN` (job type `train`, tags `p06`, `player`, `season:…`, `week:…`, `prod-candidate`).
3. **Writes `PlayerProjection` nodes** into Neo4j (no W&B run; the step's detail line says how many).

A failed refit makes the step `degraded`, writes `player_status.json` = degraded in the run folder, and the digest falls back to the labelled P04 heuristic watch list (D67).

**Scoreboard run (`scoreboard-S-wNN`).** The x-axis of its curves is **`scoreboard/week`**, the scored week. Every run redraws the whole season from the file, so the newest one is the season view (like the digest's `season/*` charts).

| Chart / table | What it shows | How to read it |
|---|---|---|
| `scoreboard/improvement_<target>_<group>` (e.g. `scoreboard/improvement_rec_yds_wrte`) | That week's % improvement in MAE over the rolling baseline (counts: against the baseline's median, D65) | Positive = better than the baseline. One week is noisy (30–300 players); look at several weeks. Backtest pooled values: model card |
| `scoreboard/coverage_<target>_<group>` | Share of that week's results inside the P10–P90 range | About 0.8 is right; under 0.7 means ranges too narrow |
| `accuracy_scoreboard` (table) | Every row of the season file: `season, week, target, position_group, target_label, n_scored, n_not_played, mae_model, mae_baseline, improvement_pct, coverage_80, brier_*` (P08), `mode` | `mode = live` rows are graded pre-kickoff projections; `mode = backtest` rows are the season's earlier weeks re-run walk-forward by the weekly fit (2026 weeks 1–3, before the model existed) |

Until a live week is scored (the first is week 4, scored by week 5's run), the curves are drawn from the `backtest` rows: the first season scoreboard run, `ip2ny9sz` (`scoreboard-2026-w03`), shows 2026 weeks 1–3 re-run walk-forward (11 rows a week; average improvement +3.5%, +8.7%, +5.5%; coverage 0.77–0.82).

**Weekly fit run (`train-S-wNN`).**
- `projections_main` (table): the main stat per group for every projected player, sorted by `outperf_z` (player, team, opponent, P10 / P50 / P90, baseline, outperformance, confidence).
- `projections_per_target` (bar): projections per model this week (a sanity check of the slate).
- `top_outperformance` (bar): the 15 biggest projected jumps over baseline (z).
- Summary: `projections`, `players`, `low_confidence_share`.
- Real runs (2026 week 4): `u5k0ivia` (first live fit), `kl4fzvl8` (the re-run after the Sol review, behind the published digest `0eh6h6ll`): 1,860 projections for the 15 games that hadn't kicked off.
- Config: per target the feature count and hash, training rows, trained-through week, range parameter (conformal shift or NB dispersion), scale, settings.
- **Artifact `player-model`** (type `model`): every booster (`<target>-<group>-<q10|q50|q90|mean>.txt`) plus `meta.json`; aliases `<season>-w<NN>` (+ `production` with `--promote`); description = the [player model card](../model_cards/player-model-v1.md).

### 4.8 Step `digest`: the published digest (`digest-<season>-w<NN>`)

**What it does:** builds the payload (including the report card on last week's saved predictions), has the writer produce the prose, runs the checks, renders the digest, updates the season scorecard on D:, then opens one W&B run and logs everything. Code: `run_digest`, `log_digest_run`, `log_digest_charts`, `season_series` in `src/nflengine/digest/run.py`. How the checks work is in the [LLM writer guide](llm-digest-writer.md).

| | |
|---|---|
| Group / job type | `weekly-pipeline` / `main` |
| Name | `digest-2026-w04` |
| Tags | `p05`, `digest`, `season:2026`, `week:04`, `llm:openrouter`, `graph:ok`, `prod`, `launched-by:…` |
| Real runs | `0eh6h6ll` (the published week-4 digest with the player model, P06); before it `n1ifxdb0` (before the fact-check's driver wording) and `78r6sgfa` (P06 first live pass), `o0skjazq` (the P05 digest); earlier same-week passes `0rbvplt9`, `vvit9fdd`, `gf5j6sr1` (Neo4j-down test), `755t5su2`, `ev79x0i8` |

**Config:** `season`, `week`, `mode`, `run_time`, `prompt_hash` (changes whenever a prompt file changes), `llm_provider`, `llm_model` (`z-ai/glm-5.3-flash`), `word_budgets` (per section), `followed_teams`, `git_commit`, `dataset_version`.

**Charts.** The `season/*` x-axis is **`season/week`**: the **graded** week (the digest for week N grades week N−1, so the week-5 digest adds the point at x = 4). Every digest run redraws the whole season so far from the scorecard file, so the newest digest run always has the full picture (§7).

| Chart | What it shows |
|---|---|
| `season/brier_model`, `season/brier_elo`, `season/brier_market` | That graded week's Brier score for the digest's probabilities, Elo and the consensus line. Lower is better; ~0.25 is a coin flip |
| `season/cum_brier_model`, `_elo`, `_market` | Season to date, game-weighted. The main chart: model should end below Elo and near the market |
| `season/pick_accuracy`, `season/cum_pick_accuracy` | Share of winners called (ties and 50% calls left out) |
| `season/points_mae` | Average miss on each team's score, in points |
| `season/watchlist_hit_rate`, `season/cum_watchlist_hit_rate` | Share of played watch-list picks that hit (heuristic until P06) |
| `words_per_section` (bar, web only) | Words per digest section. The data table `words_per_section_table` also has each section's budget |
| `check_issue_counts` (bar, web only) | Issues found per check (warnings included) |

**In week 4 the `season/*` charts are empty.** No predictions were saved for week 3 (the first live run was week 4), so the report card's status is `no_saved_predictions` and there's no row to plot. The first live point (week 4 graded) arrives with the week-5 digest; expect `not_graded` 1 there, because Thursday's PIT@CLE prediction was made after kickoff. For what the curves look like with data, open the 2025 backtest digests (§6.4).

**Tables:**
- `season_scorecard`: the season's scorecard so far, one row per graded week (§7). 0 rows in week 4;
- `check_issues`: each issue: check, level (`fail` / `warn`), section, token, reason, sentence. Week 4: 2 warnings (`length_short` on the 23-word report card note, which code-written sections no longer trigger since the end of P05, and `hedging` on one trend sentence);
- `game_outlook`: the games as printed: away %, home %, score, margin, confidence call (15 games in week 4: Thursday's game had started).

**Summary:**

| Key family | Meaning | Week 4 (`o0skjazq`) |
|---|---|---|
| `checks_passed`, `checks_failed`, `checks_warnings` | The final verdict; failed and warning check names, comma-separated | `true`, empty, `length_short,hedging` |
| `check/<name>`, `check_issues/<name>` | 1/0 per check and the issue count, for all 11 checks (`complete`, `number_provenance`, `spelled_out_numbers`, `entity_binding`, `unknown_entities`, `meaning`, `banned_language`, `length`, `length_short`, `hedging`, `name_heuristic`) | every fail-level check 1; `hedging` and `length_short` 0 (warnings) |
| `regenerated`, `banner` | Whether a second writer attempt was needed; whether a warning banner was printed | `false`, `false` |
| `words/<section>`, `words_total` | Words per section and in all | 716 in all |
| `games`, `team_trends`, `under_the_hood_items`, `watchlist_items`, `news_items` | How many items each section got | 15, 4, 5, 5, 5 |
| `graph_status`, `graph_items` | Whether the graph sections were in, and how many insights | `ok`, 3 |
| `graph_more_items`, `qb_changes`, `starters_out` | The code-written lists (D63): "More from the graph" lines, QB changes flagged in the game table, starters-out entries | added after week 4's live digest; expect about 4–6, 0–7, 20–40 |
| `market_data_used` | Whether any shown game used the market variant | `true` |
| `report_card_status` | `first_week`, `no_saved_predictions` or `scored` | `no_saved_predictions` |
| `rc/picks_correct`, `rc/picks_total`, `rc/brier_model`, `rc/brier_elo`, `rc/points_mae` | The report card's numbers, only when it was `scored` | (none in week 4) |
| `llm/final_writer`, `llm/fallback` | Who wrote the final prose; whether the template fallback took over | `openrouter`, `false` |
| `llm/calls`, `llm/latency_s`, `llm/prompt_tokens`, `llm/completion_tokens`, `llm/reasoning_tokens`, `llm/cost_usd`, `llm/providers` | Real-LLM usage (absent for the placeholder writer) | 1 call, 146.8 s, 8,975 / 17,010 / 15,963 tokens, $0.0055, Novita |

**Artifact `digest`** (type `digest`): `payload.json`, `raw_llm_output.json`, `checks.json` and `digest.md` from `runs/<season>/week<NN>/`. Alias `<season>-w<NN>` (moves to the newest run of that week). Metadata: `prompt_hash`, `checks_passed`. Week 4: `digest:v5` (`o0skjazq`) has `2026-w04`; `v0`–`v4` are the earlier passes. Nothing reads it; it's the frozen record of exactly what was published and from which payload. The digest Markdown itself is also on D: at `reports/<season>/week<NN>-digest.md`.

## 5. One weekly cycle at a glance

| Step | W&B run | Charts | Artifact | Look at first |
|---|---|---|---|---|
| ingest | none | none | none | `weekly_run.json` → `ingest` detail ("30 datasets (0 optional failures)"); the run manifest for any `failed` dataset |
| ready | none | none | none | `weekly_run.json` → `ready` detail ("16/16 games final") |
| curate | none | none | none | `curated/_quality/latest.json` (any check not passed) or `nfl data-status` |
| ratings | none | none | none | `runs/<S>/week<NN>/ratings_sanity.md` |
| game | `track1-game` / `train`, `train-S-wNN` | `slate/*` lines, `slate_home_win_pct` bar | `game-model:S-wNN` (+ `production` with `--promote`) | `games`, `market_fallback_games`; the gap between `slate/home_win_prob_model_only` and `_market` |
| graph | `track1-graph` / `build`, `graph-S-wNN` | `load/*` lines, `graph_counts` and `query_seconds` bars | `graph-results:S-wNN` | `status`, `count_mismatches`, `query/max_seconds`, `insights/picked` |
| player | `track1-player` / `eval` `scoreboard-S-wNN` + `track1-player` / `train` `train-S-wNN` | `scoreboard/*` lines; `projections_per_target`, `top_outperformance` bars | `player-model:S-wNN` | `weekly_run.json` → `player` detail (projections, graph nodes); `scoreboard/improvement_*` for last week; `low_confidence_share` |
| digest | `weekly-pipeline` / `main`, `digest-S-wNN` | `season/*` lines, `words_per_section` and `check_issue_counts` bars | `digest:S-wNN` | `checks_passed`, `banner`, `graph_status`, `llm/fallback`; `season/cum_brier_model` vs `_elo` |

### What to check each week in 2 minutes

1. **Did it all run?** `runs/<S>/week<NN>/weekly_run.json`: every step `ok` (the graph may say `degraded`, which means no graph sections, with a banner). Or filter W&B on `season:S` + `week:NN`: there should be one `train`, one `graph` and one `digest` run, newest last.
2. **Digest run (`digest-S-wNN`, Overview):** `checks_passed` true, `banner` false, `graph_status` ok, `llm/fallback` false, `report_card_status` scored (from week 5 on).
3. **Season charts in that run:** `season/cum_brier_model` below `season/cum_brier_elo` and near `season/cum_brier_market`; `season/cum_pick_accuracy` in the 60s. Single weeks jump around: 2025 backtest weeks ranged from 0.17 to 0.32 Brier. Only a run of bad weeks matters.
4. **Graph run:** `status` ok, `count_mismatches` 0, counts close to last week, `query/max_seconds` under 2.
5. **Game fit:** `games` = the slate size, `market_fallback_games` 0, sigma about 13. On the `slate/*` chart, large model-only vs market gaps are what the digest's "model vs consensus" lines describe.
6. **Artifacts:** `digest:S-wNN`, `graph-results:S-wNN` and `game-model:S-wNN` all point at this week's final runs. If a model was promoted, `production` is on the right `game-model` version.

## 6. Everything outside the weekly run

### 6.1 `nfl wandb-smoke` (group `smoke-test`, job type `smoke`)

A fake training run that proves W&B works. It logs `train/loss` and `val/loss` (two falling curves with noise) and `step` for 50 steps, 0.15 s apart, so the chart fills while you watch. Then a `smoke_summary` table (`steps_logged`, `final_val_loss`) and the summary `final_val_loss`. Config: `steps`, `delay`. Tags `p00` + `launched-by`. No custom x-axis (the chart uses W&B's own step). The first one is `pfcqqjfy` ("restful-serenity-1").

### 6.2 Team ratings (`track1-ratings`; `src/nflengine/models/ratings_runs.py`)

All three commands score walk-forward predictions of each regular-season game's EPA-per-play margin, 2015–2025. Their curves use **`season`** (the year) as the x-axis: 11 points. Details and our values: [team ratings card → Reading the W&B charts](../model_cards/team_ratings.md#reading-the-wb-charts).

**`nfl ratings tune`: a sweep (job type `tune`, tags `p02`, `ratings`, `sweep`).** A W&B grid sweep named `ratings-tune-<YYYYmmdd-HHMM>`, one run per combination of `half_life_weeks`, `prior_regression` and `ridge_alpha` (and `qb_change_regression` when given), minimizing `objective_mse`. Results also go to `runs/ratings/tune-<stamp>/results.csv` and `sweep.json` on D:.
- Curves: `season/mse_pred` (the ratings), `season/mse_base_last_season` and `season/mse_base_to_date` (baselines), the matching `season/cum_mse_*` (pooled through that season; the last point is the final number), `season/games`.
- Summary: `objective_mse`, `mae`, `corr_epa_margin`, `corr_points`, `mse_w1_3` / `mse_w4_8` / `mse_w9plus`, `mse_base_*`, `improvement_vs_*`, `mse_base_*_w1_3`, `games`.
- Sweep page: parallel coordinates and parameter importance for `objective_mse`. Real sweeps: `dwyj31wk` (175 runs, flat around half-life 12 / prior 0.1 / alpha 250, MSE 0.1041), `pbpcouy1` (QB-change pull; 0 is best), `3koh08dr` (2-config smoke).

**`nfl ratings eval` (job type `eval`, name `ratings-eval`, tags `p02`, `ratings`, `elo`).** The chosen parameters against both baselines, plus Elo.
- Curves: the same `season/mse_*` and `season/cum_mse_*`, plus `season/elo_brier_all`, `season/elo_log_loss_all`, `season/elo_accuracy_all` and the `_reg` (regular season only) versions.
- Panels: `by_week_mse` (MSE by week of season for ratings and both baselines; a custom line chart, web only), `by_week_table`, `by_season_table`, `elo_by_season`.
- Summary: the tuning summary plus `elo_brier`, `elo_log_loss`, `elo_accuracy`, `elo_brier_reg`, `ratings_beat_last_season`, `ratings_beat_to_date` (1 = beat it). Real run `hcir0po5`: MSE 0.1041 vs 0.1194 / 0.1249; Elo Brier 0.2214.

**`nfl ratings validate-trend` (job type `eval`, name `validate-trend`, tags `p02`, `trend`).** Does the trend predict the next 3 weeks beyond the rating?
- Curves: `season/r2_rating`, `season/r2_rating_trend`, `season/r2_rating_pve`, `season/r2_rating_both` (out-of-sample R² for each model), `season/delta_r2_trend` and `season/delta_r2_pve` (gain over the rating alone; around 0 = no help), `season/rows`.
- Panel: `by_season` table.
- Summary: `r2_*`, `delta_r2_*` with `*_ci90_low` / `*_ci90_high`, `seasons_improved_*`, `corr_trend_delta_vs_rating_miss`, `corr_perf_vs_expected_vs_rating_miss`, `decision_trend_delta` / `decision_perf_vs_expected` ("predictive" or "descriptive"). Real run `ae30tzkf`: ΔR² −0.0002, descriptive (D47).

`nfl ratings build` (the weekly `ratings` step) has no W&B run (§4).

### 6.3 Game model research (`track1-game`; `src/nflengine/models/game_runs.py`)

Full chart-by-chart notes: [game model card → Reading the W&B charts](../model_cards/game-model-v0.md#reading-the-wb-charts).

**`nfl backtest game` (job type `backtest`, name `backtest-<label>`).** Walk-forward by week over 2018–2025 (after a 5-season burn-in that isn't reported), logged live as it runs. Tags `p03`, `game`, `variant:…`, `qb:…`. Config: the model config, `sample_weights`, `feature_hash`, `report_seasons`, `train_start`, `burn_in_seasons`, `qb_mode`, `git_commit`, `dataset_version`. Predictions and the summary also go to `runs/backtests/game/<label>/` on D: (the digest backtests read the canonical `model_only` and `market` folders).
- **Live curves, x-axis `bt/step`** (1 = 2018 week 1, about 170 = the 2025 Super Bowl):
  - `bt/brier_model`, `bt/brier_elo`, `bt/brier_market`, `bt/brier_home`: that week's Brier for each predictor (jumpy);
  - `bt/cum_brier_model` and the `_elo` / `_market` / `_home` versions: every game so far (**the main chart**; final values 0.2199 / 0.2221 / 0.2104 / 0.2477 model-only);
  - `bt/cum_log_loss_model`, `bt/cum_accuracy_model`;
  - `bt/cum_margin_mae_model` vs `bt/cum_margin_mae_market` (10.2 vs 9.8 points), `bt/cum_total_mae_model`;
  - `bt/sigma` (should stay flat near 13), `bt/games`, `bt/season`, `bt/week` (lookups).
- **End-of-run panels:** `reliability_diagram` (predicted vs actual home win rate, with the perfect-calibration diagonal; web only), `reliability_table`, `brier_by_season` (web only), `by_season`, `by_week_bucket` (weeks 1–4, 5–9, 10+, playoffs), `predictions` (every game).
- **Summary:** `brier_<p>`, `log_loss_<p>`, `accuracy_<p>`, `ece_<p>` for p = model, elo, market, home; `brier_gain_vs_elo` / `_market` / `_home`, `beats_elo`; `seasons_beating_elo` of `seasons`; margin, total and per-team points MAE (`mae_margin_*`, `mae_total_*`, `mae_points_*`), `margin_rmse_model`; `score_margin_max_gap` (must be 0); `games`, `games_dropped`; `reg_brier_*`, `reg_brier_gain_vs_*`, `reg_games`; `market_ml_brier`, `games_ml`.
- Real runs: `5x33rk56` (model-only), `ekz4277b` (market). The first round (`r9gmo5rd`, `rkv43aot`, `goh98ltt`, `l5dmbew2`, `yv2rntfq`) had a sign bug and is superseded.

**Research variants of the same command** get a suffixed label so they never overwrite the canonical folders: `--qb-mode actual` (the listed-starter oracle: `backtest-model_only_qb-actual`, run `3ots68nz`, Brier 0.2183), `--calibration platt|isotonic`, `--win-method logistic`, `--current-weight` (e.g. `backtest-model_only_calibration-platt`). Same charts and summary.

**`nfl backtest game-weights` (a sweep, job type `tune`).** A W&B grid sweep named `game-weights-<variant>-<YYYYmmdd-HHMM>` over the current-season sample weight, minimizing `brier_model`. Tags `p03`, `game`, `sweep`, `variant:…`. Each run has the full `bt/*` curves; the summary adds `brier_model_w01-04`, `brier_model_w05-09`, `brier_model_w10+`, `brier_model_post`. Results also go to `runs/backtests/game/weights-<stamp>/`. Real sweep `aoflnafa`: 1× / 2× / 3× / 5× gave 0.21992 / 0.21984 / 0.21985 / 0.21989 (flat; 3× kept).

`nfl features game` (builds `features/game_features.parquet`) has no W&B run; its record is `features/_meta/game_features.json`.

### 6.3b Player model research (`track1-player`; `src/nflengine/models/player_runs.py`, P06)

Full chart-by-chart notes and real values: [player model card → Reading the W&B charts](../model_cards/player-model-v1.md#reading-the-wb-charts).

**`nfl backtest player --target <name>` (job type `backtest`, name `backtest-<target>-<group>`).** Walk-forward by week, 2019–2025 reported after 2 burn-in seasons (2017–2018). Tags `p06`, `player`, `group:…`, `target:…`. Config: the LightGBM settings, `weights`, `feature_hash`, `features`, `n_features`, `report_seasons`, `burn_in_seasons`, `dataset_version`, `git_commit`. Predictions + summary go to `runs/backtests/player/<target>-<group>/`; their scoreboard rows to `runs/backtests/player/scoreboard.parquet`.
- **Live curves, x-axis `bt/step`** (1 = 2019 week 1, about 124 = 2025 week 18): `bt/mae_model`, `bt/mae_baseline` (that week), `bt/cum_mae_model`, `bt/cum_mae_baseline` (**the main chart**: the model line should end below), `bt/improvement_pct`, `bt/cum_improvement_pct`, `bt/coverage_80`, `bt/cum_coverage_80` (should settle near 0.8), `bt/range_param` (the conformal shift for yards, the NB dispersion for counts), `bt/n`, `bt/season`, `bt/week`.
- **LightGBM training curves** `lgb/curve_<season>` (web only): each reported season's opening fit replayed with the previous season held out, train vs validation loss per boosting round (quantile loss for yards, Poisson deviance for counts). Validation should flatten, not turn up, by the last round.
- **End-of-run panels:** `by_season` (table), `mae_by_season` (line), `accuracy_scoreboard` (the weekly rows), `feature_importance` (gain of the last fit, top 25), `shap_summary` (mean |SHAP| over 2025, top 20, in target units), `predictions` (2025 rows).
- **Summary:** `n_scored`, `mae_model`, `mae_baseline` (counts: the baseline's median, D65), `mae_baseline_mean` (the raw rolling mean), `improvement_pct`, `mae_season_mean_same_rows` / `improvement_vs_season_mean_pct`, `coverage_80`, `spearman_outperformance` (does the projected gap to baseline rank the real one?), `share_role_baseline`, `seasons_beating_baseline` of `seasons`.
- `--smoke` tags a run `smoke` and saves nothing (the P06 smoke run: `xoahaq4o`).

**`nfl tune player --target <name>` (a sweep per target, job type `tune`).** A W&B grid sweep `tune-<target>-<group>` over `num_leaves` (7 / 15 / 31) × `min_data_in_leaf` (50 / 200) × `n_estimators` (150 / 300), minimizing `tune/mae_model`: a walk-forward over every other week of **2017–2018 only** (never the reported years; pressures, a PFR stat from 2018, uses late 2018 only). Summary `tune/mae_model`, `tune/mae_baseline`, `tune/improvement_pct`, `tune/n`. The best configuration per target goes into `settings.yaml` → `player_model.per_target` (D68).

### 6.4 Digest backtests (`nfl digest --backtest`; group `digest-dev`, job type `backtest`)

The same code as the live digest, producing a past week as if it were that week's Tuesday. The week's predictions come from the canonical game backtests, copied into `runs/digest-backtests/<season>/week<NN>/` for that week and every earlier week of the season, so the report card grades them exactly as it does live.
- **Name** `digest-<season>-w<NN>-backtest-<writer>`, tags `p05`, `digest`, `season:…`, `week:…`, `llm:<writer>`, `graph:<status>`, `backtest`.
- **Charts, tables and summary:** identical to the live digest (§4.7), including the `season/*` curves. The backtest scorecard is `runs/digest-backtests/<season>/season_scorecard.parquet`.
- **Artifact** `digest-backtest` (type `digest`), alias `<season>-w<NN>-<writer>`, e.g. `2025-w09-openrouter`, `2025-w09-placeholder`. (Versions `v0`–`v3`, from before the writer suffix existed, carry plain `2025-w08` etc.)
- **The graph** is rebuilt as of that Tuesday but **without a W&B run**: the digest run's `graph_status` and `graph_items` are the only W&B trace.
- **Reports** go to `reports/backtests/<season>/week<NN>-digest-<writer>.md`, one per writer, side by side.
- Real runs: `ngxgsudj` (2025 week 9, GLM, with graph sections, final), `p3vwl1f1` / `yf45rwhr` / `ar1v132l` / `egbdqx5m` (the four P04 review digests, superseded).

**Real `season/*` values (2025 backtest scorecard: weeks 3–8 and 13 graded):** `season/cum_brier_model` ends at 0.213 vs Elo 0.230 and market 0.215; `season/cum_pick_accuracy` 0.673; `season/cum_watchlist_hit_rate` 0.35. Week 5 shows what a bad week looks like: Brier 0.320 (5 of 14 picks right), and every predictor was bad that week (Elo 0.358, market 0.306). A digest run only draws the scorecard rows that existed when it ran, so an earlier run of week 14 can show fewer weeks than the file has now.

### 6.5 Graph builds outside the weekly run

- **`nfl graph build --season S --week N`**: a live build, same as the weekly step: `graph-S-wNN`, artifact alias `S-wNN`.
- **`nfl graph build --season S --week N --backtest`**: as of that week's Tuesday, written to `runs/digest-backtests/<S>/week<NN>/graph_results.json`. Name `graph-S-wNN-backtest`, tag `backtest`, artifact alias `S-wNN-backtest`. Same charts and summary as live.
- **`nfl digest --graph build`** (live) rebuilds the graph with a W&B run first. In backtest mode the digest's graph rebuild never logs to W&B.
- **`--no-wandb`** on `nfl graph build` or `nfl digest` skips W&B entirely (local tests).
- **`nfl graph query`** is read-only and never logs.

## 7. Season-long views

### The season scorecard

Each digest grades the previous week and keeps one row per graded week in a parquet file on D: (`runs/<season>/season_scorecard.parquet` live, `runs/digest-backtests/<season>/season_scorecard.parquet` for backtests). The flow:

1. **Week N's digest** reads week N−1's saved `predictions_games.parquet` and `watchlist.parquet` (never re-computed predictions), and grades only predictions made before kickoff (`digest/report_card.py`).
2. The **report card** in the digest gets the week's record, Brier vs Elo, points error, biggest miss and the season to date. If it was `scored`, the same numbers go to the digest run's summary as `rc/*`.
3. The **scorecard row** for week N−1 is upserted (re-running a week replaces its row): `season, week, games, picks_correct, picks_total, brier_model, brier_elo, brier_market, logloss_model, ece_model, points_mae, watchlist_hits, watchlist_total, player_mae_vs_baseline, checks_passed, not_graded`. `checks_passed` is the result of **week N−1's** digest checks, so the row describes one week completely. `player_mae_vs_baseline` stays empty until P06.
4. The **digest run** logs the whole file as the `season_scorecard` table, and turns it into the `season/*` curves (`season_series`): one point per graded week, x = `season/week`. The `cum_*` values are weighted by games. `ece_model` and `logloss_model` are in the table only (no curve).

So **the newest digest run is the season dashboard** until P07 builds a W&B Report. To compare weeks, open that run's `season/*` charts rather than overlaying old digest runs (each older run stops at its own week). The digest's printed report card and W&B always agree because both come from the same row.

### Drift signals (doc 08): what exists today

| Signal (doc 08) | Threshold | Today | Planned |
|---|---|---|---|
| Game model worse than Elo | Rolling 4-week Brier worse for 3 straight weeks | **By eye only.** `season/brier_model` vs `season/brier_elo` (weekly) and the `cum_*` lines in the newest digest run; both columns are in `season_scorecard`. No rolling 4-week number is computed | Automatic check and alert in P07 |
| Calibration drift | Season ECE above 0.05 | **Not really visible.** Only a per-week `ece_model` in the scorecard table, which is very noisy on ~15 games (0.13–0.32 every week in the 2025 backtests). The digest's report card shows season-to-date calibration buckets (in `payload.json`). Note: v0 has no calibration layer to refit (calibration `none`, D48), so the doc's response needs a new answer | Season ECE and the alert in P07; calibration curve in the P07 dashboard |
| Player model worse than baseline | Rolling 4-week MAE worse for a position group for 3 straight weeks | **By eye:** the newest `scoreboard-S-wNN` run's `scoreboard/improvement_*` curves (below 0 = worse than the baseline that week) and its `accuracy_scoreboard` table (P06). No rolling number is computed | Alert in P07 |
| Data freshness | Any source more than 1 week stale | **Not in W&B.** In the digest footer and `payload.json` → `meta.sources` (inside the `digest` artifact), the `ready` step, and `nfl data-status` | Logged to W&B and alerted in P07 |
| Checks | Failure rate above 20% of runs over a month | **By eye:** the runs table filtered to group `weekly-pipeline`, column `checks_passed`; also the scorecard's `checks_passed` column | Pass-rate panel in the P07 dashboard; alert in P07 |

P10 (season operations) then reviews any alert each week, and the agent investigates before Rishi decides. Nothing ever retunes automatically.

## 8. Artifacts summary

| Artifact | Type | Aliases | Files | Written by | Read by |
|---|---|---|---|---|---|
| `game-model` | `model` | `<season>-w<NN>`; `production` (only with `--promote`); `latest` (automatic) | `model_only.joblib`, `market.joblib`, two `*_coefficients.csv`, `meta.json`; description = the model card | `nfl train game`, weekly `game` step | Nobody yet (the digest reads the parquet on D:). The `production` alias is the swap point for P07 and P08 (a `candidate` → `production` promotion between weeks) |
| `graph-results` | `graph` | `<season>-w<NN>` (live), `<season>-w<NN>-backtest`; `latest` | `graph_results.json` | `nfl graph build` (both modes), weekly `graph` step, `nfl digest --graph build` (live). Successful builds only | Nobody yet. Planned: P06 reads Q2 / Q3 rows as player features |
| `digest` | `digest` | `<season>-w<NN>`; `latest` | `payload.json`, `raw_llm_output.json`, `checks.json`, `digest.md` | Live `nfl digest`, weekly `digest` step | Nobody (the record of what shipped) |
| `digest-backtest` | `digest` | `<season>-w<NN>-<writer>`; `latest` | Same four files | `nfl digest --backtest` | Nobody (review record) |
| `player-model` | `model` | `<season>-w<NN>`; `production` (only with `--promote`; on the `kl4fzvl8` version of `2026-w04`); `latest` | One LightGBM booster file per target model (`<key>-q10/q50/q90.txt` for yards, `<key>-mean.txt` for counts), `meta.json`; description = the player model card | `nfl train player`, weekly `player` step | Nobody yet (the digest reads `predictions_players.parquet` on D:) |

**Where things stand (checked through the W&B API):** `game-model` `v0`–`v3` (`production` on `v1` from `ump6sftd`; `2026-w04` on `v3` from `1vbkvjdj`); `graph-results` `v0` (`2026-w04`, `binb1iho`); `digest` `v0`–`v5` (`2026-w04` on `v5`, `o0skjazq`); `digest-backtest` `v0`–`v31`.

**Planned, not built:** a reference artifact for the training-data snapshot (doc 08); `candidate` aliases (P08). The player models are one artifact, `player-model`, not one per group (`player-model-wr` in doc 08).

## 9. Where the build differs from doc 08

| Doc 08 says | As built |
|---|---|
| Group table: `track1-game` job types include `eval`; ratings tuning and trend validation | `track1-game` uses `backtest`, `tune`, `train` (no `eval`). Not in the table: `smoke-test` / `smoke` (the P00 smoke run) |
| `weekly-pipeline` holds production runs, job types `main` and `injury-update` | Only the **digest** runs there (`main`). The weekly fit and graph build go to `track1-game` / `train` and `track1-graph` / `build`. `injury-update` is P07 |
| Tags: `prod`, model family, position group | `prod` is only on live digests. Live fits are tagged `prod-candidate`, live graph builds `live`. Phase tags (`p02`, `p03`, `p05`), `variant:*`, `qb:*`, `llm:*`, `graph:*` exist and aren't listed |
| Config: dataset version = "snapshot date + hash"; a reference artifact for the training data | `dataset_version` is the pbp snapshot date plus the curate time, no hash; no data artifact |
| Plots: ROC / precision-recall, feature importance, SHAP, residuals by week | Game model: none yet (v0 is linear; P08). Player backtests (P06): `feature_importance` and `shap_summary` bars, `mae_by_season`, LightGBM curves |
| Weekly run logs freshness, row counts, readiness, model versions (aliases) and feature hash, step timings, market fallback | Freshness and row counts aren't in W&B (`weekly_run.json`, the raw manifest and the digest footer instead). The digest run doesn't record which `game-model` version or graph run it used. The feature hash is in the fit's config only. Market use is `market_data_used` (digest) and `market_fallback_games` (fit) |
| Digest backtest artifact "aliased `<season>-w<NN>`" | Aliased `<season>-w<NN>-<writer>` |
| "As built in P05": graph build logs counts, timings, queries, insights, tables | True, plus `load/elapsed_s`, `query/max_seconds`, `count_mismatches` and the **`graph-results` artifact**, which doc 08 doesn't mention yet |
| Season scorecard columns | Also `picks_total`, `points_mae` and `not_graded`; `checks_passed` is the previous week's digest's |
| W&B Report "2026 Season Dashboard" | Not built (P07). The newest digest run's `season/*` charts stand in for it |
| `accuracy_scoreboard` table | Built in P06: logged by the backtests (backtest rows) and by each weekly `scoreboard-S-wNN` run (the season file), with `scoreboard/*` curves |
| Drift signals | None automated (P07); calibration's "refit the calibration layer" has no layer to refit in v0 |
| Model registry: `production` points to what the weekly pipeline uses | The weekly pipeline doesn't read any artifact; it refits every week. `production` moves only with `--promote` and is on `game-model:v1` while week 4's digest used `v3`'s predictions. `candidate` isn't used yet |

## 10. Known gaps and quirks

Things that look wrong or confusing in W&B today, with where they come from:

1. **Fixed in P05:** the cumulative season Brier used to divide by every graded game even in a week where that predictor's Brier was missing. Each `season/cum_brier_<x>` now divides by the games its own weeks cover (`digest.run.season_series`, tested). Still true: a week's `brier_market` is computed on the games that have a market line, so in a week with a few missing lines it covers fewer games than the model's.
2. **The `load/*` points don't say which table they are** (`graph/build.py:325-336`). Use the order in §4.6 or the `*_s` timings in `graph_results.json`.
3. **No lineage from the digest to its inputs.** The digest run doesn't call `use_artifact` on `game-model` or `graph-results`, so the W&B lineage graph can't show which model version and graph build a published digest used. `payload.meta.model_versions` says `game-model-v0:2026-w04`, which fits three artifact versions (`v1`–`v3`).
4. **`production` lags behind** unless every live fit is promoted (`models/game_runs.py:813`; `weekly.py` passes `--promote` only when given). Harmless today because nothing reads the alias; P07 has to settle who moves it.
5. **Fixed in P05:** a fail-soft graph build that couldn't run (Neo4j down, count mismatch) now ends its W&B run as **failed** (`exit_code=1`), as well as logging `status: unavailable` and `error`. Runs from before the fix (the Neo4j-down check of 2026-10-04) show as finished.
6. **The week's `graph_results.json` on D: is overwritten by any later rebuild of the same week.** The week-4 file now comes from `binb1iho`, built after the published digest (`8txvvr92` / `o0skjazq`). The artifact versions and the digest's own `payload.json` are the reliable records.
7. **`season/*` means two different things.** Ratings runs use `season` (the year) as the x-axis for `season/mse_*` and `season/r2_*`; digest runs use `season/week` for `season/brier_*` etc. Both land in the same "season" section of the project workspace. Filter by group before reading those charts.
8. **Backtest digests share one scorecard across writers** (`runs/digest-backtests/<season>/season_scorecard.parquet`), so a row's `checks_passed` belongs to whichever writer last ran week N−1.
9. **Phase tags are hard-coded per module** (`digest/run.py:638` says `p05`, `game_runs.py` says `p03`). They tell you which phase's code made the run, not when it ran, and will go stale as later phases change the code.
10. **`train` runs are opened after the predictions and models are written** (`models/game_runs.py:785`). If W&B is unreachable, the step fails although the files on D: are fine; `--from-step game` then refits.
11. **Small doc slip:** the `game_runs.py` module docstring lists `nfl features game` among the W&B runs. It doesn't log to W&B.
12. **Config keys containing "token" are refused** by the secret guard (`tracking.py:18`, `SECRET_KEY_HINTS`), even harmless ones like `max_tokens`.

## 11. Glossary

| Term | Meaning here |
|---|---|
| **Run** | One execution of a command that logs to W&B. Has a unique 8-character id (e.g. `o0skjazq`) and a name that may repeat |
| **Project** | The container for all runs: `nfl-analytics-engine` |
| **Entity** | The account or team that owns the project: `models-ontario-tech-university` |
| **Group** | A label that collects related runs (`track1-game`, `weekly-pipeline`). Filter or group by it in the runs table |
| **Job type** | What kind of work a run did within its group (`backtest`, `train`, `tune`, `eval`, `build`, `main`) |
| **Tag** | A free label on a run (`season:2026`, `prod`, `launched-by:agent`). A run can have many |
| **Config** | The settings a run started with |
| **Summary** | The final value of each key, shown on the Overview and as runs-table columns. For keys logged repeatedly it's the last value |
| **History / step** | Every value logged over time. W&B numbers each `log` call with its own step (`_step`) |
| **`define_metric` step axis** | A rule that says "draw these keys against that key instead of `_step`": `bt/*` against `bt/step`, `slate/*` against `slate/game`, `load/*` against `load/step`, `season/*` against `season/week` (digest) or `season` (ratings) |
| **Panel / workspace** | A chart or table in the website. The workspace is the page of panels; sections group panels by key prefix |
| **Custom chart** | A chart built from a table (`wandb.plot.bar`, `wandb.plot.line_series`): bar charts, the reliability diagram. Website only |
| **Table** | A logged grid of rows (`predictions_games`, `season_scorecard`), viewable, sortable and filterable on the website |
| **Sweep** | A set of runs that W&B launches over a grid of settings, with its own page (parallel coordinates, parameter importance). Ours run in-process, one configuration after another |
| **Artifact** | A versioned bundle of files logged by a run (`game-model`, `graph-results`, `digest`). Each new upload with changed contents is a new version (`v0`, `v1`, …) |
| **Alias** | A movable name for one artifact version (`2026-w04`, `production`, `latest`). Logging a new version with the same alias moves it |
| **Lineage** | The graph of which run logged which artifact and which runs used it. Ours only has the "logged by" half (§10 item 3) |
