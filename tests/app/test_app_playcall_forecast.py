"""PC02: the forecast and its grades on the control room's Play calling pages (backend).

The same endpoints as PC01 (`/api/weeks/{S}/{W}/play-calls`, `/api/playcalling/teams/{team}`) now
carry the week's forecast (`matchups[].forecast`, `forecast`, `graded`, `shifts_kind`,
`forecast_hint`) and, on a team page, the Next game card (`next_game.forecast`) and the season
card (`forecast_record`). Tree: `playcall_fixtures.make_world(pc02=True)`, where week 5 has seven
forecast games (one graded after Thursday night), week 4 a backfill graded in full, week 3 a
forecast nobody graded, week 1 the new season's, and week 2 none; every default row is a boring
0.3-point forecast and the rows that matter are pinned by hand in `PINS_5` / `PINS_1` (their
z-scores and wording are worked out in that file's comment and below). Nothing reads the real
data root.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, NamedTuple

import playcall_fixtures as F
import polars as pl
import pytest
import test_app_playcalling as T  # the PC01 contract machinery (Contract, check_answer)
from app_helpers import make_client, make_paths
from fastapi.testclient import TestClient
from playcall_fixtures import World, g5, make_world

import nflengine.app.readers.playcalling as R
from nflengine.playcalling import forecast as PF
from nflengine.playcalling import forecast_runs as FR

MINUS = "−"
WEEK5 = "/api/weeks/2026/5/play-calls"
SHIPPED_ORDER = ["proe", "run_middle", "play_action_rate", "blitz_rate", "rushers_avg"]
OFFENSE_ORDER = ["proe", "run_middle", "play_action_rate"]
DEFENSE_ORDER = ["blitz_rate", "rushers_avg"]


class Pc02(NamedTuple):
    root: Path
    paths: Any
    world: World
    client: TestClient


@pytest.fixture(scope="module")
def pc(tmp_path_factory) -> Pc02:
    root = tmp_path_factory.mktemp("pc02")
    paths = make_paths(root)
    world = make_world(paths, pc02=True)
    return Pc02(root, paths, world, make_client(root))


def fresh(tmp_path: Path) -> Pc02:
    """A tree of its own, for a test that edits files."""
    paths = make_paths(tmp_path)
    return Pc02(tmp_path, paths, make_world(paths, pc02=True), make_client(tmp_path))


def get(client: TestClient, url: str) -> dict[str, Any]:
    r = client.get(url)
    assert r.status_code == 200, (url, r.status_code, r.text[:300])
    return r.json()


def tab(p: Pc02, week: int = 5) -> dict[str, Any]:
    return get(p.client, f"/api/weeks/2026/{week}/play-calls")


def page(p: Pc02, team: str, side: str = "offense") -> dict[str, Any]:
    return get(p.client, f"/api/playcalling/teams/{team}?season=2026&side={side}")


def game(body: dict[str, Any], gid: str) -> dict[str, Any]:
    return next(g for g in body["games"] if g["game_id"] == gid)


def values(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [v for g in body["games"] for m in g["matchups"] for v in m["forecast"]]


def value(body: dict[str, Any], gid: str, team: str, target: str) -> dict[str, Any]:
    (v,) = [
        v
        for m in game(body, gid)["matchups"]
        for v in m["forecast"]
        if v["team"] == team and v["target"] == target
    ]
    return v


def pct(x: float) -> str:
    return f"{x * 100:.1f}%".replace("-", MINUS)


def points(x: float) -> str:
    return f"{x * 100:+.1f}".replace("-", MINUS)


# ---- the fixture is what the runs write ----------------------------------------------------------


def test_the_fixture_files_have_the_runs_columns(pc):
    folder = pc.paths.playcalling / "2026"
    for week in (1, 3, 4, 5):
        df = pl.read_parquet(folder / f"week{week:02d}" / "forecast.parquet")
        assert df.columns == FR.FORECAST_COLUMNS, week
        meta = json.loads((folder / f"week{week:02d}" / "forecast.json").read_text("utf-8"))
        assert meta["week"] == week and meta["version"] == F.PC02_VERSION
    sb = pl.read_parquet(folder / "playcall_scoreboard.parquet")
    assert sb.columns == FR.SCOREBOARD_COLUMNS
    # (and one status the pages don't know, in week 3)
    assert set(sb["status"]) == {"graded", "waiting_ftn", "not_played", "settled"}
    assert F.PC02_VERSION and R._VERSION_RE.match(F.PC02_VERSION)
    model = json.loads((pc.paths.models / "playcall" / "production.json").read_text("utf-8"))
    assert model == {"version": F.PC02_VERSION}
    assert (pc.paths.models / "playcall" / F.PC02_VERSION / "params.json").is_file()


# ---- the week tab: the forecast block ------------------------------------------------------------


def test_week5_forecast_block(pc):
    body = tab(pc)
    fc = body["forecast"]
    assert body["shifts_kind"] == "forecast" and body["forecast_hint"] is None
    assert {
        k: fc[k] for k in ("version", "made_at", "as_of_week", "backfill", "after_kickoff")
    } == {
        "version": F.PC02_VERSION,
        "made_at": "2026-10-06T10:05:00+00:00",
        "as_of_week": 5,
        "backfill": False,
        "after_kickoff": [],
    }
    assert fc["range_level"] == 0.8
    assert set(fc) == {"version", "made_at", "as_of_week", "backfill", "after_kickoff",
                       "range_level", "targets", "note"}  # fmt: skip
    # the shipped targets only, in the registry's order, with the model's record for each
    assert [t["target"] for t in fc["targets"]] == SHIPPED_ORDER
    by = {t["target"]: t for t in fc["targets"]}
    assert by["blitz_rate"]["backtest"] == {
        "shipped": True, "mae": 0.0731, "mae_season_to_date": 0.0759, "wins": 3, "seasons": 4,
        "coverage": 0.81,
    }  # fmt: skip
    assert by["proe"]["backtest"]["wins"] == 5 and by["proe"]["backtest"]["seasons"] == 7
    for t in fc["targets"]:
        want = R.target_info(t["target"])
        assert {k: v for k, v in t.items() if k != "backtest"} == want
    assert by["run_middle"]["context"] == "venue" and by["proe"]["context"] == "opponent"
    assert by["rushers_avg"]["unit"] == "mean" and by["rushers_avg"]["digits"] == 2
    assert by["run_middle"]["help"].startswith("Designed runs up the middle")
    assert "stats crew" in by["run_middle"]["help"]
    # the note says how to read a forecast, with the noise floor
    assert "80%" in fc["note"] and "noisy" in fc["note"] and "inside the noise" in fc["note"]
    assert body["note"] == fc["note"]  # the tab's own note is the same text
    assert body["week"] == 5 and body["window"] == "season"


def test_the_tab_without_a_forecast_is_the_descriptive_one(pc):
    body = tab(pc, 2)
    assert body["forecast"] is None and body["graded"] is None
    assert body["shifts_kind"] == "descriptive"
    assert body["forecast_hint"] == (
        "No forecast for week 2 yet: `uv run nfl playcalling forecast --season 2026 --week 2` "
        "(after the week's `nfl playcalling build`)."
    )
    assert "isn't a forecast" in body["note"]
    assert all(m["forecast"] == [] for g in body["games"] for m in g["matchups"])


def test_a_forecast_nobody_graded_says_so(pc):
    body = tab(pc, 3)
    assert body["forecast"] is not None and body["graded"] is None
    assert body["forecast_hint"] == (
        "Not graded yet: `uv run nfl playcalling grade --season 2026 --week 3` after the week's "
        "games are in the play-calling tables."
    )
    assert body["shifts_kind"] == "forecast"
    assert len(values(body)) == 2 * 2 * 5  # two games, two teams, five shipped targets
    assert all(v["status"] is None and v["actual"] is None for v in values(body))


def test_a_scoreboard_status_the_pages_do_not_know_is_not_a_grade(pc):
    odd = value(tab(pc, 1), g5("KC", "LV", 1), "LV", "run_middle")  # the scoreboard says "settled"
    assert odd["status"] is None and odd["actual"] is None and odd["beat_season_to_date"] is None


def test_grades_of_an_earlier_forecast_say_the_forecast_was_made_again(tmp_path):
    """Grades exist for the week but none belongs to a row of this forecast (it was made again
    after grading): no summary, and the hint names the command that grades this one."""
    p = fresh(tmp_path)
    sb = pl.read_parquet(p.paths.playcalling / "2026" / "playcall_scoreboard.parquet")
    other = sb.with_columns(
        pl.when(pl.col("week") == 5)
        .then(pl.col("game_id") + "_old")
        .otherwise(pl.col("game_id"))
        .alias("game_id")
    )
    F.write_scoreboard(p.paths, other.to_dicts())
    body = tab(p)
    assert body["graded"] is None
    assert body["forecast_hint"] == (
        "The forecast was made again after grading: "
        "`uv run nfl playcalling grade --season 2026 --week 5` grades this one."
    )
    assert all(v["status"] is None for v in values(body))
    assert tab(p, 4)["graded"]["graded"] == 14  # the other weeks' grades are untouched


STALE_HINT = (
    "The forecast was made again after grading: "
    "`uv run nfl playcalling grade --season 2026 --week {week}` grades this one."
)


def remake(p: Pc02, week: int, *, where=None, made_at=None, version=None) -> None:
    """The forecast file as `nfl playcalling forecast` writes it again after grading: a new
    `made_at` (and/or model version) on the rows in `where` (default: all)."""
    path = p.paths.playcalling / "2026" / f"week{week:02d}" / "forecast.parquet"
    df = pl.read_parquet(path)
    hit = where if where is not None else pl.lit(True)
    if made_at is not None:
        df = df.with_columns(
            pl.when(hit).then(made_at).otherwise(pl.col("made_at")).alias("made_at")
        )
    if version is not None:
        df = df.with_columns(
            pl.when(hit).then(pl.lit(version)).otherwise(pl.col("version")).alias("version")
        )
    df.write_parquet(path)


def test_a_forecast_made_again_after_grading_is_ungraded_until_it_is_graded_again(tmp_path):
    p = fresh(tmp_path)
    assert tab(p)["graded"]["graded"] == 4  # graded as made at 10:05
    remake(p, 5, made_at=F.MADE_AT + dt.timedelta(hours=1))
    body = tab(p)
    assert body["graded"] is None and body["forecast_hint"] == STALE_HINT.format(week=5)
    for v in values(body):  # nothing of the earlier grade is carried over to the new forecast
        assert v["status"] is None and v["actual"] is None and v["n"] is None
        assert v["in_range"] is None and v["beat_season_to_date"] is None
    assert body["forecast"] is not None and body["shifts_kind"] == "forecast"
    # the team card's Next game shows the new forecast with no grade, too
    ari = page(p, "ARI")["next_game"]
    assert all(v["status"] is None for v in ari["forecast"]["own"] + ari["forecast"]["opponent"])
    # (the season card counts the scoreboard's own rows, whatever forecast they came from)
    assert page(p, "ARI")["forecast_record"]["overall"]["graded"] == 2


def test_a_different_model_version_does_not_match_the_grade_either(tmp_path):
    p = fresh(tmp_path)
    remake(p, 5, version="2019-2024_20261009T000000Z")
    body = tab(p)
    assert body["graded"] is None and body["forecast_hint"] == STALE_HINT.format(week=5)
    assert all(v["status"] is None for v in values(body))
    # same made_at but a model version the grade doesn't have: a different forecast
    q = fresh(tmp_path / "second")
    remake(
        q, 4, version="2019-2024_20261009T000000Z", where=pl.col("game_id") == g5("KC", "DEN", 4)
    )
    assert tab(q, 4)["graded"]["graded"] == 4  # only Atlanta at New Orleans still matches


def test_only_the_rows_that_were_made_again_lose_their_grade(tmp_path):
    p = fresh(tmp_path)
    kc_den = pl.col("game_id") == g5("KC", "DEN", 4)
    remake(p, 4, where=kc_den, made_at=F.MADE_AT + dt.timedelta(minutes=5))
    body = tab(p, 4)
    # Atlanta at New Orleans keeps its grades: 4 play-by-play rows graded, 6 waiting for FTN
    assert (body["graded"]["rows"], body["graded"]["graded"], body["graded"]["waiting"]) == (
        10,
        4,
        6,
    )
    assert body["forecast_hint"] is None  # something is graded, so it isn't the stale case
    kc = value(body, g5("KC", "DEN", 4), "KC", "proe")
    assert kc["status"] is None and kc["actual"] is None  # made again
    atl = value(body, g5("ATL", "NO", 4), "ATL", "proe")
    assert atl["status"] == "graded" and atl["actual"] == 0.02
    # the grade the page shows is for the rows that match, by hand: ATL .006 / .010, NO .014 / .010,
    # ATL run .047 / .050, NO run .013 / .010
    assert body["graded"]["overall"] == {
        "graded": 4, "mae": 0.02, "mae_season_to_date": 0.02, "beat": 2, "in_range": 1.0,
    }  # fmt: skip


def test_the_shift_floor_for_proe_is_one_and_a_half_points(pc):
    """CHI's PROE moves exactly 1.5 points and is listed; MIN's moves 1.49 and isn't. (Both
    have a range of +-3 points, so the floor, not the z-score, decides.)"""
    body = tab(pc, 3)
    sea, la, chi_s = body["shifts"]  # z .75, .70, .50
    assert (chi_s["game_id"], chi_s["offense"], chi_s["defense"], chi_s["metric"]) == (
        g5("MIN", "CHI", 3), "CHI", "MIN", "proe",
    )  # fmt: skip
    assert (chi_s["shift"], chi_s["change"]) == (0.5, 0.015)
    assert chi_s["text"] == (
        "CHI's PROE against MIN: +2.5 expected, +1.0 so far (+1.5 points). "
        "Against MIN's defense, offenses have done more of it than they usually do (+1.4 points)."
    )
    # the wording follows the games played: one game is "few", three are "games" (two is the edge)
    assert (sea["metric"], sea["offense"], sea["defense"], sea["shift"]) == (
        "blitz_rate", "LA", "SEA", 0.75,
    )  # fmt: skip
    assert sea["text"] == (
        "SEA's blitz against LA: 23.5% expected, 25.0% so far (" + MINUS + "1.5 points). "
        "Few dropbacks so far (30): the forecast leans on last season and the league ("
        + MINUS
        + "2.0 points)."
    )
    assert (la["metric"], la["offense"], la["defense"], la["shift"]) == (
        "play_action_rate", "LA", "SEA", 0.7,
    )  # fmt: skip
    assert la["text"] == (
        "LA's play-action against SEA: 25.5% expected, 22.0% so far (+3.5 points). The forecast "
        "pulls LA's rate so far toward last season and the league (+3.0 points): 3 games move a "
        "rate less than they seem to."
    )
    chi = value(body, g5("MIN", "CHI", 3), "CHI", "proe")
    mn = value(body, g5("MIN", "CHI", 3), "MIN", "proe")
    assert (chi["shift"], mn["shift"]) == (0.015, 0.0149)
    # the plays behind a number: 20 is enough, 19 is a small sample
    assert (chi["n_so_far"], chi["small"], mn["n_so_far"], mn["small"]) == (20, False, 19, True)


def test_a_forecast_that_ties_the_season_to_date_rate_has_not_beaten_it(pc):
    body = tab(pc, 1)
    v = value(body, g5("BAL", "BUF", 1), "BAL", "proe")
    assert (v["status"], v["actual"], v["in_range"]) == ("graded", 0.012, True)
    assert v["beat_season_to_date"] is False  # |0.014 - 0.012| = |0.010 - 0.012|
    g = body["graded"]
    assert (g["rows"], g["graded"], g["waiting"]) == (1, 1, 0)
    assert g["overall"] == {
        "graded": 1, "mae": 0.002, "mae_season_to_date": 0.002, "beat": 0, "in_range": 1.0,
    }  # fmt: skip
    assert body["forecast_hint"] is None
    rec = page(pc, "BAL")["forecast_record"]
    assert rec["weeks"] == [1] and rec["overall"]["beat"] == 0


def test_a_backfill_names_the_games_that_had_kicked_off(pc):
    fc = tab(pc, 4)["forecast"]
    assert fc["backfill"] is True
    assert fc["after_kickoff"] == ["KC at DEN, week 4", "ATL at NO, week 4"]
    assert tab(pc, 1)["forecast"]["after_kickoff"] == [
        "KC at LV, week 1", "LA at LAC, week 1", "BAL at BUF, week 1",
    ]  # fmt: skip
    assert tab(pc, 5)["forecast"]["backfill"] is False


# ---- only shipped rows, only real teams and games -----------------------------------------------


def test_only_shipped_rows_reach_the_answers(pc):
    body = tab(pc)
    vs = values(body)
    assert len(vs) == 7 * 2 * 5
    assert {v["target"] for v in vs} == set(SHIPPED_ORDER)
    # (deep shots are a PC01 metric, so they stay in the tab's descriptive rows; the forecast
    # blocks never name them)
    blocks = [body["forecast"], body["graded"], body["shifts"], vs, tab(pc, 4)["forecast"],
              tab(pc, 4)["graded"], tab(pc, 4)["shifts"]]  # fmt: skip
    for team, side in (("MIN", "defense"), ("DEN", "offense"), ("KC", "offense")):
        t = page(pc, team, side)
        blocks += [t["next_game"], t["forecast_record"]]
    for b in blocks:
        assert "deep_shot_rate" not in json.dumps(b), "an unshipped target leaked"
    # the unshipped deep-shot row is pinned to a huge shift (z 20): it would head the list
    assert all(s["metric"] != "deep_shot_rate" for s in body["shifts"])
    # and the graded summary doesn't count the unshipped deep-shot rows the scoreboard holds
    assert body["graded"]["rows"] == 70 and body["graded"]["graded"] == 4


def test_decoy_rows_are_dropped(pc):
    """A code that is no team, an unknown target and a game the schedule doesn't have."""
    body = tab(pc)
    text = json.dumps(body)
    assert "XYZ" not in text and "bogus_target" not in text
    vs = values(body)
    assert {v["team"] for v in vs} <= set(F.TEAMS)
    assert "2026_05_KC_WAS" not in text  # KC is on a bye; the decoy game isn't in the schedule
    assert not any(v["team"] == "KC" for v in vs)
    # all three decoys carry a 30-point shift on a one-point range: none reached the shifts
    assert not any(s["offense"] == "KC" or s["defense"] == "KC" for s in body["shifts"])
    assert sum(1 for v in vs if v["team"] == "MIN") == 5  # not 6, not 7: the XYZ copy is gone


