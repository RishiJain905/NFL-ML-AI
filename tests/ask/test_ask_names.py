"""AE00 name resolution (`ask/names.py`): a `NameIndex` built directly from `_Player` / `_Coach`
lists (no parquet), the uniqueness rules, last names, coach / player clashes, teams, fuzzy near
misses, sentence-initial words, stop words; and the loader over a tiny curated folder.
"""

from __future__ import annotations

import polars as pl
import pytest
from ask_fakes import coach, make_index, player, standard_index

from nflengine.ask.names import (
    FUZZY_CUTOFF,
    LAST_NAME_MIN_GAMES,
    MAX_CHOICES,
    MIN_GAMES,
    STOPWORDS,
    Mention,
    NameIndex,
    Resolution,
    _Coach,
    ambiguous,
    norm,
)


@pytest.fixture(scope="module")
def ix() -> NameIndex:
    return standard_index()


def one(index: NameIndex, question: str) -> Mention:
    found = index.mentions(question)
    assert len(found) == 1, [(m.text, m.kind, m.resolution.status) for m in found]
    return found[0]


def texts(index: NameIndex, question: str) -> list[str]:
    return [m.text for m in index.mentions(question)]


# ---- norm -----------------------------------------------------------------------------------


class TestNorm:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("A.J. Brown", "aj brown"),
            ("Michael Penix Jr.", "michael penix"),
            ("Michael Penix Jr", "michael penix"),
            ("Odell Beckham III", "odell beckham"),
            ("Amon-Ra St. Brown", "amon ra st brown"),
            ("Patrick Mahomés", "patrick mahomes"),
            ("Ja'Marr Chase", "jamarr chase"),
            ("D’Andre Swift", "dandre swift"),
            ("Cooper Kupp's", "cooper kupp"),
            ("  Josh   Allen  ", "josh allen"),
            ("JOSH ALLEN", "josh allen"),
            ("Jr.", "jr"),  # a lone suffix stays
            ("", ""),
            ("   ", ""),
            ("---", ""),
            ("Kansas City", "kansas city"),
        ],
    )
    def test_norm(self, raw, expected):
        assert norm(raw) == expected

    def test_only_trailing_suffixes_are_dropped(self):
        assert norm("Jr Smith") == "jr smith"
        assert norm("John V Smith") == "john v smith"
        assert norm("John Smith Jr Sr") == "john smith"


# ---- teams ----------------------------------------------------------------------------------


class TestTeam:
    @pytest.mark.parametrize(
        "text, code",
        [
            ("KC", "KC"),
            ("kc", "KC"),
            ("Kc", "KC"),
            ("  dal  ", "DAL"),
            ("Kansas City Chiefs", "KC"),
            ("Chiefs", "KC"),
            ("chiefs", "KC"),
            ("The Chiefs", "KC"),
            ("the chiefs", "KC"),
            ("Kansas City", "KC"),
            ("Dallas", "DAL"),
            ("Cowboys", "DAL"),
            ("Green Bay", "GB"),
            ("Packers", "GB"),
            ("San Francisco 49ers", "SF"),
            ("49ers", "SF"),
            ("Niners", "SF"),
            ("Bucs", "TB"),
            ("Pats", "NE"),
            ("Jags", "JAX"),
            ("Redskins", "WAS"),
            ("Washington Football Team", "WAS"),
            ("Commanders", "WAS"),
            ("Oakland Raiders", "LV"),
            ("Oakland", "LV"),
            ("San Diego Chargers", "LAC"),
            ("OAK", "LV"),
            ("SD", "LAC"),
            ("STL", "LA"),
            ("LA", "LA"),
            ("la", "LA"),
            ("LA Rams", "LA"),
            ("Los Angeles Rams", "LA"),
            ("LA Chargers", "LAC"),
            ("Los Angeles Chargers", "LAC"),
            ("NY Giants", "NYG"),
            ("New York Jets", "NYJ"),
            ("Giants", "NYG"),
            ("Jets", "NYJ"),
            ("Dalas", "DAL"),  # a near miss
            ("Chifs", "KC"),
            ("Philadelphia Eagels", "PHI"),
        ],
    )
    def test_resolves(self, ix, text, code):
        assert ix.team(text) == code

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            None,
            "Nowhere",
            "New York",
            "new york",
            "Los Angeles",
            "NY",
            "xyz",
            "Springfield",
        ],
    )
    def test_does_not_resolve(self, ix, text):
        assert ix.team(text) is None

    def test_all_32_codes_and_names_resolve(self, ix):
        from nflengine.digest.names import TEAMS

        assert len(TEAMS) == 32
        for code, (loc, nick) in TEAMS.items():
            assert ix.team(code) == code
            assert ix.team(nick) == code
            assert ix.team(f"{loc} {nick}") == code

    def test_labels(self, ix):
        assert ix.team_label("KC") == "Kansas City Chiefs"
        assert ix.team_label("ZZZ") == "ZZZ"


