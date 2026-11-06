"""D157: `nfl app --tailscale NAME.ts.net`, the phone through `tailscale serve`.

On that name the app is read-only plus Ask: GETs answer, `POST /api/ask` passes the
middleware with the Tailscale origin and the token, everything else that changes state is
refused before it reaches a route. Without the flag the name is just another bad host.
"""

from __future__ import annotations

import pytest
from app_helpers import BASE, TOKEN, make_client
from typer.testing import CliRunner

from nflengine.app import serve as serve_mod
from nflengine.app.security import tailscale_name
from nflengine.cli import app as cli

NAME = "rishi-pc.tail1234.ts.net"
REMOTE = {"host": NAME}
REMOTE_ORIGIN = f"https://{NAME}"
MIDDLEWARE_CODES = {"bad_host", "bad_origin", "bad_token", "remote_read_only"}


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, tailscale=NAME)


def _code(r):
    return r.json()["error"]["code"]


@pytest.mark.parametrize("host", [NAME, f"{NAME}:443", NAME.upper()])
def test_reads_answer_on_the_tailscale_name(client, host):
    assert client.get("/api/meta", headers={"host": host}).status_code == 200
    assert client.get("/api/session", headers={"host": host}).json() == {"token": TOKEN}


def test_local_hosts_still_answer(client):
    assert client.get("/api/meta").status_code == 200


@pytest.mark.parametrize(
    "host", ["other-pc.tail1234.ts.net", f"{NAME}:8765", f"{NAME}.evil.example", "ts.net"]
)
def test_other_names_are_refused(client, host):
    assert _code(client.get("/api/meta", headers={"host": host})) == "bad_host"


def test_without_the_flag_the_name_is_a_bad_host(tmp_path):
    plain = make_client(tmp_path)
    assert _code(plain.get("/api/meta", headers=REMOTE)) == "bad_host"


def test_ask_passes_the_middleware_from_the_phone(client):
    ok = {**REMOTE, "origin": REMOTE_ORIGIN, "x-cr-token": TOKEN}
    r = client.post("/api/ask", headers=ok, json={"question": "Who leads in passing yards?"})
    assert r.status_code < 400 or _code(r) not in MIDDLEWARE_CODES


@pytest.mark.parametrize(
    "headers, code",
    [
        ({"origin": REMOTE_ORIGIN}, "bad_token"),
        ({"origin": REMOTE_ORIGIN, "x-cr-token": "wrong"}, "bad_token"),
        ({"origin": BASE, "x-cr-token": TOKEN}, "bad_origin"),  # the local origin, remotely
        ({"origin": f"http://{NAME}", "x-cr-token": TOKEN}, "bad_origin"),
        ({"x-cr-token": TOKEN}, "bad_origin"),
    ],
)
def test_ask_from_the_phone_still_needs_its_origin_and_the_token(client, headers, code):
    r = client.post("/api/ask", headers={**REMOTE, **headers}, json={"question": "x"})
    assert (r.status_code, _code(r)) == (403, code)


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/api/run"),
        ("post", "/api/ask/"),
        ("post", "/api/anything"),
        ("put", "/api/ask"),
        ("delete", "/api/run"),
    ],
)
def test_everything_else_is_read_only_from_the_phone(client, method, path):
    ok = {**REMOTE, "origin": REMOTE_ORIGIN, "x-cr-token": TOKEN}
    r = getattr(client, method)(path, headers=ok)
    assert (r.status_code, _code(r)) == (403, "remote_read_only")
    assert "read-only" in r.json()["error"]["message"]


def test_the_tailscale_origin_does_not_work_locally(client):
    r = client.post("/api/run", headers={"origin": REMOTE_ORIGIN, "x-cr-token": TOKEN})
    assert (r.status_code, _code(r)) == (403, "bad_origin")


def test_security_headers_on_the_phone(client):
    r = client.get("/api/meta", headers=REMOTE)
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


@pytest.mark.parametrize("name", [NAME, f" {NAME.upper()}. "])
def test_tailscale_name_accepts_full_names(name):
    assert tailscale_name(name) == NAME


@pytest.mark.parametrize(
    "name",
    ["0.0.0.0", "localhost", "ts.net", "pc.ts.net.evil.example", "example.com", "pc..ts.net",
     "-pc.tail1.ts.net", "pc tail.ts.net", "*.tail1.ts.net", ""],
)  # fmt: skip
def test_tailscale_name_refuses_anything_else(name):
    with pytest.raises(ValueError, match="Tailscale name"):
        tailscale_name(name)


def test_flag_reaches_serve(monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr(serve_mod, "serve", fake)
    r = CliRunner().invoke(cli, ["app", "--no-browser", "--tailscale", NAME.upper()])
    assert r.exit_code == 0
    assert seen["tailscale"] == NAME
    r = CliRunner().invoke(cli, ["app", "--no-browser"])
    assert seen["tailscale"] is None


def test_flag_refuses_a_non_tailscale_name(monkeypatch):
    monkeypatch.setattr(serve_mod, "serve", lambda **_kw: 0)
    r = CliRunner().invoke(cli, ["app", "--tailscale", "0.0.0.0"])
    assert r.exit_code != 0
