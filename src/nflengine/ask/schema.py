"""The schemas the generation prompt shows (AE00), filtered to what the question points at.

The full schema of both sources is long (plays alone has 372 columns), and schema filtering
helps small models most (AE00 pitfall). So:
- **SQL:** a fixed core (games, player_games, teams) plus the tables the question's words
  point to (`TABLE_HINTS`); each table's note says what a row is and its quirks (from the
  `curated-data` skill); wide tables show their key columns plus the columns that share a word
  with the question.
- **Graph:** the weekly graph's labels and relationships with their properties and what they
  mean (from the `neo4j-graph` skill), always whole (it's short), plus the as-of rules.
- **History** (AE02): the history graph's play-level schema (`history_graph/schema.py`) with
  its conventions (roles only on plays that count, participation and FTN coverage, the
  situations), always whole, plus what the build covers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# ---- SQL ------------------------------------------------------------------------------------

SQL_RULES = """\
DuckDB SQL over the curated NFL tables (read-only; one SELECT; no files or system functions).
Conventions:
- Teams are canonical codes: ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LA
  (Rams) LAC LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS. Relocated teams use today's
  code (OAK -> LV, SD -> LAC, STL -> LA). Never parse teams out of game_id.
- Season scope: count every game, playoffs included, unless the question says "regular season"
  or "playoffs". Regular season: games.game_type = 'REG'; plays / player_games / team_games
  season_type = 'REG'. Playoffs: games.game_type IN ('WC', 'DIV', 'CON', 'SB') (games has no
  'POST'); plays / player_games / team_games season_type = 'POST'. Never filter the regular season
  with week <= 18 (17 weeks before 2021).
- Coverage: games 1999+, plays / player_games / team_games 2010+, NGS 2016+, PFR 2018+,
  FTN charting 2022+. The current season is in progress.
- games.result = home score - away score; spread_line > 0 means the HOME team is favoured.
- A player is identified by player_id (= gsis_id elsewhere). Return full names:
  player_games.player_display_name, or players.display_name joined on gsis_id. The plays
  columns passer_player_name / receiver_player_name / rusher_player_name are abbreviated
  ("B.Thomas"): never return them; join players ON players.gsis_id = plays.<role>_player_id.
- A game: return its game_id with away_team and home_team.
- Scrambles are play_type 'run' with qb_dropback = 1; sacks are play_type 'pass'; drop no_play
  rows and two-point tries (two_point_attempt = 1) for per-play counts and rates.
- Answer the question in its own shape: one row for a single answer ("who led", "the most",
  "the coldest": ORDER BY the measure with a tie-break, LIMIT 1); one row per player / team /
  coach / game for "which ..." (not a row per game or season unless it asks for those); one
  league-wide row unless the question names a team or asks which team; the top N for "top N".