# ---- players --------------------------------------------------------------------------------


class TestPlayer:
    def test_a_gsis_id(self, ix):
        r = ix.player("00-0034857")
        assert r.status == "unique" and r.value == "00-0034857"
        assert r.label == "Josh Allen (QB, BUF, 2018-2026)"

    def test_a_full_name_in_any_case(self, ix):
        for text in ("Patrick Mahomes", "patrick mahomes", "PATRICK MAHOMES", " Patrick  Mahomes "):
            assert ix.player(text).value == "00-0033873"

    def test_suffixes_and_punctuation_in_the_query_or_the_data(self, ix):
        assert ix.player("Michael Penix").value == "00-0040000"
        assert ix.player("Michael Penix Jr.").value == "00-0040000"
        assert ix.player("Amon-Ra St. Brown").value == "00-0036000"
        assert ix.player("Amon Ra St Brown").value == "00-0036000"

    def test_the_recent_prominent_namesake_wins(self, ix):
        # Josh Allen: the Bills QB (130 games, 2026) and a 2014-2016 center (30 games)
        r = ix.player("Josh Allen")
        assert r.status == "unique" and r.value == "00-0034857"

    def test_two_recent_namesakes_are_ambiguous(self):
        index = make_index(
            [
                player("A1", "Mike Williams", "WR", "LAC", 2017, 2026, 100),
                player("A2", "Mike Williams", "WR", "PIT", 2020, 2026, 60),
            ]
        )
        r = index.player("Mike Williams")
        assert r.status == "ambiguous" and r.value is None and r.label is None
        assert [m.value for m in r.matches] == ["A1", "A2"]  # most recent, then most games

    def test_two_old_namesakes_are_ambiguous(self):
        index = make_index(
            [
                player("A1", "Chris Smith", "DE", "X", 2010, 2015, 90),
                player("A2", "Chris Smith", "WR", "Y", 2011, 2016, 80),
            ]
        )
        assert index.player("Chris Smith").status == "ambiguous"

    def test_min_games_decides_who_makes_a_name_ambiguous(self):
        few = MIN_GAMES - 1
        index = make_index(
            [
                player("A1", "Sam Jones", "WR", "X", 2018, 2026, 100),
                player("A2", "Sam Jones", "WR", "Y", 2018, 2026, few),
            ]
        )
        assert index.player("Sam Jones").value == "A1"
        index = make_index(
            [
                player("A1", "Sam Jones", "WR", "X", 2018, 2026, 100),
                player("A2", "Sam Jones", "WR", "Y", 2018, 2026, MIN_GAMES),
            ]
        )
        assert index.player("Sam Jones").status == "ambiguous"

    def test_nobody_with_enough_games_lists_everyone(self):
        index = make_index(
            [player("A1", "Rookie One", "WR", "X", 2026, 2026, 2)]
            + [player("A2", "Rookie One", "RB", "Y", 2026, 2026, 3)]
        )
        r = index.player("Rookie One")
        assert r.status == "ambiguous" and {m.value for m in r.matches} == {"A1", "A2"}

    def test_a_single_player_is_unique_whatever_his_games(self):
        index = make_index([player("A1", "Lone Wolf", "WR", "X", 2026, 2026, 0)])
        assert index.player("Lone Wolf").value == "A1"

    def test_at_most_five_choices_most_recent_first(self):
        index = make_index(
            [
                player(f"A{i}", "Pat Brown", "WR", "X", 2010 + i, 2010 + i + 2, 50 + i)
                for i in range(8)
            ]
        )
        r = index.player("Pat Brown")
        assert r.status == "ambiguous" and len(r.matches) == MAX_CHOICES
        lasts = [int(m.label.split(",")[-1].strip(" )").split("-")[-1]) for m in r.matches]
        assert lasts == sorted(lasts, reverse=True)

    def test_a_near_miss(self, ix):
        r = ix.player("Patrik Mahomes")
        assert r.status == "unique" and r.value == "00-0033873"
        assert 88 <= r.matches[0].score < 100
        assert ix.player("Patrick Mahomez").value == "00-0033873"

    def test_too_far_is_none(self, ix):
        for text in ("Pat Mahonk", "Mahomes Patrick Q Public", "Zzzz Yyyy"):
            assert ix.player(text).status == "none"
        assert FUZZY_CUTOFF == 88.0

    def test_empty_is_none(self, ix):
        for text in ("", "   ", None):
            r = ix.player(text)
            assert r.status == "none" and r.matches == ()

    # ---- last names ----

    def test_a_last_name_that_one_prominent_player_has(self, ix):
        assert ix.player("Mahomes").value == "00-0033873"
        assert ix.player("mahomes").value == "00-0033873"

    def test_a_last_name_needs_a_prominent_player(self):
        index = make_index(
            [
                player("A1", "Pat Quark", "WR", "X", 2026, 2026, LAST_NAME_MIN_GAMES - 1),
                player("A2", "Pat Prom", "WR", "X", 2026, 2026, LAST_NAME_MIN_GAMES),
            ]
        )
        assert index.player("Quark").status == "none"
        assert index.player("Prom").value == "A2"

    def test_a_shared_last_name_is_ambiguous_unless_one_is_recent(self, ix):
        r = ix.player("Allen")
        assert r.status == "ambiguous"
        assert {m.value for m in r.matches} == {"00-0034857", "00-0041000", "00-0026000"}
        # Kelce: Travis (2026) and Jason (retired 2023): only Travis played recently
        assert ix.player("Kelce").value == "00-0030506"

    def test_a_hyphenated_surname(self):
        index = make_index([player("A1", "Josh Hines-Allen", "LB", "JAX", 2019, 2026, 110)])
        assert index.player("Josh Hines-Allen").value == "A1"
        assert [
            m.value
            for m in index.mentions("How many sacks does Hines-Allen have?")[0].resolution.matches
        ] == ["A1"]

    def test_player_label_formats(self, ix):
        assert ix.player_label("00-0033873") == "Patrick Mahomes (QB, KC, 2017-2026)"
        assert ix.player_label("nope") is None
        index = make_index(
            [
                player("A1", "Nobody", "", "", None, None, 0),
                player("A2", "Only Last", "K", "", None, 2024, 3),
            ]
        )
        assert index.player_label("A1") == "Nobody"
        assert index.player_label("A2") == "Only Last (K, 2024)"


