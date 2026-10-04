---
name: curated-data
description: Data dictionary and query recipes for this project's curated NFL data (DuckDB views + Parquet on D:). Use whenever reading, joining, aggregating or building features from games, plays, player/team stats, NGS, PFR, FTN, snaps, injuries, rosters, depth charts, lines, weather or ESPN tables, so you don't have to re-explore schemas or trip over known quirks (sign conventions, team codes, week numbering, IDs, data lag).
---

# Curated data: dictionary + recipes

The curated data lives under `{NFL_DATA_ROOT}/curated` (`D:/nfl-ml-data/curated`):
- `nfl.duckdb`: views over everything
- one `{table}.parquet` per table
- `plays/season=YYYY.parquet` for play-by-play

It's rebuilt by `uv run nfl curate` from the raw snapshots (`uv run nfl ingest`). Source details and the reasons behind each choice are in `documentation/03-data-sources.md` (the "Findings from P01" section) and decisions D32–D37.

**Always open DuckDB read-only** (`read_only=True`), so a concurrent `nfl curate` isn't blocked and nothing is written by accident.

## Keys and conventions (read these first)

| Concept | Convention |
|---|---|
| Game | `game_id` (nflverse, e.g. `2026_05_TB_DAL`). **Never parse teams from `game_id`**: it keeps historical codes (`2015_01_CIN_OAK` has `home_team = LV`) |
| Play | (`game_id`, `play_id`) is unique |
| Player | `gsis_id` everywhere. `player_games.player_id` **is** the gsis_id. NGS uses `player_gsis_id`. PFR, snaps and ESPN tables got a `gsis_id` column in curation (join rates 99.9–100%) |
| Team | One canonical code per franchise (32): `ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LA LAC LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS`. Relocations follow the franchise (OAK→LV, SD→LAC, STL→LA). Rams = `LA`. Map anything new via `nflengine.curate.teams.normalize_team` / `team_expr` |
| Season / week | Integers. **Regular season = weeks 1–17 before 2021, 1–18 from 2021.** Playoffs follow (WC/DIV/CON/SB), so the playoff week numbers shift by era. Filter with `game_type == 'REG'` (games) or `season_type == 'REG'` (plays, player_games), never with `week <= 18` |
| Spread | `home_spread` / `spread_line` **> 0 means the home team is favored** (nflverse convention). ESPN's own sign is the opposite and is flipped in `lines` |
| Result | `games.result` = home score − away score; `total` = combined points; `completed` = result is not null |
| Kickoff | `games.kickoff_utc` (UTC). `gameday`/`gametime` are US Eastern |
| Neutral site | `games.neutral_site` (international games; home field = 0) |

## Tables

