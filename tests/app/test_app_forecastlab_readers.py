"""FL02 (D158): the readers behind the Availability and Outcomes tabs
(`app/readers/forecastlab.py`, the routes `/availability`, `/outcomes`, `/outcomes/curve`)
against files this test writes in `forecastlab.schema`'s layout, so each rule is shown on data
built for it:

- **no text from a file reaches the browser unmasked:** secret-shaped text and the data root's
  own path are planted in every string column and every string field of the week's files;
- **a hostile or damaged file is an empty state, never a 500;**
- **the ship gate** (an output or target that didn't ship is absent, its baseline stays);
- **the might-sit list and who inherits**, rule by rule;
- **`?team=` / `?target=` and the curve's own query**: one team or stat only, a fixed-text 404 for
  one that isn't there (never echoing the input), a fixed-text 422 for a bad pattern.

The pre-flight's gate and the field-by-field match with `web/src/api/types.ts` are in
`test_app_forecastlab_contract.py`; the plan and the run are in `tests/forecastlab`.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from app_helpers import make_client, make_paths

from nflengine.app.readers import forecastlab as R
from nflengine.forecastlab import schema as S
from nflengine.ops.summary import scrub
from nflengine.paths import DataPaths

SEASON, WEEK = 2026, 5
UTC = dt.UTC
KICK = dt.datetime(2026, 10, 11, 17, 0, tzinfo=UTC)
MADE = dt.datetime(2026, 10, 6, 14, 40, tzinfo=UTC)
GAME = "2026_05_TB_ATL"
OUTPUTS = ("play", "snap", "tgt_share", "carry_share")
AV = f"/api/weeks/{SEASON}/{WEEK}/availability"
OUT = f"/api/weeks/{SEASON}/{WEEK}/outcomes"
CURVE = f"/api/weeks/{SEASON}/{WEEK}/outcomes/curve"
COULDNT = "couldn't be read"


# ---- builders --------------------------------------------------------------------------------


def frame(rows: list[dict[str, Any]], schema: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame([{c: r.get(c) for c in schema} for r in rows], schema=schema)


def prow(pid: str, name: str, team: str = "TB", view: str = "tuesday", **kw: Any) -> dict:
    """One availability row: a healthy regular with a 95% chance of playing and 70% of the snaps."""
    row = {
        "season": SEASON, "week": WEEK, "game_id": GAME, "kickoff_utc": KICK, "created_at": MADE,
        "view": view, "player_id": pid, "player": name, "team": team, "opponent": "ATL",
        "home": team == "ATL", "position": "WR", "pgroup": "WR", "side": "off",
        "report_status": None, "practice_status": None, "prev_status": None,
        "roster_status": "ACT", "snap_l1": 0.7, "snap_l2": 0.7, "games_season": 4,
        "p_play": 0.95, "base_p_play": 0.94, "snap_p10": 0.5, "snap_p50": 0.7, "snap_p90": 0.85,
        "base_snap": 0.7, "tgt_share": 0.2, "base_tgt_share": 0.2, "carry_share": 0.1,
        "base_carry_share": 0.1, "exp_snap": 0.66, "at_risk": False, "model_version": "avail-v1",
        "trained_through": "2026-w04", "played": None, "actual_snap": None,
        "actual_tgt_share": None, "actual_carry_share": None,
    }  # fmt: skip
    return {**row, **kw}


def irow(out_pid: str, pid: str, name: str = "B. Back", view: str = "tuesday", **kw: Any) -> dict:
    """One "who inherits" row: `pid` gains 5 points of target share when `out_pid` sits."""
    row = {
        "season": SEASON, "week": WEEK, "game_id": GAME, "view": view, "team": "TB",
        "kind": "targets", "out_player_id": out_pid, "out_player": "A. Receiver",
        "out_position": "WR", "out_p_play": 0.55, "out_share": 0.22, "player_id": pid,
        "player": name, "position": "RB", "share_if_in": 0.08, "share_if_out": 0.13,
        "delta": 0.05, "rank": 1,
    }  # fmt: skip
    return {**row, **kw}


def crow(pid: str, name: str, team: str = "TB", target_key: str = "rec_yds-wrte", **kw: Any):
    """One curve row of a known target (the contract's own label, group and kind)."""
    t = S.CURVE_BY_KEY[target_key] if target_key in S.CURVE_BY_KEY else S.CURVE_TARGETS[4]
    cdf = [min(1.0, round((i + 1) / 120, 5)) for i in range(135)]
    cdf[-1] = 1.0
    marks = [
        {"mark": 24.5, "p_over": 0.8, "bell_p_over": 0.78},
        {"mark": 49.5, "p_over": 0.55, "bell_p_over": 0.6},
    ]
    row = {
        "season": SEASON, "week": WEEK, "game_id": GAME, "kickoff_utc": KICK, "created_at": MADE,
        "player_id": pid, "player": name, "team": team, "opponent": "ATL", "position": "WR",
        "group": t.group, "target": t.name, "target_key": target_key, "target_label": t.label,
        "kind": t.kind, "p10": 22.0, "p50": 58.0, "p90": 104.0, "mean": 61.2,
        "source_version": "player-v12", "grid_lo": int(t.floor), "cdf": cdf, "q05": 15.0,
        "q10": 22.0, "q25": 38.0, "q50": 57.0, "q75": 80.0, "q90": 104.0, "q95": 121.0,
        "band": t.band, "p_band": 0.12, "bell_p_band": 0.14, "marks": marks, "n_analogs": 900,
        "model_version": "curves-v1", "actual": None, "played": None,
    }  # fmt: skip
    return {**row, **kw}


def avail_meta(**over: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "views": {
            "tuesday": {"made_at": "2026-10-06T14:40:00+00:00", "rows": 3},
            "saturday": {"made_at": "2026-10-10T15:05:00+00:00", "rows": 3},
        },
        "version": "availability-v1",
        "trained_through": "2026-w04",
        "shipped": {"tuesday": list(OUTPUTS), "saturday": list(OUTPUTS)},
        "games_after_kickoff": [],
    }
    return {**meta, **over}


def curves_meta(**over: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "made_at": "2026-10-06T14:45:00+00:00",
        "version": "curves-v1",
        "shipped": ["rec_yds-wrte", "receptions-wrte", "carries-rb"],
        "source_version": "player-v12",
        "games_after_kickoff": [],
    }
    return {**meta, **over}


def week_dir(paths: DataPaths) -> Path:
    d = S.week_dir(paths, SEASON, WEEK)
    d.mkdir(parents=True, exist_ok=True)
    return d


def promote(paths: DataPaths, *families: str) -> None:
    for fam in families or S.FAMILIES:
        f = S.production_file(paths, fam)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"version": f"{fam}-v1", "shipped": []}), encoding="utf-8")


def put_availability(
    paths: DataPaths,
    rows: list[dict[str, Any]],
    inherit: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | str | None = None,
) -> None:
    """`availability.parquet` (+ `inherit.parquet`, `availability.json`); both models promoted."""
    promote(paths)
    d = week_dir(paths)
    frame(rows, S.AVAIL_SCHEMA).write_parquet(d / "availability.parquet")
    if inherit is not None:
        frame(inherit, S.INHERIT_SCHEMA).write_parquet(d / "inherit.parquet")
    text = meta if isinstance(meta, str) else json.dumps(avail_meta() if meta is None else meta)
    (d / "availability.json").write_text(text, encoding="utf-8")


def put_curves(
    paths: DataPaths, rows: list[dict[str, Any]], meta: dict[str, Any] | str | None = None
) -> None:
    promote(paths)
    d = week_dir(paths)
    frame(rows, S.CURVE_SCHEMA).write_parquet(d / "curves.parquet")
    text = meta if isinstance(meta, str) else json.dumps(curves_meta() if meta is None else meta)
    (d / "curves.json").write_text(text, encoding="utf-8")


def base_players() -> list[dict[str, Any]]:
    """The starter who might sit and his backup, as the Saturday report left them."""
    return [
        prow("00-0000001", "A. Receiver", report_status="Questionable", at_risk=True, p_play=0.6),
        prow("00-0000002", "B. Back", position="RB", pgroup="RB", base_snap=0.4, snap_p50=0.45),
    ]


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    return make_paths(tmp_path)


@pytest.fixture
def client(tmp_path, paths):
    return make_client(tmp_path)


def fetch(client, url: str, status: int = 200) -> dict[str, Any]:
    r = client.get(url)
    assert r.status_code == status, (url, r.status_code, r.text[:300])
    return r.json()


def avail(client, team: str | None = None) -> dict[str, Any]:
    return fetch(client, AV + (f"?team={team}" if team else ""))


def outcomes(client, target: str | None = None) -> dict[str, Any]:
    return fetch(client, OUT + (f"?target={target}" if target else ""))


def by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["player_id"]: r for r in rows}


def at_risk_ids(client) -> list[str]:
    return [r["player_id"] for r in avail(client)["at_risk"]]


