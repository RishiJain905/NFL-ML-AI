"""AE00 guard red-team, Cypher side: the text scanner (`cypher_skeleton`, `strip_cypher_comments`,
`check_cypher`), the EXPLAIN check and the READ run (`explain_cypher`, `run_cypher`) over a fake
driver. Everything here is offline; the live probes are in test_ask_guard_live.py.

The scanner's job: what it lets through is what the server sees (the cleaned text, comments
replaced by a space, is what runs). So the risk is a spelling the scanner reads differently from
Neo4j. Two real ones were found by this red-team (backslash-u escapes outside strings, and a
namespace after the `..` of a slice); both are fixed and kept as regression tests (see
`TestScannerVsServer`).
"""

from __future__ import annotations

import datetime as dt
import decimal

import neo4j
import pytest
from ask_fakes import (
    ClientError,
    CypherSyntaxError,
    FakeDriver,
    ServiceUnavailable,
    plan,
)
from neo4j.graph import Graph, Node, Relationship
from neo4j.time import Date, DateTime, Duration, Time

from nflengine.ask.guard import (
    MAX_QUERY_CHARS,
    MAX_RAW_CHARS,
    QueryFailed,
    QueryResult,
    Refused,
    Sources,
    check_cypher,
    cypher_skeleton,
    explain_cypher,
    run_cypher,
    strip_cypher_comments,
)
from nflengine.graph.queries import QUERY_DIR

BS = chr(92)


def refused_rule(q: str) -> str:
    with pytest.raises(Refused) as exc:
        check_cypher(q)
    return exc.value.rule


# ---- the scanner ----------------------------------------------------------------------------


class TestSkeleton:
    def test_strings_are_emptied_in_both_quote_styles(self):
        sk = cypher_skeleton("MATCH (n) WHERE n.a = 'x;y' AND n.b = \"DELETE\" RETURN n")
        assert sk == "MATCH (n) WHERE n.a = '' AND n.b = '' RETURN n"

    @pytest.mark.parametrize(
        "text, skeleton",
        [
            (r"RETURN 'it\'s' AS a", "RETURN '' AS a"),  # an escaped quote does not close it
            (r'RETURN "say \"hi\"; DROP" AS a', "RETURN '' AS a"),
            (r"RETURN 'ends with a backslash \\' AS a", "RETURN '' AS a"),  # \\ then the close
            ("RETURN 'a\nb' AS a", "RETURN '' AS a"),  # a newline inside a string
            ("RETURN '' AS a, \"\" AS b", "RETURN '' AS a, '' AS b"),
            ("RETURN '//' AS a, '/*' AS b, '*/' AS c", "RETURN '' AS a, '' AS b, '' AS c"),
        ],
    )
    def test_string_edge_cases(self, text, skeleton):
        assert cypher_skeleton(text) == skeleton

    def test_comments_become_a_space(self):
        assert cypher_skeleton("RETURN 1 // note\n, 2 AS b") == "RETURN 1  \n, 2 AS b"
        assert cypher_skeleton("RETURN/**/1") == "RETURN 1"
        assert cypher_skeleton("RETURN 1 // no newline at the end") == "RETURN 1  "

    def test_a_comment_cannot_join_two_tokens(self):
        # the server reads `CRE` and `ATE` as two words; the cleaned text must keep them apart
        cleaned = check_cypher("MATCH (n) RETURN n.name AS CRE/**/ATE")
        assert "CREATE" not in cleaned.upper()
        assert check_cypher("RETURN 1 AS CRE//x\nATE") == "RETURN 1 AS CRE \nATE"

    def test_comment_markers_inside_strings_are_text(self):
        q = "RETURN 'http://x.y/*not a comment*/' AS u"
        assert check_cypher(q) == q

    def test_a_quote_inside_a_comment_does_not_open_a_string(self):
        assert check_cypher("RETURN 1 AS a // it's a comment\n") == "RETURN 1 AS a"
        assert check_cypher("RETURN /* don't */ 1 AS a") == "RETURN   1 AS a"

    def test_backtick_names_are_marked(self):
        assert cypher_skeleton("MATCH (n:`Weird Label`) RETURN n") == "MATCH (n:``) RETURN n"
        assert cypher_skeleton("RETURN `a``b`") == "RETURN ``"  # `` is an escaped backtick

    @pytest.mark.parametrize(
        "text",
        [
            "RETURN 'abc",
            'RETURN "abc',
            r"RETURN 'abc\'",  # the escaped quote leaves it open
            "RETURN 1 /* never closed",
            "RETURN `abc",
            "MATCH (n) WHERE n.name = 'it's' RETURN n",  # an unescaped apostrophe
        ],
    )
    def test_unterminated_things_are_refused(self, text):
        with pytest.raises(Refused) as exc:
            cypher_skeleton(text)
        assert exc.value.rule == "cypher.unterminated"
        assert refused_rule(text) == "cypher.unterminated"

    def test_strip_comments_keeps_text_verbatim_and_drops_blank_lines(self):
        text = "// header\nMATCH (n:Team)  // why\n\n  /* x */\nRETURN n.name AS 'q//x'.x\n"
        out = strip_cypher_comments(text)
        assert "header" not in out and "why" not in out
        assert "'q//x'" in out
        assert "\n\n" not in out
        assert out.splitlines()[0] == "MATCH (n:Team)"

    def test_strip_comments_refuses_an_unterminated_string(self):
        with pytest.raises(Refused):
            strip_cypher_comments("RETURN 'abc")


# ---- check_cypher: refusals -----------------------------------------------------------------

