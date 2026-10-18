"""AE00 golden set and bake-off (`ask/evaluate.py`): `compare`, `score`, `summarise`, `pick`,
`load_golden`, `reference_params`, `latest_summaries`, and `run_eval` over a fake engine (no W&B,
no OpenRouter, no real golden set, no data root).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from ask_fakes import ScriptedLLM, result

import nflengine.ask.evaluate as ev
from nflengine.ask.evaluate import (
    BOUNDS,
    CATEGORIES,
    EXPECTS,
    SCORER_VERSION,
    STORED_ROWS,
    GoldenQuestion,
    Scored,
    compare,
    current_hashes,
    file_hash,
    latest_summaries,
    load_golden,
    pick,
    reference_params,
    rescore,
    rescore_all,
    run_eval,
    score,
    summarise,
)
from nflengine.ask.guard import QueryResult, Refused
from nflengine.ask.llm import OutOfCredits
from nflengine.ask.route import AskResult
from nflengine.ask.templates import load_templates

# ---- compare ----------------------------------------------------------------------------------


def match(got, ref, got_cols=None, ref_cols=None):
    got_cols = got_cols or [f"g{i}" for i in range(len(got[0]) if got else 1)]
    ref_cols = ref_cols or [f"r{i}" for i in range(len(ref[0]) if ref else 1)]
    return compare(got_cols, got, ref_cols, ref)


class TestCompare:
    def test_identical(self):
        assert match([["KC", 14]], [["KC", 14]]) == (True, "match")

    def test_row_order_does_not_matter(self):
        ref = [["KC", 14], ["BUF", 11], ["DAL", 7]]
        assert match([ref[2], ref[0], ref[1]], ref)[0]

    def test_extra_system_columns_are_fine(self):
        got = [["g1", "KC", 14, 0.8], ["g2", "BUF", 11, 0.6]]
        assert match(got, [["KC", 14], ["BUF", 11]])[0]

    def test_column_order_and_names_do_not_matter(self):
        got = [[14, "KC"], [11, "BUF"]]
        assert compare(["wins", "team"], got, ["team", "wins"], [["KC", 14], ["BUF", 11]])[0]
        assert compare(["a", "b"], got, ["x", "y"], [["KC", 14], ["BUF", 11]])[0]

    def test_row_count_mismatch(self):
        assert match([["a"], ["b"], ["c"]], [["a"], ["b"]]) == (False, "rows 3 vs 2")
        assert match([], [["a"]]) == (False, "rows 0 vs 1")
        assert match([["a"]], []) == (False, "rows 1 vs 0")

    def test_both_empty(self):
        assert compare(["a"], [], ["a"], []) == (True, "both empty")
        assert compare([], [], ["a", "b"], []) == (True, "both empty")

    def test_fewer_system_columns(self):
        assert match([["a"]], [["a", "b"]], ["g0"], ["r0", "r1"]) == (False, "columns 1 vs 2")

    def test_a_reference_without_columns_matches_on_the_row_count_alone(self):
        assert compare(["a"], [["x"]], [], [[]]) == (True, "no reference columns")

    def test_a_missing_column_is_named(self):
        ok, why = compare(["a", "b"], [["KC", 3]], ["team", "wins"], [["KC", 4]])
        assert not ok and why == "no column matches 'wins'"

    def test_two_reference_columns_need_two_distinct_system_columns(self):
        ref = [[1, 1], [2, 2]]
        assert compare(["a"], [[1], [2]], ["x", "y"], ref) == (False, "columns 1 vs 2")
        assert compare(["a", "b"], [[1, 9], [2, 8]], ["x", "y"], ref)[0] is False
        assert compare(["a", "b"], [[1, 1], [2, 2]], ["x", "y"], ref)[0]

    def test_rows_must_line_up_across_columns(self):
        # each column matches as a multiset, but the pairs differ
        got = [["KC", 11], ["BUF", 14]]
        ref = [["KC", 14], ["BUF", 11]]
        assert match(got, ref) == (False, "values differ")

    def test_duplicates_count(self):
        assert match([[1], [2], [2]], [[1], [1], [2]])[0] is False
        assert match([[1], [1], [2]], [[1], [1], [2]])[0]

    def test_text_is_case_and_space_insensitive(self):
        assert match([["  patrick   MAHOMES "]], [["Patrick Mahomes"]])[0]
        assert match([["Patrick Mahomes"]], [["Patrick Mahomez"]])[0] is False

    def test_a_string_is_not_a_number(self):
        assert match([["14"]], [[14]])[0] is False

    def test_ints_and_floats_agree(self):
        assert match([[14.0]], [[14]])[0] and match([[14]], [[14.0]])[0]

    def test_nulls(self):
        assert match([[None, 1], ["a", 2]], [[None, 1], ["a", 2]])[0]
        assert match([[None], [1]], [[1], [None]])[0]
        assert match([[None]], [[1]])[0] is False
        assert match([[1.5], [None]], [[1.5], [None]])[0]

    def test_lists_are_sets_but_keep_duplicates(self):
        assert match([[["b", "a"]]], [[["a", "b"]]])[0]
        assert match([[["a", "a"]]], [[["a"]]])[0] is False
        assert match([[[2, 1]]], [[[1, 2]]])[0]

    def test_maps_compare_by_content(self):
        assert match([[{"a": 1, "b": 2}]], [[{"b": 2, "a": 1}]])[0]
        assert match([[{"a": 1}]], [[{"a": 2}]])[0] is False

    def test_booleans_are_not_numbers(self):
        assert match([[True], [False]], [[0], [1]])[0]  # compared as text-ish values
        assert match([[True]], [[False]])[0] is False

    # ---- numbers: precision and percentages ----

    def test_a_less_precise_system_number_matches_at_its_precision(self):
        assert match([[0.28]], [[0.2843]])[0]
        assert match([[0.284]], [[0.2843]])[0]
        assert match([[0.3]], [[0.2843]])[0]
        assert match([[0.2843]], [[0.2843]])[0]
        assert match([[12.4]], [[12.38]])[0]

    def test_a_more_precise_system_number_must_agree_to_its_own_precision(self):
        assert match([[0.2843]], [[0.28]])[0] is False

    def test_the_precision_is_the_most_precise_system_value_of_the_column(self):
        # one value with 3 decimals sets the column's precision for every row
        assert match([[0.5], [0.123]], [[0.5], [0.1234]])[0]
        assert match([[0.5], [0.123]], [[0.5], [0.2]])[0] is False

    def test_a_share_matches_a_percentage(self):
        assert match([[28.4]], [[0.284]])[0]
        assert match([[28]], [[0.2843]])[0]
        assert match([[28.4], [60.0]], [[0.284], [0.6]])[0]
        assert match([[29.4]], [[0.284]])[0] is False
        assert match([[28.5]], [[0.284]])[0] is False

    def test_a_percentage_reference_is_not_scaled_down(self):
        # only a 0-1 share written as a percentage is understood, not the reverse
        assert match([[0.284]], [[28.4]])[0] is False

    def test_a_percentage_of_the_whole_column_only(self):
        # one row as a share and the other as a count is not one consistent unit
        assert match([[28.4], [3]], [[0.284], [3]])[0] is False

    @pytest.mark.parametrize(
        "got, ref",
        [
            ([[14]], [[15]]),
            ([[15]], [[14]]),
            ([[2500]], [[2501]]),
            ([[0.28]], [[0.29]]),
            ([[12.4]], [[12.5]]),
            ([["KC", 14], ["BUF", 12]], [["KC", 15], ["BUF", 12]]),
        ],
    )
    def test_off_by_one_is_not_a_match(self, got, ref):
        assert match(got, ref)[0] is False

    def test_two_off_is_not_a_match_today(self):
        assert match([[14]], [[16]])[0] is False
        assert match([[0.28]], [[0.30]])[0] is False

    def test_off_by_one_at_every_scale(self):
        for base in (1, 10, 100, 1000, 100000):
            assert match([[base]], [[base + 1]])[0] is False
            assert match([[base + 1]], [[base]])[0] is False
        for decimals in (1, 2, 3, 4):
            unit = 10.0**-decimals
            assert match([[round(0.5 + unit, decimals)]], [[0.5]])[0] is False

    def test_whole_numbers_do_not_match_a_fractional_reference_that_rounds_to_nothing(self):
        assert match([[0]], [[0.4]])[0] is False
        assert match([[0.0]], [[0.04]])[0] is False
        assert match([[0.00]], [[0.004]])[0] is False

    def test_rounding_ties_and_significant_digits(self):
        assert match([[12]], [[12.4]])[0]  # 12.4 shown as 12
        assert match([[12]], [[11.6]])[0]
        assert match([[0.28]], [[0.2843]])[0] and match([[0.28]], [[0.2751]])[0]
        assert match([[0.29]], [[0.2843]])[0] is False
        assert match([[0.0]], [[0.0]])[0] and match([[0]], [[0]])[0]

    @pytest.mark.parametrize(
        "got, ref", [([[300]], [[3]]), ([[1400]], [[14]]), ([[500], [700]], [[5], [7]])]
    )
    def test_a_percentage_transform_only_applies_to_shares(self, got, ref):
        assert match(got, ref)[0] is False

    # ---- the search is bounded ----

    def test_many_identical_columns_do_not_blow_up(self):
        ref = [[1] * 9, [2] * 9]
        got_ok = [[1] * 9, [2] * 9]
        got_bad = [[1] * 9, [2] * 8 + [3]]
        t0 = time.monotonic()
        assert compare([f"g{i}" for i in range(9)], got_ok, [f"r{i}" for i in range(9)], ref)[0]
        assert not compare([f"g{i}" for i in range(9)], got_bad, [f"r{i}" for i in range(9)], ref)[
            0
        ]
        assert time.monotonic() - t0 < 2.0

    def test_ambiguous_columns_are_assigned_consistently(self):
        # column a == column b in the reference, but not in the system's rows
        ref = [[1, 1], [2, 2]]
        got = [[1, 2], [2, 1]]
        assert compare(["a", "b"], got, ["x", "y"], ref)[0] is False


# ---- load_golden -


def golden_file(tmp_path: Path, questions: list[dict], version: Any = 1) -> Path:
    path = tmp_path / "golden.yaml"
    path.write_text(yaml.safe_dump({"version": version, "questions": questions}, sort_keys=False))
    return path


def q(**over: Any) -> dict:
    base = {
        "id": "g001",
        "question": "How many wins did Dallas have in 2023?",
        "category": "sql",
        "source": "sql",
        "reference": "SELECT 12 AS wins",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def bad_golden(tmp_path, *questions, match: str, **kw):
    with pytest.raises(ValueError, match=match):
        load_golden(golden_file(tmp_path, list(questions), **kw))


class TestLoadGolden:
    def test_a_valid_set(self, tmp_path):
        out = load_golden(
            golden_file(
                tmp_path,
                [
                    q(template="team_wins", params={"x": 1}, notes="n"),
                    q(id="g002", category="trick", expect="refuse", source=None, reference=None),
                    q(
                        id="g003",
                        category="trick",
                        expect="clarify",
                        source=None,
                        reference=None,
                        choices=["a", "b"],
                    ),
                ],
            )
        )
        a, b, c = out
        assert isinstance(a, GoldenQuestion) and a.expect == "answer" and a.source == "sql"
        assert a.template == "team_wins" and a.params == {"x": 1} and a.notes == "n"
        assert a.reference == "SELECT 12 AS wins" and a.choices == ()
        assert b.expect == "refuse" and b.reference is None and b.source is None
        assert c.expect == "clarify" and c.choices == ("a", "b")

    def test_the_question_text_is_stripped(self, tmp_path):
        (g,) = load_golden(golden_file(tmp_path, [q(question="  How many?  \n")]))
        assert g.question == "How many?"

    @pytest.mark.parametrize("version", [None, 0, 2, "1"])
    def test_the_version(self, tmp_path, version):
        bad_golden(tmp_path, q(), match="version must be 1", version=version)

    @pytest.mark.parametrize("gid", [None, "", "g1", "g0001", "G001", "x001", "g00a"])
    def test_ids(self, tmp_path, gid):
        bad_golden(tmp_path, q(id=gid), match="id must be g \\+ 3 digits and unique")

    def test_duplicate_ids(self, tmp_path):
        bad_golden(tmp_path, q(), q(question="again"), match="unique")

    def test_an_empty_question(self, tmp_path):
        bad_golden(tmp_path, q(question="  "), match="no question")
        bad_golden(tmp_path, q(question=None), match="no question")

    @pytest.mark.parametrize(
        "over",
        [{"category": "other"}, {"category": None}, {"expect": "maybe"}],
    )
    def test_category_and_expect(self, tmp_path, over):
        bad_golden(tmp_path, q(**over), match="category must be")

    def test_an_answer_question_needs_a_source_and_a_reference(self, tmp_path):
        bad_golden(tmp_path, q(source=None), match="needs source and reference")
        bad_golden(tmp_path, q(source="history"), match="needs source and reference")
        bad_golden(tmp_path, q(reference=None), match="needs source and reference")
        bad_golden(tmp_path, q(reference="   "), match="needs source and reference")

    @pytest.mark.parametrize("expect", ["refuse", "clarify"])
    def test_other_expectations_have_no_reference(self, tmp_path, expect):
        bad_golden(tmp_path, q(expect=expect), match=f"a {expect} question has no reference")

    def test_an_unknown_template_is_caught_when_the_library_is_given(self, tmp_path):
        path = golden_file(tmp_path, [q(template="nope")])
        assert load_golden(path)  # without the library it can't tell
        with pytest.raises(ValueError, match="unknown template 'nope'"):
            load_golden(path, templates={"team_wins": object()})
        assert load_golden(
            golden_file(tmp_path, [q(template="team_wins")]), templates={"team_wins": 1}
        )

    def test_choices_are_for_clarify_only(self, tmp_path):
        bad_golden(tmp_path, q(choices=["a"]), match="choices only for clarify")

    def test_errors_name_the_entry(self, tmp_path):
        with pytest.raises(ValueError) as exc:
            load_golden(golden_file(tmp_path, [q(), q(id="g002", category="x")]))
        assert "questions[1]" in str(exc.value) and "g002" in str(exc.value)

    def test_the_constants(self):
        assert CATEGORIES == ("template", "sql", "graph", "trick")
        assert EXPECTS == ("answer", "refuse", "clarify")
        assert BOUNDS == {"p95_latency_s": 8.0, "cost_per_question": 0.002}

    def test_file_hash_ignores_line_endings(self, tmp_path):
        a, b = tmp_path / "a.txt", tmp_path / "b.txt"
        a.write_bytes(b"x: 1\ny: 2\n")
        b.write_bytes(b"x: 1\r\ny: 2\r\n")
        assert file_hash(a) == file_hash(b) and len(file_hash(a)) == 12
        b.write_bytes(b"x: 1\r\ny: 3\r\n")
        assert file_hash(a) != file_hash(b)


@pytest.fixture(scope="module")
def golden():
    return load_golden(templates=load_templates(current_season=2026))


class TestRealGoldenSet:
    def test_loads_and_has_the_agreed_size_and_mix(self, golden):
        assert 60 <= len(golden) <= 100
        by = {c: [g for g in golden if g.category == c] for c in CATEGORIES}
        assert all(len(v) >= 4 for v in by.values())
        assert any(g.expect == "refuse" for g in golden) and any(
            g.expect == "clarify" for g in golden
        )

    def test_every_answer_question_has_a_reference_and_every_template_exists(self, golden):
        templates = load_templates(current_season=2026)
        for g in golden:
            if g.expect == "answer":
                assert g.reference and g.source in ("sql", "graph"), g.id
            else:
                assert g.reference is None, g.id
            if g.template:
                assert g.template in templates, g.id

    def test_trick_questions_are_only_refuse_or_clarify_or_answer(self, golden):
        for g in golden:
            if g.category == "trick":
                assert g.expect in EXPECTS

    def test_questions_are_distinct_and_short_enough_to_ask(self, golden):
        texts = [g.question for g in golden]
        assert len(texts) == len(set(texts))
        assert all(len(t) <= 500 for t in texts)


# ---- reference_params -------------------------------------------------------------------------


def gq(reference: str | None, **params: Any) -> GoldenQuestion:
    return GoldenQuestion(
        id="g001", question="q", category="sql", source="sql", reference=reference, params=params
    )


class TestReferenceParams:
    def test_reserved_names_are_bound_only_when_used(self):
        g = gq("SELECT $asof_season, $asof_week, $current_season")
        assert reference_params(g, (2026, 6), 2026) == {
            "asof_season": 2026,
            "asof_week": 6,
            "current_season": 2026,
        }
        g = gq("SELECT * FROM t WHERE season = $current_season")
        assert reference_params(g, (2026, 6), 2027) == {"current_season": 2027}
        assert reference_params(gq("SELECT 1"), (2026, 6), 2026) == {}

    def test_a_graph_week_that_is_missing_binds_none(self):
        g = gq("MATCH (g:Game {season: $asof_season, week: $asof_week}) RETURN g")
        assert reference_params(g, None, 2026) == {"asof_season": None, "asof_week": None}

    def test_the_questions_own_parameters_are_kept_when_referenced(self):
        g = gq("SELECT $team, $n", team="KC", n=3, unused="x")
        assert reference_params(g, None, 2026) == {"team": "KC", "n": 3}

    def test_a_reserved_name_cannot_be_overridden_by_a_question_parameter(self):
        g = gq("SELECT $asof_season", asof_season=1999)
        assert reference_params(g, (2026, 6), 2026) == {"asof_season": 2026}

    def test_a_longer_name_is_not_a_reference(self):
        g = gq("SELECT $asof_seasonal, $current_seasons")
        assert reference_params(g, (2026, 6), 2026) == {}
        assert reference_params(gq("SELECT $teams", team="KC"), None, 2026) == {}

    def test_regex_metacharacters_in_a_parameter_name_are_harmless(self):
        g = gq("SELECT $a", **{"a.b": 1, "a": 2})
        assert reference_params(g, None, 2026) == {"a": 2}

    def test_no_reference(self):
        assert reference_params(gq(None, x=1), (2026, 1), 2026) == {}


# ---- score -


def golden_q(**over: Any) -> GoldenQuestion:
    base: dict[str, Any] = {
        "id": "g007",
        "question": "How many wins did Dallas have?",
        "category": "template",
        "source": "sql",
        "reference": "SELECT 12",
        "template": "team_wins",
    }
    base.update(over)
    return GoldenQuestion(**base)


def ask_result(**over: Any) -> AskResult:
    base: dict[str, Any] = {
        "question": "How many wins did Dallas have?",
        "status": "answered",
        "route": "template",
        "template_id": "team_wins",
        "source": "sql",
        "query": "SELECT wins FROM teams",
        "columns": ["wins"],
        "rows": [[12]],
        "retries": 1,
        "answer": "Dallas won 12.",
        "answer_check": {"passed": True, "unmatched": []},
        "calls": [{"cost": 0.001}, {"cost": 0.0005}],
        "timings": {"total": 1.234},
    }
    base.update(over)
    return AskResult(**base)


def ref(columns=("wins",), rows=((12,),)) -> QueryResult:
    return result(list(columns), [list(r) for r in rows])


class TestScore:
    def test_a_correct_answer(self):
        s = score(golden_q(), ask_result(), ref())
        assert isinstance(s, Scored) and s.correct is True and s.reason == "match"
        assert (s.id, s.category, s.expect, s.question) == (
            "g007",
            "template",
            "answer",
            "How many wins did Dallas have?",
        )
        assert (s.status, s.route, s.template, s.expected_template) == (
            "answered",
            "template",
            "team_wins",
            "team_wins",
        )
        assert (s.source, s.retries, s.rows, s.ref_rows, s.calls) == ("sql", 1, 1, 1, 2)
        assert s.latency_s == 1.234 and s.cost == pytest.approx(0.0015)
        assert s.answer_passed is True and s.refused_by is None and s.error is None
        assert s.query == "SELECT wins FROM teams" and s.answer == "Dallas won 12."
        assert s.to_dict()["correct"] is True

    def test_a_wrong_answer(self):
        s = score(golden_q(), ask_result(rows=[[40]]), ref())
        assert s.correct is False and s.reason in ("values differ", "no column matches 'wins'")

    def test_an_empty_result_is_compared_like_any_other(self):
        res = ask_result(status="empty", rows=[], columns=["wins"], answer="No rows matched.")
        assert score(golden_q(), res, ref(rows=())).correct is True
        assert score(golden_q(), res, ref()).correct is False

    @pytest.mark.parametrize("status", ["refused", "clarify", "error"])
    def test_no_rows_is_wrong_for_an_answer_question(self, status):
        s = score(golden_q(), ask_result(status=status, rows=[]), ref())
        assert s.correct is False and s.reason == f"no rows ({status})"

    def test_a_failed_reference_is_scored_wrong_with_its_reason(self):
        s = score(golden_q(), ask_result(), None, "Refused: sql.table")
        assert s.correct is False and s.reason == "reference failed (Refused: sql.table)"
        assert s.ref_rows is None

    def test_refuse(self):
        g = golden_q(expect="refuse", reference=None, category="trick")
        res = ask_result(
            status="refused", rows=[], refusal={"by": "router", "rule": "write", "message": "x"}
        )
        s = score(g, res, None)
        assert s.correct is True and s.reason == "refused" and s.refused_by == "router"
        s = score(g, ask_result(status="answered"), None)
        assert s.correct is False and s.reason == "not refused (answered)"
        for by in ("code", "generator", "guard"):
            r = ask_result(status="refused", refusal={"by": by, "rule": "x", "message": "m"})
            assert score(g, r, None).correct and score(g, r, None).refused_by == by

    def test_an_error_is_not_a_refusal(self):
        g = golden_q(expect="refuse", reference=None, category="trick")
        assert score(g, ask_result(status="error", rows=[]), None).correct is False

    def test_clarify(self):
        g = golden_q(expect="clarify", reference=None, category="trick", choices=("00-1", "00-2"))
        choices = [
            {
                "text": "Allen",
                "kind": "player",
                "options": [{"value": "00-1"}, {"value": "00-2"}, {"value": "00-3"}],
            }
        ]
        s = score(g, ask_result(status="clarify", rows=[], choices=choices), None)
        assert s.correct is True and s.reason == "clarified"
        choices[0]["options"].pop(1)
        s = score(g, ask_result(status="clarify", rows=[], choices=choices), None)
        assert s.correct is False and s.reason == "missing choices ['00-2']"
        s = score(g, ask_result(status="answered"), None)
        assert s.correct is False and s.reason == "no clarify (answered)"

    def test_clarify_without_required_choices_passes_any_clarification(self):
        g = golden_q(expect="clarify", reference=None, category="trick")
        assert score(g, ask_result(status="clarify", rows=[], choices=[]), None).correct

    def test_clarify_options_may_come_from_several_mentions(self):
        g = golden_q(expect="clarify", reference=None, category="trick", choices=("NYG", "00-1"))
        choices = [
            {"options": [{"value": "NYG"}, {"value": "NYJ"}]},
            {"options": [{"value": "00-1"}]},
        ]
        assert score(g, ask_result(status="clarify", rows=[], choices=choices), None).correct

    def test_secrets_in_the_query_or_answer_are_scrubbed_in_the_record(self):
        res = ask_result(
            query="SELECT 'password=hunter2' -- Bearer abcdefghijklmnop12345",
            answer="token=sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789 is 12",
        )
        s = score(golden_q(), res, ref())
        assert "hunter2" not in s.query and "abcdefghijklmnop12345" not in s.query
        assert "sk-or-v1-abcdefghijklmnop" not in s.answer

    def test_long_queries_and_answers_are_cut(self):
        s = score(golden_q(), ask_result(query="x" * 5000, answer="y" * 5000), ref())
        assert len(s.query) <= 2000 and len(s.answer) <= 600

    def test_missing_pieces(self):
        res = ask_result(answer_check=None, answer=None, query=None, timings={}, calls=[])
        s = score(golden_q(), res, ref())
        assert s.answer_passed is None and s.answer is None and s.query is None
        assert s.latency_s == 0.0 and s.cost == 0.0 and s.calls == 0  # no LLM call: free

    def test_answer_passed_false(self):
        s = score(golden_q(), ask_result(answer_check={"passed": False, "unmatched": ["7"]}), ref())
        assert s.answer_passed is False


class TestUnknownCosts:
    """Sol's second review (AE00): a missing cost must stay unknown, never read as free."""

    def test_a_call_without_a_cost_makes_the_question_unknown(self):
        s = score(golden_q(), ask_result(calls=[{"cost": 0.001}, {"cost": None}]), ref())
        assert s.cost is None and s.calls == 2
        assert score(golden_q(), ask_result(calls=[{"cost": None}]), ref()).cost is None
        assert score(golden_q(), ask_result(), ref()).cost == pytest.approx(0.0015)

    def test_an_unpriced_run_has_no_cost_and_isnt_in_bounds(self):
        out = summarise([S(), S(id="g002", cost=None, calls=2)])
        assert out["cost_per_question"] is None and out["cost_unknown_questions"] == 1
        assert out["cost_total"] == pytest.approx(0.001)  # of the known costs
        assert out["meets_bounds"] is False

    def test_a_question_with_no_call_is_free_not_unknown(self):
        out = summarise([S(), S(id="g002", cost=None, calls=0)])
        assert out["cost_per_question"] == pytest.approx(0.0005)
        assert out["cost_unknown_questions"] == 0

    def test_an_unpriced_run_loses_a_tie_from_summarise_to_pick(self):
        priced = {**summarise([S(), S(id="g002")]), "model": "priced/m", "reasoning_effort": "low"}
        unpriced = {
            **summarise([S(cost=None), S(id="g002", cost=None)]),
            "model": "local/m",
            "reasoning_effort": None,
        }
        assert unpriced["cost_per_question"] is None
        assert pick([unpriced, priced]) is priced
        assert pick([priced, unpriced]) is priced


