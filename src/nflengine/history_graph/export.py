"""Curated data + research participation -> one frame per node label and relationship type ->
CSVs for the bulk import (AE02). Pure Polars over files (read-only; the curated tables and the
research folder are never written). The weekly append (`append.py`) uses the same frames for
one season, so a bulk-imported season and an appended one hold the same things.

What comes from where (schema.py has every property):
- **Plays, drives, roles:** curated `plays/season=S.parquet` (rows with a `play_type`).
- **FTN labels** (2022 on): curated `ftn_plays`, through the Play calling build's own rules
  (`playcalling.build._ftn_labels`: FTN's placeholder rows count as not charted, PC00).
- **Participation** (research, 2018-2025): `research/nflverse/participation`, the newest
  snapshot per season (`playcalling.participation`); its labels through PC00's parser, its
  player lists exploded into `ON_FIELD`. A 2018-2022 snap has no per-play positions: the
  player's roster position that week (else his `players` position) stands in.
- **Games, teams, players, rosters, box scores:** curated `games` (head coaches with
  `config/head_coach_fixes.csv`, as the weekly graph and Ask's SQL do), `teams`, `players`,
  `rosters_weekly`, `player_games`.
- **Coordinators:** `config/coaching_seed.csv` (D86).

Rows whose endpoints aren't nodes are dropped and counted (`HistoryFrames.dropped`), as the
weekly graph's tables do; the bulk import itself refuses a dangling relationship.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.history_graph import schema as S
from nflengine.paths import DataPaths

# participation's first season on D: (research; 2016 on). No upper bound: a season loads
# whenever its file exists (2026's arrives about February 2027), and a missing one is noted
PART_FIRST = 2016
FTN_FIRST = 2022

ROLE_COLUMNS: dict[str, list[tuple[str, dict[str, Any]]]] = {
    # role type -> [(plays column, the relationship's properties)], first listed wins a tie
    "PASSED": [("passer_player_id", {})],
    "RUSHED": [("rusher_player_id", {})],
    "TARGETED": [("receiver_player_id", {})],
    "CAUGHT": [("receiver_player_id", {})],
    "INTERCEPTED": [("interception_player_id", {})],
    "SACKED": [
        ("sack_player_id", {"half": False}),
        ("half_sack_1_player_id", {"half": True}),
        ("half_sack_2_player_id", {"half": True}),
    ],
    "TACKLED": [
        ("solo_tackle_1_player_id", {"kind": "solo"}),
        ("solo_tackle_2_player_id", {"kind": "solo"}),
        ("tackle_with_assist_1_player_id", {"kind": "with_assist"}),
        ("tackle_with_assist_2_player_id", {"kind": "with_assist"}),
        ("assist_tackle_1_player_id", {"kind": "assist"}),
        ("assist_tackle_2_player_id", {"kind": "assist"}),
        ("assist_tackle_3_player_id", {"kind": "assist"}),
        ("assist_tackle_4_player_id", {"kind": "assist"}),
    ],
}
ID_COLUMNS = sorted({c for cols in ROLE_COLUMNS.values() for c, _ in cols})
FLOAT_COLUMNS = (
    "play_id", "qtr", "game_seconds_remaining", "order_sequence", "down", "ydstogo",
    "yardline_100", "goal_to_go", "score_differential", "wp", "yards_gained", "epa", "success",
    "first_down", "touchdown", "penalty", "two_point_attempt", "pass", "sack", "qb_scramble",
    "complete_pass", "interception", "fumble_lost", "air_yards", "yards_after_catch", "shotgun",
    "no_huddle", "fixed_drive", "drive_play_count", "drive_first_downs", "drive_quarter_start",
    "drive_ended_with_score",
)  # fmt: skip
STRING_COLUMNS = (
    "game_id", "season_type", "time", "posteam", "defteam", "play_type", "desc", "pass_length",
    "pass_location", "run_location", "run_gap", "fixed_drive_result", "drive_start_yard_line",
    "drive_end_yard_line", "drive_time_of_possession", *ID_COLUMNS,
)  # fmt: skip
INT_COLUMNS = ("season", "week")


@dataclass
class HistoryInputs:
    seasons: list[int]
    plays: pl.DataFrame
    games: pl.DataFrame
    teams: pl.DataFrame
    players: pl.DataFrame
    rosters: pl.DataFrame
    player_games: pl.DataFrame
    ftn: pl.DataFrame  # FTN labels per (game_id, play_id)
    ftn_flags: pl.DataFrame  # qb_sneak, drop, throw_away, out_of_pocket per charted play
    part: pl.DataFrame  # participation labels per (game_id, play_id)
    on_field: pl.DataFrame  # game_id, play_id, side, player_id, position (may be null)
    seed: pl.DataFrame  # coaching seed rows: coach, team, season, role
    notes: list[str] = field(default_factory=list)


@dataclass
class HistoryFrames:
    nodes: dict[str, pl.DataFrame]
    rels: dict[str, pl.DataFrame]
    dropped: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def expected_counts(self) -> dict[str, int]:
        """`{"Play": n, ..., "ON_FIELD": n, ...}`: what Neo4j must hold after an import."""
        return {
            **{k: v.height for k, v in self.nodes.items()},
            **{k: v.height for k, v in self.rels.items()},
        }


# ---- reading ----------------------------------------------------------------------------------


def _read_plays(cur: Path, season: int, through_week: int | None) -> pl.DataFrame:
    f = cur / "plays" / f"season={season}.parquet"
    if not f.exists():
        raise FileNotFoundError(f"no curated plays for {season} ({f.name})")
    scan = pl.scan_parquet(f.as_posix())
    have = scan.collect_schema()
    want = [*STRING_COLUMNS, *FLOAT_COLUMNS, *INT_COLUMNS]
    df = (
        scan.select([c for c in want if c in have])
        .with_columns(pl.lit(None).alias(c) for c in want if c not in have)
        .with_columns(
            *(pl.col(c).cast(pl.String) for c in STRING_COLUMNS),
            *(pl.col(c).cast(pl.Float64) for c in FLOAT_COLUMNS),
            *(pl.col(c).cast(pl.Int32) for c in INT_COLUMNS),
        )
        # a play has a type and an offense: timeouts are `no_play` rows without one (~2,000 a
        # season), and the rows without a type are game / quarter markers
        .filter(
            pl.col("play_type").is_not_null()
            & pl.col("posteam").is_not_null()
            & pl.col("play_id").is_not_null()
        )
    )
    if through_week is not None:
        df = df.filter(pl.col("week") <= through_week)
    return df.with_columns(clean_id(c) for c in ID_COLUMNS).collect()


def load_inputs(
    paths: DataPaths,
    seasons: Iterable[int],
    *,
    through_week: int | None = None,
    participation: bool = True,
    log: Callable[[str], None] = print,
) -> HistoryInputs:
    """Everything the frames need for `seasons` (`through_week` limits the last season: the
    weekly append). Participation is read only for 2018-2025 and only when asked."""
    seasons = sorted(set(seasons))
    if not seasons:
        raise ValueError("no seasons")
    cur, notes = paths.curated, []
    last = seasons[-1]
    plays = pl.concat(
        [_read_plays(cur, s, through_week if s == last else None) for s in seasons],
        how="vertical_relaxed",
    )
    games = pl.read_parquet(cur / "games.parquet", memory_map=False).filter(
        pl.col("season").is_in(seasons)
    )
    from nflengine.graph.tables_extra import apply_coach_fixes, read_coach_fixes

    games = apply_coach_fixes(games, read_coach_fixes(log=log))
    teams = pl.read_parquet(cur / "teams.parquet", memory_map=False)
    players = pl.read_parquet(
        cur / "players.parquet",
        columns=["gsis_id", "display_name", "position", "position_group"],
        memory_map=False,
    )
    rosters = pl.read_parquet(
        cur / "rosters_weekly.parquet",
        columns=["season", "week", "team", "gsis_id", "status", "position", "full_name"],
        memory_map=False,
    ).with_columns(clean_id("gsis_id"), clean_id("team"))
    rosters = rosters.filter(pl.col("season").is_in(seasons) & pl.col("gsis_id").is_not_null())
    if through_week is not None:
        rosters = rosters.filter((pl.col("season") < last) | (pl.col("week") <= through_week))
    pg = (
        pl.read_parquet(
            cur / "player_games.parquet",
            columns=["player_id", "game_id", "team", "season", "week"],
            memory_map=False,
        )
        .filter(pl.col("season").is_in(seasons))
        .with_columns(clean_id("player_id"))
    )
    if through_week is not None:  # PLAYED_FOR.games counts only the weeks kept (Sol: a rewind)
        pg = pg.filter((pl.col("season") < last) | (pl.col("week") <= through_week))

    ftn = pl.DataFrame()
    ftn_flags = pl.DataFrame()
    ftn_seasons = [s for s in seasons if s >= FTN_FIRST]
    ftn_path = cur / "ftn_plays.parquet"
    if ftn_seasons and ftn_path.exists():
        from nflengine.playcalling.build import _ftn_labels

        raw = (
            pl.read_parquet(ftn_path, memory_map=False)
            .filter(pl.col("season").is_in(ftn_seasons))
            .with_columns(
                pl.col("nflverse_game_id").alias("game_id"),
                pl.col("nflverse_play_id").cast(pl.Float64).alias("play_id"),
            )
            .unique(["game_id", "play_id"], keep="first", maintain_order=True)
        )
        ftn = _ftn_labels(raw)
        charted = ftn.select("game_id", "play_id")  # the placeholder rows are dropped there
        flags = {"is_qb_sneak": "qb_sneak", "is_drop": "drop", "is_throw_away": "throw_away",
                 "is_qb_out_of_pocket": "out_of_pocket"}  # fmt: skip
        have = [c for c in flags if c in raw.columns]
        ftn_flags = raw.join(charted, on=["game_id", "play_id"], how="semi").select(
            pl.col("game_id"),
            pl.col("play_id").cast(pl.Int64),
            *(pl.col(c).cast(pl.Boolean).alias(flags[c]) for c in have),
        )
    elif ftn_seasons:
        notes.append("ftn_plays missing: no FTN labels")

    part = pl.DataFrame()
    on_field = pl.DataFrame()
    part_seasons = [s for s in seasons if s >= PART_FIRST]
    if participation and part_seasons:
        from nflengine.playcalling.participation import load_participation, parse_participation

        raw = load_participation(paths, part_seasons)
        if raw.height:
            raw = raw.with_columns(pl.col("play_id").cast(pl.Float64))
            part = parse_participation(raw)
            on_field = _explode_on_field(raw)
        missing = sorted(set(part_seasons) - set(raw["season"].unique().to_list()) if raw.height
                         else part_seasons)  # fmt: skip
        if missing:
            notes.append(f"participation missing for {missing}")

    from nflengine.graph.tables_extra import read_coaching_seed

    seed = read_coaching_seed(log=log).filter(pl.col("season").is_in(seasons))
    for n in notes:
        log(f"[yellow]history graph: {n}[/]")
    return HistoryInputs(
        seasons=seasons,
        plays=plays,
        games=games,
        teams=teams,
        players=players,
        rosters=rosters,
        player_games=pg,
        ftn=ftn,
        ftn_flags=ftn_flags,
        part=part,
        on_field=on_field,
        seed=seed,
        notes=notes,
    )


def _explode_on_field(raw: pl.DataFrame) -> pl.DataFrame:
    """One row per (play, player) from participation's `;`-joined lists. The position lists
    exist from 2023 (FTN era); before that the position is null here."""
    frames = []
    for side in ("offense", "defense"):
        pos = f"{side}_positions"
        has_pos = pos in raw.columns
        x = raw.select(
            pl.col("nflverse_game_id").alias("game_id"),
            pl.col("play_id"),
            pl.col(f"{side}_players").str.split(";").alias("player_id"),
            (
                pl.col(pos).str.split(";") if has_pos else pl.lit(None, dtype=pl.List(pl.String))
            ).alias("position"),
        )
        # positions only where the two lists line up (they always have: 2023-2025, AE02 probe)
        x = x.with_columns(
            pl.when(pl.col("position").list.len() == pl.col("player_id").list.len())
            .then(pl.col("position"))
            .otherwise(pl.col("player_id").list.eval(pl.lit(None, dtype=pl.String)))
            .alias("position")
        )
        x = x.explode("player_id", "position", empty_as_null=True).with_columns(
            pl.lit(side).alias("side")
        )
        frames.append(x)
    out = pl.concat(frames, how="vertical_relaxed")
    return out.with_columns(clean_id("player_id"), clean_id("position")).filter(
        pl.col("player_id").is_not_null()
    )


# ---- frames -----------------------------------------------------------------------------------


def clean_id(col: str) -> pl.Expr:
    """An id column stripped, blank -> null (rosters_weekly has a whitespace GSIS id; an empty
    key would break the bulk import: "missing START_ID field", AE02 smoke build)."""
    s = pl.col(col).cast(pl.String).str.strip_chars()
    return pl.when(s == "").then(None).otherwise(s).alias(col)


def pass_attempt() -> pl.Expr:
    """A pass thrown, as the box score counts it: passes that weren't sacks, and spikes (an
    incomplete attempt: 75 in 2024, which the first build's check showed missing, AE02)."""
    return pl.col("play_type").is_in(["pass", "qb_spike"]) & ~_flag("sack")