# ---- coaches --------------------------------------------------------------------------------


class TestCoach:
    def test_exact_name_in_any_case(self, ix):
        for text in ("Andy Reid", "andy reid", "ANDY REID"):
            r = ix.coach(text)
            assert r.kind == "coach" and r.status == "unique" and r.value == "Andy Reid"
        assert ix.coach("Andy Reid").label == "Andy Reid (head coach, PHI/KC, 2013-2026)"

    def test_near_miss(self, ix):
        assert ix.coach("Andy Ried").value == "Andy Reid"
        assert ix.coach("Mike Tomlinn").value == "Mike Tomlin"

    def test_last_name(self, ix):
        assert ix.coach("Reid").value == "Andy Reid"
        assert ix.coach("Campbell").value == "Dan Campbell"

    def test_a_shared_last_name_is_ambiguous(self):
        index = make_index(
            coaches=[
                coach("Mike Smith", ["ATL"], 2008, 2014),
                coach("Pat Smith", ["XXX"], 2020, 2024),
            ]
        )
        r = index.coach("Smith")
        assert r.status == "ambiguous"
        assert [m.value for m in r.matches] == ["Pat Smith", "Mike Smith"]  # most recent first

    def test_not_found(self, ix):
        for text in ("Zzzz Yyyy", "", "   ", "Nobody", None):
            assert ix.coach(text).status == "none"

    def test_labels_for_non_head_coaches(self):
        c = coach("Kellen Moore", ["PHI", "LAC", "DAL", "PHI"], 2019, 2024, {"OC"})
        assert c.label == "Kellen Moore (OC, LAC/DAL/PHI, 2019-2024)"
        c = coach("Multi Role", ["A"], 2020, 2021, {"OC", "ST"})
        assert "OC/ST" in c.label
        c = _Coach("No Role")
        assert c.label == "No Role (coach)"