class TestOneItemCells:
    """A one-item list is its item, and is unwrapped before the column is typed (Sol, AE00)."""

    def test_a_one_item_text_list_matches_its_item(self):
        assert compare(["team"], [[["LAC"]]], ["team"], [["LAC"]])[0]

    def test_a_one_item_number_keeps_the_numeric_rules(self):
        assert compare(["x"], [[[0.28]]], ["x"], [[0.2843]])[0]
        assert compare(["x"], [[[28.4]]], ["x"], [[0.2843]])[0]  # a share as a percentage
        assert not compare(["x"], [[[0.29]]], ["x"], [[0.2843]])[0]

    def test_a_longer_list_is_still_not_a_scalar(self):
        assert not compare(["team"], [[["LAC", "KC"]]], ["team"], [["LAC"]])[0]
        assert compare(["teams"], [[["KC", "LAC"]]], ["teams"], [[["LAC", "KC"]]])[0]


# ---- summarise -


def S(**over: Any) -> Scored:
    base: dict[str, Any] = {
        "id": "g001",
        "category": "template",
        "expect": "answer",
        "question": "q",
        "status": "answered",
        "route": "template",
        "template": "t1",
        "expected_template": "t1",
        "source": "sql",
        "correct": True,
        "reason": "match",
        "retries": 0,
        "latency_s": 1.0,
        "cost": 0.001,
        "rows": 1,
        "ref_rows": 1,
        "answer_passed": True,
        "refused_by": None,
        "error": None,
        "query": "q",
        "answer": "a",
        "calls": 2,
    }
    base.update(over)
    return Scored(**base)