def _flag(col: str) -> pl.Expr:
    return (pl.col(col) == 1).fill_null(False)


def play_key(game_id: pl.Expr, play_id: pl.Expr) -> pl.Expr:
    return game_id + pl.lit(":") + play_id.cast(pl.Int64).cast(pl.String)


def _plays_frame(inp: HistoryInputs) -> pl.DataFrame:
    p = inp.plays
    attempt = pass_attempt()
    run = pl.col("play_type") == "run"
    out = p.select(
        play_key(pl.col("game_id"), pl.col("play_id")).alias("play_key"),
        pl.col("game_id"),
        pl.col("play_id").cast(pl.Int64),
        pl.col("season").cast(pl.Int64),
        pl.col("week").cast(pl.Int64),
        pl.col("season_type"),
        pl.col("qtr").cast(pl.Int64).alias("quarter"),
        pl.col("time").alias("clock"),
        pl.col("game_seconds_remaining").cast(pl.Int64).alias("seconds_left"),
        pl.col("order_sequence").alias("order"),
        pl.col("posteam").alias("offense"),
        pl.col("defteam").alias("defense"),
        pl.col("down").cast(pl.Int64),
        pl.col("ydstogo").cast(pl.Int64).alias("distance"),
        pl.col("yardline_100").cast(pl.Int64).alias("yards_to_goal"),
        _flag("goal_to_go").alias("goal_to_go"),
        pl.col("score_differential").cast(pl.Int64).alias("score_diff"),
        pl.col("wp"),
        pl.col("play_type"),
        pl.col("desc").str.replace_all(r"[\r\n\t]+", " ").alias("description"),
        pl.col("yards_gained").cast(pl.Int64).alias("yards"),
        pl.col("epa"),
        pl.when(pl.col("success").is_null())
        .then(None)
        .otherwise(_flag("success"))
        .alias("success"),
        _flag("first_down").alias("first_down"),
        _flag("touchdown").alias("touchdown"),
        _flag("penalty").alias("penalty"),
        _flag("two_point_attempt").alias("two_point"),
        _flag("pass").alias("dropback"),
        attempt.alias("pass_attempt"),
        _flag("sack").alias("sack"),
        _flag("qb_scramble").alias("scramble"),
        pl.when(attempt).then(_flag("complete_pass")).alias("complete"),
        _flag("interception").alias("interception"),
        _flag("fumble_lost").alias("fumble_lost"),
        pl.when(attempt).then(pl.col("air_yards")).alias("air_yards"),
        pl.col("yards_after_catch"),
        pl.when(attempt).then(pl.col("pass_length")).alias("pass_length"),
        pl.when(attempt).then(pl.col("pass_location")).alias("pass_location"),
        pl.when(run).then(pl.col("run_location")).alias("run_location"),
        pl.when(run).then(pl.col("run_gap")).alias("run_gap"),
        pl.when(pl.col("shotgun").is_null())
        .then(None)
        .otherwise(_flag("shotgun"))
        .alias("shotgun"),
        pl.when(pl.col("no_huddle").is_null())
        .then(None)
        .otherwise(_flag("no_huddle"))
        .alias("no_huddle"),
    )
    keys = {"game_id": pl.String, "play_id": pl.Int64}
    if not inp.ftn.is_empty():
        ftn = inp.ftn.select(
            pl.col("game_id"),
            pl.col("play_id").cast(pl.Int64),
            pl.col("ftn_charted").alias("ftn"),
            "play_action", "screen", "rpo", "motion",
            pl.col("blitzers").cast(pl.Int64),
            pl.col("rushers").cast(pl.Int64),
            "blitz",
            pl.col("box").cast(pl.Int64),
            "qb_alignment",
        )  # fmt: skip
        out = out.join(ftn, on=list(keys), how="left")
        if not inp.ftn_flags.is_empty():  # FTN flags PC00's labels don't carry (AE02 eval)
            out = out.join(inp.ftn_flags, on=list(keys), how="left")
    if not inp.part.is_empty():
        part = inp.part.select(
            pl.col("game_id"),
            pl.col("play_id").cast(pl.Int64),
            pl.lit(True).alias("part"),
            "formation", "personnel", "extra_ol", "def_package", "coverage", "man_zone",
            "target_route", "pressure", "time_to_throw",
            pl.col("part_box").cast(pl.Int64),
            pl.col("part_rushers").cast(pl.Int64),
        )  # fmt: skip
        out = out.join(part, on=list(keys), how="left")
    out = _conform(out, S.NODES["Play"])
    # a play keeps the FTN / participation flags only where the source had it
    return out.with_columns(
        pl.when(pl.col("ftn")).then(True).alias("ftn"),
        pl.when(pl.col("part")).then(True).alias("part"),
    ).unique("play_key", keep="first", maintain_order=True)