# ---- the answers on a sound week -----------------------------------------------------------------


def test_a_sound_week_answers_ok(client, paths) -> None:
    put_availability(paths, base_players())
    put_curves(paths, [crow("00-0000001", "A. Receiver")])
    a = avail(client)
    assert (a["status"], a["hint"]) == ("ok", None)
    assert a["meta"]["shipped"] == {"tuesday": list(OUTPUTS), "saturday": list(OUTPUTS)}
    assert a["teams"] == ["TB"] and a["players"] == [] and a["team"] is None
    o = outcomes(client)
    assert (o["status"], o["stat"], o["tiles"]["curves"]) == ("ok", "rec_yds-wrte", 1)
    assert fetch(client, f"{CURVE}?player=00-0000001&target=rec_yds-wrte")["grid_lo"] == -15


def test_no_model_and_no_file_are_their_own_empty_states(client, paths) -> None:
    a = avail(client)
    assert (a["status"], a["players"], a["at_risk"]) == ("no_model", [], [])
    assert "promoted" in a["hint"]
    promote(paths)
    a = avail(client)
    assert a["status"] == "none" and f"Nothing for week {WEEK}" in a["hint"]
    assert outcomes(client)["status"] == "none"
    assert fetch(client, f"{CURVE}?player=00-0000001&target=rec_yds-wrte", 404)["error"]


def test_a_production_file_that_is_not_json_is_no_model(client, paths) -> None:
    put_availability(paths, base_players())
    S.production_file(paths, "availability").write_text("{not json", encoding="utf-8")
    assert avail(client)["status"] == "no_model"
    S.production_file(paths, "availability").write_text("[]", encoding="utf-8")
    assert avail(client)["status"] == "no_model"


def test_an_unknown_team_on_a_week_without_a_file_is_still_the_empty_state(client, paths) -> None:
    promote(paths)
    assert fetch(client, AV + "?team=KC")["status"] == "none"
    assert fetch(client, OUT + "?target=rec_yds-wrte")["status"] == "none"


# ---- planted secrets: nothing a file says reaches the browser unmasked ---------------------

KINDS = ("api_key", "token", "password", "secret")


def planted(i: int, root: Path) -> str:
    """The i-th planted value: a credential shape (four kinds in turn) or the data root."""
    n = i % 6
    if n < 4:
        return f"{KINDS[n]}=SENTINEL{i:02d}Z0123456789abcdef0123456789 tail"
    path = str(root / "secret" / f"planted{i:02d}.parquet")  # (no sentinel: a relative path stays)
    return path if n == 4 else path.replace("\\", "/")


def string_columns(schema: dict[str, pl.DataType]) -> list[str]:
    return [c for c, t in schema.items() if t == pl.String()]


def all_urls(player: str = "00-0000009") -> list[str]:
    return [
        AV,
        AV + "?team=TB",
        OUT,
        OUT + "?target=rec_yds-wrte",
        f"{CURVE}?player={player}&target=rec_yds-wrte",
    ]


def sweep(client, paths: DataPaths, root: Path, player: str = "00-0000009") -> str:
    """Every answer joined; none may hold a sentinel or the data root (the leak scan's own
    `scrub(body) == body` is `test_every_answer_survives_the_leak_scan`)."""
    text = ""
    for url in all_urls(player):
        r = client.get(url)
        assert r.status_code in (200, 404), (url, r.status_code)
        assert "SENTINEL" not in r.text, f"{url} carries planted text"
        for form in (str(root), str(root).replace("\\", "/"), json.dumps(str(root))[1:-1]):
            assert form not in r.text, f"{url} carries the data root"
        text += r.text
    return text


SCAN_URLS = [
    pytest.param(AV, id="availability"),
    pytest.param(AV + "?team=TB", id="availability-team"),
    pytest.param(OUT, id="outcomes"),
    pytest.param(OUT + "?target=rec_yds-wrte", id="outcomes-target"),
    pytest.param(f"{CURVE}?player=00-0000001&target=rec_yds-wrte", id="curve"),
]


@pytest.mark.parametrize("url", SCAN_URLS)
def test_every_answer_survives_the_leak_scan(client, paths, url) -> None:
    """What tests/app/test_app_security.py does to every GET, on a populated week (the field
    of the Outcomes answers is `stat`: a name with `key` in it would be masked by `scrub`)."""
    put_availability(paths, base_players())
    put_curves(paths, [crow("00-0000001", "A. Receiver")])
    r = client.get(url)
    assert r.status_code == 200
    assert scrub(r.text, limit=10**9) == r.text, "text that looks like a secret"


def test_the_empty_states_survive_the_leak_scan_too(client, paths) -> None:
    for url in (AV, OUT, OUT + "?target=rec_yds-wrte"):
        r = client.get(url)  # no model yet
        assert r.status_code == 200 and scrub(r.text, limit=10**9) == r.text, url
    promote(paths)  # a model, no file
    for url in (AV, OUT):
        r = client.get(url)
        assert r.status_code == 200 and scrub(r.text, limit=10**9) == r.text, url


# the columns a response echoes: a planted value there must come back masked, not dropped
ECHOED_AVAIL = (
    "player_id", "player", "team", "opponent", "game_id", "position", "pgroup", "side", "view",
    "report_status", "practice_status", "prev_status", "roster_status",
)  # fmt: skip
AVAIL_COLUMNS = string_columns(S.AVAIL_SCHEMA)


@pytest.mark.parametrize("column", AVAIL_COLUMNS)
def test_planted_text_in_an_availability_column_is_masked(tmp_path, paths, client, column) -> None:
    i = AVAIL_COLUMNS.index(column)
    row = prow("00-0000009", "X. Planted", report_status="Out", at_risk=True, p_play=0.05)
    put_availability(paths, [*base_players(), {**row, column: planted(i, tmp_path)}])
    put_curves(paths, [crow("00-0000009", "X. Planted")])
    text = sweep(client, paths, tmp_path)
    if column in ECHOED_AVAIL and i % 6 < 4:  # a credential shape: it comes back as a mask
        assert "***" in text, f"{column}: the value was dropped, not masked"
    a = avail(client)
    assert a["status"] == "ok" and a["tiles"]["players"] >= 2  # and the week still reads


INHERIT_COLUMNS = string_columns(S.INHERIT_SCHEMA)
ECHOED_INHERIT = ("player_id", "player", "position")


@pytest.mark.parametrize("column", INHERIT_COLUMNS)
def test_planted_text_in_an_inherit_column_is_masked(tmp_path, paths, client, column) -> None:
    i = INHERIT_COLUMNS.index(column)
    out = prow("00-0000009", "X. Planted", report_status="Out", at_risk=True, p_play=0.05)
    put_availability(
        paths,
        [*base_players(), out],
        inherit=[irow("00-0000009", "00-0000002", **{column: planted(i, tmp_path)})],
    )
    put_curves(paths, [crow("00-0000009", "X. Planted")])
    text = sweep(client, paths, tmp_path)
    if column in ECHOED_INHERIT and i % 6 < 4:
        assert "***" in avail(client)["at_risk"][0]["inherit"][0][column], column
    assert text  # (the answers came back)


CURVE_COLUMNS = string_columns(S.CURVE_SCHEMA)
ECHOED_CURVE = (
    "player",
    "team",
    "opponent",
    "game_id",
    "position",
    "group",
    "target_label",
    "kind",
)


@pytest.mark.parametrize("column", CURVE_COLUMNS)
def test_planted_text_in_a_curves_column_is_masked(tmp_path, paths, client, column) -> None:
    i = CURVE_COLUMNS.index(column)
    put_availability(paths, base_players())
    row = crow("00-0000009", "X. Planted")
    put_curves(paths, [crow("00-0000001", "A. Receiver"), {**row, column: planted(i, tmp_path)}])
    text = sweep(client, paths, tmp_path)
    assert text
    if column in ECHOED_CURVE and i % 6 < 4:
        rows = outcomes(client)["rows"]
        assert any("***" in str(r[column]) for r in rows), f"{column}: dropped, not masked"


AVAIL_JSON_FIELDS = (
    "version", "trained_through", "tuesday.made_at", "saturday.made_at", "shipped.tuesday",
    "shipped.saturday", "games_after_kickoff", "views.tuesday.rows",
)  # fmt: skip


def plant_in_availability_json(meta: dict[str, Any], field: str, text: str) -> dict[str, Any]:
    if field in ("version", "trained_through"):
        meta[field] = text
    elif field.endswith(".made_at"):
        meta["views"][field.split(".")[0]]["made_at"] = text
    elif field.startswith("shipped."):
        meta["shipped"][field.split(".")[1]].append(text)
    elif field == "games_after_kickoff":
        meta[field] = [text, "2026_05_TB_ATL"]
    else:
        meta["views"]["tuesday"]["rows"] = text
    return meta


