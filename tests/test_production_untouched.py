"""The no-touch guard for the Live decisions, Play calling and Ask the Engine tracks (D107).

The three feature tracks add new models only. They never change the production models, their
features, settings, training or artifacts, the digest, the weekly graph or the weekly run. This
file enforces that with a manifest of SHA-256 hashes (`tests/fixtures/production_manifest.json`):

- every file under the protected folders and the two protected single files, walked on the
  filesystem so a brand-new untracked file inside a protected folder is caught too;
- the parsed value of every top-level block of `config/settings.yaml` that existed when the
  manifest was written (new blocks, such as the track's `live:`, are allowed).

Updating the manifest is a deliberate, separate commit that needs Rishi's OK:

    uv run python tests/test_production_untouched.py --write-manifest

Pure stdlib + yaml + pytest on purpose: nothing here imports `nflengine`, so a change to the
code under guard can never change how the guard itself behaves.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "production_manifest.json"
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"

PROTECTED_DIRS = (
    "src/nflengine/models",
    "src/nflengine/features",
    "src/nflengine/digest",
    "src/nflengine/graph",
    "src/nflengine/ingest",
    "src/nflengine/curate",
    "src/nflengine/ops",
)
PROTECTED_FILES = (
    "src/nflengine/weekly.py",
    "src/nflengine/schedule.py",
)
SKIPPED_SUFFIXES = (".pyc", ".pyo")

MANIFEST_NOTE = (
    "D107 no-touch guard: SHA-256 hashes (line endings normalised to LF) of the protected "
    "production files and of the pre-existing top-level blocks of config/settings.yaml. "
    "Regenerate only with Rishi's OK, in a deliberate separate commit: "
    "uv run python tests/test_production_untouched.py --write-manifest"
)
HEADLINE = "a protected file changed: production models are off limits (D107)"
HINT = (
    "Updating the manifest is a deliberate, separate commit that needs Rishi's OK; "
    "regenerate with: uv run python tests/test_production_untouched.py --write-manifest"
)


# --- hashing ---------------------------------------------------------------------------------


def normalised_sha256(data: bytes) -> str:
    """SHA-256 of the bytes with CRLF folded to LF (core.autocrlf=true here, LF elsewhere)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def protected_paths(root: Path = REPO_ROOT) -> list[Path]:
    """Every protected file that exists under `root`, as absolute paths in sorted POSIX order."""
    found: set[Path] = set()
    for rel in PROTECTED_DIRS:
        for folder, subdirs, names in os.walk(root / rel):
            subdirs[:] = [d for d in subdirs if d != "__pycache__"]
            found.update(Path(folder) / n for n in names if not n.endswith(SKIPPED_SUFFIXES))
    found.update(p for p in (root / rel for rel in PROTECTED_FILES) if p.is_file())
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def hash_protected_files(root: Path = REPO_ROOT) -> dict[str, str]:
    """{repo-relative POSIX path: normalised SHA-256} for every protected file."""
    return {
        p.relative_to(root).as_posix(): normalised_sha256(p.read_bytes())
        for p in protected_paths(root)
    }


# The new tracks' own blocks (D107: "a new `live:` block"; PC00 `playcalling:`; AE `ask:` and
# `history_graph:`): theirs to change, so a regenerated manifest never protects them.
TRACK_BLOCKS = frozenset({"live", "playcalling", "ask", "history_graph"})


def hash_settings_blocks(settings_path: Path = SETTINGS_PATH) -> dict[str, str]:
    """{top-level key: SHA-256 of the block's parsed value}. Comments and layout don't count;
    the tracks' own blocks (`TRACK_BLOCKS`) are left out."""
    data = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    return {
        str(key): hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        for key, value in sorted(data.items(), key=lambda kv: str(kv[0]))
        if str(key) not in TRACK_BLOCKS
    }


# --- comparing -------------------------------------------------------------------------------


