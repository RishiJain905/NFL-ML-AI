"""FL02 (D158): the Availability and Outcomes answers and pre-flight's `forecastlab` gate against
`web/src/api/types.ts` (its FL02 block). Every answer the routes give is checked field by field
against the TypeScript interface the tabs read (a missing field, an extra one, a wrong type, a
null where the type says no), with the small TypeScript parser of PC01's contract test. The
answers are real: the routes over files written here in `forecastlab.schema`'s layout and
schemas, so a change to the reader's fields shows up here."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import test_app_playcalling as T
from app_helpers import make_client, make_paths

from nflengine.app import preflight
from nflengine.forecastlab import schema as S
from nflengine.paths import DataPaths

TYPES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "types.ts"
SEASON, WEEK = 2026, 5
NOW = dt.datetime(2026, 10, 7, 14, 0, tzinfo=dt.UTC)
UTC = dt.UTC


def fl02_source() -> str:
    """The FL02 block of types.ts (up to the next block header) without comments."""
    text = TYPES_TS.read_text(encoding="utf-8")
    m = re.search(r"^// -{10,} FL02: Forecast lab", text, re.M)
    assert m, "no FL02 block in web/src/api/types.ts"
    end = re.search(r"^// -{4,} ", text[m.end() :], re.M)
    block = text[m.start() : m.end() + end.start()] if end else text[m.start() :]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    return re.sub(r"//[^\n]*", "", block)


@pytest.fixture(scope="module")
def contract() -> T.Contract:
    return T.Contract(fl02_source())


# ---- the files ---------------------------------------------------------------------------------


def _frame(rows: list[dict[str, Any]], schema: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame([{c: r.get(c) for c in schema} for r in rows], schema=schema)


def _player(pid: str, name: str, team: str, opp: str, pos: str, group: str, view: str, **kw: Any):
    row = {
        "season": SEASON, "week": WEEK, "game_id": "2026_05_TB_ATL",
        "kickoff_utc": dt.datetime(2026, 10, 11, 17, 0, tzinfo=UTC),
        "created_at": dt.datetime(2026, 10, 6, 14, 40, tzinfo=UTC), "view": view,
        "player_id": pid, "player": name, "team": team, "opponent": opp, "home": team == "ATL",
        "position": pos, "pgroup": group, "side": "off", "report_status": None,
        "practice_status": None, "prev_status": None, "roster_status": "ACT", "snap_l1": 0.7,
        "snap_l2": 0.7, "games_season": 4, "p_play": 0.95, "base_p_play": 0.94,
        "snap_p10": 0.5, "snap_p50": 0.7, "snap_p90": 0.85, "base_snap": 0.7, "tgt_share": 0.2,
        "base_tgt_share": 0.2, "carry_share": 0.1, "base_carry_share": 0.1, "exp_snap": 0.66,
        "at_risk": False, "model_version": "avail-v1", "trained_through": "2026-w04",
        "played": None, "actual_snap": None, "actual_tgt_share": None, "actual_carry_share": None,
    }  # fmt: skip
    return {**row, **kw}


def write_world(paths: DataPaths) -> None:
    """Both models promoted, week 5's files with both views, a graded week 4 in the scoreboards."""
    for fam, shipped in (("availability", ["play", "snap"]), ("curves", ["rec_yds-wrte"])):
        f = S.production_file(paths, fam)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"version": f"{fam}-v1", "shipped": shipped}), encoding="utf-8")
    week = S.week_dir(paths, SEASON, WEEK)
    week.mkdir(parents=True, exist_ok=True)

    # availability: a starter who might sit (both views, Saturday says Questionable), the RB who
    # inherits, a receiver whose share rises (a mover), a played row (graded fields filled)
    rows = []
    for view in S.VIEWS:
        sat = view == "saturday"
        rows += [
            _player("00-0000001", "A. Receiver", "TB", "ATL", "WR", "WR", view,
                    report_status="Questionable" if sat else None,
                    practice_status="limited" if sat else None, prev_status="Questionable",
                    p_play=0.55 if sat else 0.8, base_p_play=0.7, at_risk=True,
                    snap_p50=0.6, base_snap=0.8, tgt_share=0.15, base_tgt_share=0.22),
            _player("00-0000002", "B. Back", "TB", "ATL", "RB", "RB", view,
                    snap_p50=0.75, base_snap=0.6, carry_share=0.6, base_carry_share=0.45,
                    tgt_share=0.12, base_tgt_share=0.08),
            _player("00-0000003", "C. End", "ATL", "TB", "TE", "TE", view, played=True,
                    actual_snap=0.81, actual_tgt_share=0.2, actual_carry_share=0.0),
        ]  # fmt: skip
    _frame(rows, S.AVAIL_SCHEMA).write_parquet(week / "availability.parquet")
    inherit = [
        {
            "season": SEASON,
            "week": WEEK,
            "game_id": "2026_05_TB_ATL",
            "view": view,
            "team": "TB",
            "kind": "targets",
            "out_player_id": "00-0000001",
            "out_player": "A. Receiver",
            "out_position": "WR",
            "out_p_play": 0.55,
            "out_share": 0.22,
            "player_id": "00-0000002",
            "player": "B. Back",
            "position": "RB",
            "share_if_in": 0.08,
            "share_if_out": 0.13,
            "delta": 0.05,
            "rank": 1,
        }  # fmt: skip
        for view in S.VIEWS
    ]
    _frame(inherit, S.INHERIT_SCHEMA).write_parquet(week / "inherit.parquet")
    (week / "availability.json").write_text(
        json.dumps(
            {
                "views": {
                    "tuesday": {"made_at": "2026-10-06T14:40:00+00:00", "rows": 3},
                    "saturday": {"made_at": "2026-10-10T15:05:00+00:00", "rows": 3},
                },
                "version": "availability-v1",
                "trained_through": "2026-w04",
                "shipped": {
                    "tuesday": ["play", "snap"],
                    "saturday": ["play", "snap", "tgt_share", "carry_share"],
                },
                "games_after_kickoff": ["2026_05_TB_ATL"],
            }
        ),
        encoding="utf-8",
    )
    graded_at = dt.datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
    score = [
        {
            "season": SEASON,
            "week": w,
            "view": "tuesday",
            "output": out,
            "group": "ALL",
            "n": 400,
            "brier_model": 0.05,
            "brier_baseline": 0.06,
            "mae_model": 0.08,
            "mae_baseline": 0.09,
            "coverage_80": 0.81,
            "mode": "live",
            "graded_at": graded_at,
        }  # fmt: skip
        for w in (3, 4)
        for out in ("play", "snap")
    ]
    _frame(score, S.AVAIL_SCORE_SCHEMA).write_parquet(
        S.season_dir(paths, SEASON) / "availability_scoreboard.parquet"
    )

    # curves: two receivers, one graded
    cdf = [min(1.0, round((i + 1) / 120, 5)) for i in range(135)]
    cdf[-1] = 1.0
    marks = [
        {"mark": 24.5, "p_over": 0.8, "bell_p_over": 0.78},
        {"mark": 49.5, "p_over": 0.55, "bell_p_over": 0.6},
    ]

    def curve(pid: str, name: str, team: str, opp: str, **kw: Any) -> dict[str, Any]:
        row = {
            "season": SEASON, "week": WEEK, "game_id": "2026_05_TB_ATL",
            "kickoff_utc": dt.datetime(2026, 10, 11, 17, 0, tzinfo=UTC),
            "created_at": dt.datetime(2026, 10, 6, 14, 45, tzinfo=UTC), "player_id": pid,
            "player": name, "team": team, "opponent": opp, "position": "WR", "group": "WR/TE",
            "target": "rec_yds", "target_key": "rec_yds-wrte", "target_label": "receiving yards",
            "kind": "amount", "p10": 22.0, "p50": 58.0, "p90": 104.0, "mean": 61.2,
            "source_version": "player-v12", "grid_lo": -15, "cdf": cdf, "q05": 15.0,
            "q10": 22.0, "q25": 38.0, "q50": 57.0, "q75": 80.0, "q90": 104.0, "q95": 121.0,
            "band": 7.0, "p_band": 0.12, "bell_p_band": 0.14, "marks": marks,
            "n_analogs": 900, "model_version": "curves-v1", "actual": None, "played": None,
        }  # fmt: skip
        return {**row, **kw}

    curves = [
        curve("00-0000001", "A. Receiver", "TB", "ATL"),
        curve("00-0000004", "D. Wideout", "ATL", "TB", actual=71.0, played=True),
    ]
    _frame(curves, S.CURVE_SCHEMA).write_parquet(week / "curves.parquet")
    (week / "curves.json").write_text(
        json.dumps(
            {
                "made_at": "2026-10-06T14:45:00+00:00",
                "version": "curves-v1",
                "shipped": ["rec_yds-wrte"],
                "source_version": "player-v12",
                "games_after_kickoff": ["2026_05_TB_ATL"],
            }
        ),
        encoding="utf-8",
    )
    cscore = [
        {
            "season": SEASON,
            "week": w,
            "target_key": "rec_yds-wrte",
            "n": 150,
            "crps_model": 14.1,
            "crps_bell": 14.9,
            "crps_own": 16.0,
            "brier_model": 0.21,
            "brier_bell": 0.22,
            "brier_own": 0.24,
            "band_pred": 0.12,
            "band_hit": 0.11,
            "pit_ece": 0.02,
            "mode": "live",
            "graded_at": graded_at,
        }  # fmt: skip
        for w in (3, 4)
    ]
    _frame(cscore, S.CURVE_SCORE_SCHEMA).write_parquet(
        S.season_dir(paths, SEASON) / "curves_scoreboard.parquet"
    )