_POLARS = {
    S.LONG: pl.Int64,
    S.DOUBLE: pl.Float64,
    S.BOOL: pl.Boolean,
    S.STRING: pl.String,
    S.LONGS: pl.List(pl.Int64),
    S.STRINGS: pl.List(pl.String),
}


def _conform(df: pl.DataFrame, spec: S.NodeSpec | S.RelSpec) -> pl.DataFrame:
    """The spec's columns in its order with its types (missing ones null)."""
    cols = spec.columns()
    types = (
        {spec.key: S.key_type(spec.label), **spec.props}
        if isinstance(spec, S.NodeSpec)
        else {"s": None, "e": None, **spec.props}
    )
    exprs = []
    for c in cols:
        t = types.get(c)
        dtype = None if t is None else _POLARS[t]
        if dtype is None:  # a relationship's endpoint keys: text, or Season's number
            exprs.append(pl.col(c) if c in df.columns else pl.lit(None).alias(c))
        elif c in df.columns:
            exprs.append(pl.col(c).cast(dtype))
        else:
            exprs.append(pl.lit(None, dtype=dtype).alias(c))
    return df.select(exprs)


def _drives_frame(inp: HistoryInputs) -> pl.DataFrame:
    p = inp.plays.filter(pl.col("fixed_drive").is_not_null()).sort(
        "game_id", "fixed_drive", "order_sequence", "play_id"
    )
    # the offense: the first play's team that isn't a kickoff (a drive starts with the kick
    # that the receiving team "possesses" in nflverse; 73 of 6,124 drives in 2024 change hands)
    team = (
        p.filter(pl.col("play_type") != "kickoff")
        .group_by("game_id", "fixed_drive", maintain_order=True)
        .agg(pl.col("posteam").drop_nulls().first().alias("team"))
    )
    agg = p.group_by("game_id", "fixed_drive", maintain_order=True).agg(
        pl.col("season").first(),
        pl.col("week").first(),
        pl.col("posteam").drop_nulls().first().alias("team_any"),
        pl.col("fixed_drive_result").drop_nulls().first().alias("result"),
        pl.col("drive_play_count").drop_nulls().first().alias("plays"),
        pl.col("drive_first_downs").drop_nulls().first().alias("first_downs"),
        pl.col("drive_quarter_start").drop_nulls().first().alias("quarter"),
        pl.col("drive_start_yard_line").drop_nulls().first().alias("start"),
        pl.col("drive_end_yard_line").drop_nulls().first().alias("end"),
        pl.col("drive_time_of_possession").drop_nulls().first().alias("time_of_possession"),
        pl.col("drive_ended_with_score").drop_nulls().first().alias("scored"),
    )
    d = agg.join(team, on=["game_id", "fixed_drive"], how="left").with_columns(
        (pl.col("game_id") + ":" + pl.col("fixed_drive").cast(pl.Int64).cast(pl.String)).alias(
            "drive_key"
        ),
        pl.col("fixed_drive").cast(pl.Int64).alias("drive"),
        pl.coalesce("team", "team_any").alias("team"),
        (pl.col("scored") == 1).alias("scored"),
    )
    return _conform(d, S.NODES["Drive"])


