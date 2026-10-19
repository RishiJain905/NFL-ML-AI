"""The compose file's history service (AE02), parsed with yaml (never `docker compose config`,
which CLAUDE.md forbids): the weekly run's bare `docker compose up -d` starts the weekly
`neo4j` and never the history service; the two share nothing (container, ports, folders); the
weekly service block is pinned so an edit for the history graph can't drift it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from nflengine.history_graph import docker as D
from nflengine.history_graph.config import HistoryConfig, history_config

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
SRC = ROOT / "src" / "nflengine"

# the weekly `neo4j` service exactly as it was before AE02 (and as P05-P10 ran it)
WEEKLY_PINNED = {
    "image": "neo4j:5.26-community",
    "container_name": "nfl-neo4j",
    "ports": ["7474:7474", "7687:7687"],
    "environment": {
        "NEO4J_AUTH": "${NEO4J_USER:-neo4j}/${NEO4J_PASSWORD:?NEO4J_PASSWORD must be set}",
        "NEO4J_PLUGINS": '["apoc", "graph-data-science"]',
        "NEO4J_dbms_security_procedures_unrestricted": "gds.*,apoc.*",
        "NEO4J_dbms_security_procedures_allowlist": "gds.*,apoc.*",
        "NEO4J_server_memory_heap_initial__size": "1G",
        "NEO4J_server_memory_heap_max__size": "2G",
        "NEO4J_server_memory_pagecache_size": "1G",
    },
    "volumes": [
        "${NFL_DATA_ROOT:?NFL_DATA_ROOT must be set}/neo4j/data:/data",
        "${NFL_DATA_ROOT}/neo4j/logs:/logs",
        "${NFL_DATA_ROOT}/neo4j/plugins:/plugins",
    ],
    "restart": "unless-stopped",
}


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def weekly(compose) -> dict:
    return compose["services"]["neo4j"]


@pytest.fixture(scope="module")
def hist(compose) -> dict:
    return compose["services"]["neo4j-history"]


def started_by_bare_up(compose: dict) -> set[str]:
    """What `docker compose up -d` starts with no `--profile`: the services without profiles."""
    return {n for n, s in compose["services"].items() if not s.get("profiles")}


def started_with_profile(compose: dict, profile: str) -> set[str]:
    return {
        n
        for n, s in compose["services"].items()
        if not s.get("profiles") or profile in s["profiles"]
    }


def host_ports(service: dict) -> set[int]:
    return {int(p.split(":")[0]) for p in service["ports"]}


# ---- the profile -----------------------------------------------------------------------------


def test_the_compose_file_has_exactly_the_two_services(compose):
    assert set(compose) == {"services"}  # no top-level volumes / networks added
    assert set(compose["services"]) == {"neo4j", "neo4j-history"}


def test_the_weekly_service_has_no_profile_so_a_bare_up_starts_it(compose, weekly):
    assert "profiles" not in weekly
    assert "neo4j" in started_by_bare_up(compose)


def test_the_history_service_is_under_the_history_profile_so_a_bare_up_never_starts_it(
    compose, hist
):
    assert hist["profiles"] == ["history"]
    assert started_by_bare_up(compose) == {"neo4j"}
    assert started_with_profile(compose, "history") == {"neo4j", "neo4j-history"}
    assert started_with_profile(compose, "some-other-profile") == {"neo4j"}


def test_the_weekly_runs_up_command_is_the_bare_one(compose):
    # ops.services.ensure_neo4j (what `nfl weekly run` calls) runs `docker compose up -d` with
    # no profile and no service name: with the history service profiled, that starts `neo4j` only
    src = SRC / "ops" / "services.py"
    assert '["docker", "compose", "up", "-d"]' in src.read_text(encoding="utf-8")
    assert started_by_bare_up(compose) == {"neo4j"}


def test_the_weekly_start_up_code_never_mentions_the_history_graph():
    for f in (SRC / "ops" / "services.py", SRC / "graph" / "client.py"):
        text = f.read_text(encoding="utf-8")
        assert "--profile" not in text and "neo4j-history" not in text, f.name


def test_the_history_package_builds_no_destructive_docker_command():
    for name in ("docker.py", "build.py", "append.py", "status.py", "client.py", "state.py"):
        f = SRC / "history_graph" / name  # (export.py has a `down` column; schema.py too)
        text = f.read_text(encoding="utf-8")
        found = re.findall(r"""["'](down|inspect|prune|kill|config)["']""", text)
        assert found == [], f"{f.name} builds a forbidden docker argument: {found}"
        assert '"-v", "--volumes"' not in text
        # compose commands are built in one place, which always puts --profile first
        assert len(re.findall(r'"docker", "compose"', text)) <= 1, f.name


# ---- nothing shared --------------------------------------------------------------------------


def test_container_names_differ(weekly, hist):
    assert weekly["container_name"] == "nfl-neo4j"
    assert hist["container_name"] == "nfl-neo4j-history"


def test_host_ports_differ(weekly, hist):
    assert host_ports(weekly) == {7474, 7687}
    assert host_ports(hist) == {7475, 7688}
    assert hist["ports"] == ["7475:7474", "7688:7687"]  # Browser, Bolt
    assert not host_ports(weekly) & host_ports(hist)


def test_data_folders_differ_and_stay_inside_their_own_tree(weekly, hist):
    def sources(s):
        return [v.split(":/", 1)[0] for v in s["volumes"]]

    w, h = sources(weekly), sources(hist)
    assert all("/neo4j/" in p and "/neo4j-history/" not in p for p in w)
    assert all("/neo4j-history/" in p and "/neo4j/" not in p for p in h)
    assert not set(w) & set(h)
    assert {v.split(":/", 1)[1] for v in hist["volumes"]} == {"data", "logs", "import", "plugins"}
    # the history import folder is the one the build's CSVs go to
    assert any(v.endswith("/neo4j-history/import:/import") for v in hist["volumes"])


def test_no_named_volumes_are_shared_or_declared(compose, weekly, hist):
    for s in (weekly, hist):
        for v in s["volumes"]:
            assert v.startswith("${NFL_DATA_ROOT"), v  # bind mounts under the data root only


def test_bolt_advertised_address_is_the_history_port(hist):
    env = hist["environment"]
    assert env["NEO4J_server_bolt_advertised__address"] == "localhost:7688"
    assert env["NEO4J_server_http_advertised__address"] == "localhost:7475"


def test_the_weekly_service_does_not_advertise_the_history_ports(weekly):
    text = str(weekly)
    assert "7688" not in text and "7475" not in text


def test_same_image_and_plugins_as_the_weekly_graph(weekly, hist):
    for k in ("image",):
        assert hist[k] == weekly[k]
    for k in (
        "NEO4J_PLUGINS",
        "NEO4J_dbms_security_procedures_unrestricted",
        "NEO4J_dbms_security_procedures_allowlist",
    ):
        assert hist["environment"][k] == weekly["environment"][k]


def test_auth_uses_the_same_variables_and_no_new_secret(weekly, hist):
    assert hist["environment"]["NEO4J_AUTH"] == weekly["environment"]["NEO4J_AUTH"]
    text = yaml.safe_dump(hist)
    assert set(re.findall(r"\$\{(\w+)", text)) == {"NEO4J_USER", "NEO4J_PASSWORD", "NFL_DATA_ROOT"}
    # nothing that looks like a literal secret is in the service block
    assert not re.search(r"(password|token|key)\s*[:=]\s*['\"]?[A-Za-z0-9]{8,}", text, re.I)


def test_no_secret_value_in_the_whole_file():
    text = COMPOSE.read_text(encoding="utf-8")
    for var in re.findall(r"\$\{([A-Z_]+)[:?\-}]", text):
        assert var in {"NEO4J_USER", "NEO4J_PASSWORD", "NFL_DATA_ROOT"}, var


def test_the_history_heap_is_smaller_than_the_weekly_one(weekly, hist):
    def mb(v):
        n = int(re.match(r"(\d+)", v).group(1))
        return n * 1024 if v.upper().endswith("G") else n

    w, h = weekly["environment"], hist["environment"]
    assert mb(h["NEO4J_server_memory_heap_max__size"]) < mb(w["NEO4J_server_memory_heap_max__size"])
    assert mb(h["NEO4J_server_memory_heap_initial__size"]) <= mb(
        h["NEO4J_server_memory_heap_max__size"]
    )


def test_started_when_wanted_not_with_docker(hist, weekly):
    assert hist["restart"] == "no"  # the string "no", not YAML's boolean false
    assert weekly["restart"] == "unless-stopped"


def test_the_history_service_stops_gracefully_and_sends_no_usage_data(hist, weekly):
    assert hist["stop_grace_period"] == "2m"  # `docker compose stop` default 10 s SIGKILLed Neo4j
    assert hist["environment"]["NEO4J_dbms_usage__report_enabled"] == "false"
    assert "stop_grace_period" not in weekly
    assert "NEO4J_dbms_usage__report_enabled" not in weekly["environment"]
    assert D.STOP_TIMEOUT_S == 120


# ---- the weekly block is pinned --------------------------------------------------------------


def test_the_weekly_neo4j_service_block_equals_its_pinned_value(weekly):
    assert weekly == WEEKLY_PINNED


# ---- the config matches the compose file -----------------------------------------------------


def test_default_config_matches_the_compose_service(hist):
    cfg = HistoryConfig()
    assert cfg.service == "neo4j-history"
    assert cfg.container == hist["container_name"]
    assert cfg.profile in hist["profiles"]
    assert cfg.bolt_uri.endswith(":7688") and cfg.browser_url.endswith(":7475")


def test_compose_service_accepts_the_real_file_and_returns_the_history_block(hist):
    got = D.compose_service(HistoryConfig(), COMPOSE)
    assert got == hist


def test_the_repo_compose_file_is_the_one_docker_py_reads():
    assert D.COMPOSE_FILE == COMPOSE


def test_the_image_comes_from_the_compose_file(weekly):
    assert D.image(HistoryConfig()) == weekly["image"] == "neo4j:5.26-community"


def test_settings_yaml_block_validates_and_agrees_with_compose(hist):
    cfg = history_config()  # config/settings.yaml's history_graph block (no env file read)
    assert cfg.service == "neo4j-history" and cfg.container == hist["container_name"]
    assert cfg.profile in hist["profiles"]
    assert cfg.bolt_uri == "bolt://localhost:7688"
    assert cfg.browser_url == "http://localhost:7475"
    assert cfg.staging_volume.startswith("nfl-neo4j-history")
    D.compose_service(cfg, COMPOSE)


# ---- compose_service refuses a file that lost the profile ------------------------------------


def write_compose(tmp_path: Path, history: dict | None, extra: dict | None = None) -> Path:
    services = {"neo4j": {"image": "x", "container_name": "nfl-neo4j"}}
    if history is not None:
        services["neo4j-history"] = history
    services.update(extra or {})
    p = tmp_path / "docker-compose.yml"
    p.write_text(yaml.safe_dump({"services": services}), encoding="utf-8")
    return p


GOOD = {"image": "x", "container_name": "nfl-neo4j-history", "profiles": ["history"]}


def test_compose_service_accepts_a_minimal_good_block(tmp_path):
    assert D.compose_service(HistoryConfig(), write_compose(tmp_path, GOOD)) == GOOD


def test_compose_service_refuses_a_missing_service(tmp_path):
    with pytest.raises(D.HistoryDockerError, match="has no `neo4j-history` service"):
        D.compose_service(HistoryConfig(), write_compose(tmp_path, None))


def test_compose_service_refuses_a_service_that_lost_its_profile(tmp_path):
    bad = {k: v for k, v in GOOD.items() if k != "profiles"}
    with pytest.raises(D.HistoryDockerError, match="isn't under the `history` profile"):
        D.compose_service(HistoryConfig(), write_compose(tmp_path, bad))


def test_compose_service_refuses_an_empty_profile_list(tmp_path):
    with pytest.raises(D.HistoryDockerError, match="profile"):
        D.compose_service(HistoryConfig(), write_compose(tmp_path, {**GOOD, "profiles": []}))


def test_compose_service_refuses_another_profile(tmp_path):
    with pytest.raises(D.HistoryDockerError, match="profile"):
        D.compose_service(HistoryConfig(), write_compose(tmp_path, {**GOOD, "profiles": ["x"]}))


def test_compose_service_refuses_a_renamed_container(tmp_path):
    with pytest.raises(D.HistoryDockerError, match="container_name isn't nfl-neo4j-history"):
        D.compose_service(
            HistoryConfig(), write_compose(tmp_path, {**GOOD, "container_name": "nfl-neo4j"})
        )


def test_compose_service_refuses_a_service_with_no_container_name(tmp_path):
    bad = {k: v for k, v in GOOD.items() if k != "container_name"}
    with pytest.raises(D.HistoryDockerError, match="container_name"):
        D.compose_service(HistoryConfig(), write_compose(tmp_path, bad))


def test_compose_service_refuses_a_non_mapping_service(tmp_path):
    with pytest.raises(D.HistoryDockerError, match="no `neo4j-history` service"):
        D.compose_service(HistoryConfig(), write_compose(tmp_path, "oops"))  # type: ignore[arg-type]


def test_compose_service_on_an_empty_file(tmp_path):
    p = tmp_path / "docker-compose.yml"
    p.write_text("", encoding="utf-8")
    with pytest.raises(D.HistoryDockerError):
        D.compose_service(HistoryConfig(), p)


def test_image_refuses_a_service_without_one(tmp_path, monkeypatch):
    no_image = {k: v for k, v in GOOD.items() if k != "image"}
    p = write_compose(tmp_path, no_image)
    real = D.compose_service
    monkeypatch.setattr(D, "compose_service", lambda cfg, path=p: real(cfg, path))
    with pytest.raises(D.HistoryDockerError, match="has no image"):
        D.image(HistoryConfig())
