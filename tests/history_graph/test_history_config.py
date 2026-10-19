"""The `history_graph:` block (AE02): typed, and validated so that no edit of it can point a
command at the weekly graph (service `neo4j` would have made `stop` stop the weekly container)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nflengine.history_graph.config import HistoryConfig, history_config


def test_defaults_are_the_history_graphs_own():
    c = HistoryConfig()
    assert (c.first_season, c.service, c.container, c.profile) == (
        2018,
        "neo4j-history",
        "nfl-neo4j-history",
        "history",
    )
    assert c.bolt_uri == "bolt://localhost:7688" and c.browser_url == "http://localhost:7475"
    assert c.staging_volume == "nfl-neo4j-history-staging"
    assert c.batch_size == 5000 and c.start_timeout_s == 300.0


def test_history_config_from_a_dict_and_from_nothing():
    assert history_config({"first_season": 2020}).first_season == 2020
    assert history_config({}) == HistoryConfig()


def test_the_real_settings_block_validates():
    assert history_config().service == "neo4j-history"


@pytest.mark.parametrize("field", ["service", "container", "profile"])
@pytest.mark.parametrize(
    "value", ["neo4j", "nfl-neo4j", " NEO4J ", "NFL-Neo4j", "", "default", "weekly"]
)
def test_names_that_could_point_at_the_weekly_graph_are_refused(field, value):
    with pytest.raises(ValidationError):
        HistoryConfig(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [("service", "my-history-db"), ("container", "history-test"), ("profile", "history2")],
)
def test_other_names_that_say_history_are_fine(field, value):
    assert getattr(HistoryConfig(**{field: value}), field) == value


@pytest.mark.parametrize("value", ["nfl-neo4j", "weekly-staging", "", "staging", "neo4j"])
def test_the_staging_volume_must_be_the_history_graphs(value):
    with pytest.raises(ValidationError):
        HistoryConfig(staging_volume=value)


def test_a_history_staging_volume_name_is_fine():
    assert HistoryConfig(staging_volume="nfl-neo4j-history-x").staging_volume.endswith("-x")


@pytest.mark.parametrize(
    "field,value",
    [
        ("bolt_uri", "bolt://localhost:7687"),  # the weekly Bolt port
        ("bolt_uri", "bolt://localhost"),  # no explicit port: the driver would default to 7687
        ("bolt_uri", "neo4j://127.0.0.1"),
        ("browser_url", "http://localhost:7474"),  # the weekly Browser port
        ("browser_url", "http://localhost"),
    ],
)
def test_ports_must_be_explicit_and_not_the_weekly_ones(field, value):
    with pytest.raises(ValidationError):
        HistoryConfig(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [("bolt_uri", "bolt://localhost:7690"), ("browser_url", "http://127.0.0.1:7480")],
)
def test_another_history_port_is_fine(field, value):
    assert getattr(HistoryConfig(**{field: value}), field) == value


@pytest.mark.parametrize("value", [0, -5])
def test_batch_size_must_be_positive(value):
    with pytest.raises(ValidationError):
        HistoryConfig(batch_size=value)


def test_a_bad_block_stops_history_config_before_any_command_is_built():
    with pytest.raises(ValidationError):
        history_config({"service": "neo4j"})


def test_the_error_says_what_is_wrong():
    with pytest.raises(ValidationError) as e:
        HistoryConfig(service="neo4j")
    assert "history graph's own name" in str(e.value)
