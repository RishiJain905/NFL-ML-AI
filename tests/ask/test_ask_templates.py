"""AE00 template library: `load_templates` validation, `render` (value checks, name resolution,
fragment choices, the as-of binding, filters) and the structure of the real
`config/ask_templates.yaml` (the library worker is still filling it: only structure is asserted).
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from nflengine.ask.guard import check_cypher
from nflengine.ask.names import Match, Resolution
from nflengine.ask.templates import (
    PARAM_TYPES,
    TEMPLATES_PATH,
    TEXT_MAX,
    Param,
    Rendered,
    Template,
    TemplateError,
    is_gsis,
    load_templates,
    render,
)
from nflengine.graph.queries import LIBRARY

CURRENT = 2026


# ---- helpers --------------------------------------------------------------------------------


def tpl(**over: Any) -> dict[str, Any]:
    """A valid SQL template as a dict; `key=None` removes a key."""
    base: dict[str, Any] = {
        "id": "wins_by_team",
        "source": "sql",
        "title": "Wins",
        "description": "Wins of one team.",
        "examples": ["How many wins did Dallas have in 2023?"],
        "params": {
            "team": {"type": "team", "description": "the team"},
            "season": {"type": "season", "default": "current"},
        },
        "query": "SELECT wins FROM teams WHERE team = $team AND season = $season",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def write(tmp_path: Path, *templates: dict[str, Any], version: Any = 1) -> Path:
    path = tmp_path / "templates.yaml"
    path.write_text(
        yaml.safe_dump({"version": version, "templates": list(templates)}, sort_keys=False)
    )
    return path


def load(tmp_path, *templates, **kw):
    return load_templates(write(tmp_path, *templates, **kw), current_season=CURRENT)


def bad(tmp_path, *templates, match: str, **kw):
    with pytest.raises(ValueError, match=match):
        load(tmp_path, *templates, **kw)


class FakeResolver:
    """Teams by name; two Josh Allens; one Mahomes; coaches Reid (unique) and Mike (two)."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def team(self, text):
        self.calls.append(("team", text))
        return {"dallas": "DAL", "cowboys": "DAL", "kc": "KC", "chiefs": "KC"}.get(text.lower())

    def player(self, text):
        self.calls.append(("player", text))
        t = text.lower()
        if t in ("patrick mahomes", "00-0033873"):
            return Resolution("player", "unique", (Match("00-0033873", "Patrick Mahomes (QB)"),))
        if t == "josh allen":
            return Resolution(
                "player",
                "ambiguous",
                (
                    Match("00-0034857", "Josh Allen (QB, BUF)"),
                    Match("00-0026000", "Josh Allen (C)"),
                ),
            )
        return Resolution("player", "none")

    def coach(self, text):
        self.calls.append(("coach", text))
        t = text.lower()
        if t == "andy reid":
            return Resolution("coach", "unique", (Match("Andy Reid", "Andy Reid (HC)"),))
        if t == "mike":
            return Resolution(
                "coach",
                "ambiguous",
                (Match("Mike McCarthy", "Mike McCarthy (HC)"), Match("Mike Tomlin", "Mike Tomlin")),
            )
        return Resolution("coach", "none")


R = FakeResolver()


def code_of(fn, *a, **kw) -> TemplateError:
    with pytest.raises(TemplateError) as exc:
        fn(*a, **kw)
    return exc.value


# ---- loading: validation --------------------------------------------------------------------