@pytest.mark.parametrize("field", AVAIL_JSON_FIELDS)
def test_planted_text_in_availability_json_is_masked(tmp_path, paths, client, field) -> None:
    i = AVAIL_JSON_FIELDS.index(field)
    meta = plant_in_availability_json(avail_meta(), field, planted(i, tmp_path))
    put_availability(paths, base_players(), meta=meta)
    put_curves(paths, [crow("00-0000001", "A. Receiver")])
    sweep(client, paths, tmp_path, "00-0000001")
    assert avail(client)["status"] == "ok"


CURVES_JSON_FIELDS = ("version", "made_at", "shipped", "source_version", "games_after_kickoff")


@pytest.mark.parametrize("field", CURVES_JSON_FIELDS)
def test_planted_text_in_curves_json_is_masked(tmp_path, paths, client, field) -> None:
    i = CURVES_JSON_FIELDS.index(field)
    meta = curves_meta()
    text = planted(i, tmp_path)
    meta[field] = [*meta[field], text] if field in ("shipped", "games_after_kickoff") else text
    put_availability(paths, base_players())
    put_curves(paths, [crow("00-0000001", "A. Receiver")], meta=meta)
    sweep(client, paths, tmp_path, "00-0000001")
    assert outcomes(client)["status"] == "ok"


def test_every_kind_of_planted_text_at_once_in_every_file(tmp_path, paths, client) -> None:
    """Credential shapes, the data root and a long bare token in the same player name, the same
    curve name and the meta fields: nothing but masks and relative paths comes back."""
    big = " ".join(planted(i, tmp_path) for i in range(6)) + " " + "A" * 60 + "0123456789abcdef"
    row = prow("00-0000009", "X. Planted", report_status="Out", at_risk=True, p_play=0.05)
    meta = plant_in_availability_json(avail_meta(), "version", big)
    meta = plant_in_availability_json(meta, "games_after_kickoff", big)
    put_availability(
        paths,
        [*base_players(), {**row, "player": big, "position": big}],
        inherit=[irow("00-0000009", "00-0000002", name=big)],
        meta=meta,
    )
    put_curves(
        paths,
        [crow("00-0000009", big)],
        meta=curves_meta(
            version=big, made_at=big, source_version=big, shipped=["rec_yds-wrte", big]
        ),
    )
    text = sweep(client, paths, tmp_path)
    assert "***" in text


def test_every_string_of_an_answer_is_capped(tmp_path, paths, client) -> None:
    long_name = "Z" * 5000
    row = prow("00-0000009", long_name, report_status="Out", at_risk=True, p_play=0.05)
    put_availability(paths, [*base_players(), row])
    put_curves(paths, [crow("00-0000009", long_name)])
    a = avail(client)
    assert all(len(r["player"]) <= R.STR_LIMIT for r in a["at_risk"])
    assert all(len(r["player"]) <= R.STR_LIMIT for r in outcomes(client)["rows"])


def test_a_secret_shaped_player_name_is_masked_but_an_ordinary_one_is_not(paths, client) -> None:
    put_availability(paths, [*base_players(), prow("00-0000009", "api_key=hunter2hunter2 x")])
    names = {r["player"] for r in avail(client, "TB")["players"]}
    assert "A. Receiver" in names and not any("hunter2" in n for n in names)


# ---- hostile and damaged files: an empty state or a fixed-text 404, never a 500 -----------------


def rewrite(path: Path, **cols: pl.Expr | pl.Series) -> None:
    pl.read_parquet(path).with_columns(**cols).write_parquet(path)


def avail_file(paths: DataPaths) -> Path:
    return week_dir(paths) / "availability.parquet"


def curves_file(paths: DataPaths) -> Path:
    return week_dir(paths) / "curves.parquet"


def sound_week(paths: DataPaths) -> None:
    """A week whose files are fine: the files the tests below damage one at a time."""
    out = prow("00-0000009", "X. Planted", report_status="Out", at_risk=True, p_play=0.05)
    put_availability(
        paths, [*base_players(), out], inherit=[irow("00-0000009", "00-0000002", view="tuesday")]
    )
    put_curves(paths, [crow("00-0000001", "A. Receiver"), crow("00-0000009", "X. Planted")])
    for fam, rows in (("availability", [4, 4]), ("curves", [4])):
        pl.DataFrame({"week": rows}, schema={"week": pl.Int32}).write_parquet(
            S.season_dir(paths, SEASON) / f"{fam}_scoreboard.parquet"
        )


def unreadable(body: dict[str, Any]) -> None:
    assert body["status"] == "none" and COULDNT in body["hint"], body["hint"]
    assert body["meta"] is None and body["tiles"] is None
    assert body.get("players", []) == [] and body.get("rows", []) == []
    assert body.get("at_risk", []) == [] and body.get("targets", []) == []


def no_such_curve(client, player: str = "00-0000001", target: str = "rec_yds-wrte") -> None:
    r = client.get(f"{CURVE}?player={player}&target={target}")
    assert r.status_code == 404
    assert r.json() == {
        "error": {"code": "not_found", "message": "No curve for that player and stat this week."}
    }


def break_p_play(paths: DataPaths) -> None:
    rewrite(avail_file(paths), p_play=pl.lit("abc"))  # a column that can't be cast


def break_rank(paths: DataPaths) -> None:
    rewrite(week_dir(paths) / "inherit.parquet", rank=pl.lit("one"))


def break_avail_empty(paths: DataPaths) -> None:
    frame([], S.AVAIL_SCHEMA).write_parquet(avail_file(paths))


def break_avail_not_parquet(paths: DataPaths) -> None:
    avail_file(paths).write_bytes(b"not a parquet file")


def break_avail_zero_bytes(paths: DataPaths) -> None:
    avail_file(paths).write_bytes(b"")


@pytest.mark.parametrize(
    "damage",
    [break_p_play, break_rank, break_avail_empty, break_avail_not_parquet, break_avail_zero_bytes],
)
def test_a_damaged_availability_file_is_the_empty_state(paths, client, damage) -> None:
    sound_week(paths)
    assert avail(client)["status"] == "ok"
    damage(paths)
    unreadable(avail(client))
    unreadable(avail(client, "TB"))  # with a team, too: no 404 for what can't be read
    assert outcomes(client)["status"] == "ok"  # the other tab is untouched


def test_an_availability_file_of_another_shape_never_raises(paths, client) -> None:
    sound_week(paths)
    pl.DataFrame({"foo": [1, 2]}).write_parquet(avail_file(paths))  # none of our columns
    r = client.get(AV)
    assert r.status_code == 200 and r.json()["status"] in ("ok", "none")
    assert client.get(AV + "?team=TB").status_code in (200, 404)


@pytest.mark.parametrize("how", ["garbage", "empty", "zero"])
def test_a_damaged_inherit_file_only_costs_the_gains(paths, client, how) -> None:
    sound_week(paths)
    f = week_dir(paths) / "inherit.parquet"
    if how == "garbage":
        f.write_bytes(b"junk")
    elif how == "zero":
        f.write_bytes(b"")
    else:
        frame([], S.INHERIT_SCHEMA).write_parquet(f)
    a = avail(client)
    assert a["status"] == "ok" and a["at_risk"]
    assert all(r["inherit"] == [] for r in a["at_risk"])


@pytest.mark.parametrize("text", ["{not json", "[]", "5", '"x"', ""])
def test_availability_json_that_is_not_a_mapping_ships_nothing_and_reads(
    paths, client, text
) -> None:
    put_availability(paths, base_players(), meta=text)
    a = avail(client, "TB")
    assert a["status"] == "ok"
    assert a["meta"]["shipped"] == {"tuesday": [], "saturday": []}
    assert a["meta"]["views"] == {} and a["meta"]["games_after_kickoff"] == []
    p = by_id(a["players"])["00-0000001"]
    assert p["p_play"] is None and p["snap_p50"] is None  # nothing shipped: the model is off...
    assert p["base_p_play"] == 0.94 and p["base_snap"] == 0.7  # ...the baseline is not


def test_availability_json_missing_entirely_reads_like_an_empty_one(paths, client) -> None:
    put_availability(paths, base_players())
    (week_dir(paths) / "availability.json").unlink()
    a = avail(client)
    assert a["status"] == "ok" and a["meta"]["shipped"] == {"tuesday": [], "saturday": []}


@pytest.mark.parametrize(
    "views",
    [
        {"tuesday": ["x"], "saturday": 5},
        {"tuesday": None, "saturday": "2026-10-10T15:05:00+00:00"},
        ["tuesday", "saturday"],
        "tuesday",
        7,
        None,
    ],
)
def test_views_that_are_not_mappings_are_left_out(paths, client, views) -> None:
    put_availability(paths, base_players(), meta=avail_meta(views=views))
    a = avail(client)
    assert a["status"] == "ok" and a["meta"]["views"] == {}
    assert a["tiles"]["view"] == "tuesday"