def _games_frame(inp: HistoryInputs, game_ids: set[str]) -> pl.DataFrame:
    g = inp.games.filter(pl.col("game_id").is_in(list(game_ids))).with_columns(
        (pl.col("overtime") == 1).alias("overtime"),
        pl.col("gameday").cast(pl.String),
    )
    return _conform(g, S.NODES["Game"]).unique("game_id", keep="first", maintain_order=True)


def _teams_frame(inp: HistoryInputs) -> pl.DataFrame:
    t = inp.teams.select(
        pl.col("team").alias("team_id"),
        pl.col("team_name").alias("name"),
        pl.col("team_nick").alias("nickname"),
        pl.col("team_conf").alias("conference"),
        pl.col("team_division").alias("division"),
    )
    return _conform(t, S.NODES["Team"]).unique("team_id", keep="first", maintain_order=True)


def _role_frames(inp: HistoryInputs) -> dict[str, pl.DataFrame]:
    """The pbp roles, only on plays that count in the box score (no `no_play`, no two-point
    tries); one relationship per (player, play, type), the first listed column winning."""
    p = inp.plays.filter(
        (pl.col("play_type") != "no_play") & ~_flag("two_point_attempt")
    ).with_columns(play_key(pl.col("game_id"), pl.col("play_id")).alias("play_key"))
    attempt = pass_attempt()
    conds = {
        "PASSED": ~_flag("qb_scramble"),
        "TARGETED": attempt,
        "CAUGHT": attempt & _flag("complete_pass"),
    }
    out: dict[str, pl.DataFrame] = {}
    for rtype, cols in ROLE_COLUMNS.items():
        spec = S.RELATIONSHIPS[rtype]
        q = p.filter(conds[rtype]) if rtype in conds else p
        parts = []
        for i, (col, props) in enumerate(cols):
            x = q.filter(pl.col(col).is_not_null() & (pl.col(col) != "")).select(
                pl.col(col).alias("s"),
                pl.col("play_key").alias("e"),
                pl.lit(i).alias("_rank"),
                *(pl.lit(v).alias(k) for k, v in props.items()),
            )
            parts.append(x)
        df = pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame()
        df = df.sort("_rank").unique(["s", "e"], keep="first", maintain_order=True)
        out[rtype] = _conform(df, spec)
    return out