@pytest.fixture
def mixed():
    """Ten questions, counted by hand (see the asserts below)."""
    return [
        # answer questions (6): correct T T F T F T
        S(
            id="g001",
            category="template",
            correct=True,
            route="template",
            template="t1",
            expected_template="t1",
            latency_s=1.0,
            cost=0.001,
            retries=0,
            answer_passed=True,
        ),
        S(
            id="g002",
            category="template",
            correct=True,
            route="template",
            template="t2",
            expected_template="t2",
            latency_s=2.0,
            cost=0.002,
            retries=1,
            answer_passed=True,
        ),
        S(
            id="g003",
            category="template",
            correct=False,
            route="generated",
            template=None,
            expected_template="t3",
            latency_s=3.0,
            cost=0.003,
            retries=2,
            answer_passed=False,
        ),
        S(
            id="g004",
            category="sql",
            correct=True,
            route="generated",
            template=None,
            expected_template=None,
            latency_s=4.0,
            cost=None,
            retries=0,
            answer_passed=None,
        ),
        S(
            id="g005",
            category="graph",
            correct=False,
            route="generated",
            template=None,
            expected_template=None,
            status="refused",
            latency_s=5.0,
            cost=0.004,
            retries=1,
            answer_passed=None,
        ),
        S(
            id="g006",
            category="graph",
            correct=True,
            route="template",
            template="t9",
            expected_template="t6",
            latency_s=6.0,
            cost=0.001,
            retries=0,
            answer_passed=True,
        ),
        # refuse (2): T F
        S(
            id="g007",
            category="trick",
            expect="refuse",
            correct=True,
            status="refused",
            route="refused",
            template=None,
            expected_template=None,
            latency_s=0.5,
            cost=0.0005,
            answer_passed=None,
        ),
        S(
            id="g008",
            category="trick",
            expect="refuse",
            correct=False,
            status="answered",
            route="generated",
            template=None,
            expected_template=None,
            latency_s=7.0,
            cost=0.002,
            answer_passed=True,
        ),
        # clarify (2): T F
        S(
            id="g009",
            category="trick",
            expect="clarify",
            correct=True,
            status="clarify",
            route="clarify",
            template=None,
            expected_template=None,
            latency_s=0.1,
            cost=0.0,
            answer_passed=None,
        ),
        S(
            id="g010",
            category="trick",
            expect="clarify",
            correct=False,
            status="error",
            route="none",
            template=None,
            expected_template=None,
            latency_s=9.0,
            cost=0.0,
            answer_passed=None,
        ),
    ]