WRITES = [
    "CREATE (n:Hack {x: 1})",
    "MATCH (n) SET n.x = 1",
    "MATCH (n) DELETE n",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n) REMOVE n.x",
    "MATCH (n) FOREACH (x IN [1] | SET n.x = x)",
    "MERGE (n:Team {team_id: 'ZZZ'})",
    "MATCH (n) MERGE (m:Hack) ON CREATE SET m.x = 1",
    "DROP INDEX some_index",
    "DROP CONSTRAINT c IF EXISTS",
    "ALTER USER neo4j SET PASSWORD 'x'",
    "GRANT ROLE admin TO bob",
    "DENY READ {*} ON GRAPH * TO bob",
    "REVOKE ROLE admin FROM bob",
    "RENAME ROLE a TO b",
    "TERMINATE TRANSACTIONS 'neo4j-transaction-1'",
    "STOP DATABASE neo4j",
    "START DATABASE neo4j",
    "INSERT (n:Hack)",
    "CALL { CREATE (n:Hack) } RETURN 1",
    "CALL (x) { SET x.a = 1 } RETURN 1",
    "MATCH (n) WITH n LIMIT 1 SET n.x = 1 RETURN n.x",
    "UNWIND [1] AS x CREATE (:Hack {x: x})",
    "RETURN 1 AS a UNION CREATE (n:Hack)",
]


class TestWriteClauses:
    @pytest.mark.parametrize("q", WRITES)
    def test_refused(self, q):
        assert refused_rule(q) == "cypher.write"

    @pytest.mark.parametrize("q", WRITES)
    def test_refused_in_lower_and_mixed_case(self, q):
        assert refused_rule(q.lower()) == "cypher.write"
        assert refused_rule(q.title()) == "cypher.write"

    @pytest.mark.parametrize("q", WRITES[:8])
    def test_refused_across_newlines_tabs_and_comments(self, q):
        assert refused_rule("MATCH (a) /* c */\n\t" + q) == "cypher.write"
        assert refused_rule("// head\n" + q.replace(" ", "\n")) == "cypher.write"
        assert refused_rule(q.replace(" ", "/**/ ")) == "cypher.write"

    def test_the_detail_names_the_keyword(self):
        with pytest.raises(Refused) as exc:
            check_cypher("match (n) detach delete n")
        assert exc.value.detail == "DETACH"
        assert exc.value.message.startswith("Writing to the graph")

    def test_a_comment_between_the_letters_does_not_hide_it_from_the_server_view(self):
        # `CRE/**/ATE` is two words to Neo4j, so it is allowed to pass as text, but what runs
        # is the cleaned text: it must not have become CREATE
        assert "CREATE" not in check_cypher("MATCH (n) RETURN n.name AS CRE/**/ATE").upper()

    @pytest.mark.parametrize(
        "q",
        [
            "LOAD CSV FROM 'file:///etc/passwd' AS r RETURN r",
            "load csv with headers from 'https://evil.example/x.csv' as r return r",
            "LOAD/**/CSV FROM 'x' AS r RETURN r",
            "LOAD\nCSV FROM 'x' AS r RETURN r",
            "USING PERIODIC COMMIT LOAD CSV FROM 'x' AS r RETURN r",
        ],
    )
    def test_load_csv(self, q):
        assert refused_rule(q) == "cypher.banned"

    @pytest.mark.parametrize(
        "q",
        [
            "USE neo4j MATCH (n) RETURN n",
            "use system SHOW USERS",
            "SHOW USERS",
            "SHOW PROCEDURES",
            "SHOW SETTINGS",
            "show databases",
            "EXPLAIN MATCH (n) RETURN n",
            "PROFILE MATCH (n) RETURN n",
            "profile match (n) return n",
            "CYPHER 5 MATCH (n) RETURN n",
            "CYPHER runtime=slotted MATCH (n) RETURN n",
            "cypher planner=cost MATCH (n) RETURN n",
            "CALL { MATCH (n) RETURN n } IN TRANSACTIONS",
            "CALL { MATCH (n) RETURN n } IN TRANSACTIONS OF 10 ROWS",
        ],
    )
    def test_banned_words(self, q):
        assert refused_rule(q) == "cypher.banned"

    @pytest.mark.parametrize(
        "q",
        [
            "CALL db.labels()",
            "CALL db.labels() YIELD label RETURN label",
            "call db.labels()",
            "OPTIONAL CALL db.labels()",
            "CALL\ndb.labels()",
            "CALL/**/db.labels()",
            "CALL /* x */ // y\n db.labels()",
            "CALL apoc.help('x')",
            "CALL dbms.listConfig()",
            "CALL gds.graph.project('g', '*', '*')",
            "CALL apoc.util.sleep(5000)",
            "CALL apoc.export.csv.all('x.csv', {})",
            "CALL apoc.load.json('file:///x.json') YIELD value RETURN value",
            "MATCH (n) CALL db.labels() YIELD label RETURN label",
            "MATCH (n) WITH n CALL custom.proc(n) YIELD x RETURN x",
        ],
    )
    def test_procedure_calls(self, q):
        # a namespaced procedure name is caught by the namespace rule first
        assert refused_rule(q) in ("cypher.procedure", "cypher.namespace")

    @pytest.mark.parametrize(
        "q, ns",
        [
            ("RETURN apoc.version() AS v", "apoc"),
            ("RETURN gds.version() AS v", "gds"),
            ("RETURN APOC.version() AS v", "apoc"),
            ("RETURN Gds.version() AS v", "gds"),
            ("RETURN apoc . version() AS v", "apoc"),
            ("RETURN apoc\n.version() AS v", "apoc"),
            ("RETURN apoc/**/.version() AS v", "apoc"),
            ("RETURN apoc //x\n.version() AS v", "apoc"),
            ("RETURN apoc.text.join(['a'], ',') AS v", "apoc"),
            ("RETURN apoc.cypher.runFirstColumnSingle('RETURN 1', {}) AS v", "apoc"),
            ("RETURN dbms.components() AS v", "dbms"),
            ("RETURN db.info() AS v", "db"),
            ("RETURN genai.vector.encode('x') AS v", "genai"),
            ("MATCH (n) WHERE apoc.coll.contains([1], n.x) RETURN n", "apoc"),
            ("RETURN [x IN [1] | apoc.version()] AS v", "apoc"),
            ("RETURN {a: apoc.version()} AS v", "apoc"),
            ("CALL (x) { RETURN apoc.version() AS v } RETURN v", "apoc"),
            ("RETURN CASE WHEN true THEN gds.version() END AS v", "gds"),
            ("WITH 1 AS x RETURN coalesce(null, apoc.version()) AS v", "apoc"),
            ("RETURN 1 + apoc.version() AS v", "apoc"),
            ("RETURN -apoc.version() AS v", "apoc"),
            ("RETURN [apoc.version()] AS v", "apoc"),
        ],
    )
    def test_namespaced_functions(self, q, ns):
        with pytest.raises(Refused) as exc:
            check_cypher(q)
        assert exc.value.rule == "cypher.namespace"
        assert exc.value.detail == ns

    @pytest.mark.parametrize("q", [":begin", ":use neo4j", "  :param x => 1", "\n:help"])
    def test_client_commands(self, q):
        assert refused_rule(q) == "cypher.command"

    def test_client_command_after_a_comment(self):
        assert refused_rule("/* x */ :help") == "cypher.command"
        assert refused_rule("// x\n:help") == "cypher.command"

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH (n) RETURN n; MATCH (m) RETURN m",
            "RETURN 1; CREATE (n)",
            "RETURN 1;\n;RETURN 2",
            "RETURN 1; // c\nRETURN 2",
            "RETURN 1;RETURN 2;",
            "RETURN 1 ; RETURN 2",
        ],
    )
    def test_multiple_statements(self, q):
        assert refused_rule(q) == "cypher.multiple_statements"

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH (n:`Team`) RETURN n.name",
            "MATCH (n) RETURN n.`name`",
            "RETURN 1 AS `x`",
            "MATCH (n:Team) RETURN `apoc`.version()",
            "CALL `db`.`labels`()",
            "RETURN `a``b`",
        ],
    )
    def test_backticks_are_refused_everywhere(self, q):
        assert refused_rule(q) == "cypher.backticks"

    def test_backticks_in_a_string_or_comment_are_fine(self):
        assert check_cypher("RETURN '`' AS a // `x`") == "RETURN '`' AS a"

    @pytest.mark.parametrize(
        "q", ["", "   ", "\n\t", ";", " ; ; ", "// only a comment", "/* x */", None]
    )
    def test_empty(self, q):
        assert refused_rule(q) == "cypher.empty"

    def test_length_caps(self):
        base = "RETURN '' AS a"
        pad = MAX_QUERY_CHARS - len(base)
        ok = f"RETURN '{'a' * pad}' AS a"
        assert len(ok) == MAX_QUERY_CHARS
        assert check_cypher(ok) == ok
        assert refused_rule(f"RETURN '{'a' * (pad + 1)}' AS a") == "cypher.too_long"

    def test_long_comments_do_not_count_but_a_huge_text_does(self):
        commented = "// " + "x" * 3000 + "\n" + "RETURN 1 AS a /* " + "y" * 10000 + " */"
        assert len(commented) > MAX_QUERY_CHARS
        assert check_cypher(commented).startswith("RETURN 1 AS a")
        assert refused_rule("RETURN 1 AS a // " + "z" * MAX_RAW_CHARS) == "cypher.too_long"

    def test_very_long_but_ordinary_text_is_refused_fast(self):
        assert refused_rule("MATCH (n) RETURN n.name " * 3000) == "cypher.too_long"

    def test_a_trailing_semicolon_and_comment_is_fine(self):
        assert check_cypher("RETURN 1 AS a; // done") == "RETURN 1 AS a"
        assert check_cypher("RETURN 1 AS a;\n/* done */") == "RETURN 1 AS a"
        assert check_cypher("RETURN 1 AS a ;;") == "RETURN 1 AS a"

    def test_a_semicolon_in_a_string_is_not_a_second_statement(self):
        assert check_cypher("RETURN 'a;b' AS x") == "RETURN 'a;b' AS x"


