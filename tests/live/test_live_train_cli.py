"""LD00 train / CLI: `run_checks`, the bootstrap gate check, the production pointer, `run_train`'s
flow (fit, checks and the promotion rules, with the fit and the data stubbed) and `nfl live ...`
through typer's runner. No data root, no W&B, no network."""

from __future__ import annotations

import dataclasses
import json
import re

import numpy as np
import polars as pl
import pytest
from live_stubs import (
    FIT_SEASONS,
    engine,
    fitted_bundle,
    make_models,
    random_state,
    simple_punt,
    small_cfg,
)
from typer.testing import CliRunner

from nflengine import tracking
from nflengine.cli import app
from nflengine.live import data as D
from nflengine.live import models as M
from nflengine.live import schema
from nflengine.live import train as T
from nflengine.live.models import FitReport
from nflengine.live.schema import GameState
from nflengine.paths import DataPaths

runner = CliRunner()
AGREE = {0: 0.5, 5: 0.5}
WIDE = GameState(2025, 0, 1500, 1500, 4, 15, 80)  # 4th & 15 at the own 20: a clear punt


def flat(text: str) -> str:
    """CLI error text without rich's box drawing, on one line."""
    return " ".join(re.sub(r"[│╭╮╰╯─┃┏┓┗┛━]", " ", text).split())


# --- run_checks and gate_check -----------------------------------------------------------------
def test_run_checks_on_a_stub_bundle() -> None:
    out = T.run_checks(make_models(boots=[AGREE] * 3), repeats=2)
    assert set(out) == {"gate", "calls", "calls_ok", "timing", "timing_ok", "budget_ms"}
    assert out["gate"] is None, "no gate states given: no gate"
    assert out["budget_ms"] == T.TIMING_BUDGET_MS == 20.0
    assert len(out["calls"]) == len(T.CHECKS) == 3
    for c, (name, _state, want) in zip(out["calls"], T.CHECKS, strict=True):
        assert set(c) == {"state", "want", "got", "ok", "gap", "label", "ms", "wp"}
        assert c["state"] == name and c["want"] == want and c["ok"] == (c["got"] == want)
        assert c["got"] in ("go", "fg", "punt") and set(c["wp"]) == {"go", "fg", "punt"}
    assert out["calls_ok"] is True, (
        f"the stub's hand-made calls: {[c['got'] for c in out['calls']]}"
    )
    assert len(out["timing"]) == len(T.TIMING_STATES) == 2
    for t in out["timing"]:
        assert set(t) == {"state", "rows", "median_ms", "max_ms"}
        assert t["rows"] > 0 and 0 <= t["median_ms"] <= t["max_ms"]
    assert out["timing_ok"] is True
    json.dumps(out)  # lands in checks.json


def test_run_checks_reports_a_wrong_call() -> None:
    """A bundle that never converts can't 'go for it' on 4th & 1: calls_ok must say so."""
    out = T.run_checks(make_models(gain={0: 1.0}), repeats=1)
    first = out["calls"][0]
    assert first["want"] == "go" and first["got"] != "go" and first["ok"] is False
    assert out["calls_ok"] is False


def test_run_checks_with_gate_states() -> None:
    states = [WIDE, GameState(2025, 0, 1500, 1500, 4, 12, 78)]
    out = T.run_checks(make_models(boots=[AGREE] * 3), repeats=1, gate_states=states)
    assert out["gate"] is not None
    assert set(out["gate"]) == {"states", "wide", "below_confident", "worst_share", "ok"}
    assert out["gate"]["states"] == 2 and out["gate"]["ok"] is True


def test_run_checks_on_a_fitted_bundle_has_the_same_shape() -> None:
    """The real LightGBM path (bootstrap refits, threads): the structure holds, the calls
    themselves are meaningless on synthetic data."""
    rng = np.random.default_rng(2)
    states = [random_state(rng).replace(down=4) for _ in range(6)]
    out = T.run_checks(fitted_bundle(), repeats=1, gate_states=states)
    assert set(out) == {"gate", "calls", "calls_ok", "timing", "timing_ok", "budget_ms"}
    assert out["gate"]["states"] == 6 and len(out["calls"]) == 3
    for c in out["calls"]:
        assert all(0.0 <= x <= 1.0 for x in c["wp"].values() if x is not None)
    json.dumps(out)


