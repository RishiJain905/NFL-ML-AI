"""The control room's JSON rules: NaN / inf → null, dates ISO, numpy scalars plain (README §9)."""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import numpy as np

from nflengine.app.jsonsafe import SafeJSONResponse, dumps, to_jsonable


def test_nan_and_inf_become_null_everywhere():
    data = {"a": math.nan, "b": [1.5, math.inf, -math.inf], "c": {"d": float("nan")}}
    assert to_jsonable(data) == {"a": None, "b": [1.5, None, None], "c": {"d": None}}


def test_output_is_strict_json_a_browser_accepts():
    text = dumps({"roof": float("nan"), "n": np.float64("nan"), "k": np.int64(3)})
    parsed = json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    assert parsed == {"roof": None, "n": None, "k": 3}


def test_dates_paths_and_numpy_types():
    t = dt.datetime(2026, 10, 8, 20, 15, tzinfo=dt.UTC)
    out = to_jsonable(
        {
            "t": t,
            "d": dt.date(2026, 10, 6),
            "p": Path("x") / "y",
            "f": np.float32(0.25),
            "b": np.bool_(True),
            "s": {3},
        }
    )
    assert out["t"] == "2026-10-08T20:15:00+00:00"
    assert out["d"] == "2026-10-06"
    assert out["p"].replace("\\", "/") == "x/y"
    assert out["f"] == 0.25 and out["b"] is True and out["s"] == [3]


def test_response_class_renders_utf8_without_nan():
    body = SafeJSONResponse({"x": math.nan, "name": "Gaël"}).body
    assert body.decode("utf-8") == '{"x": null, "name": "Gaël"}'
