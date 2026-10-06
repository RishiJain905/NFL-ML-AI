"""`nfl app`: flags, the missing-build and port-in-use paths, the 127.0.0.1 bind. Never starts a
real server (uvicorn is stubbed)."""

from __future__ import annotations

import socket

import pytest
from typer.testing import CliRunner

import nflengine.app.serve as serve_mod
import nflengine.app.server as srv
from nflengine.cli import app as cli


def test_help_mentions_localhost():
    r = CliRunner().invoke(cli, ["app", "--help"])
    assert r.exit_code == 0
    assert "127.0.0.1" in r.output and "--host" not in r.output


def test_flags_reach_serve(monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr(serve_mod, "serve", fake)
    r = CliRunner().invoke(cli, ["app", "--port", "8899", "--no-browser", "--rehearsal"])
    assert r.exit_code == 0
    assert (seen["port"], seen["open_browser"], seen["dev"], seen["rehearsal"]) == (
        8899,
        False,
        False,
        True,
    )


@pytest.fixture
def no_uvicorn(monkeypatch):
    import uvicorn

    started = []

    class FakeServer:
        def __init__(self, config):
            started.append(config)

        def run(self):
            pass

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    return started


def test_missing_build_stops_before_starting(tmp_path, monkeypatch, no_uvicorn):
    monkeypatch.setattr(srv, "WEB_DIST", tmp_path / "dist")
    lines = []
    assert serve_mod.serve(open_browser=False, log=lines.append) == serve_mod.EXIT_NO_BUILD
    assert no_uvicorn == [] and "npm --prefix web run build" in lines[0]


def test_port_in_use(tmp_path, monkeypatch, no_uvicorn):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr(srv, "WEB_DIST", dist)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        lines = []
        assert serve_mod.serve(port=port, open_browser=False, log=lines.append) == 1
    assert no_uvicorn == [] and "in use" in lines[0]


def test_binds_127_0_0_1_only(tmp_path, monkeypatch, no_uvicorn):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr(srv, "WEB_DIST", dist)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert serve_mod.serve(port=port, open_browser=False, log=lambda _m: None) == 0
    (config,) = no_uvicorn
    assert config.host == "127.0.0.1" and config.port == port


def test_dev_mode_needs_no_build(tmp_path, monkeypatch, no_uvicorn):
    monkeypatch.setattr(srv, "WEB_DIST", tmp_path / "missing")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    lines = []
    assert serve_mod.serve(port=port, dev=True, log=lines.append) == 0
    assert "npm --prefix web run dev" in lines[0]


def test_ctrl_c_is_a_normal_stop(tmp_path, monkeypatch):
    """Sol review (CR00): uvicorn re-raises KeyboardInterrupt after a clean shutdown."""
    import uvicorn

    class InterruptedServer:
        def __init__(self, config):
            pass

        def run(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(uvicorn, "Server", InterruptedServer)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr(srv, "WEB_DIST", dist)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    lines = []
    assert serve_mod.serve(port=port, open_browser=False, log=lines.append) == 0
    assert lines[-1] == "Control room stopped."
