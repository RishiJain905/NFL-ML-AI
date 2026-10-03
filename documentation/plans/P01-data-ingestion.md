# P01: Data Ingestion and Curation

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P00
- **Unlocks:** P02 (and every later phase)
- **Read first:** [03 Data sources](../03-data-sources.md) (all of it), [02 → Storage](../02-system-architecture.md#storage-data-lives-on-d), [04 → Leakage rules](../04-track1-models.md#leakage-rules-apply-to-all-models)

## Goal

One command pulls every data source into **dated Parquet snapshots on D:**, then builds clean **curated tables** with consistent team codes and player IDs, passing quality checks. History covers 2010 to the current week (2018+ for sources that start later).

## Scope

- **In:** all nflverse loaders in [03](../03-data-sources.md); the ESPN client; the extra free sources (NGS site, ESPN win rates, PFR coverage, Open-Meteo, The Odds API) as optional, fail-soft modules; snapshots; curation; quality checks; the data-readiness check.
- **Out:** features and models (P02+). The graph load (P05).

## Tasks

### Ingestion framework
- [x] 🤖 `ingest/base.py`: a common pattern of `fetch() → DataFrame → write_snapshot(source, dataset, df)` to `raw/{source}/{dataset}/snapshot=YYYY-MM-DD/part.parquet`, with a content hash and row count recorded in a `manifest.json`.
- [x] 🤖 Idempotent: a rerun on the same day overwrites that day's snapshot. `--seasons` and `--sources` flags.
- [x] 🤖 A shared HTTP client (`httpx`) with retries, rate limiting per host, and on-disk caching under `cache/http/`.

### nflverse sources (required)
- [x] 🤖 play-by-play (2010+), schedules (all), player stats + team stats (weekly), NGS passing / receiving / rushing (2016+), PFR advanced pass / rush / rec / def (2018+, weekly), FTN charting (2022+), snap counts, injuries, weekly rosters, depth charts, players, teams, officials, trades, draft picks.
- [x] 🤖 Participation (2016+) ingested **for research only**. Write it to a separate `research/` area so no live feature can read it by accident ([D14](../10-decisions-log.md)).

### ESPN + extra free sources (optional, fail-soft)
- [x] 🤖 ESPN: scoreboard (incl. odds for upcoming games), news, injuries, QBR, FPI, team depth charts. Save raw JSON + parsed tables.
- [x] 🤖 **NGS site exploration:** find the JSON endpoints behind nextgenstats.nfl.com. Document in [03](../03-data-sources.md) exactly which fields go **beyond** nflverse's NGS copy. Ingest the useful ones.
- [x] 🤖 ESPN pass rush / run stop / pass block win rates, if reachable. Otherwise record "not available" in 03.
- [x] 🤖 PFR per-defender coverage stats (careful scraping, ≤ 20 requests/min, cached), only for fields nflverse doesn't already have.
- [x] 🤖 Open-Meteo forecasts for upcoming games (stadium coordinates table; skip domes). Stored as data available at that time.
- [x] 🤖 The Odds API (if `ODDS_API_KEY` is set): current lines for the coming week. Respect the free-tier quota.
- [x] 🤖 Every optional source sits behind a feature flag and **fails soft**: errors are logged, the run continues, and the manifest records `status: skipped/failed`.

### Curation
- [x] 🤖 Team code normalization (OAK→LV, SD→LAC, STL→LA, LA/LAR spellings) + a `franchise_id` mapping table.
- [x] 🤖 Player ID crosswalk: every source mapped to `gsis_id` (using `load_players` IDs plus name/team/position fuzzy matching for ESPN, NGS and PFR scrapes). Log join rates.
- [x] 🤖 Curated tables in `curated/`, plus a DuckDB file with views: `games`, `plays`, `player_games`, `team_games`, `ngs_weekly`, `pfr_adv_weekly`, `ftn_plays`, `snaps`, `injuries`, `rosters_weekly`, `depth_charts`, `lines` (historical + current), `weather`, `officials`.
- [x] 🤖 A canonical `lines` table: historical closing lines (schedules) + current lines (Odds API → ESPN → schedules, in that order), with `source` and `pulled_at` columns.

### Quality checks and readiness
- [x] 🤖 Every check in [03 → Data-quality checks](../03-data-sources.md#curation-and-data-quality-checks), as code, with block/warn levels, results saved per run.
- [x] 🤖 **Lines sign-convention test** (spread vs moneyline favorite agreement ≥ 95%).
- [x] 🤖 `nfl ingest --check-ready --season 2026 --week N`: are all of week N's games final and in play-by-play?
- [x] 🤖 A `nfl data-status` command: newest week per source, snapshot dates, row counts, join rates.
- [x] 🤖 Tests: normalization, ID crosswalk, the quality-check logic (with small fixtures; no network in unit tests).

### Wrap-up
- [x] 🤖 Update [03](../03-data-sources.md) with findings: what the NGS site and the other extra sources actually returned, and which nflverse fields are filled in for upcoming games (e.g. lines on Tuesday).
- [x] ✋ **Checkpoint:** show Rishi the `nfl data-status` output, the disk use on D:, and any sources that didn't work out. Get approval to close P01. *(Approved by Rishi 2026-10-03.)*

### As built: deviations from the task list (all logged in the decisions log)

- **Snapshots:** completed seasons are pulled once, the current season every run (D32).
- **Franchise mapping:** the canonical team code *is* the franchise ID. The `team_aliases` table holds the mapping.
- **ID crosswalk:** no fuzzy name matching needed. NGS carries `gsis_id`, PFR tables join on `pfr_id`, ESPN on `espn_id` (join rates 99.9–100%).
- **Curated table names:**
  - `ngs_passing` / `ngs_receiving` / `ngs_rushing`, not one `ngs_weekly`
  - `pfr_pass` / `pfr_rush` / `pfr_rec` / `pfr_def`, not `pfr_adv_weekly`
  - `weather_forecasts`, not `weather`
- **Lines:** nflverse schedules first, ESPN as a second source; The Odds API is off until a key exists (D35).
- **PFR scraping:** not needed (D33).
- **ESPN depth charts:** not ingested; nflverse already carries them.
- **Win rates:** not available (D34).
- **Extra tables:** `espn_scoreboard`, `espn_news` (fantasy flagged, D37), `espn_injuries`, `espn_qbr`, `espn_fpi`, `ngs_leaders`, `players`, `player_ids`, `trades`, `draft_picks`, `teams`, `team_aliases`.

## Rishi-in-the-loop moments

Ingestion is 🤖 by agreement. There's one optional look at the end: open a curated table in DuckDB or a notebook, for example the 2026 NGS receiving data, to see what's available for "Under the hood".

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| One command refreshes everything | `uv run nfl ingest --season 2026` finishes; the manifest lists every source with ok/skipped status |
| History loaded | `nfl data-status` shows play-by-play 2010–2026, NGS 2016+, PFR 2018+, FTN 2022+ |
| Curated and joined | Player-ID join rate ≥ 98% for nflverse sources; extra-source join rates logged |
| Quality checks pass | Every check passes for 2018–2026 (warn-level items reviewed) |
| Readiness check works | Returns ready for a completed 2026 week and not-ready for a future week |
| Tests | `uv run pytest` passes |

## Handoff to P02

- Curated play-by-play, games and lines tables, with a stable schema and DuckDB views.
- `paths.curated(...)` helpers and a `load_curated(name)` function.
- A known list of which sources are live and which are optional.

## Pitfalls / notes

- `load_pfr_advstats(summary_level="week")` needs explicit seasons.
- `load_participation` is filled in only after the season ends. Never use it in live features.
- Unofficial endpoints change. Keep parsers defensive, and save the raw response so you can re-parse without re-fetching.
- Use Polars lazily for large play-by-play reads. Keep memory in mind on a full 2010+ load.