def test_no_nan_or_infinity_in_the_new_answers(pc):
    def boom(constant: str):
        raise AssertionError(f"{constant} in a JSON answer")

    for url in all_urls():
        text = pc.client.get(url).text
        json.loads(text, parse_constant=boom)
    dal = value(tab(pc), g5("DAL", "DEN"), "DAL", "proe")
    assert dal["lo"] is None and dal["hi"] == 0.1  # the stored NaN reached the browser as null
    assert dal["forecast"] == 0.051  # .010 + 0 + .040 + the PROE default script .001


def all_urls() -> list[str]:
    urls = [f"/api/weeks/2026/{w}/play-calls" for w in range(1, 8)]
    for team in F.TEAMS:
        urls += [f"/api/playcalling/teams/{team}?season=2026&side={s}"
                 for s in ("offense", "defense")]  # fmt: skip
    return urls


# ---- the numbers of a forecast, by hand ----------------------------------------------------------


def test_a_forecast_value_holds_the_stored_numbers(pc):
    v = value(tab(pc), g5("MIA", "MIN"), "MIN", "rushers_avg")
    assert v == {
        "target": "rushers_avg", "team": "MIN", "opponent": "MIA", "caller": "defense",
        "forecast": 4.75, "lo": 4.25, "hi": 5.25,  # 4.30 + 0.05 + 0.40, +- 0.5
        "season_to_date": 4.3, "league": 4.2, "shrunk": 4.35, "team_term": 4.35,
        "opponent_effect": 0.4, "context_team": "MIA", "script_effect": 0.0,
        "shift": 0.45, "n_so_far": 400, "games_so_far": 4, "small": False,
        "status": "not_played", "actual": None, "n": None, "in_range": None,
        "beat_season_to_date": None,
    }  # fmt: skip
    p = value(tab(pc), g5("MIA", "MIN"), "MIN", "proe")
    assert (p["forecast"], p["lo"], p["hi"], p["season_to_date"]) == (0.052, 0.002, 0.102, 0.01)
    assert (p["team_term"], p["opponent_effect"], p["script_effect"]) == (0.012, 0.03, 0.01)
    assert p["caller"] == "offense" and p["context_team"] == "MIA" and p["shift"] == 0.042


