"""Connections without a database (AE03, D147): the person-id check, the evidence text, the
chain assembly, the people index and the fuzzy name search (`connections.py`)."""

from __future__ import annotations

import polars as pl
import pytest
from history_fakes import no_real_docker  # noqa: F401  (the safety net)
from history_mini import mini_frames, mini_snaps

from nflengine.history_graph import connections as C
from nflengine.history_graph import derive as D


class TestParsePerson:
    @pytest.mark.parametrize(
        ("text", "kind", "pid"),
        [
            ("player:00-0033873", "player", "00-0033873"),
            ("player:00-0000001", "player", "00-0000001"),
            ("coach:andy-reid", "coach", "andy-reid"),
            ("coach:bill-belichick", "coach", "bill-belichick"),
            ("coach:a", "coach", "a"),
            ("coach:9ers-coach-2", "coach", "9ers-coach-2"),  # a digit may start the slug
            ("coach:" + "a" * 64, "coach", "a" * 64),  # the longest slug
            ("  player:00-0033873  ", "player", "00-0033873"),  # surrounding blanks are trimmed
            ("coach:andy-reid\n", "coach", "andy-reid"),
        ],
    )
    def test_good_ids(self, text, kind, pid):
        ref = C.parse_person(text)
        assert (ref.kind, ref.id) == (kind, pid)
        assert ref.text() == f"{kind}:{pid}"

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            None,
            "00-0033873",  # no kind
            "andy-reid",
            "player:",
            "coach:",
            "player:00-003387",  # six digits
            "player:00-00338731",  # eight digits
            "player:00-003387a",
            "player:01-0033873",  # not a GSIS prefix
            "player:0-0033873",
            "player:00_0033873",
            "player:00-0033873-1",
            "Player:00-0033873",  # case matters
            "PLAYER:00-0033873",
            "team:KC",
            "game:2024_01_LV_KC",
            "coach:Andy-Reid",  # slugs are lower case
            "coach:andy reid",
            "coach:-andy",  # a slug starts with a letter or a digit
            "coach:andy_reid",
            "coach:" + "a" * 65,  # one too long
            "player:00-0033873:coach:andy-reid",
            "player:00-0033873 coach:andy-reid",
            "player:00-0033873;",
        ],
    )
    def test_anything_else_is_refused_before_any_query(self, text):
        with pytest.raises(C.ConnectionError_) as e:
            C.parse_person(text)
        assert e.value.code == "bad_id"
        assert "player:<GSIS id>" in str(e.value)

    @pytest.mark.parametrize(
        "text",
        [
            "coach:x'}) DETACH DELETE (n) //",
            "coach:x' OR 1=1 //",
            "player:00-0033873' RETURN 1 //",
            "player:00-0033873}) MATCH (n) DETACH DELETE n //",
            "coach:andy-reid\nMATCH (n) DETACH DELETE n",
            "coach:andy-reid\r\nMATCH (n) DELETE n",
            "player:00-0033873\nMATCH (n) DELETE n",
            "coach:andy-reid`) RETURN 1 //",
            'coach:andy-reid"; DROP',
            "coach:${jndi:ldap://x}",
            "coach:$a",
            "coach:../../etc/passwd",
            "coach:andy-reid\x00",
            "player:00-0033873\x00",
        ],
    )
    def test_injection_shaped_ids_are_refused(self, text):
        with pytest.raises(C.ConnectionError_):
            C.parse_person(text)

    @pytest.mark.parametrize(
        "text",
        [
            "player:00-٠٠٣٣٨٧٣",  # Arabic-Indic digits
            "player:00-００３３８７３",  # full-width digits
        ],
    )
    def test_non_ascii_digits_are_not_a_gsis_id(self, text):
        # `\d` in a Python str pattern matches any Unicode digit: a GSIS id is ASCII
        with pytest.raises(C.ConnectionError_):
            C.parse_person(text)

    def test_the_ref_names_its_label_and_key(self):
        p, c = C.parse_person("player:00-0033873"), C.parse_person("coach:andy-reid")
        assert (p.label, p.key) == ("Player", "player_id")
        assert (c.label, c.key) == ("Coach", "coach_id")

    def test_refs_compare_by_value(self):
        assert C.parse_person("player:00-0033873") == C.parse_person(" player:00-0033873")
        assert C.parse_person("player:00-0033873") != C.parse_person("player:00-0030506")


