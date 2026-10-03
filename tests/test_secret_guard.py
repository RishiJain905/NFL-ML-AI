"""Regression tests for the credential guard hook (.claude/hooks/block_secrets.py)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "block_secrets.py"
ENV_NAME = ".e" + "nv"  # built at runtime so this file's text stays neutral

CASES = [
    (True, "Read", {"file_path": f"F:/repo/{ENV_NAME}"}),
    (True, "Read", {"file_path": f"F:/repo/sub/{ENV_NAME}.local"}),
    (False, "Read", {"file_path": f"F:/repo/{ENV_NAME}.example"}),
    (True, "Bash", {"command": f"cat ./sub/{ENV_NAME}"}),
    (True, "Bash", {"command": "printenv"}),
    (True, "Bash", {"command": "env"}),
    (True, "Bash", {"command": "echo $NEO4J_PASSWORD"}),
    (True, "Bash", {"command": "docker compose config"}),
    (True, "Bash", {"command": "docker inspect nfl-neo4j"}),
    (True, "PowerShell", {"command": "Get-ChildItem env:"}),
    (True, "PowerShell", {"command": "Write-Output $env:WANDB_API_KEY"}),
    (True, "Glob", {"pattern": f"**/{ENV_NAME}*"}),
    # Gaps found in the P00 review
    (
        True,
        "Bash",
        {"command": 'python -c "from dotenv import dotenv_values; print(dotenv_values())"'},
    ),
    (True, "Bash", {"command": "set | grep KEY"}),
    (True, "Bash", {"command": "export -p"}),
    (True, "Bash", {"command": "declare -x"}),
    (True, "Bash", {"command": "cmd /c set"}),
    (True, "Bash", {"command": "python -c 'from os import environ; print(environ)'"}),
    (True, "Bash", {"command": "uv run python -c 's.neo4j_password.get_secret_value()'"}),
    (True, "PowerShell", {"command": "Get-Item env:WANDB_API_KEY"}),
    (True, "PowerShell", {"command": "[Environment]::GetEnvironmentVariable('X')"}),
    (False, "Bash", {"command": "set -euo pipefail; uv run nfl doctor"}),
    (False, "PowerShell", {"command": "Set-Location F:/Personal/NFL/NFL-ML-AI"}),
    (False, "Bash", {"command": "uv run pytest && uv run ruff check"}),
    (False, "Bash", {"command": "docker compose up -d && docker compose ps"}),
    (False, "Bash", {"command": "uv venv .venv && uv sync"}),
    (False, "PowerShell", {"command": "$env:UV_CACHE_DIR = 'D:/nfl-ml-data/cache/uv'"}),
]


@pytest.mark.parametrize(("blocked", "tool", "tool_input"), CASES)
def test_guard(blocked: bool, tool: str, tool_input: dict) -> None:
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True
    )
    assert (proc.returncode == 2) is blocked, proc.stderr


def test_guard_fails_closed_on_unparseable_input() -> None:
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input="not json", capture_output=True, text=True
    )
    assert proc.returncode == 2