# ---- mentions in a question -----------------------------------------------------------------


class TestMentions:
    def test_a_team_by_city(self, ix):
        m = one(ix, "Which receivers have the most 100-yard games against Dallas since 2020?")
        assert (m.kind, m.text) == ("team", "Dallas")
        assert m.resolution.value == "DAL"

    def test_spans_point_at_the_text(self, ix):
        q = "How did Patrick Mahomes do against Bills in 2024?"
        found = ix.mentions(q)
        assert [(m.kind, m.text) for m in found] == [
            ("player", "Patrick Mahomes"),
            ("team", "Bills"),
        ]
        for m in found:
            assert q[m.start : m.end] == m.text
        assert [m.start for m in found] == sorted(m.start for m in found)

    def test_the_longest_phrase_wins(self, ix):
        m = one(ix, "How good are Dallas Cowboys?")
        assert m.text == "Dallas Cowboys" and m.resolution.value == "DAL"
        m = one(ix, "Record of Kansas City Chiefs at home")
        assert m.text == "Kansas City Chiefs"

    def test_an_article_before_a_team_is_tolerated(self, ix):
        m = one(ix, "Record of the Kansas City Chiefs at home")
        assert m.resolution.value == "KC" and m.text.endswith("Kansas City Chiefs")
        assert one(ix, "against the Bills").resolution.value == "BUF"

    def test_codes_count_only_in_capitals(self, ix):
        assert one(ix, "How many wins does NO have?").resolution.value == "NO"
        assert one(ix, "What is SF's record?").resolution.value == "SF"
        assert ix.mentions("Are there no wins for anyone?") == []
        assert ix.mentions("show me kc") == []  # a lowercase code is a word

    def test_nicknames_in_any_case(self, ix):
        assert one(ix, "how about the chiefs this year").resolution.value == "KC"
        assert one(ix, "Niners offense").resolution.value == "SF"
        assert one(ix, "the Washington Football Team in 2020").resolution.value == "WAS"

    def test_shared_markets_are_ambiguous(self, ix):
        for q, codes in (
            ("How are the New York teams doing? New York has two", ("NYG", "NYJ")),
            ("Games in NY this week", ("NYG", "NYJ")),
            ("Los Angeles in 2022", ("LA", "LAC")),
        ):
            first = ix.mentions(q)[0]
            assert first.resolution.status == "ambiguous" and first.kind == "team", q
            assert tuple(m.value for m in first.resolution.matches) == codes, q

    def test_the_rams_code_is_not_ambiguous(self, ix):
        m = one(ix, "How did LA do?")
        assert m.resolution.status == "unique" and m.resolution.value == "LA"

    def test_full_shared_market_names_are_unique(self, ix):
        assert one(ix, "Los Angeles Rams record").resolution.value == "LA"
        assert one(ix, "New York Giants record").resolution.value == "NYG"
        assert one(ix, "NY Jets record").resolution.value == "NYJ"

    def test_two_teams_in_one_token(self, ix):
        q = "Predict the Bills-Chiefs game"
        found = ix.mentions(q)
        assert [(m.kind, m.text, m.resolution.value) for m in found] == [
            ("team", "Bills", "BUF"),
            ("team", "Chiefs", "KC"),
        ]
        for m in found:
            assert q[m.start : m.end] == m.text

    def test_a_hyphenated_word_that_is_not_two_teams_is_left_alone(self, ix):
        assert ix.mentions("a run-heavy offense") == []
        assert ix.mentions("Bills-Nothing game") == [] or all(
            m.text != "Bills-Nothing" for m in ix.mentions("Bills-Nothing game")
        )

    def test_a_full_name_before_a_team_word(self):
        index = make_index([player("A1", "Dallas Goedert", "TE", "PHI", 2018, 2026, 120)])
        m = one(index, "How many catches did Dallas Goedert have?")
        assert m.kind == "player" and m.text == "Dallas Goedert"

    def test_a_prominent_player_wins_the_name(self, ix):
        m = one(ix, "How many yards does Josh Allen have?")
        assert (m.kind, m.resolution.status, m.resolution.value) == (
            "player",
            "unique",
            "00-0034857",
        )

    def test_a_lone_ambiguous_last_name(self, ix):
        m = one(ix, "How many yards does Allen have?")
        assert m.kind == "player" and m.resolution.status == "ambiguous"
        assert "Josh Allen" in m.resolution.matches[0].label

    def test_the_coach_not_his_never_played_namesake(self, ix):
        # a 2-game "Andy Reid" receiver exists in the data; the question means the coach
        m = one(ix, "What is Andy Reid's record after a bye?")
        assert (m.kind, m.text, m.resolution.status) == ("coach", "Andy Reid", "unique")
        assert m.resolution.value == "Andy Reid"

    def test_a_prominent_player_and_a_coach_with_the_same_name_ask(self, ix):
        m = one(ix, "How did Sean Payton do in 2018?")
        assert m.resolution.status == "ambiguous"
        labels = [x.label for x in m.resolution.matches]
        assert any("head coach" in label for label in labels)
        assert any("S, DEN" in label for label in labels)
        assert m.resolution.matches[-1].value == "Sean Payton"  # the coach is listed last

    def test_a_last_name_shared_by_a_player_and_a_coach_asks(self):
        index = make_index(
            [player("A1", "Pat Walsh", "WR", "X", 2020, 2026, 80)],
            [coach("Bill Walsh", ["SF"], 2000, 2008)],
        )
        m = one(index, "How did Walsh do?")
        assert m.resolution.status == "ambiguous"
        assert len(m.resolution.matches) == 2

    def test_a_coach_by_last_name_alone(self, ix):
        m = one(ix, "How does Tomlin do after a loss?")
        assert m.kind == "coach" and m.resolution.value == "Mike Tomlin"

    def test_sentence_initial_names(self, ix):
        assert one(ix, "Mahomes threw 5 touchdowns").resolution.value == "00-0033873"
        m = one(ix, "Mahomes's passing yards in 2023")
        assert m.text == "Mahomes" and m.resolution.value == "00-0033873"
        m = one(ix, "Kelce's catches")
        assert m.text == "Kelce"

    def test_sentence_initial_ordinary_words_are_not_names(self, ix):
        for q in (
            "Who has the most rushing yards?",
            "Which team scored the most points?",
            "Compare the top five receivers",
            "Show me the longest drives",
            "Find every game since 2020",
            "What is the average depth of target?",
            "How many Super Bowl wins in the AFC and NFC?",
            "Top QBs on Sunday Monday Thursday",
        ):
            assert ix.mentions(q) == [], q

    def test_several_stop_words_that_are_also_team_like(self, ix):
        assert {"qb", "te", "nfl", "afc", "nfc", "week", "season", "compare"} <= STOPWORDS
        assert ix.mentions("QB TE WR RB OL") == []

    def test_near_miss_names_in_a_question(self, ix):
        m = one(ix, "How many yards did Patrik Mahomes throw?")
        assert m.kind == "player" and m.text == "Patrik Mahomes"
        assert m.resolution.value == "00-0033873"
        m = one(ix, "What is Mike Tomlinn's record?")
        assert m.kind == "coach" and m.resolution.value == "Mike Tomlin"

    def test_a_near_miss_coach_is_not_taken_for_a_never_played_namesake(self, ix):
        # exactly spelled, "Andy Reid" is the coach (the data has a 2-game receiver of that name)
        assert one(ix, "What is Andy Reid's record?").kind == "coach"
        m = one(ix, "What is Andy Ried's record?")
        assert m.kind == "coach" and m.resolution.value == "Andy Reid"

    def test_a_near_miss_of_a_prominent_player_who_is_also_a_coach_asks(self, ix):
        m = one(ix, "How did Sean Peyton do in 2018?")
        assert m.text == "Sean Peyton" and m.resolution.status == "ambiguous"
        labels = [x.label for x in m.resolution.matches]
        assert any("head coach" in label for label in labels)
        assert any("S, DEN" in label for label in labels)

    def test_a_near_miss_of_a_player_only_stays_a_player(self, ix):
        m = one(ix, "What did Patrik Mahomes throw for?")
        assert m.kind == "player" and m.resolution.status == "unique"

    def test_stop_words_never_start_a_near_miss(self, ix):
        assert ix.mentions("Which Teams Show Results") == []
        assert ix.mentions("Compare Offense Defense") == []

    def test_two_players_and_a_team(self, ix):
        q = "Compare Patrick Mahomes and Josh Allen against Cowboys"
        found = ix.mentions(q)
        assert [(m.kind, m.text) for m in found] == [
            ("player", "Patrick Mahomes"),
            ("player", "Josh Allen"),
            ("team", "Cowboys"),
        ]

    def test_numbers_and_years_are_not_names(self, ix):
        assert ix.mentions("In 2020, week 5, 3rd and 10, from the 50") == []

    def test_empty_and_none(self, ix):
        assert ix.mentions("") == [] and ix.mentions(None) == []

    def test_a_name_in_a_possessive_with_a_curly_apostrophe(self, ix):
        m = one(ix, "How is Mahomes’s arm?")
        assert m.text == "Mahomes"

    def test_ambiguous_helper_and_prompt_lines(self, ix):
        found = ix.mentions("Josh Allen and Allen and Bills and Zzzz Yyyy")
        amb = ambiguous(found)
        assert [m.text for m in amb] == ["Allen"]
        lines = {m.text: m.prompt_line() for m in found}
        assert (
            lines["Josh Allen"]
            == '"Josh Allen" -> player 00-0034857 (Josh Allen (QB, BUF, 2018-2026))'
        )
        assert lines["Bills"] == '"Bills" -> team BUF (Buffalo Bills)'
        assert lines["Allen"].startswith('"Allen" -> ambiguous player: Josh Allen (QB, BUF')
        none = Mention("Zed", 0, 3, Resolution("player", "none"))
        assert none.prompt_line() == '"Zed" -> no player found'

    def test_mention_kind(self, ix):
        assert one(ix, "the Bills").kind == "team"
        assert one(ix, "Andy Reid").kind == "coach"