def _on_field_frame(inp: HistoryInputs, plays: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """ON_FIELD from participation; (frame, participation rows of plays we don't have)."""
    spec = S.RELATIONSHIPS["ON_FIELD"]
    if inp.on_field.is_empty():
        empty = pl.DataFrame(schema={"s": pl.String, "e": pl.String})
        return _conform(empty, spec), 0
    of = inp.on_field.with_columns(play_key(pl.col("game_id"), pl.col("play_id")).alias("e"))
    known = plays.select(pl.col("play_key").alias("e"), "season", "week")
    joined = of.join(known, on="e", how="inner")
    lost = of.select("e").unique().join(known.select("e"), on="e", how="anti").height
    # 2018-2022: the roster position that week, else the player's listed position
    roster_pos = (
        inp.rosters.select(
            pl.col("gsis_id").alias("s"),
            pl.col("season").cast(pl.Int64),
            pl.col("week").cast(pl.Int64),
            pl.col("position").alias("roster_position"),
        )
        .drop_nulls("roster_position")
        .unique(["s", "season", "week"], keep="last", maintain_order=True)
    )
    listed = inp.players.select(pl.col("gsis_id").alias("s"), pl.col("position").alias("listed"))
    df = (
        joined.rename({"player_id": "s"})
        .join(roster_pos, on=["s", "season", "week"], how="left")
        .join(listed, on="s", how="left")
        .with_columns(pl.coalesce("position", "roster_position", "listed").alias("position"))
        .unique(["s", "e"], keep="first", maintain_order=True)
    )
    return _conform(df, spec), lost


def _played_for_frame(inp: HistoryInputs) -> pl.DataFrame:
    r = inp.rosters.filter(~pl.col("status").is_in(S.NOT_ON_TEAM) & pl.col("team").is_not_null())
    r = r.sort("season", "week")
    games = inp.player_games.group_by("player_id", "team", "season").agg(
        pl.col("game_id").n_unique().alias("games")
    )
    pf = (
        r.group_by("gsis_id", "team", "season", maintain_order=True)
        .agg(
            pl.col("week").unique().sort().alias("weeks"),
            pl.col("week").min().alias("first_week"),
            pl.col("week").max().alias("last_week"),
            pl.col("status").last().alias("status_last"),
        )
        .join(
            games.rename({"player_id": "gsis_id"}),
            on=["gsis_id", "team", "season"],
            how="left",
        )
        .with_columns(pl.col("games").fill_null(0))
        .rename({"gsis_id": "s", "team": "e"})
    )
    return _conform(pf, S.RELATIONSHIPS["PLAYED_FOR"])


def _coach_frames(
    inp: HistoryInputs, games: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, pl.DataFrame]]:
    from nflengine.graph.tables import slug

    sides = pl.concat(
        [
            games.select(
                "season", "week", "game_id",
                pl.col(f"{side}_team").alias("team"),
                pl.col(f"{side}_coach").alias("name"),
            )
            for side in ("home", "away")
        ]
    ).filter(pl.col("name").is_not_null() & (pl.col("name").str.strip_chars() != ""))  # fmt: skip
    sides = sides.with_columns(
        pl.col("name").map_elements(slug, return_dtype=pl.String).alias("coach_id")
    ).filter(pl.col("coach_id").is_not_null() & (pl.col("coach_id") != ""))
    hc = (
        sides.group_by("coach_id", "team", "season")
        .agg(
            pl.col("game_id").n_unique().alias("games"),
            pl.col("week").min().alias("first_week"),
            pl.col("week").max().alias("last_week"),
        )
        .rename({"coach_id": "s", "team": "e"})
        .sort("season", "e", "s")
    )
    rels = {"HEAD_COACH": _conform(hc, S.RELATIONSHIPS["HEAD_COACH"])}
    seed = inp.seed.filter(pl.col("role").is_in(list(S.SEED_ROLES)))
    seed = seed.with_columns(
        pl.col("coach").map_elements(slug, return_dtype=pl.String).alias("coach_id")
    ).filter(pl.col("coach_id").is_not_null() & (pl.col("coach_id") != ""))
    for role, rtype in S.SEED_ROLES.items():
        x = (
            seed.filter(pl.col("role") == role)
            .select(pl.col("coach_id").alias("s"), pl.col("team").alias("e"), "season")
            .unique(maintain_order=True)
        )
        rels[rtype] = _conform(x, S.RELATIONSHIPS[rtype])
    names = pl.concat(
        [
            sides.select("coach_id", "name", "season"),
            seed.select("coach_id", pl.col("coach").alias("name"), "season"),
        ],
        how="vertical_relaxed",
    )
    nodes = names.sort("season").group_by("coach_id").agg(pl.col("name").last())
    return _conform(nodes, S.NODES["Coach"]).sort("coach_id"), rels


