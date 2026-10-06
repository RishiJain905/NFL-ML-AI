"""Shared fixtures: a tiny synthetic league for the P02 rating / trend tests."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

LEAGUE_TEAMS = ["ARI", "ATL", "BAL", "BUF"]
TRUE_OFF = {"ARI": 0.10, "ATL": -0.05, "BAL": 0.0, "BUF": -0.05}
TRUE_DEF = {"ARI": 0.0, "ATL": 0.05, "BAL": -0.10, "BUF": 0.05}  # EPA allowed: lower = better
TRUE_HFA = 0.04
PAIRINGS = [((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))]


@pytest.fixture(autouse=True)
def _no_real_wandb_runs(request, monkeypatch):
    """No unit test may create a real W&B run or send a W&B alert (P07: a CLI test once
    reached the real pipeline and logged failed runs). Integration tests are exempt."""
    if request.node.get_closest_marker("integration") is None:
        monkeypatch.setenv("WANDB_MODE", "disabled")


@pytest.fixture(autouse=True)
def _no_real_runner_process(monkeypatch):
    """CR02: the control room's runner never starts a real process in pytest. Tests give
    `Runner` a fake launcher; anything that reaches the real one fails loudly instead."""
    import nflengine.app.runner as R

    def refuse(argv, log_file):
        raise AssertionError(f"a test tried to start a real process: {argv[3:6]}")

    monkeypatch.setattr(R, "default_launcher", refuse)


@pytest.fixture(autouse=True)
def _no_real_schedule_refresh(monkeypatch):
    """`weekly.refresh_schedule` would run a real nflverse ingest on the data root (P07).
    Returns the seasons it was asked to refresh."""
    import nflengine.weekly as W

    calls: list[int] = []
    monkeypatch.setattr(W, "refresh_schedule", lambda season, log=print: calls.append(season))
    return calls


def make_league(
    seasons=(2020, 2021, 2022),
    weeks: int = 6,
    plays_per_side: int = 60,
    in_progress: int | None = 2022,
    seed: int = 7,
) -> dict[str, pl.DataFrame]:
    """Games, plays and week-1 depth charts for 4 teams.

    Each week has two games (round robin, home side alternating). The `in_progress`
    season has weeks 1-4 played, one week-5 game played, and the rest scheduled.
    ATL starts a new QB in week 1 of the second season.
    """
    rng = np.random.default_rng(seed)
    games, plays, depth = [], [], []
    for si, season in enumerate(seasons):
        start = dt.datetime(season, 9, 10, 17, tzinfo=dt.UTC)
        for week in range(1, weeks + 1):
            pairs = PAIRINGS[(week - 1) % 3]
            for gi, (a, b) in enumerate(pairs):
                home, away = (a, b) if week % 2 else (b, a)
                h, v = LEAGUE_TEAMS[home], LEAGUE_TEAMS[away]
                kickoff = start + dt.timedelta(days=7 * (week - 1), hours=3 * gi)
                played = not (season == in_progress and (week > 5 or (week == 5 and gi == 1)))
                game_id = f"{season}_{week:02d}_{v}_{h}"
                qb = {t: f"QB_{t}_{2 if (t == 'ATL' and si >= 1) else 1}" for t in (h, v)}
                hs, vs = (
                    (int(rng.integers(10, 35)), int(rng.integers(10, 35)))
                    if played
                    else (
                        None,
                        None,
                    )
                )
                games.append(
                    {
                        "game_id": game_id,
                        "season": season,
                        "week": week,
                        "game_type": "REG",
                        "home_team": h,
                        "away_team": v,
                        "home_score": hs,
                        "away_score": vs,
                        "result": None if hs is None else hs - vs,
                        "neutral_site": False,
                        "completed": played,
                        "kickoff_utc": kickoff,
                        "home_qb_id": qb[h],
                        "away_qb_id": qb[v],
                        "home_qb_name": qb[h],
                        "away_qb_name": qb[v],
                    }
                )
                if not played:
                    continue
                for off, dfn in ((h, v), (v, h)):
                    for i in range(plays_per_side):
                        drop = i % 5 < 3
                        epa = (
                            TRUE_OFF[off]
                            + TRUE_DEF[dfn]
                            + (TRUE_HFA if off == h else 0.0)
                            + rng.normal(0, 0.5)
                        )
                        plays.append(
                            {
                                "game_id": game_id,
                                "play_id": float(i + 1),
                                "season": season,
                                "week": week,
                                "season_type": "REG",
                                "posteam": off,
                                "defteam": dfn,
                                "home_team": h,
                                "location": "Home",
                                "play_type": "pass" if drop else "run",
                                "two_point_attempt": 0.0,
                                "qb_dropback": 1.0 if drop else 0.0,
                                "epa": epa,
                                "success": 1.0 if epa > 0 else 0.0,
                                "wp": float(rng.uniform(0.01, 0.99)),
                            }
                        )
                # Rows the rating filters must drop.
                for ptype, extra in (
                    ("qb_kneel", {}),
                    ("no_play", {}),
                    ("pass", {"two_point_attempt": 1.0}),
                ):
                    row = {
                        "game_id": game_id,
                        "play_id": 999.0,
                        "season": season,
                        "week": week,
                        "season_type": "REG",
                        "posteam": h,
                        "defteam": v,
                        "home_team": h,
                        "location": "Home",
                        "play_type": ptype,
                        "two_point_attempt": 0.0,
                        "qb_dropback": 0.0,
                        "epa": 5.0,
                        "success": 1.0,
                        "wp": 0.5,
                    }
                    row.update(extra)
                    plays.append(row)
        for t in LEAGUE_TEAMS:
            qb = f"QB_{t}_{2 if (t == 'ATL' and si >= 1) else 1}"
            depth.append(
                {
                    "season": season,
                    "week": 1,
                    "team": t,
                    "position": "QB",
                    "depth_rank": 1,
                    "gsis_id": qb,
                }
            )
    games_df = pl.DataFrame(games).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    plays_df = pl.DataFrame(plays).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    depth_df = pl.DataFrame(depth).with_columns(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("depth_rank").cast(pl.Int32),
    )
    return {"games": games_df, "plays": plays_df, "depth_charts": depth_df}


@pytest.fixture(scope="session")
def league() -> dict[str, pl.DataFrame]:
    return make_league()


# ---- P04 digest fixtures ----------------------------------------------------------------------


def make_payload(**overrides):
    """A small, valid digest payload (2 games, 1 trend, 1 under-the-hood item, 1 watch item,
    a scored report card) built with the real formatting helpers."""
    from nflengine.digest import format as F
    from nflengine.digest.payload import (
        BiggestMiss,
        CalibrationBucket,
        Consensus,
        GameHighlights,
        GameItem,
        HighlightGame,
        Meta,
        Payload,
        PredictedScore,
        ReportCard,
        SeasonToDate,
        TrendDriver,
        TrendItem,
        UnderHoodItem,
        WatchItem,
    )

    meta = Meta(
        season=2025,
        week=8,
        run_id="2025-w08-backtest",
        mode="backtest",
        run_time="2025-10-21T14:00:00+00:00",
        season_display=F.count(2025),
        week_display=F.count(8),
        prev_week_display=F.count(7),
        data_through="2025-W07",
        market_data_used=True,
        model_versions={"game model": "game-model-v0:backtest"},
    )
    rc = ReportCard(
        status="scored",
        scored_week=7,
        scored_week_display=F.count(7),
        picks_correct=F.count(11),
        picks_total=F.count(15),
        brier=F.brier(0.2013),
        brier_elo=F.brier(0.2261),
        points_mae=F.points_error(7.43),
        biggest_miss=BiggestMiss(
            game_id="2025_07_NYJ_BUF",
            game="Jets at Bills",
            favored="BUF",
            favored_name="Bills",
            prob=F.pct(0.78, cap=True),
            winner="NYJ",
            winner_name="Jets",
            final_score="24–17",
        ),
        calibration=[
            CalibrationBucket(bucket=F.bucket(0.6, 0.7), games=F.count(8), favorite_wins=F.count(6))
        ],
        season_to_date=SeasonToDate(
            weeks=F.count(7),
            picks_correct=F.count(70),
            picks_total=F.count(105),
            brier=F.brier(0.2152),
            brier_elo=F.brier(0.2231),
        ),
    )
    games = [
        GameItem(
            game_id="2025_08_KC_DEN",
            kickoff="Sun 4:25 PM",
            status="upcoming",
            home="DEN",
            away="KC",
            home_name="Broncos",
            away_name="Chiefs",
            matchup="Chiefs at Broncos",
            home_win_prob=F.pct(0.41),
            away_win_prob=F.pct(0.59),
            favored="KC",
            expected_margin=F.margin(-2.6, "DEN", "KC"),
            predicted_score=PredictedScore(home=F.points(20.8), away=F.points(23.4)),
            confidence="lean",
        ),
        GameItem(
            game_id="2025_08_NE_BUF",
            kickoff="Sun 1:00 PM",
            status="upcoming",
            home="BUF",
            away="NE",
            home_name="Bills",
            away_name="Patriots",
            matchup="Patriots at Bills",
            home_win_prob=F.pct(0.73),
            away_win_prob=F.pct(0.27),
            favored="BUF",
            expected_margin=F.margin(7.6, "BUF", "NE"),
            predicted_score=PredictedScore(home=F.points(28.8), away=F.points(21.2)),
            confidence="solid",
            model_vs_consensus=Consensus(
                direction="higher_on_home",
                size="notable",
                team="BUF",
                team_name="Bills",
                text="notably higher on the Bills than consensus",
            ),
        ),
    ]
    highlights = GameHighlights(
        most_lopsided=[
            HighlightGame(
                game_id="2025_08_NE_BUF", matchup="Patriots at Bills", team="BUF",
                team_name="Bills", prob=F.pct(0.73), rank_note="the most lopsided call",
            ),
        ],
        closest=[
            HighlightGame(
                game_id="2025_08_KC_DEN", matchup="Chiefs at Broncos", team="KC",
                team_name="Chiefs", prob=F.pct(0.59), rank_note="the closest game",
            ),
        ],
    )  # fmt: skip
    trends = [
        TrendItem(
            team="DET",
            team_name="Lions",
            direction="down",
            trend_delta=F.epa_change(-0.049, 3),
            window=F.weeks(3),
            net_rating=F.epa(0.015),
            drivers=[
                TrendDriver(
                    unit="pass defense",
                    change=F.driver_change("pass defense", 0.058),
                    effect="worse",
                )
            ],
            evidence=["recently without Christian Mahogany (G)"],
            people=["Christian Mahogany"],
        )
    ]
    uh = [
        UnderHoodItem(
            player="Ja'Marr Chase",
            player_id="00-0036900",
            team="CIN",
            team_name="Bengals",
            position="WR",
            metric="avg_separation",
            label="average separation",
            kind="riser",
            last_week=F.yards(4.1),
            season_avg=F.yards(2.9),
            norm_note="his average over the previous 6 games",
            volume=F.count(9),
            volume_label="targets",
            rank_note="highest among WRs with 5+ targets",
            source="NGS",
        )
    ]
    watch = [
        WatchItem(
            player="Jaylen Warren",
            player_id="00-0037000",
            team="PIT",
            team_name="Steelers",
            position="RB",
            opponent="GB",
            opponent_name="Packers",
            game_id="2025_08_PIT_GB",
            target="scrimmage yards",
            baseline=F.amount(61.3, "scrimmage yards"),
            usage_metric="carry share",
            usage_recent=F.share(0.58),
            usage_before=F.share(0.41),
            usage_before_note="earlier this season",
            opp_def_rank=F.text_num("3rd-weakest", 3),
            opp_def_unit="run defense",
        )
    ]
    data = dict(
        meta=meta,
        report_card=rc,
        games=games,
        game_highlights=highlights,
        team_trends=trends,
        under_the_hood=uh,
        players_to_watch=watch,
    )
    data.update(overrides)
    return Payload(**data)