# ---- the loader -----------------------------------------------------------------------------


@pytest.fixture
def curated(tmp_path, monkeypatch):
    monkeypatch.setattr(NameIndex, "_cache", {})
    cur = tmp_path / "curated"
    cur.mkdir()
    pl.DataFrame(
        {
            "gsis_id": ["00-1", "00-2", "00-3", None, "00-5"],
            "display_name": [
                "Fake Quarterback",
                "Fake Quarterback",
                "Solo Receiver",
                "No Id",
                None,
            ],
            "position": ["QB", "QB", None, "WR", "WR"],
            "latest_team": ["BUF", "NE", "KC", "DAL", "DAL"],
        }
    ).write_parquet(cur / "players.parquet")
    pl.DataFrame(
        {
            "player_id": ["00-1"] * 3 + ["00-2"] * 2 + ["00-3", None],
            "season": [2023, 2024, 2025, 2014, 2015, 2025, 2025],
            "team": ["BUF", "BUF", "MIA", "NE", "NE", "KC", "KC"],
        }
    ).write_parquet(cur / "player_games.parquet")
    pl.DataFrame(
        {
            "season": [2023, 2024, 2024],
            "home_team": ["KC", "DAL", "BUF"],
            "away_team": ["DAL", "KC", "DAL"],
            "home_coach": ["Test Coachone", "Test Coachtwo", "Test Coachthree"],
            "away_coach": ["Test Coachthree", "Test Coachone", None],
        }
    ).write_parquet(cur / "games.parquet")
    pl.DataFrame(
        {"alias": ["OAK", "SD", "ZZZ", "STL"], "team": ["LV", "LAC", "NOPE", "LA"]}
    ).write_parquet(cur / "team_aliases.parquet")
    return cur


