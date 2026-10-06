"""The sidebar's week list (`/api/weeks`) and `/api/meta` on fixture run folders."""

from __future__ import annotations

import pytest
from app_helpers import (
    ALL_OK,
    TUESDAY,
    final_through_week4,
    make_client,
    make_paths,
    write_week,
)

from nflengine.ops.lock import lock_path, run_lock


def weeks_by_number(client) -> dict[int, dict]:
    data = client.get("/api/weeks").json()
    return {w["week"]: w for w in data["weeks"]}


def test_published_week_and_the_current_week(tmp_path):
    paths = make_paths(tmp_path)
    write_week(paths, 2026, 4, ALL_OK, report=True, checks={"passed": True, "regenerated": True})
    client = make_client(tmp_path)
    data = client.get("/api/weeks").json()
    assert data["season"] == 2026 and data["current_week"] == 5 and data["go_live_week"] == 4
    assert [w["week"] for w in data["weeks"]] == [5, 4]  # newest first, current week included
    w4, w5 = data["weeks"][1], data["weeks"][0]
    assert (w4["status"], w4["label"], w4["detail"]) == (
        "published",
        "Published",
        "Checks passed after a rewrite",
    )
    assert w4["published_at"].startswith("2026-10-04T05:02:13")
    assert w4["published_at"][19:] != ""  # naive local time gets an offset
    assert w4["has_run_summary"] is False  # week 4 ran before run records (P07)
    # the fixture's schedule is as of 2026-10-04: week 4 isn't final yet
    assert (w5["status"], w5["label"], w5["is_current"]) == ("waiting", "Waiting on week 4", True)


def test_current_week_ready_when_last_week_is_final(tmp_path):
    client = make_client(tmp_path, sched=final_through_week4())
    w5 = weeks_by_number(client)[5]
    assert (w5["status"], w5["label"]) == ("ready", "Ready to run")
    assert w5["detail"] == "Week 4 is final (16/16)"


def test_report_file_needed_for_published(tmp_path):
    paths = make_paths(tmp_path)
    write_week(paths, 2026, 4, ALL_OK, report=False)
    assert weeks_by_number(make_client(tmp_path))[4]["status"] == "partial"


def test_failed_partial_and_not_ready_weeks(tmp_path):
    paths = make_paths(tmp_path)
    write_week(paths, 2026, 2, {"ingest": "ok", "ready": "ok", "game": "ok"})
    write_week(paths, 2026, 3, {"ingest": "ok", "ready": "ok", "curate": "failed"})
    write_week(paths, 2026, 5, {"ready": "failed"}, summary={"status": "not_ready"})
    weeks = weeks_by_number(make_client(tmp_path))
    assert (weeks[2]["status"], weeks[2]["label"]) == ("partial", "Incomplete")
    assert (weeks[3]["status"], weeks[3]["detail"]) == ("failed", "curate failed")
    assert weeks[5]["status"] == "waiting"  # a not-ready run isn't a failure
    assert weeks[5]["has_run_summary"] is True


def test_on_time_comes_from_the_run_summary(tmp_path):
    paths = make_paths(tmp_path)
    write_week(paths, 2026, 4, ALL_OK, report=True, summary={"status": "ok", "on_time": True})
    assert weeks_by_number(make_client(tmp_path))[4]["on_time"] is True


@pytest.mark.parametrize(
    "command, week",
    [
        ("nfl weekly run --auto", 5),
        ("nfl weekly run --season 2026 --week 4 --from-step digest", 4),
        ("nfl weekly injury-update --season 2026 --week 4", 4),
    ],
)
def test_running_week_from_the_lock(tmp_path, command, week):
    paths = make_paths(tmp_path)
    write_week(paths, 2026, 4, ALL_OK, report=True)
    client = make_client(tmp_path)
    with run_lock(lock_path(paths.runs), command):
        weeks = weeks_by_number(client)
        meta = client.get("/api/meta").json()
    assert weeks[week]["status"] == "running"
    assert meta["lock"] == {"held": True, "command": command, "started": meta["lock"]["started"]}
    assert set(meta["lock"]) == {"held", "command", "started"}  # no pid / host