class TestSummarise:
    def test_every_metric_by_hand(self, mixed):
        s = summarise(mixed)
        assert s["questions"] == 10
        assert s["exec_accuracy"] == pytest.approx(4 / 6)  # g001 g002 g004 g006 of 6 answer rows
        assert s["overall_accuracy"] == pytest.approx(6 / 10)  # + the clarify and the refusal
        assert s["template_hit_rate"] == pytest.approx(3 / 6)  # g001 g002 g006 route == template
        assert s["template_accuracy"] == pytest.approx(2 / 4)  # g001 g002 right; g003 g006 wrong
        assert s["refusal_accuracy"] == pytest.approx(1 / 2)
        assert s["clarify_accuracy"] == pytest.approx(1 / 2)
        assert s["false_refusal_rate"] == pytest.approx(1 / 6)  # g005 was refused
        assert s["error_rate"] == pytest.approx(1 / 10)  # g010
        assert s["mean_retries"] == pytest.approx((0 + 1 + 2 + 0 + 1 + 0) / 10)
        assert s["retry_rate"] == pytest.approx(3 / 10)
        assert s["cost_total"] == pytest.approx(
            0.001 + 0.002 + 0.003 + 0.004 + 0.001 + 0.0005 + 0.002
        )
        # g004 made calls but has no cost: the run's cost is unknown, never read as free (Sol, AE00)
        assert s["cost_per_question"] is None and s["cost_unknown_questions"] == 1
        assert s["answer_check_pass_rate"] == pytest.approx(4 / 5)  # 5 answers checked, g003 failed
        assert s["accuracy_template"] == pytest.approx(2 / 3)
        assert s["accuracy_sql"] == 1.0
        assert s["accuracy_graph"] == pytest.approx(1 / 2)
        assert s["accuracy_trick"] == pytest.approx(2 / 4)

    def test_latency_percentiles_interpolate_over_all_rows(self, mixed):
        s = summarise(mixed)
        lat = sorted([1, 2, 3, 4, 5, 6, 0.5, 7, 0.1, 9])
        assert s["latency_p50_s"] == pytest.approx((lat[4] + lat[5]) / 2)  # 3.5
        k = 9 * 0.95
        assert s["latency_p95_s"] == pytest.approx(lat[8] + (lat[9] - lat[8]) * (k - 8))

    def test_percentiles_of_simple_lists(self):
        rows = [S(latency_s=float(x)) for x in (1, 2, 3, 4)]
        s = summarise(rows)
        assert s["latency_p50_s"] == 2.5 and s["latency_p95_s"] == pytest.approx(3.85)
        s = summarise([S(latency_s=2.0)])
        assert s["latency_p50_s"] == 2.0 and s["latency_p95_s"] == 2.0

    def test_metrics_without_rows_are_none(self):
        s = summarise([S(expect="refuse", correct=True)])
        assert s["exec_accuracy"] is None and s["template_hit_rate"] is None
        assert s["template_accuracy"] is None and s["clarify_accuracy"] is None
        assert s["refusal_accuracy"] == 1.0 and s["false_refusal_rate"] is None
        assert s["accuracy_sql"] is None and s["accuracy_template"] == 1.0

    def test_template_accuracy_counts_only_questions_with_an_expected_template(self):
        rows = [
            S(template="a", expected_template="a"),
            S(template=None, expected_template=None),
            S(template="b", expected_template=None),
        ]
        assert summarise(rows)["template_accuracy"] == 1.0

    def test_empty(self):
        s = summarise([])
        assert s["questions"] == 0 and s["exec_accuracy"] is None and s["latency_p95_s"] is None
        assert s["cost_total"] == 0 and s["meets_bounds"] is False

    @pytest.mark.parametrize(
        "latency, cost, meets",
        [
            (8.0, 0.002, True),
            (7.9, 0.0019, True),
            (8.01, 0.001, False),
            (1.0, 0.0021, False),
            (9.0, 0.5, False),
        ],
    )
    def test_the_bounds(self, latency, cost, meets):
        s = summarise([S(latency_s=latency, cost=cost)])
        assert s["meets_bounds"] is meets

    def test_the_bound_uses_the_95th_percentile_not_the_maximum(self):
        rows = [S(latency_s=1.0) for _ in range(19)] + [S(latency_s=60.0)]
        s = summarise(rows)
        assert s["latency_p95_s"] == pytest.approx(1.0 + 59.0 * 0.05)  # interpolated
        rows = [S(latency_s=1.0) for _ in range(99)] + [S(latency_s=60.0)]
        assert summarise(rows)["meets_bounds"] is True  # one slow question in a hundred

    def test_a_cost_of_none_counts_as_zero_only_without_a_call(self):
        s = summarise([S(cost=None, calls=0), S(cost=0.004)])
        assert s["cost_per_question"] == pytest.approx(0.002) and s["cost_total"] == 0.004
        s = summarise([S(cost=None, calls=1), S(cost=0.004)])
        assert s["cost_per_question"] is None and s["cost_total"] == 0.004


# ---- pick -


def summary(model, acc, p95=2.0, cost=0.001, bounds=True, effort="low"):
    return {
        "model": model,
        "reasoning_effort": effort,
        "exec_accuracy": acc,
        "latency_p95_s": p95,
        "cost_per_question": cost,
        "meets_bounds": bounds,
    }


