"""The coaching seed builder (P08, D86): parsing Wikipedia staff templates and infoboxes
into seed rows, the scope (coordinators only), and the name check. No network."""

import polars as pl

from nflengine.graph import coaching_seed as CS
from nflengine.graph.tables_extra import read_coaching_seed

FINAL_STAFF = """
Some prose.
* Offensive coordinator – a bullet in the article body that must not be read
==Staff==
{{NFL final staff
|Year=2010
|TeamName=Minnesota Vikings
|Head Coaches=
* Head coach – [[Brad Childress]]
* Interim head coach/defensive coordinator – [[Leslie Frazier]]
|Offensive Coaches=
* Offensive coordinator – [[Darrell Bevell]]
* Quarterbacks – [[Craig Johnson (American football)|Craig Johnson]]
* Assistant offensive coordinator – Somebody Else
|Special Teams Coaches=
* Special teams coordinator – [[Brian Murphy (American football)|Brian Murphy]]<ref>a note</ref>
}}
"""

INFOBOX_ONLY = """{{Infobox NFL season
| team = Washington Redskins
| coach = [[Jay Gruden]]
| off_coach = [[Sean McVay]]
| def_coach = [[Jim Haslett]]
}}
Body.
* Offensive coordinator – prose bullet, not staff
"""

CURRENT_TEMPLATE = """;Head coach
* [[List of head coaches|Head coach]] – [[Dan Quinn (American football)|Dan Quinn]]
* [[Offensive coordinator]] – [[David Blough]]
* Offensive pass game coordinator – [[David Raih]]
* [[Defensive coordinator]] – [[Daronte Jones]]
* Assistant head coach/special teams coordinator – [[Larry Izzo]] and [[Co Person]]
"""


def test_final_staff_template_roles_and_names():
    rows, source = CS.season_rows("MIN", 2010, FINAL_STAFF)
    assert source == "staff"
    got = {(r["role"], r["coach"]) for r in rows}
    assert got == {("OC", "Darrell Bevell"), ("DC", "Leslie Frazier"), ("ST", "Brian Murphy")}
    assert {r["head_coach"] for r in rows} == {"Brad Childress"}  # before 2018: filled


def test_infobox_fallback_and_body_bullets_ignored():
    rows, source = CS.season_rows("WAS", 2014, INFOBOX_ONLY)
    assert source == "infobox"
    assert {(r["role"], r["coach"], r["head_coach"]) for r in rows} == {
        ("OC", "Sean McVay", "Jay Gruden"),
        ("DC", "Jim Haslett", "Jay Gruden"),
    }


def test_current_staff_template_and_graph_era_head_coach_blank():
    rows, source = CS.season_rows("WAS", 2026, CURRENT_TEMPLATE, staff_page=True)
    assert source == "staff"
    got = {(r["role"], r["coach"]) for r in rows}
    assert got == {
        ("OC", "David Blough"),
        ("DC", "Daronte Jones"),
        ("ST", "Larry Izzo"),
        ("ST", "Co Person"),
    }
    assert {r["head_coach"] for r in rows} == {""}  # 2018 on: the schedules name him


def test_roles_of_titles():
    assert CS.roles_of("Assistant head coach/special teams coordinator") == {"ST"}
    assert CS.roles_of("Interim head coach/defensive coordinator") == {"HC", "DC"}
    assert CS.roles_of("Co-offensive coordinator") == {"OC"}
    assert CS.roles_of("Assistant offensive coordinator") == set()
    assert CS.roles_of("Quarterbacks") == set()
    assert CS.roles_of("Run game coordinator") == set()


def test_team_names_follow_the_franchise():
    assert CS.team_name("LA", 2015) == "St. Louis Rams"
    assert CS.team_name("LAC", 2017) == "Los Angeles Chargers"
    assert CS.team_name("LV", 2019) == "Oakland Raiders"
    assert CS.team_name("WAS", 2021) == "Washington Football Team"
    assert CS.page_title("KC", 2026, 2026) == "Template:Kansas City Chiefs staff"
    assert CS.page_title("KC", 2021, 2026) == "2021 Kansas City Chiefs season"


def test_written_seed_reads_back_through_the_graph_loader(tmp_path):
    rows, _ = CS.season_rows("MIN", 2010, FINAL_STAFF)
    seed = pl.DataFrame(rows).with_columns(pl.col("season").cast(pl.Int32))
    path = CS.write_seed(seed, tmp_path / "coaching_seed.csv", built="2026-10-04", stats={})
    back = read_coaching_seed(path)
    assert back.height == 3 and set(back["role"]) == {"OC", "DC", "ST"}
    assert back["head_coach"].to_list() == ["Brad Childress"] * 3


def test_check_names_flags_spelling_splits():
    seed = pl.DataFrame(
        {
            "coach": ["Kevin OConnell", "Sean McVay"],
            "team": ["LA", "WAS"],
            "season": [2021, 2014],
            "role": ["OC", "OC"],
            "head_coach": ["", "Jay Gruden"],
        }
    )
    flagged = CS.check_names(seed, ["Kevin O'Connell", "Sean McVay", "Jay Gruden"])
    assert ("Kevin OConnell", "Kevin O'Connell") in flagged
    assert all(a != "Sean McVay" for a, _ in flagged)


def test_names_drop_notes_and_keep_suffixes():
    assert CS.names_in("[[Ken Dorsey]]; fired after Week 10") == ["Ken Dorsey"]
    assert CS.names_in("[[Alan Williams]] (resigned on September 20)") == ["Alan Williams"]
    assert CS.names_in("[[Anthony Levine Sr.]]") == ["Anthony Levine Sr."]
    assert CS.names_in("[[A One]] (until week 8), [[B Two]] (from week 9)") == ["A One", "B Two"]
    assert CS.names_in("TBD") == []


def test_footnote_marks_are_stripped():
    assert CS.names_in("[[Gregg Williams]]†") == ["Gregg Williams"]


def test_written_blank_head_coach_reads_back_as_null(tmp_path):
    rows, _ = CS.season_rows("WAS", 2026, CURRENT_TEMPLATE, staff_page=True)
    path = CS.write_seed(pl.DataFrame(rows), tmp_path / "s.csv", built="2026-10-04", stats={})
    assert '""' not in path.read_text(encoding="utf-8")
    assert read_coaching_seed(path)["head_coach"].null_count() == 4