| Table | Grain / key | Seasons | Notes |
|---|---|---|---|
| `games` | `game_id` | 1999–2026 | Schedules + results + closing lines + weather + starting QBs + coaches + referee + stadium. Future games already have lines for about 9 weeks ahead |
| `lines` | `game_id` × `source` (× `provider`) | — | `source` ∈ `nflverse_schedules` (historical, mostly closing: `is_closing`; upcoming about 9 weeks ahead), `espn` (current, provider DraftKings), `odds_api` (current, one row per bookmaker in `provider`, about the next 1–2 weeks, 9 books). Columns `home_spread` (+ = home favored for **all** sources), `total`, moneylines, `snapshot_date` |
| `plays` | `game_id`, `play_id` | 2010–2026 | Full nflverse play-by-play (372 cols): `epa`, `success`, `wp`, `vegas_wp`, `pass`, `rush`, `qb_dropback`, `xpass`, `pass_oe`, `cpoe`, `passer/receiver/rusher_player_id`, `posteam`/`defteam`, `down`, `ydstogo`, `yardline_100` ... |
| `player_games` | `player_id`, `game_id` | 2010–2026 | Weekly box score, offense **and** defense (`def_tackles_*`, `def_sacks`, `def_qb_hits`, `def_interceptions`, `def_pass_defended`), plus `target_share`, `air_yards_share`, `wopr`, EPA. `position_group` ∈ QB RB WR TE OL DL LB DB SPEC |
| `team_games` | `team`, `game_id` | 2010–2026 | Team weekly box score |
| `ngs_passing` / `ngs_receiving` / `ngs_rushing` | `player_gsis_id`, `season`, `week` | 2016–2026 | Next Gen Stats. **`week = 0` rows are season totals** (`is_season_total`); exclude them for weekly work. Minimum-volume thresholds apply. Receiving: `avg_separation`, `avg_cushion`, `avg_yac_above_expectation`. Rushing: `rush_yards_over_expected(_per_att)`. Passing: `avg_time_to_throw`, `aggressiveness`, `completion_percentage_above_expectation` |
| `pfr_pass` / `pfr_rush` / `pfr_rec` / `pfr_def` | `gsis_id`, `game_id` | 2018–2026 | PFR advanced. `pfr_def` has pressures, hurries, QB hits, blitzes, missed tackles **and coverage** (targets, completions, yards, TDs, passer rating allowed). **About 1 week behind** in season |
| `ftn_plays` | `nflverse_game_id`, `nflverse_play_id` | 2022–2026 | Charting: play action, motion, RPO, screen, blitzers, pass rushers, box count, catchable/contested, drops. **About 1 week behind** |
| `snaps` | `gsis_id`, `game_id` | 2013–2026 | Offense, defense and special-teams snaps and % |
| `injuries` | `gsis_id`, `season`, `week` | 2010–2026 | Weekly report: `report_status` (Out/Doubtful/Questionable/Probable; null = listed for practice only), `practice_status`. Final designations come on **Friday** |
| `rosters_weekly` | `gsis_id`, `season`, `week` | 2010–2026 | Who was on which team each week; all the cross-platform IDs |
| `depth_charts` | `season`, `week`, `team`, `position`, `depth_rank` | 2010–2026 | Unified: `source_format` `weekly` (≤2024) or `daily_snapshot` (2025+, latest snapshot on or before game day, games within 10 days only). `snap_date` = when a daily chart was published (null for the weekly feed). **A game-day snapshot is after Tuesday:** check `snap_date` before using a chart in a Tuesday feature. Position labels differ between the formats; check them before filtering |
| `officials` | `game_id`, `official_id` | 2015–2026 | Crew by position |
| `players` / `player_ids` | `gsis_id` | — | Bio, position, draft; `player_ids` = the ID crosswalk |
| `teams` / `team_aliases` | `team` / `alias` | — | Canonical team metadata; alias → canonical map |
| `trades` / `draft_picks` | — | 2002+ / 1980+ | Team codes normalized (`gave`, `received`, `team`) |
| `espn_scoreboard` | `espn_event_id` (+ `game_id`) | current season | Status, scores, odds, weather per week |
| `espn_injuries` | `athlete_espn_id` (+ `gsis_id`, `team`) | current snapshot | Latest ESPN injury list with return dates and comments |
| `espn_qbr` | `espn_id` (+ `gsis_id`), `week` | current season | Weekly Total QBR parts (`general__*`) |
| `espn_fpi` | `team` | current snapshot | FPI + offense/defense/ST EPA + projections (`fpi__*`, `projections__*`, `efficiencies__*`) |
| `espn_news` | `article_id` | latest | **Use `WHERE NOT is_fantasy`**: fantasy content is out of scope |
| `ngs_leaders` | `board`, `rank` | current season | Fastest ball carrier / tackle chase distance / time to sack, play-level |
| `weather_forecasts` | `game_id` | upcoming | Open-Meteo forecast at kickoff, with `pulled_at` (data available at that time). Domes skipped |

Research only (never live features): `{NFL_DATA_ROOT}/research/nflverse/participation`, 2016–2025 (routes, coverage, pressure).

