"""The history graph's container and its bulk import (AE02). Every command names the history
service (`docker compose --profile history ... neo4j-history`) or its own staging volume;
nothing here runs `docker compose up -d` bare, `down`, or touches the weekly `neo4j` service or
its folders. Command output is captured and never echoed (compose can print warnings about
variables); only exit codes and fixed reasons are reported. The import's own output (file
names and counts, no secrets: the import container gets no environment) goes to a log file.

**How a build replaces the store** (the AE02 probe, 2026-10-18: one season's 1.0M relationships
took 2 m 43 s to import onto the HDD bind mount, 13.6 s onto the SSD, 3.6 s into a Docker
volume): `neo4j-admin database import full` runs in a throw-away container (`--entrypoint
neo4j-admin`, no environment) into the staging **volume**; then, with the history container
stopped, a second throw-away container replaces `data/databases/neo4j` and
`data/transactions/neo4j` under `{NFL_DATA_ROOT}/neo4j-history/` with the staged ones (a
sequential copy, ~15 s for 400 MB) and the volume is removed. The system database (users, the
password) stays as it is.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from nflengine.history_graph.config import HistoryConfig
from nflengine.paths import DataPaths

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DATABASE = "neo4j"  # Community: the one user database
IMPORT_CONTAINER = "nfl-neo4j-history-import"
COPY_CONTAINER = "nfl-neo4j-history-copy"
STOP_TIMEOUT_S = 120  # seconds Neo4j gets to shut down cleanly before Docker kills it
INSTALL_SCRIPT = (
    "set -e; "
    f"rm -rf /data/databases/{DATABASE} /data/transactions/{DATABASE}; "
    "mkdir -p /data/databases /data/transactions; "
    f"cp -a /staging/databases/{DATABASE} /data/databases/{DATABASE}; "
    f"cp -a /staging/transactions/{DATABASE} /data/transactions/{DATABASE}"
)

Runner = Callable[[Sequence[str], float], "Done"]


@dataclass(frozen=True)
class Done:
    code: int
    out: str = ""  # captured stdout + stderr; never printed, only parsed or saved where noted


def run(args: Sequence[str], timeout: float) -> Done:
    """Run a command from the repo root with its output captured; -1 on a timeout or when the
    program can't start."""
    try:
        done = subprocess.run(
            list(args),
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return Done(-1)
    return Done(done.returncode, (done.stdout or "") + (done.stderr or ""))


@dataclass(frozen=True)
class ServiceResult:
    ok: bool
    action: str  # already_up | started | stopped | not_running | failed
    detail: str  # fixed, secret-free wording
    seconds: float = 0.0


class HistoryDockerError(RuntimeError):
    """A docker step of the build failed (fixed, secret-free text)."""


# ---- the compose file (parsed as YAML, never `docker compose config`: CLAUDE.md) --------------


def compose_service(cfg: HistoryConfig, path: Path = COMPOSE_FILE) -> dict[str, Any]:
    """The history service's block of docker-compose.yml. Raises if it's missing or isn't
    under its profile (then a bare `docker compose up -d` would start it)."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    svc = (doc.get("services") or {}).get(cfg.service)
    if not isinstance(svc, dict):
        raise HistoryDockerError(f"docker-compose.yml has no `{cfg.service}` service")
    if cfg.profile not in (svc.get("profiles") or []):
        raise HistoryDockerError(f"`{cfg.service}` isn't under the `{cfg.profile}` profile")
    if svc.get("container_name") != cfg.container:
        raise HistoryDockerError(f"`{cfg.service}`'s container_name isn't {cfg.container}")
    return svc


def image(cfg: HistoryConfig) -> str:
    img = compose_service(cfg).get("image")
    if not isinstance(img, str) or not img:
        raise HistoryDockerError(f"`{cfg.service}` has no image")
    return img


def history_dir(paths: DataPaths, sub: str) -> Path:
    """A folder of the history graph's own (`data`, `logs`, `import`, `plugins`); refuses
    anything that would land in the weekly graph's `neo4j/` folder."""
    p = paths.neo4j_history / sub
    weekly = paths.root / "neo4j"
    if p.resolve() == weekly.resolve() or weekly.resolve() in p.resolve().parents:
        raise HistoryDockerError("refusing a path inside the weekly graph's folder")
    if paths.neo4j_history.resolve() not in p.resolve().parents:
        raise HistoryDockerError("refusing a path outside the history graph's folder")
    return p


def _mount(p: Path) -> str:
    return p.as_posix()


# ---- the container ----------------------------------------------------------------------------


def _compose(cfg: HistoryConfig, *args: str) -> list[str]:
    return ["docker", "compose", "--profile", cfg.profile, *args]


def container_state(cfg: HistoryConfig, runner: Runner = run) -> str:
    """running | exited | created | ... | missing | unknown (docker unreachable)."""
    done = runner(
        ["docker", "ps", "-a", "--filter", f"name=^/{cfg.container}$", "--format", "{{.State}}"],
        30,
    )
    if done.code != 0:
        return "unknown"
    state = done.out.strip().splitlines()[-1].strip() if done.out.strip() else ""
    return state or "missing"


def docker_engine_up(runner: Runner = run) -> bool:
    return runner(["docker", "version", "--format", "{{.Server.Version}}"], 30).code == 0


def _wait(check: Callable[[], bool], timeout_s: float, every_s: float, sleep) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        if check():
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(every_s)


def start(
    cfg: HistoryConfig,
    log: Callable[[str], None] = print,
    *,
    runner: Runner = run,
    up: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    timeout_s: float | None = None,
) -> ServiceResult:
    """Start the history container (only it) and wait until Bolt answers."""
    from nflengine.history_graph.client import history_up

    up = up or (lambda: history_up(cfg))
    t0 = time.monotonic()
    if up():
        return ServiceResult(True, "already_up", "history graph reachable")
    if not docker_engine_up(runner):
        return ServiceResult(
            False, "failed", "the Docker engine isn't running: start Docker Desktop first"
        )
    compose_service(cfg)  # refuses a compose file where the service isn't profiled
    log(f"[yellow]starting the history graph (`docker compose --profile {cfg.profile} up -d "
        f"{cfg.service}`) ...[/]")  # fmt: skip
    code = runner(_compose(cfg, "up", "-d", cfg.service), 600).code
    if code != 0:
        return ServiceResult(
            False, "failed", f"`docker compose up` for {cfg.service} exited with code {code}",
            round(time.monotonic() - t0, 1),
        )  # fmt: skip
    limit = timeout_s if timeout_s is not None else cfg.start_timeout_s
    if not _wait(up, limit, 3, sleep):
        return ServiceResult(
            False, "failed", f"started, but Bolt didn't answer within {limit:.0f} s",
            round(time.monotonic() - t0, 1),
        )  # fmt: skip
    secs = round(time.monotonic() - t0, 1)
    log(f"[green]history graph up after {secs:.0f} s[/]")
    return ServiceResult(True, "started", "history graph started", secs)


def stop(cfg: HistoryConfig, *, runner: Runner = run) -> ServiceResult:
    """Stop the history container (only it); a container that doesn't exist is fine."""
    t0 = time.monotonic()
    compose_service(cfg)  # the service is the profiled history one, or nothing is stopped
    state = container_state(cfg, runner)
    if state in ("missing", "exited", "created"):
        return ServiceResult(True, "not_running", f"history container {state}")
    # Neo4j needs time to checkpoint and close its stores: compose's default 10 s ended in a
    # SIGKILL (exit 137) on the HDD in the first full build (AE02)
    code = runner(_compose(cfg, "stop", "--timeout", str(STOP_TIMEOUT_S), cfg.service), 300).code
    secs = round(time.monotonic() - t0, 1)
    if code != 0:
        return ServiceResult(
            False, "failed", f"`docker compose stop` exited with code {code}", secs
        )
    return ServiceResult(True, "stopped", "history container stopped", secs)


# ---- the bulk import --------------------------------------------------------------------------


def import_args(
    cfg: HistoryConfig, img: str, import_dir: Path, files: dict[str, dict]
) -> list[str]:
    nodes = [f"--nodes={n}=/import/{f['file']}" for n, f in files.items() if f["kind"] == "nodes"]
    rels = [
        f"--relationships={n}=/import/{f['file']}"
        for n, f in files.items()
        if f["kind"] == "relationships"
    ]
    return [
        "docker", "run", "--rm", "--name", IMPORT_CONTAINER, "--entrypoint", "neo4j-admin",
        "-v", f"{_mount(import_dir)}:/import:ro", "-v", f"{cfg.staging_volume}:/data", img,
        "database", "import", "full", DATABASE, "--overwrite-destination",
        "--report-file=/data/import.report", *nodes, *rels,
    ]  # fmt: skip


def import_full(
    paths: DataPaths,
    cfg: HistoryConfig,
    files: dict[str, dict],
    *,
    runner: Runner = run,
    stamp: str,
    timeout_s: float = 3600,
) -> dict[str, Any]:
    """`neo4j-admin database import full` into a fresh staging volume. Returns `{seconds,
    log}`; raises `HistoryDockerError` on failure (the import's output is in the log file)."""
    img = image(cfg)
    import_dir = history_dir(paths, "import")
    logs = history_dir(paths, "logs")
    logs.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    runner(["docker", "rm", "-f", IMPORT_CONTAINER], 60)  # a leftover from a killed build
    runner(["docker", "volume", "rm", "-f", cfg.staging_volume], 120)
    if runner(["docker", "volume", "create", cfg.staging_volume], 60).code != 0:
        raise HistoryDockerError("couldn't create the staging volume")
    done = runner(import_args(cfg, img, import_dir, files), timeout_s)
    if done.code == -1:  # timed out: only the CLI was killed, the container keeps going
        runner(["docker", "rm", "-f", IMPORT_CONTAINER], 60)
    log_path = logs / f"import-{stamp}.log"
    log_path.write_text(done.out, encoding="utf-8")
    if done.code != 0:
        raise HistoryDockerError(
            f"neo4j-admin import exited with code {done.code} (see {log_path.name} in "
            "neo4j-history/logs)"
        )
    return {"seconds": round(time.monotonic() - t0, 1), "log": log_path.name}


def install_store(
    paths: DataPaths, cfg: HistoryConfig, *, runner: Runner = run, timeout_s: float = 1800
) -> float:
    """Replace the history store on D: with the staged one (the container must be stopped)."""
    img = image(cfg)
    data = history_dir(paths, "data")
    data.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    runner(["docker", "rm", "-f", COPY_CONTAINER], 60)
    args = [
        "docker", "run", "--rm", "--name", COPY_CONTAINER, "--entrypoint", "sh",
        "-v", f"{cfg.staging_volume}:/staging:ro", "-v", f"{_mount(data)}:/data", img,
        "-c", INSTALL_SCRIPT,
    ]  # fmt: skip
    code = runner(args, timeout_s).code
    if code == -1:
        runner(["docker", "rm", "-f", COPY_CONTAINER], 60)
    if code != 0:
        raise HistoryDockerError(f"copying the new store to D: failed (exit code {code})")
    return round(time.monotonic() - t0, 1)


def drop_staging(cfg: HistoryConfig, *, runner: Runner = run) -> None:
    runner(["docker", "volume", "rm", "-f", cfg.staging_volume], 120)
