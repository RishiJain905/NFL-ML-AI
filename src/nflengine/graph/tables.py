"""Knowledge-graph tables: every node and relationship as a Polars frame (documentation/05).

The graph is rebuilt from curated Parquet every week (plan P05). This module does the
"Polars" half: it reads the curated tables and returns one frame per node label and per
relationship type, **as of one key**, so the same code builds the live graph and the graph
a past week's backtest would have seen. `graph/load.py` writes the frames to Neo4j.

Visibility for a key (season S, week W, run time T); D44 (only weeks strictly before W)
plus the live-run rules of `features.qb.live_starters`:

- **Game nodes:** every REG / POST game from `seasons.graph_start` through week W of S
  (week-W games have no scores).
- **Results** (scores, APPEARED_IN, PLAYED_IN EPA and special-teams EPA, THREW_TO,
  officials' games): completed games strictly before (S, W). `PLAYED_IN.st_epa` is centered
  on the league's mean EPA per special-teams play type in that season, over those visible
  plays only.
- **Rosters** (PLAYED_FOR): `rosters_weekly` rows strictly before (S, W).
- **Depth charts:** rows strictly before (S, W), plus week-W charts dated (`snap_date`) on
  or before T's date.
- **Injury reports:** rows strictly before (S, W), plus week-W reports modified on or
  before T. 2025+ rows have no `date_modified`: a live run takes the week-W rows it has,
  a backtest takes none (D62).
- **Trades:** trade date on or before T's date. **Draft picks:** draft year <= S.
- **TeamWeek:** feature rows with (season, week) <= (S, W) (the week-W row is built from
  weeks < W).
- **Expected QBs** (week-W Game nodes): the Tuesday rule from `game_features`; a live run
  adds `live_starters` (schedule, depth chart, injury report at T).
- **Officials (P08):** crews of completed games strictly before (S, W). Week-W crews only
  in a live run and only if the snapshot has them (nflverse publishes a crew after its
  game, weeks late: on 2026-10-04 the table had 2026 week 1 only); a backtest never sees a
  week-W crew, because no Tuesday run could have.
- **FTN play action (P08, `PLAYED_IN.pa_rate`):** plays strictly before (S, W); a backtest
  also drops week W-1 of S (FTN is about a week late on a Tuesday, like PFR).
- **QB style (P08, `APPEARED_IN.scrambles` / `air_yards` / `air_att`) and usage profiles
  (P08, `UsageProfile`):** visible plays only (strictly before (S, W)); profiles are
  regular season.
- **Coaching seed (P08, optional `config/coaching_seed.csv`):** rows with season <= S
  (a staff is known before its season starts).
- **GDS results (P08, `PASS_CENTRALITY`, `SIMILAR_TO`):** computed in Neo4j from the
  loaded, already as-of relationships (`graph/gds.py`), so they inherit these rules.

Relationship frames have columns `s` (start key), `e` (end key) and the properties.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from nflengine.paths import DataPaths

GAME_TYPES = ("REG", "WC", "DIV", "CON", "SB")
# rosters_weekly statuses that mean "not on the team that week"
NOT_ON_TEAM = ("CUT", "RET", "UFA", "RFA", "TRD", "TRC", "TRT")
# play types that are special-teams plays (PLAYED_IN.st_epa). nflverse: on a kickoff `posteam`
# is the RECEIVING team, on a punt / field goal / extra point the kicking team; `epa` is
# always posteam's, so the other side's is -epa (FG plays have `special_teams_play` = 0)
ST_PLAY_TYPES = ("kickoff", "punt", "field_goal", "extra_point")
# depth-chart slots that are special teams (left out of DEPTH_CHART)
SPECIAL_SLOTS = ("KR", "PR", "H", "LS", "P", "PK", "K", "KOR", "PK/KO", "KO")
NODE_KEYS = {
    "Team": "team_id",
    "Player": "player_id",
    "Game": "game_id",
    "Coach": "coach_id",
    "Venue": "stadium_id",
    "Official": "official_id",
    "TeamWeek": "key",
    "GamePrediction": "key",
    "PublishedInsight": "key",
    "UsageProfile": "key",
}
# relationship type -> (start label, end label)
REL_ENDS = {
    "PLAYED_FOR": ("Player", "Team"),
    "APPEARED_IN": ("Player", "Game"),
    "PLAYED_IN": ("Team", "Game"),
    "AT": ("Game", "Venue"),
    "HEAD_COACH_OF": ("Coach", "Team"),
    "COACHED_IN": ("Coach", "Game"),
    "OFFICIATED": ("Official", "Game"),
    "THREW_TO": ("Player", "Player"),
    "ON_INJURY_REPORT": ("Player", "Game"),
    "DEPTH_CHART": ("Player", "Team"),
    "DRAFTED_BY": ("Player", "Team"),
    "TRADED_TO": ("Player", "Team"),
    "HAS_WEEK": ("Team", "TeamWeek"),
    "NEXT": ("TeamWeek", "TeamWeek"),
    "HAS_PREDICTION": ("Game", "GamePrediction"),
    # P08: the optional coaching seed and the usage profiles behind player similarity
    "COORDINATOR_OF": ("Coach", "Team"),
    "WORKED_UNDER": ("Coach", "Coach"),
    "HAS_PROFILE": ("Player", "UsageProfile"),
}
# write order (plan P05): nodes, then relationships
NODE_ORDER = (
    "Team",
    "Venue",
    "Coach",
    "Official",
    "Player",
    "Game",
    "TeamWeek",
    "GamePrediction",
    "PublishedInsight",
    "UsageProfile",
)
REL_ORDER = tuple(REL_ENDS)


@dataclass(frozen=True)
class GraphKey:
    """The as-of key of one graph build: the Tuesday (backtest) or the moment (live) of a
    run that previews week `week` of `season`."""

    season: int
    week: int
    run_time: dt.datetime  # tz-aware UTC
    mode: str = "live"  # live | backtest
    start_season: int = 2018

    def __post_init__(self) -> None:
        if self.run_time.tzinfo is None:
            raise ValueError("GraphKey.run_time must be timezone-aware (UTC)")
        if self.mode not in ("live", "backtest"):
            raise ValueError(f"mode must be live or backtest, got {self.mode!r}")

    @property
    def run_date(self) -> dt.date:
        return self.run_time.astimezone(dt.UTC).date()

    @property
    def tag(self) -> str:
        return f"{self.season}-w{self.week:02d}"

    def before(self, season: str = "season", week: str = "week") -> pl.Expr:
        """Rows strictly before the key's week (D44)."""
        return (pl.col(season) < self.season) | (
            (pl.col(season) == self.season) & (pl.col(week) < self.week)
        )

    def through(self, season: str = "season", week: str = "week") -> pl.Expr:
        """Rows up to and including the key's week."""
        return (pl.col(season) < self.season) | (
            (pl.col(season) == self.season) & (pl.col(week) <= self.week)
        )

    def in_window(self, season: str = "season") -> pl.Expr:
        return pl.col(season).is_between(self.start_season, self.season)