**Feature tables (P02, in `{NFL_DATA_ROOT}/features/`, also DuckDB views; rebuilt by `nfl ratings build`).** Key = (`season`, `week`, `team`). **`week` is the as-of week:** the row is built only from games in weeks before `week`, so a week-w game joins the week-w row directly (D44). Rows run from week 1 to the last week with games (for the current season, up to its first unfinished week).

| Table | Content |
|---|---|
| `team_ratings` | Opponent-adjusted ratings relative to league average: `off_/def_/net_` × `epa`, `pass_epa`, `rush_epa`, `sr`, `pass_sr`, `rush_sr`. `def_*` = allowed, so **lower is better**; `net = off − def`. Also `mu_epa` / `hfa_epa` (intercept, home field), `off_plays`, `def_plays`, `plays_observed`, `prior_weight` (≈ share of the preseason prior), `qb_change_prior` |
| `team_elo` | `elo` before week w's games, `elo_games` played this season |
| `team_trends` | `net_epa`, `net_epa_prev` (3 weeks earlier), `trend_delta` (week ≥ 4), `perf_vs_expected` + `pve_games`, `band_low`/`band_high`, `direction` (up/down/stable; null for 2010–2012), plus evidence: `qb_change`, `qb_now_*`, `qb_before_*`, `key_players_out(_names)`, `off_cpoe_delta`, `off_proe_delta`, `off/def_sack_rate_delta`, `off/def_pressure_rate_delta` (PFR, 2018+), `off_time_to_throw_delta` (NGS, 2016+) |
| `team_trend_drivers` | Top 3 rating parts that moved over 3 weeks: `part`, `delta`, `change` (+ = better), `contribution`, `effect` |
| `game_features` (P03) | **One row per game** (key `game_id`), as of the game's week, 2011+. Rebuilt by `nfl features game`.<br>• Targets (null until played): `margin` (= home − away), `total`, `home_win` (1/0/0.5).<br>• Per side `home_*` / `away_*`: ratings, `elo`, `pf_avg` / `pa_avg` (last 8 games), `qb_id`, `qb_name`, `qb_source`, `qb_value`, `qb_baseline`, `qb_adj`, `qb_changed`, `travel_km`, `tz_shift`.<br>• Matchup: `net_diff`, `pass_matchup`, `rush_matchup`, `elo_diff` (per 100), `hfa`, `rest_diff`, `bye_diff`, `short_diff`, `div_game`, `travel_diff`, `tz_diff`, `qb_adj_diff`.<br>• Totals: `off_sum`, `def_sum`, `qb_adj_sum`, `league_ppg`, `pts_base_total`, `roof_dome`.<br>• Market and baselines: `spread_line`, `total_line`, `market_source`, `mkt_home_points`, `mkt_away_points`, `elo_prob`, `elo_margin`, `market_ml_prob` |

**Model outputs (P03).**
- `runs/<season>/week<NN>/predictions_games.parquet` (from `nfl train game`): one row per game × `variant` (`model_only` / `market`). `is_primary` marks the digest row. Schema in `documentation/model_cards/game-model-v0.md`.
- `runs/backtests/game/<variant>/predictions_games.parquet`: walk-forward predictions for every week from 2013 on (2018+ reported), with outcomes and the Elo / market / home baseline probabilities on the same rows. Use it to test P04's report card on past weeks.

## Availability and leakage reminders
- For a prediction of week N, use only rows from games **strictly before** week N's games (see `documentation/04` → Leakage rules). Use `nflengine.features.asof` (`before_expr`, `as_of`). It also hides the current season's week-0 NGS season totals.
- **Backtests must see what a Tuesday run saw (D44).** PFR for week N−1 isn't out by Tuesday of week N, so evidence code lags it one week for every key. Apply the same rule to any new late source.
- Historical `lines` are mostly **closing** lines, so backtests look a bit better than live use. Live runs use the current snapshot.
- PFR and FTN for the latest week may be missing on Tuesday. Features must handle a missing final week (fall back to earlier weeks).
- `injuries` for the current week fill in during the week. Use the snapshot the run actually had.
- `weather_forecasts` are forecasts. Historical `games.temp` / `games.wind` are actual observations.