def test_a_view_that_is_a_mapping_keeps_its_time_and_rows(paths, client) -> None:
    put_availability(paths, base_players(), meta=avail_meta(views={"saturday": {"rows": 3}}))
    a = avail(client)
    assert a["meta"]["views"] == {"saturday": {"made_at": None, "rows": 3}}
    assert a["tiles"]["view"] == "saturday"


@pytest.mark.parametrize("gak", [7, "2026_05_TB_ATL", {"a": 1}, None, True, [1, 2, None]])
def test_games_after_kickoff_that_is_not_a_list_of_text_is_empty(paths, client, gak) -> None:
    put_availability(paths, base_players(), meta=avail_meta(games_after_kickoff=gak))
    put_curves(
        paths, [crow("00-0000001", "A. Receiver")], meta=curves_meta(games_after_kickoff=gak)
    )
    assert avail(client)["meta"]["games_after_kickoff"] == []
    assert outcomes(client)["meta"]["games_after_kickoff"] == []


def test_games_after_kickoff_keeps_its_strings_only(paths, client) -> None:
    put_availability(
        paths, base_players(), meta=avail_meta(games_after_kickoff=["2026_05_TB_ATL", 5, None])
    )
    assert avail(client)["meta"]["games_after_kickoff"] == ["2026_05_TB_ATL"]


@pytest.mark.parametrize(
    "shipped",
    [
        "play snap tgt_share carry_share",  # a string
        "play",
        ["play", "snap", "tgt_share", "carry_share"],  # a list where a dict is expected
        {"tuesday": "play snap", "saturday": "snap"},  # a dict of strings
        {"tuesday": {"play": True}, "saturday": {"snap": 1}},  # a dict of dicts
        {"tuesday": [["play"]], "saturday": [None, 5]},
        {"tuesday": ["play snap"], "saturday": ["PLAY", "p_play"]},  # near misses
        {"wednesday": ["play"]},  # not a view
        None,
        5,
    ],
)
def test_an_availability_shipped_field_of_the_wrong_shape_ships_nothing(
    paths, client, shipped
) -> None:
    """Only a list of exact names ships an output: text never matches by substring."""
    put_availability(paths, base_players(), meta=avail_meta(shipped=shipped))
    a = avail(client, "TB")
    assert a["status"] == "ok"
    for p in a["players"]:
        for col in MODEL_COLS:
            assert p[col] is None, (p["player_id"], col)
        assert p["base_p_play"] is not None and p["base_snap"] is not None  # the baselines stay


@pytest.mark.parametrize(
    "meta", [{"version": 5}, {"version": ["x"]}, {"trained_through": {"a": 1}}]
)
def test_meta_fields_of_another_type_are_text_or_null(paths, client, meta) -> None:
    put_availability(paths, base_players(), meta=avail_meta(**meta))
    a = avail(client)
    assert a["status"] == "ok"
    for k in ("version", "trained_through"):
        assert a["meta"][k] is None or isinstance(a["meta"][k], str)


# ---- hostile curves files ---------------------------------------------------------------------


def break_unknown_targets(paths: DataPaths) -> None:
    rewrite(curves_file(paths), target_key=pl.lit("zzz-qq"))


def break_curves_empty(paths: DataPaths) -> None:
    frame([], S.CURVE_SCHEMA).write_parquet(curves_file(paths))


def break_curves_not_parquet(paths: DataPaths) -> None:
    curves_file(paths).write_bytes(b"not a parquet file")


def break_curves_zero_bytes(paths: DataPaths) -> None:
    curves_file(paths).write_bytes(b"")


def break_curves_p50_text(paths: DataPaths) -> None:
    rewrite(curves_file(paths), p50=pl.lit("abc"))


def break_curves_other_shape(paths: DataPaths) -> None:
    pl.DataFrame({"foo": [1]}).write_parquet(curves_file(paths))


@pytest.mark.parametrize(
    "damage",
    [
        break_unknown_targets,
        break_curves_empty,
        break_curves_not_parquet,
        break_curves_zero_bytes,
        break_curves_p50_text,
        break_curves_other_shape,
    ],
)
def test_a_damaged_curves_file_is_the_empty_state(paths, client, damage) -> None:
    sound_week(paths)
    assert outcomes(client)["status"] == "ok"
    damage(paths)
    unreadable(outcomes(client))
    unreadable(outcomes(client, "rec_yds-wrte"))
    no_such_curve(client)
    assert avail(client)["status"] == "ok"  # the other tab is untouched


def test_only_the_known_targets_of_a_mixed_file_show(paths, client) -> None:
    sound_week(paths)
    rows = [
        crow("00-0000001", "A. Receiver"),
        crow("00-0000008", "Q. Unknown", target_key="zzz-qq"),
    ]
    put_curves(paths, rows, meta=curves_meta(shipped=["rec_yds-wrte", "zzz-qq"]))
    o = outcomes(client)
    assert [t["stat"] for t in o["targets"]] == ["rec_yds-wrte"]
    assert o["tiles"]["curves"] == 1
    no_such_curve(client, "00-0000008", "zzz-qq")


@pytest.mark.parametrize(
    "marks",
    [
        [None, {"mark": 1.5, "p_over": None, "bell_p_over": 0.2}],
        [{"mark": None, "p_over": 0.4, "bell_p_over": 0.5}, None],
        None,
        [],
        [None],
    ],
)
def test_marks_with_a_null_element_or_null_fields_read(paths, client, marks) -> None:
    sound_week(paths)
    df = pl.read_parquet(curves_file(paths))
    df = df.with_columns(pl.Series("marks", [marks] * df.height, dtype=pl.List(S.MARK)))
    df.write_parquet(curves_file(paths))
    o = outcomes(client)
    assert o["status"] == "ok"
    for row in o["rows"]:
        assert all(isinstance(m, dict) for m in row["marks"])
        assert row["gap"] is None or {"mark", "p_over", "bell_p_over", "diff"} <= set(row["gap"])
    assert client.get(f"{CURVE}?player=00-0000001&target=rec_yds-wrte").status_code == 200


def test_the_gap_uses_only_marks_with_both_chances(paths, client) -> None:
    sound_week(paths)
    marks = [
        {"mark": 10.5, "p_over": None, "bell_p_over": 0.9},  # no curve chance: no gap
        {"mark": 20.5, "p_over": 0.5, "bell_p_over": 0.4},  # 0.1
        {"mark": 30.5, "p_over": 0.2, "bell_p_over": 0.35},  # 0.15: the biggest
    ]
    df = pl.read_parquet(curves_file(paths))
    df = df.with_columns(pl.Series("marks", [marks] * df.height, dtype=pl.List(S.MARK)))
    df.write_parquet(curves_file(paths))
    row = outcomes(client)["rows"][0]
    assert row["gap"] == {"mark": 30.5, "p_over": 0.2, "bell_p_over": 0.35, "diff": -0.15}


def set_cdfs(paths: DataPaths, cdf: list[float | None] | None) -> None:
    df = pl.read_parquet(curves_file(paths))
    df = df.with_columns(pl.Series("cdf", [cdf] * df.height, dtype=pl.List(pl.Float64)))
    df.write_parquet(curves_file(paths))


@pytest.mark.parametrize(
    "cdf",
    [[0.1, None, 1.0], [None], [], None],
    ids=["null inside", "only a null", "empty", "null list"],
)
def test_a_curve_with_a_null_or_missing_cdf_is_a_fixed_text_404(paths, client, cdf) -> None:
    sound_week(paths)
    set_cdfs(paths, cdf)
    no_such_curve(client)
    assert outcomes(client)["status"] == "ok"  # the summaries don't read the curve itself


def test_a_curve_without_its_grid_is_a_404_too(paths, client) -> None:
    sound_week(paths)
    rewrite(curves_file(paths), grid_lo=pl.lit(None, dtype=pl.Int32))
    no_such_curve(client)


def test_a_sound_curve_has_its_grid_and_a_closed_cdf(paths, client) -> None:
    sound_week(paths)
    c = fetch(client, f"{CURVE}?player=00-0000001&target=rec_yds-wrte")
    assert c["grid_lo"] == -15 and len(c["cdf"]) == 135 and c["cdf"][-1] == 1.0
    assert c["player"] == "A. Receiver" and c["stat"] == "rec_yds-wrte"


@pytest.mark.parametrize("week_col", ["text", "null", "float"])
def test_a_scoreboard_with_an_odd_week_column_is_no_grades(paths, client, week_col) -> None:
    sound_week(paths)
    for fam in S.FAMILIES:
        f = S.season_dir(paths, SEASON) / f"{fam}_scoreboard.parquet"
        df = pl.read_parquet(f)
        if week_col == "text":
            df = df.with_columns(pl.col("week").cast(pl.String))
        elif week_col == "null":
            df = df.with_columns(pl.lit(None, dtype=pl.Int32).alias("week"))
        else:
            df = df.with_columns(pl.col("week").cast(pl.Float64) + 0.5)
        df.write_parquet(f)
    for body in (avail(client), outcomes(client)):
        assert body["status"] == "ok"
        assert isinstance(body["scoreboard"]["week"], list)
        assert isinstance(body["scoreboard"]["season"], list)