@dataclass
class GraphTables:
    key: GraphKey
    nodes: dict[str, pl.DataFrame] = field(default_factory=dict)
    rels: dict[str, pl.DataFrame] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)  # rel rows without both endpoints

    def expected_counts(self) -> dict[str, int]:
        """What Neo4j must hold after loading these tables (the load test's oracle)."""
        out = {f"node:{k}": v.height for k, v in self.nodes.items()}
        out.update({f"rel:{k}": v.height for k, v in self.rels.items()})
        return out


# ---- helpers ------------------------------------------------------------------------------------


def slug(name: str) -> str:
    """`coach_id` from a name: "Andy Reid" -> "andy-reid"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _scan(paths: DataPaths, name: str) -> pl.LazyFrame | None:
    path = paths.curated / f"{name}.parquet"
    return pl.scan_parquet(path) if path.exists() else None


def _plays(paths: DataPaths, key: GraphKey, cols: list[str]) -> pl.DataFrame:
    files = sorted((paths.curated / "plays").glob("season=*.parquet"))
    files = [f for f in files if key.start_season <= int(f.stem.split("=")[1]) <= key.season]
    if not files:
        return pl.DataFrame()
    frames = []
    for f in files:
        lf = pl.scan_parquet(f)
        present = lf.collect_schema().names()
        frames.append(lf.select([c for c in cols if c in present]))
    return (
        pl.concat(frames, how="diagonal_relaxed")
        .with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))
        .filter(key.before())
        .collect()
    )


def _ends(df: pl.DataFrame, s: str, e: str) -> pl.DataFrame:
    return df.rename({s: "s", e: "e"}) if s != "s" or e != "e" else df


def _keep_known(
    rels: pl.DataFrame, starts: pl.Series, ends: pl.Series, name: str, dropped: dict[str, int]
) -> pl.DataFrame:
    """Drop relationship rows whose endpoints aren't nodes (and count them)."""
    out = rels.filter(
        pl.col("s").is_in(starts.unique().implode()) & pl.col("e").is_in(ends.unique().implode())
    )
    dropped[name] = rels.height - out.height
    return out


# ---- inputs -------------------------------------------------------------------------------------


@dataclass
class GraphInputs:
    """Curated frames the builders need (already restricted to the graph's seasons)."""

    games: pl.DataFrame
    teams: pl.DataFrame
    players: pl.DataFrame
    rosters: pl.DataFrame
    player_games: pl.DataFrame
    snaps: pl.DataFrame
    pfr_def: pl.DataFrame
    ngs: dict[str, pl.DataFrame]
    plays: pl.DataFrame
    team_games: pl.DataFrame
    officials: pl.DataFrame
    injuries: pl.DataFrame
    depth_charts: pl.DataFrame
    draft_picks: pl.DataFrame
    trades: pl.DataFrame
    venues: pl.DataFrame  # game_id -> venue stadium_id
    stadiums: pl.DataFrame  # stadium_id, name
    expected_qbs: pl.DataFrame  # week-W games: game_id, home_qb_expected, away_qb_expected
    team_weeks: pl.DataFrame
    predictions: pl.DataFrame
    published: pl.DataFrame
    # P08: FTN charting (game_id, play_id, season, week, is_play_action) and the optional
    # hand-made coaching seed (graph/tables_extra.py)
    ftn: pl.DataFrame = field(default_factory=pl.DataFrame)
    coaching_seed: pl.DataFrame = field(default_factory=pl.DataFrame)


PLAY_COLS = [
    "season",
    "week",
    "game_id",
    "play_id",
    "season_type",
    "posteam",
    "defteam",
    "play_type",
    "pass",
    "rush",
    "epa",
    "success",
    "qb_dropback",
    "qb_scramble",
    "two_point_attempt",
    "pass_attempt",
    "sack",
    "complete_pass",
    "pass_touchdown",
    "receiving_yards",
    "passer_player_id",
    "receiver_player_id",
    "passer_id",
    "qb_epa",
    # P08: QB style and usage profiles (graph/tables_extra.py)
    "air_yards",
    "yardline_100",
    "rusher_player_id",
    "rushing_yards",
]


def load_inputs(
    paths: DataPaths,
    key: GraphKey,
    *,
    predictions_path: Path | None = None,
    published_path: Path | None = None,
    coaching_seed_path: Path | None = None,
    log: Callable[[str], None] = print,
) -> GraphInputs:
    """Read every curated table the graph needs, restricted to the key's window (plus the
    optional coaching seed, `config/coaching_seed.csv` unless `coaching_seed_path`)."""
    from nflengine.features.venues import game_travel, load_venues
    from nflengine.graph.tables_extra import apply_coach_fixes, read_coach_fixes, read_coaching_seed

    games_all = pl.read_parquet(paths.curated / "games.parquet")
    games = games_all.filter(
        key.in_window() & key.through() & pl.col("game_type").is_in(GAME_TYPES)
    )
    # P10: hand-checked corrections to the schedules' head coaches (config/head_coach_fixes.csv)
    games = apply_coach_fixes(games, read_coach_fixes(log=log))

    def table(name: str, season_col: str | None = "season") -> pl.DataFrame:
        lf = _scan(paths, name)
        if lf is None:
            log(f"[yellow]graph: curated table {name} missing; skipped[/]")
            return pl.DataFrame()
        if season_col:
            lf = lf.filter(pl.col(season_col).is_between(key.start_season, key.season))
        return lf.collect()

    ngs = {}
    for kind in ("receiving", "passing", "rushing"):
        df = table(f"ngs_{kind}")
        if df.height:
            df = df.filter(~pl.col("is_season_total").fill_null(False)).filter(key.before())
        ngs[kind] = df

    try:
        travel = game_travel(games_all.filter(key.in_window()))
        venues = travel.select("game_id", pl.col("venue_stadium_id").alias("stadium_id"))
    except Exception as e:  # venue resolution is a nicety; never block the build
        log(f"[yellow]graph: venue resolution failed ({type(e).__name__}); using stadium_id[/]")
        venues = games.select("game_id", "stadium_id")
    stadiums = load_venues().select("stadium_id", "name")

    return GraphInputs(
        games=games,
        teams=table("teams", None),
        players=table("players", None),
        rosters=table("rosters_weekly"),
        player_games=table("player_games"),
        snaps=table("snaps"),
        pfr_def=table("pfr_def"),
        ngs=ngs,
        plays=_plays(paths, key, PLAY_COLS),
        team_games=table("team_games"),
        officials=table("officials"),
        injuries=table("injuries"),
        depth_charts=table("depth_charts"),
        draft_picks=table("draft_picks", None),
        trades=table("trades", None),
        venues=venues,
        stadiums=stadiums,
        expected_qbs=expected_qbs(paths, key, log),
        team_weeks=team_week_inputs(paths, key),
        predictions=(
            pl.read_parquet(predictions_path)
            if predictions_path is not None and predictions_path.exists()
            else pl.DataFrame()
        ),
        published=(
            pl.read_parquet(published_path)
            if published_path is not None and published_path.exists()
            else pl.DataFrame()
        ),
        ftn=ftn_inputs(paths, key),
        coaching_seed=read_coaching_seed(coaching_seed_path, log=log),
    )


