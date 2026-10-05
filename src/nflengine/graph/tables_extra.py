"""Knowledge-graph tables added in P08 (documentation/05 -> Q5, Q7, Q10): the optional
coaching seed (`COORDINATOR_OF`, `WORKED_UNDER`), FTN play action per team-game
(`PLAYED_IN.pa_rate`), a QB's style per game (`APPEARED_IN.scrambles`, `air_yards`,
`air_att`) and the usage profiles behind player similarity (`UsageProfile` nodes,
`HAS_PROFILE`).

Same contract as `graph/tables.py`: pure Polars, as of one `GraphKey`; the visibility rules
are in that module's docstring (the P08 rows are marked there). `build_tables` calls these.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import polars as pl

from nflengine.graph.tables import GraphInputs, GraphKey, _ends, slug

# ---- the optional coaching seed (Q5; decisions-log open question Q06) ---------------------------

SEED_FILE = "coaching_seed.csv"  # in config/; absent = no coordinators / trees (skipped silently)
SEED_COLUMNS = ("coach", "team", "season", "role")  # + optional `head_coach`
COORDINATOR_ROLES = ("OC", "DC", "ST")  # roles that make a COORDINATOR_OF link


def seed_path() -> Path:
    from nflengine.settings import CONFIG_DIR

    return CONFIG_DIR / SEED_FILE


COACH_FIXES_FILE = "head_coach_fixes.csv"  # in config/ (P10): corrections to nflverse's coaches


def coach_fixes_path() -> Path:
    from nflengine.settings import CONFIG_DIR

    return CONFIG_DIR / COACH_FIXES_FILE


def read_coach_fixes(path: Path | None = None, log: Callable[[str], None] = print) -> pl.DataFrame:
    """Hand-checked corrections to the schedules' head coaches (P10): `season, team,
    from_week, coach[, source]`, the coach from that week on. nflverse carried last season's
    head coach into 2026 for three new hires and misspelt a fourth, and has no 2025 interim
    coaches (P10 roster-churn check). A missing or malformed file is no fixes, never a
    failed build."""
    path = path or coach_fixes_path()
    empty = pl.DataFrame(
        schema={"season": pl.Int32, "team": pl.String, "from_week": pl.Int32, "coach": pl.String}
    )
    if not path.exists():
        return empty
    try:
        df = pl.read_csv(path, comment_prefix="#", infer_schema_length=0)
        df = df.rename({c: c.strip().lower() for c in df.columns})
        return df.select(
            pl.col("season").str.strip_chars().cast(pl.Int32),
            pl.col("team").str.strip_chars().str.to_uppercase(),
            pl.col("from_week").str.strip_chars().cast(pl.Int32),
            pl.col("coach").str.strip_chars(),
        ).filter(pl.col("coach").is_not_null() & (pl.col("coach") != ""))
    except Exception as e:  # a hand-made file: log the type only
        log(f"[yellow]graph: head-coach fixes unreadable ({type(e).__name__}); skipped[/]")
        return empty


def apply_coach_fixes(games: pl.DataFrame, fixes: pl.DataFrame) -> pl.DataFrame:
    """`home_coach` / `away_coach` with the fixes applied (a later `from_week` for the same
    team-season wins from its week on)."""
    if fixes.is_empty() or not {"home_coach", "away_coach"} <= set(games.columns):
        return games
    out = games
    for r in fixes.sort("season", "team", "from_week").iter_rows(named=True):
        for side in ("home", "away"):
            hit = (
                (pl.col("season") == r["season"])
                & (pl.col(f"{side}_team") == r["team"])
                & (pl.col("week") >= r["from_week"])
            )
            out = out.with_columns(
                pl.when(hit)
                .then(pl.lit(r["coach"]))
                .otherwise(pl.col(f"{side}_coach"))
                .alias(f"{side}_coach")
            )
    return out


def read_coaching_seed(
    path: Path | None = None, log: Callable[[str], None] = print
) -> pl.DataFrame:
    """The hand-made coaching seed, one row per (coach, team, season, role): "Kevin
    O'Connell, LA, 2021, OC". `head_coach` (optional) names his boss that season; when it's
    blank the boss is the team's head coach from the schedules (2018 on). Lines starting with
    `#` are comments. A missing file is no seed; a malformed one is logged and skipped (a
    hand-made file must never sink the build)."""
    path = path or seed_path()
    empty = pl.DataFrame(
        schema={
            "coach": pl.String,
            "team": pl.String,
            "season": pl.Int32,
            "role": pl.String,
            "head_coach": pl.String,
        }
    )
    if not path.exists():
        return empty
    try:
        df = pl.read_csv(path, comment_prefix="#", infer_schema_length=0)
        df = df.rename({c: c.strip().lower() for c in df.columns})
        missing = [c for c in SEED_COLUMNS if c not in df.columns]
        if missing:
            log(f"[yellow]graph: coaching seed lacks columns {missing}; skipped[/]")
            return empty
        if "head_coach" not in df.columns:
            df = df.with_columns(pl.lit(None, pl.String).alias("head_coach"))
        df = df.select(
            pl.col("coach").str.strip_chars(),
            pl.col("team").str.strip_chars().str.to_uppercase(),
            pl.col("season").str.strip_chars().cast(pl.Int32),
            pl.col("role").str.strip_chars().str.to_uppercase(),
            # blank ("" or a quoted "") means "from the schedules", never a coach named ""
            pl.col("head_coach").str.strip_chars().replace("", None),
        )
        return df.filter(
            pl.col("coach").is_not_null()
            & (pl.col("coach") != "")
            & pl.col("team").is_not_null()
            & pl.col("season").is_not_null()
        )
    except Exception as e:  # a hand-made file: log the type only
        log(f"[yellow]graph: coaching seed unreadable ({type(e).__name__}); skipped[/]")
        return empty


def coaching_seed_frames(
    inp: GraphInputs, key: GraphKey, head_coach_of: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Coach nodes the seed adds, `COORDINATOR_OF {season, role}` and `WORKED_UNDER {seasons,
    teams, roles}` (coach -> his boss), from seed rows with season <= the key's season (staffs
    are known before a season starts). The boss is the row's `head_coach`, else the team's
    head coach that season from HEAD_COACH_OF (the one with the most games)."""
    rel_schema = {"s": pl.String, "e": pl.String}
    seed = inp.coaching_seed
    if seed.is_empty():
        return (
            pl.DataFrame(schema={"coach_id": pl.String, "name": pl.String}),
            pl.DataFrame(schema=rel_schema | {"season": pl.Int32, "role": pl.String}),
            pl.DataFrame(schema=rel_schema),
        )
    seed = seed.filter(pl.col("season") <= key.season).with_columns(
        pl.col("coach").map_elements(slug, return_dtype=pl.String).alias("coach_id"),
        pl.col("head_coach").map_elements(slug, return_dtype=pl.String).alias("boss_given"),
    )
    hc = (
        head_coach_of.sort("games", descending=True)
        .unique(["e", "season"], keep="first")
        .select(pl.col("e").alias("team"), "season", pl.col("s").alias("boss_hc"))
    )
    seed = seed.join(hc, on=["team", "season"], how="left").with_columns(
        pl.coalesce("boss_given", "boss_hc").alias("boss_id")
    )
    names = pl.concat(
        [
            seed.select("coach_id", pl.col("coach").alias("name")),
            seed.filter(pl.col("head_coach").is_not_null() & (pl.col("head_coach") != "")).select(
                pl.col("boss_given").alias("coach_id"), pl.col("head_coach").alias("name")
            ),
        ]
    ).unique("coach_id", keep="first")
    coord = _ends(
        seed.filter(pl.col("role").is_in(COORDINATOR_ROLES))
        .select("coach_id", "team", pl.col("season").cast(pl.Int32), "role")
        .unique()
        .sort("season", "team", "coach_id"),
        "coach_id",
        "team",
    )
    under = (
        seed.filter(pl.col("boss_id").is_not_null() & (pl.col("boss_id") != pl.col("coach_id")))
        .sort("season")
        .group_by("coach_id", "boss_id", maintain_order=True)
        .agg(
            pl.col("season").unique().sort().cast(pl.Int32).alias("seasons"),
            pl.col("team").unique(maintain_order=True).alias("teams"),
            pl.col("role").unique(maintain_order=True).alias("roles"),
        )
        .sort("coach_id", "boss_id")
    )
    return names.sort("coach_id"), coord, _ends(under, "coach_id", "boss_id")


