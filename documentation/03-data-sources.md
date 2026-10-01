# 03: Data Sources

All primary data is free. nflverse, loaded with `nflreadpy`, is the backbone. ESPN is optional extra context that **fails soft**. The Big Data Bowl data is a one-time historical download for Track 2.

> **The availability rule.** A feature can only be used by a live model if it can be computed **on Tuesday of week N from data through week N−1**. Every source below lists when it becomes available. Anything that isn't available until after the season (for example `load_participation`) can be used for historical research but **never as a live feature**.

## nflverse (via `nflreadpy`)

`nflreadpy` returns Polars DataFrames and has built-in caching. Season ranges below are from the nflreadpy docs (checked 2026-09-30).

| Loader | Seasons | Used for | When available in season |
|---|---|---|---|
| `load_pbp()` | 1999+ | EPA, success rate, win probability, pass/rush splits, scrambles, explosive plays: the core of team ratings | Usually overnight after games; check before each run |
| `load_schedules()` | all | Games, results, **betting lines** (`spread_line`, `total_line`, moneylines, spread odds), `home_rest`/`away_rest`, `div_game`, `roof`, `surface`, `temp`, `wind`, **starting QBs**, **head coaches**, **referee**, stadium | Upcoming games are listed ahead of time; check how far ahead lines are filled in |
| `load_team_stats()` / `load_player_stats()` | recent seasons | Weekly box-score stats, offense **and defense** (targets, carries, yards, TDs; tackles, sacks, QB hits, INTs, passes defended): the actual results used to score player predictions | After games |
| `load_nextgen_stats(stat_type=...)` | 2016+ | **Current-season tracking summaries**: passing (time to throw, aggressiveness, completion % over expected, air yards differential), receiving (**average separation**, **cushion**, **yards after catch above expected**, share of team air yards), rushing (**rush yards over expected**, efficiency, % of runs against 8+ in the box) | Weekly; minimum-attempt thresholds apply; check for a season-to-date (week 0) row |
| `load_pfr_advstats(stat_type=pass/rush/rec/def)` | 2018+ | **Pressures, hurries, QB hits, blitzes** (defense), pressures faced (QB), broken tackles, drops, missed tackles, yards before/after contact | Weekly (week level needs the season given) |
| `load_ftn_charting()` | 2022+ | Play-level charting: play action, motion, RPO, screens, number of pass rushers, blitz, QB out of pocket, catchable ball, contested catch | Weekly, a short lag after games |
| `load_snap_counts()` | 2012+ | Snap share for role and usage trends (offense, defense, special teams) | Weekly |
| `load_injuries()` | 2009+ | Practice participation and game status (Out, Doubtful, Questionable) | Updated through the week; **final designations come Friday** |
| `load_rosters_weekly()` | 2002+ | Who was on which team each week (player–team tenure for the graph) | Weekly |
| `load_depth_charts()` | 2001+ | Depth order, used for injury ripple (who steps in) | Weekly, but can be noisy |
| `load_players()` | n/a | Player IDs across systems (GSIS, PFR, PFF, ESPN), position, birth date | Rolling |
| `load_teams()` | n/a | Team metadata | Static |
| `load_officials()` | 2015+ | Officiating crews per game (graph: crew tendencies) | Weekly |
| `load_trades()`, `load_draft_picks()`, `load_combine()`, `load_contracts()` | various | Player movement and background for the graph | Rarely changes |
| `load_participation()` | 2016+ | Players on the field each play, formations, coverage | **Filled in only after the season ends → historical research only, never a live feature** |

Not used: the `load_ff_*` loaders (fantasy; out of scope by principle).

### Betting-market data

- **Use:** (1) as a feature in the *market-informed* game model, (2) as the benchmark, since market-implied probability is the strongest public baseline, (3) the player model may use team total points implied by the market as context. **Never shown in the digest** as lines or odds.
- **Historical lines** come from `load_schedules()`. They are generally **closing lines**, which reflect information up to kickoff. A Tuesday prediction only sees an earlier line, so models trained on closing lines will look a little better in backtests than they will live. Record this in the model card. Benchmark against closing lines anyway, because they're the hardest bar.
- **Current-week lines** at run time: `load_schedules()` if it's filled in for upcoming games by Tuesday. Otherwise the ESPN scoreboard's odds field. If neither is available, the game model falls back to model-only.
- Check the `spread_line` sign convention (which side is positive) once during ingestion, and write a test for it.

## ESPN public API (unofficial)

Used for:
- news headlines and blurbs for teams and players in the payload
- injury updates between nflverse refreshes
- current-week lines (fallback)
- **ESPN Total QBR** (weekly)
- **ESPN FPI** team ratings, as an extra benchmark and feature
- player game logs and depth charts, to cross-check nflverse

- Unofficial and undocumented. Endpoints can change without notice.
- **Always fails soft.** Missing ESPN data never blocks a digest; the footer notes it.
- Store the raw JSON responses in the dated snapshot so the payload can be reproduced.
- News text goes into the payload as *context*. The number check treats numbers inside news snippets as "sourced from news" and the digest attributes them ("per ESPN").

## Additional free sources

This is a private project, so any free source that improves the models is fair game. Every source here is **optional extra data that fails soft**: if it's down or changes, the run carries on without it. Several are unofficial endpoints, so **confirm what each actually returns while building the ingest** ([P01](plans/P01-data-ingestion.md)) and record the findings in this doc.