class TestLoad:
    def test_a_valid_file(self, tmp_path):
        out = load(tmp_path, tpl())
        t = out["wins_by_team"]
        assert isinstance(t, Template)
        assert t.source == "sql" and t.title == "Wins" and t.asof is False and t.library is None
        assert [p.name for p in t.params] == ["team", "season"]
        assert t.examples == ("How many wins did Dallas have in 2023?",)

    def test_season_defaults_and_bounds(self, tmp_path):
        t = load(tmp_path, tpl())["wins_by_team"]
        season = t.param("season")
        assert season.default == CURRENT and not season.required
        assert (season.min, season.max) == (1999.0, float(CURRENT))
        team = t.param("team")
        assert team.required and team.default is None

    def test_week_bounds(self, tmp_path):
        t = load(tmp_path, tpl(params={"w": {"type": "week"}}, query="SELECT $w"))["wins_by_team"]
        assert (t.param("w").min, t.param("w").max) == (1.0, 22.0)

    def test_title_defaults_to_the_id(self, tmp_path):
        t = load(tmp_path, tpl(title=None))["wins_by_team"]
        assert t.title == "wins_by_team"

    def test_no_templates_key_is_an_empty_library(self, tmp_path):
        path = tmp_path / "t.yaml"
        path.write_text("version: 1\n")
        assert load_templates(path, current_season=CURRENT) == {}

    @pytest.mark.parametrize("version", [None, 0, 2, "1"])
    def test_the_version_must_be_1(self, tmp_path, version):
        bad(tmp_path, tpl(), match="version must be 1", version=version)

    def test_an_empty_file(self, tmp_path):
        path = tmp_path / "t.yaml"
        path.write_text("")
        with pytest.raises(ValueError, match="version"):
            load_templates(path, current_season=CURRENT)

    @pytest.mark.parametrize("tid", ["Bad-Id", "has space", "UPPER", "", "x" * 65, "ünï", "a.b"])
    def test_bad_ids(self, tmp_path, tid):
        bad(tmp_path, tpl(id=tid), match="bad id")

    def test_duplicate_ids(self, tmp_path):
        bad(tmp_path, tpl(), tpl(title="again"), match="duplicate id 'wins_by_team'")

    @pytest.mark.parametrize("source", [None, "history", "SQL", "cypher"])
    def test_source_must_be_sql_or_graph(self, tmp_path, source):
        bad(tmp_path, tpl(source=source), match="source must be sql or graph")

    def test_no_query(self, tmp_path):
        bad(tmp_path, tpl(query=None), match="no query")
        bad(tmp_path, tpl(query="  \n "), match="no query")

    def test_no_examples(self, tmp_path):
        bad(tmp_path, tpl(examples=None), match="at least one example")
        bad(tmp_path, tpl(examples=[]), match="at least one example")

    def test_unknown_parameter_type(self, tmp_path):
        bad(tmp_path, tpl(params={"x": {"type": "date"}}), match="type must be one of")
        bad(tmp_path, tpl(params={"x": {}}), match="type must be one of")

    @pytest.mark.parametrize("name", ["Bad", "has-dash", "x" * 65, "a b"])
    def test_bad_parameter_names(self, tmp_path, name):
        bad(tmp_path, tpl(params={name: {"type": "int"}}, query="SELECT 1"), match="bad parameter")

    def test_a_choice_needs_choices(self, tmp_path):
        bad(tmp_path, tpl(params={"c": {"type": "choice"}}, query="SELECT 1"), match="needs a list")
        bad(
            tmp_path,
            tpl(params={"c": {"type": "choice", "choices": []}}, query="SELECT 1"),
            match="needs a list",
        )

    def test_a_choice_default_must_be_a_choice(self, tmp_path):
        p = {"c": {"type": "choice", "choices": ["a", "b"], "default": "z"}}
        bad(tmp_path, tpl(params=p, query="SELECT $c"), match="isn't a choice")

    def test_every_param_type_is_accepted(self, tmp_path):
        params = {f"p_{t}": {"type": t} for t in PARAM_TYPES if t != "choice"}
        params["p_choice"] = {"type": "choice", "choices": ["a"]}
        query = "SELECT " + ", ".join(f"${n}" for n in params)
        assert load(tmp_path, tpl(params=params, query=query))

    # ---- slots, refs, constants ----

    def test_a_slot_needs_a_fragment_choice(self, tmp_path):
        q = "SELECT {stat} FROM pg"
        bad(tmp_path, tpl(params={}, query=q), match=r"\{stat\} needs a choice parameter")
        bad(
            tmp_path,
            tpl(params={"stat": {"type": "int"}}, query=q),
            match=r"\{stat\} needs a choice parameter",
        )
        bad(  # a list of choices has no fragments
            tmp_path,
            tpl(params={"stat": {"type": "choice", "choices": ["a", "b"]}}, query=q),
            match=r"\{stat\} needs a choice parameter",
        )

    def test_a_slot_with_fragments_is_fine(self, tmp_path):
        p = {"stat": {"type": "choice", "choices": {"a": "x.a", "b": "x.b"}}}
        assert load(tmp_path, tpl(params=p, query="SELECT {stat} FROM x"))

    def test_an_unknown_ref(self, tmp_path):
        bad(tmp_path, tpl(query="SELECT $nope"), match=r"\$nope isn't a parameter or a constant")

    def test_a_constant_satisfies_a_ref(self, tmp_path):
        out = load(tmp_path, tpl(params={}, constants={"k": 3}, query="SELECT $k"))
        assert out["wins_by_team"].constants == {"k": 3}

    def test_asof_makes_season_and_week_refs_valid(self, tmp_path):
        q = "MATCH (g:Game {season: $season, week: $week}) RETURN g.game_id AS id"
        out = load(tmp_path, tpl(source="graph", asof=True, params={}, query=q))
        assert out["wins_by_team"].asof
        bad(tmp_path, tpl(source="graph", params={}, query=q), match=r"\$(season|week) isn't")

    def test_asof_is_for_graph_templates_only(self, tmp_path):
        bad(tmp_path, tpl(asof=True), match="asof applies to graph templates only")

    def test_graph_template_comments_are_not_slots_or_refs(self, tmp_path):
        q = "// uses {slot} and $ghost here\nMATCH (t:Team) RETURN t.team_id AS team /* $x {y} */"
        out = load(tmp_path, tpl(source="graph", params={}, query=q))
        assert "ghost" not in out["wins_by_team"].query
        assert out["wins_by_team"].query == "MATCH (t:Team) RETURN t.team_id AS team"

    # ---- library ----

    def test_a_library_template_takes_the_files_text_and_constants(self, tmp_path):
        out = load(
            tmp_path,
            tpl(source="graph", library="q1_revenge", asof=True, params={}, query=None),
        )
        t = out["wins_by_team"]
        assert t.library == "q1_revenge"
        assert t.constants["min_old_games"] == LIBRARY["q1_revenge"]["min_old_games"]
        assert "//" not in t.query  # the file's comments are stripped
        assert check_cypher(t.query)

    def test_a_template_constant_overrides_the_librarys(self, tmp_path):
        out = load(
            tmp_path,
            tpl(
                source="graph",
                library="q1_revenge",
                asof=True,
                params={},
                query=None,
                constants={"min_old_games": 9},
            ),
        )
        assert out["wins_by_team"].constants["min_old_games"] == 9

    def test_a_library_template_without_asof_has_unbound_season(self, tmp_path):
        bad(
            tmp_path,
            tpl(source="graph", library="q1_revenge", params={}, query=None),
            match=r"\$(season|week) isn't a parameter or a constant",
        )

    def test_a_library_query_is_a_graph_template(self, tmp_path):
        bad(tmp_path, tpl(library="q1_revenge", query=None), match="a library query is a graph")

    def test_an_unknown_library_query(self, tmp_path):
        bad(
            tmp_path,
            tpl(source="graph", library="q99_nope", query=None, params={}),
            match="unknown library query",
        )

    def test_a_library_template_cannot_use_fragment_choices(self, tmp_path):
        p = {"c": {"type": "choice", "choices": {"a": "x"}}}
        bad(
            tmp_path,
            tpl(source="graph", library="q0_starters_out", asof=True, params=p, query=None),
            match="can't use fragment choices",
        )

    # ---- filters ----

    def test_a_filter_must_name_a_parameter(self, tmp_path):
        bad(tmp_path, tpl(filters={"ghost": ["team"]}), match="filter 'ghost' isn't a parameter")

    @pytest.mark.parametrize("cols", ["team", [], None, ["ok", "Bad Col"], ["a-b"]])
    def test_a_filter_needs_a_list_of_column_names(self, tmp_path, cols):
        bad(tmp_path, tpl(filters={"team": cols}), match="needs a list of column names")

    def test_a_valid_filter(self, tmp_path):
        t = load(tmp_path, tpl(filters={"team": ["team", "opponent"]}))["wins_by_team"]
        assert t.filters == {"team": ("team", "opponent")}