@pytest.mark.parametrize("how", ["garbage", "zero", "no_week", "empty"])
def test_a_scoreboard_that_is_not_one_is_no_grades(paths, client, how) -> None:
    sound_week(paths)
    for fam in S.FAMILIES:
        f = S.season_dir(paths, SEASON) / f"{fam}_scoreboard.parquet"
        if how == "garbage":
            f.write_bytes(b"junk")
        elif how == "zero":
            f.write_bytes(b"")
        elif how == "no_week":
            pl.DataFrame({"n": [1]}).write_parquet(f)
        else:
            pl.DataFrame({"week": []}, schema={"week": pl.Int32}).write_parquet(f)
    for body in (avail(client), outcomes(client)):
        assert body["status"] == "ok" and body["scoreboard"] == {"week": [], "season": []}


def test_a_sound_scoreboard_is_read_through_the_week(paths, client) -> None:
    sound_week(paths)
    pl.DataFrame(
        {
            "week": [3, 4, 5, 6],
            "target_key": ["rec_yds-wrte"] * 4,
            "n": [100, 100, 100, 100],
            "crps_model": [10.0, 12.0, 14.0, 99.0],
        },
        schema_overrides={"week": pl.Int32, "n": pl.Int32},
    ).write_parquet(S.season_dir(paths, SEASON) / "curves_scoreboard.parquet")
    sb = outcomes(client)["scoreboard"]
    assert [r["crps_model"] for r in sb["week"]] == [14.0]  # this week's row
    assert sb["week"][0]["stat"] == "rec_yds-wrte"  # named `stat`, not by its parquet column
    assert all("target_key" not in r for r in (*sb["week"], *sb["season"]))
    assert sb["season"][0]["crps_model"] == 12.0 and sb["season"][0]["n"] == 300  # weeks 3-5
    assert sb["season"][0]["weeks"] == 3  # week 6 hasn't happened as far as week 5 is concerned


@pytest.mark.parametrize("text", ["{not json", "[]", "5", '"x"', ""])
def test_curves_json_that_is_not_a_mapping_shows_every_known_target(paths, client, text) -> None:
    put_availability(paths, base_players())
    rows = [
        crow("00-0000001", "A. Receiver"),
        crow("00-0000002", "B. Back", target_key="carries-rb"),
    ]
    put_curves(paths, rows, meta=text)
    o = outcomes(client)
    assert o["status"] == "ok" and {t["stat"] for t in o["targets"]} == {
        "rec_yds-wrte",
        "carries-rb",
    }
    assert o["meta"]["shipped"] == []  # nothing said what shipped


@pytest.mark.parametrize(
    "shipped",
    [
        "rec_yds-wrte",
        {"rec_yds-wrte": True},
        [],
        [None, 5],
        ["rec_yds-wrte "],
        ["REC_YDS-WRTE"],
        None,
        5,
    ],
)
def test_a_curves_shipped_field_of_the_wrong_shape_shows_no_curve(paths, client, shipped) -> None:
    """With a `shipped` field present, only the exact names in its list ship: a string, a
    mapping or near misses ship nothing (and a week with nothing is the empty state)."""
    put_availability(paths, base_players())
    put_curves(paths, [crow("00-0000001", "A. Receiver")], meta=curves_meta(shipped=shipped))
    unreadable(outcomes(client))
    no_such_curve(client)


def test_curves_json_with_other_json_types_in_the_meta_never_raises(paths, client) -> None:
    put_availability(paths, base_players())
    meta = curves_meta(made_at=12345, version={"a": 1}, source_version=[1])
    put_curves(paths, [crow("00-0000001", "A. Receiver")], meta=meta)
    o = outcomes(client)
    assert o["status"] == "ok"
    for k in ("version", "source_version"):
        assert o["meta"][k] is None or isinstance(o["meta"][k], str)


def test_made_at_and_rows_of_another_json_type_are_text_or_number_or_null(paths, client) -> None:
    views = {
        "tuesday": {"made_at": {"when": "now"}, "rows": ["x"]},
        "saturday": {"made_at": 12345, "rows": 3},
    }
    put_availability(paths, base_players(), meta=avail_meta(views=views))
    put_curves(paths, [crow("00-0000001", "A. Receiver")], meta=curves_meta(made_at=["2026"]))
    assert avail(client)["meta"]["views"] == {
        "tuesday": {"made_at": None, "rows": None},
        "saturday": {"made_at": None, "rows": 3},
    }
    assert outcomes(client)["meta"]["made_at"] is None


@pytest.mark.parametrize("rows", [True, False, 3.0, "3", None, [3], {"n": 3}])
def test_a_view_rows_count_must_be_a_whole_number(paths, client, rows) -> None:
    views = {"tuesday": {"made_at": "2026-10-06T14:40:00+00:00", "rows": rows}}
    put_availability(paths, base_players(), meta=avail_meta(views=views))
    assert avail(client)["meta"]["views"]["tuesday"] == {
        "made_at": "2026-10-06T14:40:00+00:00",
        "rows": None,
    }


def test_a_sound_made_at_and_rows_come_back_as_written(paths, client) -> None:
    put_availability(paths, base_players())
    put_curves(paths, [crow("00-0000001", "A. Receiver")])
    views = avail(client)["meta"]["views"]
    assert views["tuesday"] == {"made_at": "2026-10-06T14:40:00+00:00", "rows": 3}
    assert outcomes(client)["meta"]["made_at"] == "2026-10-06T14:45:00+00:00"


# ---- the ship gate ---------------------------------------------------------------------------


def gate_world(paths: DataPaths, shipped: Any) -> None:
    """P1 has both views (Saturday: Questionable), P2 only a Tuesday row. Every model value
    differs from its baseline, so a nulled column can't be mistaken for an equal one."""
    rows = [
        prow("00-0000001", "A. Receiver", view="tuesday", p_play=0.8, snap_p50=0.61,
             snap_p10=0.4, snap_p90=0.8, exp_snap=0.5, tgt_share=0.17, carry_share=0.07,
             base_p_play=0.7, base_snap=0.8, base_tgt_share=0.22, base_carry_share=0.05),
        prow("00-0000001", "A. Receiver", view="saturday", p_play=0.55, snap_p50=0.62,
             snap_p10=0.41, snap_p90=0.81, exp_snap=0.34, tgt_share=0.16, carry_share=0.06,
             base_p_play=0.7, base_snap=0.8, base_tgt_share=0.22, base_carry_share=0.05,
             report_status="Questionable", at_risk=True),
        prow("00-0000002", "B. Back", view="tuesday", p_play=0.9, snap_p50=0.75, snap_p10=0.6,
             snap_p90=0.9, exp_snap=0.7, tgt_share=0.12, carry_share=0.6, base_p_play=0.88,
             base_snap=0.6, base_tgt_share=0.08, base_carry_share=0.45),
    ]  # fmt: skip
    put_availability(paths, rows, meta=avail_meta(shipped=shipped))


MODEL_COLS = ("p_play", "snap_p10", "snap_p50", "snap_p90", "exp_snap", "tgt_share", "carry_share")


def test_an_output_missing_from_shipped_nulls_its_columns_and_keeps_the_baseline(
    paths, client
) -> None:
    gate_world(paths, {"tuesday": ["play"], "saturday": ["snap", "tgt_share"]})
    players = by_id(avail(client, "TB")["players"])
    p1, p2 = players["00-0000001"], players["00-0000002"]
    # P1 is shown as his Saturday row: the chance of playing didn't ship there...
    assert p1["view"] == "saturday" and p1["p_play"] is None
    assert (p1["snap_p10"], p1["snap_p50"], p1["snap_p90"], p1["exp_snap"]) == (
        0.41,
        0.62,
        0.81,
        0.34,
    )
    assert p1["tgt_share"] == 0.16 and p1["carry_share"] is None
    assert p1["p_play_tuesday"] == 0.8  # ...but Tuesday's did, and rides along
    # P2 only has Tuesday: the chance shipped, nothing else did
    assert p2["view"] == "tuesday" and p2["p_play"] == 0.9
    assert all(p2[c] is None for c in MODEL_COLS if c != "p_play")
    # the baselines are never gated
    for p, base in ((p1, (0.7, 0.8, 0.22, 0.05)), (p2, (0.88, 0.6, 0.08, 0.45))):
        assert (
            p["base_p_play"],
            p["base_snap"],
            p["base_tgt_share"],
            p["base_carry_share"],
        ) == base