def test_a_venue_target_names_the_home_team_whoever_is_visiting(pc):
    body = tab(pc)
    for team in ("CHI", "CAR"):
        v = value(body, g5("CAR", "CHI"), team, "run_middle")
        assert v["context_team"] == "CHI"  # the home stadium's stats crew
    chi = value(body, g5("CAR", "CHI"), "CHI", "run_middle")
    assert (chi["forecast"], chi["opponent_effect"], chi["shift"]) == (0.272, -0.03, -0.028)
    # every other target's context is the opponent
    other = value(body, g5("CAR", "CHI"), "CHI", "proe")
    assert other["context_team"] == "CAR"


def test_small_samples_are_flagged(pc):
    body = tab(pc, 1)
    lv = value(body, g5("KC", "LV", 1), "LV", "blitz_rate")
    assert lv["n_so_far"] == 0 and lv["games_so_far"] == 0 and lv["small"] is True
    big = value(body, g5("KC", "LV", 1), "KC", "proe")
    assert big["n_so_far"] == 400 and big["small"] is False
    edge = tab(pc)
    assert value(edge, g5("HOU", "IND"), "IND", "blitz_rate")["small"] is False  # 70 >= 20


def test_matchups_hold_the_offenses_calls_then_the_defenses(pc):
    g = game(tab(pc), g5("MIA", "MIN"))
    away, home = g["matchups"]
    assert (away["offense"], away["defense"]) == ("MIA", "MIN")
    assert [(v["team"], v["target"], v["caller"]) for v in away["forecast"]] == [
        *[("MIA", t, "offense") for t in OFFENSE_ORDER],
        *[("MIN", t, "defense") for t in DEFENSE_ORDER],
    ]
    assert (home["offense"], home["defense"]) == ("MIN", "MIA")
    assert [(v["team"], v["target"]) for v in home["forecast"]] == [
        *[("MIN", t) for t in OFFENSE_ORDER],
        *[("MIA", t) for t in DEFENSE_ORDER],
    ]
    # a game the forecast does not cover has an empty list, not a missing key
    sea = game(tab(pc), "2026_05_SEA_SF")
    assert [m["forecast"] for m in sea["matchups"]] == [[], []]
    # PC01's rows are still there beside the forecast
    assert away["rows"] and set(away) == {"offense", "defense", "rows", "forecast"}