class TestSeasonRanges:
    @pytest.mark.parametrize(
        ("seasons", "text"),
        [
            ([], ""),
            ([2019], "2019"),
            ([2018, 2019, 2020, 2023], "2018-2020, 2023"),
            ([2023, 2018, 2020, 2019], "2018-2020, 2023"),  # sorted first
            ([2019, 2019, 2020], "2019-2020"),  # duplicates collapse
            ([2018, 2020, 2022], "2018, 2020, 2022"),
            ([2018, 2019], "2018-2019"),
            (["2018", "2019"], "2018-2019"),  # numbers as text
        ],
    )
    def test_ranges(self, seasons, text):
        assert C.season_ranges(seasons) == text


class TestSpan:
    @pytest.mark.parametrize(
        ("first", "last", "text"),
        [(2018, 2025, "2018-2025"), (2018, 2018, "2018"), (2018, None, "2018"),
         (2019, 2020, "2019-2020")],
    )  # fmt: skip
    def test_a_span_of_one_season_is_one_year(self, first, last, text):
        assert C._span(first, last) == text


class TestEvidence:
    A = {"name": "Patrick Mahomes"}
    B = {"name": "Travis Kelce"}

    def test_teammates_on_one_stint(self):
        ev = C.evidence(
            "TEAMMATE",
            {"snaps": 612, "seasons": [2019], "teams": ["KC"], "stint_seasons": [2019],
             "stint_teams": ["KC"], "stint_snaps": [612]},
            self.A, self.B,
        )  # fmt: skip
        assert ev["text"] == "teammates on 2019 KC, 612 snaps together"
        assert ev["type"] == "TEAMMATE" and ev["snaps"] == 612
        assert ev["seasons"] == [2019] and ev["teams"] == ["KC"]
        assert ev["best"] == {"season": 2019, "team": "KC", "snaps": 612, "roles": None}
        assert ev["stints"] == [ev["best"]]

    def test_teammates_on_several_stints_name_the_best_and_the_total(self):
        ev = C.evidence(
            "TEAMMATE",
            {"snaps": 1962, "seasons": [2018, 2019, 2021], "teams": ["DEN", "KC"],
             "stint_seasons": [2018, 2019, 2021], "stint_teams": ["KC", "KC", "DEN"],
             "stint_snaps": [1000, 612, 350]},
            self.A, self.B,
        )  # fmt: skip
        assert ev["best"]["season"] == 2018
        assert ev["text"] == (
            "teammates on 2018 KC, 1,000 snaps together (1,962 in all: KC 2018-2019; DEN 2021)"
        )

    def test_the_best_stint_ties_go_to_the_later_season(self):
        ev = C.evidence(
            "TEAMMATE",
            {"snaps": 200, "stint_seasons": [2019, 2020], "stint_teams": ["KC", "KC"],
             "stint_snaps": [100, 100]},
            self.A, self.B,
        )  # fmt: skip
        assert ev["best"]["season"] == 2020

    def test_teammates_without_stints_or_snaps(self):
        ev = C.evidence("TEAMMATE", {}, self.A, self.B)
        assert ev["text"] == "teammates" and ev["best"] is None and ev["snaps"] is None
        assert ev["stints"] == [] and ev["seasons"] == [] and ev["teams"] == []

    def test_a_null_stint_snap_counts_as_zero_for_the_best(self):
        ev = C.evidence(
            "TEAMMATE",
            {"snaps": 50, "stint_seasons": [2019, 2020], "stint_teams": ["KC", "KC"],
             "stint_snaps": [None, 50]},
            self.A, self.B,
        )  # fmt: skip
        assert ev["best"]["season"] == 2020 and ev["stints"][0]["snaps"] is None

    def test_coached_starts_with_the_coach_and_names_the_roles(self):
        coach, player = {"name": "Andy Reid"}, {"name": "Patrick Mahomes"}
        ev = C.evidence(
            "COACHED",
            {"snaps": 1234, "seasons": [2018, 2019, 2022], "teams": ["KC"],
             "roles": ["HC"], "stint_seasons": [2018, 2019, 2022],
             "stint_teams": ["KC", "KC", "KC"], "stint_roles": ["HC", "HC/OC", "HC"],
             "stint_snaps": [500, 400, 334]},
            coach, player,
        )  # fmt: skip
        assert ev["roles"] == ["HC", "OC"]  # from the stints, sorted
        assert ev["text"] == (
            "Andy Reid coached Patrick Mahomes (head coach and offensive coordinator): "
            "KC 2018-2019, 2022, 1,234 snaps"
        )
        assert ev["best"]["season"] == 2018

    def test_coached_without_roles_or_snaps(self):
        ev = C.evidence(
            "COACHED",
            {"stint_seasons": [2020], "stint_teams": ["DEN"]},
            {"name": "A Coach"}, {"name": "A Player"},
        )  # fmt: skip
        assert ev["text"] == "A Coach coached A Player (coach): DEN 2020"
        assert ev["roles"] == []

    @pytest.mark.parametrize(
        ("role", "words"),
        [
            ("HC", "head coach"),
            ("OC", "offensive coordinator"),
            ("DC", "defensive coordinator"),
            ("ST", "special teams coordinator"),
            ("XX", "XX"),  # an unknown role is shown as it is
        ],
    )
    def test_every_role_has_words(self, role, words):
        props = {"stint_seasons": [2020], "stint_teams": ["KC"], "stint_roles": [role]}
        ev = C.evidence("COACHED", props, {"name": "C"}, {"name": "P"})
        assert f"({words})" in ev["text"]

    def test_worked_with_names_both_roles_in_the_last_stint(self):
        ev = C.evidence(
            "WORKED_WITH",
            {"snaps": 2000, "games": 34, "seasons": [2018, 2019], "teams": ["KC"],
             "stint_seasons": [2018, 2019], "stint_teams": ["KC", "KC"],
             "stint_roles": ["HC|OC", "HC|OC"]},
            {"name": "Andy Reid"}, {"name": "Eric Bieniemy"},
        )  # fmt: skip
        assert ev["text"] == (
            "on the same staff: KC 2018-2019 (Andy Reid head coach, Eric Bieniemy "
            "offensive coordinator in 2019)"
        )
        assert ev["games"] == 34 and ev["best"]["season"] == 2019 and ev["snaps"] == 2000

    def test_worked_with_on_two_teams(self):
        ev = C.evidence(
            "WORKED_WITH",
            {"stint_seasons": [2018, 2022], "stint_teams": ["KC", "DEN"],
             "stint_roles": ["DC|HC", "DC|OC"]},
            {"name": "A"}, {"name": "B"},
        )  # fmt: skip
        assert ev["text"].startswith("on the same staff: KC 2018; DEN 2022")
        assert "A defensive coordinator, B offensive coordinator in 2022" in ev["text"]

    def test_worked_with_without_roles_has_no_parenthesis(self):
        ev = C.evidence("WORKED_WITH", {"stint_seasons": [2018], "stint_teams": ["KC"]},
                        {"name": "A"}, {"name": "B"})  # fmt: skip
        assert ev["text"] == "on the same staff: KC 2018"

    def test_an_unknown_type_is_its_lower_case_name(self):
        assert C.evidence("OPPONENT", {}, self.A, self.B)["text"] == "opponent"

    def test_stints_zip_to_the_shortest_list(self):
        s = C._stints({"stint_seasons": [2018, 2019], "stint_teams": ["KC"]})
        assert s == [{"season": 2018, "team": "KC", "snaps": None, "roles": None}]

    def test_by_team_keeps_the_order_of_first_appearance(self):
        stints = [{"team": "KC", "season": 1}, {"team": "DEN", "season": 2},
                  {"team": "KC", "season": 3}]  # fmt: skip
        assert C._by_team(stints) == [("KC", [1, 3]), ("DEN", [2])]

    def test_role_words(self):
        assert C._role_words("HC/OC") == "head coach and offensive coordinator"
        assert C._role_words(None) == "" and C._role_words("") == ""
        assert C._role_words("//HC") == "head coach"