class TestLoad:
    def test_players_with_their_games_seasons_and_latest_team(self, curated):
        index = NameIndex.load(curated, 2026)
        by_id = {p.gsis_id: p for p in index.players}
        assert set(by_id) == {"00-1", "00-2", "00-3"}  # no id / no name: left out
        p = by_id["00-1"]
        assert (p.games, p.first, p.last, p.team, p.position) == (3, 2023, 2025, "MIA", "QB")
        p = by_id["00-2"]
        assert (p.games, p.first, p.last, p.team) == (2, 2014, 2015, "NE")
        assert by_id["00-3"].position == ""  # a missing position is empty text
        assert by_id["00-3"].games == 1

    def test_the_namesakes_resolve_by_the_loaded_games(self, curated):
        index = NameIndex.load(curated, 2026)
        # neither has MIN_GAMES games: both are listed
        assert index.player("Fake Quarterback").status == "ambiguous"
        assert index.player("00-1").label == "Fake Quarterback (QB, MIA, 2023-2025)"

    def test_head_coaches_come_from_the_games(self, curated):
        index = NameIndex.load(curated, 2026)
        assert index.coach("Test Coachone").status == "unique"
        assert index.coach("Test Coachtwo").label.startswith("Test Coachtwo (head coach, DAL")
        three = next(c for c in index.coaches if c.name == "Test Coachthree")
        # his teams in order of play, his first and last seasons, a null coach skipped
        assert three.teams == ["DAL", "BUF"] and (three.first, three.last) == (2023, 2024)
        assert "HC" in three.roles

    def test_teams_and_aliases(self, curated):
        index = NameIndex.load(curated, 2026)
        assert index.team("OAK") == "LV" and index.team("sd") == "LAC" and index.team("STL") == "LA"
        assert "ZZZ" not in index.codes  # an alias of a team that doesn't exist
        assert index.team("Chiefs") == "KC"
        assert index.current_season == 2026

    def test_the_index_is_cached_until_a_file_changes(self, curated):
        a = NameIndex.load(curated, 2026)
        assert NameIndex.load(curated, 2026) is a
        assert NameIndex.load(curated, 2027) is not a
        pl.DataFrame(
            {
                "gsis_id": ["00-9"],
                "display_name": ["New Guy"],
                "position": ["RB"],
                "latest_team": ["SF"],
            }
        ).write_parquet(curated / "players.parquet")
        b = NameIndex.load(curated, 2026)
        assert b is not a and {p.gsis_id for p in b.players} == {"00-9"}

    def test_a_missing_alias_file_is_fine(self, curated):
        (curated / "team_aliases.parquet").unlink()
        index = NameIndex.load(curated, 2026)
        assert index.team("KC") == "KC" and index.team("OAK") is None or index.team("OAK") == "LV"