@pytest.mark.parametrize("output", OUTPUTS)
def test_each_output_ships_or_not_on_its_own(paths, client, output) -> None:
    gate_world(paths, {"tuesday": [output], "saturday": [output]})
    p = by_id(avail(client, "TB")["players"])["00-0000002"]
    owned = {
        "play": ("p_play",),
        "snap": ("snap_p10", "snap_p50", "snap_p90", "exp_snap"),
        "tgt_share": ("tgt_share",),
        "carry_share": ("carry_share",),
    }[output]
    for col in MODEL_COLS:
        assert (p[col] is not None) == (col in owned), (output, col)


def test_nothing_shipped_is_still_a_week_with_baselines(paths, client) -> None:
    gate_world(paths, {"tuesday": [], "saturday": []})
    a = avail(client, "TB")
    assert a["status"] == "ok" and len(a["players"]) == 2
    assert all(p[c] is None for p in a["players"] for c in MODEL_COLS)
    assert a["movers"] == {"snap_up": [], "snap_down": [], "targets_up": [], "carries_up": []}
    assert [r["player_id"] for r in a["at_risk"]] == ["00-0000001"]  # he has a game status


def test_a_shipped_output_can_move_a_player_up_the_movers(paths, client) -> None:
    rows = [prow("00-0000002", "B. Back", snap_p50=0.9, base_snap=0.6, tgt_share=0.2,
                 base_tgt_share=0.1, carry_share=0.5, base_carry_share=0.3)]  # fmt: skip
    put_availability(paths, rows, meta=avail_meta(shipped={"tuesday": ["play", "snap"]}))
    m = avail(client)["movers"]
    assert [r["player_id"] for r in m["snap_up"]] == ["00-0000002"] and m["snap_up"][0][
        "change"
    ] == 0.3
    assert m["targets_up"] == [] and m["carries_up"] == []  # those two didn't ship
    put_availability(paths, rows, meta=avail_meta(shipped={"tuesday": list(OUTPUTS)}))
    m = avail(client)["movers"]
    assert len(m["targets_up"]) == 1 and len(m["carries_up"]) == 1


def test_the_saturday_row_is_the_one_shown_and_tuesday_rides_along(paths, client) -> None:
    gate_world(paths, {"tuesday": list(OUTPUTS), "saturday": list(OUTPUTS)})
    a = avail(client)
    assert a["tiles"]["view"] == "saturday" and a["tiles"]["players"] == 2
    p = by_id(avail(client, "TB")["players"])["00-0000001"]
    assert (p["view"], p["p_play"], p["p_play_tuesday"]) == ("saturday", 0.55, 0.8)
    assert by_id(avail(client, "TB")["players"])["00-0000002"]["p_play_tuesday"] == 0.9


# ---- inherit: what the share model shipped, who is unavailable, how big a gain -----------------


def inherit_world(
    paths: DataPaths,
    *,
    inherit: list[dict[str, Any]] | None = None,
    mate: dict[str, Any] | None = None,
    shipped: Any = None,
    out_view: str = "tuesday",
    mate_rows: list[dict[str, Any]] | None = None,
) -> None:
    """P-out is Out (so he is in the might-sit list); his teammate `00-0000002` is a regular
    (shaped by `mate`) and the gain rows are `inherit` (default: one, 5 points of targets)."""
    out = prow("00-0000001", "A. Receiver", view=out_view, report_status="Out", at_risk=True,
               p_play=0.02)  # fmt: skip
    if mate_rows is None:
        mate_rows = [prow("00-0000002", "B. Back", view=out_view, **(mate or {}))]
    if shipped is None:
        shipped = {"tuesday": list(OUTPUTS), "saturday": list(OUTPUTS)}
    put_availability(
        paths,
        [out, *mate_rows],
        inherit=inherit if inherit is not None else [irow("00-0000001", "00-0000002")],
        meta=avail_meta(shipped=shipped),
    )


def gains(client) -> list[dict[str, Any]]:
    risk = {r["player_id"]: r for r in avail(client)["at_risk"]}
    return risk["00-0000001"]["inherit"]


def test_a_teammate_who_gains_is_listed_with_his_shares(paths, client) -> None:
    inherit_world(paths)
    (g,) = gains(client)
    assert g == {
        "kind": "targets", "player_id": "00-0000002", "player": "B. Back", "position": "RB",
        "share_if_in": 0.08, "share_if_out": 0.13, "delta": 0.05,
    }  # fmt: skip


@pytest.mark.parametrize(
    ("teammate", "kept"),
    [
        ({"roster_status": "RES"}, False),  # on reserve: not coming back this week
        ({"report_status": "Out"}, False),
        ({"report_status": "Doubtful"}, False),
        ({"report_status": "Questionable"}, True),
        ({"p_play": 0.1, "base_p_play": 0.1, "at_risk": True}, True),  # a low chance is not "gone"
        ({"roster_status": "ACT"}, True),
    ],
    ids=["reserve", "Out", "Doubtful", "Questionable", "low-chance backup", "active"],
)
def test_a_teammate_who_is_unavailable_never_inherits(paths, client, teammate, kept) -> None:
    inherit_world(paths, mate=teammate)
    assert [g["player_id"] for g in gains(client)] == (["00-0000002"] if kept else [])


def test_a_teammate_out_on_saturday_inherits_nothing_though_tuesday_had_him_active(
    paths, client
) -> None:
    rows = [
        prow("00-0000001", "A. Receiver", view="tuesday", at_risk=True),
        prow("00-0000001", "A. Receiver", view="saturday", report_status="Out", at_risk=True),
        prow("00-0000002", "B. Back", view="tuesday"),
        prow("00-0000002", "B. Back", view="saturday", report_status="Out"),
        prow("00-0000003", "C. Slot", view="tuesday"),
        prow("00-0000003", "C. Slot", view="saturday"),
    ]
    inh = [
        irow("00-0000001", "00-0000002", view="saturday", rank=1),
        irow("00-0000001", "00-0000003", name="C. Slot", view="saturday", rank=2),
    ]
    put_availability(paths, rows, inherit=inh)
    assert [g["player_id"] for g in gains(client)] == ["00-0000003"]  # B is Out on Saturday


@pytest.mark.parametrize("kind", ["targets", "carries", "snaps"])
@pytest.mark.parametrize("output", OUTPUTS)
def test_a_gain_needs_the_share_model_behind_it_to_have_shipped(
    paths, client, kind, output
) -> None:
    owner = {"targets": "tgt_share", "carries": "carry_share", "snaps": "snap"}[kind]
    inherit_world(
        paths,
        inherit=[irow("00-0000001", "00-0000002", kind=kind, delta=0.08)],
        shipped={"tuesday": [output], "saturday": [output]},
    )
    assert (len(gains(client)) == 1) == (output == owner), (kind, output)


@pytest.mark.parametrize("kind", [None, "", "points", "TARGETS"])
def test_a_gain_of_an_unknown_kind_is_dropped(paths, client, kind) -> None:
    inherit_world(paths, inherit=[irow("00-0000001", "00-0000002", kind=kind)])
    assert gains(client) == []


def test_a_gain_is_gated_by_its_own_views_shipped_list(paths, client) -> None:
    mate = [prow("00-0000002", "B. Back", view="saturday")]
    inh = [irow("00-0000001", "00-0000002", view="saturday")]
    inherit_world(  # targets shipped on Tuesday only; the page shows his Saturday row
        paths, out_view="saturday", mate_rows=mate, inherit=inh,
        shipped={"tuesday": ["tgt_share"], "saturday": ["play"]},
    )  # fmt: skip
    assert gains(client) == []
    inherit_world(  # the same week with it shipped for Saturday
        paths, out_view="saturday", mate_rows=mate, inherit=inh,
        shipped={"tuesday": [], "saturday": ["tgt_share"]},
    )  # fmt: skip
    assert [g["player_id"] for g in gains(client)] == ["00-0000002"]


@pytest.mark.parametrize(
    ("kind", "delta", "kept"),
    [
        ("targets", 0.0099, False), ("targets", 0.0101, True), ("targets", 0.0, False),
        ("targets", -0.05, False), ("targets", None, False),
        ("carries", 0.0099, False), ("carries", 0.0101, True),
        ("snaps", 0.0299, False), ("snaps", 0.02, False), ("snaps", 0.0301, True),
    ],
)  # fmt: skip
def test_a_gain_under_one_point_of_targets_or_carries_or_three_of_snaps_is_dropped(
    paths, client, kind, delta, kept
) -> None:
    inherit_world(paths, inherit=[irow("00-0000001", "00-0000002", kind=kind, delta=delta)])
    assert (len(gains(client)) == 1) is kept, (kind, delta)


@pytest.mark.parametrize(
    ("rank", "kept"), [(1, True), (3, True), (4, False), (9, False), (None, False)]
)
def test_only_the_top_three_gains_of_a_player_are_kept(paths, client, rank, kept) -> None:
    inherit_world(paths, inherit=[irow("00-0000001", "00-0000002", rank=rank)])
    assert (len(gains(client)) == 1) is kept