class TestAssemble:
    def people(self, *ids):
        return [{"element": f"e{i}", "kind": "player", "id": pid, "name": f"N{i}", "position": "QB"}
                for i, pid in enumerate(ids)]  # fmt: skip

    def link(self, start, **props):
        return {"type": "TEAMMATE", "start": start,
                "props": {"snaps": 100, "stint_seasons": [2020], "stint_teams": ["KC"],
                          "stint_snaps": [100], **props}}  # fmt: skip

    def test_one_hop(self):
        out = C._assemble(self.people("a", "b"), [self.link("e0")])
        assert out["hops"] == 1
        assert out["people"] == [
            {"kind": "player", "id": "a", "name": "N0", "position": "QB"},
            {"kind": "player", "id": "b", "name": "N1", "position": "QB"},
        ]  # the element ids never leave
        (ev,) = out["links"]
        assert (ev["from"], ev["to"]) == (0, 1) and ev["type"] == "TEAMMATE"
        assert out["weakest_snaps"] == 100 and out["cost"] == 0.01

    def test_two_hops_weakest_and_cost(self):
        links = [self.link("e0", snaps=400), self.link("e2", snaps=50)]
        out = C._assemble(self.people("a", "b", "c"), links)
        assert out["hops"] == 2 and [(x["from"], x["to"]) for x in out["links"]] == [(0, 1), (1, 2)]
        assert out["weakest_snaps"] == 50
        assert out["cost"] == round(1 / 400 + 1 / 50, 6) == 0.0225

    def test_a_link_without_snaps_has_no_strength(self):
        link = {"type": "TEAMMATE", "start": "e0", "props": {}}
        out = C._assemble(self.people("a", "b"), [link])
        assert out["weakest_snaps"] is None and out["cost"] is None

    def test_a_zero_snap_link_costs_one(self):
        out = C._assemble(self.people("a", "b"), [self.link("e0", snaps=0)])
        assert out["cost"] == 1.0 and out["weakest_snaps"] == 0

    def test_the_stored_direction_decides_who_starts_the_evidence(self):
        # the chain runs player -> coach, but COACHED is stored coach -> player: the evidence's
        # start is the coach (the rule: the link's own direction, not the walk's)
        people = [
            {"element": "ep", "kind": "player", "id": "00-0033873", "name": "Patrick Mahomes",
             "position": "QB"},
            {"element": "ec", "kind": "coach", "id": "andy-reid", "name": "Andy Reid",
             "position": "coach"},
        ]  # fmt: skip
        link = {"type": "COACHED", "start": "ec", "props": {
            "snaps": 900, "stint_seasons": [2019], "stint_teams": ["KC"], "stint_roles": ["HC"],
            "stint_snaps": [900]}}  # fmt: skip
        ev = C._assemble(people, [link])["links"][0]
        assert ev["text"].startswith("Andy Reid coached Patrick Mahomes (head coach)")
        # walked the other way round it reads the same
        back = C._assemble(people[::-1], [link])["links"][0]
        assert back["text"].startswith("Andy Reid coached Patrick Mahomes (head coach)")
        assert (back["from"], back["to"]) == (0, 1)  # but the hop is still in walk order

    def test_a_link_whose_start_isnt_either_end_falls_back_to_the_first(self):
        out = C._assemble(self.people("a", "b"), [self.link("zzz")])
        assert out["hops"] == 1

    def test_a_direct_link_with_no_hops(self):
        assert C._assemble(self.people("a"), [])["hops"] == 0