def _players_frame(inp: HistoryInputs, ids: pl.Series) -> pl.DataFrame:
    want = pl.DataFrame({"player_id": ids.unique().drop_nulls()})
    listed = inp.players.select(
        pl.col("gsis_id").alias("player_id"),
        pl.col("display_name").alias("name"),
        "position",
        "position_group",
    ).unique("player_id", keep="first", maintain_order=True)
    roster = (
        inp.rosters.sort("season", "week")
        .group_by("gsis_id")
        .agg(pl.col("full_name").drop_nulls().last(), pl.col("position").drop_nulls().last())
        .rename({"gsis_id": "player_id", "full_name": "r_name", "position": "r_position"})
    )
    df = (
        want.join(listed, on="player_id", how="left")
        .join(roster, on="player_id", how="left")
        .with_columns(
            pl.coalesce("name", "r_name").alias("name"),
            pl.coalesce("position", "r_position").alias("position"),
        )
    )
    return _conform(df, S.NODES["Player"]).sort("player_id")


def _keep_known(rel: pl.DataFrame, starts: pl.Series, ends: pl.Series) -> tuple[pl.DataFrame, int]:
    ok = rel.filter(pl.col("s").is_in(starts.implode()) & pl.col("e").is_in(ends.implode()))
    return ok, rel.height - ok.height