# ---- the grades ----------------------------------------------------------------------------------


def test_graded_rows_carry_the_actual_and_how_it_went(pc):
    body = tab(pc)
    thu = g5("ARI", "ATL")
    got = {(v["team"], v["target"]): v for m in game(body, thu)["matchups"] for v in m["forecast"]}
    ari = got[("ARI", "proe")]  # forecast 0.014, season to date 0.010, actual 0.030
    assert (ari["status"], ari["actual"], ari["n"], ari["in_range"]) == ("graded", 0.03, 50, True)
    assert ari["beat_season_to_date"] is True  # |0.014 - 0.030| < |0.010 - 0.030|
    rm = got[("ARI", "run_middle")]  # forecast 0.203, range 0.153-0.253, actual 0.26
    assert (rm["actual"], rm["in_range"], rm["beat_season_to_date"]) == (0.26, False, True)
    atl = got[("ATL", "proe")]  # actual -0.020: 0.034 off against 0.030 for season to date
    assert (atl["actual"], atl["in_range"], atl["beat_season_to_date"]) == (-0.02, True, False)
    # Thursday's FTN charting has not landed: no actual yet
    pa = got[("ARI", "play_action_rate")]
    assert pa["status"] == "waiting_ftn"
    assert (pa["actual"], pa["n"], pa["in_range"], pa["beat_season_to_date"]) == (None,) * 4
    # everything else this week hasn't been played
    other = value(body, g5("MIA", "MIN"), "MIN", "proe")
    assert other["status"] == "not_played" and other["actual"] is None


def test_week5_graded_summary_by_hand(pc):
    g = tab(pc)["graded"]
    # 7 games x 2 teams x 5 shipped targets carry a status: 4 graded (Thursday's play-by-play
    # targets), 6 waiting for FTN, 60 not played
    assert (g["rows"], g["graded"], g["waiting"]) == (70, 4, 66)
    # overall: ARI proe .016 / .020, ARI run .057 / .060, ATL proe .034 / .030, ATL run .007 / .010
    assert g["overall"] == {
        "graded": 4, "mae": 0.0285, "mae_season_to_date": 0.03, "beat": 3, "in_range": 0.75,
    }  # fmt: skip
    by = {t["target"]: t for t in g["targets"]}
    assert list(by) == SHIPPED_ORDER  # every shipped target, graded or not
    assert by["proe"] == {"target": "proe", "graded": 2, "mae": 0.025, "mae_season_to_date": 0.025,
                          "beat": 1, "in_range": 1.0}  # fmt: skip
    assert by["run_middle"] == {
        "target": "run_middle",
        "graded": 2,
        "mae": 0.032,
        "mae_season_to_date": 0.035,
        "beat": 2,
        "in_range": 0.5,
    }
    for k in ("play_action_rate", "blitz_rate", "rushers_avg"):
        assert by[k] == {"target": k, "graded": 0, "mae": None, "mae_season_to_date": None,
                         "beat": 0, "in_range": None}  # fmt: skip


def independent_summary(pc, week: int, actuals: dict) -> dict[str, Any]:
    """The week's grade from the fixture's own rows and rates, by the documented rules."""
    rows = [
        r
        for r in pc.world.fc[week]
        if r["shipped"] and (r["game_id"], r["team"], r["target"]) in actuals
    ]
    errs = []
    for r in rows:
        a = actuals[(r["game_id"], r["team"], r["target"])]
        errs.append((r, abs(r["forecast"] - a), abs(r["b2"] - a), r["lo"] <= a <= r["hi"]))
    pts = [e for e in errs if PF.get_target(e[0]["target"]).unit != "mean"]
    return {
        "graded": len(errs),
        "mae": round(sum(e[1] for e in pts) / len(pts), 6),
        "mae_season_to_date": round(sum(e[2] for e in pts) / len(pts), 6),
        "beat": sum(1 for e in errs if e[1] < e[2]),
        "in_range": round(sum(1 for e in errs if e[3]) / len(errs), 4),
    }


def test_week4_graded_summary_matches_an_independent_recount(pc):
    g = tab(pc, 4)["graded"]
    # 2 games x 2 teams x 5 targets = 20 rows; Atlanta at New Orleans waits for FTN on 3 of 5
    assert (g["rows"], g["graded"], g["waiting"]) == (20, 14, 6)
    want = independent_summary(pc, 4, F.ACTUAL_4)
    assert g["overall"] == want
    # by hand: twelve rows in points sum to .450 on both sides (a tie at 3.75 points); rushers
    # (a count) are left out of the MAE but count in the wins and in the range
    assert g["overall"]["mae"] == 0.0375 and g["overall"]["mae_season_to_date"] == 0.0375
    assert g["overall"]["beat"] == 7 and g["overall"]["in_range"] == 0.7857
    rush = next(t for t in g["targets"] if t["target"] == "rushers_avg")
    assert rush["graded"] == 2 and rush["mae"] == 0.25  # (0.27 + 0.23) / 2, in rushers
    assert rush["mae_season_to_date"] == 0.25 and rush["beat"] == 1 and rush["in_range"] == 1.0


def test_the_overall_mae_leaves_out_a_target_in_rushers(pc):
    g = tab(pc, 4)["graded"]
    only_points = [t for t in g["targets"] if t["target"] != "rushers_avg"]
    pooled = sum(t["mae"] * t["graded"] for t in only_points) / sum(
        t["graded"] for t in only_points
    )
    assert g["overall"]["mae"] == pytest.approx(pooled, abs=1e-6)
    assert g["overall"]["graded"] == 14  # the count still includes them


# ---- the shifts ----------------------------------------------------------------------------------