| Source | What it adds | Notes |
|---|---|---|
| **NFL Next Gen Stats site** (the JSON endpoints behind nextgenstats.nfl.com) | Whatever goes beyond nflverse's NGS copy: possibly pass-rush and coverage leaderboards, extra tracking-based measures, more detailed weekly cuts | **Top priority:** NGS player stats are central to the project. Map each field to `gsis_id`. |
| **ESPN win rates** (pass rush / run stop / pass block win rate) | Partly fills the missing offensive-line and pass-rush data | Published as leaderboards; collect if reachable, otherwise skip |
| **Pro Football Reference** (careful scraping) | Per-defender coverage stats (targets, completions, yards and TDs allowed, passer rating allowed) and anything nflverse's PFR copy doesn't have | Stay under PFR's rate limit (roughly ≤ 20 requests per minute); cache every page |
| **Open-Meteo** (free API, no key) | **Game-time weather forecasts** (wind, temperature, precipitation) by stadium location | nflverse weather is only filled in after games; the live model needs forecasts. Store forecasts as data available at that time |
| **The Odds API** (free tier, needs a key) | Current lines from multiple sportsbooks (spread, total, moneyline) for the market-informed model | Free tier has a monthly request cap: pull once per main run plus once on Saturday. ESPN odds are the fallback |
| **nflverse extras** (`nflverse-data` releases not wrapped by nflreadpy, if any) | e.g. ESPN QBR mirrors | Check at ingest time |

Rules for all of them:
- Cache raw responses in the dated snapshot.
- Use polite rate limits.
- Map IDs to `gsis_id`, and log the join rate.
- Each source has a feature flag in `settings.yaml`.

## Big Data Bowl 2026 (Kaggle)

Track 2 only. Full detail in [07](07-track2-big-data-bowl.md).

- One-time download with the Kaggle CLI (you must accept the competition rules first). Convert to Parquet partitioned by week, and keep a version tag.
- Frame-level tracking at 10 Hz (x, y, speed, acceleration, direction, orientation) for pass plays from historical seasons.
- Lives under `{NFL_DATA_ROOT}\bdb\` on D:, never in the repo (it's large).
- **The 2027 Big Data Bowl had not been announced as of 2026-09-30.** Check again when it's announced. It may bring newer seasons or a new task.

## What isn't available (and what we use instead)

| Wanted | Why not | What we use instead |
|---|---|---|
| Current-season frame-level tracking | The NFL only releases tracking data through the Big Data Bowl, and it's historical | Weekly Next Gen Stats summaries (`load_nextgen_stats`) |
| Cornerback-vs-receiver coverage matchups | PFF / paid charting, well over the $15 budget | Per-defender coverage stats (PFR); opponent-adjusted pass defense by position and target depth; NGS cushion and separation; FTN charting |
| Individual offensive-lineman pressure allowed | PFF (paid) | Team-level pressure rate allowed (PFR advanced passing); ESPN pass block / pass rush win rates if reachable |
| Live coverage and personnel data | Participation data is only filled in after the season | FTN charting (number of rushers, blitz) for the current season |
| Coordinators (OC/DC) and coaching trees | Not in nflverse | Small hand-curated CSV seed (32 teams × 2 roles × season); optional, see [05](05-knowledge-graph.md) |

No paid dataset is currently worth buying. Revisit only if a specific digest section is clearly blocked by missing data.

## Storage layout

All data lives under the data root on D: (see [02](02-system-architecture.md#storage-data-lives-on-d)):

```
{NFL_DATA_ROOT}\raw\{source}\{dataset}\snapshot={YYYY-MM-DD}\part.parquet
{NFL_DATA_ROOT}\curated\{table}.parquet      # rebuilt from the latest snapshots
{NFL_DATA_ROOT}\runs\{season}\week{NN}\      # features, predictions, payload.json, digest.md, run_log.json
```

- **Snapshot every pull.** nflverse corrects data after the fact (stat corrections, late play-by-play fixes). Snapshots make every run reproducible. The *dataset version* logged to W&B is the snapshot date plus a content hash.
- Prune raw snapshots older than one season if disk space matters, but keep one snapshot per production week.

## Curation and data-quality checks

Run on every ingest. A failure blocks the pipeline unless the check is marked "warn".

| Check | Rule |
|---|---|
| Completeness | Every game in the previous week's schedule shows up in play-by-play with final scores |
| Row counts | Plays per game within normal bounds; players per team-week within normal bounds |
| Team codes | Normalize relocations and aliases (OAK→LV, SD→LAC, STL→LA, and LA vs LAR spellings) to one canonical code per franchise, with a franchise ID for history |
| Player IDs | Join everything on `gsis_id`; log the rate of unmatched IDs per source (warn above 2%) |
| Duplicates | No duplicate (game, play) or (player, game) keys |
| Freshness | The newest week in each source is recorded and logged; a stale source is flagged |
| Lines sign convention | `spread_line` agrees with moneyline favorites for ≥ 95% of games |

## Usage notes

- This is a **private, personal project**; nothing is published. Licensing isn't a design constraint.
- The digest footer still lists data sources (nflverse, NGS, FTN, ESPN, and so on) as provenance, so it's always clear where a number came from.
- Practical rules still apply: polite rate limits, caching, and keeping large data out of git.