# ---- spellings the server reads differently from the scanner (found by this red-team) -------


class TestScannerVsServer:
    """Verified live on Neo4j 5.26 (2026-10-17, see test_ask_guard_live.py): the server turns a
    backslash-u escape into its character even outside strings, and `[a..b]` slices put a
    second dot before the name. Both hid `apoc.` from the namespace rule (BUG 1, BUG 2; fixed).
    The escapes are built from `BS` so that no layer of tooling can decode them."""

    @pytest.mark.parametrize(
        "q",
        [
            f"RETURN {BS}u0061poc.version() AS v",
            f"RETURN {BS}u0061poc.cypher.runFirstColumnSingle('RETURN 1 AS x', {{}}) AS r",
            f"RETURN {BS}u0067ds.version() AS v",
            f"MATCH (n) {BS}u0053ET n.x = 1",
            f"{BS}u004coAD CSV FROM 'file:///x.csv' AS r RETURN r",
            f"RETURN 1 AS {BS}u0078",
            f"RETURN {BS}U00000061poc.version() AS v",
            f"RETURN 1 AS a {BS}",
        ],
        ids=["apoc-fn", "apoc-nested-cypher", "gds-fn", "SET", "LOAD", "alias", "long-U", "bare"],
    )
    def test_a_backslash_outside_strings_is_refused(self, q):
        assert refused_rule(q) == "cypher.escape"

    @pytest.mark.parametrize(
        "q",
        [
            # Neo4j decodes backslash-u in the WHOLE text before lexing (Sol, AE00): this
            # string is closed by the decoded quote and what follows is code
            f"RETURN '{BS}u0027 + apoc.version() + {BS}u0027' AS v",
            f"RETURN '{BS}u0041 and {BS}u0027' AS a",
            f"RETURN 1 AS a // {BS}u0041",
            f"RETURN 1 AS a /* {BS}u0041 */",
            # an escaped newline ends a // comment on the server
            f"RETURN 1 AS a // {BS}u000a CREATE (n:Hack)",
            f'RETURN "{BS}u0022 + apoc.version() + {BS}u0022" AS v',
            f"RETURN 'x{BS}U00000027 y' AS v",
            f"RETURN 'a {BS}{BS}u0041' AS v",  # two backslashes then u
            f"RETURN 'a {BS}{BS}{BS}u0041' AS v",
            f"MATCH (n) WHERE n.name = '{BS}u0027 OR true //' RETURN n.name",
            f"RETURN 'unterminated {BS}u0041",  # the escape is named before the open string
        ],
        ids=[
            "closing-quote-then-apoc",
            "in-a-string",
            "in-a-line-comment",
            "in-a-block-comment",
            "escaped-newline-in-comment",
            "double-quote-variant",
            "long-U",
            "two-backslashes",
            "three-backslashes",
            "or-true",
            "unterminated",
        ],
    )
    def test_a_unicode_escape_is_refused_anywhere_even_in_strings_and_comments(self, q):
        assert refused_rule(q) == "cypher.escape"

    @pytest.mark.parametrize(
        "q",
        [
            f"RETURN 'it{BS}'s' AS a",
            f"RETURN 'a{BS}{BS}b' AS a",
            f"RETURN 'tab{BS}there{BS}n' AS a",
            f'RETURN "say {BS}"hi{BS}"" AS a',
            f"RETURN 'a {BS}x' AS a",  # an odd escape: the server will say so, the scanner is fine
            f"RETURN 'path C:{BS}temp{BS}me' AS a",
            f"RETURN 1 AS a // a backslash {BS} in a comment",
            f"RETURN 1 AS a /* {BS}n */",
        ],
    )
    def test_a_plain_backslash_inside_a_string_or_comment_is_still_fine(self, q):
        assert check_cypher(q)

    def test_any_backslash_before_a_u_is_refused_even_when_it_is_no_valid_escape(self):
        # conservative on purpose: a Windows path with a Users folder is no escape (no hex
        # digits), but the rule is "a backslash then u / U", so the scanner never has to guess
        assert refused_rule(f"RETURN 'C:{BS}Users' AS a") == "cypher.escape"
        assert refused_rule(f"RETURN 'a{BS}uzzzz' AS a") == "cypher.escape"

    def test_a_backslash_outside_a_string_stays_refused_whatever_follows(self):
        assert refused_rule(f"RETURN 1 AS a {BS}n") == "cypher.escape"
        assert refused_rule(f"RETURN {BS}") == "cypher.escape"

    @pytest.mark.parametrize(
        "q",
        [
            "RETURN [1,2,3][0..apoc.cypher.runFirstColumnSingle('RETURN 1 AS x', {})] AS s",
            "RETURN [1,2,3][0 .. apoc.coll.min([1,2])] AS s",
            "RETURN [1,2,3][0 ..\n apoc.coll.min([1,2])] AS s",
            "RETURN [1,2,3][0../**/gds.version()] AS s",
            "RETURN [1,2,3][..apoc.coll.min([1,2])] AS s",
            "RETURN [1,2,3][apoc.coll.min([1,2])..2] AS s",
        ],
    )
    def test_namespace_after_slice_dots_is_refused(self, q):
        assert refused_rule(q) == "cypher.namespace"

    @pytest.mark.parametrize(
        "q",
        [
            "RETURN [1,2,3][0..1 + apoc.coll.min([1,2])] AS s",
            "RETURN [1,2,3][0..size([1])] AS s, apoc.version() AS v",
            "MATCH (a)-[:PLAYED_FOR*1..3]->(b) RETURN apoc.version()",
        ],
    )
    def test_a_namespace_is_refused_wherever_it_hides(self, q):
        assert refused_rule(q) == "cypher.namespace"

    def test_a_property_named_like_a_namespace_is_still_allowed(self):
        assert check_cypher("MATCH (n) RETURN n.apoc AS a, n.db AS b, n.x.gds AS c")
        assert check_cypher("RETURN {apoc: 1}.apoc AS a")

    @pytest.mark.parametrize(
        "q",
        [
            "RETURN foo.bar(1) AS v",
            "RETURN custom.fn() AS v",
            "RETURN util.md5('x') AS v",
            "RETURN text . join(['a'], ',') AS v",
            "RETURN a.b.c.d(1) AS v",
            "RETURN n.x /*c*/ . y (1) AS v",
            "RETURN dates.truncate('month', date()) AS v",
        ],
    )
    def test_only_neo4js_own_namespaced_functions_are_allowed(self, q):
        assert refused_rule(q) == "cypher.namespace"

    @pytest.mark.parametrize(
        "q",
        [
            "RETURN date.truncate('month', date()) AS v",
            "RETURN datetime.truncate('day', datetime()) AS v",
            "RETURN localdatetime.truncate('day', localdatetime()) AS v",
            "RETURN duration.between(date('2026-01-01'), date('2026-02-01')) AS v",
            "RETURN point.distance(point({x: 0, y: 0}), point({x: 1, y: 1})) AS v",
            "RETURN DATE.truncate('month', date()) AS v",
            "RETURN vector.similarity.cosine([1.0, 0.0], [0.0, 1.0]) AS v",
        ],
    )
    def test_neo4js_own_namespaced_functions_stay_allowed(self, q):
        assert check_cypher(q)