# ---- the answers -------------------------------------------------------------------------------


def contract_answers(tmp_path: Path) -> list[tuple[str, str, Any]]:
    out: list[tuple[str, str, Any]] = []
    full = tmp_path / "full"
    write_world(make_paths(full))
    fc = make_client(full)
    empty = make_client(tmp_path / "empty")  # nothing promoted: `no_model`
    for client in (fc, empty):
        for week in (WEEK, 9):  # 9: promoted (full) but nothing for the week: `none`
            out.append(("AvailabilityResponse", f"/api/weeks/{SEASON}/{week}/availability",
                        T.get(client, f"/api/weeks/{SEASON}/{week}/availability")))  # fmt: skip
            out.append(("OutcomesResponse", f"/api/weeks/{SEASON}/{week}/outcomes",
                        T.get(client, f"/api/weeks/{SEASON}/{week}/outcomes")))  # fmt: skip
    # one team's players (`?team=`): the week's other parts don't change
    for team in ("TB", "ATL"):  # ATL's tight end has played: the graded fields
        url = f"/api/weeks/{SEASON}/{WEEK}/availability?team={team}"
        out.append(("AvailabilityResponse", url, T.get(fc, url)))
    # one stat's rows (`?target=`); the week's other answers don't change
    url = f"/api/weeks/{SEASON}/{WEEK}/outcomes?target=rec_yds-wrte"
    out.append(("OutcomesResponse", url, T.get(fc, url)))
    for pid in ("00-0000001", "00-0000004"):
        url = f"/api/weeks/{SEASON}/{WEEK}/outcomes/curve?player={pid}&target=rec_yds-wrte"
        out.append(("OutcomeCurve", url, T.get(fc, url)))
    # pre-flight's gate: before the week is published, nothing due, and with steps due
    full_paths = make_paths(full)
    for published, busy in ((False, False), (True, False), (True, True)):
        out.append(
            (
                "ForecastLabGate",
                f"forecastlab_gate(published={published}, busy={busy})",
                preflight.forecastlab_gate(
                    full_paths, SEASON, 9, NOW, published=published, busy=busy
                ),
            )
        )
    gate = T.get(fc, "/api/preflight").get("forecastlab")
    out.append(("ForecastLabGate", "/api/preflight .forecastlab", gate))
    return out