# ---- FTN play action per team-game (Q7) ----------------------------------------------------------

PA_MIN_DROPBACKS = 10  # charted dropbacks a team-game needs for a play-action rate


def _dropbacks(plays: pl.DataFrame) -> pl.DataFrame:
    return plays.filter(
        (pl.col("qb_dropback").fill_null(0) == 1)
        & pl.col("play_type").is_in(["pass", "run"])
        & (pl.col("two_point_attempt").fill_null(0) == 0)
        & pl.col("posteam").is_not_null()
    )


def visible_ftn(ftn: pl.DataFrame, key: GraphKey) -> pl.DataFrame:
    """FTN charting rows a run at the key could have used: plays strictly before week W; a
    backtest also drops week W-1 of the season (FTN is about a week late on a Tuesday, like
    PFR; curated-data skill). A live run takes the snapshot it has (`date_pulled` is a
    refresh time, rewritten on every pull, so it can't date a row)."""
    if ftn.is_empty():
        return ftn
    out = ftn.filter(key.before())
    if key.mode == "backtest":
        out = out.filter(~((pl.col("season") == key.season) & (pl.col("week") == key.week - 1)))
    return out


def play_action_rates(inp: GraphInputs, key: GraphKey) -> pl.DataFrame:
    """Per (game, offense): `pa_rate` = share of the offense's dropbacks FTN charted as play
    action, over `pa_dropbacks` charted dropbacks (>= PA_MIN_DROPBACKS, else no rate)."""
    schema = {
        "game_id": pl.String,
        "team_id": pl.String,
        "pa_rate": pl.Float64,
        "pa_dropbacks": pl.Int32,
    }
    ftn = visible_ftn(inp.ftn, key)
    if ftn.is_empty() or inp.plays.is_empty():
        return pl.DataFrame(schema=schema)
    db = _dropbacks(inp.plays).select(
        "game_id", pl.col("play_id").cast(pl.Float64), pl.col("posteam").alias("team_id")
    )
    f = ftn.select(
        "game_id",
        pl.col("play_id").cast(pl.Float64),
        pl.col("is_play_action").cast(pl.Float64).fill_null(0.0).alias("_pa"),
    ).unique(["game_id", "play_id"], keep="first")
    out = (
        db.join(f, on=["game_id", "play_id"])
        .group_by("game_id", "team_id")
        .agg(pl.col("_pa").mean().alias("pa_rate"), pl.len().cast(pl.Int32).alias("pa_dropbacks"))
        .filter(pl.col("pa_dropbacks") >= PA_MIN_DROPBACKS)
    )
    return out.cast(schema)  # type: ignore[arg-type]