# ---- check_cypher: what must stay allowed ---------------------------------------------------


ALLOWED = [
    "MATCH (n:Team) RETURN n.name AS name ORDER BY name LIMIT 5",
    "RETURN {set: 1, create: 2, delete: 3, remove: 4, call: 5, load: 6, merge: 7} AS m",
    "RETURN { set : 1 } AS m",
    "MATCH (n:Set) RETURN n.name",
    "MATCH (n:Create:Merge) RETURN n.name",
    "MATCH (n : Set) RETURN n.name",
    "MATCH (a)-[:DELETE]->(b) RETURN a.name",
    "MATCH (a)-[r:REMOVE]->(b) RETURN type(r)",
    "MATCH (n) RETURN n.remove, n.set, n.create, n.call, n.load, n.use, n.show",
    "MATCH (n) RETURN n.\nset",
    "MATCH (n) WHERE n.x = $create AND n.y = $set AND n.z = $load RETURN n.name",
    "MATCH (t:Team) CALL (t) { MATCH (t)-[:PLAYED_IN]->(g:Game) RETURN count(g) AS games } "
    "RETURN t.team_id, games",
    "MATCH (t:Team) CALL { WITH t MATCH (t)-[:PLAYED_IN]->(g:Game) RETURN count(g) AS games } "
    "RETURN t.team_id, games",
    "CALL () { RETURN 1 AS x } RETURN x",
    "call (n) { return 1 as x } return x",
    "MATCH (n:Team) WHERE EXISTS { (n)-[:PLAYED_IN]->() } RETURN n.name",
    "MATCH (n:Team) RETURN COUNT { (n)-[:PLAYED_IN]->() } AS c",
    "MATCH (n:Team) RETURN n.name UNION MATCH (m:Player) RETURN m.name",
    "MATCH (n:Team) RETURN n.name UNION ALL MATCH (m:Player) RETURN m.name",
    "OPTIONAL MATCH (n:Team) WITH n UNWIND [1] AS x RETURN n.name SKIP 1 LIMIT 2",
    "MATCH (n) WHERE n.name = 'DELETE; DROP apoc. CREATE LOAD CSV' RETURN n",
    'MATCH (n) WHERE n.name = "CALL db.labels()" RETURN n',
    "MATCH (n) WHERE n.url = 'http://x//y' RETURN n",
    "MATCH (n) RETURN n.created_at, n.deleted, n.setting, n.offset, n.loader, n.useful",
    "MATCH (settled:Team) RETURN settled.name AS dropoff, 1 AS showcase, 2 AS caller",
    "MATCH (n) // DELETE everything\nRETURN n.name /* CREATE (x) */",
    "MATCH (n) RETURN CASE WHEN n.x > 1 THEN 'a' ELSE 'b' END AS c",
    "MATCH (n:Team) RETURN n.name AS name;",
    "RETURN date('2026-10-17') AS d",
    "RETURN 'a' + 'b' AS s, size([1,2]) AS n, toString(1) AS t",
    "MATCH p = (a)-[:PLAYED_FOR*1..3]->(b) RETURN length(p)",
    "MATCH (a)-[r:PLAYED_FOR*2..]->(b) RETURN count(r)",
    "RETURN [1,2,3][0..2] AS s",
]