## Quirks found in P02 (checked live 2026-10-03)
- **Play types.**
  - Scrambles are `play_type = 'run'` with `qb_dropback = 1`, `pass = 1`, `rush = 0`. Sacks are `play_type = 'pass'`.
  - Two-point tries carry `pass`/`run` play types, and `qb_dropback = 1` for passes. Filter them with `two_point_attempt`.
  - `no_play` rows (penalties) still carry `pass = 1` / `pass_oe`. Drop them by `play_type`.
  - `cpoe` exists only on `play_type = 'pass'`.
- **QB columns in `games`.** `home_qb_id` / `away_qb_id` are filled for some **unplayed** games (projected starters). Filter on `completed` before using them as "the starter".
- **The cancelled 2022 BUF–CIN game isn't in `games` at all** (2022 week 17 has 15 games).
- **NGS week numbers.** NGS numbers the Super Bowl one week later than `games` (2016–2020: 22 vs 21; 2021+: 23 vs 22) because of the Pro Bowl gap. Wild card, divisional and conference weeks line up.
- **PFR.** `pfr_pass` has no dropbacks column (use plays). `pfr_def.def_pressures` summed per team correlates 0.975 with the opponent's `pfr_pass.times_pressured`.
- **Injuries.** `injuries.report_status` also has a rare `Note` value. A player's position label can change between weeks. A few player-weeks appear for two teams (mid-week trades).
- **Snaps.** `snaps.offense_pct` / `defense_pct` are on a 0–1 scale in every season.
- **Week-1 depth chart.** The week-1 depth-chart QB1 matches the actual week-1 starter in ~98% of team-seasons (2010–2026).
- **Home field in EPA terms is small and noisy.** The per-season home EPA/play edge swings from −0.045 to +0.036.