def expected_shifts() -> list[dict[str, Any]]:
    """The week's eight, best first, worked out from PINS_5's numbers (z = |shift| / half-width)."""
    return [
        dict(game=g5("NE", "NO"), off="NE", dfn="NO", metric="blitz_rate", z=1.1, change=-0.055,
             text="NO's blitz against NE: 24.5% expected, 30.0% so far (" + MINUS + "5.5 points). "
             "The forecast pulls NO's rate so far toward last season and the league (" + MINUS
             + "4.5 points): 4 games move a rate less than they seem to."),
        dict(game=g5("MIA", "MIN"), off="MIA", dfn="MIN", metric="rushers_avg", z=0.9, change=0.45,
             text="MIN's pass rushers against MIA: 4.75 expected, 4.30 so far (+0.45). Against "
             "MIA's offense, defenses have done more of it than they usually do (+0.40)."),
        dict(game=g5("DAL", "DEN"), off="DEN", dfn="DAL", metric="play_action_rate", z=0.8,
             change=0.04,
             text="DEN's play-action against DAL: 29.0% expected, 25.0% so far (+4.0 points). The "
             "forecast pulls DEN's rate so far toward last season and the league (+3.0 points): "
             "4 games move a rate less than they seem to."),
        dict(game=g5("HOU", "IND"), off="HOU", dfn="IND", metric="blitz_rate", z=0.75,
             change=-0.015,
             text="IND's blitz against HOU: 23.5% expected, 25.0% so far ("
             + MINUS + "1.5 points). "
             "Few dropbacks so far (70): the forecast leans on last season and the league ("
             + MINUS + "2.0 points)."),
        dict(game=g5("CAR", "CHI"), off="CHI", dfn="CAR", metric="run_middle", z=0.56,
             change=-0.028,
             text="CHI's runs up the middle against CAR: 27.2% expected, 30.0% so far (" + MINUS
             + "2.8 points). In CHI's home games the stats crew, who chart run direction, has "
             "logged fewer of them than the teams' own rates (" + MINUS + "3.0 points)."),
        dict(game=g5("DET", "GB"), off="GB", dfn="DET", metric="proe", z=0.5, change=0.03,
             text="GB's PROE against DET: +3.5 expected, +0.5 so far (+3.0 points). The game "
             "script: GB favoured by 6.5, total 47.5 (+2.5 points)."),
        dict(game=g5("DET", "GB"), off="GB", dfn="DET", metric="rushers_avg", z=0.45, change=0.05,
             text="DET's pass rushers against GB: 4.35 expected, 4.30 so far (+0.05). Against "
             "GB's offense, defenses have done more of it than they usually do (+0.05)."),
        dict(game=g5("MIA", "MIN"), off="MIA", dfn="MIN", metric="proe", z=0.43, change=-0.03,
             text="MIA's PROE against MIN: " + MINUS + "1.0 expected, +2.0 so far (" + MINUS
             + "3.0 points). The game script: MIA a 3-point underdog, total 44 (" + MINUS
             + "2.0 points)."),
    ]  # fmt: skip


def test_week5_shifts_are_the_eight_biggest_by_the_rules(pc):
    got = tab(pc)["shifts"]
    want = expected_shifts()
    assert [(s["game_id"], s["offense"], s["defense"], s["metric"], s["shift"], s["change"])
            for s in got] == [(w["game"], w["off"], w["dfn"], w["metric"], w["z"], w["change"])
                              for w in want]  # fmt: skip
    for s, w in zip(got, want, strict=True):
        assert s["text"] == w["text"], s["metric"]
        assert set(s) == {"game_id", "offense", "defense", "metric", "shift", "change", "text"}
    assert [s["shift"] for s in got] == sorted((s["shift"] for s in got), reverse=True)


def test_what_the_shift_rules_leave_out(pc):
    """z is |forecast - season to date| over the half-width of the range; at least 1.5 points
    (0.05 rushers); one per team and game; at most eight."""
    listed = {(s["game_id"], s["metric"], s["offense"], s["defense"]) for s in tab(pc)["shifts"]}
    # below the floor, however tight the range (z would be 37 and 14 000)
    assert (g5("DET", "GB"), "proe", "DET", "GB") not in listed  # DET's PROE: 1.49 points
    assert (g5("CAR", "CHI"), "rushers_avg", "CHI", "CAR") not in listed  # 0.0499 < 0.05
    assert (g5("HOU", "IND"), "blitz_rate", "HOU", "IND") in listed  # IND's: exactly 1.5 points
    # exactly the floor is in: IND blitz (1.5 points) and DET rushers (0.05)
    assert any(g == g5("DET", "GB") and m == "rushers_avg" for g, m, _, _ in listed)
    # a zero-width range and a missing lower end give no z
    assert (g5("HOU", "IND"), "blitz_rate", "IND", "HOU") not in listed  # HOU's blitz, no width
    assert (g5("DAL", "DEN"), "proe", "DAL", "DEN") not in listed
    # one per team and game: MIN's PROE (z .84) loses to MIN's rushers (z .90)
    min_rows = [s for s in tab(pc)["shifts"] if s["game_id"] == g5("MIA", "MIN")]
    assert [(s["metric"], s["offense"], s["defense"]) for s in min_rows] == [
        ("rushers_avg", "MIA", "MIN"),  # MIN's defense, z .90
        ("proe", "MIA", "MIN"),  # MIA's offense, z .43 (MIN's PROE, z .84, lost to its rushers)
    ]  # fmt: skip
    # the cap: three more candidates (z .4167, .16, .109) are cut
    assert len(listed) == 8
    cut = [
        (g5("NE", "NO"), "blitz_rate", "NO", "NE"),  # NE's blitz
        (g5("HOU", "IND"), "play_action_rate", "HOU", "IND"),  # HOU's play-action
        (g5("DAL", "DEN"), "rushers_avg", "DEN", "DAL"),  # DAL's rushers
    ]
    assert not [c for c in cut if c in listed]


def test_the_new_season_wording_in_week_one(pc):
    body = tab(pc, 1)
    assert body["window"] == "last_season" and body["shifts_kind"] == "forecast"
    buf, lac, s = body["shifts"]  # z .50, .44, .40
    assert (s["offense"], s["defense"], s["metric"], s["shift"], s["change"]) == (
        "KC", "LV", "blitz_rate", 0.4, -0.02,
    )  # fmt: skip
    assert s["text"] == (
        "LV's blitz against KC: 26.0% expected, 28.0% last season (" + MINUS + "2.0 points). "
        "A new season: the forecast pulls last season's rate toward the league (rosters and "
        "coaches change; " + MINUS + "3.0 points)."
    )
    # the spread decides how the script is put: a half-point favourite is still a favourite,
    # a one-point underdog is not a pick'em (the pick'em case is the missing-lines test below)
    assert (lac["offense"], lac["defense"], lac["metric"], lac["shift"]) == (
        "LAC", "LA", "proe", 0.44,
    )  # fmt: skip
    assert lac["text"] == (
        "LAC's PROE against LA: +3.0 expected, +1.0 so far (+2.0 points). "
        "The game script: LAC favoured by 0.5, total 41 (+2.0 points)."
    )
    assert (buf["offense"], buf["defense"], buf["metric"], buf["shift"]) == (
        "BUF", "BAL", "proe", 0.5,
    )  # fmt: skip
    assert buf["text"] == (
        "BUF's PROE against BAL: "
        + MINUS
        + "1.5 expected, +1.0 so far ("
        + MINUS
        + "2.5 points). The game script: BUF a 1-point underdog, total 38.5 ("
        + MINUS
        + "2.5 points)."
    )


def test_a_pickem_game_and_a_missing_total_read_plainly(tmp_path):
    p = fresh(tmp_path)
    df = pl.read_parquet(p.paths.playcalling / "2026" / "week05" / "forecast.parquet")
    # GB's lines go missing: the script sentence names no favourite and no total
    df = df.with_columns(
        pl.when((pl.col("game_id") == g5("DET", "GB")) & (pl.col("team") == "GB"))
        .then(None)
        .otherwise(pl.col("spread"))
        .alias("spread"),
        pl.when((pl.col("game_id") == g5("DET", "GB")) & (pl.col("team") == "GB"))
        .then(None)
        .otherwise(pl.col("total"))
        .alias("total"),
    )
    df.write_parquet(p.paths.playcalling / "2026" / "week05" / "forecast.parquet")
    gb = next(s for s in tab(p)["shifts"] if s["offense"] == "GB" and s["metric"] == "proe")
    assert gb["text"].endswith("The game script: GB in a pick'em (+2.5 points).")


# ---- the team page: the Next game card -----------------------------------------------------------


