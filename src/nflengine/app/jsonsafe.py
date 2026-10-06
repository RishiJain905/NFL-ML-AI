"""JSON for the control room (documentation/control-room/README.md §9).

Pandas and Polars hand back NaN for blank cells, and a browser rejects a whole response that
contains NaN. Everything the API sends goes through `to_jsonable`: NaN and infinity become
null, dates become ISO strings, numpy scalars become plain numbers.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

from starlette.responses import JSONResponse


def to_jsonable(o: Any) -> Any:
    if o is None or isinstance(o, bool | str | int):
        return o
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, dict):
        return {str(k): to_jsonable(v) for k, v in o.items()}
    if isinstance(o, list | tuple | set | frozenset):
        return [to_jsonable(v) for v in o]
    if isinstance(o, dt.datetime | dt.date | dt.time):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return to_jsonable(dataclasses.asdict(o))
    item = getattr(o, "item", None)  # numpy / polars scalars
    if callable(item) and type(o).__module__.split(".")[0] in ("numpy", "polars"):
        return to_jsonable(item())
    return str(o)


def dumps(content: Any) -> str:
    return json.dumps(to_jsonable(content), allow_nan=False, ensure_ascii=False)


class SafeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return dumps(content).encode("utf-8")