def test_gains_come_back_in_rank_order(paths, client) -> None:
    mates = [prow(f"00-000000{n}", f"M. {n}", view="tuesday") for n in (2, 3, 4)]
    inh = [  # written out of order
        irow("00-0000001", "00-0000003", name="M. 3", rank=2),
        irow("00-0000001", "00-0000004", name="M. 4", rank=3),
        irow("00-0000001", "00-0000002", name="M. 2", rank=1),
    ]
    inherit_world(paths, mate_rows=mates, inherit=inh)
    assert [g["player"] for g in gains(client)] == ["M. 2", "M. 3", "M. 4"]


def test_gains_of_a_view_the_page_does_not_show_for_that_player_are_dropped(paths, client) -> None:
    rows = [
        prow("00-0000001", "A. Receiver", view="tuesday", report_status="Out", at_risk=True),
        prow("00-0000001", "A. Receiver", view="saturday", report_status="Out", at_risk=True),
        prow("00-0000002", "B. Back", view="tuesday"),
        prow("00-0000003", "C. Slot", view="tuesday"),
    ]
    inh = [
        irow("00-0000001", "00-0000002", view="tuesday", delta=0.09),  # Tuesday's: not shown
        irow("00-0000001", "00-0000003", name="C. Slot", view="saturday", delta=0.04),
    ]
    put_availability(paths, rows, inherit=inh)
    assert [g["player_id"] for g in gains(client)] == ["00-0000003"]


def test_a_gain_for_a_player_who_is_not_in_the_file_is_dropped(paths, client) -> None:
    inherit_world(paths, inherit=[irow("00-0000777", "00-0000002")])
    assert gains(client) == []


# ---- the might-sit list ------------------------------------------------------------------------


def sit(pid: str, **kw: Any) -> dict[str, Any]:
    return prow(pid, f"P {pid}", at_risk=True, **kw)


def might_sit_world() -> list[dict[str, Any]]:
    return [
        sit("p-out", report_status="Out", p_play=0.02, base_snap=0.8),
        sit("p-dbt", report_status="Doubtful", p_play=0.3, base_snap=0.5),
        sit("p-q1", report_status="Questionable", p_play=0.6, base_snap=0.9),
        sit("p-q2", report_status="Questionable", p_play=0.5, base_snap=0.3),
        prow("p-psq", "P psq", roster_status="DEV", report_status="Questionable", at_risk=True,
             p_play=0.5, base_snap=0.1),  # practice squad, but with a game status
        sit("p-u2", p_play=0.5, base_snap=0.9),
        sit("p-u1", p_play=0.3, base_snap=0.5),
        sit("p-floor", p_play=0.2, base_snap=0.30),  # exactly at the floor: on
        sit("p-low", p_play=0.2, base_snap=0.29),  # under it: off
        prow("p-ps", "P ps", roster_status="DEV", at_risk=True, p_play=0.1, base_snap=0.8),
        sit("p-res", roster_status="RES", p_play=0.1, base_snap=0.6),
        prow("p-ok", "P ok", at_risk=False, p_play=0.9, base_snap=0.9),
    ]  # fmt: skip


def test_the_might_sit_list_by_group_status_and_stake(paths, client) -> None:
    put_availability(paths, might_sit_world())
    a = avail(client)
    assert [r["player_id"] for r in a["at_risk"]] == [
        "p-out", "p-dbt", "p-q1", "p-q2", "p-psq",  # a game status: worst status first, then stake
        "p-u2", "p-u1", "p-floor",  # a regular who may sit for another reason
        "p-res",  # on a reserve list
    ]  # fmt: skip
    groups = {r["player_id"]: r["risk_group"] for r in a["at_risk"]}
    assert groups["p-psq"] == "report" and groups["p-u1"] == "uncertain"
    assert groups["p-res"] == "reserve"
    assert a["tiles"]["at_risk"] == 9
    assert a["tiles"]["by_group"] == {"report": 5, "uncertain": 3, "reserve": 1}
    assert (a["tiles"]["out"], a["tiles"]["doubtful"], a["tiles"]["questionable"]) == (1, 1, 3)
    assert a["tiles"]["role_floor"] == 0.3 and a["tiles"]["players"] == 12


def test_a_practice_squad_player_is_off_unless_he_has_a_game_status(paths, client) -> None:
    put_availability(
        paths,
        [
            prow("p-ps", "P ps", roster_status="DEV", at_risk=True, base_snap=0.9),
            prow("p-psq", "P psq", roster_status="DEV", report_status="Doubtful", base_snap=0.9),
            prow("p-pso", "P pso", roster_status="DEV", report_status="Out", base_snap=0.0),
        ],
    )
    assert at_risk_ids(client) == ["p-pso", "p-psq"]


@pytest.mark.parametrize(
    ("snap", "on"), [(0.29, False), (0.2, False), (0.30, True), (0.31, True), (None, False)]
)
def test_a_regular_under_the_role_floor_is_off(paths, client, snap, on) -> None:
    put_availability(paths, [sit("p-x", p_play=0.4, base_snap=snap)])
    assert (at_risk_ids(client) == ["p-x"]) is on


def test_a_game_status_beats_the_role_floor(paths, client) -> None:
    put_availability(
        paths,
        [
            prow("p-q", "P q", report_status="Questionable", at_risk=False, base_snap=0.01),
            prow("p-n", "P n", report_status=None, at_risk=True, base_snap=0.01),
        ],
    )
    assert at_risk_ids(client) == ["p-q"]


def test_inside_a_group_the_order_is_by_the_playing_time_at_stake(paths, client) -> None:
    """The stake is his usual snap share times the chance he sits: a starter who may sit goes
    ahead of a rotation player who probably will."""
    put_availability(
        paths,
        [
            sit("p-rotation", p_play=0.1, base_snap=0.4),  # stake 0.36
            sit("p-starter", p_play=0.8, base_snap=0.95),  # stake 0.19
            sit("p-big", p_play=0.5, base_snap=0.98),  # stake 0.49
        ],
    )
    a = avail(client)
    assert [r["player_id"] for r in a["at_risk"]] == ["p-big", "p-rotation", "p-starter"]
    assert [r["stake"] for r in a["at_risk"]] == [0.49, 0.36, 0.19]


def test_the_stake_falls_back_on_the_baseline_chance_when_the_model_is_off(paths, client) -> None:
    put_availability(
        paths,
        [
            sit("p-a", p_play=0.5, base_p_play=0.9, base_snap=0.8),
            sit("p-b", p_play=0.5, base_p_play=0.1, base_snap=0.8),
        ],  # fmt: skip
        meta=avail_meta(shipped={"tuesday": []}),  # `play` didn't ship: p_play is null
    )
    stakes = {r["player_id"]: r["stake"] for r in avail(client)["at_risk"]}
    assert stakes == {"p-a": 0.08, "p-b": 0.72}  # 0.8 * (1 - base chance)


def test_the_list_is_capped_but_the_tile_counts_everyone(paths, client) -> None:
    rows = [sit(f"p-{i:03d}", p_play=0.5, base_snap=0.31 + i * 0.004) for i in range(160)]
    put_availability(paths, rows)
    a = avail(client)
    assert len(a["at_risk"]) == R.AT_RISK_ROWS == 150 and a["tiles"]["at_risk"] == 160
    assert a["at_risk"][0]["player_id"] == "p-159"  # the biggest stake first
    assert "p-000" not in {r["player_id"] for r in a["at_risk"]}  # the smallest left off


# ---- ?team= and ?target= --------------------------------------------------------------


def two_team_world(paths: DataPaths) -> None:
    rows = [
        prow("00-0000001", "A. Receiver", team="TB", base_snap=0.8),
        prow("00-0000002", "B. Back", team="TB", base_snap=0.9, side="def"),
        prow("00-0000003", "C. End", team="ATL", base_snap=0.6),
    ]
    put_availability(paths, rows)


def test_a_team_gets_only_its_own_players_and_the_week_stays_whole(paths, client) -> None:
    two_team_world(paths)
    whole = avail(client)
    tb = avail(client, "TB")
    assert whole["players"] == [] and whole["team"] is None
    assert tb["team"] == "TB" and {p["team"] for p in tb["players"]} == {"TB"}
    assert [p["player_id"] for p in tb["players"]] == ["00-0000002", "00-0000001"]  # def, then off
    assert {p["player_id"] for p in avail(client, "ATL")["players"]} == {"00-0000003"}
    for key in ("teams", "tiles", "at_risk", "movers", "meta"):
        assert tb[key] == whole[key]  # one team's players, nothing else changes
    assert tb["teams"] == ["ATL", "TB"]


@pytest.mark.parametrize("team", ["KC", "ZZ", "TBX", "ABC"])
def test_a_team_with_no_rows_is_a_fixed_404_that_does_not_echo_it(paths, client, team) -> None:
    two_team_world(paths)
    r = client.get(AV, params={"team": team})
    assert r.status_code == 404
    assert r.json() == {
        "error": {"code": "not_found", "message": "No players for that team this week."}
    }
    assert team not in r.text