# ---- a QB's style per game (Q7) ------------------------------------------------------------------


def qb_style_stats(plays: pl.DataFrame) -> pl.DataFrame:
    """Per (passer, game): `scrambles` (on his dropbacks), `air_yards` (intended air yards
    on his pass attempts) and `air_att` (those attempts), from the visible plays. With
    APPEARED_IN.dropbacks they give a QB's scramble rate and air yards per attempt over any
    window, in Cypher."""
    schema = {
        "pid": pl.String,
        "game_id": pl.String,
        "scrambles": pl.Int32,
        "air_yards": pl.Float64,
        "air_att": pl.Int32,
    }
    if plays.is_empty() or "passer_id" not in plays.columns:
        return pl.DataFrame(schema=schema)
    sc = (
        _dropbacks(plays)
        .filter(pl.col("passer_id").is_not_null())
        .group_by(pl.col("passer_id").alias("pid"), "game_id")
        .agg(pl.col("qb_scramble").fill_null(0).sum().cast(pl.Int32).alias("scrambles"))
    )
    if "air_yards" in plays.columns:
        att = (
            plays.filter(
                (pl.col("pass_attempt").fill_null(0) == 1)
                & (pl.col("sack").fill_null(0) == 0)
                & (pl.col("two_point_attempt").fill_null(0) == 0)
                & (pl.col("play_type") == "pass")
                & pl.col("passer_player_id").is_not_null()
                & pl.col("air_yards").is_not_null()
            )
            .group_by(pl.col("passer_player_id").alias("pid"), "game_id")
            .agg(
                pl.col("air_yards").sum().cast(pl.Float64).alias("air_yards"),
                pl.len().cast(pl.Int32).alias("air_att"),
            )
        )
        out = sc.join(att, on=["pid", "game_id"], how="full", coalesce=True)
    else:
        out = sc.with_columns(
            pl.lit(None, pl.Float64).alias("air_yards"), pl.lit(None, pl.Int32).alias("air_att")
        )
    return out.cast(schema).select(list(schema))  # type: ignore[arg-type]