def build_frames(inp: HistoryInputs, log: Callable[[str], None] = print) -> HistoryFrames:
    """Every node and relationship frame for `inp`'s seasons (schema.py's columns and types)."""
    t0 = time.monotonic()
    plays = _plays_frame(inp)
    drives = _drives_frame(inp)
    games = _games_frame(inp, set(plays["game_id"].unique().to_list()))
    teams = _teams_frame(inp)
    seasons = _conform(
        pl.DataFrame({"season": sorted(games["season"].unique().to_list())}), S.NODES["Season"]
    )
    roles = _role_frames(inp)
    on_field, part_lost = _on_field_frame(inp, plays)
    played_for = _played_for_frame(inp)
    coaches, coach_rels = _coach_frames(inp, inp.games.filter(pl.col("game_id").is_in(
        games["game_id"].implode())))  # fmt: skip

    ids = pl.concat(
        [
            *(r["s"] for r in roles.values()),
            on_field["s"],
            played_for["s"],
        ]
    )
    players = _players_frame(inp, ids)

    rels: dict[str, pl.DataFrame] = {
        "IN_SEASON": games.select(pl.col("game_id").alias("s"), pl.col("season").alias("e")),
        "HOME_TEAM": games.select(pl.col("game_id").alias("s"), pl.col("home_team").alias("e")),
        "AWAY_TEAM": games.select(pl.col("game_id").alias("s"), pl.col("away_team").alias("e")),
        "IN_GAME": drives.select(pl.col("drive_key").alias("s"), pl.col("game_id").alias("e")),
        "IN_DRIVE": inp.plays.select(
            play_key(pl.col("game_id"), pl.col("play_id")).alias("s"),
            (pl.col("game_id") + ":" + pl.col("fixed_drive").cast(pl.Int64).cast(pl.String)).alias(
                "e"
            ),
        ).unique("s", keep="first", maintain_order=True),
        "OFFENSE": plays.select(pl.col("play_key").alias("s"), pl.col("offense").alias("e")),
        "DEFENSE": plays.select(pl.col("play_key").alias("s"), pl.col("defense").alias("e")),
        "ON_FIELD": on_field,
        **roles,
        "PLAYED_FOR": played_for,
        **coach_rels,
    }
    nodes = {
        "Season": seasons,
        "Team": teams,
        "Player": players,
        "Coach": coaches,
        "Game": games,
        "Drive": drives,
        "Play": plays,
    }
    keys = {label: df[S.key_of(label)] for label, df in nodes.items()}
    for label, k in keys.items():  # the bulk import refuses these; say which label, early
        blank = k.is_null() | (k.cast(pl.String).str.strip_chars() == "")
        if blank.any() or k.n_unique() != k.len():
            raise ValueError(f"history graph: {label} keys are blank or repeated")
    dropped: dict[str, int] = {}
    for rtype, df in rels.items():
        spec = S.RELATIONSHIPS[rtype]
        df = df.drop_nulls(["s", "e"])
        if df.schema["s"] == pl.String:  # a blank key can't be a node (belt and braces)
            df = df.filter(pl.col("s").str.strip_chars() != "")
        if df.schema["e"] == pl.String:
            df = df.filter(pl.col("e").str.strip_chars() != "")
        if rtype == "IN_SEASON":
            df = df.with_columns(pl.col("e").cast(pl.Int64))
        kept, lost = _keep_known(df, keys[spec.start], keys[spec.end])
        rels[rtype] = _conform(kept, spec)
        if lost:
            dropped[rtype] = lost
    if part_lost:
        dropped["participation_plays_not_in_pbp"] = part_lost
    log(
        f"history graph frames: {plays.height:,} plays, {drives.height:,} drives, "
        f"{games.height:,} games, {players.height:,} players, "
        f"{sum(r.height for r in rels.values()):,} relationships "
        f"({time.monotonic() - t0:.1f} s)"
    )
    return HistoryFrames(nodes=nodes, rels=rels, dropped=dropped, notes=list(inp.notes))