- Return named columns: the names and team codes the answer needs and the numbers asked for
  (counts as counts, rates as 0-1 shares rounded to 3 decimals); label rows with the question's
  own words (home / road); ORDER BY what the question ranks by, with a tie-break."""

TABLE_NOTES: dict[str, str] = {
    "games": (
        "one row per game, 1999+ (schedule, scores, closing lines, QBs, coaches, roof, weather, "
        "kickoff_utc); home_coach / away_coach carry the project's corrections (2025 interims, "
        "2026 hires), as the graph's do"
    ),
    "plays": (
        "one row per play, 2010+ (nflverse play-by-play: epa, success, wp, pass/rush, down, "
        "distance, players)"
    ),
    "player_games": (
        "one row per player per game, 2010+ (box score: passing, rushing, receiving, defense, "
        "kicking)"
    ),
    "team_games": "one row per team per game, 2010+ (team box score)",
    "teams": "one row per franchise (team code, name, nickname, conference, division)",
    "players": (
        "one row per player (display_name, position, college, draft, rookie_season, last_season)"
    ),
    "ftn_plays": (
        "FTN charting per play, 2022+: join plays ON nflverse_game_id = game_id AND "
        "nflverse_play_id = CAST(play_id AS INT); play action, motion, RPO, screen, blitzers, "
        "box. Placeholder rows: qb_location '0' and n_defense_box 0 mean not charted"
    ),
    "injuries": (
        "weekly injury report per player (report_status Out / Doubtful / Questionable; "
        "practice_status)"
    ),
    "depth_charts": (
        "depth chart per team-week-position (depth_rank 1 = starter); slot labels differ before /"
        " after 2025"
    ),
    "snaps": (
        "snap counts per player per game, 2013+ (offense_snaps, offense_pct 0-1, defense, special"
        " teams)"
    ),
    "rosters_weekly": (
        "who was on which team each week (status ACT, RES = reserve/IR, INA = game-day inactive "
        "...)"
    ),
    "draft_picks": (
        "every draft pick 1980+ (season, round, pick, team, position, college, career stats)"
    ),
    "trades": (
        "trades 2002+ (gave = old team, received = new team; pick_season set = a traded pick, not"
        " a player)"
    ),
    "lines": (
        "betting lines per game and source (home_spread > 0 = home favoured, total, moneylines)"
    ),
    "ngs_passing": (
        "Next Gen Stats passing per QB-week, 2016+. The week-0 rows (is_season_total) are NGS's "
        "own season figures: use them for a whole season (regular season: season_type 'REG'), "
        "and the weekly rows (NOT is_season_total) for single weeks or parts of a season"
    ),
    "ngs_receiving": (
        "Next Gen Stats receiving per player-week, 2016+ (separation, cushion, YAC over expected)"
    ),
    "ngs_rushing": "Next Gen Stats rushing per player-week, 2016+ (rush yards over expected)",
    "pfr_pass": "PFR advanced passing per QB-game, 2018+ (drops, bad throws, pressured, blitzed)",
    "pfr_rush": (
        "PFR advanced rushing per player-game, 2018+ (yards before / after contact, broken tackles)"
    ),
    "pfr_rec": "PFR advanced receiving per player-game, 2018+ (drops, broken tackles)",
    "pfr_def": (
        "PFR advanced defense per player-game, 2018+ (pressures, missed tackles, coverage allowed)"
    ),
    "officials": (
        "referee crews per game (game_id here is the OLD id: join games.old_game_id). The ids "
        "changed in 2023 (108 officials have two official_id values): group and count by "
        "official_name, never by official_id"
    ),
    "espn_qbr": "ESPN Total QBR per QB-week, current season",
    "espn_fpi": "ESPN FPI per team, current snapshot",
    "espn_injuries": "ESPN's latest injury list, current snapshot",
    "espn_scoreboard": "ESPN scoreboard per game, current season",
    "weather_forecasts": "Open-Meteo forecasts for upcoming outdoor games",
    "ngs_leaders": "NGS leaderboards (fastest ball carriers ...), current season",
    "player_ids": "ID crosswalk per player",
    "team_aliases": "old / alternative team codes -> canonical code",
    "odds_api": "raw sportsbook prices per event and bookmaker (team names, not codes)",
}

CORE_TABLES = ("games", "player_games", "teams")
TABLE_HINTS: dict[str, tuple[str, ...]] = {
    "plays": ("play", "down", "epa", "success", "drive", "red zone", "redzone", "4th", "3rd",
              "fourth", "third", "scramble", "sack", "pass rate", "proe", "explosive", "deep",
              "air yards", "yards after", "quarter", "touchdown pass", "two-point", "kick",
              "punt", "field goal", "penalt", "garbage", "neutral", "win probability", "wp"),
    "ftn_plays": ("play-action", "play action", "motion", "rpo", "screen", "blitz", "box",
                  "pressure", "drop", "out of pocket", "trick play", "sneak", "contested"),
    "team_games": ("team total", "team stats", "as a team", "offense", "defense", "per game"),
    "players": ("college", "drafted", "rookie", "height", "weight", "born", "age", "position"),
    "injuries": ("injur", "questionable", "doubtful", "practice", "out for"),
    "depth_charts": ("depth chart", "starter", "backup", "starting"),
    "snaps": ("snap",),
    "rosters_weekly": ("roster", "reserve", "practice squad", "inactive"),
    "draft_picks": ("draft", "pick", "round", "hall of fame"),
    "trades": ("trade",),
    "lines": ("spread", "favorite", "favourite", "underdog", "line", "over/under", "moneyline"),
    "ngs_passing": ("time to throw", "aggressive", "completion percentage above", "cpoe",
                    "next gen", "ngs"),
    "ngs_receiving": ("separation", "cushion", "next gen", "ngs"),
    "ngs_rushing": ("over expected", "ryoe", "next gen", "ngs", "eight defenders"),
    "pfr_pass": ("pressured", "bad throw", "drop", "blitzed", "hurried"),
    "pfr_rush": ("before contact", "after contact", "broken tackle"),
    "pfr_rec": ("drop", "broken tackle"),
    "pfr_def": ("pressure", "missed tackle", "coverage", "allowed", "hurries",
                "passer rating allowed"),
    "officials": ("referee", "official", "crew"),
    "espn_qbr": ("qbr",),
    "espn_fpi": ("fpi", "power index"),
    "weather_forecasts": ("forecast",),
}  # fmt: skip
KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "plays": (
        "game_id", "play_id", "season", "week", "season_type", "posteam", "defteam", "home_team",
        "away_team", "qtr", "down", "ydstogo", "yardline_100", "game_seconds_remaining",
        "score_differential", "play_type", "desc", "yards_gained", "pass", "rush", "qb_dropback",
        "qb_scramble", "sack", "interception", "fumble_lost", "touchdown", "pass_touchdown",
        "rush_touchdown", "two_point_attempt", "first_down", "epa", "success", "wp", "xpass",
        "pass_oe", "air_yards", "yards_after_catch", "pass_length", "pass_location",
        "run_location", "run_gap", "shotgun", "no_huddle", "passer_player_id",
        "passer_player_name", "receiver_player_id", "receiver_player_name", "rusher_player_id",
        "rusher_player_name", "penalty", "penalty_type", "field_goal_result", "kick_distance",
        "fourth_down_converted", "fourth_down_failed", "third_down_converted",
        "third_down_failed", "drive", "fixed_drive_result", "cpoe", "qb_epa",
    ),
    "player_games": (
        "player_id", "player_display_name", "position", "position_group", "season", "week",
        "season_type", "game_id", "team", "opponent_team", "completions", "attempts",
        "passing_yards", "passing_tds", "passing_interceptions", "sacks_suffered", "passing_epa",
        "carries", "rushing_yards", "rushing_tds", "rushing_epa", "receptions", "targets",
        "receiving_yards", "receiving_tds", "receiving_epa", "target_share", "def_tackles_solo",
        "def_tackle_assists", "def_sacks", "def_qb_hits", "def_interceptions",
        "def_pass_defended", "fg_made", "fg_att", "fg_long",
    ),
    "team_games": (
        "season", "week", "team", "season_type", "game_id", "opponent_team", "passing_yards",
        "passing_tds", "passing_interceptions", "sacks_suffered", "rushing_yards", "rushing_tds",
        "def_sacks", "def_interceptions", "penalties", "penalty_yards", "fumbles_lost_total",
    ),
}  # fmt: skip
WIDE = 40  # tables wider than this show key columns + question-matched ones


def words(question: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) >= 3}


def sql_tables_for(question: str, available: Iterable[str]) -> list[str]:
    q = question.lower()
    avail = set(available)
    picked = [t for t in CORE_TABLES if t in avail]
    for table, hints in TABLE_HINTS.items():
        if table in avail and table not in picked and any(h in q for h in hints):
            picked.append(table)
    return picked


def _matching(cols: list[tuple[str, str]], qwords: set[str]) -> list[tuple[str, str]]:
    stems = {w[:5] for w in qwords if len(w) >= 4}
    return [(c, t) for c, t in cols if any(s in c for s in stems)]


def sql_schema(
    question: str, columns: dict[str, list[tuple[str, str]]], max_extra: int = 25
) -> str:
    """The SQL rules + the picked tables with their columns, for the generation prompt.
    `columns` = {table: [(column, arrow type)]} (the sandbox's)."""
    qwords = words(question)
    lines = [SQL_RULES, "", "Tables:"]
    for t in sql_tables_for(question, columns):
        cols = columns[t]
        if len(cols) > WIDE:
            keys = KEY_COLUMNS.get(t, tuple(c for c, _ in cols[:20]))
            shown = [(c, ty) for c, ty in cols if c in keys]
            extra = [x for x in _matching(cols, qwords) if x[0] not in keys][:max_extra]
            shown += extra
            more = len(cols) - len(shown)
        else:
            shown, more = cols, 0
        col_text = ", ".join(f"{c} {_short(ty)}" for c, ty in shown)
        tail = f" (+{more} more columns)" if more else ""
        lines.append(f"- {t}: {TABLE_NOTES.get(t, '')}\n  columns: {col_text}{tail}")
    return "\n".join(lines)


def _short(arrow_type: str) -> str:
    t = arrow_type.lower()
    if "string" in t:
        return "text"
    if t.startswith(("int", "uint")):
        return "int"
    if t in ("double", "float") or "decimal" in t:
        return "num"
    if t == "bool":
        return "bool"
    if "timestamp" in t:
        return "timestamp"
    if "date" in t:
        return "date"
    return t


# ---- graph ----------------------------------------------------------------------------------

GRAPH_SCHEMA = """\
Neo4j 5 (Cypher), the weekly knowledge graph, READ ONLY. It is a snapshot as of one week W of
the current season (given below): results only from games before week W; week-W games exist
with no scores. Seasons 2018+. Return named properties (p.name AS player), never whole nodes,
relationships or paths. Use CALL (x) { ... } subqueries, EXISTS { }, COUNT { } as needed; no
procedures (no CALL db.* / apoc.* / gds.*).

Nodes (key property first):
Conventions: count every game, playoffs included, unless the question says "regular
season" or "playoffs" (Game.game_type 'REG'; playoffs 'WC', 'DIV', 'CON', 'SB'; THREW_TO and
PLAYED_FOR include playoffs and can't be split). Teammates = PLAYED_FOR the same team in the same
season with overlapping roster_weeks (any(w IN a.roster_weeks WHERE w IN b.roster_weeks)). A game:
return g.game_id with g.away_team and g.home_team. Answer in the question's own shape: one row
for a single answer ("who", "the most": ORDER BY the measure, LIMIT 1); one row per player /
coach / team / game for "which ..." (DISTINCT; not a row per season unless it asks).

- Team {team_id (code), name, nickname, conference, division}
- Player {player_id (GSIS id), name, position, position_group, college, draft_year,
  rookie_season, birth_date}
- Game {game_id, season, week, game_type ('REG', 'WC', 'DIV', 'CON', 'SB'), home_team,
  away_team (codes), home_score, away_score, completed, kickoff, roof, surface, temp, wind,
  neutral_site, div_game}
- Coach {coach_id, name}; Official {official_id, name}; Venue {stadium_id, name, roof, surface}
- TeamWeek {key 'KC:2026:4', team_id, season, week, net_epa ratings: off_epa, def_epa (EPA
  allowed: lower is better), net, off_pass_epa, off_rush_epa, def_pass_epa, def_rush_epa, elo,
  trend_delta, trend_dir, perf_vs_expected, prior_weight}: a team's rating going into a week
- GamePrediction {game_id, home_win_prob, expected_margin, pred_home_points, pred_away_points,
  variant, is_primary, model_version}: this week's game model predictions
- UsageProfile {player_id, season, group, team_id, games, target_share, carry_share,
  snap_share, adot, rz_share, targets, carries, rec_yds, rush_yds, yds_rank}
- PlayerProjection {player_id, game_id, season, week, target, target_label, mean, p10, p50,
  p90, team, opponent}: this week's player projections

Relationships:
- (Player)-[:PLAYED_FOR {season, first_week, last_week, weeks, roster_weeks (list of the
  weeks on the roster), games, status_last}]->(Team)
- (Player)-[:APPEARED_IN {season, week, team_id, snaps, off_snaps, def_snaps, snap_pct,
  pass_att, completions, pass_yds, pass_tds, ints, dropbacks, scrambles, carries, rush_yds,
  rush_tds, targets, rec, rec_yds, rec_tds, target_share, tackles, sacks, qb_hits, pressures,
  def_ints, passes_defended, epa, qb_started}]->(Game)
- (Team)-[:PLAYED_IN {season, week, home, opponent, points, points_allowed, margin, result
  ('W'/'L'/'T'), epa_per_play, epa_allowed, epa_margin, success_rate, plays, penalties,
  st_epa}]->(Game)
- (Player)-[:THREW_TO {season, team_id, targets, completions, yards, tds, epa,
  games_together}]->(Player): QB -> receiver, per season and team
- (Coach)-[:HEAD_COACH_OF {season, games}]->(Team); (Coach)-[:COACHED_IN {team_id}]->(Game)
- (Coach)-[:COORDINATOR_OF {season, role 'OC'/'DC'/'ST'}]->(Team)
- (Coach)-[:WORKED_UNDER {seasons, teams, roles}]->(Coach): coordinator under a head coach
- (Player)-[:ON_INJURY_REPORT {season, week, team_id, status, practice_status,
  body_part}]->(Game)
- (Player)-[:DEPTH_CHART {season, week, position, rank}]->(Team)
- (Player)-[:DRAFTED_BY {year, round, pick}]->(Team)
- (Player)-[:TRADED_TO {season, date, from_team}]->(Team)
- (Official)-[:OFFICIATED {role}]->(Game); (Game)-[:AT]->(Venue)
- (Team)-[:HAS_WEEK]->(TeamWeek); (TeamWeek)-[:NEXT]->(TeamWeek)
- (Game)-[:HAS_PREDICTION]->(GamePrediction); (Player)-[:HAS_PROFILE]->(UsageProfile)
- (Player)-[:PASS_CENTRALITY {season, team_id, share, rank, pagerank, degree}]->(Team): how
  much of a team's passing game runs through a player (this season)
- (Player)-[:SIMILAR_TO {season, other_season, other_team, group, score, rank}]->(Player):
  usage-profile look-alikes"""


def graph_schema() -> str:
    return GRAPH_SCHEMA


# ---- the history graph (AE02) ---------------------------------------------------------------

# from `history_graph/schema.py` (labels, keys, properties) and the play-calling definitions
# (`playcalling/labels.py`: distance 1-3 short, 4-6 medium, 7+ long; red zone = 20 yards or less)
HISTORY_SCHEMA = """\
Neo4j 5 (Cypher), the HISTORY graph, READ ONLY: every play since 2018 with its drive and game,
who did what on it, and (2018-2025) the players on the field. Return named properties
(p.season AS season, r.name AS receiver), never whole nodes, relationships or paths. Use
CALL (x) { ... } subqueries, EXISTS { }, COUNT { } as needed; no procedures.

Conventions:
- Start from an indexed match: (p:Play {offense: 'KC'}), (p:Play {defense: 'KC'}),
  (pl:Player {player_id: '00-0033873'}) (the "Names" list gives the id) or Play season/week.
  Teams are codes (KC, BUF, LA = Rams, LAC, LV, WAS ...). Players by full name (Player.name).
- Count every game, playoffs included, unless the question says "regular season" (Play
  season_type 'REG') or "playoffs" ('POST'). Game.game_type: 'REG', 'WC', 'DIV', 'CON', 'SB'.
- Real plays: p.play_type IN ['pass', 'run'] (a scramble and a sack are dropbacks:
  p.dropback); drop p.play_type = 'no_play' (a penalty snap) and p.two_point for per-play counts
  and rates. Role relationships (PASSED, RUSHED, TARGETED, CAUGHT, INTERCEPTED, SACKED,
  TACKLED) exist only on plays that count, so count() of a role equals the box score.
- PASSED includes sacks (p.sack): pass attempts are p.pass_attempt (spikes included, as the box
  score counts them), completions p.complete.
  Scrambles are RUSHED (p.scramble). A receiver's yards = p.yards on his completed targets.
- p.touchdown is any touchdown on the play, the defense's return of a fumble or interception
  too: an offensive touchdown is p.touchdown AND NOT p.interception AND NOT p.fumble_lost.
- Situations: 3rd and long = p.down = 3 AND p.distance >= 7 (short 1-3, medium 4-6, long
  7+); red zone = p.yards_to_goal <= 20; goal to go = p.goal_to_go; quarter 5 = overtime.
- On a kickoff the offense is the receiving team. p.offense / p.defense equal the OFFENSE /
  DEFENSE relationships (use the properties: they're indexed).
- Participation (research data, seasons 2018-2025 only, only where p.part is true):
  formation, personnel, extra_ol, def_package, coverage, man_zone, target_route, pressure,
  time_to_throw, part_box, part_rushers and the ON_FIELD relationships. Never for the current
  season. FTN charting (2022 on, only where p.ftn is true): play_action, screen, rpo, motion,
  blitzers, rushers, blitz (1+ blitzer), box, qb_alignment, qb_sneak, drop (the targeted
  receiver dropped it), throw_away, out_of_pocket (the QB). Rates over these use only the plays
  that have them (p.part / p.ftn) in the denominator.
- A blitz is FTN's (p.ftn AND p.blitz, 2022 on); before 2022 the closest measure is 5+ pass
  rushers from participation (p.part_rushers >= 5). Say which one a rate uses.
- Answer in the question's own shape: one row for a single answer (ORDER BY the measure,
  LIMIT 1); one row per player / team for "which ..."; the top N for "top N".

Nodes (key property first):
- Season {season}
- Team {team_id (code), name, nickname, conference, division}
- Player {player_id (GSIS id), name (full name), position, position_group}
- Coach {coach_id ('andy-reid'), name}
- Game {game_id, season, week, game_type, gameday ('2024-09-05'), home_team, away_team,
  home_score, away_score, overtime, stadium, roof, home_coach, away_coach}
- Drive {drive_key, game_id, season, week, drive (1, 2 ... in the game), team (the offense),
  result ('Touchdown', 'Field goal', 'Punt', 'Turnover' ...), plays, first_downs, quarter,
  start, end, time_of_possession, scored}
- Play {play_key, game_id, play_id, season, week, season_type ('REG' / 'POST'), quarter,
  clock, seconds_left (in the game), order, offense, defense, down, distance, yards_to_goal,
  goal_to_go, score_diff (offense minus defense), wp (offense win probability), play_type
  ('pass', 'run', 'punt', 'field_goal', 'extra_point', 'kickoff', 'qb_kneel', 'qb_spike',
  'no_play'), description, yards, epa, success, first_down, touchdown, penalty, two_point,
  dropback, pass_attempt, sack, scramble, complete, interception, fumble_lost, air_yards,
  yards_after_catch, pass_length ('short' / 'deep': 16+ air yards), pass_location / run_location
  ('left' / 'middle' / 'right'), run_gap ('end' / 'tackle' / 'guard'), shotgun, no_huddle,
  FTN: ftn, play_action, screen, rpo, motion, blitzers, rushers, blitz, box, qb_alignment
  ('shotgun' / 'under_center' / 'pistol'), qb_sneak, drop, throw_away, out_of_pocket,
  participation: part, formation, personnel ('11' = 1
  back, 1 tight end), extra_ol, def_package ('base' / 'nickel' / 'dime'), coverage ('cover_0',
  'cover_1', 'cover_2', 'cover_2_man', 'cover_3', 'cover_4', ...), man_zone ('man' / 'zone'),
  target_route, pressure, time_to_throw, part_box, part_rushers}

Relationships:
- (Game)-[:IN_SEASON]->(Season); (Game)-[:HOME_TEAM]->(Team); (Game)-[:AWAY_TEAM]->(Team)
- (Drive)-[:IN_GAME]->(Game); (Play)-[:IN_DRIVE]->(Drive)
- (Play)-[:OFFENSE]->(Team); (Play)-[:DEFENSE]->(Team)
- (Player)-[:PASSED|RUSHED|TARGETED|CAUGHT|INTERCEPTED]->(Play)
- (Player)-[:SACKED {half (a shared sack)}]->(Play); (Player)-[:TACKLED {kind: 'solo' /
  'assist' / 'with_assist'}]->(Play)
- (Player)-[:ON_FIELD {side ('offense' / 'defense'), position}]->(Play): participation, every
  snap (no_play and special teams too: on a kickoff the return unit is 'offense', on a punt or
  field goal the kicking unit). A snap is any play participation lists (an ON_FIELD link),
  penalty and special-teams snaps included; "on offense" / "on defense" match ON_FIELD.side.
  Filter to scrimmage plays (p.play_type IN ['pass', 'run'] AND NOT p.two_point) only when
  the question asks about plays, passes or runs rather than snaps
- (Player)-[:PLAYED_FOR {season, weeks, first_week, last_week, games, status_last}]->(Team)
- (Coach)-[:HEAD_COACH {season, games, first_week, last_week}]->(Team);
  (Coach)-[:OFF_COORD|DEF_COORD|ST_COORD {season}]->(Team)

Person links (AE03, derived from the plays; one link per pair, totals over every season;
stint_* are parallel lists, one item per season and team, in season order):
- (Player)-[:TEAMMATE {snaps (on the field together, same side), seasons, teams, first_season,
  last_season, stint_seasons, stint_teams, stint_snaps}]-(Player): participation, 2018-2025;
  stored once per pair, so match it without a direction: (a)-[t:TEAMMATE]-(b)
- (Player)-[:CONNECTION {targets, catches, yards, touchdowns, epa (total), seasons, teams,
  first_season, last_season}]->(Player): passer -> receiver (the quarterback, or a WR / RB on
  a trick play: q.position = 'QB' for quarterbacks only), every season (the current one too);
  EPA per target = c.epa / c.targets
- (Player)-[:OPPONENT {plays, seasons, first_season, last_season}]->(Player): offense ->
  defense on the same scrimmage plays, only skill players (WR, TE, RB, FB) vs defensive backs,
  offensive linemen vs defensive linemen and the quarterback vs any defender, 2018-2025
- (Coach)-[:COACHED {snaps (the player's snaps in the coach's units, each counted once),
  seasons, teams, roles ('HC', 'OC', 'DC', 'ST'), stint_seasons, stint_teams, stint_roles,
  stint_snaps}]->(Player): head coach (all units) or the coordinator of the player's unit,
  from snap counts (2018 on); one stint item per season, team and role (stint_snaps: that
  role's snaps), so one role's snaps = the sum of stint_snaps where stint_roles[i] = the role
- (Coach)-[:WORKED_WITH {snaps (the team's, while both were there), games, seasons, teams,
  stint_seasons, stint_teams, stint_roles ('HC|OC': the start coach's role | the end
  coach's)}]-(Coach): on the same staff (head coach and coordinators) in a season, over the
  weeks both were there; stored once per pair
- "How are X and Y connected": MATCH p = SHORTEST 1 (a)-[:TEAMMATE|COACHED|WORKED_WITH]-+(b),
  then one row per link (UNWIND range(0, length(p) - 1)); never through Team / Play nodes"""


def history_schema(asof: dict | None = None) -> str:
    """The history graph's schema for the generation prompt, with what the build covers
    (`history_graph.state.history_asof`)."""
    if not asof:
        return HISTORY_SCHEMA
    cover = f"The graph covers {asof['first_season']}-{asof['through_season']}"
    if asof.get("season"):
        cover += f" plus {asof['season']} through week {asof['week']}"
    return f"{HISTORY_SCHEMA}\n{cover} ({asof.get('label') or 'history graph'})."