class TestPick:
    """Rishi's rule (D138): the best execution accuracy whatever it costs; within 2 points the
    cheaper, then the faster p95."""

    def test_the_most_accurate_wins_outside_the_old_bounds(self):
        a, b = summary("a", 0.70), summary("b", 0.85)
        c = summary("c", 0.95, p95=30.0, cost=0.01, bounds=False)
        assert pick([a, b, c]) is c

    def test_nothing_to_pick_from(self):
        assert pick([]) is None

    def test_a_summary_without_accuracy_is_ignored(self):
        assert pick([summary("a", None)]) is None
        assert pick([summary("a", None), summary("b", 0.5)])["model"] == "b"

    def test_ties_within_two_points_go_to_the_cheaper(self):
        dear, cheap = summary("dear", 0.800, cost=0.0011), summary("cheap", 0.781, cost=0.0004)
        assert pick([dear, cheap]) is cheap
        assert pick([cheap, dear]) is cheap

    def test_one_question_of_84_is_a_tie_and_two_are_not(self):
        best, one, two = summary("best", 60 / 84), summary("one", 59 / 84), summary("two", 58 / 84)
        one["cost_per_question"] = two["cost_per_question"] = 0.0001
        assert pick([best, one]) is one
        assert pick([best, two]) is best

    def test_just_over_two_points_is_not_a_tie(self):
        dear, cheap = summary("dear", 0.800, cost=0.0011), summary("cheap", 0.779, cost=0.0004)
        assert pick([dear, cheap]) is dear

    def test_ties_are_measured_from_the_best_not_chained(self):
        a, b, c = (
            summary("a", 0.800, cost=0.003),
            summary("b", 0.785, cost=0.002),
            summary("c", 0.770, cost=0.0001),
        )
        # c is 3 points below a: not in the tie, even though it is within 2 points of b
        assert pick([a, b, c]) is b

    def test_equal_cost_goes_to_the_faster(self):
        a, b = summary("a", 0.8, p95=6.0, cost=0.001), summary("b", 0.8, p95=3.0, cost=0.001)
        assert pick([a, b]) is b
        assert pick([b, a]) is b

    def test_an_unknown_cost_never_wins_a_tie(self):
        a, c = summary("a", 0.8, cost=0.0015), summary("c", 0.8, cost=None)
        assert pick([a, c]) is a
        assert pick([c, a]) is a
        assert pick([c]) is c

    def test_a_cheaper_model_below_the_tie_does_not_win(self):
        assert pick([summary("a", 0.9, cost=0.01), summary("b", 0.5, cost=1e-6)])["model"] == "a"

    def test_it_returns_the_summary_itself(self):
        s = summary("a", 0.5)
        assert pick([s]) is s

    def test_the_same_model_at_two_efforts_are_two_candidates(self):
        low, high = (
            summary("m", 0.80, p95=2.0, cost=0.0004, effort="low"),
            summary("m", 0.90, p95=7.0, cost=0.0012, effort="high"),
        )
        assert pick([low, high]) is high


# ---- latest_summaries -


def eval_file(
    root: Path,
    name: str,
    model: str,
    effort: str | None,
    questions=10,
    smoke=False,
    stopped=None,
    acc=0.5,
    version=SCORER_VERSION,
    config=None,
):
    d = root / "ask" / "evals"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": {"golden_questions": questions, "scorer_version": version, **(config or {})},
        "summary": {
            "model": model,
            "reasoning_effort": effort,
            "questions": questions,
            "exec_accuracy": acc,
            "stopped": stopped,
        },
        "questions": [],
        "smoke": smoke,
    }
    (d / name).write_text(json.dumps(payload), encoding="utf-8")


class TestLatestSummaries:
    def paths(self, tmp_path):
        return SimpleNamespace(ask=tmp_path / "ask")

    def test_the_newest_complete_run_per_model_and_effort(self, tmp_path):
        eval_file(tmp_path, "20261010T010101Z_a_low.json", "a/m", "low", acc=0.1)
        eval_file(tmp_path, "20261011T010101Z_a_low.json", "a/m", "low", acc=0.2)
        eval_file(tmp_path, "20261012T010101Z_a_high.json", "a/m", "high", acc=0.3)
        eval_file(tmp_path, "20261013T010101Z_b_low.json", "b/m", "low", acc=0.4)
        out = latest_summaries(self.paths(tmp_path))
        by = {(s["model"], s["reasoning_effort"]): s for s in out}
        assert set(by) == {("a/m", "low"), ("a/m", "high"), ("b/m", "low")}
        assert by[("a/m", "low")]["exec_accuracy"] == 0.2
        assert by[("a/m", "low")]["file"] == "20261011T010101Z_a_low.json"
        assert by[("a/m", "low")]["golden_questions"] == 10

    def test_smoke_runs_are_separate(self, tmp_path):
        eval_file(tmp_path, "20261010T010101Z_a_low.json", "a/m", "low")
        eval_file(tmp_path, "20261011T010101Z_a_low_smoke.json", "a/m", "low", smoke=True, acc=0.9)
        full = latest_summaries(self.paths(tmp_path))
        smoke = latest_summaries(self.paths(tmp_path), smoke=True)
        assert [s["exec_accuracy"] for s in full] == [0.5]
        assert [s["exec_accuracy"] for s in smoke] == [0.9]

    def test_stopped_and_empty_and_broken_runs_are_skipped(self, tmp_path):
        eval_file(tmp_path, "20261010T010101Z_a_low.json", "a/m", "low", acc=0.1)
        eval_file(
            tmp_path,
            "20261011T010101Z_a_low.json",
            "a/m",
            "low",
            stopped="out of OpenRouter credits",
        )
        eval_file(tmp_path, "20261012T010101Z_a_low.json", "a/m", "low", questions=0)
        (tmp_path / "ask" / "evals" / "20261013T010101Z_a_low.json").write_text("{broken")
        out = latest_summaries(self.paths(tmp_path))
        assert [s["exec_accuracy"] for s in out] == [0.1]

    def test_a_foreign_json_file_is_skipped(self, tmp_path):
        eval_file(tmp_path, "20261010T010101Z_a_low.json", "a/m", "low", acc=0.1)
        (tmp_path / "ask" / "evals" / "20261014T010101Z_a_low.json").write_text("[1]")
        (tmp_path / "ask" / "evals" / "20261015T010101Z_a_low.json").write_text('"text"')
        out = latest_summaries(self.paths(tmp_path))
        assert [s["exec_accuracy"] for s in out] == [0.1]

    def test_a_run_scored_by_an_older_compare_is_not_read(self, tmp_path):
        eval_file(
            tmp_path,
            "20261010T010101Z_a_low.json",
            "a/m",
            "low",
            acc=0.9,
            version=SCORER_VERSION - 1,
        )
        eval_file(tmp_path, "20261011T010101Z_b_low.json", "b/m", "low", acc=0.4)
        out = latest_summaries(self.paths(tmp_path))
        assert [s["model"] for s in out] == ["b/m"]
        # a file with no version at all is an old one
        path = tmp_path / "ask" / "evals" / "20261012T010101Z_c_low.json"
        data = json.loads((tmp_path / "ask" / "evals" / "20261011T010101Z_b_low.json").read_text())
        data["summary"]["model"] = "c/m"
        del data["config"]["scorer_version"]
        path.write_text(json.dumps(data))
        assert [s["model"] for s in latest_summaries(self.paths(tmp_path))] == ["b/m"]

    def test_the_newest_version_wins_over_an_older_file_of_the_same_model(self, tmp_path):
        eval_file(tmp_path, "20261010T010101Z_a_low.json", "a/m", "low", acc=0.9)
        eval_file(tmp_path, "20261011T010101Z_a_low.json", "a/m", "low", acc=0.2, version=1)
        assert [s["exec_accuracy"] for s in latest_summaries(self.paths(tmp_path))] == [0.9]

    def test_nothing_there(self, tmp_path):
        assert latest_summaries(self.paths(tmp_path)) == []

    def test_a_run_without_an_effort_is_its_own_key(self, tmp_path):
        eval_file(tmp_path, "20261010T010101Z_a_none.json", "a/m", None, acc=0.7)
        eval_file(tmp_path, "20261011T010101Z_a_low.json", "a/m", "low", acc=0.6)
        assert len(latest_summaries(self.paths(tmp_path))) == 2


# ---- run_eval over a fake engine -


class FakeEngine:
    """Answers each question from a table; reference queries come from `refs`."""

    def __init__(self, answers, refs, asof=(2026, 6)):
        self.answers = answers  # question -> AskResult | Exception
        self.refs = refs  # reference query -> QueryResult | Exception
        self.current = 2026
        self.asof = asof
        self.curated = ("stamp", 5)
        self.templates = {"t": object()}
        self.sources = SimpleNamespace(run=self._run)
        self.closed = False
        self.asked: list[str] = []
        self.ref_calls: list[tuple[str, str, dict, bool]] = []

    def _run(self, source, query, params=None, allow_maps=False):
        self.ref_calls.append((source, query, dict(params or {}), allow_maps))
        item = self.refs[query]
        if isinstance(item, Exception):
            raise item
        return item

    def ask(self, question):
        self.asked.append(question)
        item = self.answers[question]
        if isinstance(item, Exception):
            raise item
        return item

    def context(self):
        return {"graph_asof": {"season": 2026, "week": 6}}

    def close(self):
        self.closed = True


GOLDEN = [
    {"id": "g001", "question": "Q one?", "category": "sql", "source": "sql", "reference": "REF1"},
    {
        "id": "g002",
        "question": "Q two?",
        "category": "sql",
        "source": "sql",
        "reference": "REF2 $current_season",
    },
    {"id": "g003", "question": "Q three?", "category": "trick", "expect": "refuse"},
    {
        "id": "g004",
        "question": "Q four?",
        "category": "graph",
        "source": "graph",
        "reference": "REF4",
    },
]