# ---- CSV --------------------------------------------------------------------------------------


def _csv_ready(df: pl.DataFrame, name: str) -> pl.DataFrame:
    """Strings blank -> null (an empty field is no property), lists -> `;`-joined text, and a
    numeric key written twice (`:ID` + its typed column, schema.csv_header)."""
    exprs = []
    for c, dtype in df.schema.items():
        if dtype == pl.String:
            exprs.append(
                pl.when(pl.col(c).str.strip_chars() == "").then(None).otherwise(pl.col(c)).alias(c)
            )
        elif isinstance(dtype, pl.List):
            exprs.append(pl.col(c).cast(pl.List(pl.String)).list.join(";").alias(c))
        else:
            exprs.append(pl.col(c))
    out = df.select(exprs)
    if name in S.NODES and S.key_type(name) != S.STRING:
        key = S.key_of(name)
        out = out.select(pl.col(key).cast(pl.String).alias("_id"), pl.all())
    return out


def write_csvs(frames: HistoryFrames, out_dir: Path) -> dict[str, dict[str, Any]]:
    """`nodes_<Label>.csv` / `rels_<TYPE>.csv` with the bulk import's headers; returns
    `{name: {"file": name, "rows": n, "kind": "nodes"|"relationships"}}`. Old CSVs in the
    folder are removed first (the import reads only what's listed, but a stale file misleads)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.csv"):
        old.unlink()
    written: dict[str, dict[str, Any]] = {}
    for kind, group in (("nodes", frames.nodes), ("relationships", frames.rels)):
        for name, df in group.items():
            fname = f"{'nodes' if kind == 'nodes' else 'rels'}_{name}.csv"
            ready = _csv_ready(df, name)
            ready.columns = S.csv_header(name)
            ready.write_csv(out_dir / fname, null_value="")
            written[name] = {"file": fname, "rows": df.height, "kind": kind}
    return written
