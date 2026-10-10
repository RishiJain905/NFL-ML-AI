"""LD03: the control room's finished-week state, `GET /api/live/{season}/{week}/review` and
`GET /api/live/{season}/season-review?through=N`
(documentation/live-decisions/LD03-decision-review.md).

The app runs on a hand-made curated folder (`live_review_fixtures.season_world`: nine 4th downs in
weeks 1 and 2, one in week 4, week 3 missing) with a stub decision engine whose win chances the
test chooses; the hand-worked numbers are in that module's docstring. Nothing reaches ESPN or the
network (the `no_real_network` guard), nothing is written outside tmp_path, and the app writes
only to its cache folder `cache/control-room/live/review/` (hashed before and after, like the
CR tests).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "live"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_app_live_review as TLR  # noqa: E402  (the TypeScript parser of the LD02 contract test)
from app_helpers import make_client  # noqa: E402
from live_fakes import no_real_network  # noqa: E402, F401  (autouse guard)
from live_review_fixtures import (  # noqa: E402
    G1,
    G2,
    World,
    season_engine,
    season_world,
)

from nflengine.app.readers.live import NO_MODELS, LiveService  # noqa: E402
from nflengine.live import review  # noqa: E402
from nflengine.ops.summary import scrub  # noqa: E402

KEY = "sk-or-v1-abcdefabcdef0123456789"  # shaped like a credential: scrub() masks it
WEEK = "/api/live/2025/{}/review"
SEASON = "/api/live/2025/season-review"
CACHE = "cache/control-room/live/review"
VERSION = "stub-v1"


class AppWorld:
    """One app over a curated folder: the review store (cache folder under the data root), a
    counted engine loader, and a TestClient."""

    def __init__(
        self,
        tmp_path: Path,
        world: World | None = None,
        *,
        engine: bool = True,
        loader_error: Exception | None = None,
        background: bool = False,
        slow_load: float = 0.0,
    ):
        self.root = tmp_path
        self.w = world or season_world(with_week4=True)
        self.paths = self.w.write(tmp_path)
        self.engine = season_engine()
        self.loads: list[int] = []

        def loader(_paths):
            self.loads.append(1)
            if slow_load:
                time.sleep(slow_load)
            if loader_error is not None:
                raise loader_error
            return self.engine, VERSION

        self.store = review.ReviewStore(
            cache_folder=self.paths.cache / "control-room" / "live" / "review",
            background_writes=background,
        )
        self.live = LiveService(
            engine_loader=loader,
            models_version=lambda _p: VERSION if engine else None,
            review_store=self.store,
            background=False,
        )
        self.http = make_client(tmp_path, live=self.live)

    def get(self, url: str, **headers: str):
        return self.http.get(url, headers=headers or None)

    def week(self, n: int, season: int = 2025) -> dict[str, Any]:
        r = self.http.get(f"/api/live/{season}/{n}/review")
        assert r.status_code == 200, r.text
        return r.json()

    def season(self, query: str = "") -> dict[str, Any]:
        r = self.http.get(f"{SEASON}{query}")
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def app(tmp_path):
    return AppWorld(tmp_path)


def play_ids(body: dict) -> list[int]:
    return [p["play_id"] for p in body["plays"]]


# --- the finished week ----------------------------------------------------------------------------
def test_a_finished_week_answers_with_plays_summary_highlights_and_games(app):
    body = app.week(1)
    assert body["status"] == "ok" and body["message"] is None
    assert (body["season"], body["week"]) == (2025, 1)
    assert body["model"] == {"version": VERSION, "in_sample": True, "trained_seasons": [2010, 2025]}
    assert body["source"] == "computed" and body["computed_at"].startswith("20")
    assert play_ids(body) == [1, 3, 5, 7, 9, 11], "kickoff order, then game order"
    s = body["summary"]
    assert (s["decisions"], s["agree"], s["toss_ups"], s["go_spots"], s["went_in_go_spots"]) == (
        6, 3, 1, 3, 1,
    )  # fmt: skip
    assert s["coach"] == {"go": 3, "fg": 1, "punt": 2} and s["bot"] == {"go": 4, "fg": 0, "punt": 2}
    assert s["wp_lost"] == pytest.approx(0.17) and (s["fakes"], s["wiped"], s["skipped"]) == (
        0,
        0,
        0,
    )
    hl = body["highlights"]
    assert hl["costliest"]["play_id"] == 11 and hl["boldest"]["play_id"] == 11
    assert [p["play_id"] for p in hl["top"]] == [11, 5, 3]
    (game,) = body["games"]
    assert (game["game_id"], game["away"], game["home"]) == (G1, "AWY", "HOM")
    assert (game["away_score"], game["home_score"], game["decisions"]) == (17, 24, 6)
    assert game["wp_lost"] == {"AWY": pytest.approx(0.14), "HOM": pytest.approx(0.03)}
    first, costly = body["plays"][0], body["plays"][-1]
    assert (first["choice"], first["best"], first["agree"], first["label"]) == (
        "go", "go", True, "Confident",
    )  # fmt: skip
    assert first["wp"] == {"go": 0.62, "fg": 0.58, "punt": 0.5} and first["edge"] == 0.04
    assert first["result"] == "Converted (+3 yds)" and first["situation"] == "4th & 2 at AWY 40"
    assert first["coach"] == "Home Coach" and first["clock"] == "10:00" and first["qtr"] == 3
    assert costly["cost"] == 0.08 and costly["edge"] == -0.08 and costly["best"] == "punt"
    assert body["plays"][1]["wp"]["fg"] is None, "an option the engine doesn't price is null"


def test_another_week_has_its_own_review(app):
    body = app.week(2)
    assert play_ids(body) == [1, 3, 5] and body["summary"]["decisions"] == 3
    assert body["games"][0]["game_id"] == G2 and body["highlights"]["costliest"]["play_id"] == 3
    assert body["highlights"]["boldest"] is None, "the only go was a clear call"


def test_the_review_uses_the_bootstrap_and_chunks_through_the_engine(app):
    app.week(1)
    assert app.engine.calls == [(6, True)] and app.loads == [1]


def test_a_second_request_is_served_without_the_engine(app):
    first = app.week(1)
    again = app.week(1)
    assert again["plays"] == first["plays"] and again["summary"] == first["summary"]
    assert app.engine.calls == [(6, True)] and app.loads == [1]
    app.season("?through=1")
    app.season("?through=1")
    assert len(app.engine.calls) == 1, "the season reused the week's review"


def test_a_new_app_reads_the_cache_the_first_one_wrote(tmp_path):
    first = AppWorld(tmp_path)
    first.week(1)
    first.season("?through=2")
    again = AppWorld(tmp_path)  # same data root, empty memory, a fresh engine that must not load
    body = again.week(1)
    assert body["source"] == "cache" and body["plays"] == first.week(1)["plays"]
    assert again.season("?through=2")["source"] == "cache"
    assert again.loads == [] and again.engine.calls == []


def test_a_new_app_reads_the_files_the_command_wrote(tmp_path):
    app0 = AppWorld(tmp_path)
    engine = season_engine()
    review.run_review(
        2025, [1, 2, 4], paths=app0.paths, engine_factory=lambda: (engine, VERSION),
        log=lambda s: None,
    )  # fmt: skip
    cmd_engine_calls = len(engine.calls)
    app1 = AppWorld(tmp_path)
    assert app1.week(1)["source"] == "file" and app1.week(4)["source"] == "file"
    assert app1.season()["source"] == "file"
    assert app1.loads == [] and app1.engine.calls == [] and cmd_engine_calls == 3
    assert not (tmp_path / "cache" / "control-room").exists(), "nothing needed the app's cache"


def test_a_new_model_version_is_rebuilt_not_served_stale(tmp_path):
    first = AppWorld(tmp_path)
    first.week(1)
    again = AppWorld(tmp_path)
    again.live._models_version = lambda _p: "stub-v2"  # noqa: SLF001
    again.live._engine_loader = lambda _p: (again.engine, "stub-v2")  # noqa: SLF001
    body = again.week(1)
    assert body["source"] == "computed" and body["model"]["version"] == "stub-v2"
    assert again.engine.calls == [(6, True)]


def test_a_season_outside_the_training_window_is_out_of_sample(tmp_path):
    app = AppWorld(tmp_path, season_world(season=2026))
    body = app.week(1, season=2026)
    assert body["status"] == "ok" and body["model"]["in_sample"] is False
    assert body["model"]["trained_seasons"] == [2010, 2025]


def test_two_requests_at_once_score_the_week_once(tmp_path):
    app = AppWorld(tmp_path, slow_load=0.3)
    out: list[Any] = []

    def ask():
        out.append(app.http.get(WEEK.format(1)))

    threads = [threading.Thread(target=ask) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert [r.status_code for r in out] == [200, 200, 200]
    assert len({json.dumps(r.json()["plays"], sort_keys=True) for r in out}) == 1
    assert app.engine.calls == [(6, True)] and app.loads == [1]


# --- weeks without a review -----------------------------------------------------------------------
def test_a_week_after_the_newest_curated_one_waits_for_tuesdays_run(tmp_path):
    app = AppWorld(tmp_path, season_world())  # weeks 1 and 2
    body = app.week(3)
    assert body["status"] == "no_plays"
    assert "Week 3's plays aren't in the curated data yet" in body["message"]
    assert "Tuesday" in body["message"]
    assert body["plays"] == [] and body["games"] == [] and body["summary"] is None
    assert body["highlights"] is None and body["model"] is None
    assert app.loads == [] and app.engine.calls == [], "no engine for a week with nothing to review"


def test_a_week_between_two_curated_ones_has_no_plays(app):
    body = app.week(3)  # weeks 1, 2 and 4 are in
    assert (
        body["status"] == "no_plays"
        and body["message"] == "Week 3 has no plays in the curated data."
    )


def test_a_season_with_no_curated_plays(app):
    body = app.week(1, season=2024)
    assert (
        body["status"] == "no_plays" and body["message"] == "No 2024 plays in the curated data yet."
    )
    season = app.get("/api/live/2024/season-review").json()
    assert season["status"] == "no_plays" and season["message"] == body["message"]
    assert season["leaderboard"] == [] and season["through_week"] is None and season["weeks"] == []


# --- no models ------------------------------------------------------------------------------------
def test_no_promoted_bundle_is_said_plainly(tmp_path):
    app = AppWorld(tmp_path, engine=False)
    for body in (app.week(1), app.season()):
        assert body["status"] == "no_models" and body["message"] == NO_MODELS
        assert body["model"] is None
    assert app.week(1)["plays"] == [] and app.season()["leaderboard"] == []
    assert app.loads == []


def test_a_pointer_with_no_files_behind_it_is_no_models_too(tmp_path):
    app = AppWorld(tmp_path, loader_error=FileNotFoundError("production.json points nowhere"))
    body = app.week(1)
    assert body["status"] == "no_models" and body["message"] == NO_MODELS
    assert "production.json" not in json.dumps(body)
    assert app.season()["status"] == "no_models"


def test_a_bundle_that_does_not_load_names_only_the_error_type(tmp_path):
    app = AppWorld(tmp_path, loader_error=RuntimeError(f"corrupt model file {KEY} at {tmp_path}"))
    r = app.http.get(WEEK.format(1))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "no_models" and "RuntimeError" in body["message"]
    assert KEY not in r.text and str(tmp_path) not in body["message"]
    assert len(app.loads) == 1
    app.week(1)
    assert len(app.loads) == 1, "a failed load is remembered for a minute, not retried per request"


def test_a_stored_review_is_not_served_when_there_is_no_bundle(tmp_path):
    first = AppWorld(tmp_path)
    first.week(1)
    none = AppWorld(tmp_path, engine=False)
    assert none.week(1)["status"] == "no_models"


# --- the season -----------------------------------------------------------------------------------
def test_the_season_review_through_the_newest_week(app):
    body = app.season()
    assert body["status"] == "ok" and body["message"] is None and body["source"] == "computed"
    assert body["through_week"] == 4 and body["weeks"] == [1, 2, 4] and body["decisions"] == 10
    assert body["model"] == {"version": VERSION, "in_sample": True, "trained_seasons": [2010, 2025]}
    assert [r["coach"] for r in body["leaderboard"]] == ["Home Coach", "Away Coach"]
    home = body["leaderboard"][0]
    assert (home["rank"], home["decisions"], home["go_spots"], home["went_in_go_spots"]) == (
        1,
        5,
        3,
        2,
    )
    assert home["go_rate_spots"] == pytest.approx(0.6667) and home["teams"] == ["HOM"]
    assert body["league"]["decisions"] == 10 and "coach" not in body["league"]
    assert [t["week"] for t in body["trend"]] == [1, 2, 4]
    cal = body["calibration"]
    assert cal["wp"]["games"] == 3 and cal["conversion"]["rows"] and cal["fg"]["n"] == 1
    assert {"model", "vegas"} <= set(cal["wp"])


def test_the_season_review_through_a_given_week(app):
    body = app.season("?through=2")
    assert body["through_week"] == 2 and body["weeks"] == [1, 2] and body["decisions"] == 9
    assert body["trend"][-1]["week"] == 2
    assert body["league"]["go_spots"] == 5 and body["league"]["wp_lost"] == pytest.approx(0.22)
    assert app.season("?through=2")["source"] == "memory"


def test_through_a_week_without_plays_clamps_to_the_newest_one_before_it(app):
    body = app.season("?through=3")
    assert body["through_week"] == 2 and body["weeks"] == [1, 2]
    assert app.season("?through=22")["through_week"] == 4


def test_through_below_the_first_week_with_plays_is_no_plays(tmp_path):
    w = season_world()
    w.rows = [r for r in w.rows if r["week"] != 1]
    del w.games[G1]
    app = AppWorld(tmp_path, w)
    body = app.season("?through=1")
    assert body["status"] == "no_plays" and body["through_week"] is None
    assert body["message"] == "No 2025 week up to week 1 is in the curated data yet."
    assert body["leaderboard"] == [] and body["calibration"] is None and body["league"] is None
    assert app.loads == [], "the empty answer never needed the engine"
    assert app.season()["through_week"] == 2, "no cut: the newest week there is"


# --- bad requests ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "/api/live/2025/23/review",
        "/api/live/2025/0/review",
        "/api/live/1998/1/review",
        "/api/live/2025/x/review",
        "/api/live/2025/1.5/review",
        "/api/live/2025/season-review?through=0",
        "/api/live/2025/season-review?through=23",
        "/api/live/2025/season-review?through=zzz",
        "/api/live/1998/season-review",
        "/api/live/abc/season-review",
    ],
)
def test_bad_season_week_or_through_is_a_422_with_fixed_text(app, url):
    r = app.get(url)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "bad_request"
    assert "zzz" not in r.text and "abc" not in r.text, "the input is never echoed"
    assert app.loads == [] and app.engine.calls == []


# --- other sites ----------------------------------------------------------------------------------
@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site"])
def test_cross_site_requests_are_refused_before_anything_runs(app, site):
    for url in (WEEK.format(1), SEASON, f"{SEASON}?through=2"):
        r = app.get(url, **{"Sec-Fetch-Site": site})
        assert r.status_code == 403, url
        assert r.json()["error"]["code"] == "not_allowed"
    assert app.loads == [] and app.engine.calls == []
    assert not (app.root / "cache" / "control-room").exists()


@pytest.mark.parametrize("site", ["same-origin", "none", None])
def test_the_pages_own_requests_pass(app, site):
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    assert app.get(WEEK.format(1), **headers).status_code == 200
    assert app.get(SEASON, **headers).status_code == 200


# --- the app writes only its cache ----------------------------------------------------------------
def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
        )
        for p in root.rglob("*")
    }


@pytest.mark.parametrize("background", [False, True])
def test_the_app_writes_nothing_outside_its_review_cache(tmp_path, background):
    app = AppWorld(tmp_path, background=background)
    before = tree(tmp_path)
    for url in (
        WEEK.format(1), WEEK.format(2), WEEK.format(3), WEEK.format(9), WEEK.format(1),
        SEASON, f"{SEASON}?through=2", f"{SEASON}?through=3", "/api/live/2024/1/review",
        "/api/live/2024/season-review",
    ):  # fmt: skip
        assert app.get(url).status_code == 200, url
    app.store.flush(10)
    after = tree(tmp_path)
    assert not (set(before) - set(after)), "nothing was removed"
    assert not {k for k in before if k in after and before[k] != after[k]}, "nothing was changed"
    added = set(after) - set(before)
    assert added, "the review cache was written"
    for k in added:
        assert k.startswith(CACHE) or CACHE.startswith(k), f"{k} is outside the review cache"
    names = {Path(k).name for k in added if k.startswith(CACHE + "/")}
    assert {"decision_review.parquet", "decision_review.json", "season_review.json"} <= names
    assert not app.paths.live_data.exists(), "the official folder is the command's"


# --- text reaches the browser cleaned -------------------------------------------------------------
def leaky_world(root: Path) -> World:
    w = season_world()
    here = str(root)
    w.rows[0]["desc"] = f"\x1b[31mfirst {KEY} then {here}\\notes.txt and that is all"
    for g in w.games.values():
        g["home_coach"] = f"Coach {KEY} Jr"
    for r in w.rows:
        r["home_coach"] = f"Coach {KEY} Jr"
    return w


def strings(o: Any):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


def test_text_fields_are_cleaned_before_they_reach_the_browser(tmp_path):
    app = AppWorld(tmp_path, leaky_world(tmp_path))
    root = str(tmp_path)
    for url in (WEEK.format(1), WEEK.format(2), SEASON):
        r = app.http.get(url)
        assert r.status_code == 200, url
        assert KEY not in r.text, "a key-shaped string is masked"
        assert root not in r.text and root.replace("\\", "\\\\") not in r.text
        assert all(root not in s for s in strings(r.json())), "the data root is made relative"
        assert scrub(r.text, limit=10**9) == r.text, f"{url} has text that looks like a secret"
    first = app.week(1)["plays"][0]
    assert first["desc"].startswith("\x1b[31mfirst ***") or "first ***" in first["desc"]
    assert "notes.txt" in first["desc"] and "that is all" in first["desc"]
    assert first["coach"] == "Coach *** Jr"
    assert app.season()["leaderboard"][0]["coach"] in ("Coach *** Jr", "Away Coach")


# --- the contract with web/src/api/types.ts -------------------------------------------------------
CHILD: dict[tuple[str, str], tuple[str, bool]] = {
    ("LiveReviewResponse", "model"): ("ReviewModel", False),
    ("LiveReviewResponse", "summary"): ("ReviewSummary", False),
    ("LiveReviewResponse", "games"): ("ReviewGame", True),
    ("LiveReviewResponse", "plays"): ("ReviewPlay", True),
    ("LiveSeasonReviewResponse", "model"): ("ReviewModel", False),
    ("LiveSeasonReviewResponse", "leaderboard"): ("ReviewCoachRow", True),
    ("LiveSeasonReviewResponse", "league"): ("ReviewCoachRow", False),
    ("LiveSeasonReviewResponse", "trend"): ("ReviewTrendRow", True),
    ("LiveSeasonReviewResponse", "calibration"): ("ReviewCalibration", False),
}
NAMED = re.compile(r"\b(?:Live|Review)[A-Z]\w*\b")


def type_ok(value: Any, ty: str, optional: bool) -> bool:
    if value is None:
        return optional or "null" in ty
    if isinstance(value, bool):
        return "boolean" in ty
    if isinstance(value, int | float):
        literal = re.fullmatch(r"\s*\d+(\s*\|\s*\d+)*\s*", ty)
        return "number" in ty or bool(literal and str(value) in re.findall(r"\d+", ty))
    if isinstance(value, str):
        if "string" in ty or value in set(re.findall(r"'([^']+)'", ty)):
            return True
        return any(value in (TLR.ts_alias(n) or ()) for n in NAMED.findall(ty))
    if isinstance(value, list):
        return "[]" in ty or ty.lstrip().startswith("[")
    return ty.lstrip().startswith("{") or "Record<" in ty or bool(NAMED.search(ty))


def check_shape(obj: Any, name: str, where: str, problems: list[str]) -> None:
    fields = TLR.ts_interface(name)
    if not isinstance(obj, dict):
        problems.append(f"{where}: expected an object for {name}, got {type(obj).__name__}")
        return
    for k in sorted(set(obj) - set(fields)):
        problems.append(f"{where}: field {k!r} is not in {name}")
    for k, (ty, optional, keys) in fields.items():
        if k not in obj:
            if not optional:
                problems.append(f"{where}: {name}.{k} is missing")
            continue
        v = obj[k]
        if not type_ok(v, ty, optional):
            problems.append(f"{where}.{k}: {v!r} does not fit {ty}")
        if keys is not None and isinstance(v, dict) and set(v) != set(keys):
            problems.append(f"{where}.{k}: keys {sorted(v)} != {sorted(keys)}")
        child = CHILD.get((name, k))
        if child and v is not None:
            iface, is_list = child
            for i, item in enumerate(v if is_list else [v]):
                check_shape(item, iface, f"{where}.{k}" + (f"[{i}]" if is_list else ""), problems)


def check_inline_rows(rows: Any, keys: list[str], where: str, problems: list[str]) -> None:
    for i, row in enumerate(rows or []):
        if set(row) != set(keys):
            problems.append(f"{where}[{i}]: keys {sorted(row)} != {sorted(keys)}")


def check_week(body: dict, where: str, problems: list[str]) -> None:
    check_shape(body, "LiveReviewResponse", where, problems)
    hl = body.get("highlights")
    if hl:
        for k in ("boldest", "costliest"):
            if hl[k] is not None:
                check_shape(hl[k], "ReviewPlay", f"{where}.highlights.{k}", problems)
        for i, p in enumerate(hl["top"]):
            check_shape(p, "ReviewPlay", f"{where}.highlights.top[{i}]", problems)
    for p in body.get("plays") or []:
        if set(p["wp"]) != {"go", "fg", "punt"}:
            problems.append(f"{where}: a play's wp has {sorted(p['wp'])}")
    summary = body.get("summary")
    if summary:
        for k in ("coach", "bot"):
            if set(summary[k]) != {"go", "fg", "punt"}:
                problems.append(f"{where}.summary.{k}: keys {sorted(summary[k])}")


def check_season(body: dict, where: str, problems: list[str]) -> None:
    check_shape(body, "LiveSeasonReviewResponse", where, problems)
    cal = body.get("calibration")
    if not cal:
        return
    cal_keys = TLR.ts_interface("ReviewCalibration")
    if cal["wp"]:
        for k in ("model", "vegas"):
            for i, b in enumerate(cal["wp"][k]):
                check_shape(b, "ReviewCalBin", f"{where}.calibration.wp.{k}[{i}]", problems)
    if cal["conversion"]:
        for i, row in enumerate(cal["conversion"]["rows"]):
            check_shape(
                row, "ReviewConversionRow", f"{where}.calibration.conversion.rows[{i}]", problems
            )
        check_inline_rows(
            cal["conversion"]["overall"], ["down", "n", "actual", "pred", "brier"],
            f"{where}.calibration.conversion.overall", problems,
        )  # fmt: skip
    if cal["fg"]:
        check_inline_rows(
            cal["fg"]["rows"],
            ["band", "n", "made", "pred"],
            f"{where}.calibration.fg.rows",
            problems,
        )
        for row in cal["fg"]["rows"]:
            if row["band"] not in ("<30", "30-39", "40-49", "50+"):
                problems.append(f"{where}: band {row['band']!r}")
    assert {"wp", "conversion", "fg"} == set(cal_keys) & {"wp", "conversion", "fg"}


def contract_world() -> World:
    """The season world plus a week 3 with a fake punt, a wiped-out go, a state the rules refuse
    and a home team with no coach on file."""
    w = season_world(with_week4=True)
    g = w.game("2025_03_AWY_HOM", 3, "HOM", "AWY", home_coach=None)
    desc = "(Punt formation) J.Smith runs left end for 12 yards (T.Jones)."
    w.go(g, "HOM", 9, 61, converted=True, gained=12, desc=desc)
    w.snap(g, "HOM", 49)
    w.add(g, "AWY", "no_play", 4, 3, 38, desc="T.Brady pass short right to C.Davis for 5 yards. "
          "PENALTY on HOM, Defensive Holding, 5 yards - No Play.")  # fmt: skip
    w.snap(g, "AWY", 33)
    w.go(g, "AWY", 5, 3, converted=False, gained=0)  # refused: 5 to go from the 3
    w.snap(g, "HOM", 60)
    return w


def test_the_ts_parser_sees_the_review_types():
    """A guard on the guard: the interfaces this test reads are there."""
    assert {"status", "model", "plays", "highlights", "games"} <= set(
        TLR.ts_interface("LiveReviewResponse")
    )
    assert TLR.ts_interface("LiveReviewResponse")["highlights"][2] == [
        "boldest",
        "costliest",
        "top",
    ]
    assert TLR.ts_alias("ReviewStatus") == {"ok", "no_plays", "no_models"}
    assert TLR.ts_interface("ReviewCoachRow")["rank"][1] is True, "the league row has no rank"
    assert TLR.ts_interface("ReviewCalibration")["wp"][2][:2] == ["snaps", "games"]


def test_every_review_answer_matches_the_typescript_types(tmp_path):
    problems: list[str] = []
    app = AppWorld(tmp_path / "rich", contract_world())
    for wk in (1, 2, 3, 4):
        check_week(app.week(wk), f"week{wk}", problems)
    body = app.week(3)
    assert body["summary"]["fakes"] == 1 and body["summary"]["wiped"] == 1
    assert body["summary"]["skipped"] == 1, "the contract world has one refused state"
    assert any(p["coach"] is None for p in body["plays"]), "a null coach is in the answers"
    assert any(p["success"] is None for p in app.week(1)["plays"]), "a punt has no success"
    for q in ("", "?through=1", "?through=2", "?through=3", "?through=4"):
        check_season(app.season(q), f"season{q}", problems)
    # the empty states
    plain = AppWorld(tmp_path / "plain", season_world())
    check_week(plain.week(3), "no_plays.week", problems)
    check_week(plain.week(1, season=2024), "no_plays.year", problems)
    check_season(plain.get("/api/live/2024/season-review").json(), "no_plays.season", problems)
    none = AppWorld(tmp_path / "none", engine=False)
    check_week(none.week(1), "no_models.week", problems)
    check_season(none.season(), "no_models.season", problems)
    assert not problems, "\n".join(problems)


def test_the_contract_check_catches_a_drifting_answer(tmp_path):
    """A guard on the guard: an unknown field, a missing one and a wrong type are all reported."""
    app = AppWorld(tmp_path)
    body = app.week(1)
    problems: list[str] = []
    check_week(body, "ok", problems)
    assert problems == []
    bad = json.loads(json.dumps(body))
    bad["surprise"] = 1
    del bad["summary"]["agree"]
    bad["plays"][0]["gap"] = "wide"
    bad["plays"][1]["wp"] = {"go": 0.5}
    bad["highlights"]["boldest"]["choice"] = "kneel"
    bad["status"] = "gone"
    check_week(bad, "bad", problems)
    text = "\n".join(problems)
    for needle in (
        "'surprise' is not in LiveReviewResponse", "ReviewSummary.agree is missing",
        "plays[0].gap: 'wide'", "a play's wp has ['go']", "boldest.choice: 'kneel'",
        "bad.status: 'gone'",
    ):  # fmt: skip
        assert needle in text, needle
    season = app.season()
    season["calibration"]["fg"]["rows"][0]["band"] = "60+"
    season["trend"][0]["week"] = None
    more: list[str] = []
    check_season(season, "season", more)
    assert any("band '60+'" in p for p in more) and any("trend[0].week" in p for p in more)
