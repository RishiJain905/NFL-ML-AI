"""The player model in the digest (plan P06): model picks, tough spots, the look-back, the
scoreboard highlights, scoring saved picks, backtest materialization and the checks."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest
from conftest import make_payload

from nflengine.digest import format as F
from nflengine.digest import render as R
from nflengine.digest.facts import build_fact_index
from nflengine.digest.llm.placeholder import PlaceholderLLM
from nflengine.digest.payload import LookbackItem, ReportCard, SideTally
from nflengine.digest.players import (
    PLAYER_PRED_FILE,
    WatchSizes,
    build_model_watch,
    driver_texts,
    lookback_text,
    model_watch,
    player_watch,
    score_watch_rows,
    scoreboard_highlights,
    stamp_model_watch,
    watch_lookback,
    week_improvement,
)
from nflengine.digest.prompt import load_prompt
from nflengine.digest.report_card import PRED_FILE, WATCH_FILE, build_report_card
from nflengine.digest.synthesize import synthesize
from nflengine.models.player_schema import MAIN_TARGETS, PRED_SCHEMA, conform

TUESDAY = dt.datetime(2025, 10, 21, 14, tzinfo=dt.UTC)
SUNDAY = dt.datetime(2025, 10, 26, 17, tzinfo=dt.UTC)
BUDGETS = {
    "report_card": 60,
    "game_outlook": 80,
    "team_trends": 120,
    "under_the_hood": 110,
    "players_to_watch": 160,
}


def drv(phrase: str, contribution: float) -> dict:
    return {"feature": phrase.replace(" ", "_"), "phrase": phrase, "value": 1.0,
            "contribution": contribution}  # fmt: skip


def proj(pid: str, name: str, team: str, opp: str, z: float, group: str = "WR/TE", **kw) -> dict:
    t = MAIN_TARGETS[group]
    base = 60.0 if t.unit == "yards" else 5.0
    step = 25.0 if t.unit == "yards" else 1.5
    r = {
        "season": 2025, "week": 8, "game_id": f"2025_08_{opp}_{team}", "kickoff_utc": SUNDAY,
        "created_at": TUESDAY, "player_id": pid, "player": name, "team": team,
        "opponent": opp, "home": True, "position": {"WR/TE": "WR", "LB/S": "LB"}.get(group, group),
        "pgroup": "WR" if group == "WR/TE" else "LB", "group": group, "target": t.name,
        "target_label": t.label, "kind": t.kind, "unit": t.unit, "is_main": True,
        "p10": base + step * z - step, "p50": base + step * z, "p90": base + step * z + 2 * step,
        "baseline": base, "baseline_source": "rolling", "outperformance": step * z,
        "outperf_z": z, "role_ok": True, "role_change": False, "confidence": "medium",
        "drivers": [drv("target share up over his last 4 games", step * 0.6),
                    drv("opponent allows many yards to his position", -step * 0.2)],
        "model_version": "player-model-v1:2025-w08",
    }  # fmt: skip
    r.update(kw)
    return r


def frame(rows: list[dict]) -> pl.DataFrame:
    return conform(pl.DataFrame(rows, schema_overrides={"drivers": PRED_SCHEMA["drivers"]}))


def week_preds() -> pl.DataFrame:
    return frame(
        [
            proj("p1", "Puka Nacua", "LA", "SF", 1.0),
            proj("p2", "Zay Flowers", "BAL", "CHI", 0.6, confidence="low"),
            proj("p3", "Fred Warner", "SF", "LA", 0.8, group="LB/S"),
            proj("p4", "Garrett Wilson", "NYJ", "MIA", 0.4),
            proj("t1", "Davante Adams", "LV", "KC", -1.2),
        ]
    )


# ---- format helpers -------------------------------------------------------------------------


def test_player_stat_formats_say_their_unit_and_direction():
    assert F.stat(84.4, "receiving yards").display == "84 receiving yards"
    assert F.stat(5.25, "tackles", "count").display == "5.3 tackles"
    assert F.stat(6.0, "tackles", "count").display == "6 tackles"
    assert F.stat(1.0, "carries", "count").display == "1 carry"
    assert F.stat(0.123, "EPA per dropback", "epa").display == "+0.12 EPA per dropback"
    assert F.stat_range(52.2, 118.4, "receiving yards").display == "52–118 receiving yards"
    assert F.vs_baseline(23.2, "receiving yards").display == "23 receiving yards above his baseline"
    assert F.vs_baseline(-0.44, "tackles", "count").display == "0.4 tackles below his baseline"
    assert F.vs_baseline(0.2, "receiving yards").display == "level with his baseline"
    # a driver is measured from a typical player in his group, never from his own baseline
    assert F.driver_effect(9.4, "receiving yards").display == (
        "puts the projection 9 receiving yards above a typical player in his group"
    )
    assert F.driver_effect(-0.4, "tackles", "count").display == (
        "puts the projection 0.4 tackles below a typical player in his group"
    )
    assert F.driver_effect(0.3, "receiving yards") is None
    assert F.number_atoms(F.driver_effect(-0.4, "tackles", "count").display) == ["0.4"]


def test_drivers_pushing_the_picks_way_come_first():
    drivers = [drv("recent form", -11.0), drv("snap share", 6.0), drv("opponent", 3.0)]
    up = driver_texts(drivers, "passing yards", "yards", direction=15.0)
    assert [t.split(" (")[0] for t in up] == ["snap share", "opponent", "recent form"]
    down = driver_texts(drivers, "passing yards", "yards", direction=-20.0)
    assert down[0].startswith("recent form (puts the projection 11 passing yards below")
    assert driver_texts(drivers, "passing yards", "yards")[0].startswith("recent form")


def test_a_driver_smaller_than_a_fifth_of_the_gap_is_not_shown():
    """Fact-check: Kirk Cousins was +29 yards vs baseline with a 3-yard 'main driver'."""
    drivers = [drv("his yards per carry lately", 3.0), drv("recent form", -7.0)]
    assert driver_texts(drivers, "passing yards", "yards", direction=29.0) == [
        "recent form (puts the projection 7 passing yards below a typical player in his group)"
    ]
    assert driver_texts(drivers, "passing yards", "yards", direction=40.0) == []
    rows = frame([proj("q1", "Kirk Cousins", "LV", "KC", 1.0, drivers=drivers, games_season=2)])
    (w,) = build_model_watch(rows)  # gap 25: 20% = 5, the 3-yard driver is out, -7 stays
    assert len(w.drivers) == 1 and w.driver_note is None
    big = [drv("his yards per carry lately", 3.0), drv("recent form", -4.0)]
    (w,) = build_model_watch(frame([proj("q1", "Kirk Cousins", "LV", "KC", 1.0, drivers=big,
                                         games_season=2)]))  # fmt: skip
    assert w.drivers == [] and w.driver_note == "no single factor stands out"
    p = make_payload(players_to_watch=[w])
    cell = R.watch_table(p).splitlines()[-1]
    assert "no single factor stands out; his baseline comes from 2 games this season" in cell
    text = run_placeholder(p).sections["players_to_watch"]
    assert "For Cousins, no single factor stands out." in text


def test_driver_texts_drop_silent_and_banned_phrases():
    drivers = [
        drv("target share up", 9.0),
        drv("barely matters", 0.2),  # rounds to 0 yards
        drv("market spread favors his team", 5.0),  # banned word: never reaches the prose
        drv("opponent allows many yards", -4.0),
        {"feature": "x", "phrase": "no contribution", "value": 1.0, "contribution": None},
    ]
    assert driver_texts(drivers, "receiving yards", "yards") == [
        "target share up (puts the projection 9 receiving yards above a typical player in his "
        "group)",
        "opponent allows many yards (puts the projection 4 receiving yards below a typical "
        "player in his group)",
    ]


# ---- payload items --------------------------------------------------------------------------


def test_model_items_carry_projection_range_baseline_and_notes():
    rows = frame(
        [
            proj("p1", "Puka Nacua", "LA", "SF", 1.0, home=False, injury_status="Questionable"),
            proj(
                "p9",
                "Jalen Coker",
                "CAR",
                "ATL",
                0.5,
                role_ok=False,
                role_change=True,
                vacated_share=0.21,
                baseline_source="role",
                confidence="weird",
            ),
        ]  # fmt: skip
    )
    w1, w2 = build_model_watch(rows)
    assert w1.source == "model" and w1.target == "receiving yards" and w1.group == "WR/TE"
    assert w1.projection.display == "85 receiving yards"
    assert w1.interval.display == "60–135 receiving yards"
    assert w1.baseline.display == "60 receiving yards"
    assert w1.vs_baseline.display == "25 receiving yards above his baseline"
    assert w1.matchup == "Rams at 49ers"  # away at home
    assert w1.injury_note == "listed questionable on this week's injury report"
    assert w1.drivers[0].endswith(
        "(puts the projection 15 receiving yards above a typical player in his group)"
    )
    assert w1.opp_def_rank is None and w1.usage_metric is None
    assert w2.role_note == (
        "a bigger role this week: regular teammates who missed his team's last game or are "
        "ruled out this week had 21% of his position group's usage"
    )
    assert w2.baseline_note.startswith("his baseline is the average for players in his role")
    assert w2.confidence == "low"  # unknown labels are treated as low


def test_baseline_note_only_when_the_baseline_rests_on_little():
    from nflengine.digest.players import baseline_note

    assert baseline_note({"baseline_source": "rolling", "games_season": 6}) is None
    assert baseline_note({"baseline_source": "rolling", "games_season": 2}) == (
        "his baseline comes from 2 games this season"
    )
    assert baseline_note({"baseline_source": "rolling", "games_season": 1}).endswith(
        "from 1 game this season"
    )
    assert baseline_note({"baseline_source": "last_season", "games_season": 1}) == (
        "his baseline comes mostly from last season (1 game this season)"
    )
    # pressures (PFR) arrive a week late: say so, or "2 games" for a 3-game player reads wrong
    late = {"baseline_source": "rolling", "games_season": 2, "target": "pressures"}
    assert baseline_note(late) == (
        "his baseline comes from 2 games this season (pressures data arrives a week late)"
    )
    assert baseline_note({**late, "target": "tackles"}) == (
        "his baseline comes from 2 games this season"
    )
    (w,) = build_model_watch(frame([proj("p1", "Puka Nacua", "LA", "SF", 1.0, games_season=2)]))
    p = make_payload(players_to_watch=[w])
    assert "his baseline comes from 2 games this season" in R.watch_table(p)
    assert "2" in build_fact_index(p).entities["player:p1"].atoms


def test_a_failed_refit_uses_the_heuristic_and_says_why(tmp_path: Path):
    from nflengine.digest.players import PlayerWatch
    from nflengine.digest.run import REFIT_FAILED_VERSION, choose_watch, make_context
    from nflengine.models.player_schema import write_status
    from nflengine.paths import DataPaths

    ctx = make_context(2025, 8, "live", TUESDAY, DataPaths(tmp_path))
    calls: list[str] = []

    def heuristic(ctx, games, followed, n_items, version="heuristic-v0 (none)"):
        calls.append(version)
        return PlayerWatch(pl.DataFrame(), [], [], version)

    logs: list[str] = []
    # no projections: the plain fallback
    assert (
        choose_watch(ctx, pl.DataFrame(), [], WatchSizes(), logs.append, heuristic).version
        == calls[0]
    )
    # projections present: the model
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    week_preds().write_parquet(ctx.run_dir / PLAYER_PRED_FILE)
    pw = choose_watch(ctx, pl.DataFrame(), [], WatchSizes(), logs.append, heuristic)
    assert pw.version == "player-model-v1:2025-w08" and len(calls) == 1
    # a failed refit: the old file is ignored, the footer says why, one log line
    write_status(ctx.run_dir, "degraded", "ValueError")
    logs.clear()
    pw = choose_watch(ctx, pl.DataFrame(), [], WatchSizes(), logs.append, heuristic)
    assert pw.version == REFIT_FAILED_VERSION and calls[-1] == REFIT_FAILED_VERSION
    assert len(logs) == 1 and "refit failed" in logs[0]
    write_status(ctx.run_dir, "ok")
    assert choose_watch(ctx, pl.DataFrame(), [], WatchSizes(), logs.append, heuristic).items


def test_model_watch_picks_and_tough_spots_and_stamps():
    pw = model_watch(week_preds(), TUESDAY)
    assert [(w.player, w.side) for w in pw.items] == [
        ("Puka Nacua", "offense"), ("Zay Flowers", "offense"), ("Garrett Wilson", "offense"),
        ("Fred Warner", "defense"),
    ]  # fmt: skip
    assert [w.player for w in pw.tough] == ["Davante Adams"]
    assert pw.frame["side"].to_list() == ["offense"] * 3 + ["defense"]
    small = model_watch(week_preds(), TUESDAY, sizes=WatchSizes(offense=1, defense=0, tough=0))
    assert [w.player for w in small.items] == ["Puka Nacua"] and small.tough == []
    assert pw.version == "player-model-v1:2025-w08"
    f = pw.frame
    assert f["source"].unique().to_list() == ["model"]
    assert f["created_at"].unique().to_list() == [TUESDAY]
    assert f["projected_at"].unique().to_list() == [TUESDAY]
    later = TUESDAY + dt.timedelta(days=1)
    assert stamp_model_watch(week_preds().head(1), later)["created_at"].item() == later


def test_player_watch_is_none_without_a_projection_file(tmp_path: Path):
    assert player_watch(tmp_path, TUESDAY) is None
    week_preds().write_parquet(tmp_path / PLAYER_PRED_FILE)
    pw = player_watch(tmp_path, TUESDAY, sizes=WatchSizes(offense=1, defense=1))
    assert pw is not None and len(pw.items) == 2


# ---- checks, placeholder, render ------------------------------------------------------------


def model_payload(**rc_kw):
    pw = model_watch(week_preds(), TUESDAY)
    rc = make_payload().report_card.model_copy(update=rc_kw)
    return make_payload(players_to_watch=pw.items, tough_spots=pw.tough, report_card=rc)


def run_placeholder(payload, budgets=BUDGETS):
    return synthesize(payload, build_fact_index(payload), PlaceholderLLM(), load_prompt(), budgets)


def test_placeholder_writes_model_picks_that_pass_every_check():
    lb = LookbackItem(
        player="Tee Higgins", player_id="00-9", team="CIN", team_name="Bengals",
        position="WR", target="receiving yards",
        text=lookback_text({"source": "model", "p10": 30.0, "p50": 70.0, "p90": 110.0,
                            "baseline": 55.0, "actual": 97.0, "played": True,
                            "target_label": "receiving yards", "unit": "yards"}),
        played=True, hit=True, inside=True,
    )  # fmt: skip
    p = model_payload(
        scoreboard_highlights=["receiving yards projections beat the rolling baseline by 9% "
                               "so far this season (3 weeks scored)"],
        watch_lookback=[lb], watchlist_inside=F.count(1),
    )  # fmt: skip
    # a roomier report card so the optional highlight sentence fits (60 words leave no room)
    synth = run_placeholder(p, {**BUDGETS, "report_card": 90})
    assert synth.final.passed, synth.final.to_dict()
    assert "hedging" not in synth.final.warnings
    text = synth.sections["players_to_watch"]
    assert "the model projects 85 receiving yards" in text
    assert "Zay Flowers" in text and "low confidence" in text
    assert "Player projections: receiving yards projections beat" in synth.sections["report_card"]


def test_checks_catch_a_projection_moved_to_another_player_and_a_missing_hedge():
    from nflengine.digest.checks import run_checks

    p = model_payload()
    fx = build_fact_index(p)
    wrong = {"players_to_watch": "Garrett Wilson: the model projects 85 receiving yards."}
    r = run_checks(wrong, fx)
    assert "entity_binding" in r.failed
    unhedged = {"players_to_watch": "Zay Flowers: the model projects 75 receiving yards."}
    assert "hedging" in run_checks(unhedged, fx).warnings
    # tough spots and the look-back are code-written: no hedge duty, but numbers bind
    tough = {"players_to_watch": "Davante Adams is projected at 30 receiving yards."}
    assert run_checks(tough, fx).passed


def test_render_uses_the_model_table_and_places_tough_spots():
    p = model_payload()
    table = R.watch_table(p)
    assert table.startswith("**Offense** (3 picks)\n\n| Player | Game | Projection |")
    assert "**Defense** (1 pick)" in table
    assert table.index("Garrett Wilson") < table.index("**Defense**") < table.index("Fred Warner")
    assert (
        "| Puka Nacua (Rams WR) | vs 49ers | 85 receiving yards | 60–135 receiving yards" in table
    )
    tough = R.tough_spots_list(p)
    assert "Davante Adams (Raiders WR) vs the Chiefs: projected 30 receiving yards, 30 " in tough
    info = R.FooterInfo(True, [], [], False, "abc", "placeholder", "templates-v1")
    secs = {"report_card": "A.", "players_to_watch": "B.", "matchup_risk": "C."}
    md = R.render_digest(p, secs, info, None)
    assert R.MODEL_WATCH_NOTE in md and md.index("C.") < md.index("**Tough spots**")
    del secs["matchup_risk"]  # no graph sections: the list sits under the picks
    md = R.render_digest(p, secs, info, None)
    assert md.index("B.") < md.index("**Tough spots**")


def test_heuristic_payload_keeps_its_table_and_note():
    p = make_payload()  # conftest: a heuristic pick
    assert R.watch_source(p) == "heuristic"
    assert "Usage (recent vs before)" in R.watch_table(p)
    info = R.FooterInfo(True, [], [], False, "abc", "placeholder", "templates-v1")
    md = R.render_digest(p, {"players_to_watch": "B."}, info, None)
    assert R.HEURISTIC_WATCH_NOTE in md and "**Tough spots**" not in md


def test_report_card_numbers_show_highlights_and_the_look_back():
    rc = ReportCard(
        status="scored", scored_week=7, scored_week_display=F.count(7),
        picks_correct=F.count(9), picks_total=F.count(14), brier=F.brier(0.21),
        brier_elo=F.brier(0.22), points_mae=F.points_error(7.1),
        watchlist_hits=F.count(4), watchlist_total=F.count(6), watchlist_inside=F.count(5),
        scoreboard_highlights=["no live week of player projections has been scored yet"],
        watch_lookback=[LookbackItem(player="Tee Higgins", player_id="x", team="CIN",
                                     team_name="Bengals", position="WR",
                                     target="receiving yards", text="did not play, so not scored")],
    )  # fmt: skip
    out = R.report_card_numbers(rc)
    assert "watch list 4 of 6 above baseline, 5 inside their range" in out
    assert "**Player projections:** No live week of player projections has been scored yet." in out
    rc2 = rc.model_copy(update={"scoreboard_highlights": ["a b", "the 80% ranges held"]})
    assert "**Player projections:** A b. The 80% ranges held." in R.report_card_numbers(rc2)
    assert "| – | Tee Higgins (Bengals WR) | receiving yards | – | – | – | – | – |" in out


def test_the_look_back_is_one_compact_table_with_side_tallies():
    lb = [
        LookbackItem(
            player="Puka Nacua",
            player_id="a",
            team="LA",
            team_name="Rams",
            position="WR",
            target="receiving yards",
            text="t",
            played=True,
            hit=True,
            inside=True,
            side="offense",
            projection=F.stat(85, "receiving yards"),
            interval=F.stat_range(60, 135, "receiving yards"),
            actual=F.stat(97, "receiving yards"),
        ),
        LookbackItem(
            player="Fred Warner",
            player_id="b",
            team="SF",
            team_name="49ers",
            position="LB",
            target="tackles",
            text="t",
            played=False,
            side="defense",
            projection=F.stat(6.2, "tackles", "count"),
            interval=F.stat_range(3, 9, "tackles", "count"),
            status="did not play",
        ),
    ]
    rc = ReportCard(
        status="scored", scored_week=7, scored_week_display=F.count(7),
        picks_correct=F.count(9), picks_total=F.count(14), brier=F.brier(0.21),
        brier_elo=F.brier(0.22), points_mae=F.points_error(7.1),
        watchlist_hits=F.count(13), watchlist_total=F.count(19), watchlist_inside=F.count(15),
        watchlist_by_side=[SideTally(side="offense", hits=F.count(7), total=F.count(10)),
                           SideTally(side="defense", hits=F.count(6), total=F.count(9))],
        watch_lookback=lb,
    )  # fmt: skip
    out = R.report_card_numbers(rc)
    assert (
        "watch list 13 of 19 above baseline, 15 inside their range (offense 7 of 10, defense 6 "
        "of 9)" in out
    )
    assert (
        "| Side | Player | Stat | Projected | Range | Actual | In range | Above baseline |" in out
    )
    assert (
        "| offense | Puka Nacua (Rams WR) | receiving yards | 85 receiving yards | 60–135 "
        "receiving yards | 97 receiving yards | ✓ | ✓ |" in out
    )
    assert (
        "| defense | Fred Warner (49ers LB) | tackles | 6.2 tackles | 3–9 tackles | did not play "
        "| – | – |" in out
    )
    assert "  - " not in out  # no per-pick bullets any more
    fx = build_fact_index(make_payload(report_card=rc))
    assert {"7", "10", "6", "9"} <= fx.entities["model"].atoms


# ---- scoring saved picks and the look-back ---------------------------------------------------


def history(season: int) -> pl.DataFrame:
    """Player history for 2025 week 8: p1 played (97 yards), p2 didn't play, p3 played."""
    return pl.DataFrame(
        {
            "player_id": ["p1", "p2", "p3", "zz"],
            "game_id": ["2025_08_SF_LA", "2025_08_CHI_BAL", "2025_08_LA_SF", "2025_08_CHI_BAL"],
            "pgroup": ["WR", "WR", "LB", "WR"],
            "main_qb": [False] * 4,
            "played_off": [True, False, False, True],
            "played_def": [False, False, True, False],
            "receiving_yards": [97.0, 0.0, 0.0, 40.0],
            "tackles": [3.0, 0.0, 4.0, 0.0],
        }
    )