def people_df(rows: list[dict]) -> pl.DataFrame:
    base = {"kind": "player", "position": "WR", "team": "KC", "teams": ["KC"],
            "first_season": 2018, "last_season": 2025, "snaps": 100, "links": 1}  # fmt: skip
    return pl.DataFrame(
        [{**base, **r} for r in rows],
        schema={"kind": pl.String, "id": pl.String, "name": pl.String, "position": pl.String,
                "team": pl.String, "teams": pl.List(pl.String), "first_season": pl.Int64,
                "last_season": pl.Int64, "snaps": pl.Int64, "links": pl.Int64},
    )  # fmt: skip


PEOPLE = people_df([
    {"id": "00-0033873", "name": "Patrick Mahomes", "position": "QB", "snaps": 5000, "links": 40},
    {"id": "00-0030506", "name": "Travis Kelce", "position": "TE", "snaps": 6000, "links": 90},
    {"id": "00-0000011", "name": "Travis Etienne", "position": "RB", "snaps": 3000, "links": 30,
     "team": "JAX"},
    {"id": "00-0019596", "name": "Tom Brady", "position": "QB", "snaps": 4000, "links": 80,
     "first_season": 2018, "last_season": 2022, "team": "TB"},
    {"id": "00-0000012", "name": "Tom Brady", "position": "WR", "snaps": 300, "links": 5},
    {"id": "00-0000013", "name": "Nobody Links", "snaps": 9000, "links": 0},
    {"id": "00-0000014", "name": "Tj Hockenson", "position": "TE", "snaps": 2000, "links": 12},
    {"kind": "coach", "id": "andy-reid", "name": "Andy Reid", "position": "coach", "snaps": None,
     "links": 60, "first_season": 2018, "last_season": 2025},
    {"kind": "coach", "id": "bill-belichick", "name": "Bill Belichick", "position": "coach",
     "snaps": None, "links": 70, "team": "NE", "first_season": None, "last_season": None},
])  # fmt: skip