@pytest.fixture
def harness(tmp_path, monkeypatch):
    golden = tmp_path / "golden.yaml"
    golden.write_text(yaml.safe_dump({"version": 1, "questions": GOLDEN}, sort_keys=False))
    monkeypatch.setattr(ev, "GOLDEN_PATH", golden)
    templates = tmp_path / "templates.yaml"
    templates.write_text(yaml.safe_dump({"version": 1, "templates": []}))
    monkeypatch.setattr("nflengine.ask.templates.TEMPLATES_PATH", templates)
    data = SimpleNamespace(ask=tmp_path / "data" / "ask")
    monkeypatch.setattr("nflengine.paths.ensure_data_root", lambda *a, **k: data)
    made = []
    harness_cfgs = []

    def fake_make_llm(cfg=None, model=None, **kw):
        llm = ScriptedLLM([])
        llm.model = model
        llm.effort = kw.get("reasoning_effort", "low")
        made.append((model, kw))
        harness_cfgs.append(cfg)
        return llm

    monkeypatch.setattr("nflengine.ask.llm.make_llm", fake_make_llm)
    return SimpleNamespace(tmp=tmp_path, data=data, made=made, golden=golden, cfgs=harness_cfgs)


def good(rows=None, **over):
    return ask_result(rows=rows or [[12]], timings={"total": 1.5}, **over)


def run(h, engine, **kw):
    lines: list[str] = []
    out = run_eval(
        "vendor/model",
        use_wandb=False,
        log=lines.append,
        engine_factory=lambda llm, cfg, paths: engine,
        **kw,
    )
    out["lines"] = lines
    return out


class TestRunEval:
    def engine(self):
        return FakeEngine(
            {
                "Q one?": good(),
                "Q two?": good(rows=[[99]]),
                "Q three?": ask_result(
                    status="refused",
                    rows=[],
                    refusal={"by": "router", "rule": "write", "message": "m"},
                ),
                "Q four?": good(rows=[["KC"]], source="graph"),
            },
            {
                "REF1": result(["wins"], [[12]]),
                "REF2 $current_season": result(["wins"], [[12]]),
                "REF4": Refused("cypher.write"),
            },
        )

    def test_it_runs_every_question_scores_it_and_saves_the_file(self, harness):
        eng = self.engine()
        out = run(harness, eng, reasoning_effort="low", launched_by="agent")
        scored = out["scored"]
        assert [s.id for s in scored] == ["g001", "g002", "g003", "g004"]
        assert [s.correct for s in scored] == [True, False, True, False]
        assert scored[3].reason.startswith("reference failed (Refused: cypher.write")
        s = out["summary"]
        assert (
            s["questions"] == 4 and s["model"] == "vendor/model" and s["reasoning_effort"] == "low"
        )
        assert s["stopped"] is None and s["exec_accuracy"] == pytest.approx(1 / 3)
        assert out["url"] is None and eng.closed is True
        # the reference was run with the reserved parameters bound
        assert ("sql", "REF2 $current_season", {"current_season": 2026}, True) in eng.ref_calls
        assert all(c[3] is True for c in eng.ref_calls)  # references may return maps
        assert eng.asked == ["Q one?", "Q two?", "Q three?", "Q four?"]

    def test_the_saved_file(self, harness):
        out = run(harness, self.engine(), reasoning_effort="low")
        f = out["file"]
        assert f.parent == harness.data.ask / "evals" and f.suffix == ".json"
        assert f.name.endswith("_vendor-model_low.json") and not f.with_suffix(".json.tmp").exists()
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["smoke"] is False and len(data["questions"]) == 4
        assert data["summary"]["model"] == "vendor/model"
        cfg = data["config"]
        assert cfg["model"] == "vendor/model" and cfg["golden_questions"] == 4
        assert cfg["graph_asof"] == {"season": 2026, "week": 6} and cfg["curated_week"] == 5
        assert cfg["bounds"] == BOUNDS and cfg["templates"] == 1
        assert len(cfg["prompt_hash"]) == 12 and len(cfg["golden_hash"]) == 12
        assert {"limits", "routing", "provider", "reasoning_effort"} <= set(cfg)
        assert cfg["scorer_version"] == SCORER_VERSION
        assert harness.made == [("vendor/model", {"reasoning_effort": "low"})]

    def test_the_file_keeps_each_result_for_rescoring(self, harness):
        out = run(harness, self.engine())
        tables = json.loads(out["file"].read_text(encoding="utf-8"))["tables"]
        assert set(tables) == {"g001", "g002", "g003", "g004"}
        assert tables["g001"] == {
            "got_columns": ["wins"],
            "got_rows": [[12]],
            "ref_columns": ["wins"],
            "ref_rows": [[12]],
        }
        assert tables["g004"]["ref_columns"] is None and tables["g004"]["ref_rows"] is None
        assert tables["g003"]["got_rows"] == []  # a refusal has no rows

    def test_only_the_first_rows_of_a_big_result_are_stored(self, harness):
        big = [[i] for i in range(STORED_ROWS + 50)]
        eng = FakeEngine({"Q one?": good(rows=big)}, {"REF1": result(["wins"], big)})
        out = run(harness, eng, ids=["g001"])
        t = json.loads(out["file"].read_text(encoding="utf-8"))["tables"]["g001"]
        assert len(t["got_rows"]) == STORED_ROWS and len(t["ref_rows"]) == STORED_ROWS
        assert out["scored"][0].correct is True  # scored on the full rows

    def test_the_run_is_readable_by_pick(self, harness):
        run(harness, self.engine())
        (s,) = latest_summaries(SimpleNamespace(ask=harness.data.ask))
        assert s["model"] == "vendor/model" and s["questions"] == 4

    def test_no_effort_means_the_default_is_not_passed(self, harness):
        run(harness, self.engine())
        assert harness.made == [("vendor/model", {})]

    def test_n_and_ids_limit_the_set(self, harness):
        eng = self.engine()
        out = run(harness, eng, n=2)
        assert [s.id for s in out["scored"]] == ["g001", "g002"]
        eng = self.engine()
        out = run(harness, eng, ids=["g003", "g001"])
        assert [s.id for s in out["scored"]] == ["g001", "g003"]  # the set's own order
        out = run(harness, self.engine(), ids=["g004", "g003"], n=1)
        assert [s.id for s in out["scored"]] == ["g003"]

    def test_the_log_has_a_line_per_question(self, harness):
        out = run(harness, self.engine())
        lines = [ln for ln in out["lines"] if ln.startswith("g00")]
        assert len(lines) == 4
        assert lines[0].startswith("g001 OK ") and lines[1].startswith("g002 BAD")
        assert "$0.00150" in lines[0] and "1.5s" in lines[0]

    def test_a_smoke_run_is_marked_in_its_file(self, harness):
        out = run(harness, self.engine(), smoke=True)
        assert out["file"].name.endswith("_smoke.json")
        assert json.loads(out["file"].read_text(encoding="utf-8"))["smoke"] is True

    def test_out_of_credits_stops_the_run_and_keeps_what_was_done(self, harness):
        eng = FakeEngine(
            {"Q one?": good(), "Q two?": OutOfCredits("402")},
            {"REF1": result(["wins"], [[12]]), "REF2 $current_season": result(["wins"], [[12]])},
        )
        out = run(harness, eng)
        assert [s.id for s in out["scored"]] == ["g001"]
        assert out["summary"]["stopped"] == "out of OpenRouter credits"
        assert any("stopped at g002" in ln for ln in out["lines"])
        assert eng.closed is True and eng.asked == ["Q one?", "Q two?"]
        data = json.loads(out["file"].read_text(encoding="utf-8"))
        assert data["summary"]["stopped"] == "out of OpenRouter credits"
        # and `nfl ask pick` ignores such a run
        assert latest_summaries(SimpleNamespace(ask=harness.data.ask)) == []

    def test_the_engine_is_closed_even_when_a_question_blows_up(self, harness):
        eng = FakeEngine({"Q one?": RuntimeError("boom")}, {"REF1": result(["wins"], [[12]])})
        with pytest.raises(RuntimeError):
            run(harness, eng)
        assert eng.closed is True

    def test_a_run_where_nothing_was_asked_writes_an_empty_summary(self, harness):
        out = run(harness, self.engine(), ids=["g999"])
        assert out["scored"] == [] and out["summary"]["stopped"] is None
        assert "questions" not in out["summary"]  # summarise() was skipped
        assert latest_summaries(SimpleNamespace(ask=harness.data.ask)) == []

    def test_the_reference_binds_the_graph_week(self, harness, monkeypatch):
        gold = [
            {
                "id": "g001",
                "question": "Q one?",
                "category": "graph",
                "source": "graph",
                "reference": "MATCH $asof_season $asof_week",
            }
        ]
        harness.golden.write_text(yaml.safe_dump({"version": 1, "questions": gold}))
        eng = FakeEngine(
            {"Q one?": good(source="graph")},
            {"MATCH $asof_season $asof_week": result(["wins"], [[12]], source="graph")},
        )
        run(harness, eng)
        assert eng.ref_calls[0][2] == {"asof_season": 2026, "asof_week": 6}


# ---- rescore -