def test_the_next_game_card_splits_own_and_opponent_calls(pc):
    off = page(pc, "MIN", "offense")["next_game"]
    assert (off["game_id"], off["week"], off["opponent"], off["home"]) == (
        g5("MIA", "MIN"), 5, "MIA", True,
    )  # fmt: skip
    fc = off["forecast"]
    assert off["forecast_hint"] is None
    # an offense page: MIN's own offensive calls, and MIA's defensive ones in the same matchup
    assert [(v["team"], v["caller"], v["target"]) for v in fc["own"]] == [
        ("MIN", "offense", t) for t in OFFENSE_ORDER
    ]
    assert [(v["team"], v["caller"], v["target"]) for v in fc["opponent"]] == [
        ("MIA", "defense", t) for t in DEFENSE_ORDER
    ]
    dfn = page(pc, "MIN", "defense")["next_game"]["forecast"]
    assert [(v["team"], v["target"]) for v in dfn["own"]] == [("MIN", t) for t in DEFENSE_ORDER]
    assert [(v["team"], v["target"]) for v in dfn["opponent"]] == [
        ("MIA", t) for t in OFFENSE_ORDER
    ]
    # the card has the tab's meta block, and the same values as the tab
    meta = tab(pc)["forecast"]
    assert {k: v for k, v in fc.items() if k not in ("own", "opponent")} == meta
    tab_values = {(v["team"], v["target"]): v for v in values(tab(pc))
                  if v["opponent"] in ("MIA", "MIN") and v["team"] in ("MIA", "MIN")}  # fmt: skip
    for v in fc["own"] + fc["opponent"]:
        assert v == tab_values[(v["team"], v["target"])]


def test_the_next_game_card_carries_a_grade_once_the_game_is_played(pc):
    ari = page(pc, "ARI")["next_game"]  # Thursday's game: ahead of the page's clock (Tuesday)
    assert ari["game_id"] == g5("ARI", "ATL")
    own = {v["target"]: v for v in ari["forecast"]["own"]}
    assert own["proe"]["status"] == "graded" and own["proe"]["actual"] == 0.03
    assert own["play_action_rate"]["status"] == "waiting_ftn"
    assert {v["target"]: v["status"] for v in ari["forecast"]["opponent"]} == {
        "blitz_rate": "waiting_ftn", "rushers_avg": "waiting_ftn",
    }  # fmt: skip


def test_the_card_reads_only_its_own_game_and_week(pc):
    # MIN also has a week-3 forecast (at CHI): it never appears on the week-5 card
    fc = page(pc, "MIN")["next_game"]["forecast"]
    assert {v["opponent"] for v in fc["own"] + fc["opponent"]} == {"MIA", "MIN"}
    assert {v["team"] for v in fc["own"]} == {"MIN"}
    # a week with no forecast: the command that makes it
    kc = page(pc, "KC")["next_game"]
    assert kc["week"] == 6 and kc["forecast"] is None
    assert kc["forecast_hint"] == (
        "No forecast for week 6 yet: `uv run nfl playcalling forecast --season 2026 --week 6`."
    )


def test_the_card_for_a_team_the_forecast_skipped(pc):
    """LA at LAC is in the schedule but not in week 5's forecast (made before it was scheduled):
    no card, and the hint names the command that makes it again."""
    for side in ("offense", "defense"):
        la = page(pc, "LA", side)["next_game"]
        assert la["game_id"] == g5("LA", "LAC") and la["forecast"] is None
        assert "nothing for this game" in la["forecast_hint"]
        assert "uv run nfl playcalling forecast --season 2026 --week 5" in la["forecast_hint"]
    assert page(pc, "LAC")["next_game"]["forecast"] is None  # the other side of the same game
    # a week with no forecast at all gets the other hint
    assert "nothing for this game" not in page(pc, "KC")["next_game"]["forecast_hint"]


# ---- the team page: the season card --------------------------------------------------------------


def test_kc_offense_forecast_record_by_hand(pc):
    rec = page(pc, "KC")["forecast_record"]
    assert (rec["season"], rec["weeks"]) == (2026, [4])
    # proe .036 / .040 (beat), play-action .083 / .080 (lost, out of range), run .007 / .010 (beat)
    assert rec["overall"] == {
        "graded": 3, "mae": 0.042, "mae_season_to_date": 0.043333, "beat": 2, "in_range": 0.6667,
    }  # fmt: skip
    assert [t["target"] for t in rec["targets"]] == ["proe", "run_middle", "play_action_rate"]
    by = {t["target"]: t for t in rec["targets"]}
    p = by["proe"]
    assert (p["graded"], p["mae"], p["mae_season_to_date"], p["beat"], p["in_range"]) == (
        1, 0.036, 0.04, 1, 1.0,
    )  # fmt: skip
    assert by["play_action_rate"]["beat"] == 0 and by["play_action_rate"]["in_range"] == 0.0
    # each target brings its name, unit and the model's record
    assert p["short"] == "PROE" and p["context"] == "opponent"
    assert p["backtest"]["wins"] == 5 and p["backtest"]["mae"] == 0.0412
    assert by["run_middle"]["context"] == "venue"


def test_kc_defense_record_leaves_rushers_out_of_the_overall_mae(pc):
    rec = page(pc, "KC", "defense")["forecast_record"]
    assert [t["target"] for t in rec["targets"]] == ["blitz_rate", "rushers_avg"]
    # blitz .047 / .050 (beat), rushers .27 / .30 (beat); the overall MAE is blitz alone
    assert rec["overall"] == {
        "graded": 2, "mae": 0.047, "mae_season_to_date": 0.05, "beat": 2, "in_range": 1.0,
    }  # fmt: skip
    rush = rec["targets"][1]
    assert (rush["mae"], rush["mae_season_to_date"], rush["graded"]) == (0.27, 0.3, 1)


def test_the_record_follows_the_games_graded_in_each_week(pc):
    rec = page(pc, "ARI")["forecast_record"]  # Thursday's game, week 5: two graded rows
    assert rec["weeks"] == [5]
    assert rec["overall"] == {
        "graded": 2, "mae": 0.0365, "mae_season_to_date": 0.04, "beat": 2, "in_range": 0.5,
    }  # fmt: skip
    # the unshipped deep-shot rows the scoreboard also holds (graded, far off) are not in it
    assert "deep_shot_rate" not in json.dumps(rec)
    # a team with nothing graded (waiting for FTN, or not played) has no record
    assert page(pc, "ATL", "defense")["forecast_record"] is None  # FTN calls, waiting
    assert page(pc, "MIN")["forecast_record"] is None  # not played yet
    assert page(pc, "LA")["forecast_record"] is None  # no forecast at all


def test_a_record_of_only_rushers_has_no_overall_mae(tmp_path):
    p = fresh(tmp_path)
    sb = pl.read_parquet(p.paths.playcalling / "2026" / "playcall_scoreboard.parquet")
    keep = sb.filter(
        (pl.col("team") == "DEN") & (pl.col("target") == "rushers_avg") & (pl.col("week") == 4)
    )
    F.write_scoreboard(p.paths, keep.to_dicts())
    rec = page(p, "DEN", "defense")["forecast_record"]
    assert rec["overall"]["graded"] == 1 and rec["overall"]["beat"] == 0
    assert rec["overall"]["mae"] is None and rec["overall"]["mae_season_to_date"] is None
    assert rec["overall"]["in_range"] == 1.0
    assert rec["targets"][0]["mae"] == 0.23  # but the target's own MAE is in rushers


# ---- the model's record: a bad version never names a file ----------------------------------------


def targets_backtest(body: dict[str, Any]) -> list:
    return [t["backtest"] for t in body["forecast"]["targets"]]


