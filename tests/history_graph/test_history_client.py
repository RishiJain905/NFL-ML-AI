"""The history graph's driver (AE02): Bolt on the history port with the same user and password
setting as the weekly graph, never the weekly URI; `history_up` never raises. No real server,
no real env file (`EnvSettings(_env_file=None, ...)` with explicit test values)."""

from __future__ import annotations

import pytest

from nflengine.history_graph import client as C
from nflengine.history_graph.config import HistoryConfig
from nflengine.settings import EnvSettings

CFG = HistoryConfig()
TEST_PASSWORD = "test-only-password"


def env(password: str | None = TEST_PASSWORD, user: str = "neo4j") -> EnvSettings:
    return EnvSettings(_env_file=None, neo4j_password=password, neo4j_user=user)


class FakeDriver:
    def __init__(self, uri, auth, connection_timeout, ok=True):
        self.uri, self.auth, self.connection_timeout = uri, auth, connection_timeout
        self.ok, self.closed, self.verified = ok, False, False

    def verify_connectivity(self):
        self.verified = True
        if not self.ok:
            raise OSError("no server here")

    def close(self):
        self.closed = True


@pytest.fixture
def made(monkeypatch):
    drivers: list[FakeDriver] = []
    state = {"ok": True}

    def factory(uri, auth=None, connection_timeout=None):
        d = FakeDriver(uri, auth, connection_timeout, state["ok"])
        drivers.append(d)
        return d

    monkeypatch.setattr("neo4j.GraphDatabase.driver", factory)
    monkeypatch.setattr(C, "get_env", lambda: env())
    drivers_state = type("S", (), {"drivers": drivers, "state": state})
    return drivers_state


def test_the_driver_goes_to_the_history_port_with_the_shared_credentials(made):
    d = C.get_history_driver(env("pw-1", "someone"), CFG, connection_timeout=3.0)
    assert d.uri == "bolt://localhost:7688"
    assert d.auth == ("someone", "pw-1")
    assert d.connection_timeout == 3.0


def test_it_never_uses_the_weekly_uri_even_if_the_env_says_so(made):
    e = EnvSettings(_env_file=None, neo4j_password="x", neo4j_uri="bolt://localhost:7687")
    d = C.get_history_driver(e, CFG)
    assert d.uri == CFG.bolt_uri and not d.uri.endswith(":7687")


def test_the_uri_follows_the_config(made):
    cfg = HistoryConfig(bolt_uri="bolt://127.0.0.1:7699")
    assert C.get_history_driver(env(), cfg).uri == "bolt://127.0.0.1:7699"


def test_a_missing_password_is_a_fixed_error_that_names_the_variable_only(made):
    with pytest.raises(RuntimeError) as e:
        C.get_history_driver(env(None), CFG)
    assert str(e.value) == "NEO4J_PASSWORD is not set in the env file."
    assert made.drivers == []  # no connection was attempted


def test_a_blank_password_counts_as_not_set(made):
    with pytest.raises(RuntimeError, match="NEO4J_PASSWORD is not set"):
        C.get_history_driver(env("   "), CFG)


def test_the_default_timeout_is_short(made):
    assert C.get_history_driver(env(), CFG).connection_timeout == 10.0


def test_history_up_true_when_bolt_answers_and_the_driver_is_closed(made):
    assert C.history_up(CFG) is True
    (d,) = made.drivers
    assert d.verified and d.closed
    assert d.uri == CFG.bolt_uri and d.connection_timeout == 5.0


def test_history_up_false_when_bolt_does_not_answer_and_the_driver_is_closed(made):
    made.state["ok"] = False
    assert C.history_up(CFG, timeout=1.0) is False
    (d,) = made.drivers
    assert d.closed and d.connection_timeout == 1.0


def test_history_up_false_without_a_password_and_never_raises(monkeypatch):
    monkeypatch.setattr(C, "get_env", lambda: env(None))
    assert C.history_up(CFG) is False


def test_history_up_false_when_the_driver_cannot_even_be_made(monkeypatch):
    monkeypatch.setattr(C, "get_env", lambda: env())

    def boom(*a, **k):
        raise ValueError("bad uri SECRET-TEXT")

    monkeypatch.setattr("neo4j.GraphDatabase.driver", boom)
    assert C.history_up(CFG) is False


def test_the_password_is_not_in_the_drivers_repr_or_the_modules_errors(made):
    d = C.get_history_driver(env("super-secret-value"), CFG)
    assert "super-secret-value" not in repr(CFG) and "super-secret-value" not in str(
        CFG.model_dump()
    )
    assert d.auth[1] == "super-secret-value"  # only the driver call carries it