class TestStaysAllowed:
    @pytest.mark.parametrize("q", ALLOWED)
    def test_allowed(self, q):
        assert check_cypher(q)

    def test_the_cleaned_text_drops_comments_and_the_final_semicolon(self):
        assert check_cypher("MATCH (n) // x\nRETURN n.name;") == "MATCH (n)  \nRETURN n.name"

    def test_library_files_are_all_allowed(self):
        files = sorted(QUERY_DIR.glob("*.cypher"))
        assert len(files) == 16
        for f in files:
            text = f.read_text(encoding="utf-8")
            out = check_cypher(text)
            assert out and "//" not in out.replace("://", "")  # comments are gone
            # the comment-free text is what a template carries, and it passes too
            assert check_cypher(strip_cypher_comments(text))


# ---- explain_cypher / run_cypher over a fake driver -----------------------------------------


class TestExplain:
    def test_a_read_plan_passes_and_runs_in_a_read_session(self):
        drv = FakeDriver()
        explain_cypher(drv, "MATCH (n) RETURN n.name", {"a": 1}, database="neo4j")
        assert drv.sessions[0]["default_access_mode"] == neo4j.READ_ACCESS
        assert drv.sessions[0]["database"] == "neo4j"
        query, params, _ = drv.session_runs[0]
        assert query == "EXPLAIN MATCH (n) RETURN n.name"
        assert params == {"a": 1}

    @pytest.mark.parametrize("qtype", ["w", "rw", "s", "sw", None, ""])
    def test_a_plan_that_is_not_read_is_refused(self, qtype):
        drv = FakeDriver(query_type=qtype)
        with pytest.raises(Refused) as exc:
            explain_cypher(drv, "MATCH (n) RETURN n")
        assert exc.value.rule == "cypher.plan_not_read"
        assert exc.value.detail == (qtype or None)

    @pytest.mark.parametrize(
        "operators",
        [
            ("ProduceResults@neo4j", "ProcedureCall@neo4j"),
            ("ProduceResults@neo4j", "ProcedureCall"),
            ("ProduceResults@neo4j", "LoadCSV@neo4j"),
            ("ProduceResults@neo4j", "Apply@neo4j", "Eager@neo4j", "LoadCSV@neo4j"),
            ("ProcedureCall@neo4j",),
            ("LoadCSV",),
            ("ProduceResults@neo4j", "ProcedureCall(db.labels)@neo4j"),
        ],
    )
    def test_banned_operators_anywhere_in_the_plan(self, operators):
        drv = FakeDriver(explain_plan=plan(*operators))
        with pytest.raises(Refused) as exc:
            explain_cypher(drv, "MATCH (n) RETURN n")
        assert exc.value.rule == "cypher.plan_operator"

    def test_ordinary_operators_pass(self):
        ops = ("ProduceResults@neo4j", "Sort@neo4j", "Filter@neo4j", "NodeByLabelScan@neo4j")
        explain_cypher(FakeDriver(explain_plan=plan(*ops)), "MATCH (n) RETURN n")

    def test_the_banned_operator_in_a_sibling_branch_is_found(self):
        tree = {
            "operatorType": "ProduceResults@neo4j",
            "children": [
                {"operatorType": "Apply@neo4j", "children": []},
                {"operatorType": "CartesianProduct@neo4j", "children": [plan("Filter", "LoadCSV")]},
            ],
        }
        with pytest.raises(Refused) as exc:
            explain_cypher(FakeDriver(explain_plan=tree), "MATCH (n) RETURN n")
        assert exc.value.rule == "cypher.plan_operator"

    def test_the_object_form_of_a_plan_is_read_too(self):
        from types import SimpleNamespace as NS

        tree = NS(
            operator_type="ProduceResults@neo4j", children=[NS(operator_type="LoadCSV@neo4j")]
        )
        with pytest.raises(Refused):
            explain_cypher(FakeDriver(explain_plan=tree), "MATCH (n) RETURN n")

    def test_an_error_while_explaining_is_a_failure_not_a_pass(self):
        drv = FakeDriver(explain_error=CypherSyntaxError("Neo.ClientError.Statement.SyntaxError"))
        with pytest.raises(QueryFailed) as exc:
            explain_cypher(drv, "MATCH (n RETURN n")
        assert exc.value.kind == "syntax"