@pytest.mark.parametrize(
    "version",
    [
        "../evil", "..\\evil", "2019-2025_20261008T120000Z/../x", "x", "", "2019-2025",
        "2019-2025_20261008T120000", "2019-2025_20261008T120000Zextra",
        " 2019-2025_20261008T120000Z", "2019-2025_20261008T120000Z\n", 20261008, None, ["a"],
    ],
)  # fmt: skip
def test_a_bad_version_gives_no_backtest_and_no_file_read_outside_the_model_folder(
    tmp_path, monkeypatch, version
):
    p = fresh(tmp_path)
    # a decoy params.json where a traversal would land (models/evil), with a marker number
    decoy = p.paths.models / "evil"
    decoy.mkdir(parents=True)
    (decoy / "params.json").write_text(
        json.dumps(
            {"targets": {"proe": {"shipped": True, "backtest": {"mae": {"model": 0.123456}}}}}
        ),
        encoding="utf-8",
    )
    (p.paths.models / "playcall" / "production.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    read: list[Path] = []
    real = R.read_json

    def spy(path):
        read.append(Path(path))
        return real(path)

    monkeypatch.setattr(R, "read_json", spy)
    body = tab(p)
    assert targets_backtest(body) == [None] * 5  # the forecast itself still shows
    assert len(values(body)) == 70 and body["forecast"]["version"] == F.PC02_VERSION
    assert "0.123456" not in json.dumps(body)
    model_root = p.paths.models / "playcall"
    for path in read:  # the pointer, a week's forecast.json, the build: nothing else
        assert path == model_root / "production.json" or p.paths.playcalling in path.parents, path
    assert not any("evil" in str(path) for path in read)
    assert model_root / "production.json" in read  # the pointer was looked at, and refused
    team = page(p, "MIN", "defense")
    assert all(t["backtest"] is None for t in team["next_game"]["forecast"]["targets"])


def test_a_model_folder_that_is_missing_or_malformed_gives_no_backtest(tmp_path):
    p = fresh(tmp_path)
    folder = p.paths.models / "playcall" / F.PC02_VERSION
    params = folder / "params.json"
    for text in ("not json", json.dumps([1, 2]), json.dumps({"targets": [1]}),
                 json.dumps({"targets": {"proe": "x", "bogus": {"backtest": {}}}})):  # fmt: skip
        params.write_text(text, encoding="utf-8")
        assert targets_backtest(tab(p)) == [None] * 5, text
    params.unlink()
    assert targets_backtest(tab(p)) == [None] * 5
    (p.paths.models / "playcall" / "production.json").unlink()
    assert targets_backtest(tab(p)) == [None] * 5
    # the tab and the shifts do not depend on the model file
    assert len(tab(p)["shifts"]) == 8


def test_a_partial_backtest_record_has_nulls_not_errors(tmp_path):
    p = fresh(tmp_path)
    doc = F.model_doc()
    doc["targets"]["proe"]["backtest"] = {"mae": {"model": float("nan")}, "wins_vs_b2": "5",
                                          "seasons": None, "coverage_80": float("inf")}  # fmt: skip
    del doc["targets"]["blitz_rate"]["backtest"]
    F.write_model(p.paths, doc)
    by = {t["target"]: t["backtest"] for t in tab(p)["forecast"]["targets"]}
    assert by["proe"] == {"shipped": True, "mae": None, "mae_season_to_date": None, "wins": None,
                          "seasons": None, "coverage": None}  # fmt: skip
    assert by["blitz_rate"] == {"shipped": True, "mae": None, "mae_season_to_date": None,
                                "wins": None, "seasons": None, "coverage": None}  # fmt: skip
    assert by["rushers_avg"]["mae"] == 0.512


# ---- the files: damaged, missing, hostile --------------------------------------------------------


def test_a_missing_or_unreadable_forecast_is_no_forecast(tmp_path):
    p = fresh(tmp_path)
    week = p.paths.playcalling / "2026" / "week05"
    (week / "forecast.json").unlink()  # the meta is written last: no meta, no forecast
    body = tab(p)
    assert body["forecast"] is None and body["shifts_kind"] == "descriptive"
    assert body["forecast_hint"].startswith("No forecast for week 5 yet")
    (week / "forecast.json").write_text(json.dumps({"version": "x"}), encoding="utf-8")
    (week / "forecast.parquet").write_bytes(b"not a parquet file")
    assert tab(p)["forecast"] is None
    (week / "forecast.json").write_text("[1, 2]", encoding="utf-8")
    assert tab(p)["forecast"] is None
    # the team page: same
    assert page(p, "MIN")["next_game"]["forecast"] is None
    # a damaged scoreboard means "not graded", not an error
    q = fresh(tmp_path / "second")
    (q.paths.playcalling / "2026" / "playcall_scoreboard.parquet").write_bytes(b"junk")
    body = tab(q, 5)
    assert body["forecast"] is not None and body["graded"] is None
    assert body["forecast_hint"].startswith("Not graded yet")
    assert page(q, "KC")["forecast_record"] is None
    assert all(v["status"] is None for v in values(body))


def test_free_text_from_the_forecast_file_is_scrubbed_and_labels_are_built_not_copied(tmp_path):
    p = fresh(tmp_path)
    week = p.paths.playcalling / "2026" / "week05"
    meta = json.loads((week / "forecast.json").read_text("utf-8"))
    meta["version"] = "api_key=abc123 leaked"
    meta["made_at"] = "x" * 200
    codes = F.TEAMS
    meta["games_after_kickoff"] = [
        "../../etc/passwd", "2026_05_ARI_ATL", "2026_05_ZZZ_ATL", 5, None, {"a": 1},
        *[f"2026_05_{codes[i % 32]}_{codes[(i + 1) % 32]}" for i in range(25)],
    ]  # fmt: skip
    (week / "forecast.json").write_text(json.dumps(meta), encoding="utf-8")
    fc = tab(p)["forecast"]
    assert "abc123" not in json.dumps(fc) and len(fc["made_at"]) <= 40
    assert fc["backfill"] is True
    assert fc["after_kickoff"][0] == "ARI at ATL, week 5"  # only well-formed ids become labels
    assert len(fc["after_kickoff"]) == 20  # at most twenty
    assert all(re.fullmatch(r"[A-Z]{2,3} at [A-Z]{2,3}, week \d+", s) for s in fc["after_kickoff"])
    assert "etc/passwd" not in json.dumps(fc) and "ZZZ" not in json.dumps(fc)


def test_a_relocated_codes_row_reads_as_the_current_team(tmp_path):
    p = fresh(tmp_path)
    week = p.paths.playcalling / "2026" / "week05"
    df = pl.read_parquet(week / "forecast.parquet").with_columns(
        pl.when(pl.col("team") == "MIN").then(pl.lit("XYZ")).otherwise(pl.col("team")).alias("team")
    )
    df.write_parquet(week / "forecast.parquet")  # every MIN row now has no valid team
    body = tab(p)
    assert not any(v["team"] == "XYZ" for v in values(body))
    assert not any(v["team"] == "MIN" for v in values(body))
    assert len(values(body)) == 70 - 5


# ---- safety --------------------------------------------------------------------------------------


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        if p.is_file()
        else "dir"
        for p in root.rglob("*")
    }


def test_no_endpoint_writes_anything_under_the_data_root(pc):
    before = tree(pc.root)
    for url in all_urls():
        pc.client.get(url)
    assert tree(pc.root) == before