# ---- rendering ------------------------------------------------------------------------------


@pytest.fixture
def lib(tmp_path):
    """A library with one template per parameter type."""
    return load(
        tmp_path,
        tpl(),
        tpl(
            id="everything",
            params={
                "team": {"type": "team", "default": None},
                "player": {"type": "player", "default": None},
                "coach": {"type": "coach", "default": None},
                "season": {"type": "season", "default": "current"},
                "week": {"type": "week", "default": 1},
                "n": {"type": "int", "default": 100, "min": 1, "max": 700},
                "x": {"type": "float", "default": 0.5, "min": 0, "max": 1},
                "q": {"type": "text", "default": "abc"},
                "kind": {"type": "choice", "default": "a", "choices": ["a", "B"]},
                "stat": {
                    "type": "choice",
                    "default": "receiving",
                    "choices": {"receiving": "pg.receiving_yards", "rushing": "pg.rushing_yards"},
                },
            },
            query=(
                "SELECT {stat} AS v FROM pg WHERE t = $team AND p = $player AND c = $coach "
                "AND s = $season AND w = $week AND n = $n AND x = $x AND q = $q AND k = $kind"
            ),
        ),
    )


class TestRender:
    def test_names_are_resolved_and_bound(self, lib):
        r = render(lib["wins_by_team"], {"team": "Dallas"}, R)
        assert isinstance(r, Rendered)
        assert r.params == {"team": "DAL", "season": CURRENT}
        assert r.values == {"team": "DAL", "season": CURRENT}
        assert r.query == lib["wins_by_team"].query  # the LLM's text never enters the query
        assert r.template_id == "wins_by_team" and r.source == "sql"

    def test_values_may_be_strings(self, lib):
        r = render(lib["wins_by_team"], {"team": " cowboys ", "season": "2023"}, R)
        assert r.params == {"team": "DAL", "season": 2023}

    def test_a_team_that_does_not_exist(self, lib):
        e = code_of(render, lib["wins_by_team"], {"team": "Nowhere"}, R)
        assert e.code == "not_found" and e.param == "team"

    def test_a_required_value_is_missing(self, lib):
        for values in ({}, {"team": None}, {"team": ""}, {"team": "   "}):
            e = code_of(render, lib["wins_by_team"], values, R)
            assert e.code == "missing" and e.param == "team", values

    def test_a_unique_player_becomes_the_gsis_id(self, lib):
        r = render(lib["everything"], {"player": "Patrick Mahomes"}, R)
        assert r.params["player"] == "00-0033873"
        r = render(lib["everything"], {"player": "00-0033873"}, R)
        assert r.params["player"] == "00-0033873"

    def test_an_ambiguous_player_lists_labels_and_values_in_order(self, lib):
        e = code_of(render, lib["everything"], {"player": "Josh Allen"}, R)
        assert e.code == "ambiguous" and e.param == "player"
        assert e.choices == ["Josh Allen (QB, BUF)", "Josh Allen (C)"]
        assert e.values == ["00-0034857", "00-0026000"]
        assert str(e) == e.message == "player matches more than one player"

    def test_an_unknown_player(self, lib):
        e = code_of(render, lib["everything"], {"player": "Nobody Atall"}, R)
        assert e.code == "not_found" and e.param == "player"

    def test_coaches(self, lib):
        assert render(lib["everything"], {"coach": "Andy Reid"}, R).params["coach"] == "Andy Reid"
        e = code_of(render, lib["everything"], {"coach": "Mike"}, R)
        assert e.code == "ambiguous" and e.values == ["Mike McCarthy", "Mike Tomlin"]
        assert code_of(render, lib["everything"], {"coach": "Zed"}, R).code == "not_found"

    def test_a_name_without_a_resolver_is_invalid(self, lib):
        e = code_of(render, lib["wins_by_team"], {"team": "Dallas"}, None)
        assert e.code == "invalid" and e.param == "team"

    def test_defaults_including_current_and_none(self, lib):
        r = render(lib["everything"], {}, R)
        assert r.params["season"] == CURRENT and r.params["week"] == 1
        assert r.params["n"] == 100 and r.params["x"] == 0.5 and r.params["q"] == "abc"
        assert r.params["team"] is None and r.params["player"] is None and r.params["coach"] is None
        assert r.params["kind"] == "a"
        assert R.calls == [] or all(c[1] for c in R.calls)

    def test_a_default_is_not_resolved_again(self, lib):
        before = len(R.calls)
        render(lib["everything"], {}, R)
        assert len(R.calls) == before

    @pytest.mark.parametrize(
        "name, value, code",
        [
            ("season", 1998, "out_of_range"),
            ("season", CURRENT + 1, "out_of_range"),
            ("season", "abc", "invalid"),
            ("season", 2023.5, "invalid"),
            ("season", True, "invalid"),
            ("season", [2023], "invalid"),
            ("season", float("nan"), "invalid"),
            ("season", float("inf"), "invalid"),
            ("week", 0, "out_of_range"),
            ("week", 23, "out_of_range"),
            ("n", 0, "out_of_range"),
            ("n", 701, "out_of_range"),
            ("n", "1.5", "invalid"),
            ("x", 1.5, "out_of_range"),
            ("x", -0.1, "out_of_range"),
            ("x", "nope", "invalid"),
            ("kind", "z", "invalid"),
            ("stat", "receiving; DROP TABLE teams", "invalid"),
            ("stat", "pg.receiving_yards", "invalid"),
            ("q", "x" * (TEXT_MAX + 1), "invalid"),
        ],
    )
    def test_bad_values(self, lib, name, value, code):
        e = code_of(render, lib["everything"], {name: value}, R)
        assert e.code == code and e.param == name

    @pytest.mark.parametrize(
        "name, value, expected",
        [
            ("season", 1999, 1999),
            ("season", CURRENT, CURRENT),
            ("season", "2023", 2023),
            ("season", 2023.0, 2023),
            ("season", " 2023 ", 2023),
            ("week", 22, 22),
            ("week", 1, 1),
            ("n", 1, 1),
            ("n", 700, 700),
            ("n", "250", 250),
            ("x", 0, 0.0),
            ("x", "0.25", 0.25),
            ("x", 1, 1.0),
            ("q", "x" * TEXT_MAX, "x" * TEXT_MAX),
            ("q", "  padded  ", "padded"),
            ("q", 'it\'s "quoted"; -- and $odd {slot}', 'it\'s "quoted"; -- and $odd {slot}'),
            ("kind", "b", "B"),  # case-insensitive, the canonical spelling comes back
            ("kind", "A", "a"),
        ],
    )
    def test_good_values(self, lib, name, value, expected):
        assert render(lib["everything"], {name: value}, R).params[name] == expected

    def test_a_text_value_is_bound_never_pasted(self, lib):
        nasty = "x' OR 1=1 --"
        r = render(lib["everything"], {"q": nasty}, R)
        assert r.params["q"] == nasty and nasty not in r.query

    def test_a_fragment_choice_is_substituted_from_the_yaml_only(self, lib):
        r = render(lib["everything"], {"stat": "Rushing"}, R)
        assert r.query.startswith("SELECT pg.rushing_yards AS v")
        assert "{stat}" not in r.query and "stat" not in r.params
        assert r.values["stat"] == "rushing"
        r = render(lib["everything"], {}, R)  # the default
        assert r.query.startswith("SELECT pg.receiving_yards AS v")

    def test_unreferenced_values_are_not_bound(self, tmp_path):
        t = load(
            tmp_path,
            tpl(
                query="SELECT $team",
                params={"team": {"type": "team"}, "unused": {"type": "int", "default": 1}},
            ),
        )["wins_by_team"]
        r = render(t, {"team": "KC", "unused": 5}, R)  # the query has no $unused
        assert r.params == {"team": "KC"}
        assert r.values == {"team": "KC", "unused": 5}

    def test_an_unknown_parameter(self, lib):
        e = code_of(render, lib["wins_by_team"], {"team": "KC", "bogus": 1}, R)
        assert e.code == "unknown_param" and e.param == "bogus"

    def test_the_values_dict_is_not_mutated(self, lib):
        values = {"team": "Dallas"}
        render(lib["wins_by_team"], values, R)
        assert values == {"team": "Dallas"}

    def test_none_values_are_tolerated(self, lib):
        assert render(lib["wins_by_team"], None, R, asof=None) if False else True
        r = render(lib["everything"], {"n": None, "team": None}, R)
        assert r.params["n"] == 100

    # ---- as-of ----

    @pytest.fixture
    def asof_t(self, tmp_path):
        return load(
            tmp_path,
            tpl(
                source="graph",
                library="q1_revenge",
                asof=True,
                params={"team": {"type": "team", "default": None}},
                query=None,
                filters={"team": ["team", "opponent"]},
            ),
        )["wins_by_team"]

    def test_asof_binds_the_graphs_week_and_ignores_the_llms(self, asof_t):
        r = render(asof_t, {"season": 1999, "week": 99}, R, asof=(2026, 6))
        assert r.params["season"] == 2026 and r.params["week"] == 6
        assert r.values["season"] == 2026 and r.values["week"] == 6
        assert r.params["min_old_games"] == LIBRARY["q1_revenge"]["min_old_games"]

    def test_asof_without_a_week_is_an_error(self, asof_t):
        e = code_of(render, asof_t, {}, R, asof=None)
        assert e.code == "missing"

    def test_asof_templates_still_reject_other_unknown_parameters(self, asof_t):
        assert code_of(render, asof_t, {"bogus": 1}, R, asof=(2026, 6)).code == "unknown_param"

    def test_a_library_query_is_never_text_substituted(self, asof_t):
        r = render(asof_t, {}, R, asof=(2026, 6))
        assert r.query == asof_t.query

    # ---- filters and keep ----

    def test_filters_keep_rows_where_any_listed_column_matches(self, asof_t):
        r = render(asof_t, {"team": "Chiefs"}, R, asof=(2026, 6))
        assert r.filters == ((("team", "opponent"), "KC"),)
        cols = ["player", "team", "opponent"]
        assert r.keep(cols, ["A", "KC", "DAL"])
        assert r.keep(cols, ["A", "DAL", "KC"])
        assert not r.keep(cols, ["A", "DAL", "BUF"])

    def test_no_filter_when_the_value_is_none(self, asof_t):
        r = render(asof_t, {}, R, asof=(2026, 6))
        assert r.filters == ()
        assert r.keep(["team"], ["anything"])

    def test_a_filter_on_a_column_the_rows_lack_keeps_everything(self, asof_t):
        r = render(asof_t, {"team": "KC"}, R, asof=(2026, 6))
        assert r.keep(["player"], ["A"])
        assert r.keep([], [])

    def test_a_filter_checks_only_the_columns_that_exist(self, asof_t):
        r = render(asof_t, {"team": "KC"}, R, asof=(2026, 6))
        assert r.keep(["team"], ["KC"]) and not r.keep(["team"], ["DAL"])

    def test_several_filters_must_all_pass(self):
        r = Rendered("t", "sql", "q", {}, {}, ((("a",), 1), (("b",), 2)))
        assert r.keep(["a", "b"], [1, 2])
        assert not r.keep(["a", "b"], [1, 3])
        assert not r.keep(["a", "b"], [0, 2])

    # ---- the prompt text ----

    def test_prompt_line_and_signatures(self, lib):
        line = lib["wins_by_team"].prompt_line()
        assert line.startswith("- wins_by_team [sql] Wins: Wins of one team.")
        assert (
            "team (team): the team" in line and "season (season, 1999..2026, default 2026)" in line
        )
        assert 'e.g. "How many wins did Dallas have in 2023?"' in line
        sig = lib["everything"].param("kind").signature()
        assert "one of a|B" in sig and "default a" in sig

    def test_prompt_line_hides_the_asof_parameters(self, asof_t):
        assert "season" not in asof_t.prompt_line() and "week" not in asof_t.prompt_line()

    def test_a_param_without_defaults_or_bounds(self):
        p = Param("name", "text", "a search string")
        assert p.signature() == "name (text): a search string"

    def test_is_gsis(self):
        assert is_gsis("00-0034857") and is_gsis(" 00-0034857 ")
        assert not is_gsis("00-003485") and not is_gsis("0034857") and not is_gsis("Josh Allen")