## Quirks found in P03 (checked live 2026-10-03)
- **Projected starters.** `games.home_qb_id` / `away_qb_id` on **unplayed** games are nflverse's projected starters. All 2026 week-4 and week-5 games have them, the same games that have lines. Only the latest snapshot exists, so history has no projections. For a played game they name the listed starter, which is post-Tuesday information. Never use them as a feature for a backtest week (`features/qb.py` handles this).
- **Who actually played QB.** Group dropback plays by `passer_id` (it includes scrambles and sacks) with `qb_epa`. The QB with most dropbacks can differ from the listed starter when the starter leaves early (2023 NYJ week 1: Rodgers listed, Wilson played).
- **Venues.**
  - **Every 2025 international game is mislabeled**: `stadium` and `stadium_id` name the home team's stadium. `config/stadiums.yaml` → `game_venues` corrects them by `game_id`.
  - The 2026 ones are right by name (`stadium_id` can still be the home team's).
  - Resolve venues with `features.venues.game_travel`, never with `stadium_id` alone.
- **Matchup arithmetic with the ratings:** `def_*` is EPA allowed (higher = worse defense), so the expected offense EPA of A against B is `off_A + def_B` (+ home field), and A's edge over B is `(off_A + def_B) - (off_B + def_A)`. Never subtract the opponent's defense.
- **Rest days:** 7 is normal; 4 = Thursday after Sunday; 10 = mini-bye after Thursday; 13–15 = bye. Week 1 is always 7.
- **Lines:** closing spread, total and moneylines exist for every 2010–2025 game, except one 2017 game without moneylines.

## Quirks found in P04 (checked live 2026-10-03)
- **Weekly NGS minimum volumes are built in.** Weekly rows only exist for receivers with 5+ targets, rushers with 10+ carries and passers with 20+ attempts, so the pool is already filtered.
- **Publication timing.** On Saturday 2026-10-03, NGS, PFR and FTN all had week 3 (and NGS / snaps / box scores already had Thursday's week-4 game). Whether PFR and FTN are out by a Tuesday run is still unverified (P07 should log it, D52).
- **`pfr_pass.times_pressured_pct` is a 0–1 fraction** in every season; the rare value above 1 is a data error.
- **`rosters_weekly.status == 'RES'`** marks reserve lists (IR, PUP, NFI). Use it with `injuries.report_status` (Out / Doubtful) to drop unavailable players for a week.
- **Box scores vs snaps.** A player can have snaps and no `player_games` row (a blocking TE) or the reverse. Merge both with a full join on (`gsis_id`/`player_id`, `game_id`) before deciding who played.
- **Skewed stats.** Yardage is right-skewed, so a player beats his own recent **mean** only about 43% of the time (all eligible WR/TE/RB, 2024–2025). Compare any "beat the baseline" hit rate against that base rate, not 50%.
- **`injuries.report_status`** is only Out / Doubtful / Questionable / Note / null; **IR isn't in it** (use `rosters_weekly.status == 'RES'`). In 2024–2025, 0 of 677 Out, 0 of 94 Doubtful and 0 of 1,500 RES skill players played that week. `rosters_weekly.status == 'INA'` is the **game-day** inactive list (later than Friday's report): never use it as a pre-game input.
- **NGS receiving has no RB rows since 2020** (WR and TE only). The weekly passing floor is 15 attempts (P04 uses 20+).
- **`players.position` labels many edge rushers `LB`** (e.g. Myles Garrett); use `position_group` (DL / LB) for pass-rusher pools.
- **FTN joins to 100% of REG dropbacks** on (`nflverse_game_id`, `nflverse_play_id`) = (`game_id`, `play_id`); cast `play_id` first (f64 in plays, i32 in FTN). 2025: blitz on 29% of dropbacks, play action on 23%.
- **`snaps`** has a few rows without `gsis_id` and a few duplicate (player, game) rows since 2024: drop / dedupe. `player_games.target_share` is 0–1 and never null.
- **`espn_news`** is mostly fantasy content (`is_fantasy`). Team ids are ESPN's (`team_espn_ids`); map them with `espn_injuries` (`team_espn_id` → `team`).

## Quirks found in P05 (checked live 2026-10-03)
- **`officials.game_id` is the old GSIS id** (`2015091000`), not the nflverse `game_id`: join on `games.old_game_id` (16,154 of 16,182 rows since 2018 match).
- **`injuries.date_modified` is null for every 2025-2026 row** (2024 and earlier have timestamps). A 2025+ report can't be time-scoped by when it changed: a live run uses the snapshot it has, a backtest leaves week N's report out (D62).
- **`trades` rows for traded draft picks carry the eventual draftee's `pfr_id`** (`pick_season` set; 2,737 of 3,943 rows with a `pfr_id`): the 2019 pick SF got from DEN "is" Dre Greenlaw. For player trades, filter `pick_season IS NULL`. `gave` = old team, `received` = new team.
- **Depth-chart slot formats:** to 2024, three rank-1 `WR` rows (one per slot) and two `CB` slots; 2025+, one ranked list per position (`WR` 1..8). "Rank + 1" is the backup only at single-slot positions (QB, RB, TE, C, LT ...). The 2026 charts are daily snapshots with `snap_date` (one per team-week).
- **PFR ids:** `players.pfr_id` maps them to `gsis_id` (22.7k of 24.8k players have one); `draft_picks.gsis_id` is set for almost every pick since 2010.
- **`rosters_weekly.status`:** ACT, DEV (practice squad), RES (reserve / IR), INA (game-day inactive), CUT, RET, PUP, RSN, SUS, NWT, UFA, EXE, RSR, TRC, TRD, TRT, RFA, E01, E14. "On the team" = anything but CUT, RET, UFA, RFA, TRD, TRC, TRT. `game_type` is REG / WC / DIV / CON / SB.
- **The knowledge graph mirrors these tables as of a week** (`nfl graph build`). For multi-hop questions (a player's teams, injury ripples, QB-receiver history) see the `neo4j-graph` skill and the run folder's `graph_results.json`.

## Recipes

**Open the data:**
```python
import duckdb, polars as pl
from nflengine.paths import ensure_data_root

cur = ensure_data_root().curated
con = duckdb.connect(str(cur / "nfl.duckdb"), read_only=True)
df = con.sql("SELECT * FROM games WHERE season = 2026 AND game_type = 'REG'").pl()
plays = pl.scan_parquet((cur / "plays" / "*.parquet").as_posix())  # lazy; filter before collect
```

**Team EPA per play by game (offense), regular season, no garbage-time filter:**
```sql
SELECT season, week, game_id, posteam AS team,
       avg(epa) AS epa_per_play, avg(success) AS success_rate, count(*) AS plays
FROM plays
WHERE season_type = 'REG' AND (pass = 1 OR rush = 1) AND epa IS NOT NULL
GROUP BY ALL
```

**Current line per game** (nflverse, then the Odds API median across books, then ESPN; D40), plus how much the books disagree:
```sql
SELECT game_id,
       coalesce(max(home_spread) FILTER (WHERE source = 'nflverse_schedules'),
                median(home_spread) FILTER (WHERE source = 'odds_api'),
                max(home_spread) FILTER (WHERE source = 'espn')) AS home_spread,
       max(home_spread) FILTER (WHERE source = 'odds_api')
         - min(home_spread) FILTER (WHERE source = 'odds_api') AS book_spread_range
FROM lines GROUP BY game_id
```

**Player week with snaps and NGS** (weekly NGS only):
```sql
SELECT pg.season, pg.week, pg.player_id, pg.player_display_name, pg.team, pg.targets,
       pg.receiving_yards, s.offense_pct, n.avg_separation, n.avg_cushion
FROM player_games pg
LEFT JOIN snaps s ON s.gsis_id = pg.player_id AND s.game_id = pg.game_id
LEFT JOIN ngs_receiving n ON n.player_gsis_id = pg.player_id AND n.season = pg.season
                          AND n.week = pg.week AND NOT n.is_season_total
WHERE pg.season_type = 'REG'
```

**Starters at a position for a team-week** (an empty result can just mean a bye week; check `games`):
```sql
SELECT * FROM depth_charts
WHERE season = 2026 AND week = 4 AND team = 'KC' AND position = 'QB'
ORDER BY depth_rank
```
Depth-chart position labels: the 2025+ format uses clean ESPN slots (`QB RB WR TE LT LG C RG RT LDE LDT RDT RDE NT WLB MLB SLB LILB RILB LCB RCB NB FS SS PK P LS H KR PR FB`). The ≤2024 format is noisier (`CB`, `DE`, `DT`, `OLB`, `ILB`, `EDGE`, `HB`, plus a few junk values). Whitespace is stripped in curation. Map to position groups before comparing across eras.

**Which weeks are complete / next week to predict:** `nflengine.schedule.completed_weeks(games, season)` and `next_week(...)`. **Is week N ready?** `uv run nfl ingest --check-ready --week N`.

## Refreshing and checking the data
- `uv run nfl ingest` (all sources; completed seasons are only pulled once, D32) → `uv run nfl curate` (rebuild + quality checks) → `uv run nfl data-status`.
- Quality results: `curated/_quality/latest.json`; join rates: `curated/_joins.json`.

## Improving this skill
When you find a new quirk, a column meaning, or a recipe you had to work out yourself, add it here in the same commit (and note it in the `PROGRESS.md` session log). Keep it factual and verified against the real data. Schema changes from new sources also belong in `documentation/03`.