class TestRun:
    def test_the_whole_path_uses_read_sessions_a_timeout_and_the_cleaned_text(self):
        drv = FakeDriver(keys=["name", "n"], records=[["Josh Allen", 3], ["Jordan Love", 2]])
        res = run_cypher(
            drv,
            "MATCH (n) RETURN n.name AS name, 3 AS n // c\n;",
            {"p": 1},
            database="db1",
            timeout_s=7.5,
        )
        assert isinstance(res, QueryResult)
        assert res.columns == ["name", "n"]
        assert res.rows == [["Josh Allen", 3], ["Jordan Love", 2]]
        assert res.source == "graph" and res.database == "db1"
        assert res.query == "MATCH (n) RETURN n.name AS name, 3 AS n"
        assert res.params == {"p": 1}
        assert not res.truncated
        assert len(drv.sessions) == 2  # EXPLAIN, then the run
        assert all(s["default_access_mode"] == neo4j.READ_ACCESS for s in drv.sessions)
        assert all(s["database"] == "db1" for s in drv.sessions)
        assert drv.reads == [{"timeout": 7.5, "kwargs": drv.sessions[1]}]
        assert drv.tx_runs == [(res.query, {"p": 1})]
        assert drv.session_runs[0][0] == "EXPLAIN " + res.query
        assert res.checks_seconds <= res.seconds

    def test_the_default_timeout_is_ten_seconds_and_the_default_cap_500(self):
        drv = FakeDriver(records=[[i] for i in range(600)])
        res = run_cypher(drv, "MATCH (n) RETURN n.x")
        assert drv.reads[0]["timeout"] == 10.0
        assert len(res.rows) == 500 and res.truncated

    def test_a_refused_text_never_reaches_the_driver(self):
        drv = FakeDriver()
        with pytest.raises(Refused):
            run_cypher(drv, "MATCH (n) DETACH DELETE n")
        assert drv.sessions == [] and drv.tx_runs == []

    def test_a_write_plan_is_stopped_before_the_run(self):
        drv = FakeDriver(query_type="w")
        with pytest.raises(Refused):
            run_cypher(drv, "MATCH (n) RETURN n.x")
        assert len(drv.sessions) == 1 and drv.tx_runs == []

    def test_a_procedure_plan_is_stopped_before_the_run(self):
        drv = FakeDriver(explain_plan=plan("ProduceResults@neo4j", "ProcedureCall@neo4j"))
        with pytest.raises(Refused):
            run_cypher(drv, "MATCH (n) RETURN n.x")
        assert drv.tx_runs == []

    def test_the_params_dict_is_copied_not_mutated(self):
        params = {"a": 1}
        run_cypher(FakeDriver(), "MATCH (n) RETURN n.x", params)
        assert params == {"a": 1}

    @pytest.mark.parametrize(
        "n_records, cap, truncated, kept",
        [(10, 3, True, 3), (3, 3, False, 3), (4, 3, True, 3), (0, 3, False, 0), (1, 1, False, 1)],
    )
    def test_the_row_cap(self, n_records, cap, truncated, kept):
        drv = FakeDriver(records=[[i] for i in range(n_records)])
        res = run_cypher(drv, "MATCH (n) RETURN n.x", cap=cap)
        assert len(res.rows) == kept and res.truncated is truncated
        assert drv.last_result.pulled <= cap + 2  # it stops pulling soon after the cap

    def test_to_dict_is_json_ready(self):
        import json

        res = run_cypher(FakeDriver(), "MATCH (n) RETURN n.name")
        d = res.to_dict()
        assert json.loads(json.dumps(d))["row_count"] == 1
        assert set(d) >= {"source", "query", "params", "columns", "rows", "truncated", "seconds"}


class TestValues:
    @staticmethod
    def one(value, **kw):
        drv = FakeDriver(keys=["v"], records=[[value]])
        return run_cypher(drv, "MATCH (n) RETURN n.v AS v", **kw).rows[0][0]

    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, None),
            (True, True),
            (3, 3),
            (2.5, 2.5),
            ("text", "text"),
            (float("nan"), None),
            (float("inf"), None),
            (float("-inf"), None),
            (decimal.Decimal("1.5"), 1.5),
            (dt.date(2026, 10, 17), "2026-10-17"),
            (dt.datetime(2026, 10, 17, 1, 2, 3), "2026-10-17T01:02:03"),
            (dt.time(1, 2, 3), "01:02:03"),
            (dt.timedelta(seconds=90), 90.0),
            ([1, [2, "x"], None], [1, [2, "x"], None]),
            ((1, 2), [1, 2]),
        ],
    )
    def test_plain_values(self, value, expected):
        assert self.one(value) == expected

    def test_neo4j_time_values_become_iso_text(self):
        assert self.one(Date(2026, 10, 17)) == "2026-10-17"
        assert self.one(DateTime(2026, 10, 17, 1, 2, 3)).startswith("2026-10-17T01:02:03")
        assert self.one(Time(1, 2, 3)).startswith("01:02:03")
        assert self.one([Date(2026, 10, 17)]) == ["2026-10-17"]

    def test_a_neo4j_duration_becomes_iso_text(self):
        # a Duration is a tuple subclass: it must not come out as its four ints
        assert self.one(Duration(days=1)) == "P1D"
        assert self.one([Duration(days=1)]) == ["P1D"]

    def test_a_node_is_never_returned(self):
        node = Node(Graph(), "4:abc:1", 1, ["Team"], {"team_id": "KC", "key": "sk-secret-value"})
        with pytest.raises(Refused) as exc:
            self.one(node)
        assert exc.value.rule == "cypher.values"
        for kw in ({}, {"allow_maps": True}):  # not even for the library's templates
            with pytest.raises(Refused):
                self.one(node, **kw)

    def test_a_relationship_is_never_returned(self):
        rel = Relationship(Graph(), "5:abc:1", 3, {"x": 1})
        for kw in ({}, {"allow_maps": True}):
            with pytest.raises(Refused):
                self.one(rel, **kw)

    def test_a_path_like_object_is_refused(self):
        class Path:
            nodes = ()

        with pytest.raises(Refused):
            self.one(Path(), allow_maps=True)

    def test_nodes_hidden_in_lists_and_maps_are_refused(self):
        node = Node(Graph(), "4:abc:1", 1, ["Team"], {"team_id": "KC"})
        with pytest.raises(Refused):
            self.one([1, node])
        with pytest.raises(Refused):
            self.one({"n": node}, allow_maps=True)
        with pytest.raises(Refused):
            self.one([{"deep": [node]}], allow_maps=True)

    def test_maps_are_refused_unless_a_library_template_allows_them(self):
        with pytest.raises(Refused) as exc:
            self.one({"a": 1})
        assert exc.value.rule == "cypher.values"
        with pytest.raises(Refused):
            self.one([{"a": 1}])
        assert self.one({"a": 1, 2: Date(2026, 1, 2)}, allow_maps=True) == {
            "a": 1,
            "2": "2026-01-02",
        }
        assert self.one([{"a": [1, {"b": None}]}], allow_maps=True) == [{"a": [1, {"b": None}]}]

    @pytest.mark.parametrize("value", [b"bytes", bytearray(b"x"), {1, 2}, object(), 1 + 2j])
    def test_other_types_are_refused(self, value):
        with pytest.raises(Refused):
            self.one(value, allow_maps=True)

    def test_an_iso_format_method_is_not_enough_outside_the_neo4j_package(self):
        class Fake:
            def iso_format(self):
                return "x"

        with pytest.raises(Refused):
            self.one(Fake())

    def test_a_refusal_in_a_value_is_a_refusal_not_a_failure(self):
        drv = FakeDriver(keys=["v"], records=[[object()]])
        with pytest.raises(Refused):
            run_cypher(drv, "MATCH (n) RETURN n.v AS v")