def ftn_inputs(paths: DataPaths, key: GraphKey) -> pl.DataFrame:
    """FTN play-action flags per play in the graph's seasons (the as-of rules are applied by
    `tables_extra.visible_ftn`)."""
    lf = _scan(paths, "ftn_plays")
    if lf is None:
        return pl.DataFrame()
    return (
        lf.filter(pl.col("season").is_between(key.start_season, key.season))
        .select(
            pl.col("nflverse_game_id").alias("game_id"),
            pl.col("nflverse_play_id").cast(pl.Float64).alias("play_id"),
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            "is_play_action",
        )
        .collect()
    )


def expected_qbs(paths: DataPaths, key: GraphKey, log: Callable[[str], None]) -> pl.DataFrame:
    """Expected starting QB per side for the key's games: the Tuesday rule stored in
    `game_features` (what the game model used), replaced in a live run by
    `features.qb.live_starters` (schedule / depth chart / injury report at the run time),
    exactly as `nfl train game` does. `*_qb_source` says which rule picked him (`last_game`,
    `depth_chart_w1`, ... for the Tuesday rule; `schedule`, `depth_chart`, `injury_next` live),
    so a pick that only rests on the last game's starter can be worded as unconfirmed."""
    schema = {
        "game_id": pl.String,
        "home_qb_expected": pl.String,
        "away_qb_expected": pl.String,
        "home_qb_source": pl.String,
        "away_qb_source": pl.String,
    }
    path = paths.features / "game_features.parquet"
    if not path.exists():
        return pl.DataFrame(schema=schema)
    lf = pl.scan_parquet(path)
    present = lf.collect_schema().names()
    gf = (
        lf.filter((pl.col("season") == key.season) & (pl.col("week") == key.week))
        .select(
            "game_id",
            "home_team",
            "away_team",
            "home_qb_id",
            "away_qb_id",
            *[
                pl.col(c) if c in present else pl.lit(None, pl.String).alias(c)
                for c in ("home_qb_source", "away_qb_source")
            ],
        )
        .collect()
    )
    if key.mode == "live" and gf.height:
        try:
            from nflengine.features.asof import AsOf
            from nflengine.models.game_runs import _live_overrides

            games = pl.read_parquet(paths.curated / "games.parquet")
            ov = _live_overrides(paths, AsOf(key.season, key.week), key.run_date)(games)
            if ov is not None and ov.height:
                m = dict(zip(ov["team"].to_list(), ov["qb_id"].to_list(), strict=True))
                src = dict(zip(ov["team"].to_list(), ov["qb_source"].to_list(), strict=True))
                gf = gf.with_columns(
                    *[
                        pl.col(f"{side}_team")
                        .replace_strict(mapping, default=None)
                        .fill_null(pl.col(f"{side}_qb_{col}"))
                        .alias(f"{side}_qb_{col}")
                        for side in ("home", "away")
                        for col, mapping in (("id", m), ("source", src))
                    ]
                )
        except Exception as e:  # fall back to the Tuesday rule
            log(f"[yellow]graph: live QB overrides failed ({type(e).__name__}); Tuesday rule[/]")
    return gf.select(
        "game_id",
        pl.col("home_qb_id").alias("home_qb_expected"),
        pl.col("away_qb_id").alias("away_qb_expected"),
        "home_qb_source",
        "away_qb_source",
    ).cast(schema)  # type: ignore[arg-type]


def team_week_inputs(paths: DataPaths, key: GraphKey) -> pl.DataFrame:
    """Ratings + Elo + trend rows (P02 feature tables) up to and including the key."""
    feats = paths.features
    if not (feats / "team_ratings.parquet").exists():
        return pl.DataFrame()
    keys = ["season", "week", "team"]
    path = feats / "team_ratings.parquet"
    # prior_weight: the preseason prior's share of the rating (1 in week 1, ~0.5 by week 4)
    extra = [c for c in ("prior_weight",) if c in pl.read_parquet_schema(path)]
    rt = pl.read_parquet(
        path,
        columns=[
            *keys,
            "off_epa",
            "def_epa",
            "net_epa",
            "off_pass_epa",
            "off_rush_epa",
            "def_pass_epa",
            "def_rush_epa",
            *extra,
        ],
    )
    out = rt
    if (feats / "team_elo.parquet").exists():
        out = out.join(
            pl.read_parquet(feats / "team_elo.parquet", columns=[*keys, "elo"]), on=keys, how="left"
        )
    if (feats / "team_trends.parquet").exists():
        tr = pl.read_parquet(
            feats / "team_trends.parquet",
            columns=[*keys, "trend_delta", "direction", "perf_vs_expected", "net_epa_prev"],
        )
        out = out.join(tr, on=keys, how="left")
    return out.filter(key.in_window() & key.through())


# ---- node builders ------------------------------------------------------------------------------


def team_nodes(inp: GraphInputs) -> pl.DataFrame:
    t = inp.teams
    return t.select(
        pl.col("team").alias("team_id"),
        pl.col("team").alias("franchise_id"),
        pl.col("team_name").alias("name"),
        pl.col("team_nick").alias("nickname"),
        pl.col("team_conf").alias("conference"),
        pl.col("team_division").alias("division"),
    ).unique("team_id", keep="first")


def game_nodes(inp: GraphInputs, key: GraphKey) -> pl.DataFrame:
    g = inp.games.filter(key.in_window() & key.through())
    visible = key.before() & pl.col("completed").fill_null(False)
    out = g.select(
        "game_id",
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "game_type",
        pl.col("kickoff_utc").alias("kickoff"),
        pl.col("home_team"),
        pl.col("away_team"),
        pl.when(visible).then(pl.col("home_score")).alias("home_score"),
        pl.when(visible).then(pl.col("away_score")).alias("away_score"),
        visible.alias("completed"),
        "roof",
        "surface",
        pl.when(visible).then(pl.col("temp")).alias("temp"),
        pl.when(visible).then(pl.col("wind")).alias("wind"),
        pl.col("neutral_site").fill_null(False),
        (pl.col("div_game").fill_null(0) == 1).alias("div_game"),
        "stadium_id",
    )
    if inp.expected_qbs.height:
        out = out.join(inp.expected_qbs, on="game_id", how="left")
    return out


