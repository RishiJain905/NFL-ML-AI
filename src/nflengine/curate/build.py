"""`nfl curate`: build curated tables from the newest raw snapshots (documentation/03).

Outputs under {NFL_DATA_ROOT}/curated:
    {table}.parquet                 small/medium tables
    plays/season=YYYY.parquet       play-by-play, one file per season
    nfl.duckdb                      DuckDB views over all of the above
    _joins.json                     player-ID join rates (read by quality checks)

Team codes are normalized to one canonical code per franchise, and PFR / ESPN keyed
tables get a `gsis_id`.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import polars as pl

from nflengine.curate.ids import JoinRate, attach_gsis, player_crosswalk
from nflengine.curate.teams import (
    CANONICAL_TEAMS,
    aliases_table,
    normalize_team_cols,
    team_like_columns,
)
from nflengine.fsutil import replace_file
from nflengine.ingest.base import SnapshotStore
from nflengine.ops import events
from nflengine.paths import DataPaths, configure_tool_env, ensure_data_root

NV = "nflverse"


class Curator:
    def __init__(self, paths: DataPaths, log: Callable[[str], None] = print):
        self.paths = paths
        self.raw = SnapshotStore(paths.raw)
        self.out = paths.curated
        self.log = log
        self.joins: list[JoinRate] = []
        self.built: list[str] = []
        self._xwalk: pl.DataFrame | None = None
        self._games: pl.DataFrame | None = None

    # ---- helpers -----------------------------------------------------------------
    def read(self, source: str, dataset: str) -> pl.DataFrame | None:
        return self.raw.read_latest(source, dataset)

    def write(self, name: str, df: pl.DataFrame) -> None:
        # Some nflverse tables (e.g. injuries) carry season/week as floats; keep them integer.
        casts = [
            pl.col(c).cast(pl.Int32)
            for c in ("season", "week")
            if c in df.columns and df.schema[c].is_float()
        ]
        if casts:
            df = df.with_columns(casts)
        tmp = self.out / f"{name}.parquet.tmp"
        df.write_parquet(tmp, compression="zstd")
        replace_file(tmp, self.out / f"{name}.parquet")
        self.built.append(name)
        self.log(f"  {name}: {df.height:,} rows")
        n = len(self.built)  # CR02 progress: ~32 tables on a Tuesday (an estimate, capped)
        events.progress(None, min(n, CURATE_TABLES), CURATE_TABLES, f"table {n} · {name}")

    @property
    def xwalk(self) -> pl.DataFrame:
        if self._xwalk is None:
            self._xwalk = player_crosswalk(self.read(NV, "players"))
        return self._xwalk

    def gsis(self, df: pl.DataFrame, key: str, xcol: str, table: str) -> pl.DataFrame:
        df, rate = attach_gsis(df, key, self.xwalk, xcol, table)
        self.joins.append(rate)
        return df

    # ---- reference tables ----------------------------------------------------------
    def teams(self) -> None:
        teams = self.read(NV, "teams").filter(pl.col("team_abbr").is_in(CANONICAL_TEAMS))
        self.write("teams", teams.rename({"team_abbr": "team"}))
        self.write("team_aliases", aliases_table())

    def players(self) -> None:
        self.write("players", self.read(NV, "players"))
        self.write("player_ids", self.xwalk)

    # ---- games & lines ---------------------------------------------------------------
    def games(self) -> None:
        s = normalize_team_cols(self.read(NV, "schedules"), ["home_team", "away_team"])
        s = s.with_columns(
            pl.concat_str([pl.col("gameday"), pl.col("gametime").fill_null("13:00")], separator=" ")
            .str.to_datetime("%Y-%m-%d %H:%M", strict=False)
            .dt.replace_time_zone("America/New_York", ambiguous="earliest", non_existent="null")
            .dt.convert_time_zone("UTC")
            .alias("kickoff_utc"),
            pl.col("result").is_not_null().alias("completed"),
            pl.col("espn").cast(pl.String).alias("espn_event_id"),
        )
        # neutral = nflverse "Neutral" or a venue abroad (D99: a "home" game in London is not)
        from nflengine.features.venues import with_neutral_rule

        s = with_neutral_rule(s)
        self._games = s
        self.write("games", s)

    def lines(self) -> None:
        g = self._games
        hist = g.filter(pl.col("spread_line").is_not_null()).select(
            "game_id",
            pl.lit("nflverse_schedules").alias("source"),
            pl.lit(None, dtype=pl.String).alias("provider"),
            pl.col("spread_line").alias("home_spread"),  # + = home favoured (nflverse convention)
            pl.col("total_line").alias("total"),
            pl.col("home_moneyline").cast(pl.Float64),
            pl.col("away_moneyline").cast(pl.Float64),
            pl.col("completed").alias("is_closing"),
        )
        frames = [hist]
        espn = self.read("espn", "scoreboard")
        if espn is not None and espn.height:
            e = espn.join(
                g.select("game_id", "espn_event_id"), on="espn_event_id", how="inner"
            ).filter(pl.col("espn_spread").is_not_null())
            frames.append(
                e.select(
                    "game_id",
                    pl.lit("espn").alias("source"),
                    pl.col("odds_provider").alias("provider"),
                    (-pl.col("espn_spread")).alias("home_spread"),  # ESPN: favourite negative
                    pl.col("over_under").alias("total"),
                    "home_moneyline",
                    "away_moneyline",
                    pl.col("completed").alias("is_closing"),
                )
            )
        odds = self.read("odds_api", "odds")
        if odds is not None and odds.height:
            from nflengine.curate.lines import odds_api_lines

            team_names = normalize_team_cols(
                self.read(NV, "teams").select(
                    pl.col("team_name"), pl.col("team_abbr").alias("team")
                ),
                ["team"],
            )
            rows, matched, total = odds_api_lines(odds, g, team_names)
            self.joins.append(JoinRate("odds_api_lines", "event->game_id", matched, total))
            frames.append(rows)
        lines = pl.concat(frames, how="diagonal_relaxed").with_columns(
            pl.lit(self.raw.snapshots(NV, "schedules")[-1]).alias("snapshot_date")
        )
        self.write("lines", lines)

    # ---- play-by-play (partitioned) -------------------------------------------------
    def plays(self) -> None:
        out_dir = self.out / "plays"
        out_dir.mkdir(parents=True, exist_ok=True)
        total = 0
        for part, path in sorted(self.raw.latest_parts(NV, "pbp").items()):
            df = pl.read_parquet(path)
            df = normalize_team_cols(df, team_like_columns(df))
            tmp = out_dir / f"{part}.parquet.tmp"
            df.write_parquet(tmp, compression="zstd")
            replace_file(tmp, out_dir / f"{part}.parquet")
            total += df.height
        self.built.append("plays")
        self.log(f"  plays: {total:,} rows ({len(list(out_dir.glob('*.parquet')))} seasons)")

    # ---- per-player / per-team weekly tables ------------------------------------------
    def simple(
        self, dataset: str, name: str | None = None, source: str = NV
    ) -> pl.DataFrame | None:
        df = self.read(source, dataset)
        if df is None:
            self.log(f"  {name or dataset}: no raw data, skipped")
            return None
        return normalize_team_cols(df, team_like_columns(df))

    def weekly_tables(self) -> None:
        for dataset, name in [
            ("player_stats", "player_games"),
            ("team_stats", "team_games"),
            ("injuries", "injuries"),
            ("rosters_weekly", "rosters_weekly"),
            ("officials", "officials"),
            ("ftn_charting", "ftn_plays"),
        ]:
            if (df := self.simple(dataset, name)) is not None:
                self.write(name, df)
        for st in ("passing", "receiving", "rushing"):
            if (df := self.simple(f"ngs_{st}")) is not None:
                df = df.rename({"team_abbr": "team"}).with_columns(
                    (pl.col("week") == 0).alias("is_season_total")
                )
                self.write(f"ngs_{st}", df)
        for st in ("pass", "rush", "rec", "def"):
            if (df := self.simple(f"pfr_{st}")) is not None:
                self.write(f"pfr_{st}", self.gsis(df, "pfr_player_id", "pfr_id", f"pfr_{st}"))
        if (df := self.simple("snap_counts", "snaps")) is not None:
            self.write("snaps", self.gsis(df, "pfr_player_id", "pfr_id", "snaps"))
        if (df := self.simple("trades")) is not None:
            self.write("trades", normalize_team_cols(df, ["gave", "received"]))
        if (df := self.simple("draft_picks")) is not None:
            self.write("draft_picks", df)

    # ---- depth charts: two nflverse formats -> one weekly table ----------------------
    def depth_charts(self) -> None:
        parts = self.raw.read_latest_parts(NV, "depth_charts")
        frames = []
        games = self._games.filter(pl.col("game_type") != "SBBYE")
        team_games = pl.concat(
            [
                games.select("season", "week", pl.col(side).alias("team"), "gameday")
                for side in ("home_team", "away_team")
            ]
        ).with_columns(pl.col("gameday").str.to_date())
        for part, df in parts.items():
            season = int(part.split("=")[1])
            if "dt" in df.columns:  # 2025+ format: daily ESPN snapshots
                frames.append(self._depth_new(df, season, team_games))
            else:  # <=2024 format: weekly
                frames.append(self._depth_old(df))
        out = pl.concat(frames, how="diagonal_relaxed")
        out = normalize_team_cols(out, ["team"])
        self.write("depth_charts", out)

    @staticmethod
    def _depth_old(df: pl.DataFrame) -> pl.DataFrame:
        pos = "depth_position" if "depth_position" in df.columns else "position"
        return df.select(
            "season",
            "week",
            pl.col("club_code").alias("team"),
            "gsis_id",
            pl.col("full_name").alias("player_name"),
            pl.col(pos).str.strip_chars().alias("position"),  # old feed has stray whitespace
            pl.col("depth_team").cast(pl.Int32, strict=False).alias("depth_rank"),
            pl.col("formation") if "formation" in df.columns else pl.lit(None).alias("formation"),
            pl.lit("weekly").alias("source_format"),
            pl.lit(None, dtype=pl.Date).alias("snap_date"),  # the weekly feed has no date
        ).filter(pl.col("week").is_not_null())

    @staticmethod
    def _depth_new(df: pl.DataFrame, season: int, team_games: pl.DataFrame) -> pl.DataFrame:
        df = df.with_columns(
            pl.col("dt").cast(pl.String).str.slice(0, 10).str.to_date().alias("snap_date")
        )
        snaps = df.select("team", "snap_date").unique().sort("snap_date")
        tg = team_games.filter(pl.col("season") == season).sort("gameday")
        # For each team-game, the latest depth snapshot on or before game day.
        picked = (
            tg.join_asof(
                snaps,
                left_on="gameday",
                right_on="snap_date",
                by="team",
                strategy="backward",
                check_sortedness=False,  # sorted by gameday above
            )
            .drop_nulls("snap_date")
            # Only games within 10 days of the snapshot: future weeks must not inherit
            # today's depth chart (they get their own snapshot when the time comes).
            .filter((pl.col("gameday") - pl.col("snap_date")).dt.total_days() <= 10)
        )
        return (
            df.join(picked.select("team", "snap_date", "season", "week"), on=["team", "snap_date"])
            .select(
                "season",
                "week",
                "team",
                "gsis_id",
                "player_name",
                pl.col("pos_abb").alias("position"),
                pl.col("pos_rank").cast(pl.Int32, strict=False).alias("depth_rank"),
                pl.col("pos_grp").alias("formation"),
                pl.lit("daily_snapshot").alias("source_format"),
                "snap_date",  # when this chart was published (availability checks)
            )
            .unique(subset=["season", "week", "team", "position", "depth_rank", "gsis_id"])
        )

    # ---- optional sources --------------------------------------------------------------
    def espn_tables(self) -> None:
        g = self._games
        team_names = self.read(NV, "teams").select(
            pl.col("team_name"), pl.col("team_abbr").alias("team")
        )
        # Relocated franchises appear twice (e.g. LA and LAR "Los Angeles Rams").
        team_names = normalize_team_cols(team_names, ["team"]).unique(subset="team_name")
        sb = self.read("espn", "scoreboard")
        if sb is not None and sb.height:
            sb = sb.join(g.select("game_id", "espn_event_id"), on="espn_event_id", how="left")
            sb = sb.with_columns(
                pl.col("home_team_espn").alias("home_team"),
                pl.col("away_team_espn").alias("away_team"),
            )
            self.write("espn_scoreboard", normalize_team_cols(sb, ["home_team", "away_team"]))
        if (news := self.read("espn", "news")) is not None:
            # Fantasy content is out of scope (documentation/01 -> principles); flag it so
            # the digest payload can exclude it.
            is_fantasy = pl.concat_str(
                [pl.col("headline").fill_null(""), pl.col("description").fill_null("")],
                separator=" ",
            ).str.contains(r"(?i)fantasy|\bppr\b|start/sit|waiver")
            self.write("espn_news", news.with_columns(is_fantasy.alias("is_fantasy")))
        if (inj := self.read("espn", "injuries")) is not None and inj.height:
            inj = inj.join(team_names, left_on="team_name", right_on="team_name", how="left")
            self.write(
                "espn_injuries", self.gsis(inj, "athlete_espn_id", "espn_id", "espn_injuries")
            )
        if (qbr := self.read("espn", "qbr_weekly")) is not None and qbr.height:
            self.write("espn_qbr", self.gsis(qbr, "espn_id", "espn_id", "espn_qbr"))
        if (fpi := self.read("espn", "fpi")) is not None and fpi.height:
            fpi = fpi.with_columns(pl.col("abbreviation").alias("team"))
            self.write("espn_fpi", normalize_team_cols(fpi, ["team"]))
        if (lead := self.read("ngs_site", "leaders")) is not None:
            self.write("ngs_leaders", lead)
        if (wx := self.read("open_meteo", "forecasts")) is not None:
            self.write("weather_forecasts", wx)
        if (odds := self.read("odds_api", "odds")) is not None:
            self.write("odds_api", odds)

    # ---- DuckDB views ------------------------------------------------------------------
    def duckdb_views(self) -> Path:
        return write_duckdb_views(self.paths)

    def write_joins(self) -> None:
        payload = [
            {"table": j.table, "key": j.key, "matched": j.matched, "total": j.total, "rate": j.rate}
            for j in self.joins
        ]
        (self.out / "_joins.json").write_text(json.dumps(payload, indent=2))


def write_duckdb_views(paths: DataPaths) -> Path:
    """(Re)build `curated/nfl.duckdb`: a view per curated table, `plays`, and the P02+
    feature tables under `features/` (team_ratings, team_elo, ...) when they exist."""
    import duckdb

    out = paths.curated
    db = out / "nfl.duckdb"
    tmp = out / "nfl.duckdb.tmp"
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    try:
        tables = sorted(out.glob("*.parquet")) + sorted(paths.features.glob("*.parquet"))
        for p in tables:
            con.execute(
                f"CREATE OR REPLACE VIEW {p.stem} AS SELECT * FROM read_parquet('{p.as_posix()}')"
            )
        plays_glob = (out / "plays" / "*.parquet").as_posix()
        con.execute(
            "CREATE OR REPLACE VIEW plays AS SELECT * FROM "
            f"read_parquet('{plays_glob}', union_by_name=true)"
        )
    finally:
        con.close()
    shutil.move(tmp, db)
    return db


CURATE_TABLES = 32  # tables a weekly curate writes (2026 week 4); progress events only


def build_all(log: Callable[[str], None] = print) -> list[str]:
    paths = ensure_data_root()
    configure_tool_env(paths)
    c = Curator(paths, log)
    log("curating:")
    c.teams()
    c.players()
    c.games()
    c.lines()
    c.plays()
    c.weekly_tables()
    c.depth_charts()
    c.espn_tables()
    c.write_joins()
    db = c.duckdb_views()
    log(f"  DuckDB views: {db}")
    return c.built