def test_forecast_reads_are_short_filtered_and_never_memory_mapped(pc, monkeypatch):
    import polars as pl_mod
    import pyarrow.parquet as pq_mod

    table_reads: list[tuple[str, dict, tuple]] = []
    real = pq_mod.read_table

    def spy(path, *args, **kw):
        table_reads.append((Path(str(path)).name, kw, tuple(Path(str(path)).parts[-3:])))
        return real(path, *args, **kw)

    monkeypatch.setattr(pq_mod, "read_table", spy)
    frames: list[str] = []
    real_frame = pl_mod.read_parquet
    monkeypatch.setattr(
        pl_mod, "read_parquet", lambda s, *a, **k: frames.append(str(s)) or real_frame(s, *a, **k)
    )
    for url in (WEEK5, "/api/weeks/2026/4/play-calls", "/api/playcalling/teams/MIN?side=defense",
                "/api/playcalling/teams/KC"):  # fmt: skip
        get(pc.client, url)
    names = {n for n, _, _ in table_reads}
    assert {"forecast.parquet", "playcall_scoreboard.parquet"} <= names
    assert names <= {"team_tendencies.parquet", "team_game_tendencies.parquet",
                     "forecast.parquet", "playcall_scoreboard.parquet"}  # fmt: skip
    assert all(kw.get("memory_map") is False for _, kw, _ in table_reads)
    assert all(kw.get("filters") for _, kw, _ in table_reads), "a whole file is never read"
    # only shipped rows are even read from a forecast file
    for name, kw, _ in table_reads:
        if name == "forecast.parquet":
            assert ("shipped", "=", True) in kw["filters"]
    assert not any("plays_enriched" in f for f in frames)


def test_a_new_forecast_can_replace_the_files_while_the_app_is_open(tmp_path):
    """The weekly forecast overwrites forecast.parquet with a rename; a held handle or memory
    map would make that fail on Windows."""
    p = fresh(tmp_path)
    for url in (WEEK5, "/api/playcalling/teams/MIN", "/api/playcalling/teams/KC"):
        get(p.client, url)
    week = p.paths.playcalling / "2026" / "week05"
    for name in ("forecast.parquet", "forecast.json"):
        tmp = week / f".{name}.tmp"
        tmp.write_bytes((week / name).read_bytes())
        tmp.replace(week / name)
    sb = p.paths.playcalling / "2026" / "playcall_scoreboard.parquet"
    tmp = sb.with_name(".sb.tmp")
    tmp.write_bytes(sb.read_bytes())
    tmp.replace(sb)
    assert len(tab(p)["shifts"]) == 8


# ---- the contract with web/src/api/types.ts ------------------------------------------------------


@pytest.fixture(scope="module")
def contract() -> T.Contract:
    return T.Contract(T.pc01_source())


def test_the_ts_types_name_the_pc02_interfaces(contract):
    assert {
        "PlaycallTarget",
        "PlaycallForecastValue",
        "PlaycallForecastMeta",
        "PlaycallGradeSummary",
        "PlaycallWeekGrade",
        "PlaycallNextForecast",
        "PlaycallForecastRecord",
    } <= set(contract.ifaces)
    assert set(contract.fields_of("PlaycallForecastValue")) == {
        "target", "team", "opponent", "caller", "forecast", "lo", "hi", "season_to_date", "league",
        "shrunk", "team_term", "opponent_effect", "context_team", "script_effect", "shift",
        "n_so_far", "games_so_far", "small", "status", "actual", "n", "in_range",
        "beat_season_to_date",
    }  # fmt: skip
    status = contract.fields_of("PlaycallForecastValue")["status"][0]
    assert status[0] == "union" and ("name", "null") in status[1]  # null before grading
    assert contract.literals(("union", [m for m in status[1] if m != ("name", "null")])) == {
        "graded", "waiting_ftn", "waiting_build", "not_played", "no_plays",
    }  # fmt: skip
    assert contract.literals(contract.fields_of("PlaycallTarget")["context"][0]) == {
        "opponent", "venue",
    }  # fmt: skip
    assert contract.fields_of("PlayCallsShift")["change"][1] is True  # optional: forecast only
    assert contract.literals(contract.fields_of("PlayCallsResponse")["shifts_kind"][0]) == {
        "descriptive", "forecast",
    }  # fmt: skip
    rec_targets = contract.fields_of("PlaycallForecastRecord")["targets"][0]
    assert rec_targets == (
        "arr",
        ("inter", [("name", "PlaycallTarget"), ("name", "PlaycallGradeSummary")]),
    )  # a target's name and unit plus its grade: an intersection, per target


def test_every_pc02_answer_matches_the_typescript_types(pc, contract):
    from collections import defaultdict

    seen: dict = defaultdict(set)
    problems: list[str] = []
    urls = [f"/api/weeks/2026/{w}/play-calls" for w in (1, 2, 3, 4, 5)]
    urls += [
        f"/api/playcalling/teams/{t}?season=2026&side={s}"
        for t, s in (
            ("MIN", "offense"),
            ("MIN", "defense"),
            ("KC", "offense"),
            ("KC", "defense"),
            ("ARI", "offense"),
            ("LA", "offense"),
        )  # fmt: skip
    ]
    for url in urls:
        iface = "PlayCallsResponse" if "play-calls" in url else "PlaycallTeamResponse"
        problems += T.check_answer(contract, get(pc.client, url), iface, url, seen)
    assert not problems, "\n".join(problems[:20])
    # every field of every PC02 type was non-null at least once in these answers
    for owner in (
        "PlaycallForecastValue",
        "PlaycallForecastMeta",
        "PlaycallTarget",
        "PlaycallGradeSummary",
        "PlaycallWeekGrade",
        "PlaycallNextForecast",
        "PlaycallForecastRecord",
        "PlayCallsShift",
    ):
        for field in contract.fields_of(owner):
            assert "value" in seen.get((owner, field), set()), f"{owner}.{field} was never non-null"
    # and the nullable ones were null somewhere too
    for owner, field in (
        ("PlaycallForecastValue", "actual"), ("PlaycallForecastValue", "status"),
        ("PlaycallForecastValue", "lo"), ("PlaycallNextGame", "forecast"),
        ("PlayCallsResponse", "forecast"), ("PlayCallsResponse", "graded"),
        ("PlaycallTeamResponse", "forecast_record"),
    ):  # fmt: skip
        assert "null" in seen.get((owner, field), set()), f"{owner}.{field} was never null"


def test_the_pc02_contract_check_catches_a_drifting_answer(pc, contract):
    from collections import defaultdict

    body = tab(pc)
    seen: dict = defaultdict(set)
    assert T.check_answer(contract, body, "PlayCallsResponse", "ok", seen) == []
    bad = json.loads(json.dumps(body))
    bad["forecast"]["surprise"] = 1
    del bad["graded"]["overall"]["beat"]
    bad["forecast"]["targets"][0]["context"] = "stadium"
    bad["games"][0]["matchups"][0]["forecast"] = [{"target": "proe"}]
    bad["shifts"][0]["change"] = "big"
    bad["shifts_kind"] = "guess"
    del bad["forecast_hint"]
    text = "\n".join(T.check_answer(contract, bad, "PlayCallsResponse", "bad", seen))
    for needle in (
        "field 'surprise' is not in PlaycallForecastMeta",
        "PlaycallGradeSummary.beat is missing",
        "'stadium'",
        "PlaycallForecastValue.team is missing",
        "'big' is not a number",
        "'guess'",
        "PlayCallsResponse.forecast_hint is missing",
    ):
        assert needle in text, needle
    team = page(pc, "KC")
    team["forecast_record"]["targets"][0]["mae_season_to_date"] = "x"
    team["forecast_record"]["targets"][0]["unexpected"] = 1
    del team["forecast_record"]["targets"][0]["backtest"]
    text = "\n".join(T.check_answer(contract, team, "PlaycallTeamResponse", "team", seen))
    assert "'x' is not a number" in text
    assert "field 'unexpected' is in none of" in text
    assert "PlaycallTarget.backtest is missing" in text