@pytest.mark.parametrize(
    "team", ["tb", "T", "TBTB", "T1", "TB ", " TB", "TB;", "T/B", "%00", "<b>"]
)
def test_a_bad_team_is_a_fixed_text_422(paths, client, team) -> None:
    two_team_world(paths)
    r = client.get(AV, params={"team": team})
    assert r.status_code == 422, team
    assert r.json()["error"]["code"] == "bad_request"
    shown = team.strip()
    assert len(shown) < 2 or shown not in r.text  # the input is never echoed


def curve_world(paths: DataPaths) -> None:
    put_availability(paths, base_players())
    rows = [
        crow("00-0000001", "A. Receiver", target_key="rec_yds-wrte", p50=58.0),
        crow("00-0000003", "C. End", team="ATL", target_key="rec_yds-wrte", p50=71.0),
        crow("00-0000001", "A. Receiver", target_key="receptions-wrte", mean=4.2),
        crow("00-0000003", "C. End", team="ATL", target_key="receptions-wrte", mean=5.1),
        crow("00-0000002", "B. Back", target_key="carries-rb", mean=14.0),
    ]
    put_curves(paths, rows)


def test_a_stat_gets_only_its_own_rows_and_the_week_stays_whole(paths, client) -> None:
    curve_world(paths)
    default = outcomes(client)
    assert default["stat"] == "rec_yds-wrte"  # receiving yards is where the tab opens
    assert {r["stat"] for r in default["rows"]} == {"rec_yds-wrte"}
    assert [r["player_id"] for r in default["rows"]] == ["00-0000003", "00-0000001"]  # by p50
    rec = outcomes(client, "receptions-wrte")
    assert rec["stat"] == "receptions-wrte"
    assert [r["player_id"] for r in rec["rows"]] == ["00-0000003", "00-0000001"]  # by the mean
    for key in ("targets", "tiles", "gaps", "scoreboard"):
        assert rec[key] == default[key]
    assert default["tiles"] == {"curves": 5, "players": 3, "stats": 3}
    assert [t["stat"] for t in default["targets"]] == [
        "rec_yds-wrte", "receptions-wrte", "carries-rb",
    ]  # fmt: skip


def test_the_default_stat_is_the_first_the_week_has_when_receiving_yards_is_missing(
    paths, client
) -> None:
    curve_world(paths)
    put_curves(
        paths,
        [
            crow("00-0000003", "C. End", target_key="receptions-wrte"),
            crow("00-0000002", "B. Back", target_key="carries-rb"),
        ],  # fmt: skip
    )
    assert outcomes(client)["stat"] == "receptions-wrte"


@pytest.mark.parametrize("target", ["carries-rb", "pass_yds-qb", "targets-wrte", "zzz-qq"])
def test_a_stat_the_week_does_not_have_is_a_fixed_404_that_does_not_echo_it(
    paths, client, target
) -> None:
    put_availability(paths, base_players())
    put_curves(paths, [crow("00-0000001", "A. Receiver")])
    r = client.get(OUT, params={"target": target})
    assert r.status_code == 404
    assert r.json() == {
        "error": {"code": "not_found", "message": "No curves for that stat this week."}
    }
    assert target.split("-")[0] not in r.text


@pytest.mark.parametrize(
    "target",
    [
        "REC_YDS-WRTE",
        "rec yds-wrte",
        "rec_yds",
        "-wrte",
        "rec_yds-",
        "rec_yds-wr te",
        "a" * 25 + "-b",
        "rec_yds-wrte-x",
        "rec_yds--wrte",
        "rec_yds-wrte;",
        "../x-y",
        "rec_yds-wrte\n",
        "%00",
    ],
)
def test_a_bad_stat_is_a_fixed_text_422(paths, client, target) -> None:
    curve_world(paths)
    r = client.get(OUT, params={"target": target})
    assert r.status_code == 422, repr(target)
    assert r.json()["error"]["code"] == "bad_request"
    assert "rec_yds" not in r.text and "wrte" not in r.text


def test_a_stat_with_no_week_file_is_still_the_empty_state(paths, client) -> None:
    promote(paths)
    o = fetch(client, OUT + "?target=carries-rb")
    assert o["status"] == "none"


# ---- the curve's own query ---------------------------------------------------------------


def test_a_curve_is_one_player_and_one_stat(paths, client) -> None:
    curve_world(paths)
    c = fetch(client, f"{CURVE}?player=00-0000003&target=receptions-wrte")
    assert (c["player_id"], c["stat"], c["team"], c["kind"]) == (
        "00-0000003", "receptions-wrte", "ATL", "count",
    )  # fmt: skip
    assert c["grid_lo"] == 0 and c["cdf"][-1] == 1.0


@pytest.mark.parametrize(
    ("player", "target"),
    [
        ("00-0000777", "rec_yds-wrte"),  # nobody by that id
        ("00-0000002", "rec_yds-wrte"),  # a player with no curve of that stat
        ("00-0000001", "zzz-qq"),  # a stat the contract doesn't know
        ("00-0000001", "pass_yds-qb"),  # a stat that this week lacks
    ],
)
def test_a_curve_that_is_not_there_is_a_fixed_text_404_without_the_input(
    paths, client, player, target
) -> None:
    curve_world(paths)
    r = client.get(CURVE, params={"player": player, "target": target})
    assert r.status_code == 404
    assert r.json()["error"]["message"] == "No curve for that player and stat this week."
    assert player not in r.text and target not in r.text


@pytest.mark.parametrize(
    "params",
    [
        {"player": "a" * 21, "target": "rec_yds-wrte"},
        {"player": "00 0000001", "target": "rec_yds-wrte"},
        {"player": "00-0000001;", "target": "rec_yds-wrte"},
        {"player": "../etc", "target": "rec_yds-wrte"},
        {"player": "", "target": "rec_yds-wrte"},
        {"player": "00-0000001", "target": "REC_YDS-WRTE"},
        {"player": "00-0000001", "target": ""},
        {"player": "00-0000001"},
        {"target": "rec_yds-wrte"},
        {},
    ],
)
def test_a_bad_curve_query_is_a_fixed_text_422(paths, client, params) -> None:
    curve_world(paths)
    r = client.get(CURVE, params=params)
    assert r.status_code == 422, params
    assert r.json()["error"]["code"] == "bad_request"
    for v in params.values():
        assert not v or v not in r.text


def test_a_curve_target_missing_from_the_weeks_shipped_list_is_absent_and_a_404(
    paths, client
) -> None:
    curve_world(paths)
    put_curves(
        paths,
        [
            crow("00-0000001", "A. Receiver"),
            crow("00-0000001", "A. Receiver", target_key="receptions-wrte"),
        ],
        meta=curves_meta(shipped=["rec_yds-wrte"]),
    )
    o = outcomes(client)
    assert [t["stat"] for t in o["targets"]] == ["rec_yds-wrte"] and o["tiles"]["stats"] == 1
    assert o["meta"]["shipped"] == ["rec_yds-wrte"]
    assert client.get(OUT + "?target=receptions-wrte").status_code == 404
    no_such_curve(client, "00-0000001", "receptions-wrte")
    assert client.get(f"{CURVE}?player=00-0000001&target=rec_yds-wrte").status_code == 200


def test_without_a_shipped_field_every_known_target_shows(paths, client) -> None:
    curve_world(paths)
    meta = curves_meta()
    del meta["shipped"]
    put_curves(
        paths,
        [
            crow("00-0000001", "A. Receiver"),
            crow("00-0000001", "A. Receiver", target_key="receptions-wrte"),
            crow("00-0000002", "B. Back", target_key="carries-rb"),
        ],  # fmt: skip
        meta=meta,
    )
    o = outcomes(client)
    assert [t["stat"] for t in o["targets"]] == [
        "rec_yds-wrte",
        "receptions-wrte",
        "carries-rb",
    ]
    assert client.get(f"{CURVE}?player=00-0000002&target=carries-rb").status_code == 200


def test_a_target_that_ships_in_the_list_but_has_no_rows_is_not_a_target(paths, client) -> None:
    curve_world(paths)
    put_curves(
        paths,
        [crow("00-0000001", "A. Receiver")],
        meta=curves_meta(shipped=["rec_yds-wrte", "pass_yds-qb", "carries-rb"]),
    )
    assert [t["stat"] for t in outcomes(client)["targets"]] == ["rec_yds-wrte"]


# ---- reading never writes -------------------------------------------------------------------


def test_the_readers_write_nothing(paths, client, tmp_path) -> None:
    sound_week(paths)

    def inventory() -> dict[str, tuple[int, int]]:
        return {
            str(p.relative_to(tmp_path)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in tmp_path.rglob("*")
            if p.is_file()
        }

    before = inventory()
    for url in (*all_urls("00-0000001"), AV + "?team=KC", OUT + "?target=carries-rb"):
        client.get(url)
    assert inventory() == before
