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
| Neutral site | `games.neutral_site` = nflverse `location == "Neutral"` **or** a venue abroad (D99, `features/venues.with_neutral_rule`); home field = 0. `games.location` keeps nflverse's raw label (2026 PHI@JAX at Tottenham is `Home` there, but neutral here) |

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
- **Special teams in `plays`:** `special_teams_play` is 0 on every `field_goal` (1 only on kickoffs, punts, extra points): select by `play_type` in kickoff / punt / field_goal / extra_point. On a kickoff `posteam` is the **receiving** team (`epa` is always posteam's); on punts, field goals and extra points it's the kicking team. Since 2024 a kickoff averages about +0.25 EPA to the receiver and a field goal about +0.2 (2025–26) to the kicker, so center per (season, play type) before comparing teams.
- **`team_ratings.prior_weight`:** 1.0 in week 1, about 0.72–0.85 in week 2, 0.48–0.56 in week 4, 0.41–0.51 in week 5.
- **The knowledge graph mirrors these tables as of a week** (`nfl graph build`). For multi-hop questions (a player's teams, injury ripples, QB-receiver history) see the `neo4j-graph` skill and the run folder's `graph_results.json`.

## Quirks found in P06 (checked live 2026-10-04)
- **Tackles = `def_tackles_solo + def_tackle_assists`** (correlates 0.97–0.99 with PFR's `def_tackles_combined`). `def_tackles_with_assist` is something else (0.80–0.88): don't add it.
- **PFR defense rows exist only for defenders with a stat.** About 28% of defensive player-games with snaps have no `pfr_def` row (mostly rotational DTs at ~30% of snaps): when PFR has rows for that team-game, a missing player row means 0 pressures; a team-game with no PFR rows at all is "not published yet" (null). `pfr_rush` carries only `rushing_broken_tackles` and `pfr_rec` only `receiving_broken_tackles` (the other column is all null).
- **Carry share from play-by-play, not the box score:** `player_games.carries` includes kneels and other plays outside `rush == 1`, so carries / team rushes can exceed 1. Count rush plays per `rusher_player_id` (no two-point tries) instead.
- **`plays.pass_oe` is in percentage points** (divide by 100 for a rate); `xpass` is 0–1.
- **"Probable" existed until 2015:** 14–17% of player-weeks are on the injury report in 2012–2015, 3–6% from 2016. A feature like "listed at all" changes meaning across eras; use Out / Doubtful / Questionable (and practice participation, which is stable).
- **DuckDB hands `games.kickoff_utc` back in the machine's time zone** (America/Toronto here) when read with `.pl()`: convert with `dt.convert_time_zone("UTC")` before comparing with a UTC run time. The parquet read (`pl.read_parquet`) keeps UTC.
- **Player-game oddities:** a few box-score rows have a null `player_id` (about one per week since 2012); a traded player can appear for both teams in one week (2019 weeks 16–17, 2021 week 12: 0 snaps for the old team). `features.player_data.player_history` drops the first and keeps the row with the most snaps.
- **Blitz sources disagree:** FTN `n_blitzers > 0` vs PFR `times_blitzed` correlate only 0.69 per team-game (both are features).
- **NGS receiving separation barely predicts volume** (rank correlation with receiving yards −0.03 for WRs).
- **Player feature table (P06):** `features/player_features.parquet` (+ DuckDB view), one row per (player, game), built by `nfl features player`; every column is as of the row's week (`features/player.py`). Model outputs: `runs/<season>/week<NN>/predictions_players.parquet` (live), `runs/backtests/player/<key>/` (walk-forward 2017–2025), `runs/<season>/accuracy_scoreboard.parquet`; schema in `models/player_schema.py`.

## Quirks found in P07 (checked live 2026-10-04)
- **`location == "Neutral"` isn't "international".** The 2024 wild-card MIN@LA was moved to Glendale by the wildfires (neutral, in the US), and the Super Bowl is always neutral. Decide "abroad" from the venue's time zone in `config/stadiums.yaml`, reading `game_venues` (by `game_id`) before the stadium name, as `ops.calendar` does.
- **Kickoffs:** schedule `gameday` + `gametime` are US Eastern; `games.kickoff_utc` converts them (DST handled). The raw schedule has no `kickoff_utc`: `ops.calendar.with_kickoffs` adds it the same way, so the calendar works before curation.
- **Odd 2024–2026 slates** worth testing against: Christmas 2024 on a Wednesday (week 17 deadline Wed 13:00 ET), 2024 Black Friday LV@KC, 2026 week 1 SF@LA in Melbourne on a Wednesday evening ET, 2025 week 4 Monday double-header, Saturday games in weeks 16–18. A fixture with these is `tests/fixtures/schedules_2024_2026.csv`.
- **Same-day snapshots are overwritten in place**, so "before vs after" comparisons within a day (the Saturday injury update) must read the before state first; the raw snapshot folder only keeps the latest pull of each day.
- **Newest week per source for freshness** (`ops.summary.freshness`): `max(week)` of `plays`, `player_games`, `snaps`, `injuries`, `depth_charts` for the season, plus `digest.under_hood.source_weeks` (NGS, PFR, FTN). PFR and FTN normally trail by a week on a Tuesday; one week behind isn't stale.

## Quirks found in P08 (checked live 2026-10-04)
- **Game weather (`games.temp`, `games.wind`) is missing for half of 2022's outdoor games** (96 of 187 have a reading; 150 of 191 in 2023; ~95% in other seasons). Domes and closed roofs carry a single dummy reading at most: treat roof ≠ outdoors/open as "no weather". Outdoor wind's 90th percentile (2011–2025) is 15 mph; 8% of games have ≥ 15 mph, 5% ≤ 32 °F.
- **`weather_forecasts`** (Open-Meteo) has `temp_f`, `wind_mph`, `gust_mph`, `precip_mm`, `precip_prob`, `forecast_hour_utc`, `pulled_at` for upcoming outdoor games only (domes skipped); DuckDB returns its timestamps in local time.
- **`snaps.position` has combined labels** (`FB/D`, `T/G`, `CB/R`, ...): take the part before `/` (`features.game_extra.position_group`). Snap counts include playoff games and start in 2013.
- **Team injury load from snaps** (`features.game_extra.team_injury_load`, Tuesday view): a team averages ~1.5 "regular-equivalents" missing from its last game across OL / skill / front / secondary (0.49 / 0.30 / 0.32 / 0.44), rising from 1.2 (2013) to 1.9 (2021). A starter benched for good looks the same as an injured one in snap counts.
- **League home margin by season** (non-neutral regular-season games, 3-season trailing mean): 2.4–3.0 points for 2013–2016, 0.57 in 2022, back to 2.2 by 2025.
- **Event label rates** (player-games in the pool): RB any TD (rushing + receiving; return, passing and defensive TDs excluded) 24.5%; WR/TE any TD 20.9% (2012) falling to 14–15% since 2021; QB ≥ 1 passing TD 77–83%; QB ≥ 1 interception 58% (2012) falling to 49% (2025); CB/S interception 9.8% → 6.9%, pass defended ~31%; EDGE/DL sack credit 15.5–18.8%.
- **`def_sacks` has half credits:** 0.5 steps; 15.6% of nonzero EDGE/DL player-games are exactly 0.5 and 20.8% carry a half. "Any credit" (> 0) is 16.6% of rows, "≥ 1 full sack" 13.9%.
- **`pfr_def` coverage:** `def_targets`, `def_completions_allowed`, `def_yards_allowed` (null when he allowed no completion: sum to 0), `def_adot`, `def_ints`; 14 rows without `gsis_id`; 3 rows with completions > targets (errors). A corner with ≥ 50% of snaps has a PFR row 97–99% of the time (safeties 95–97%), so a missing row in a published team-game is 0; team-games with no PFR rows at all: 2023 w12, 2024 w13 and w17, 2025 w13 (and the latest week).
- **`team_games`** includes playoffs (`season_type = 'REG'`). `passing_yards` is gross of sack yards and equals the sum of the players' `passing_yards` in 100% of team-games (play-by-play `yards_gained` differs: scrambles are rushing in the box score, accepted-penalty yards). `sacks_suffered` = sack plays (100%); `def_sacks` matches the opponent's count in 98.5% (half sacks). Takeaways = opponent interceptions + opponent `fumbles_lost_total` (99.5% vs the defense's own `def_interceptions + fumble_recovery_opp`; offense-only fumbles miss special teams). League takeaways per team-game 1.56 (2012) → 1.16 (2025); passing yards 259 (2015) → 225 (2025). Sacks and takeaways are nearly Poisson (variance 1.29× and 1.07× the mean).
- **`games.spread_line` is null for far-future weeks;** read upcoming lines through `features.game.current_lines` / `game_features`.
- **The player feature table (P08)** has CB rows and the `cvg_*` family: ~225k rows × 138 columns; CB/S pool ~3.6–3.9k player-games a season. `player_history` adds `any_td`, `def_int_any`, `pd_any`, `pfr_completions_allowed`.
- **Polars:** `(col == "x").all()` on an all-null column is `True` (vacuous): `fill_null(False)` first.

## Quirks found in P10 (checked live 2026-10-05)
- **Playoff rows by table (2025):** `games` WC 6 / DIV 4 / CON 2 / SB 1 (weeks 19–22); `injuries` and `rosters_weekly` carry `game_type` WC / DIV / CON / SB for those weeks; `pfr_*` and `ftn_plays` use weeks 19–22 with `game_type`; `plays` uses `season_type = 'POST'`, weeks 19–22; **NGS** uses `season_type = 'POST'` with weeks 19, 20, 21 and **23 for the Super Bowl** (nflverse says 22); before 2021 it's 18, 19, 20 and **22** (nflverse 21).
- **The player data layer is regular season** (`features.player_data.load_inputs`): `with_playoff_week(inp, S, W)` adds one playoff week's games (as not completed), injury report and rosters for live rows; `load_inputs(playoffs=True)` adds every playoff game, for **scoring only** (`score_weeks`, the watch-list look-back). Features and training never see playoff rows.
- **ESPN per-week ingest is regular season** (`ingest/runner.py`: weeks ≤ 18, `seasontype=2`); `espn_scoreboard` / `espn_qbr_weekly` have no consumer downstream.
- **Participation for the current season** is "not published yet" until nflverse fills it after the season; with `seasons.current` still on that season, `nfl ingest` picks it up (research only).
- **2026 schedule:** week 18 is on 2027-01-10, so Wild Card weekend is 2027-01-16/18 and the Wild Card Tuesday run is 2027-01-12.

## Quirks found in LD00 (checked live 2026-10-10)
- **Kickoff eras (where a team starts after receiving a kickoff after a score):** touchback at the 20 to 2015, the 25 in 2016-23, the 30 in 2024 (the new kickoff), the 35 from 2025; the receiver's mean start (yards from its own goal) 23.0 / 25.4 / 29.8 / 30.6 (2026 weeks 1-4: 30.5). On a kickoff `posteam` is the receiver; find the next possession with the next snap (a play with a `down`). Onside kicks: `desc` contains "onside" (0.8-1.6% of kickoffs after a score).
- **Extra points:** 99.3% made to 2014, 93-96% from 2015 (the longer kick), 95.9% in 2023-25; two-point tries ~48%, 9-10% of tries since 2018.
- **Overtime in plays:** `qtr == 5`, and `game_seconds_remaining` = `half_seconds_remaining` = the OT clock (max 900 to 2016, 600 from 2017). 2025: no OT game ended on the first possession (both teams possess); 2024: five did.
- **Field goals:** `kick_distance` = `yardline_100` + 18 on 90% of kicks (+19 8%, +17 2%); after a miss the other team's first snap is at 100 - (`yardline_100` + 8) or its 20. 16,499 regular-season tries 2010-2026 (330 blocked).
- **The second-half kickoff** goes to the team that didn't receive the opening kickoff (2,227 / 2,227 games 2018-2025); `home_opening_kickoff == 1` means the home team *received* the opening kickoff (99.5%).
- **Snaps always carry both teams' timeouts** (`posteam_timeouts_remaining` null only on non-snap rows); `penalty_yards` is never null on a defensive-penalty first down (and ≥ the distance on 71% of them).
- **3rd / 4th-down conversion:** "gain ≥ distance or an offensive TD" agrees with `first_down` on 99.5% of 2018-2025 runs and passes. Attempted 4th downs convert more than 3rd downs at the same distance (4th & 5: 53% vs 3rd & 5: 46%): teams choose when to go.
- **Neutral sites:** `plays.location` is nflverse's raw label; use `games.neutral_site` (46 neutral games in 2018-2025).
- **nflfastR's own models:** `xpass` was fit on 2006-2019 and the EP / WP models on seasons through 2019, so `vegas_wp`, `wp`, `xpass` are in-sample baselines before 2020 (an LD00 pass model lost every season 2014-2019 to `xpass` and won every season 2020-2025).
- **`ep`** isn't in the live package's play columns: read it straight from `plays/*.parquet` when needed.

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