# ---- lower-case names (Sol review) --------------------------------------------------------------


class TestLowerCaseNames:
    @pytest.fixture
    def williams(self):
        return make_index(
            [
                player("A1", "Mike Williams", "WR", "LAC", 2017, 2026, 100),
                player("A2", "Mike Williams", "WR", "PIT", 2020, 2026, 60),
            ]
        )

    @pytest.mark.parametrize(
        "q",
        [
            "mike williams yards",
            "MIKE WILLIAMS yards",
            "how many catches does mike williams have",
            "mike williams",
            "Mike williams",
        ],
    )
    def test_an_ambiguous_full_name_asks_in_any_case(self, williams, q):
        m = one(williams, q)
        assert m.kind == "player" and m.resolution.status == "ambiguous"
        assert {x.value for x in m.resolution.matches} == {"A1", "A2"}

    @pytest.mark.parametrize(
        "q, kind, value",
        [
            ("how many yards did patrick mahomes throw for", "player", "00-0033873"),
            ("compare PATRICK MAHOMES today", "player", "00-0033873"),
            ("what is andy reid record", "coach", "Andy Reid"),
            ("josh allen yards", "player", "00-0034857"),
            ("what about patrick mahomes?", "player", "00-0033873"),
            ("andy reid's record", "coach", "Andy Reid"),
        ],
    )
    def test_a_unique_full_name_resolves_in_any_case(self, ix, q, kind, value):
        m = one(ix, q)
        assert (m.kind, m.resolution.status, m.resolution.value) == (kind, "unique", value)

    def test_a_lower_case_prominent_player_and_coach_still_asks(self, ix):
        m = one(ix, "who is sean payton")
        assert m.resolution.status == "ambiguous"
        assert any("head coach" in x.label for x in m.resolution.matches)

    def test_the_spans_point_at_the_original_text(self, ix):
        q = "yards by patrick mahomes in 2023"
        m = one(ix, q)
        assert q[m.start : m.end] == "patrick mahomes" == m.text

    @pytest.mark.parametrize(
        "q",
        [
            "yards by the best team",
            "the best josh",
            "show me the top rb",
            "most yards in the nfl this season",
            "who has the most rushing yards",
            "list the top five receivers",
            "what did the team do last week",
        ],
    )
    def test_a_lower_case_window_with_a_stop_word_is_never_a_name(self, ix, q):
        assert ix.mentions(q) == []

    def test_a_stop_word_next_to_a_real_name_does_not_hide_it(self, ix):
        # the window with a stop word is skipped, the name's own window is not
        assert one(ix, "most josh allen").resolution.value == "00-0034857"
        assert one(ix, "the best patrick mahomes").resolution.value == "00-0033873"

    def test_lower_case_teams_still_work(self, ix):
        assert one(ix, "kansas city chiefs").resolution.value == "KC"
        assert one(ix, "record of dallas cowboys").resolution.value == "DAL"


