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
| `depth_charts` | `season`, `week`, `team`, `position`, `depth_rank` | 2010–2026 | Unified: `source_format` `weekly` (≤2024) or `daily_snapshot` (2025+, latest snapshot on or before game day, games within 10 days only). Position labels differ between the formats; check them before filtering |
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

## Availability and leakage reminders
- For a prediction of week N, use only rows from games **strictly before** week N's games (see `documentation/04` → Leakage rules).
- Historical `lines` are mostly **closing** lines, so backtests look a bit better than live use. Live runs use the current snapshot.
- PFR and FTN for the latest week may be missing on Tuesday. Features must handle a missing final week (fall back to earlier weeks).
- `injuries` for the current week fill in during the week. Use the snapshot the run actually had.
- `weather_forecasts` are forecasts. Historical `games.temp` / `games.wind` are actual observations.

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