class TestSearchPeople:
    def ids(self, q, **kw):
        return [p["id"] for p in C.search_people(PEOPLE, q, **kw)]

    def test_an_exact_name_comes_first(self):
        hits = C.search_people(PEOPLE, "Travis Kelce")
        assert hits[0]["id"] == "player:00-0030506"
        assert hits[0] == {
            "id": "player:00-0030506", "kind": "player", "name": "Travis Kelce",
            "position": "TE", "team": "KC", "seasons": "2018-2025", "links": 90,
        }  # fmt: skip

    def test_exact_beats_prefix_beats_fuzzy(self):
        people = people_df([
            {"id": "00-0000001", "name": "Brady Smith", "snaps": 9000},  # a word starts with it
            {"id": "00-0000002", "name": "Brady", "snaps": 1},  # exact
            {"id": "00-0000003", "name": "Bradley", "snaps": 9000},  # fuzzy only
        ])  # fmt: skip
        hits = C.search_people(people, "Brady")
        assert [h["name"] for h in hits[:2]] == ["Brady", "Brady Smith"]

    def test_a_prefix_of_any_word_matches(self):
        assert "player:00-0030506" in self.ids("kelce")  # the last name
        assert "player:00-0030506" in self.ids("trav")  # the first
        assert {"player:00-0030506", "player:00-0000011"} <= set(self.ids("travis"))

    def test_ties_go_to_the_player_with_more_snaps(self):
        ids = self.ids("Tom Brady")
        assert ids[:2] == ["player:00-0019596", "player:00-0000012"]  # 4,000 snaps before 300
        # and between the Travises (a prefix tie): Kelce's 6,000 before Etienne's 3,000
        assert self.ids("travis")[:2] == ["player:00-0030506", "player:00-0000011"]

    def test_a_tie_on_snaps_goes_to_the_name(self):
        people = people_df([
            {"id": "00-0000002", "name": "Zed Smith", "snaps": None},
            {"id": "00-0000001", "name": "Abe Smith", "snaps": None},
        ])  # fmt: skip
        assert [h["name"] for h in C.search_people(people, "smith")] == ["Abe Smith", "Zed Smith"]

    def test_people_without_links_are_never_offered(self):
        assert self.ids("Nobody Links") == []
        assert "player:00-0000013" not in self.ids("nobody")

    def test_a_close_misspelling_is_found_by_the_fuzzy_match(self):
        assert self.ids("Patrik Mahomes")[0] == "player:00-0033873"  # WRatio 97
        assert self.ids("Travs Kelce")[0] == "player:00-0030506"
        assert self.ids("Belichik")[0] == "coach:bill-belichick"  # WRatio 80 >= 75

    def test_below_75_nothing_comes_back(self):
        assert self.ids("xyzzy") == []
        assert self.ids("brdy") == []  # WRatio 68
        assert self.ids("kelsy") == []  # WRatio 64

    def test_a_one_character_query_returns_nothing(self):
        assert C.search_people(PEOPLE, "t") == [] and C.search_people(PEOPLE, "T") == []
        assert C.search_people(PEOPLE, "") == [] and C.search_people(PEOPLE, "  ") == []
        assert C.search_people(PEOPLE, " t ") == []  # blanks don't count
        assert C.search_people(PEOPLE, "!!") == []  # nothing left after cleaning
        assert C.search_people(PEOPLE, None) == []

    def test_a_two_character_query_is_searched(self):
        assert "player:00-0019596" in self.ids("to")  # "tom" starts with it

    def test_at_most_limit_come_back(self):
        assert len(C.search_people(PEOPLE, "travis", limit=1)) == 1
        assert len(C.search_people(PEOPLE, "tom brady", limit=1)) == 1
        assert len(C.search_people(PEOPLE, "travis", limit=10)) == 2
        assert C.search_people(PEOPLE, "travis", limit=0) == []

    def test_punctuation_hyphens_and_case_are_ignored(self):
        assert self.ids("TRAVIS KELCE")[0] == "player:00-0030506"
        assert self.ids("t.j. hockenson")[0] == "player:00-0000014"  # "tj hockenson"
        assert self.ids("Andy-Reid")[0] == "coach:andy-reid"
        assert self.ids("bill belichick!")[0] == "coach:bill-belichick"

    def test_coaches_are_people_too(self):
        hit = C.search_people(PEOPLE, "Andy Reid")[0]
        assert hit["kind"] == "coach" and hit["id"] == "coach:andy-reid"
        assert hit["position"] == "coach" and hit["seasons"] == "2018-2025"

    def test_a_single_season_is_one_year_not_a_range(self):
        people = people_df([{"id": "00-0000021", "name": "Rookie Only", "first_season": 2025,
                             "last_season": 2025}])  # fmt: skip
        assert C.search_people(people, "Rookie Only")[0]["seasons"] == "2025"

    def test_no_seasons_gives_none(self):
        hit = C.search_people(PEOPLE, "Bill Belichick")[0]
        assert hit["seasons"] is None and hit["team"] == "NE"

    def test_only_the_documented_fields_come_back(self):
        for hit in C.search_people(PEOPLE, "travis"):
            assert set(hit) == {"id", "kind", "name", "position", "team", "seasons", "links"}

    def test_an_empty_index_has_no_one(self):
        assert C.search_people(PEOPLE.head(0), "travis") == []

    def test_norm(self):
        assert C._norm("T.J. Hockenson") == "tj hockenson"
        assert C._norm("Andy-Reid") == "andy reid"
        assert C._norm(None) == "" and C._norm("  X  ") == "x"