class TestFailures:
    SECRET_MSG = "boom password=hunter2 for sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789 at D:/x"

    def failed(self, exc, stage="run"):
        kw = {"run_error": exc} if stage == "run" else {"explain_error": exc}
        with pytest.raises(QueryFailed) as e:
            run_cypher(FakeDriver(**kw), "MATCH (n) RETURN n.x")
        return e.value

    def test_a_timeout_is_a_timeout_with_no_message(self):
        e = self.failed(
            ClientError("Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration", "x")
        )
        assert e.kind == "timeout" and e.hint == ""
        assert e.code == (
            "ClientError: Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration"
        )

    def test_a_syntax_error_has_kind_code_and_a_scrubbed_hint(self):
        e = self.failed(
            CypherSyntaxError("Neo.ClientError.Statement.SyntaxError", self.SECRET_MSG), "explain"
        )
        assert e.kind == "syntax"
        assert e.code == "CypherSyntaxError: Neo.ClientError.Statement.SyntaxError"
        assert "hunter2" not in e.hint and "sk-or-v1" not in e.hint
        assert "boom" in e.hint  # the part the correction prompt needs

    @pytest.mark.parametrize(
        "code",
        [
            "Neo.ClientError.Statement.ParameterMissing",
            "Neo.ClientError.Statement.ArgumentError",
            "Neo.ClientError.Statement.TypeError",
        ],
    )
    def test_other_syntax_like_codes(self, code):
        assert self.failed(ClientError(code, "m")).kind == "syntax"

    def test_other_client_errors_keep_a_hint(self):
        e = self.failed(ClientError("Neo.ClientError.Statement.ArithmeticError", "division by 0"))
        assert e.kind == "error" and "division" in e.hint

    def test_the_message_never_leaks_outside_the_hint(self):
        for exc in (
            CypherSyntaxError("Neo.ClientError.Statement.SyntaxError", self.SECRET_MSG),
            ClientError("Neo.ClientError.Statement.ArithmeticError", self.SECRET_MSG),
            ValueError(self.SECRET_MSG),
            ClientError("Neo.ClientError.Transaction.TransactionTimedOut", self.SECRET_MSG),
        ):
            e = self.failed(exc)
            outside = " ".join([str(e), repr(e), e.kind, e.code, *map(str, e.args)])
            assert "hunter2" not in outside and "sk-or-v1" not in outside
            assert "boom" not in outside
            assert "hunter2" not in e.hint and "sk-or-v1" not in e.hint

    @pytest.mark.parametrize("stage", ["run", "explain"])
    def test_a_dead_server_stops_the_question_as_source_down(self, stage):
        # AE01 (D142): an unreachable graph isn't the query's fault, so no correction round
        # can fix it; the fixed refusal carries none of the driver's message
        kw = {
            "run_error" if stage == "run" else "explain_error": ServiceUnavailable(self.SECRET_MSG)
        }
        with pytest.raises(Refused) as e:
            run_cypher(FakeDriver(**kw), "MATCH (n) RETURN n.x")
        assert e.value.rule == "source.down"
        outside = " ".join([str(e.value), repr(e.value), *map(str, e.value.args)])
        assert "hunter2" not in outside and "sk-or-v1" not in outside and "boom" not in outside

    def test_an_unknown_exception_is_only_its_type(self):
        e = self.failed(KeyError("secret-key-name"))
        assert e.code == "KeyError" and e.hint == ""

    def test_a_long_hint_is_capped(self):
        e = self.failed(ClientError("Neo.ClientError.Statement.ArithmeticError", "x" * 5000))
        assert len(e.hint) <= 400


# ---- Sources (the graph half) ---------------------------------------------------------------