def test_gate_check_counts_only_wide_calls() -> None:
    with engine(make_models(boots=[AGREE] * 4)) as eng:
        g = T.gate_check(eng, [WIDE, WIDE])
    assert g == {"states": 2, "wide": 2, "below_confident": 0, "worst_share": 1.0, "ok": True}
    tie = make_models(gain={0: 1.0}, punt=simple_punt(net=0), boots=[AGREE] * 4)
    with engine(tie) as eng:
        g = T.gate_check(eng, [GameState(2025, 0, 1500, 1500, 4, 3, 60)])
    assert g["wide"] == 0 and g["ok"] is True and g["worst_share"] == 1.0, "a tie is not 'wide'"


def test_gate_check_fails_when_a_wide_call_flips_in_the_bootstraps() -> None:
    flipping = make_models(boots=[{20: 1.0}] * 4)  # every refit says going for it converts
    with engine(flipping) as eng:
        g = T.gate_check(eng, [WIDE])
    assert g["wide"] == 1 and g["below_confident"] == 1 and g["worst_share"] == 0.0
    assert g["ok"] is False, "the promotion must be refused when the D113 gate fails"


# --- the production pointer ---------------------------------------------------------------------
def test_root_and_production_folder(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    assert T.root(paths) == tmp_path / "models" / "live-decisions"
    with pytest.raises(FileNotFoundError, match="nfl live train --promote"):
        T.production_folder(paths)
    T.root(paths).mkdir(parents=True)
    (T.root(paths) / "production.json").write_text(
        json.dumps({"version": "2010-2025_20261010T000000Z"}), encoding="utf-8"
    )
    assert T.production_folder(paths) == T.root(paths) / "2010-2025_20261010T000000Z"


def test_load_production_loads_the_pointed_bundle(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    folder = T.root(paths) / "v1"
    M.save(fitted_bundle(), folder)
    (T.root(paths) / "production.json").write_text(json.dumps({"version": "v1"}), encoding="utf-8")
    models = T.load_production(paths)
    X = M.matrix(pl.DataFrame({f: [1.0] for f in schema.WP_FEATURES}), schema.WP_FEATURES)
    assert np.allclose(models.wp_prob(X), fitted_bundle().wp_prob(X), atol=1e-12)
    # the pointer names a folder that isn't there
    (T.root(paths) / "production.json").write_text(
        json.dumps({"version": "gone"}), encoding="utf-8"
    )
    with pytest.raises(FileNotFoundError):
        T.load_production(paths)


# --- run_train: fit and data stubbed, the rest is real -----------------------------------------
@pytest.fixture()
def train_env(monkeypatch):
    """`run_train` with the frames and the fit stubbed (the fitted synthetic bundle stands in)
    and the git / data lookups pinned. Returns a dict the test can add checks to."""
    env = {"fit_seasons": None, "checks": None}

    def fake_frames(seasons, gain_ratings=False, paths=None):
        env["fit_seasons"] = list(seasons)
        return {"plays": pl.DataFrame({"season": list(seasons)})}

    def fake_fit(frames, seasons, cfg=None, n_boot=None, gain_ratings=False, log=print,
                 callbacks=None):  # fmt: skip
        assert n_boot is None and cfg["n_boot"] == 2, "run_train passes --n-boot through the config"
        models = dataclasses.replace(fitted_bundle(), meta={"seasons": sorted(seasons)})
        return models, FitReport(seconds={"wp": 0.1}, rows={"wp": 10})

    ok = {"gate": None, "calls": [], "calls_ok": True, "timing": [], "timing_ok": True,
          "budget_ms": 20.0}  # fmt: skip

    def fake_checks(models, repeats=15, gate_states=None):
        return env["checks"] or ok

    monkeypatch.setattr(T, "load_frames", fake_frames)
    monkeypatch.setattr(T.M, "fit_all", fake_fit)
    monkeypatch.setattr(T.M, "settings", lambda overrides=None: small_cfg())
    monkeypatch.setattr(T, "run_checks", fake_checks)
    monkeypatch.setattr(T, "sample_fourth_downs", lambda plays, season, n=400, seed=1: [])
    monkeypatch.setattr(D, "wp_frame", lambda plays: (plays, {"rows": 1, "tie_rows": 0,
                                                              "tie_games": 0}))  # fmt: skip
    monkeypatch.setattr(tracking, "git_commit", lambda: "abc1234")
    return env


def test_run_train_writes_the_bundle_without_promoting(tmp_path, train_env) -> None:
    paths = DataPaths(tmp_path)
    log: list[str] = []
    out = T.run_train(seasons=[2018, 2019, 2020], n_boot=2, use_wandb=False, log=log.append,
                      paths=paths)  # fmt: skip
    assert train_env["fit_seasons"] == [2018, 2019, 2020]
    assert out["promoted"] is False and out["url"] is None and out["aliases"] == []
    assert out["version"].startswith("2018-2020_") and out["folder"].name == out["version"]
    assert out["folder"].parent == T.root(paths)
    names = {p.name for p in out["folder"].iterdir()}
    assert {"wp.txt", "wp_base.json", "gain.txt", "fg.json", "meta.json", "checks.json"} <= names
    meta = json.loads((out["folder"] / "meta.json").read_text(encoding="utf-8"))
    assert meta["version"] == out["version"] and meta["git_commit"] == "abc1234"
    assert meta["seasons"] == [2018, 2019, 2020] and "dataset_version" in meta
    assert not (T.root(paths) / "production.json").exists(), "no --promote: no pointer"
    assert any("fitting on 2018-2020" in line for line in log)


def test_run_train_promote_writes_the_pointer(tmp_path, train_env) -> None:
    paths = DataPaths(tmp_path)
    out = T.run_train(seasons=FIT_SEASONS, promote=True, n_boot=2, use_wandb=False,
                      log=lambda _s: None, paths=paths)  # fmt: skip
    pointer = json.loads((T.root(paths) / "production.json").read_text(encoding="utf-8"))
    assert pointer["version"] == out["version"] and pointer["seasons"] == FIT_SEASONS
    assert pointer["wandb_run"] is None and pointer["promoted_at"].endswith("+00:00")
    assert T.production_folder(paths) == out["folder"]
    assert isinstance(T.load_production(paths), M.LiveModels), "the pointer loads"


@pytest.mark.parametrize(
    "bad",
    [
        {"gate": None, "calls": [], "calls_ok": False, "timing": [], "timing_ok": True},
        {"gate": None, "calls": [], "calls_ok": True, "timing": [], "timing_ok": False},
        {"gate": {"ok": False}, "calls": [], "calls_ok": True, "timing": [], "timing_ok": True},
    ],
    ids=["calls-fail", "timing-fails", "gate-fails"],
)
def test_run_train_refuses_to_promote_when_a_check_fails(tmp_path, train_env, bad) -> None:
    train_env["checks"] = {**bad, "budget_ms": 20.0}
    paths = DataPaths(tmp_path)
    with pytest.raises(RuntimeError, match="not promoted"):
        T.run_train(seasons=FIT_SEASONS, promote=True, n_boot=2, use_wandb=False,
                    log=lambda _s: None, paths=paths)  # fmt: skip
    assert not (T.root(paths) / "production.json").exists(), "a failed check must not promote"
    # the same checks without --promote only report
    out = T.run_train(seasons=FIT_SEASONS, n_boot=2, use_wandb=False, log=lambda _s: None,
                      paths=paths)  # fmt: skip
    assert out["promoted"] is False


def test_run_train_defaults_the_seasons_from_the_config(tmp_path, train_env) -> None:
    out = T.run_train(n_boot=2, use_wandb=False, log=lambda _s: None, paths=DataPaths(tmp_path))
    seasons = train_env["fit_seasons"]
    assert seasons == list(range(seasons[0], seasons[-1] + 1)) and seasons[0] == 2010
    assert out["version"].startswith(f"{seasons[0]}-{seasons[-1]}_")


# --- the CLI ------------------------------------------------------------------------------------
def call(state, *args):
    payload = state if isinstance(state, str) else json.dumps(state)
    return runner.invoke(app, ["live", "call", "--state", payload, *args])


@pytest.fixture()
def stub_production(monkeypatch):
    monkeypatch.setattr(T, "load_production", lambda paths=None: make_models(boots=[AGREE] * 3))


def test_live_group_lists_its_commands() -> None:
    r = runner.invoke(app, ["live", "--help"])
    assert r.exit_code == 0
    for name in ("train", "backtest", "call"):
        assert name in r.output


def test_call_a_fourth_down_prints_json_with_the_best_choice(stub_production) -> None:
    r = call(WIDE.as_dict())
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["best"] == "punt" and set(out["wp"]) == {"go", "fg", "punt"}
    assert out["state"]["down"] == 4 and out["state"]["yardline_100"] == 80
    assert out["wp"]["fg"] is None and out["label"] == "Confident", "wide call: gated, Confident"
    assert 0 < out["wp_now"] < 1 and out["gap"] > 0.05


def test_call_without_bootstrap_has_no_confidence_share(stub_production) -> None:
    r = call(WIDE.as_dict(), "--no-bootstrap")
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["boot_share"] is None and out["label"] is None


def test_call_a_third_down_prints_the_table(stub_production) -> None:
    r = call(GameState(2025, 0, 1500, 1500, 3, 6, 45).as_dict())
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert len(out["table"]) == 16 and out["table"][0]["gain"] == -10
    assert out["note"] is None and out["convert"] == 0.0 and out["pass_prob"] == pytest.approx(0.55)


def test_call_a_first_down_is_a_usage_error(stub_production) -> None:
    r = call(GameState(2025, 0, 1500, 1500, 1, 10, 75).as_dict())
    assert r.exit_code == 2 and "3rd and 4th downs" in flat(r.output)


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ("not json", "Expecting value"),
        ('{"season": 2025}', "required"),  # missing fields: a TypeError from the dataclass
        (json.dumps({**WIDE.as_dict(), "extra": 1}), "unknown game-state fields: extra"),
        (json.dumps({**WIDE.as_dict(), "down": 9}), "bad game state"),
    ],
    ids=["bad-json", "missing-fields", "unknown-field", "invalid-state"],
)
def test_call_with_a_bad_state_exits_non_zero_with_a_clear_message(
    stub_production, state: str, message: str
) -> None:
    r = call(state)
    assert r.exit_code == 2, r.output
    assert message in flat(r.output), flat(r.output)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("game_seconds", None, "game_seconds must be a number"),
        ("half_seconds", None, "half_seconds must be a number"),
        ("spread", None, "spread must be a number"),
        ("yardline_100", 80.5, "yardline_100 must be a whole number"),
        ("down", "4", "down must be a whole number"),
        ("wind", "calm", "wind must be a number or null"),
    ],
)
def test_call_with_a_null_or_mistyped_field_prints_the_clear_message(
    stub_production, field: str, value, message: str
) -> None:
    """JSON `null` / a string / a fractional yard is a usage error (exit 2) that says which
    field and why, not a Python traceback or a comparison TypeError."""
    r = call({**WIDE.as_dict(), field: value})
    assert r.exit_code == 2 and isinstance(r.exception, SystemExit), r.output
    assert message in flat(r.output), flat(r.output)
    assert "not supported between" not in r.output and "Traceback" not in r.output