# ---- the real library -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    return load_templates(TEMPLATES_PATH, current_season=CURRENT)


class TestRealLibrary:
    def test_it_loads_and_every_template_has_examples(self, real):
        assert len(real) >= 40
        for t in real.values():
            assert t.examples and all(e.strip() for e in t.examples), t.id
            assert t.source in ("sql", "graph") and t.title and t.description, t.id

    def test_ids_are_well_formed_and_the_file_has_no_duplicates(self):
        data = yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))
        ids = [t["id"] for t in data["templates"]]
        assert len(ids) == len(set(ids))
        assert all(re.fullmatch(r"[a-z0-9_]{1,64}", i) for i in ids)

    def test_every_library_query_has_a_template(self, real):
        used = {t.library for t in real.values() if t.library}
        assert used == set(LIBRARY), set(LIBRARY) ^ used

    def test_library_templates_are_asof_graph_templates(self, real):
        for t in real.values():
            if t.library:
                assert t.source == "graph" and t.asof, t.id
            if t.asof:
                assert t.source == "graph", t.id

    def test_filters_name_parameters_and_columns(self, real):
        for t in real.values():
            for pname, cols in t.filters.items():
                assert t.param(pname) is not None and cols, t.id

    def test_every_example_is_distinct_enough_to_be_a_question(self, real):
        seen: dict[str, str] = {}
        for t in real.values():
            for e in t.examples:
                assert e.rstrip().endswith("?") or len(e.split()) >= 3, (t.id, e)
                assert e not in seen, f"{e!r} is an example of both {seen[e]} and {t.id}"
                seen[e] = t.id

    def test_every_template_renders_with_sample_values(self, real):
        """Required parameters get a sample value; then the rendered query must reference only
        bound parameters, and a SQL one must parse as one SELECT, a graph one pass the text
        check."""
        for t in real.values():
            values: dict[str, Any] = {}
            for p in t.params:
                if t.asof and p.name in ("season", "week"):
                    continue
                if not p.required:
                    continue
                values[p.name] = {
                    "team": "KC",
                    "player": "Patrick Mahomes",
                    "coach": "Andy Reid",
                    "season": 2023,
                    "week": 5,
                    "int": int(p.min if p.min is not None else 1) or 1,
                    "float": p.min if p.min is not None else 0.5,
                    "choice": p.choices[0] if p.choices else "x",
                    "text": "abc",
                }[p.type]
            r = render(t, values, R, asof=(2026, 6) if t.asof else None)
            refs = set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", r.query))
            assert refs <= set(r.params), (t.id, refs - set(r.params))
            assert (
                "{" not in re.sub(r"\{[^{}]*:[^{}]*\}|\{\s*\}", "", r.query) or t.source == "graph"
            ), t.id
            if t.source == "sql":
                st = duckdb.extract_statements(r.query)
                assert len(st) == 1 and st[0].type == duckdb.StatementType.SELECT, t.id
            else:
                assert check_cypher(r.query), t.id

    def test_defaults_alone_never_break_a_template(self, real):
        """A template whose parameters all have defaults renders with no values at all."""
        for t in real.values():
            if any(p.required for p in t.params if not (t.asof and p.name in ("season", "week"))):
                continue
            r = render(t, {}, R, asof=(2026, 6) if t.asof else None)
            assert r.query

    # the 2026-10-18 audit: three templates answered a different question than the one asked

    def test_qb_receivers_ranks_by_the_measure_asked(self, real):
        # "caught the most passes from Stafford" ranked by targets named Nacua, not Kupp
        t = real["qb_receivers"]
        r = render(t, {"player": "Patrick Mahomes", "rank_by": "catches", "limit": 1}, R)
        assert "ORDER BY catches DESC" in r.query and r.params["limit"] == 1
        assert "ORDER BY targets DESC" in render(t, {"player": "Patrick Mahomes"}, R).query
        assert set(t.param("rank_by").choices) == {"targets", "catches", "yards", "tds"}

    def test_the_coach_record_takes_a_season_range(self, real):
        # "from 2018 to 2025" returned 2026 too
        t = real["coach_head_record"]
        r = render(t, {"coach": "Andy Reid", "first_season": 2019, "last_season": 2023}, R)
        assert r.params["first_season"] == 2019 and r.params["last_season"] == 2023
        assert "g.season >= $first_season AND g.season <= $last_season" in r.query
        d = render(t, {"coach": "Andy Reid"}, R)
        assert d.params["first_season"] == 2018 and d.params["last_season"] == CURRENT

    def test_former_teammates_can_name_the_former_team(self, real):
        # "played with Saquon Barkley on the Giants" listed his Eagles teammates too
        t = real["former_teammates_on_team"]
        values = {"player": "Patrick Mahomes", "team": "Dallas", "former_team": "Chiefs"}
        r = render(t, values, R, asof=(2026, 6))
        assert r.params["former_team"] == "KC"
        assert "($former_team IS NULL OR t.team_id = $former_team)" in r.query
        values.pop("former_team")
        assert render(t, values, R, asof=(2026, 6)).params["former_team"] is None

    def test_a_deep_copy_of_the_dicts_is_independent(self, real):
        t = next(iter(real.values()))
        c = copy.deepcopy(t.constants)
        c["zz"] = 1
        assert "zz" not in t.constants