def test_the_ts_parser_sees_the_fl02_types(contract):
    assert {
        "ForecastLabUpdateStep", "ForecastLabGate", "AvailabilityInherit", "AvailabilityPlayer",
        "AvailabilityAtRisk", "AvailabilityMover", "AvailabilityViewMeta", "AvailabilityScoreRow",
        "AvailabilityResponse", "CurveMark", "CurveGap", "OutcomeRow", "OutcomeTarget",
        "CurveScoreRow", "OutcomesResponse", "OutcomeCurve",
    } <= set(contract.ifaces)  # fmt: skip
    assert contract.literals(contract.types["ForecastLabStatus"]) == {"ok", "none", "no_model"}
    assert contract.literals(contract.types["AvailabilityView"]) == set(S.VIEWS)
    step = contract.fields_of("ForecastLabUpdateStep")["step"][0]
    from nflengine.forecastlab.update import STEPS

    assert contract.literals(step) == set(STEPS)


def test_every_answer_matches_the_typescript_types(contract, tmp_path):
    seen: dict = defaultdict(set)
    problems: list[str] = []
    answers = contract_answers(tmp_path)
    for iface, where, body in answers:
        contract.check(body, ("name", iface), where, problems, seen)
    assert not problems, "\n".join(problems[:30])
    statuses = {b["status"] for i, _, b in answers if i == "AvailabilityResponse"}
    assert statuses == {"ok", "none", "no_model"}
    # the fixtures exercise the types: every field of every interface an answer used was
    # non-null at least once (a field the answers never fill is a field this check hasn't seen)
    owners = {o for o, _ in seen}
    for owner in owners:
        if owner == "(inline)":
            continue
        # an interface that extends another is checked on its own fields (the parent's are
        # checked where the parent itself is used: every player is in `players`)
        for field in contract.ifaces[owner][1]:
            assert "value" in seen.get((owner, field), set()), f"{owner}.{field} was never non-null"
    unused = set(contract.ifaces) - owners
    assert not unused, f"types.ts declares {sorted(unused)} but no answer uses them"