def compare_files(
    expected: dict[str, str], root: Path = REPO_ROOT
) -> tuple[list[str], list[str], list[str]]:
    """(changed, added, removed) protected files versus the manifest."""
    actual = hash_protected_files(root)
    changed = sorted(p for p in expected.keys() & actual.keys() if expected[p] != actual[p])
    return changed, sorted(actual.keys() - expected.keys()), sorted(expected.keys() - actual.keys())


def compare_settings(
    expected: dict[str, str], settings_path: Path = SETTINGS_PATH
) -> tuple[list[str], list[str]]:
    """(changed, removed) pre-existing settings blocks. New top-level blocks are allowed."""
    actual = hash_settings_blocks(settings_path)
    changed = sorted(k for k in expected.keys() & actual.keys() if expected[k] != actual[k])
    return changed, sorted(expected.keys() - actual.keys())


def failure_message(details: list[str]) -> str:
    return "\n".join([HEADLINE, *details, HINT])


def file_details(changed: list[str], added: list[str], removed: list[str]) -> list[str]:
    return [
        *(f"  changed file: {p}" for p in changed),
        *(f"  added file: {p}" for p in added),
        *(f"  removed file: {p}" for p in removed),
    ]


def settings_details(changed: list[str], removed: list[str]) -> list[str]:
    return [
        *(f"  changed settings block: {k}" for k in changed),
        *(f"  removed settings block: {k}" for k in removed),
    ]


# --- the manifest ----------------------------------------------------------------------------


def build_manifest(root: Path = REPO_ROOT, settings_path: Path = SETTINGS_PATH) -> dict:
    return {
        "note": MANIFEST_NOTE,
        "files": hash_protected_files(root),
        "settings_blocks": hash_settings_blocks(settings_path),
    }


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(
    path: Path = MANIFEST_PATH, root: Path = REPO_ROOT, settings_path: Path = SETTINGS_PATH
) -> dict:
    manifest = build_manifest(root, settings_path)
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return manifest


# --- tests -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manifest() -> dict:
    return load_manifest()


def test_protected_files_match_manifest(manifest):
    changed, added, removed = compare_files(manifest["files"])
    if changed or added or removed:
        pytest.fail(failure_message(file_details(changed, added, removed)), pytrace=False)


def test_settings_blocks_match_manifest(manifest):
    changed, removed = compare_settings(manifest["settings_blocks"])
    if changed or removed:
        pytest.fail(failure_message(settings_details(changed, removed)), pytrace=False)


def test_manifest_covers_the_protected_surface(manifest):
    files = manifest["files"]
    assert len(files) >= 80, f"only {len(files)} files in the manifest: is the walk broken?"
    for rel in PROTECTED_FILES:
        assert rel in files
    for folder in PROTECTED_DIRS:
        assert any(p.startswith(folder + "/") for p in files), f"nothing recorded under {folder}"
    assert list(files) == sorted(files)
    assert not any("\\" in p or "__pycache__" in p or p.endswith(SKIPPED_SUFFIXES) for p in files)
    assert {"seasons", "game_model", "player_model", "digest"} <= manifest["settings_blocks"].keys()
    assert "D107" in manifest["note"]


def test_crlf_and_lf_content_hash_the_same(tmp_path):
    crlf, lf = tmp_path / "crlf", tmp_path / "lf"
    for root, data in ((crlf, b"a = 1\r\nb = 2\r\n"), (lf, b"a = 1\nb = 2\n")):
        (root / "src/nflengine/models").mkdir(parents=True)
        (root / "src/nflengine/models/m.py").write_bytes(data)
    assert hash_protected_files(crlf) == hash_protected_files(lf)
    assert normalised_sha256(b"a\r\nb") != normalised_sha256(b"a\nc")