class TestThresholds:
    def test_the_numbers(self):
        assert MIN_GAMES == 8 and LAST_NAME_MIN_GAMES == 16

    def test_a_last_name_needs_sixteen_games(self):
        index = make_index(
            [
                player("A1", "Pat Quark", "WR", "X", 2026, 2026, 15),
                player("A2", "Pat Prom", "WR", "X", 2026, 2026, 16),
            ]
        )
        assert index.player("Quark").status == "none"
        assert index.player("Prom").value == "A2"
        assert index.mentions("How is Quark doing?") == []
        assert one(index, "How is Prom doing?").resolution.value == "A2"

    def test_a_same_name_player_needs_eight_games_to_count(self):
        index = make_index(
            [
                player("A1", "Sam Jones", "WR", "X", 2018, 2026, 100),
                player("A2", "Sam Jones", "WR", "Y", 2018, 2026, 7),
            ]
        )
        assert index.player("Sam Jones").value == "A1"
        index = make_index(
            [
                player("A1", "Sam Jones", "WR", "X", 2018, 2026, 100),
                player("A2", "Sam Jones", "WR", "Y", 2018, 2026, 8),
            ]
        )
        assert index.player("Sam Jones").status == "ambiguous"

    def test_last_season_counts_as_recent_but_two_seasons_ago_does_not(self):
        def namesakes(last_a, last_b):
            return make_index(
                [
                    player("A1", "Chris Smith", "WR", "X", 2015, last_a, 90),
                    player("A2", "Chris Smith", "TE", "Y", 2010, last_b, 90),
                ],
                current_season=2026,
            )

        assert namesakes(2025, 2020).player("Chris Smith").value == "A1"  # last season
        assert namesakes(2026, 2020).player("Chris Smith").value == "A1"  # this season
        assert namesakes(2024, 2020).player("Chris Smith").status == "ambiguous"
        assert namesakes(2025, 2026).player("Chris Smith").status == "ambiguous"  # both recent


class TestStopWordsInNames:
    @pytest.fixture
    def levis(self):
        return make_index([player("A1", "Will Levis", "QB", "TEN", 2023, 2026, 40)])

    def test_a_capitalised_name_that_contains_a_stop_word_is_found(self, levis):
        m = one(levis, "How did Will Levis do on Sunday?")
        assert (m.kind, m.text, m.resolution.value) == ("player", "Will Levis", "A1")

    def test_the_same_words_in_lower_case_are_a_question_not_a_name(self, levis):
        assert levis.mentions("will levis play this week") == []
        assert levis.mentions("how many yards will levis throw for") == []

    def test_a_lower_case_name_without_a_stop_word_is_found(self):
        index = make_index([player("A1", "Jordan Love", "QB", "GB", 2020, 2026, 60)])
        assert one(index, "yards by jordan love").resolution.value == "A1"