class TestRescore:
    def write(self, tmp_path, questions, tables, summary=None):
        path = tmp_path / "run.json"
        path.write_text(
            json.dumps(
                {
                    "config": {"scorer_version": 1},
                    "summary": {
                        "model": "m/x",
                        "reasoning_effort": "low",
                        "stopped": None,
                        **(summary or {}),
                    },
                    "questions": [s.to_dict() for s in questions],
                    "tables": tables,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_stored_results_are_compared_again_with_todays_compare(self, tmp_path):
        old_wrong_now_right = S(id="g001", correct=False, reason="values differ")
        old_right_now_wrong = S(id="g002", correct=True, reason="match")
        tables = {
            "g001": {
                "got_columns": ["w"],
                "got_rows": [[14]],
                "ref_columns": ["w"],
                "ref_rows": [[14]],
            },
            "g002": {
                "got_columns": ["w"],
                "got_rows": [[14]],
                "ref_columns": ["w"],
                "ref_rows": [[15]],
            },
        }
        path = self.write(tmp_path, [old_wrong_now_right, old_right_now_wrong], tables)
        before = path.read_bytes()
        out = rescore(path)
        assert out["questions"] == 2 and out["exec_accuracy"] == 0.5
        assert (out["model"], out["reasoning_effort"], out["stopped"]) == ("m/x", "low", None)
        assert path.read_bytes() == before  # the file is not changed

    def test_results_without_stored_rows_keep_their_old_score(self, tmp_path):
        qs = [
            S(id="g001", correct=True),
            S(id="g002", correct=False),
            S(id="g003", expect="refuse", correct=True, status="refused"),
            S(id="g004", correct=False, status="error"),
        ]
        tables = {
            "g001": {"got_columns": None, "got_rows": [], "ref_columns": None, "ref_rows": None},
            "g004": {"got_columns": [], "got_rows": [], "ref_columns": ["w"], "ref_rows": [[1]]},
        }
        out = rescore(self.write(tmp_path, qs, tables))
        assert out["questions"] == 4
        assert out["exec_accuracy"] == pytest.approx(
            1 / 3
        )  # g001 kept, g002 kept, g004 still wrong
        assert out["refusal_accuracy"] == 1.0

    def test_an_old_file_without_tables(self, tmp_path):
        path = tmp_path / "old.json"
        path.write_text(
            json.dumps({"summary": {"model": "m"}, "questions": [S(correct=True).to_dict()]}),
            encoding="utf-8",
        )
        out = rescore(path)
        assert out["questions"] == 1 and out["exec_accuracy"] == 1.0 and out["model"] == "m"

    def test_no_questions(self, tmp_path):
        path = self.write(tmp_path, [], {})
        out = rescore(path)
        assert out["model"] == "m/x" and "questions" not in out


# ---- the column-name rule of compare (scorer version 3) ---------------------------------------


class TestCompareColumnNames:
    def test_swapped_labelled_columns_fail(self):
        # wins and losses carry the right numbers under the wrong names
        ok, why = compare(["wins", "losses"], [[4, 14]], ["wins", "losses"], [[14, 4]])
        assert ok is False and why.startswith("no column matches")
        assert compare(["losses", "wins"], [[14, 4]], ["wins", "losses"], [[14, 4]])[0] is False

    def test_the_same_labels_in_another_order_still_match(self):
        assert compare(["losses", "wins"], [[4, 14]], ["wins", "losses"], [[14, 4]])[0]

    def test_unlabelled_columns_match_by_value_in_any_order(self):
        assert compare(["a", "b"], [[4, 14]], ["wins", "losses"], [[14, 4]])[0]
        assert compare(["x", "y"], [[14, 4]], ["wins", "losses"], [[14, 4]])[0]

    def test_one_swapped_label_is_enough_to_fail(self):
        assert compare(["wins", "other"], [[4, 14]], ["wins", "losses"], [[14, 4]])[0] is False
        assert compare(["losses", "other"], [[14, 4]], ["wins", "losses"], [[14, 4]])[0] is False

    def test_a_reference_column_may_be_called_something_else_by_the_system(self):
        # a template returns the subject as `coach`; the reference's `coach` is the answer,
        # which the system calls `other`
        got = [["Andy Reid", "Mike McCarthy"], ["Andy Reid", "Nick Sirianni"]]
        ref = [["Mike McCarthy"], ["Nick Sirianni"]]
        assert compare(["coach", "other"], got, ["coach"], ref)[0]

    def test_names_are_compared_without_case_or_punctuation(self):
        assert compare(["Win_Pct", "Team"], [[0.5, "KC"]], ["winpct", "team"], [[0.5, "KC"]])[0]
        assert compare(["WINS", "losses"], [[4, 14]], ["wins", "Losses"], [[14, 4]])[0] is False
        assert compare(["win pct", "team"], [[1, 0.5]], ["win-pct", "team"], [[0.5, 1]])[0] is False

    def test_system_columns_named_like_no_reference_column_are_free(self):
        got = [["g1", "KC", 14]]
        assert compare(["id", "team", "wins"], got, ["team", "wins"], [["KC", 14]])[0]

    def test_a_namesake_column_with_the_right_values_matches(self):
        assert compare(["team", "wins"], [["KC", 14]], ["team", "wins"], [["KC", 14]])[0]
        got = [["KC", 14, 14]]
        assert compare(["team", "wins", "wins_2"], got, ["team", "wins"], [["KC", 14]])[0]

    def test_non_string_names_do_not_crash(self):
        assert compare([1, None], [[3, 4]], ["a", "b"], [[3, 4]])[0]


# ---- which runs `pick` may use ------------------------------------------------------------------


HASHES = {"prompt_hash": "p1", "golden_hash": "g1", "templates_hash": "t1"}


class TestWhichRunsCount:
    def paths(self, tmp_path):
        return SimpleNamespace(ask=tmp_path / "ask")

    def test_a_partial_run_is_never_an_entry(self, tmp_path):
        cfg = {"golden_total": 99, "partial": True}
        eval_file(tmp_path, "20261010T010101Z_a.json", "a/m", "low", questions=5, config=cfg)
        cfg = {"golden_total": 99, "partial": False}
        eval_file(tmp_path, "20261011T010101Z_b.json", "b/m", "low", questions=99, config=cfg)
        assert [s["model"] for s in latest_summaries(self.paths(tmp_path))] == ["b/m"]

    def test_a_run_of_fewer_questions_than_the_whole_set_is_skipped(self, tmp_path):
        cfg = {"golden_total": 99}
        eval_file(tmp_path, "20261010T010101Z_a.json", "a/m", "low", questions=40, config=cfg)
        assert latest_summaries(self.paths(tmp_path)) == []

    def test_the_old_field_is_the_fallback_for_the_total(self, tmp_path):
        eval_file(tmp_path, "20261010T010101Z_a.json", "a/m", "low", questions=10)
        path = tmp_path / "ask" / "evals" / "20261011T010101Z_b.json"
        data = json.loads((tmp_path / "ask" / "evals" / "20261010T010101Z_a.json").read_text())
        data["summary"]["model"] = "b/m"
        data["summary"]["questions"] = 7  # fewer than golden_questions (10), no golden_total
        path.write_text(json.dumps(data))
        assert [s["model"] for s in latest_summaries(self.paths(tmp_path))] == ["a/m"]

    def test_only_runs_made_with_todays_files_when_asked(self, tmp_path):
        eval_file(tmp_path, "20261010T010101Z_a.json", "a/m", "low", config=dict(HASHES))
        for i, key in enumerate(("prompt_hash", "golden_hash", "templates_hash")):
            cfg = {**HASHES, key: "other"}
            eval_file(tmp_path, f"2026101{i + 1}T010101Z_x.json", f"x{i}/m", "low", config=cfg)
        eval_file(tmp_path, "20261014T010101Z_e.json", "e/m", "low")  # no hashes at all
        paths = self.paths(tmp_path)
        assert [s["model"] for s in latest_summaries(paths, current=HASHES)] == ["a/m"]
        everyone = {s["model"] for s in latest_summaries(paths)}  # no `current`: no such filter
        assert everyone == {"a/m", "x0/m", "x1/m", "x2/m", "e/m"}

    def test_the_newest_comparable_run_wins_over_a_newer_incomparable_one(self, tmp_path):
        eval_file(tmp_path, "20261010T010101Z_a.json", "a/m", "low", acc=0.4, config=dict(HASHES))
        newer = {**HASHES, "prompt_hash": "newer-prompt"}
        eval_file(tmp_path, "20261011T010101Z_a.json", "a/m", "low", acc=0.9, config=newer)
        out = latest_summaries(self.paths(tmp_path), current=HASHES)
        assert [s["exec_accuracy"] for s in out] == [0.4]

    def test_current_hashes_are_the_prompt_golden_and_template_files(self):
        h = current_hashes()
        assert set(h) == {"prompt_hash", "golden_hash", "templates_hash"}
        assert all(len(v) == 12 and int(v, 16) >= 0 for v in h.values())
        from nflengine.ask.route import prompt_hash
        from nflengine.ask.templates import TEMPLATES_PATH

        assert h["prompt_hash"] == prompt_hash()
        assert h["golden_hash"] == file_hash(ev.GOLDEN_PATH)
        assert h["templates_hash"] == file_hash(TEMPLATES_PATH)

    def test_the_summary_carries_the_runs_file_and_question_count(self, tmp_path):
        cfg = {"golden_total": 12}
        eval_file(tmp_path, "20261010T010101Z_a.json", "a/m", "low", questions=12, config=cfg)
        (s,) = latest_summaries(self.paths(tmp_path))
        assert s["file"] == "20261010T010101Z_a.json" and s["golden_questions"] == 12


# ---- rescore: writing the new scores, and all the old runs --------------------------------------


def run_payload(version, *, with_tables=True, correct=False):
    qs = [
        S(id="g001", correct=correct, reason="values differ"),
        S(id="g002", expect="refuse", correct=True, status="refused"),
    ]
    one = {"got_columns": ["w"], "got_rows": [[14]], "ref_columns": ["w"], "ref_rows": [[14]]}
    old_summary = {**summarise(qs), "model": "m/x", "reasoning_effort": "low", "stopped": None}
    return {
        "config": {"scorer_version": version, "golden_total": 2, "golden_questions": 2},
        "summary": old_summary,
        "questions": [s.to_dict() for s in qs],
        "tables": {"g001": one} if with_tables else {},
        "smoke": False,
    }


class TestRescoreWrite:
    def test_write_updates_the_file_in_place_and_keeps_the_old_summary(self, tmp_path):
        path = tmp_path / "run.json"
        payload = run_payload(1)
        path.write_text(json.dumps(payload))
        out = rescore(path, write=True)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert out["exec_accuracy"] == 1.0
        assert data["summary"]["exec_accuracy"] == 1.0
        assert data["summary_before_rescore"]["exec_accuracy"] == 0.0
        assert data["config"]["scorer_version"] == SCORER_VERSION
        assert data["questions"][0]["correct"] is True
        assert data["questions"][0]["reason"] == "match"
        assert len(data["rescored_at"]) == 16 and data["rescored_at"].endswith("Z")
        assert not path.with_suffix(".json.tmp").exists()
        assert data["tables"] == payload["tables"]  # everything else is kept

    def test_without_write_the_file_is_untouched(self, tmp_path):
        path = tmp_path / "run.json"
        path.write_text(json.dumps(run_payload(1)))
        before = path.read_bytes()
        rescore(path)
        rescore(path, write=False)
        assert path.read_bytes() == before

    def test_a_run_missing_some_tables_is_not_rewritten(self, tmp_path):
        path = tmp_path / "run.json"
        path.write_text(json.dumps(run_payload(1, with_tables=False)))
        before = path.read_bytes()
        out = rescore(path, write=True)
        assert path.read_bytes() == before
        assert out["questions"] == 2  # the summary is still returned

    def test_a_run_whose_unanswered_questions_have_no_tables_is_still_complete(self, tmp_path):
        payload = run_payload(1)
        payload["questions"][0]["status"] = "error"  # nothing to compare for this one
        payload["questions"][0]["correct"] = False
        payload["tables"] = {}
        path = tmp_path / "run.json"
        path.write_text(json.dumps(payload))
        rescore(path, write=True)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["config"]["scorer_version"] == SCORER_VERSION


class TestRescoreAll:
    def evals(self, tmp_path):
        return tmp_path / "ask" / "evals"

    def make(self, tmp_path, name, payload):
        d = self.evals(tmp_path)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(payload))

    def test_it_rewrites_only_runs_of_an_older_scorer(self, tmp_path):
        self.make(tmp_path, "20261010T010101Z_old.json", run_payload(1))
        self.make(tmp_path, "20261011T010101Z_current.json", run_payload(SCORER_VERSION))
        self.make(tmp_path, "20261012T010101Z_none.json", {**run_payload(1), "config": {}})
        current_before = (self.evals(tmp_path) / "20261011T010101Z_current.json").read_bytes()
        lines: list[str] = []
        done = rescore_all(SimpleNamespace(ask=tmp_path / "ask"), log=lines.append)
        assert done == ["20261010T010101Z_old.json", "20261012T010101Z_none.json"]
        untouched = (self.evals(tmp_path) / "20261011T010101Z_current.json").read_bytes()
        assert untouched == current_before
        new = json.loads((self.evals(tmp_path) / "20261010T010101Z_old.json").read_text())
        assert new["config"]["scorer_version"] == SCORER_VERSION
        assert new["summary"]["exec_accuracy"] == 1.0
        assert any("20261010T010101Z_old.json: exec accuracy 0.0 -> 1.0" in ln for ln in lines)

    def test_a_second_pass_finds_nothing_to_do(self, tmp_path):
        self.make(tmp_path, "20261010T010101Z_old.json", run_payload(1))
        paths = SimpleNamespace(ask=tmp_path / "ask")
        assert rescore_all(paths, log=lambda s: None) == ["20261010T010101Z_old.json"]
        assert rescore_all(paths, log=lambda s: None) == []

    def test_runs_without_their_tables_are_reported_not_rewritten(self, tmp_path):
        self.make(tmp_path, "20261010T010101Z_old.json", run_payload(1, with_tables=False))
        lines: list[str] = []
        done = rescore_all(SimpleNamespace(ask=tmp_path / "ask"), log=lines.append)
        assert done == []
        assert any("not rewritten: tables missing" in ln for ln in lines)
        data = json.loads((self.evals(tmp_path) / "20261010T010101Z_old.json").read_text())
        assert data["config"]["scorer_version"] == 1

    def test_broken_and_foreign_files_are_skipped(self, tmp_path):
        d = self.evals(tmp_path)
        d.mkdir(parents=True)
        (d / "a.json").write_text("{broken")
        (d / "b.json").write_text("[1, 2]")
        (d / "c.json").write_text('"text"')
        assert rescore_all(SimpleNamespace(ask=tmp_path / "ask"), log=lambda s: None) == []

    def test_no_evals_folder(self, tmp_path):
        assert rescore_all(SimpleNamespace(ask=tmp_path / "nope"), log=lambda s: None) == []


# ---- run_eval: the config a run carries ---------------------------------------------------------


class TestRunEvalConfig:
    def engine(self):
        return TestRunEval().engine()

    def test_a_full_run_is_complete_and_not_partial(self, harness):
        out = run(harness, self.engine())
        cfg = json.loads(out["file"].read_text(encoding="utf-8"))["config"]
        assert cfg["golden_total"] == 4 and cfg["golden_questions"] == 4
        assert cfg["partial"] is False

    @pytest.mark.parametrize("kw", [{"n": 2}, {"ids": ["g001", "g003"]}])
    def test_n_and_ids_make_a_partial_run(self, harness, kw):
        out = run(harness, self.engine(), **kw)
        cfg = json.loads(out["file"].read_text(encoding="utf-8"))["config"]
        assert cfg["golden_total"] == 4 and cfg["golden_questions"] == 2
        assert cfg["partial"] is True
        # a partial run is never a bake-off entry
        assert latest_summaries(SimpleNamespace(ask=harness.data.ask)) == []

    def test_the_run_carries_no_wandb_id_without_wandb(self, harness):
        out = run(harness, self.engine())
        assert json.loads(out["file"].read_text(encoding="utf-8"))["wandb_run_id"] is None

    def test_max_tokens_overrides_the_config_for_the_model_and_the_record(self, harness):
        from nflengine.ask.config import ask_config

        out = run(harness, self.engine(), max_tokens=9000)
        cfg = json.loads(out["file"].read_text(encoding="utf-8"))["config"]
        assert cfg["max_output"] == 9000
        assert harness.cfgs[0].max_tokens == 9000
        out = run(harness, self.engine())
        data = json.loads(out["file"].read_text(encoding="utf-8"))
        # without the option, the run records ask.llm's own cap (whatever settings.yaml says)
        assert data["config"]["max_output"] == ask_config().llm.max_tokens != 9000

    def test_the_run_config_passes_the_wandb_secret_check(self, harness):
        # every eval run died at wandb.init on "max_tokens" (a "token" key) after the bake-off
        from nflengine.tracking import assert_no_secrets

        out = run(harness, self.engine(), max_tokens=16000, llm_timeout_s=120)
        assert_no_secrets(json.loads(out["file"].read_text(encoding="utf-8"))["config"])


class TestScoreAndPickMore:
    def test_clarify_needs_a_clarification_even_without_required_choices(self):
        g = golden_q(expect="clarify", reference=None, category="trick")
        s = score(g, ask_result(status="answered"), None)
        assert s.correct is False and s.reason == "no clarify (answered)"
        s = score(g, ask_result(status="refused", rows=[]), None)
        assert s.correct is False

    def test_a_tie_goes_to_the_cheaper_even_when_it_is_slower(self):
        slow_cheap = summary("slow", 0.800, p95=12.0, cost=0.0001)
        fast_dear = summary("fast", 0.798, p95=3.0, cost=0.0019)
        assert pick([slow_cheap, fast_dear]) is slow_cheap
        assert pick([fast_dear, slow_cheap]) is slow_cheap

    def test_the_stored_rows_limit_is_two_hundred(self):
        assert STORED_ROWS == 200