def test_meta_has_the_calendar_and_status_line(tmp_path):
    meta = make_client(tmp_path).get("/api/meta").json()
    cal = meta["calendar"]
    assert (cal["season"], cal["week"], cal["previous_week"], cal["games"]) == (2026, 5, 4, 15)
    assert cal["deadline"] == "2026-10-09T00:15:00+00:00"  # TB@DAL, Thu 8:15 PM ET
    assert "thursday" in cal["special"] and cal["byes"] == ["CAR", "KC"]
    assert meta["lock"]["held"] is False
    assert meta["neo4j"]["status"] == "down"
    assert meta["mode"] == {"dev": False, "rehearsal": False}
    assert meta["data_root"]["found"] is True


def test_no_schedule_snapshot_yet(tmp_path):
    from nflengine.app.server import AppSettings, create_app

    paths = make_paths(tmp_path)

    def missing(_p):
        raise FileNotFoundError("no schedules snapshot yet")

    from app_helpers import BASE, FakeStatus
    from fastapi.testclient import TestClient

    settings = AppSettings(
        data_root=lambda: paths,
        schedules=missing,
        current_season=lambda: 2026,
        services=FakeStatus(),
    )
    client = TestClient(create_app(settings), base_url=BASE)
    assert client.get("/api/meta").json()["calendar"] is None
    data = client.get("/api/weeks").json()
    assert (data["season"], data["current_week"], data["weeks"]) == (2026, None, [])


@pytest.mark.integration
def test_real_data_root_lists_week4_published():
    from app_helpers import BASE, FakeStatus
    from fastapi.testclient import TestClient

    from nflengine.app.server import AppSettings, create_app

    client = TestClient(create_app(AppSettings(services=FakeStatus())), base_url=BASE)
    weeks = {w["week"]: w for w in client.get("/api/weeks").json()["weeks"]}
    assert weeks[4]["status"] == "published"


def test_a_real_ready_failure_is_a_failure(tmp_path):
    """Sol review (CR00): a failed `ready` step can be a real failure (exit 1), not only
    "not ready" (exit 3). With a run summary its status decides; without one, the detail."""
    paths = make_paths(tmp_path)
    write_week(paths, 2026, 3, {"ingest": "ok", "ready": "failed"}, summary={"status": "failed"})
    write_week(
        paths,
        2026,
        2,
        {"ingest": "ok", "ready": "failed"},
        details={"ready": "ArrowInvalid: corrupt play-by-play file"},
    )
    write_week(
        paths,
        2026,
        1,
        {"ingest": "ok", "ready": "failed"},
        details={"ready": "2026 week 0: NOT READY (15/16 games final)"},
    )
    weeks = weeks_by_number(make_client(tmp_path))
    assert (weeks[3]["status"], weeks[3]["detail"]) == ("failed", "ready failed")
    assert weeks[2]["status"] == "failed"
    assert weeks[1]["status"] == "partial"  # a not-ready run, before run summaries existed


def test_current_week_without_a_slate_waits_on_the_schedule(tmp_path):
    """Sol review (CR00): after week 18 the next round may not be in the schedule yet;
    `run_pipeline` stops with exit 3 then, so the sidebar must not say "Ready"."""
    import dataclasses

    from nflengine.app.readers.weeks import week_entry
    from nflengine.ops.calendar import plan_week

    paths = make_paths(tmp_path)
    plan = plan_week(final_through_week4(), TUESDAY)
    no_slate = dataclasses.replace(plan, slate=None)
    lock = {"held": False, "command": None, "started": None}
    e = week_entry(paths, 2026, 5, no_slate, lock)
    assert (e["status"], e["label"]) == ("waiting", "Waiting on the schedule")
    assert "aren't in the schedule yet" in e["detail"]


def test_a_simulation_holds_the_lock_but_not_the_live_week(tmp_path):
    """Sol review (CR00): `--auto --as-of <past>` is a time-travel run under the same lock."""
    paths = make_paths(tmp_path)
    client = make_client(tmp_path)
    with run_lock(lock_path(paths.runs), "nfl weekly run --auto --as-of 2025-11-04T10:00:00"):
        weeks = weeks_by_number(client)
        meta = client.get("/api/meta").json()
    assert weeks[5]["status"] != "running"
    assert meta["lock"]["held"] is True
