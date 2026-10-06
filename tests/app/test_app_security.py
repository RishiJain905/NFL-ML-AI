"""The control room's safety rules at the HTTP layer (documentation/control-room/README.md §5)."""

from __future__ import annotations

import pytest
from app_helpers import BASE, TOKEN, make_client

from nflengine.ops.summary import scrub
from nflengine.paths import DataRootError

# /api/session returns the launch token on purpose. The week endpoints are scanned again on a
# full synthetic week in test_app_week.py (raw LLM text, credential-shaped details, the root).
SCANNED_GETS = [
    "/api/meta",
    "/api/weeks",
    "/api/team-info",
    "/api/weeks/2026/5",
    *[
        f"/api/weeks/2026/5/{tab}"
        for tab in ("pipeline", "digest", "games", "players", "results", "graph")
    ],
]


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path)


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765"])
def test_local_hosts_are_answered(client, host):
    assert client.get("/api/meta", headers={"host": host}).status_code == 200


@pytest.mark.parametrize(
    "host", ["evil.example:8765", "127.0.0.1:9999", "localhost", "0.0.0.0:8765", "", "[::1]:8765"]
)
def test_other_hosts_are_refused(client, host):
    r = client.get("/api/meta", headers={"host": host})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_host"


def test_post_needs_same_origin_and_the_token(client):
    origin = {"origin": BASE}
    assert client.post("/api/anything").json()["error"]["code"] == "bad_origin"
    r = client.post("/api/anything", headers={"origin": "http://evil.example"})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "bad_origin")
    r = client.post("/api/anything", headers=origin)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "bad_token")
    r = client.post("/api/anything", headers={**origin, "x-cr-token": "wrong"})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "bad_token")
    # right origin + token: past the middleware, to the API's own 404
    r = client.post("/api/anything", headers={**origin, "x-cr-token": TOKEN})
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_every_state_changing_method_is_checked(client, method):
    r = getattr(client, method)("/api/anything", headers={"origin": BASE})
    assert r.json()["error"]["code"] == "bad_token"


def test_vite_dev_origin_only_in_dev_mode(tmp_path):
    vite = {"origin": "http://localhost:5173", "x-cr-token": TOKEN}
    prod = make_client(tmp_path / "a")
    assert prod.post("/api/anything", headers=vite).json()["error"]["code"] == "bad_origin"
    dev = make_client(tmp_path / "b", dev=True)
    assert dev.post("/api/anything", headers=vite).status_code == 404


def test_session_returns_the_launch_token(client):
    assert client.get("/api/session").json() == {"token": TOKEN}


def test_security_headers_on_answers_and_refusals(client):
    for r in (client.get("/api/meta"), client.get("/api/meta", headers={"host": "evil:1"})):
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/api/meta").headers["cache-control"] == "no-store"


def test_no_cors_headers_for_other_sites(client):
    r = client.get("/api/session", headers={"origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    r = client.options(
        "/api/session",
        headers={"origin": "http://evil.example", "access-control-request-method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers


def test_no_docs_or_openapi_endpoints(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_unknown_api_path_is_a_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_no_secret_or_key_shaped_text_in_any_response(tmp_path, monkeypatch):
    from nflengine import settings as S

    sentinels = {
        "WANDB_API_KEY": "wandb_v1_SENTINELwandbSENTINELwandb",
        "NEO4J_PASSWORD": "SENTINEL-neo4j-pw-123",
        "OPENROUTER_API_KEY": "sk-or-v1-SENTINELSENTINELSENTINEL",
    }
    for k, v in sentinels.items():
        monkeypatch.setenv(k, v)
    S.get_env.cache_clear()
    try:
        client = make_client(tmp_path)
        for path in SCANNED_GETS:
            body = client.get(path).text
            for v in sentinels.values():
                assert v not in body, path
            assert scrub(body, limit=10**9) == body, f"{path} has text that looks like a secret"
    finally:
        S.get_env.cache_clear()


def test_missing_data_drive(tmp_path):
    def gone():
        raise DataRootError("Data drive D:\\ is not connected (NFL_DATA_ROOT=D:/nfl-ml-data).")

    client = make_client(tmp_path, data_root=gone)
    meta = client.get("/api/meta").json()
    assert meta["data_root"]["found"] is False and meta["calendar"] is None
    r = client.get("/api/weeks")
    assert (r.status_code, r.json()["error"]["code"]) == (503, "data_root_missing")


def test_static_app_and_client_routes(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>CR</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("not for the browser")
    client = make_client(tmp_path / "root", web_dist=dist)
    assert client.get("/").text.startswith("<!doctype html>")
    assert client.get("/week/2026/5/pipeline").text.startswith("<!doctype html>")
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/api/nope").json()["error"]["code"] == "not_found"
    for sneaky in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        assert "not for the browser" not in client.get(sneaky).text


def test_an_unexpected_error_is_scrubbed_and_keeps_the_headers(tmp_path, caplog):
    """Sol review (CR00): a crash's message could carry a credential."""
    from fastapi.routing import APIRoute

    client = make_client(tmp_path)

    def boom():
        raise RuntimeError("password=SENTINEL-pw-777 and wandb_v1_SENTINELwandbSENTINEL")

    client.app.router.routes.insert(0, APIRoute("/api/boom", boom, methods=["GET"]))
    with caplog.at_level("ERROR", logger="nflengine.app"):
        r = client.get("/api/boom")
    assert r.status_code == 500
    assert r.json()["error"] == {
        "code": "server_error",
        "message": "RuntimeError (see the app's console)",
    }
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
    assert "SENTINEL-pw-777" not in caplog.text and "wandb_v1_SENTINEL" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_no_data_root_path_reaches_the_browser(tmp_path):
    """Sol review (CR00): NFL_DATA_ROOT's value is an environment value (README §5.4)."""
    meta = make_client(tmp_path).get("/api/meta").json()
    assert "path" not in meta["data_root"] and str(tmp_path) not in json_text(meta)

    def gone():
        raise DataRootError(
            "Data root Q:/SENTINEL-root does not exist (NFL_DATA_ROOT=Q:/SENTINEL-root)"
        )

    client = make_client(tmp_path / "x", data_root=gone)
    for path in ("/api/meta", "/api/weeks"):
        assert "SENTINEL-root" not in client.get(path).text


def json_text(obj) -> str:
    import json

    return json.dumps(obj)