def test_score_watch_rows_handles_model_and_heuristic_picks():
    model = model_watch(week_preds(), TUESDAY).frame.filter(
        pl.col("player_id").is_in(["p1", "p2", "p3"])
    )
    heur = pl.DataFrame({"player_id": ["h1"], "player": ["Jaylen Warren"], "team": ["PIT"],
                         "baseline": [50.0], "source": ["heuristic"],
                         "target": ["scrimmage yards"], "rank": [9]})  # fmt: skip

    def heuristic(df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns(
            pl.lit(70.0).alias("actual"), pl.lit(True).alias("played"), pl.lit(True).alias("hit")
        )

    out = score_watch_rows(pl.concat([model, heur], how="diagonal_relaxed"), history, heuristic)
    by = {r["player_id"]: r for r in out.iter_rows(named=True)}
    assert by["p1"]["actual"] == 97.0 and by["p1"]["hit"] and by["p1"]["inside"]
    assert by["p2"]["played"] is False and by["p2"]["hit"] is None
    assert by["p3"]["actual"] == 4.0 and by["p3"]["hit"] is False  # 4 tackles vs baseline 5
    assert by["h1"]["hit"] and by["h1"]["played_game"]
    texts = {lb.player: lb.text for lb in watch_lookback(out)}
    assert texts["Puka Nacua"] == (
        "projected 85 receiving yards, range 60–135 receiving yards → actual 97 receiving "
        "yards: inside the range, above his baseline of 60 receiving yards"
    )
    assert texts["Zay Flowers"].endswith("→ did not play, so not scored")
    assert texts["Jaylen Warren"] == (
        "actual 70 scrimmage yards, above his baseline of 50 scrimmage yards"
    )
    order = [(lb.player, lb.side) for lb in watch_lookback(out)]
    assert order[:2] == [("Puka Nacua", "offense"), ("Zay Flowers", "offense")]
    assert order[-1] == ("Fred Warner", "defense")  # offense first, then defense
    from nflengine.digest.players import side_tallies

    tallies = {t.side: (t.hits.value, t.total.value) for t in side_tallies(out)}
    assert tallies == {"offense": (1, 1), "defense": (0, 1)}  # Flowers didn't play


def test_an_unpublished_pressure_count_is_not_scored():
    r = {"source": "model", "p10": 0.0, "p50": 2.0, "p90": 4.0, "baseline": 1.5, "played": False,
         "played_game": True, "target_label": "pressures", "unit": "count"}  # fmt: skip
    assert lookback_text(r) == (
        "projected 2 pressures, range 0–4 pressures → no result yet (the stat isn't "
        "published), so not scored"
    )


# ---- scoreboard highlights -----------------------------------------------------------------------


def sb_rows(season: int, weeks: list[int], mode: str, specs: dict[str, tuple]) -> pl.DataFrame:
    """specs: target -> (group, label, mae_model, mae_baseline, coverage); 30 scored a week."""
    rows = [
        {"season": season, "week": w, "target": t, "position_group": g, "target_label": lab,
         "n_scored": 30, "n_not_played": 2, "mae_model": m, "mae_baseline": b,
         "improvement_pct": 100 * (b - m) / b, "coverage_80": c, "mode": mode}
        for w in weeks for t, (g, lab, m, b, c) in specs.items()
    ]  # fmt: skip
    return pl.DataFrame(rows)


SPECS = {
    "rec_yds": ("WR/TE", "receiving yards", 18.2, 20.0, 0.79),  # +9%
    "pressures": ("EDGE/DL", "pressures", 1.53, 1.5, 0.81),  # -2%
    "tackles": ("LB/S", "tackles", 2.0, 2.04, 0.65),  # +2%, ranges too narrow
}


def test_live_highlights_name_the_best_the_weakest_and_the_ranges():
    live = sb_rows(2026, [4, 5], "live", SPECS)
    lines = scoreboard_highlights(live, None, 2026, 6, "live")
    assert lines == [
        "receiving yards projections beat the rolling baseline by 9% so far this season "
        "(2 weeks scored)",
        "pressures: not yet better than the rolling baseline (2% worse) so far this season "
        "(2 weeks scored)",
        "tackles ranges are too narrow: 65% of results fell inside the 80% range so far this "
        "season (2 weeks scored)",
    ]
    # only weeks before the digest's week count
    assert scoreboard_highlights(live, None, 2026, 5, "live")[0].endswith("(1 week scored)")


def test_no_live_week_yet_says_so_and_labels_backtest_numbers():
    back = sb_rows(2019, [5], "backtest", SPECS).vstack(sb_rows(2025, [5], "backtest", SPECS))
    lines = scoreboard_highlights(None, back, 2026, 5, "live")
    assert lines[0] == "no live week of player projections has been scored yet"
    assert len(lines) == 3
    assert all(line.endswith("in walk-forward backtests (2019–2025)") for line in lines[1:])
    assert scoreboard_highlights(None, None, 2026, 5, "live") == [lines[0]]
    # a live row of another season, or of the digest week itself, is not "so far"
    live = sb_rows(2026, [5], "live", SPECS)
    assert scoreboard_highlights(live, back, 2026, 5, "live")[0] == lines[0]


def test_backtest_digest_uses_its_seasons_rows_then_earlier_seasons():
    back = sb_rows(2024, [5], "backtest", SPECS).vstack(sb_rows(2025, [3, 4], "backtest", SPECS))
    lines = scoreboard_highlights(None, back, 2025, 5, "backtest")
    assert lines[0].endswith("so far this season (2 weeks scored)")
    early = scoreboard_highlights(None, back, 2025, 2, "backtest")
    assert early[0] == "no week of player projections has been scored yet this season"
    assert early[1].endswith("in earlier backtest seasons (2024)")


def test_a_single_positive_target_still_gets_a_weak_spot_line_or_coverage():
    one = sb_rows(2026, [4], "live", {"rec_yds": SPECS["rec_yds"]})
    lines = scoreboard_highlights(one, None, 2026, 5, "live")
    assert lines[0].startswith("receiving yards projections beat") and len(lines) == 2
    assert lines[1].startswith("the 80% ranges held 79% of results")
    thin = one.with_columns(pl.lit(5).alias("n_scored"))  # too few to quote
    assert scoreboard_highlights(thin, None, 2026, 5, "live")[0].startswith("no live week")


def test_shared_labels_name_their_group_and_week_improvement():
    specs = {"receptions": ("RB", "receptions", 1.0, 1.1, 0.8)}
    lines = scoreboard_highlights(sb_rows(2026, [4], "live", specs), None, 2026, 5, "live")
    assert lines[0].startswith("receptions (RB) projections beat")
    live = sb_rows(2026, [4], "live", SPECS)
    expected = sum(100 * (b - m) / b for _, _, m, b, _ in SPECS.values()) / 3
    assert week_improvement(live, 2026, 4) == pytest.approx(expected)
    assert week_improvement(live, 2026, 3) is None and week_improvement(None, 2026, 4) is None


# ---- report card end to end ----------------------------------------------------------------------


def test_report_card_grades_model_picks_and_fills_the_scorecard(tmp_path: Path):
    wk = tmp_path / "2025" / "week07"
    wk.mkdir(parents=True)
    pl.DataFrame(
        {"game_id": ["g1"], "season": [2025], "week": [7], "home_team": ["KC"],
         "away_team": ["BUF"], "is_primary": [True], "home_win_prob": [0.6],
         "elo_prob": [0.55], "market_prob": [0.58], "pred_home_points": [24.0],
         "pred_away_points": [20.0]}
    ).write_parquet(wk / PRED_FILE)  # fmt: skip
    games = pl.DataFrame(
        {"game_id": ["g1"], "home_score": [27], "away_score": [17], "result": [10]}
    )
    picks = model_watch(week_preds().with_columns(pl.lit(7).cast(pl.Int32).alias("week")), TUESDAY)
    picks.frame.write_parquet(wk / WATCH_FILE)

    def scorer(df: pl.DataFrame) -> pl.DataFrame:
        return score_watch_rows(df, history)

    live = sb_rows(2025, [7], "live", SPECS)
    res = build_report_card(
        tmp_path, 2025, 8, games, score_watch=scorer, scoreboard=live, mode="live"
    )
    c = res.card
    assert (c.watchlist_hits.value, c.watchlist_total.value, c.watchlist_inside.value) == (
        1,
        2,
        1,  # Warner's 4 tackles fell below his range
    )
    assert [lb.player for lb in c.watch_lookback][0] == "Puka Nacua"
    assert c.scoreboard_highlights[0].startswith("receiving yards projections beat")
    assert res.scorecard_row["player_mae_vs_baseline"] == pytest.approx(
        week_improvement(live, 2025, 7)
    )
    # numbers in the look-back belong to their player in the fact index
    fx = build_fact_index(make_payload(report_card=c))
    assert "97" in fx.entities["player:p1"].atoms
    assert "9%" in fx.entities["model"].atoms


# ---- backtest materialization --------------------------------------------------------------------


def test_backtest_materializes_projections_and_earlier_watch_lists(tmp_path: Path):
    from nflengine.digest.run import make_context, materialize_backtest_player_predictions
    from nflengine.paths import DataPaths

    SIZES3 = WatchSizes(offense=2, defense=1)  # noqa: N806

    paths = DataPaths(tmp_path)
    kick = {
        6: dt.datetime(2025, 10, 12, 17, tzinfo=dt.UTC),
        7: dt.datetime(2025, 10, 19, 17, tzinfo=dt.UTC),
    }
    games = pl.DataFrame(
        {"season": [2025, 2025], "week": [6, 7], "kickoff_utc": [kick[6], kick[7]]}
    )
    rows = []
    for w in (6, 7):
        tue = dt.datetime(2025, 10, 7 if w == 6 else 14, 14, tzinfo=dt.UTC)
        for r in week_preds().iter_rows(named=True):
            gid = f"{r['game_id']}_{w}"
            rows.append({**r, "week": w, "kickoff_utc": kick[w], "created_at": tue,
                         "game_id": gid, "actual": 99.0, "played": True})  # fmt: skip
    allp = frame(rows)
    for key in ("rec_yds-wrte", "tackles-lbs"):
        d = paths.runs / "backtests" / "player" / key
        d.mkdir(parents=True)
        t = key.split("-")[0]
        allp.filter(pl.col("target") == t).write_parquet(d / PLAYER_PRED_FILE)
    ctx = make_context(2025, 7, "backtest", kick[7] - dt.timedelta(days=5), paths)
    written = materialize_backtest_player_predictions(ctx, games, sizes=SIZES3)
    assert [p.parent.name for p in written] == ["week06", "week07"]
    now = pl.read_parquet(ctx.run_dir / PLAYER_PRED_FILE)
    assert now.height == 5 and now["actual"].null_count() == 5  # outcomes blanked
    saved = pl.read_parquet(ctx.run_root / "2025" / "week06" / WATCH_FILE)
    assert saved.height == 3 and set(saved["source"]) == {"model"}
    assert saved["created_at"].unique().to_list() == [dt.datetime(2025, 10, 7, 14, tzinfo=dt.UTC)]
    assert not (ctx.run_dir / WATCH_FILE).exists()  # this week's list is the digest's job
    # re-run backtests (newer files): earlier weeks are rewritten and their lists rebuilt
    import os

    old = ctx.run_root / "2025" / "week06" / PLAYER_PRED_FILE
    os.utime(old, (1_000_000, 1_000_000))
    saved.with_columns(pl.lit("stale").alias("player")).write_parquet(
        ctx.run_root / "2025" / "week06" / WATCH_FILE
    )
    os.utime(ctx.run_root / "2025" / "week06" / WATCH_FILE, (1_000_000, 1_000_000))
    again = materialize_backtest_player_predictions(ctx, games, sizes=SIZES3)
    assert [p.parent.name for p in again] == ["week06", "week07"]
    rebuilt = pl.read_parquet(ctx.run_root / "2025" / "week06" / WATCH_FILE)
    assert "stale" not in rebuilt["player"].to_list()
    assert (
        materialize_backtest_player_predictions(ctx, games, sizes=SIZES3)[0].parent.name == "week07"
    )
    # no player backtests: nothing written (the digest falls back to the heuristic)
    empty = make_context(2025, 7, "backtest", kick[7], DataPaths(tmp_path / "none"))
    assert materialize_backtest_player_predictions(empty, games) == []


# ---- contract details from the lead (counts, live-file modes) -----------------------------------


def test_count_projections_show_the_expected_value_not_the_median():
    r = proj("p3", "Fred Warner", "SF", "LA", 0.8, group="LB/S", p50=6.0, mean=6.43,
             p10=4.0, p90=9.0, outperformance=1.43)  # fmt: skip
    (w,) = build_model_watch(frame([r]))
    assert w.projection.display == "6.4 tackles"
    assert w.interval.display == "4–9 tackles"
    assert w.vs_baseline.display == "1.4 tackles above his baseline"
    text = lookback_text({**r, "source": "model", "actual": 7.0, "played": True})
    assert text.startswith("projected 6.4 tackles, range 4–9 tackles → actual 7 tackles")


def test_backtest_rows_in_the_live_season_file_are_never_quoted_as_live():
    mixed = sb_rows(2026, [1, 2, 3], "backtest", SPECS)
    assert scoreboard_highlights(mixed, None, 2026, 4, "live") == [
        "no live week of player projections has been scored yet"
    ]
    assert week_improvement(mixed, 2026, 3) is None
    assert week_improvement(mixed, 2026, 3, "backtest") is not None


# ---- 10 offense + 10 defense (D70) ---------------------------------------------------------------


def test_watch_sizes_come_from_the_config():
    sizes = WatchSizes.from_config(
        {"watchlist_offense": 10, "watchlist_defense": 10, "tough_spots": 5, "watchlist_size": 8}
    )
    assert (sizes.offense, sizes.defense, sizes.tough, sizes.heuristic) == (10, 10, 5, 8)
    assert WatchSizes.from_config(None) == WatchSizes()
    assert WatchSizes.from_config({"tough_spots": 2}).tough == 2


def test_placeholder_covers_both_sides_offense_first_within_budget():
    offense = [proj(f"o{i}", f"Off Player{i}", f"T{i}", "OPP", 2.0 - i / 10) for i in range(10)]
    defense = [proj(f"d{i}", f"Def Player{i}", f"D{i}", "OPP", 2.0 - i / 10, group="LB/S")
               for i in range(10)]  # fmt: skip
    pw = model_watch(frame([*offense, *defense]), TUESDAY)
    assert [w.side for w in pw.items] == ["offense"] * 10 + ["defense"] * 10
    p = make_payload(players_to_watch=pw.items, tough_spots=pw.tough)
    synth = run_placeholder(p, {**BUDGETS, "players_to_watch": 200})
    assert synth.final.passed, synth.final.to_dict()
    text = synth.sections["players_to_watch"]
    for name in ("Player0", "Player1"):  # the top 2 of each side always
        assert f"Off {name}" in text and f"Def {name}" in text
    assert text.index("Off Player1") < text.index("Def Player0")  # offense first
    assert "Off Player3" not in text and "Def Player3" not in text  # at most 3 per side
