"""LD00 on the real, promoted bundle (`-m integration`; reads `models/live-decisions` on D:).

The phase file's decision tests on the shipped models: the hand-made 4th downs get the call
football analysts would make, a full 3rd-down check (bootstraps included) stays under 20 ms,
the bootstrap gate holds on real 4th downs (D113), and the bundle never saw a row of a
season after its training window."""

from __future__ import annotations

import json
import statistics

import pytest

from nflengine.live import train as T
from nflengine.live.decide import Engine, decide_settings
from nflengine.live.schema import GameState
from nflengine.paths import ensure_data_root

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def bundle():
    paths = ensure_data_root(create_dirs=False)
    try:
        folder = T.production_folder(paths)
    except FileNotFoundError:
        pytest.skip("no promoted live-decision models (nfl live train --promote)")
    return T.M.load(folder), folder


@pytest.fixture(scope="module")
def engine(bundle):
    eng = Engine(bundle[0], decide_settings())
    yield eng
    eng.close()


@pytest.mark.parametrize(("name", "state", "want"), T.CHECKS, ids=[c[0] for c in T.CHECKS])
def test_hand_made_calls(engine, name, state, want) -> None:
    d = engine.fourth_down(state)
    assert d.best == want, f"{name}: the bot says {d.best} ({d.wp}), expected {want}"


def test_more_situations(engine) -> None:
    # 4th & goal at the 1, down 4 with 10 s left: only a touchdown wins
    d = engine.fourth_down(GameState(2025, -4, 10, 10, 4, 1, 1))
    assert d.best == "go" and d.wp["fg"] < 0.01
    # 4th & 10 at the own 10, up 1 early in Q3: punt
    assert engine.fourth_down(GameState(2025, 1, 1700, 1700, 4, 10, 90)).best == "punt"
    # a 58-yard try is worth less than a 28-yard one, everything else equal
    long = engine.fourth_down(GameState(2025, 0, 2000, 200, 4, 8, 40))
    short = engine.fourth_down(GameState(2025, 0, 2000, 200, 4, 8, 10))
    assert long.fg_make < short.fg_make


@pytest.mark.parametrize(("name", "state"), T.TIMING_STATES, ids=[s[0] for s in T.TIMING_STATES])
def test_third_down_timing(engine, name, state) -> None:
    """A guard against big slowdowns with 25% headroom for a busy machine: the strict 20 ms
    budget is enforced on an idle machine at train time (`checks.json`, asserted below)."""
    engine.third_down(state)
    ms = [engine.third_down(state).ms for _ in range(15)]
    limit = 1.25 * T.TIMING_BUDGET_MS
    assert statistics.median(ms) < limit, f"{name}: median {statistics.median(ms)} ms"


def test_bundle_meta_and_checks(bundle) -> None:
    models, folder = bundle
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert max(meta["seasons"]) <= 2025, "the shipped fit must not include the live season"
    checks = json.loads((folder / "checks.json").read_text(encoding="utf-8"))
    assert checks["calls_ok"] and checks["timing_ok"]
    assert checks["gate"] is None or checks["gate"]["ok"]
    assert len(models.gain_boot) == len(models.fg_boot) == meta["n_boot"]