def test_walk_sees_new_untracked_files_and_skips_bytecode(tmp_path):
    ops = tmp_path / "src/nflengine/ops"
    (ops / "__pycache__").mkdir(parents=True)
    (ops / "run.py").write_text("x = 1\n")
    (ops / "__pycache__" / "run.cpython-312.pyc").write_bytes(b"\x00")
    (ops / "stray.pyc").write_bytes(b"\x00")
    (tmp_path / "src/nflengine/weekly.py").write_text("w = 1\n")
    (tmp_path / "src/nflengine/live").mkdir()
    (tmp_path / "src/nflengine/live/new_model.py").write_text("not protected\n")
    expected = hash_protected_files(tmp_path)
    assert sorted(expected) == ["src/nflengine/ops/run.py", "src/nflengine/weekly.py"]

    (ops / "sneaky.py").write_text("added = True\n")
    (ops / "run.py").write_text("x = 2\n")
    (tmp_path / "src/nflengine/weekly.py").unlink()
    changed, added, removed = compare_files(expected, tmp_path)
    assert changed == ["src/nflengine/ops/run.py"]
    assert added == ["src/nflengine/ops/sneaky.py"]
    assert removed == ["src/nflengine/weekly.py"]


def test_settings_comparison_ignores_formatting_and_new_blocks(tmp_path):
    original = tmp_path / "settings.yaml"
    original.write_text("seasons:\n  first: 2010\nelo:\n  k: 20  # a comment\nqb: {}\n")
    expected = hash_settings_blocks(original)
    assert set(expected) == {"seasons", "elo", "qb"}

    reformatted = tmp_path / "reformatted.yaml"
    reformatted.write_text(
        "# header\nqb: {}\nelo: {k: 20}\nseasons:\n    first: 2010\nlive:\n  wp_floor: 0.01\n"
    )
    assert compare_settings(expected, reformatted) == ([], [])


def test_settings_comparison_flags_changed_and_removed_blocks(tmp_path):
    original = tmp_path / "settings.yaml"
    original.write_text("seasons:\n  first: 2010\nelo:\n  k: 20\nqb: {}\n")
    expected = hash_settings_blocks(original)

    edited = tmp_path / "edited.yaml"
    edited.write_text("seasons:\n  first: 2011\nelo:\n  k: 20\n")
    changed, removed = compare_settings(expected, edited)
    assert (changed, removed) == (["seasons"], ["qb"])
    message = failure_message(settings_details(changed, removed))
    assert message.startswith(HEADLINE)
    assert "changed settings block: seasons" in message
    assert "removed settings block: qb" in message
    assert message.endswith(HINT)


def test_failure_message_starts_with_the_required_text():
    message = failure_message(file_details(["a.py"], ["b.py"], ["c.py"]))
    assert message.startswith("a protected file changed: production models are off limits (D107)")
    assert "changed file: a.py" in message
    assert "added file: b.py" in message
    assert "removed file: c.py" in message
    assert "needs Rishi's OK" in message
    assert "--write-manifest" in message


def test_write_manifest_round_trips(tmp_path):
    root = tmp_path / "repo"
    (root / "src/nflengine/graph").mkdir(parents=True)
    (root / "src/nflengine/graph/q.cypher").write_text("MATCH (n) RETURN n\n")
    settings = tmp_path / "settings.yaml"
    settings.write_text("seasons: {first: 2010}\n")
    target = tmp_path / "manifest.json"
    written = write_manifest(target, root, settings)
    assert load_manifest(target) == written
    assert list(written["files"]) == ["src/nflengine/graph/q.cypher"]
    assert target.read_bytes().endswith(b"}\n")
    assert b"\r\n" not in target.read_bytes()
    assert compare_files(written["files"], root) == ([], [], [])
    assert compare_settings(written["settings_blocks"], settings) == ([], [])


# --- regenerating the manifest ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D107 no-touch guard: manifest helper.")
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="rewrite tests/fixtures/production_manifest.json from the working tree "
        "(needs Rishi's OK, in a separate commit)",
    )
    args = parser.parse_args(argv)
    if not args.write_manifest:
        parser.print_help()
        return 2
    manifest = write_manifest()
    print(
        f"wrote {MANIFEST_PATH.relative_to(REPO_ROOT).as_posix()}: "
        f"{len(manifest['files'])} files, {len(manifest['settings_blocks'])} settings blocks"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