# `scrub()` (ops/summary.py `_SECRET_KV`) masks `name: value` for any name that contains one of
# these, so a field named like that breaks the leak scan (control-room skill §4a). The parquet
# column `target_key` and the `?target=` query are not fields of an answer.
SECRET_NAME = re.compile(r"key|token|secret|passw|auth|credential", re.I)


def field_names(obj: Any) -> set[str]:
    """Every dict key of an answer, however deep."""
    if isinstance(obj, dict):
        return set(obj) | {n for v in obj.values() for n in field_names(v)}
    if isinstance(obj, list):
        return {n for v in obj for n in field_names(v)}
    return set()


def test_no_field_is_named_like_a_secret(contract):
    """In the TypeScript interfaces and in every answer the routes give (a field of the
    answers that `scrub()` would mask is a failing leak scan, not a harmless name)."""
    names = {f for i in contract.ifaces for f in contract.fields_of(i)}
    assert not {n for n in names if SECRET_NAME.search(n)}


def test_no_answer_has_a_field_named_like_a_secret(tmp_path):
    answers = contract_answers(tmp_path)
    assert len(answers) >= 12
    bad = {
        (where, n)
        for _iface, where, body in answers
        for n in field_names(body)
        if SECRET_NAME.search(n)
    }
    assert not bad, sorted(bad)[:10]


def test_the_curves_scoreboard_rows_name_their_stat_not_a_key(tmp_path):
    root = tmp_path / "w"
    write_world(make_paths(root))
    c = make_client(root)
    sb = T.get(c, f"/api/weeks/{SEASON}/{WEEK}/outcomes")["scoreboard"]
    for row in (*sb["week"], *sb["season"]):
        assert row["stat"] == "rec_yds-wrte" and "target_key" not in row


def test_the_outcomes_answer_carries_one_stat_and_the_week(tmp_path):
    """`rows` is the stat in `stat` (the default receiving yards, or `?target=`); `tiles`
    covers the week; a stat the week doesn't have is a fixed-text 404 the page shows as is."""
    root = tmp_path / "w"
    write_world(make_paths(root))
    c = make_client(root)
    body = T.get(c, f"/api/weeks/{SEASON}/{WEEK}/outcomes")
    assert body["stat"] == "rec_yds-wrte"
    assert {r["stat"] for r in body["rows"]} == {"rec_yds-wrte"}
    assert body["tiles"] == {"curves": 2, "players": 2, "stats": 1}
    assert T.get(c, f"/api/weeks/{SEASON}/{WEEK}/outcomes?target=rec_yds-wrte") == body
    r = c.get(f"/api/weeks/{SEASON}/{WEEK}/outcomes?target=carries-rb")
    assert r.status_code == 404
    assert "carries" not in r.text  # never echoes the input


def test_the_availability_answer_carries_one_team_and_the_week(tmp_path):
    """`players` is one team's (`?team=`), empty without one; the might-sit list carries its
    group and the playing time at stake; an unknown team is a fixed-text 404."""
    root = tmp_path / "w"
    write_world(make_paths(root))
    c = make_client(root)
    body = T.get(c, f"/api/weeks/{SEASON}/{WEEK}/availability")
    assert body["team"] is None and body["players"] == []
    assert set(body["tiles"]["by_group"]) == {"report", "uncertain", "reserve"}
    assert all(r["risk_group"] in ("report", "uncertain", "reserve") for r in body["at_risk"])
    tb = T.get(c, f"/api/weeks/{SEASON}/{WEEK}/availability?team=TB")
    assert tb["team"] == "TB" and {r["team"] for r in tb["players"]} == {"TB"}
    r = c.get(f"/api/weeks/{SEASON}/{WEEK}/availability?team=KC")
    assert r.status_code == 404
    assert "KC" not in r.text  # never echoes the input