def test_call_accepts_null_weather(stub_production) -> None:
    r = call({**WIDE.as_dict(), "wind": None, "temp": None})
    assert r.exit_code == 0, r.output


def test_call_without_promoted_models_says_so(monkeypatch) -> None:
    def missing(paths=None):
        raise FileNotFoundError("no promoted live-decision models yet: run `nfl live train`")

    monkeypatch.setattr(T, "load_production", missing)
    r = call(WIDE.as_dict())
    assert r.exit_code == 1 and "no promoted live-decision models" in flat(r.output)


def test_call_with_a_version_loads_that_folder(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "models" / "live-decisions"
    monkeypatch.setattr(T, "root", lambda paths=None: folder)
    M.save(fitted_bundle(), folder / "v7")
    r = call(WIDE.as_dict(), "--version", "v7", "--no-bootstrap")
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)  # a real fitted bundle drives the engine end to end
    assert out["best"] in ("go", "punt") and all(
        0.0 <= x <= 1.0 for x in out["wp"].values() if x is not None
    )
    r = call(WIDE.as_dict(), "--version", "v8")
    assert r.exit_code == 1 and "missing" in flat(r.output), (
        "an unknown version names what's missing"
    )


def test_train_command_passes_its_options_through(monkeypatch) -> None:
    seen = {}

    def fake_run_train(**kw):
        seen.update(kw)
        return {"version": "v", "checks": {"calls_ok": True, "timing_ok": True}, "url": None}

    monkeypatch.setattr(T, "run_train", fake_run_train)
    r = runner.invoke(
        app, ["live", "train", "--seasons", "2015-2017", "--promote", "--n-boot", "3", "--no-wandb"]
    )
    assert r.exit_code == 0, r.output
    assert seen["seasons"] == [2015, 2016, 2017] and seen["promote"] is True
    assert seen["n_boot"] == 3 and seen["use_wandb"] is False
    assert "checks ok" in r.output
    fake = {"version": "v", "checks": {"calls_ok": False, "timing_ok": True}, "url": "http://w"}
    monkeypatch.setattr(T, "run_train", lambda **kw: fake)
    r = runner.invoke(app, ["live", "train", "--no-wandb"])
    assert "checks FAILED" in r.output and "http://w" in r.output