@pytest.fixture(scope="module")
def people():
    fr = mini_frames()
    snaps = mini_snaps()
    derived, _ = D.derived_frames(fr, snaps)
    return C.people_frame(fr, snaps, derived)


class TestPeopleFrame:
    def row(self, people, pid):
        hit = people.filter(pl.col("id") == pid)
        assert hit.height == 1, pid
        return hit.to_dicts()[0]

    def test_one_row_per_person_in_the_graph_sorted(self, people):
        assert people.columns == ["kind", "id", "name", "position", "team", "teams",
                                  "first_season", "last_season", "snaps", "links"]  # fmt: skip
        assert people.height == 16 + 5  # the Player nodes and the Coach nodes (not zz, ghost)
        assert people.select("kind", "id").equals(people.select("kind", "id").sort("kind", "id"))
        assert set(people["kind"]) == {"coach", "player"}
        assert "zz" not in people["id"].to_list() and "ghost" not in people["id"].to_list()

    def test_links_count_the_person_links_only(self, people):
        # o1: three TEAMMATE links; his CONNECTIONs (QB -> o2, o3) and OPPONENTs don't count
        assert self.row(people, "o1")["links"] == 3
        assert self.row(people, "d1")["links"] == 2  # (d1,d2) and (d1,d3)
        assert self.row(people, "o4")["links"] == 3  # o1, o2 and o3
        # coaches: hc coached q1..q4 (4) and worked with dc, oc, stc (3)
        assert self.row(people, "hc")["links"] == 7
        assert self.row(people, "oc")["links"] == 4  # q1, and dc, hc, stc
        assert self.row(people, "both")["links"] == 2  # n1, n2; alone on his staff
        assert self.row(people, "n1")["links"] == 1 and self.row(people, "q5")["links"] == 0

    def test_a_person_with_no_links_has_zero_not_null(self, people):
        assert people["links"].null_count() == 0
        assert self.row(people, "q5")["links"] == 0

    def test_players_take_their_team_and_snaps_from_the_snap_counts(self, people):
        q1 = self.row(people, "q1")
        # q1: KC 2024 (60 + 50 + 40), KC 2025 (30) and 5 snaps "for XXX" in 2024, a team the
        # graph doesn't hold (left out, like the derive's links): KC, 180 snaps in all
        assert (q1["team"], q1["first_season"], q1["last_season"], q1["snaps"]) == (
            "KC", 2024, 2025, 180)  # fmt: skip
        assert "XXX" not in q1["teams"]
        assert q1["position"] == "QB" and q1["name"] == "Player Q1"
        o1 = self.row(people, "o1")  # no snap rows
        assert (o1["team"], o1["snaps"], o1["first_season"]) == (None, None, None)

    def test_the_team_is_the_latest_seasons_biggest(self):
        fr = mini_frames()
        snaps = pl.DataFrame(
            [("q1", "KC", 2023, 1, 500, 0, 0), ("q1", "DEN", 2024, 1, 30, 0, 0),
             ("q1", "KC", 2024, 2, 10, 0, 0)],
            schema=mini_snaps().schema, orient="row",
        )  # fmt: skip
        empty = pl.DataFrame(schema={"s": pl.String, "e": pl.String})
        out = C.people_frame(
            fr, snaps, dict.fromkeys(("TEAMMATE", "COACHED", "WORKED_WITH"), empty)
        )
        q1 = out.filter(pl.col("id") == "q1").to_dicts()[0]
        assert q1["team"] == "DEN"  # the latest season's team with the most snaps
        assert q1["teams"] == ["KC", "DEN"]
        assert q1["first_season"] == 2023 and q1["last_season"] == 2024 and q1["snaps"] == 540

    def test_coaches_have_a_team_and_seasons_from_their_staff_rows(self, people):
        oc = self.row(people, "oc")
        assert oc["kind"] == "coach" and oc["position"] == "coach" and oc["name"] == "Coach OC"
        assert (oc["team"], oc["teams"], oc["first_season"], oc["last_season"]) == (
            "KC", ["KC"], 2024, 2025)  # fmt: skip
        assert oc["snaps"] is None
        assert self.row(people, "both")["team"] == "DEN"

    def test_it_is_what_the_search_reads(self, people):
        hits = C.search_people(people, "Coach HC")
        assert hits and hits[0]["id"] == "coach:hc" and hits[0]["links"] == 7
        assert all(h["id"] != "player:q5" for h in C.search_people(people, "Player Q5"))