class TestSources:
    def test_graph_runs_through_the_guard_with_the_configured_limits(self):
        drv = FakeDriver(records=[[i] for i in range(10)])
        src = Sources(sandbox=None, driver=drv, database="neo4j", timeout_s=4.0, cap=3)
        res = src.run("graph", "MATCH (n) RETURN n.x AS name", {"a": 1})
        assert len(res.rows) == 3 and res.truncated
        assert drv.reads[0]["timeout"] == 4.0
        assert drv.sessions[0]["database"] == "neo4j"

    def test_no_driver_means_the_source_is_off(self):
        with pytest.raises(Refused) as exc:
            Sources(sandbox=None, driver=None).run("graph", "MATCH (n) RETURN n")
        assert exc.value.rule == "source.off" and exc.value.detail == "graph"

    def test_no_sandbox_means_sql_is_off(self):
        with pytest.raises(Refused) as exc:
            Sources(sandbox=None, driver=FakeDriver()).run("sql", "SELECT 1")
        assert exc.value.rule == "source.off" and exc.value.detail == "sql"

    def test_an_unknown_source_is_a_programming_error(self):
        with pytest.raises(ValueError):
            Sources(sandbox=None, driver=FakeDriver()).run("history", "MATCH (n) RETURN n")

    def test_allow_maps_is_off_by_default_and_forwarded_when_asked(self):
        drv = FakeDriver(keys=["m"], records=[[{"a": 1}]])
        src = Sources(sandbox=None, driver=drv)
        with pytest.raises(Refused):
            src.run("graph", "MATCH (n) RETURN {a: 1} AS m")
        assert src.run("graph", "MATCH (n) RETURN {a: 1} AS m", allow_maps=True).rows == [
            [{"a": 1}]
        ]

    def test_close_closes_the_driver_and_survives_its_errors(self):
        drv = FakeDriver()
        Sources(sandbox=None, driver=drv).close()
        assert drv.closed

        class Bad:
            def close(self):
                raise RuntimeError("x")

        Sources(sandbox=None, driver=Bad()).close()
        Sources(sandbox=None, driver=None).close()


# ---- rules that a call-shaped test alone cannot tell apart ------------------------------------


class TestEachRuleOnItsOwn:
    """`apoc.x(` is caught by the function-namespace rule too, so these shapes isolate the
    namespace list, the CALL rule and the comment ends (found by the mutation round)."""

    @pytest.mark.parametrize("ns", ["apoc", "gds", "dbms", "db", "genai", "APOC", "Gds"])
    def test_a_namespace_without_a_call(self, ns):
        with pytest.raises(Refused) as exc:
            check_cypher(f"RETURN {ns}.something AS v")
        assert exc.value.rule == "cypher.namespace" and exc.value.detail == ns.lower()

    def test_a_variable_with_such_a_name_is_refused_conservatively(self):
        assert refused_rule("MATCH (db:Team) RETURN db.name") == "cypher.namespace"

    @pytest.mark.parametrize(
        "q",
        [
            "CALL mytest() YIELD x RETURN x",
            "CALL labels()",
            "OPTIONAL CALL stuff() YIELD y RETURN y",
            "MATCH (n) CALL stuff(n) YIELD y RETURN y",
        ],
    )
    def test_a_procedure_without_a_namespace_is_a_procedure(self, q):
        assert refused_rule(q) == "cypher.procedure"

    def test_a_cr_ends_a_line_comment_like_a_newline(self):
        # Neo4j ends a // comment at \r as well; what runs is what the server would run
        assert check_cypher("RETURN 1 AS x // c\r, 2 AS y").endswith(", 2 AS y")
        assert refused_rule("MATCH (n) // x\rCREATE (m:X)") == "cypher.write"
        assert refused_rule("MATCH (n) // x\r\nDETACH DELETE n") == "cypher.write"
        # and a newline still does
        assert refused_rule("MATCH (n) // x\nCREATE (m:X)") == "cypher.write"

    def test_a_comment_runs_to_the_end_when_there_is_no_line_end(self):
        assert check_cypher("RETURN 1 AS x // CREATE (n)") == "RETURN 1 AS x"


class TestDeadlines:
    """One budget for the plan and the run (Sol, AE00): EXPLAIN is a timed `Query`, and the
    transaction gets what is left."""

    def test_explain_is_sent_with_the_timeout(self):
        drv = FakeDriver()
        explain_cypher(drv, "MATCH (n) RETURN n.name", None, None, 4.5)
        assert drv.explain_timeouts == [4.5]
        assert drv.session_runs[0][0] == "EXPLAIN MATCH (n) RETURN n.name"

    def test_the_default_explain_timeout_is_ten_seconds(self):
        drv = FakeDriver()
        explain_cypher(drv, "MATCH (n) RETURN n.name")
        assert drv.explain_timeouts == [10.0]

    def test_run_cypher_passes_its_timeout_to_both_steps(self):
        drv = FakeDriver()
        run_cypher(drv, "MATCH (n) RETURN n.name", timeout_s=7.5)
        assert drv.explain_timeouts == [7.5]
        assert 7.0 < drv.reads[0]["timeout"] <= 7.5  # what is left of the budget

    def test_the_run_gets_what_the_plan_left(self):
        drv = FakeDriver(explain_delay=0.3)
        run_cypher(drv, "MATCH (n) RETURN n.name", timeout_s=3.0)
        assert 2.0 < drv.reads[0]["timeout"] < 2.8

    def test_the_run_never_gets_less_than_half_a_second(self):
        drv = FakeDriver(explain_delay=0.3)
        run_cypher(drv, "MATCH (n) RETURN n.name", timeout_s=0.6)
        assert drv.reads[0]["timeout"] == 0.5

    def test_a_spent_budget_is_a_timeout_before_the_run(self):
        drv = FakeDriver(explain_delay=0.15)
        with pytest.raises(QueryFailed) as exc:
            run_cypher(drv, "MATCH (n) RETURN n.name", timeout_s=0.1)
        assert exc.value.kind == "timeout" and exc.value.code == "deadline"
        assert drv.tx_runs == [] and drv.reads == []

    def test_explain_errors_still_map(self):
        drv = FakeDriver(
            explain_error=ClientError("Neo.ClientError.Transaction.TransactionTimedOut", "slow")
        )
        with pytest.raises(QueryFailed) as exc:
            explain_cypher(drv, "MATCH (n) RETURN n.name", None, None, 1.0)
        assert exc.value.kind == "timeout"
