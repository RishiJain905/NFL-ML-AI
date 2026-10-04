"""Start what the weekly run needs before it needs it (P07; documentation/02 -> Failure
handling: "Neo4j down: try to start the container once").

`ensure_neo4j()` pings Neo4j; if it is down it starts Docker Desktop when the Docker engine
isn't running (Windows), runs `docker compose up -d` once from the repo root, and waits for
Neo4j to answer. It never raises: the result says what it did, and the `graph` step then
either builds or degrades as before (D61). Command output is never echoed (compose can
print warnings about variables); only exit codes and fixed reasons are reported.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")


@dataclass(frozen=True)
class ServiceResult:
    ok: bool
    action: str  # already_up | started_container | started_docker_and_container | failed
    detail: str  # fixed, secret-free wording
    seconds: float = 0.0


class Neo4jConfigProblem(RuntimeError):
    """Neo4j can't be used for a reason starting containers won't fix (credentials)."""


def neo4j_up() -> bool:
    """True if Neo4j answers (short timeout), False if it isn't reachable. Raises
    `Neo4jConfigProblem` (a fixed, secret-free reason) for anything else, e.g. a rejected
    password or NEO4J_PASSWORD not set."""
    try:
        from nflengine.graph.client import get_driver

        driver = get_driver(connection_timeout=5.0)
        try:
            driver.verify_connectivity()
        finally:
            driver.close()
        return True
    except Exception as e:
        from nflengine.graph.client import safe_error

        if type(e).__name__ in ("ServiceUnavailable", "SessionExpired") or isinstance(e, OSError):
            return False
        raise Neo4jConfigProblem(safe_error(e)) from None


def _answers(ping: Callable[[], bool]) -> bool:
    try:
        return ping()
    except Neo4jConfigProblem:
        return False


def _run(args: list[str], timeout: float) -> int:
    """Run a command with its output captured and discarded; return the exit code."""
    try:
        done = subprocess.run(
            args,
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return -1
    return done.returncode


def docker_engine_up(run: Callable[[list[str], float], int] = _run) -> bool:
    return run(["docker", "version", "--format", "{{.Server.Version}}"], 30) == 0


def _wait(check: Callable[[], bool], timeout_s: float, every_s: float, sleep) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        if check():
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(every_s)


def ensure_neo4j(
    log: Callable[[str], None] = print,
    neo4j_timeout_s: float = 180,
    docker_timeout_s: float = 240,
    *,
    ping: Callable[[], bool] = neo4j_up,
    run: Callable[[list[str], float], int] = _run,
    start_desktop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ServiceResult:
    """Make sure Neo4j is reachable, starting Docker Desktop and the container once if not."""
    t0 = time.monotonic()
    try:
        if ping():
            return ServiceResult(True, "already_up", "Neo4j reachable")
    except Neo4jConfigProblem as e:  # e.g. a rejected password: starting Docker won't help
        return ServiceResult(False, "failed", f"Neo4j not usable ({e}); nothing was started")
    if shutil.which("docker") is None and run is _run:
        return ServiceResult(False, "failed", "Neo4j down and the docker CLI isn't installed")
    action = "started_container"
    if not docker_engine_up(run):
        starter = start_desktop or _start_docker_desktop
        log("[yellow]Docker engine not running: starting Docker Desktop ...[/]")
        if not starter():
            return ServiceResult(
                False, "failed", "Neo4j down and Docker Desktop couldn't be started"
            )
        if not _wait(lambda: docker_engine_up(run), docker_timeout_s, 5, sleep):
            return ServiceResult(
                False,
                "failed",
                f"Docker engine not up {docker_timeout_s:.0f} s after starting Docker Desktop",
                round(time.monotonic() - t0, 1),
            )
        action = "started_docker_and_container"
    log("[yellow]Neo4j down: `docker compose up -d` (once) ...[/]")
    code = run(["docker", "compose", "up", "-d"], 300)
    if code != 0:
        return ServiceResult(
            False,
            "failed",
            f"`docker compose up -d` exited with code {code}",
            round(time.monotonic() - t0, 1),
        )
    if not _wait(lambda: _answers(ping), neo4j_timeout_s, 5, sleep):
        return ServiceResult(
            False,
            "failed",
            f"container started but Neo4j didn't answer within {neo4j_timeout_s:.0f} s",
            round(time.monotonic() - t0, 1),
        )
    secs = round(time.monotonic() - t0, 1)
    log(f"[green]Neo4j up after {secs:.0f} s ({action.replace('_', ' ')})[/]")
    return ServiceResult(True, action, f"Neo4j was down; {action.replace('_', ' ')}", secs)


def _start_docker_desktop() -> bool:
    if sys.platform != "win32" or not DOCKER_DESKTOP.exists():
        return False
    try:
        subprocess.Popen(  # detached: Docker Desktop keeps running after the pipeline
            [str(DOCKER_DESKTOP)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError:
        return False
    return True