def venue_nodes(inp: GraphInputs, games: pl.DataFrame) -> pl.DataFrame:
    """Venues used by the graph's games: name from `stadiums.yaml` (else the schedule's),
    roof and surface from the latest game there."""
    gv = inp.venues.join(games.select("game_id", "season", "week", "roof", "surface"), on="game_id")
    latest = (
        gv.sort("season", "week")
        .group_by("stadium_id", maintain_order=True)
        .last()
        .select("stadium_id", "roof", "surface")
    )
    sched_names = (
        inp.games.sort("season", "week")
        .group_by("stadium_id", maintain_order=True)
        .last()
        .select("stadium_id", pl.col("stadium").alias("sched_name"))
    )
    return (
        latest.join(inp.stadiums, on="stadium_id", how="left")
        .join(sched_names, on="stadium_id", how="left")
        .select(
            "stadium_id",
            pl.coalesce("name", "sched_name", "stadium_id").alias("name"),
            "roof",
            "surface",
        )
        .filter(pl.col("stadium_id").is_not_null())
    )


def coach_frames(
    inp: GraphInputs, key: GraphKey
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Coach nodes, HEAD_COACH_OF (per team-season, scheduled games through week W) and
    COACHED_IN (every graph game: the head coach is known before kickoff)."""
    sides = pl.concat(
        [
            inp.games.select(
                "game_id",
                pl.col("season").cast(pl.Int32),
                pl.col(f"{s}_team").alias("team_id"),
                pl.col(f"{s}_coach").alias("name"),
            )
            for s in ("home", "away")
        ]
    ).filter(pl.col("name").is_not_null())
    sides = sides.with_columns(
        pl.col("name").map_elements(slug, return_dtype=pl.String).alias("coach_id")
    )
    nodes = sides.group_by("coach_id").agg(pl.col("name").last()).sort("coach_id")
    hc = (
        sides.group_by("coach_id", "team_id", "season")
        .agg(pl.len().cast(pl.Int32).alias("games"))
        .sort("season", "team_id", "coach_id")
    )
    hc = _ends(hc, "coach_id", "team_id")
    ci = _ends(sides.select("coach_id", "game_id", "team_id"), "coach_id", "game_id")
    return nodes, hc, ci


def player_nodes(
    inp: GraphInputs, ids: pl.Series, scramble: pl.DataFrame, key: GraphKey
) -> pl.DataFrame:
    """Players referenced by any relationship; properties from `players` (the master table:
    current metadata), else the latest roster row strictly before the key (Sol review: a
    later row could carry a future name or position)."""
    ids = ids.drop_nulls().unique()
    p = inp.players.select(
        pl.col("gsis_id").alias("player_id"),
        pl.col("display_name").alias("name"),
        "position",
        "position_group",
        "birth_date",
        pl.col("college_name").alias("college"),
        "rookie_season",
        "draft_year",
    )
    base = pl.DataFrame({"player_id": ids}).join(p, on="player_id", how="left")
    # fallback names / positions from the latest roster row
    ros = (
        inp.rosters.filter(pl.col("gsis_id").is_not_null() & key.before())
        .sort("season", "week")
        .group_by("gsis_id", maintain_order=True)
        .last()
        .select(
            pl.col("gsis_id").alias("player_id"),
            pl.col("full_name").alias("r_name"),
            pl.col("position").alias("r_pos"),
        )
    )
    out = base.join(ros, on="player_id", how="left").with_columns(
        pl.coalesce("name", "r_name").alias("name"),
        pl.coalesce("position", "r_pos").alias("position"),
        pl.col("birth_date").cast(pl.String),
    )
    out = out.with_columns(
        pl.coalesce("position_group", position_group_expr("position")).alias("position_group")
    ).drop("r_name", "r_pos")
    if scramble.height:
        out = out.join(scramble, on="player_id", how="left")
    return out.filter(pl.col("name").is_not_null()).sort("player_id")


POSITION_GROUPS = {
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "HB": "RB",
    "WR": "WR",
    "TE": "TE",
    "OT": "OL",
    "T": "OL",
    "G": "OL",
    "OG": "OL",
    "C": "OL",
    "OL": "OL",
    "DE": "DL",
    "DT": "DL",
    "NT": "DL",
    "DL": "DL",
    "EDGE": "DL",
    "LB": "LB",
    "OLB": "LB",
    "ILB": "LB",
    "MLB": "LB",
    "CB": "DB",
    "S": "DB",
    "SS": "DB",
    "FS": "DB",
    "SAF": "DB",
    "DB": "DB",
    "K": "SPEC",
    "P": "SPEC",
    "LS": "SPEC",
}


def position_group_expr(col: str) -> pl.Expr:
    return pl.col(col).replace_strict(POSITION_GROUPS, default=None)


def scramble_rates(plays: pl.DataFrame) -> pl.DataFrame:
    """`Player.scramble_rate` (QBs): scrambles per dropback over every visible dropback in
    the graph's seasons, with the dropback count as its sample (`dropbacks`)."""
    if plays.is_empty():
        return pl.DataFrame(
            schema={"player_id": pl.String, "dropbacks": pl.Int64, "scramble_rate": pl.Float64}
        )
    db = plays.filter(
        (pl.col("qb_dropback").fill_null(0) == 1)
        & pl.col("play_type").is_in(["pass", "run"])
        & (pl.col("two_point_attempt").fill_null(0) == 0)
        & pl.col("passer_id").is_not_null()
    )
    return (
        db.group_by(pl.col("passer_id").alias("player_id"))
        .agg(
            pl.len().alias("dropbacks"),
            (pl.col("qb_scramble").fill_null(0).sum() / pl.len()).alias("scramble_rate"),
        )
        .filter(pl.col("dropbacks") >= 1)
    )


# ---- relationship builders ----------------------------------------------------------------------


def played_for(inp: GraphInputs, key: GraphKey, appear: pl.DataFrame) -> pl.DataFrame:
    """(Player)-[:PLAYED_FOR]->(Team) per season: weeks on the roster (strictly before the
    key), the first / last such week, the latest status (RES = reserve / IR), and games
    played for the team (from APPEARED_IN)."""
    r = inp.rosters.filter(
        pl.col("gsis_id").is_not_null()
        & pl.col("team").is_not_null()
        & ~pl.col("status").is_in(NOT_ON_TEAM)
        & key.before()
    )
    agg = (
        r.sort("season", "week")
        .group_by("gsis_id", "team", "season")
        .agg(
            pl.col("week").min().cast(pl.Int32).alias("first_week"),
            pl.col("week").max().cast(pl.Int32).alias("last_week"),
            pl.col("week").n_unique().cast(pl.Int32).alias("weeks"),
            pl.col("week").unique().sort().cast(pl.Int32).alias("roster_weeks"),
            pl.col("status").last().alias("status_last"),
        )
    )
    games = appear.group_by(
        pl.col("s").alias("gsis_id"), pl.col("team_id").alias("team"), "season"
    ).agg(pl.len().cast(pl.Int32).alias("games"))
    out = agg.join(games, on=["gsis_id", "team", "season"], how="full", coalesce=True)
    out = out.with_columns(
        pl.col("games").fill_null(0),
        pl.col("season").cast(pl.Int32),
    )
    return _ends(out.sort("season", "team", "gsis_id"), "gsis_id", "team")


def appeared_in(inp: GraphInputs, key: GraphKey, starters: pl.DataFrame) -> pl.DataFrame:
    """(Player)-[:APPEARED_IN]->(Game): box score (player_games) full-joined with snaps
    (a blocking TE has snaps and no box score; curated-data P04 quirk), plus PFR pressures,
    weekly NGS and QB dropbacks / start. Completed games strictly before the key."""
    gkeys = inp.games.filter(key.before() & pl.col("completed").fill_null(False)).select("game_id")
    pg = inp.player_games.filter(pl.col("player_id").is_not_null()).join(gkeys, on="game_id")
    pg = pg.select(
        pl.col("player_id").alias("pid"),
        "game_id",
        pl.col("team").alias("pg_team"),
        "targets",
        pl.col("receptions").alias("rec"),
        pl.col("receiving_yards").alias("rec_yds"),
        pl.col("receiving_tds").alias("rec_tds"),
        "carries",
        pl.col("rushing_yards").alias("rush_yds"),
        pl.col("rushing_tds").alias("rush_tds"),
        pl.col("attempts").alias("pass_att"),
        "completions",
        pl.col("passing_yards").alias("pass_yds"),
        pl.col("passing_tds").alias("pass_tds"),
        pl.col("passing_interceptions").alias("ints"),
        (
            pl.col("passing_epa").fill_null(0)
            + pl.col("rushing_epa").fill_null(0)
            + pl.col("receiving_epa").fill_null(0)
        ).alias("epa"),
        pl.col("target_share"),
        pl.col("def_sacks").alias("sacks"),
        pl.col("def_qb_hits").alias("qb_hits"),
        (pl.col("def_tackles_solo").fill_null(0) + pl.col("def_tackle_assists").fill_null(0))
        .cast(pl.Int32)
        .alias("tackles"),
        pl.col("def_interceptions").alias("def_ints"),
        pl.col("def_pass_defended").alias("passes_defended"),
    )
    sn = (
        inp.snaps.filter(pl.col("gsis_id").is_not_null())
        .join(gkeys, on="game_id")
        .with_columns(
            (
                pl.col("offense_snaps").fill_null(0)
                + pl.col("defense_snaps").fill_null(0)
                + pl.col("st_snaps").fill_null(0)
            ).alias("_total")
        )
        .sort("_total", descending=True)
        .unique(["gsis_id", "game_id"], keep="first")
        .select(
            pl.col("gsis_id").alias("pid"),
            "game_id",
            pl.col("team").alias("sn_team"),
            pl.col("offense_snaps").cast(pl.Int32).alias("off_snaps"),
            pl.col("defense_snaps").cast(pl.Int32).alias("def_snaps"),
            pl.col("st_snaps").cast(pl.Int32).alias("st_snaps"),
            pl.max_horizontal(
                pl.col("offense_pct").fill_null(0), pl.col("defense_pct").fill_null(0)
            ).alias("snap_pct"),
        )
    )
    out = pg.join(sn, on=["pid", "game_id"], how="full", coalesce=True)
    out = out.with_columns(
        pl.coalesce("pg_team", "sn_team").alias("team_id"),
        pl.when(pl.col("off_snaps").is_null() & pl.col("def_snaps").is_null())
        .then(None)
        .otherwise(pl.col("off_snaps").fill_null(0) + pl.col("def_snaps").fill_null(0))
        .cast(pl.Int32)
        .alias("snaps"),
    ).drop("pg_team", "sn_team")
    gmeta = inp.games.select(
        "game_id", pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    out = out.join(gmeta, on="game_id", how="left")
    if inp.pfr_def.height:
        pfr = inp.pfr_def.filter(pl.col("gsis_id").is_not_null())
        if key.mode == "backtest":
            # PFR for week W-1 isn't out on the Tuesday of week W (D44 / curated-data rule)
            late = inp.games.filter(
                (pl.col("season") == key.season) & (pl.col("week") == key.week - 1)
            ).select("game_id")
            pfr = pfr.join(late, on="game_id", how="anti")
        pr = pfr.group_by(pl.col("gsis_id").alias("pid"), "game_id").agg(
            pl.col("def_pressures").sum().cast(pl.Int32).alias("pressures")
        )
        out = out.join(pr, on=["pid", "game_id"], how="left")
    ngs_cols = {
        "receiving": ("avg_separation", "ngs_separation"),
        "passing": ("avg_time_to_throw", "ngs_time_to_throw"),
        "rushing": ("rush_yards_over_expected", "ngs_ryoe"),
    }
    for kind, (col, name) in ngs_cols.items():
        df = inp.ngs.get(kind, pl.DataFrame())
        if df.height and col in df.columns:
            n = (
                df.filter(pl.col("player_gsis_id").is_not_null())
                .select(
                    pl.col("player_gsis_id").alias("pid"),
                    pl.col("season").cast(pl.Int32),
                    pl.col("week").cast(pl.Int32),
                    pl.col(col).alias(name),
                )
                .unique(["pid", "season", "week"], keep="first")
            )
            out = out.join(n, on=["pid", "season", "week"], how="left")
    if starters.height:
        out = out.join(starters, on=["pid", "game_id"], how="left").with_columns(
            pl.col("qb_started").fill_null(False)
        )
    out = out.filter(pl.col("team_id").is_not_null())
    return _ends(out.sort("season", "week", "game_id", "pid"), "pid", "game_id")


def qb_game_stats(inp: GraphInputs, key: GraphKey) -> pl.DataFrame:
    """Per (player, game): QB dropbacks and whether he was the team's main QB (most
    dropbacks; `features.qb.game_starters`)."""
    from nflengine.features.qb import game_starters, qb_dropbacks

    schema = {
        "pid": pl.String,
        "game_id": pl.String,
        "dropbacks": pl.Int64,
        "qb_started": pl.Boolean,
    }
    if inp.plays.is_empty():
        return pl.DataFrame(schema=schema)
    db = qb_dropbacks(inp.plays.filter(pl.col("passer_id").is_not_null()))
    games = inp.games.filter(key.before() & pl.col("completed").fill_null(False))
    st = game_starters(games, db).select(
        "game_id", "team", pl.col("qb_id").alias("pid"), pl.lit(True).alias("qb_started")
    )
    out = (
        db.select("game_id", "team", pl.col("player_id").alias("pid"), "dropbacks")
        .join(st, on=["game_id", "team", "pid"], how="full", coalesce=True)
        .with_columns(pl.col("qb_started").fill_null(False), pl.col("dropbacks").fill_null(0))
        .group_by("pid", "game_id")
        .agg(pl.col("dropbacks").sum(), pl.col("qb_started").any())
    )
    return out.cast(schema)  # type: ignore[arg-type]


def played_in(inp: GraphInputs, key: GraphKey) -> pl.DataFrame:
    """(Team)-[:PLAYED_IN]->(Game) for every graph game (week W included, so queries find
    this week's opponent); results and EPA only for visible completed games."""
    g = inp.games.filter(key.in_window() & key.through())
    visible = key.before() & pl.col("completed").fill_null(False)
    seen = g.filter(visible).select("game_id")
    sides = []
    for side, other in (("home", "away"), ("away", "home")):
        sides.append(
            g.select(
                pl.col(f"{side}_team").alias("team_id"),
                "game_id",
                pl.col("season").cast(pl.Int32),
                pl.col("week").cast(pl.Int32),
                pl.lit(side == "home").alias("home"),
                pl.col(f"{other}_team").alias("opponent"),
                pl.when(visible).then(pl.col(f"{side}_score")).alias("points"),
                pl.when(visible).then(pl.col(f"{other}_score")).alias("points_allowed"),
            )
        )
    out = pl.concat(sides).with_columns(
        (pl.col("points") - pl.col("points_allowed")).alias("margin")
    )
    out = out.with_columns(
        pl.when(pl.col("margin") > 0)
        .then(pl.lit("W"))
        .when(pl.col("margin") < 0)
        .then(pl.lit("L"))
        .when(pl.col("margin") == 0)
        .then(pl.lit("T"))
        .alias("result")
    )
    if inp.plays.height:
        pp = inp.plays.join(seen, on="game_id").filter(
            pl.col("play_type").is_in(["pass", "run"])
            & ((pl.col("pass") == 1) | (pl.col("rush") == 1))
            & pl.col("epa").is_not_null()
            & (pl.col("two_point_attempt").fill_null(0) == 0)
        )
        off = pp.group_by("game_id", pl.col("posteam").alias("team_id")).agg(
            pl.col("epa").mean().alias("epa_per_play"),
            pl.col("success").mean().alias("success_rate"),
            pl.len().cast(pl.Int32).alias("plays"),
        )
        dfn = pp.group_by("game_id", pl.col("defteam").alias("team_id")).agg(
            pl.col("epa").mean().alias("epa_allowed"),
            pl.col("success").mean().alias("success_allowed"),
        )
        out = out.join(off, on=["game_id", "team_id"], how="left").join(
            dfn, on=["game_id", "team_id"], how="left"
        )
        out = out.with_columns((pl.col("epa_per_play") - pl.col("epa_allowed")).alias("epa_margin"))
        out = out.join(special_teams_epa(inp.plays, seen), on=["game_id", "team_id"], how="left")
    if inp.team_games.height:
        tg = (
            inp.team_games.join(seen, on="game_id")
            .select(
                "game_id",
                pl.col("team").alias("team_id"),
                pl.col("penalties").cast(pl.Int32),
                pl.col("penalty_yards").cast(pl.Int32),
            )
            .unique(["game_id", "team_id"], keep="first")
        )
        out = out.join(tg, on=["game_id", "team_id"], how="left")
    return _ends(out.sort("season", "week", "game_id", "team_id"), "team_id", "game_id")


def special_teams_epa(plays: pl.DataFrame, seen: pl.DataFrame) -> pl.DataFrame:
    """Net special-teams EPA per (game, team) over the visible games in `seen`: kickoffs,
    punts, field goals and extra points. Each play's EPA is posteam's (on a kickoff, the
    receiving team), so posteam gets +epa and defteam -epa; the two sides of a game sum to 0.
    Every play is first centered on the league's mean for its (season, play type) over the
    same visible plays (a kickoff averages about +0.25 EPA to the receiver since the 2024
    rule change, a field goal about +0.2 to the kicker), so a team isn't charged for simply
    kicking off more often (`st_epa` = EPA above an average unit). `st_plays` is its count."""
    schema = {
        "game_id": pl.String,
        "team_id": pl.String,
        "st_epa": pl.Float64,
        "st_plays": pl.Int32,
    }
    if plays.is_empty() or "play_type" not in plays.columns:
        return pl.DataFrame(schema=schema)
    st = plays.join(seen, on="game_id").filter(
        pl.col("play_type").is_in(ST_PLAY_TYPES)
        & pl.col("epa").is_not_null()
        & pl.col("posteam").is_not_null()
        & pl.col("defteam").is_not_null()
    )
    st = st.with_columns(
        (pl.col("epa") - pl.col("epa").mean().over("season", "play_type")).alias("_epa")
    )
    sides = pl.concat(
        [
            st.select("game_id", pl.col("posteam").alias("team_id"), "_epa"),
            st.select("game_id", pl.col("defteam").alias("team_id"), -pl.col("_epa")),
        ]
    )
    out = sides.group_by("game_id", "team_id").agg(
        pl.col("_epa").sum().alias("st_epa"), pl.len().cast(pl.Int32).alias("st_plays")
    )
    return out.cast(schema)  # type: ignore[arg-type]


def threw_to(inp: GraphInputs, key: GraphKey) -> pl.DataFrame:
    """(Player)-[:THREW_TO]->(Player): passer -> intended receiver, per season (all visible
    targets, playoffs included; sacks, two-point tries and penalties without a pass out)."""
    if inp.plays.is_empty():
        return pl.DataFrame(schema={"s": pl.String, "e": pl.String, "season": pl.Int32})
    t = inp.plays.filter(
        key.before()
        & (pl.col("pass_attempt").fill_null(0) == 1)
        & (pl.col("sack").fill_null(0) == 0)
        & (pl.col("two_point_attempt").fill_null(0) == 0)
        & (pl.col("play_type") == "pass")
        & pl.col("passer_player_id").is_not_null()
        & pl.col("receiver_player_id").is_not_null()
    )
    out = (
        t.sort("season", "week", "game_id", "play_id")
        .group_by("passer_player_id", "receiver_player_id", "season")
        .agg(
            pl.col("posteam").last().alias("team_id"),
            pl.col("game_id").n_unique().cast(pl.Int32).alias("games_together"),
            pl.len().cast(pl.Int32).alias("targets"),
            pl.col("complete_pass").fill_null(0).sum().cast(pl.Int32).alias("completions"),
            pl.col("receiving_yards").fill_null(0).sum().cast(pl.Int32).alias("yards"),
            pl.col("pass_touchdown").fill_null(0).sum().cast(pl.Int32).alias("tds"),
            pl.col("epa").fill_null(0).sum().alias("epa"),
        )
        .sort("season", "passer_player_id", "receiver_player_id")
    )
    return _ends(out, "passer_player_id", "receiver_player_id")


def officiated(inp: GraphInputs, key: GraphKey) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Official nodes + OFFICIATED (role). `officials.game_id` is the old GSIS-style id:
    it maps to `games.old_game_id`. Crews of completed games before the key; a live run also
    takes week-W crews when the snapshot has them (P08, Q9), a backtest never does: nflverse
    publishes a crew after the game, so no Tuesday run could have known it."""
    o = inp.officials
    if o.is_empty():
        return pl.DataFrame(schema={"official_id": pl.String, "name": pl.String}), pl.DataFrame(
            schema={"s": pl.String, "e": pl.String, "role": pl.String}
        )
    seen = key.before() & pl.col("completed").fill_null(False)
    if key.mode == "live":
        seen = seen | ((pl.col("season") == key.season) & (pl.col("week") == key.week))
    gmap = inp.games.filter(seen).select(pl.col("old_game_id").alias("ogid"), "game_id")
    rows = (
        o.filter(pl.col("official_id").is_not_null())
        .rename({"game_id": "ogid"})
        .join(gmap, on="ogid")
        .select(
            pl.col("official_id").cast(pl.String),
            pl.col("official_name").alias("name"),
            "game_id",
            pl.col("position").alias("role"),
        )
        .unique(["official_id", "game_id"], keep="first")
    )
    nodes = rows.group_by("official_id").agg(pl.col("name").last()).sort("official_id")
    rel = _ends(rows.select("official_id", "game_id", "role"), "official_id", "game_id")
    return nodes, rel


def injury_reports(inp: GraphInputs, key: GraphKey) -> pl.DataFrame:
    """(Player)-[:ON_INJURY_REPORT]->(Game): weekly reports with a game status or practice
    status, matched to the team's game that week. Week W's report only when modified by T."""
    inj = inp.injuries.filter(pl.col("gsis_id").is_not_null())
    if inj.is_empty():
        return pl.DataFrame(schema={"s": pl.String, "e": pl.String})
    cur = (pl.col("season") == key.season) & (pl.col("week") == key.week)
    seen = pl.col("date_modified").is_not_null() & (pl.col("date_modified") <= key.run_time)
    if key.mode == "live":
        # 2025+ reports carry no `date_modified`: a live run sees the snapshot it has; a
        # backtest (Tuesday) never sees an undated week-W report
        seen = seen | pl.col("date_modified").is_null()
    inj = inj.filter(key.before() | (cur & seen))
    games = pl.concat(
        [
            inp.games.select(
                "game_id",
                pl.col("season").cast(pl.Int32),
                pl.col("week").cast(pl.Int32),
                pl.col(f"{s}_team").alias("team"),
            )
            for s in ("home", "away")
        ]
    )
    out = inj.with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)).join(
        games, on=["season", "week", "team"]
    )
    out = out.select(
        "gsis_id",
        "game_id",
        pl.col("team").alias("team_id"),
        "season",
        "week",
        pl.col("report_status").alias("status"),
        "practice_status",
        pl.col("report_primary_injury").alias("body_part"),
        pl.col("date_modified").alias("report_date"),
    )
    out = out.sort("report_date", nulls_last=False, maintain_order=True).unique(
        ["gsis_id", "game_id"], keep="last", maintain_order=True
    )
    return _ends(out.sort("season", "week", "game_id", "gsis_id"), "gsis_id", "game_id")


def depth_chart(inp: GraphInputs, key: GraphKey) -> pl.DataFrame:
    """(Player)-[:DEPTH_CHART]->(Team) per (season, week, position), offense and defense
    slots only. Week W only from charts dated on or before the run date (D44: undated
    weekly charts for week W could be post-Tuesday, so a Tuesday backtest leaves them out)."""
    dc = inp.depth_charts.filter(
        pl.col("gsis_id").is_not_null()
        & pl.col("position").is_not_null()
        & (pl.col("position") != "")
        & ~pl.col("position").is_in(SPECIAL_SLOTS)
        & (pl.col("formation").fill_null("") != "Special Teams")
    )
    if dc.is_empty():
        return pl.DataFrame(schema={"s": pl.String, "e": pl.String})
    cur = (pl.col("season") == key.season) & (pl.col("week") == key.week)
    dated = pl.col("snap_date").is_not_null() & (pl.col("snap_date") <= key.run_date)
    dc = dc.filter(key.before() | (cur & dated))
    # one snapshot per team-week (the latest), then one row per player-slot
    dc = dc.filter(
        pl.col("snap_date").fill_null(dt.date.min)
        == pl.col("snap_date").fill_null(dt.date.min).max().over("season", "week", "team")
    )
    out = (
        dc.select(
            "gsis_id",
            "team",
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            "position",
            pl.col("depth_rank").cast(pl.Int32).alias("rank"),
        )
        .sort("rank")
        .unique(["gsis_id", "team", "season", "week", "position"], keep="first")
        .sort("season", "week", "team", "position", "rank", "gsis_id")
    )
    return _ends(out, "gsis_id", "team")


def drafted_by(inp: GraphInputs, key: GraphKey) -> pl.DataFrame:
    d = inp.draft_picks.filter(pl.col("gsis_id").is_not_null() & (pl.col("season") <= key.season))
    out = d.select(
        "gsis_id",
        "team",
        pl.col("season").cast(pl.Int32).alias("year"),
        pl.col("round").cast(pl.Int32),
        pl.col("pick").cast(pl.Int32),
    ).unique(["gsis_id"], keep="last")
    return _ends(out, "gsis_id", "team")


def traded_to(inp: GraphInputs, key: GraphKey) -> pl.DataFrame:
    """(Player)-[:TRADED_TO]->(Team) from trade rows naming a player (`pfr_id` -> gsis via
    `players.pfr_id`); `received` is the new team, `gave` the old one. Rows for a traded
    **draft pick** also carry the eventual draftee's `pfr_id` (the 2019 pick SF got from
    DEN became Dre Greenlaw): those have `pick_season` set and are left out."""
    t = inp.trades.filter(
        pl.col("pfr_id").is_not_null()
        & (pl.col("pfr_id") != "")
        & pl.col("pick_season").is_null()
        & pl.col("trade_date").is_not_null()
        & (pl.col("trade_date") <= key.run_date)
        & (pl.col("season") >= key.start_season)
    )
    ids = inp.players.filter(pl.col("pfr_id").is_not_null()).select("pfr_id", "gsis_id")
    out = (
        t.join(ids, on="pfr_id")
        .select(
            "gsis_id",
            pl.col("received").alias("team"),
            pl.col("trade_date").alias("date"),
            pl.col("gave").alias("from_team"),
            pl.col("season").cast(pl.Int32),
        )
        .unique(["gsis_id", "team", "date"], keep="first")
        .sort("date", "gsis_id")
    )
    return _ends(out, "gsis_id", "team")


def team_week_frames(inp: GraphInputs) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """TeamWeek nodes (key "KC:2026:4"), HAS_WEEK and the NEXT chain per team (as-of weeks
    in order, across seasons)."""
    tw = inp.team_weeks
    if tw.is_empty():
        empty = pl.DataFrame(schema={"s": pl.String, "e": pl.String})
        return pl.DataFrame(schema={"key": pl.String}), empty, empty
    nodes = tw.sort("team", "season", "week").select(
        pl.format("{}:{}:{}", "team", "season", "week").alias("key"),
        pl.col("team").alias("team_id"),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "off_epa",
        "def_epa",
        pl.col("net_epa").alias("net"),
        "off_pass_epa",
        "off_rush_epa",
        "def_pass_epa",
        "def_rush_epa",
        *[
            c
            for c in ("elo", "trend_delta", "perf_vs_expected", "net_epa_prev", "prior_weight")
            if c in tw.columns
        ],
        *([pl.col("direction").alias("trend_dir")] if "direction" in tw.columns else []),
    )
    has = nodes.select(pl.col("team_id").alias("s"), pl.col("key").alias("e"))
    nxt = nodes.select(
        pl.col("key").alias("s"),
        pl.col("key").shift(-1).over("team_id").alias("e"),
    ).filter(pl.col("e").is_not_null())
    return nodes, has, nxt


def prediction_frames(inp: GraphInputs) -> tuple[pl.DataFrame, pl.DataFrame]:
    """GamePrediction nodes for the week's saved predictions (both variants)."""
    p = inp.predictions
    if p.is_empty():
        return pl.DataFrame(schema={"key": pl.String}), pl.DataFrame(
            schema={"s": pl.String, "e": pl.String}
        )
    nodes = p.select(
        pl.format("{}|{}|{}", "game_id", "model_version", "variant").alias("key"),
        "game_id",
        "model_version",
        "variant",
        "is_primary",
        "home_win_prob",
        "expected_margin",
        pl.col("pred_home_points"),
        pl.col("pred_away_points"),
        pl.col("confidence_label").alias("confidence"),
        pl.col("created_at"),
    ).unique("key", keep="last")
    rel = nodes.select(pl.col("game_id").alias("s"), pl.col("key").alias("e"))
    return nodes, rel


def published_nodes(inp: GraphInputs, key: GraphKey) -> pl.DataFrame:
    """PublishedInsight nodes from the log, weeks strictly before the key (novelty)."""
    p = inp.published
    if p.is_empty():
        return pl.DataFrame(schema={"key": pl.String})
    p = p.filter(key.before())
    return p.select(
        pl.format("{}-w{}:{}", "season", "week", "insight_id").alias("key"),
        "insight_id",
        pl.col("insight_type").alias("type"),
        "entities",
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "published_at",
    ).unique("key", keep="last")


# ---- everything ---------------------------------------------------------------------------------


def build_tables(inp: GraphInputs, key: GraphKey) -> GraphTables:
    """All node and relationship frames for one as-of key (pure: no Neo4j)."""
    from nflengine.graph import tables_extra as X

    gt = GraphTables(key)
    teams = team_nodes(inp)
    games = game_nodes(inp, key)
    starters = qb_game_stats(inp, key)
    appear = appeared_in(inp, key, starters)
    style = X.qb_style_stats(inp.plays)
    if style.height:
        appear = appear.join(style.rename({"pid": "s", "game_id": "e"}), on=["s", "e"], how="left")
    pf = played_for(inp, key, appear)
    thr = threw_to(inp, key)
    inj = injury_reports(inp, key)
    dc = depth_chart(inp, key)
    drafted = drafted_by(inp, key)
    traded = traded_to(inp, key)
    officials, off_rel = officiated(inp, key)
    coaches, hc, ci = coach_frames(inp, key)
    tw, has_week, nxt = team_week_frames(inp)
    preds, has_pred = prediction_frames(inp)

    person_ids = pl.concat(
        [
            appear["s"],
            pf["s"],
            thr["s"],
            thr["e"],
            inj["s"] if inj.height else pl.Series("s", [], pl.String),
            dc["s"] if dc.height else pl.Series("s", [], pl.String),
        ]
    )
    players = player_nodes(inp, person_ids, scramble_rates(inp.plays), key)
    profiles, has_profile = X.usage_profiles(inp, key, players)
    seed_coaches, coord, under = X.coaching_seed_frames(inp, key, hc)
    if seed_coaches.height:
        coaches = pl.concat(
            [coaches, seed_coaches.join(coaches.select("coach_id"), on="coach_id", how="anti")]
        ).sort("coach_id")
    played = played_in(inp, key)
    pa = X.play_action_rates(inp, key)
    if pa.height:
        played = played.join(pa.rename({"team_id": "s", "game_id": "e"}), on=["s", "e"], how="left")
    venues = venue_nodes(inp, games)
    at = _ends(
        inp.venues.join(games.select("game_id"), on="game_id").filter(
            pl.col("stadium_id").is_not_null()
        ),
        "game_id",
        "stadium_id",
    )

    gt.nodes = {
        "Team": teams,
        "Venue": venues,
        "Coach": coaches,
        "Official": officials,
        "Player": players,
        "Game": games,
        "TeamWeek": tw,
        "GamePrediction": preds,
        "PublishedInsight": published_nodes(inp, key),
        "UsageProfile": profiles,
    }
    keys = {label: df[NODE_KEYS[label]] for label, df in gt.nodes.items() if df.height}
    empty = pl.Series("k", [], pl.String)
    raw = {
        "PLAYED_FOR": pf,
        "APPEARED_IN": appear,
        "PLAYED_IN": played,
        "AT": at,
        "HEAD_COACH_OF": hc,
        "COACHED_IN": ci,
        "OFFICIATED": off_rel,
        "THREW_TO": thr,
        "ON_INJURY_REPORT": inj,
        "DEPTH_CHART": dc,
        "DRAFTED_BY": drafted,
        "TRADED_TO": traded,
        "HAS_WEEK": has_week,
        "NEXT": nxt,
        "HAS_PREDICTION": has_pred,
        "COORDINATOR_OF": coord,
        "WORKED_UNDER": under,
        "HAS_PROFILE": has_profile,
    }
    for name, df in raw.items():
        start, end = REL_ENDS[name]
        if df.is_empty():
            gt.rels[name] = df
            gt.dropped[name] = 0
            continue
        gt.rels[name] = _keep_known(
            df, keys.get(start, empty), keys.get(end, empty), name, gt.dropped
        )
    return gt