# ---- usage profiles (Q10 player similarity) ------------------------------------------------------

USAGE_GROUPS = ("WR", "TE", "RB")
RECEIVER_FEATURES = (
    "target_share",  # his targets / his team's targets, in the games he played
    "air_yards_share",  # his intended air yards / the team's
    "adot",  # his air yards per target (depth of target)
    "rz_share",  # his red-zone targets (+ carries for RBs) / the team's
    "snap_share",  # average offensive snap share
    "yards_per_team_att",  # his receiving yards per team pass attempt (a yards-per-route proxy)
)
RB_FEATURES = (
    "carry_share",  # his designed carries / the team's (scrambles left out)
    "target_share",
    "rz_share",
    "snap_share",
    "yards_per_team_play",  # his scrimmage yards per team play (pass attempts + carries)
)
USAGE_FEATURES = {"WR": RECEIVER_FEATURES, "TE": RECEIVER_FEATURES, "RB": RB_FEATURES}
# a profile needs this much (current season: weeks before W so far)
MIN_GAMES_CURRENT, MIN_OPPS_CURRENT = 3, 12
MIN_GAMES_PAST, MIN_OPPS_PAST = 8, 40
RZ_YARDLINE = 20


def _usage_inputs(plays: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Regular-season targets and designed carries, one row per play."""
    p = plays.filter(
        (pl.col("season_type") == "REG") & (pl.col("two_point_attempt").fill_null(0) == 0)
    )
    rz = pl.col("yardline_100") <= RZ_YARDLINE if "yardline_100" in p.columns else pl.lit(False)
    air = pl.col("air_yards") if "air_yards" in p.columns else pl.lit(None, pl.Float64)
    tg = p.filter(
        (pl.col("pass_attempt").fill_null(0) == 1)
        & (pl.col("sack").fill_null(0) == 0)
        & (pl.col("play_type") == "pass")
    ).select(
        "season",
        "game_id",
        pl.col("posteam").alias("team"),
        pl.col("receiver_player_id").alias("pid"),
        air.cast(pl.Float64).alias("air"),
        pl.col("receiving_yards").fill_null(0).cast(pl.Float64).alias("rec_yds"),
        rz.fill_null(False).alias("rz"),
    )
    if "rusher_player_id" in p.columns:
        ru = p.filter(
            (pl.col("play_type") == "run")
            & (pl.col("qb_scramble").fill_null(0) == 0)
            & pl.col("rusher_player_id").is_not_null()
        ).select(
            "season",
            "game_id",
            pl.col("posteam").alias("team"),
            pl.col("rusher_player_id").alias("pid"),
            (pl.col("rushing_yards") if "rushing_yards" in p.columns else pl.lit(0.0))
            .fill_null(0)
            .cast(pl.Float64)
            .alias("rush_yds"),
            rz.fill_null(False).alias("rz"),
        )
    else:
        ru = pl.DataFrame(
            schema={
                "season": pl.Int32,
                "game_id": pl.String,
                "team": pl.String,
                "pid": pl.String,
                "rush_yds": pl.Float64,
                "rz": pl.Boolean,
            }
        )
    return tg, ru


def usage_profiles(
    inp: GraphInputs, key: GraphKey, players: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """`UsageProfile` nodes (one per WR / TE / RB season with enough volume, regular season,
    visible plays only) and `(Player)-[:HAS_PROFILE]->(UsageProfile)`.

    Shares are over the games the player played (a target, a carry or an offensive snap), so
    missed games don't dilute them. `vector` = the group's features as z-scores over every
    profile of that group in the graph's window (2018 through the key), the input to GDS
    k-nearest neighbors. `yds_rank` ranks the season's receiving yards (WR / TE) or scrimmage
    yards (RB) within its group and season, among profiles."""
    node_schema = {"key": pl.String}
    empty = (
        pl.DataFrame(schema=node_schema),
        pl.DataFrame(schema={"s": pl.String, "e": pl.String}),
    )
    if inp.plays.is_empty() or "receiver_player_id" not in inp.plays.columns:
        return empty
    plays = inp.plays.filter(key.before())  # the loader already does; a builder never trusts it
    tg, ru = _usage_inputs(plays)
    if tg.is_empty():
        return empty
    gkeys = ["season", "game_id", "team"]
    team_tot = (
        tg.group_by(gkeys)
        .agg(
            pl.len().alias("t_targets"),
            pl.col("air").fill_null(0).sum().alias("t_air"),
            pl.col("rz").sum().alias("t_rz_targets"),
        )
        .join(
            ru.group_by(gkeys).agg(
                pl.len().alias("t_carries"), pl.col("rz").sum().alias("t_rz_carries")
            ),
            on=gkeys,
            how="full",
            coalesce=True,
        )
        .fill_null(0)
    )
    pk = [*gkeys, "pid"]
    pt = (
        tg.filter(pl.col("pid").is_not_null())
        .group_by(pk)
        .agg(
            pl.len().alias("targets"),
            pl.col("air").fill_null(0).sum().alias("air"),
            pl.col("rz").sum().alias("rz_targets"),
            pl.col("rec_yds").sum().alias("rec_yds"),
        )
    )
    pr = ru.group_by(pk).agg(
        pl.len().alias("carries"),
        pl.col("rz").sum().alias("rz_carries"),
        pl.col("rush_yds").sum().alias("rush_yds"),
    )
    pg = pt.join(pr, on=pk, how="full", coalesce=True).with_columns(pl.col("season").cast(pl.Int32))
    gids = plays.select("game_id", "season").unique("game_id")
    if inp.snaps.height:
        sn = (
            inp.snaps.filter(
                pl.col("gsis_id").is_not_null() & (pl.col("offense_snaps").fill_null(0) > 0)
            )
            .select("game_id", "team", "gsis_id", "offense_pct")
            .join(gids, on="game_id")
            .select(
                "season",
                "game_id",
                "team",
                pl.col("gsis_id").alias("pid"),
                pl.col("offense_pct").cast(pl.Float64).alias("snap_pct"),
            )
            .unique(["game_id", "pid"], keep="first")
        )
        pg = pg.join(sn, on=pk, how="full", coalesce=True)
    else:
        pg = pg.with_columns(pl.lit(None, pl.Float64).alias("snap_pct"))
    # only regular-season games the plays cover (the snaps join may add others)
    pg = pg.join(team_tot, on=gkeys, how="inner").fill_null(0)
    season_rows = (
        pg.with_columns(pl.col("season").cast(pl.Int32))
        .sort("season", "game_id")
        .group_by("pid", "season")
        .agg(
            pl.col("team").mode().first().alias("team_id"),
            pl.col("game_id").n_unique().cast(pl.Int32).alias("games"),
            *[
                pl.col(c).sum().alias(c)
                for c in (
                    "targets",
                    "air",
                    "rz_targets",
                    "rec_yds",
                    "carries",
                    "rz_carries",
                    "rush_yds",
                    "t_targets",
                    "t_air",
                    "t_rz_targets",
                    "t_carries",
                    "t_rz_carries",
                )
            ],
            pl.col("snap_pct").filter(pl.col("snap_pct") > 0).mean().alias("snap_share"),
        )
    )
    grp = players.select(pl.col("player_id").alias("pid"), "name", "position_group")
    prof = season_rows.join(grp, on="pid", how="inner").filter(
        pl.col("position_group").is_in(USAGE_GROUPS)
    )
    rb = pl.col("position_group") == "RB"

    def ratio(a: pl.Expr, b: pl.Expr) -> pl.Expr:
        return pl.when(b > 0).then(a / b).otherwise(None)

    prof = prof.with_columns(
        ratio(pl.col("targets"), pl.col("t_targets")).alias("target_share"),
        ratio(pl.col("air"), pl.col("t_air")).alias("air_yards_share"),
        ratio(pl.col("air"), pl.col("targets")).alias("adot"),
        pl.when(rb)
        .then(
            ratio(
                pl.col("rz_targets") + pl.col("rz_carries"),
                pl.col("t_rz_targets") + pl.col("t_rz_carries"),
            )
        )
        .otherwise(ratio(pl.col("rz_targets"), pl.col("t_rz_targets")))
        .alias("rz_share"),
        ratio(pl.col("rec_yds"), pl.col("t_targets")).alias("yards_per_team_att"),
        ratio(pl.col("carries"), pl.col("t_carries")).alias("carry_share"),
        ratio(
            pl.col("rec_yds") + pl.col("rush_yds"), pl.col("t_targets") + pl.col("t_carries")
        ).alias("yards_per_team_play"),
        (pl.col("rec_yds") + pl.col("rush_yds")).alias("scrimmage_yds"),
        pl.when(rb)
        .then(pl.col("targets") + pl.col("carries"))
        .otherwise(pl.col("targets"))
        .alias("opportunities"),
        (pl.col("season") == key.season).alias("current"),
    )
    enough = (
        pl.when(pl.col("current"))
        .then(
            (pl.col("games") >= MIN_GAMES_CURRENT) & (pl.col("opportunities") >= MIN_OPPS_CURRENT)
        )
        .otherwise((pl.col("games") >= MIN_GAMES_PAST) & (pl.col("opportunities") >= MIN_OPPS_PAST))
    )
    prof = prof.filter(enough)
    if prof.is_empty():
        return empty
    frames = []
    for group, feats in USAGE_FEATURES.items():
        g = prof.filter(pl.col("position_group") == group)
        if g.is_empty():
            continue
        # a missing feature (no snap record) sits at the group mean: z = 0
        z = [
            ((pl.col(f) - pl.col(f).mean()) / pl.col(f).std()).fill_nan(0.0).fill_null(0.0)
            for f in feats
        ]
        size = (
            pl.when(pl.lit(group == "RB"))
            .then(pl.col("scrimmage_yds"))
            .otherwise(pl.col("rec_yds"))
        )
        g = g.with_columns(
            pl.concat_list([e.round(4) for e in z]).alias("vector"),
            size.rank("min", descending=True).over("season").cast(pl.Int32).alias("yds_rank"),
            pl.lit(",".join(feats)).alias("basis"),
        )
        frames.append(g)
    out = pl.concat(frames, how="diagonal_relaxed")
    nodes = out.select(
        pl.format("{}:{}", "pid", "season").alias("key"),
        pl.col("pid").alias("player_id"),
        "name",
        pl.col("position_group").alias("group"),
        "season",
        "team_id",
        "current",
        "games",
        pl.col("targets").cast(pl.Int32),
        pl.col("carries").cast(pl.Int32),
        pl.col("rec_yds").cast(pl.Int32),
        pl.col("rush_yds").cast(pl.Int32),
        pl.col("scrimmage_yds").cast(pl.Int32),
        *[
            pl.col(f).round(4)
            for f in (
                "target_share",
                "air_yards_share",
                "adot",
                "rz_share",
                "snap_share",
                "yards_per_team_att",
                "carry_share",
                "yards_per_team_play",
            )
        ],
        "yds_rank",
        "basis",
        "vector",
    ).sort("key")
    rel = nodes.select(pl.col("player_id").alias("s"), pl.col("key").alias("e"))
    return nodes, rel
