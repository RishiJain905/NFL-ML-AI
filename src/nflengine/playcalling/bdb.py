"""The Big Data Bowl concept library (PC03): "See a real one".

A play diagram (`diagrams.py`) is reconstructed from charting data, because no public tracking
exists for 2024-2026 games (D110). For five passing concepts the Play calling pages can also
show a REAL tracked play of the same idea, from the NFL Big Data Bowl 2026 data (2023 weeks
1-18, pass plays): the passer, every route runner and the coverage defenders 10 times a second
until the throw, then the targeted receiver and the defenders near the ball until it lands, with
the play's labels (the target's route, play-action, the pass length, the coverage, EPA).

Why this edition (checked 2026-10-17): BDB 2025 and 2024 (whole plays, runs included) are
withdrawn from Kaggle (a README is all they serve); BDB 2023 stops at the throw; BDB 2027 tracks
only Combine rookies. So runs and RPOs have no real example; their diagrams keep the button off.

The data's terms (the competition's rules): CC BY-NC 4.0, "non-commercial purposes only,
including ... academic research and education", and no passing the data to anyone who hasn't
accepted the rules. So the files stay on this computer: raw files under
`{NFL_DATA_ROOT}/bdb/<edition>/raw/`, the library under `playcalling/bdb_library/`; nothing goes
into git, W&B or a published page.

- `list_files` / `download`: Kaggle's API with the settings module's KAGGLE_API_TOKEN (or
  KAGGLE_USERNAME + KAGGLE_KEY) (the secret never leaves the HTTP client); Rishi joins the
  competition on Kaggle first.
- `build_library`: picks about `per_concept` clean examples of each concept from the weeks on
  disk and writes one compact JSON per play (the diagram shape of `diagrams.py`, `kind: "real"`)
  plus `index.json` (written last).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import shutil
import zipfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import polars as pl

from nflengine.fsutil import replace_file
from nflengine.paths import DataPaths
from nflengine.playcalling import diagrams as D

EDITION = "2026"
COMPETITION = "nfl-big-data-bowl-2026-analytics"
# BDB 2026's tracking files are 2023 weeks 1-18 (targeted passes) and named after 2023; its
# label file also lists 2024 weeks 14-18 (Kaggle's held-out set: labels, no tracking), so a
# play's season comes from its own label row, and only tracked plays are candidates
SEASON = 2023  # the tracking files' season (their names)
WEEKS = (1, 18)
KAGGLE_API = "https://www.kaggle.com/api/v1"
NO_CREDENTIALS = (
    "neither KAGGLE_API_TOKEN nor KAGGLE_USERNAME + KAGGLE_KEY is set (nfl doctor lists them)"
)
# file names on disk (Kaggle keeps them in a folder: `kaggle_names` finds the full names)
NEEDED = ("supplementary_data.csv",)
INPUT = "input_{season}_w{week:02d}.csv"  # tracking before the throw (every tracked player)
OUTPUT = "output_{season}_w{week:02d}.csv"  # after the throw (the target, nearby defenders)
LIBRARY = "bdb_library"
PER_CONCEPT = 10
FPS = 10
NOTE = (
    "Big Data Bowl 2026 data: NFL Next Gen Stats tracking of 2023 pass plays, used under the "
    "competition's rules (CC BY-NC 4.0, non-commercial, not redistributed), kept on this "
    "computer only."
)
ROLE = {
    "QB": "qb",
    "RB": "back",
    "FB": "back",
    "HB": "back",
    "WR": "receiver",
    "TE": "receiver",
    "T": "ol",
    "G": "ol",
    "C": "ol",
    "DE": "dl",
    "DT": "dl",
    "NT": "dl",
    "OLB": "lb",
    "ILB": "lb",
    "MLB": "lb",
    "LB": "lb",
    "CB": "db",
    "SS": "db",
    "FS": "db",
    "S": "db",
    "DB": "db",
}
# BDB's team codes -> the project's (only those that differ)
TEAM_FIX = {"LAR": "LA", "OAK": "LV", "SD": "LAC", "STL": "LA", "WSH": "WAS"}


# ---- Kaggle -------------------------------------------------------------------------------------
class KaggleError(RuntimeError):
    """A Kaggle call failed; the message never carries the key."""


def kaggle_auth() -> tuple[str, str] | None:
    """The credentials from the settings module: ("", token) for Kaggle's newer API token
    (KAGGLE_API_TOKEN, a Bearer header: no username needed), else (username, key) for a
    classic key (KAGGLE_USERNAME + KAGGLE_KEY, HTTP basic); None when neither is set."""
    from nflengine.settings import get_env

    env = get_env()
    if env.kaggle_api_token is not None:
        token = env.kaggle_api_token.get_secret_value().strip()
        if token:
            return ("", token)
    if not env.kaggle_username or env.kaggle_key is None:
        return None
    key = env.kaggle_key.get_secret_value().strip()
    return (env.kaggle_username.strip(), key) if key else None


def _check_auth(auth: tuple[str, str]) -> None:
    """Refuse credentials an HTTP header can't carry (a space or a line break inside): the HTTP
    stack's own error would quote the header, secret included (Sol, PC03). Never echoes them."""
    for part in auth:
        if any(ch.isspace() or not 32 < ord(ch) < 127 for ch in part):
            raise KaggleError(
                "the Kaggle credentials contain a space, a line break or a non-ASCII "
                "character: fix them in the env "
                "file (the value isn't shown)"
            )


@contextmanager
def _requests(what: str) -> Iterator[None]:
    """Kaggle calls fail with fixed text: an HTTP library error (a refused connection, a
    protocol error that may quote a header) becomes a KaggleError naming only its type, and its
    message and chain are dropped (Sol, PC03)."""
    try:
        yield
    except httpx.HTTPError as e:
        raise KaggleError(f"{what}: the request failed ({type(e).__name__})") from None


def _client(auth: tuple[str, str], transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        auth=auth,
        timeout=httpx.Timeout(60.0, read=600.0),
        follow_redirects=True,
        transport=transport,
    )


@contextmanager
def _session(
    auth: tuple[str, str], competition: str, transport: httpx.BaseTransport | None = None
) -> Iterator[httpx.Client]:
    """A client that Kaggle accepts, closed afterwards: the classic username + key (HTTP basic)
    first; if Kaggle answers 401, a newer API token as a Bearer header. One probe of the file
    list decides. (A context manager: the probe opens the client, so callers can't `with` it.)"""
    _check_auth(auth)
    probe = f"{KAGGLE_API}/competitions/data/list/{competition}"
    c = _client(auth, transport)
    try:
        # no username = an API token: Bearer straight away; else basic, Bearer on a 401
        if not auth[0] or c.get(probe, params={"pageSize": 1}).status_code == 401:
            c.close()
            c = httpx.Client(
                headers={"Authorization": f"Bearer {auth[1]}"},
                timeout=httpx.Timeout(60.0, read=600.0),
                follow_redirects=True,
                transport=transport,
            )
        yield c
    finally:
        c.close()


def _fail(what: str, r: httpx.Response) -> KaggleError:
    hint = {
        401: "Kaggle refused the credentials (KAGGLE_API_TOKEN, or KAGGLE_USERNAME + KAGGLE_KEY)",
        403: "Kaggle refused: join the competition on kaggle.com and accept its rules first",
        404: "Kaggle has no such competition or file",
    }.get(r.status_code, f"Kaggle answered HTTP {r.status_code}")
    return KaggleError(f"{what}: {hint}")


def list_files(
    competition: str = COMPETITION,
    auth: tuple[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """The competition's files: [{"name", "bytes"}], every page."""
    auth = auth or kaggle_auth()
    if auth is None:
        raise KaggleError(NO_CREDENTIALS)
    out: list[dict[str, Any]] = []
    token: str | None = None
    with _requests("listing the files"), _session(auth, competition, transport) as c:
        for _ in range(50):
            params = {"pageSize": 200}
            if token:
                params["pageToken"] = token
            r = c.get(f"{KAGGLE_API}/competitions/data/list/{competition}", params=params)
            if r.status_code != 200:
                raise _fail("listing the files", r)
            try:
                body = r.json()
                files = body.get("files", body) if isinstance(body, dict) else body
                for f in files or []:
                    name = f.get("name") or f.get("ref")
                    size = f.get("totalBytes") or f.get("size") or 0
                    if name:
                        out.append({"name": str(name), "bytes": int(size or 0)})
                token = body.get("nextPageToken") if isinstance(body, dict) else None
            except (ValueError, AttributeError, TypeError):
                raise KaggleError("listing the files: Kaggle's answer wasn't a file list") from None
            if not token:
                break
    return out


def raw_dir(paths: DataPaths, edition: str = EDITION) -> Path:
    """Where an edition's files live: `bdb/<edition>/raw/` (the P10 guide's layout, §9)."""
    return paths.bdb / edition / "raw"


def library_dir(paths: DataPaths) -> Path:
    return paths.playcalling / LIBRARY


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(
    paths: DataPaths,
    names: Iterable[str],
    competition: str = COMPETITION,
    auth: tuple[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Download `names` into `raw_dir` (streamed; a zipped answer is unpacked next to it) and
    record them in `raw/manifest.json` (name, bytes, sha256, when). Files already there with
    the manifest's size are skipped."""
    auth = auth or kaggle_auth()
    if auth is None:
        raise KaggleError(NO_CREDENTIALS)
    raw = raw_dir(paths)
    raw.mkdir(parents=True, exist_ok=True)
    man_path = raw / "manifest.json"
    manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {}
    files = manifest.setdefault("files", {})
    done: list[dict[str, Any]] = []
    with _requests("downloading"), _session(auth, competition, transport) as c:
        for name in names:
            # Kaggle's names can carry folders: keep the file name only, and refuse one that
            # could point outside raw/ (either separator, '.', '..')
            local = re.split(r"[\\/]", name)[-1]
            if local in ("", ".", "..") or ":" in local:
                raise KaggleError(f"refusing a file name that isn't a plain file: {local!r}")
            dest = raw / local
            if dest.exists() and files.get(local, {}).get("bytes") == dest.stat().st_size:
                log(f"{local}: already on disk")
                continue
            tmp = raw / f".{local}.part"
            try:
                with c.stream(
                    "GET",
                    f"{KAGGLE_API}/competitions/data/download/{competition}/{quote(name, safe='')}",
                ) as r:
                    if r.status_code != 200:
                        raise _fail(f"downloading {local}", r)
                    with tmp.open("wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
            except BaseException:
                tmp.unlink(missing_ok=True)  # never leave half a file behind
                raise
            with tmp.open("rb") as f:
                zipped = f.read(4) == b"PK\x03\x04"
            if zipped:
                zpath = raw / f"{local}.zip"
                tmp.replace(zpath)
                with zipfile.ZipFile(zpath) as z:
                    member = next((m for m in z.namelist() if m.endswith(local)), z.namelist()[0])
                    with z.open(member) as src, (raw / f".{local}.unzip").open("wb") as out:
                        shutil.copyfileobj(src, out, 1 << 20)
                (raw / f".{local}.unzip").replace(dest)
            else:
                tmp.replace(dest)
            entry = {
                "bytes": dest.stat().st_size,
                "sha256": _sha256(dest),
                "downloaded": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "competition": competition,
                "kaggle_name": name,
            }
            files[local] = entry
            man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            log(f"{local}: {entry['bytes'] / 1e6:.1f} MB")
            done.append({"name": local, **entry})
    return done


def kaggle_names(files: Iterable[dict[str, Any]], wanted: Iterable[str]) -> list[str]:
    """Kaggle's full names (with their folders) for the file names on disk we want, in order;
    a name Kaggle doesn't list raises (so a renamed file fails loudly, not silently)."""
    by_base = {str(f["name"]).rsplit("/", 1)[-1]: str(f["name"]) for f in files}
    missing = [w for w in wanted if w not in by_base]
    if missing:
        raise KaggleError(f"Kaggle doesn't list {', '.join(missing)} for {COMPETITION}")
    return [by_base[w] for w in wanted]


def week_files(weeks: Iterable[int]) -> list[str]:
    return [f.format(season=SEASON, week=w) for w in weeks for f in (INPUT, OUTPUT)]


# ---- reading the raw files ----------------------------------------------------------------------
@dataclass
class Raw:
    supp: pl.DataFrame  # one row per play: the labels
    weeks: list[int]  # weeks with both tracking files on disk


def tracking_weeks(paths: DataPaths) -> list[int]:
    raw = raw_dir(paths)
    out = []
    for p in raw.glob(f"input_{SEASON}_w*.csv"):
        m = re.fullmatch(rf"input_{SEASON}_w(\d+)\.csv", p.name)
        if m and (raw / OUTPUT.format(season=SEASON, week=int(m.group(1)))).is_file():
            out.append(int(m.group(1)))
    return sorted(out)


def load_raw(paths: DataPaths) -> Raw:
    raw = raw_dir(paths)
    missing = [n for n in NEEDED if not (raw / n).is_file()]
    if missing:
        raise FileNotFoundError(f"Big Data Bowl files missing under bdb/{EDITION}/raw: {missing}")
    supp = pl.read_csv(
        raw / "supplementary_data.csv", null_values=["NA", ""], infer_schema_length=50000
    )
    return Raw(supp=supp, weeks=tracking_weeks(paths))


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def coverage_concept(cov: Any) -> str | None:
    """The coverage label -> cover_1 / cover_3 / 2_man (the library's coverage concepts):
    'COVER_1_MAN', 'Cover-1' -> cover_1; 'COVER_3_ZONE' -> cover_3; 'COVER_2_MAN', '2-Man' ->
    2_man; anything else (Cover 2 zone, quarters, Cover 6, Cover 0 ...) -> None."""
    c = _norm(cov)
    if c.startswith("cover 1"):
        return "cover_1"
    if c.startswith("cover 3"):
        return "cover_3"
    if c in ("2 man", "cover 2 man"):
        return "2_man"
    return None


def _truthy(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return not (isinstance(v, float) and math.isnan(v)) and v != 0
    return str(v).strip().upper() in ("TRUE", "Y", "YES", "1")


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def classify(supp: pl.DataFrame) -> pl.DataFrame:
    """Each clean play's concepts (a play can be in several): rows (game_id, play_id, week,
    concept, good). Clean = not nullified by a penalty, no penalty yards, a pass thrown.
    Concepts: screen (the target's route), play_action_deep (play-action, 20+ air yards),
    cover_1 / cover_3 / 2_man (the coverage). `good` = the play gained expected points."""
    rows = []
    for r in supp.iter_rows(named=True):
        if _truthy(r.get("play_nullified_by_penalty")) or (_num(r.get("penalty_yards")) or 0) != 0:
            continue
        if str(r.get("pass_result") or "") not in ("C", "I", "IN"):
            continue
        found: list[str] = []
        if _norm(r.get("route_of_targeted_receiver")) == "screen":
            found.append("screen")
        length = _num(r.get("pass_length"))
        if _truthy(r.get("play_action")) and length is not None and length >= 20:
            found.append("play_action_deep")
        cc = coverage_concept(r.get("team_coverage_type"))
        if cc:
            found.append(cc)
        epa = _num(r.get("expected_points_added"))
        for c in found:
            rows.append(
                {
                    "game_id": int(r["game_id"]),
                    "play_id": int(r["play_id"]),
                    "week": int(r["week"]),
                    "concept": c,
                    "good": bool(epa is not None and epa > 0),
                }
            )
    return pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Int64,
            "play_id": pl.Int64,
            "week": pl.Int64,
            "concept": pl.String,
            "good": pl.Boolean,
        },
    )


def pick(candidates: pl.DataFrame, weeks: list[int], per_concept: int) -> pl.DataFrame:
    """About `per_concept` examples per concept from the weeks on disk, spread over the season:
    one play per game, the games taken week by week in turn (week 1's first game, week 2's
    first ... then each week's second game), plays that gained expected points first.
    Deterministic."""
    c = candidates.filter(pl.col("week").is_in(weeks))
    out = []
    for concept in D.CONCEPTS:
        sub = c.filter(pl.col("concept") == concept).sort(
            "good", "game_id", "play_id", descending=[True, False, False]
        )
        by_game: dict[int, list[dict[str, Any]]] = {}
        week_of: dict[int, int] = {}
        for r in sub.iter_rows(named=True):
            by_game.setdefault(r["game_id"], []).append(r)
            week_of[r["game_id"]] = r["week"]
        per_week: dict[int, list[int]] = {}
        for g in sorted(by_game):
            per_week.setdefault(week_of[g], []).append(g)
        order = [
            per_week[w][k]
            for k in range(max((len(v) for v in per_week.values()), default=0))
            for w in sorted(per_week)
            if k < len(per_week[w])
        ]
        chosen: list[dict[str, Any]] = []
        while len(chosen) < per_concept and any(by_game.values()):
            for g in order:
                if by_game[g] and len(chosen) < per_concept:
                    chosen.append(by_game[g].pop(0))
        out += chosen
    return pl.DataFrame(out, schema=candidates.schema) if out else candidates.head(0)


def tracked_keys(paths: DataPaths, weeks: Iterable[int]) -> pl.DataFrame:
    """(game_id, play_id) of every play in the weeks' input tracking files (two columns
    scanned): the label file lists plays without tracking (2024 weeks 14-18)."""
    raw = raw_dir(paths)
    frames = [
        pl.scan_csv(raw / INPUT.format(season=SEASON, week=w), infer_schema_length=50000)
        .select(pl.col("game_id").cast(pl.Int64), pl.col("play_id").cast(pl.Int64))
        .unique()
        .collect()
        for w in weeks
        if (raw / INPUT.format(season=SEASON, week=w)).is_file()
    ]
    if not frames:
        return pl.DataFrame(schema={"game_id": pl.Int64, "play_id": pl.Int64})
    return pl.concat(frames).unique()


def read_frames(
    paths: DataPaths, week: int, keys: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The input (before the throw) and output (after it) frames of the plays in `keys`
    (game_id, play_id), scanned lazily."""
    raw = raw_dir(paths)
    k = keys.lazy().select("game_id", "play_id")
    opts = {"null_values": ["NA", ""], "infer_schema_length": 50000}
    inp = (
        pl.scan_csv(raw / INPUT.format(season=SEASON, week=week), **opts)
        .join(k, on=["game_id", "play_id"], how="semi")
        .collect()
    )
    out = (
        pl.scan_csv(raw / OUTPUT.format(season=SEASON, week=week), **opts)
        .join(k, on=["game_id", "play_id"], how="semi")
        .collect()
    )
    return inp, out


# ---- one real play -> the diagram shape ---------------------------------------------------------
def _team(code: Any) -> str | None:
    s = str(code).strip() if code is not None else ""
    return TEAM_FIX.get(s, s) or None


def _clock(gc: Any) -> str | None:
    s = str(gc or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", s)
    return f"{int(m.group(1))}:{m.group(2)}" if m else None


def _pretty(label: Any) -> str:
    """'COVER_3_ZONE' -> 'Cover 3 zone', 'SHOTGUN' -> 'shotgun' style text."""
    s = str(label or "").replace("_", " ").strip().lower()
    return s[:1].upper() + s[1:] if s else ""


def real_diagram(
    play: dict[str, Any], inp: pl.DataFrame, out: pl.DataFrame, concept: str
) -> dict[str, Any] | None:
    """One tracked play in `diagrams.py`'s shape (`kind: "real"`): every tracked player's keys
    at 10 a second until the throw, the target and nearby defenders after it until the ball
    lands, the offense moving to +x (x 0 = its own goal line), y from its left sideline.
    None when the play lacks a passer, a target or the landing spot."""

    def finite(df: pl.DataFrame) -> pl.DataFrame:
        if df.is_empty():
            return df
        return df.with_columns(pl.col("x", "y").cast(pl.Float64, strict=False)).filter(
            pl.col("x").is_finite() & pl.col("y").is_finite()
        )

    inp, out = finite(inp), finite(out)
    if inp.is_empty():
        return None
    inp = inp.sort("nfl_id", "frame_id")
    direction = str(inp["play_direction"][0])

    def xy(x: float, y: float) -> tuple[float, float]:
        # to "offense moves to +x": a left-moving play is turned 180 degrees; then x - 10 puts
        # the offense's goal line at 0 and y is measured from its left sideline (BDB's y runs
        # up the page, so the left of an offense moving right is the high-y sideline)
        if direction == "left":
            x, y = 120.0 - x, D.FIELD_WIDTH - y
        return round(x - 10.0, 2), round(D.FIELD_WIDTH - y, 2)

    n_in = int(inp["frame_id"].max())
    t_rel = round((n_in - 1) / FPS, 2)  # the throw: the last frame before the pass
    n_out = int(_num(inp["num_frames_output"].max()) or 0)
    land_x, land_y = _num(inp["ball_land_x"][0]), _num(inp["ball_land_y"][0])
    if land_x is None or land_y is None:
        return None
    after: dict[int, list[list[float]]] = {}
    for r in out.sort("nfl_id", "frame_id").iter_rows(named=True):
        x, y = xy(float(r["x"]), float(r["y"]))
        after.setdefault(int(r["nfl_id"]), []).append(
            [round(t_rel + int(r["frame_id"]) / FPS, 2), x, y]
        )
    players: list[dict[str, Any]] = []
    passer_keys: list[list[float]] | None = None
    for (nid,), g in inp.group_by(["nfl_id"], maintain_order=True):
        first = g.row(0, named=True)
        keys = [
            [round((int(r["frame_id"]) - 1) / FPS, 2), *xy(float(r["x"]), float(r["y"]))]
            for r in g.iter_rows(named=True)
        ]
        keys += after.get(int(nid), [])
        side = "offense" if str(first.get("player_side")).lower() == "offense" else "defense"
        role_txt = str(first.get("player_role") or "")
        pos = str(first.get("player_position") or "")
        role = ROLE.get(pos, "receiver" if side == "offense" else "db")
        if role == "qb":  # only the passer is drawn as the QB
            role = "receiver" if side == "offense" else "db"
        if role_txt == "Passer":
            role, passer_keys = "qb", keys
        players.append(
            {
                "id": f"{'o' if side == 'offense' else 'd'}_{int(nid)}",
                "side": side,
                "role": role,
                "label": pos or ("O" if side == "offense" else "D"),
                "approx": False,
                "rusher": False,
                "target": role_txt == "Targeted Receiver",
                "carrier": role_txt == "Targeted Receiver" and str(play.get("pass_result")) == "C",
                "track": keys,
            }
        )
    if passer_keys is None or not any(p["target"] for p in players):
        return None
    # the ball: with the passer until the throw (it isn't tracked in this edition), then from
    # his hand to where it landed, over the recorded time in the air
    ball = [[k[0], k[1], k[2], 1.0] for k in passer_keys if k[0] <= t_rel]
    rel = ball[-1]
    lx, ly = xy(land_x, land_y)
    flight = max(n_out, 1) / FPS
    peak = min(6.0, 1.0 + math.hypot(lx - rel[1], ly - rel[2]) / 8.0)
    for k in range(1, 9):
        s_ = k / 8
        ball.append(
            [
                round(t_rel + flight * s_, 2),
                round(rel[1] + (lx - rel[1]) * s_, 2),
                round(rel[2] + (ly - rel[2]) * s_, 2),
                round(1.0 + 4 * peak * s_ * (1 - s_), 2),
            ]
        )
    t_land = ball[-1][0]
    ayl = _num(inp["absolute_yardline_number"][0])
    los = (
        round((120.0 - ayl if direction == "left" else ayl) - 10.0, 2)
        if ayl is not None
        else rel[1]
    )
    togo = _num(play.get("yards_to_go"))
    first_down = round(min(100.0, los + togo), 2) if togo is not None else None
    if first_down is not None and first_down >= 100.0:
        first_down = None
    off, dfn = _team(play.get("possession_team")), _team(play.get("defensive_team"))
    pr = str(play.get("pass_result") or "")
    yards = _num(play.get("yards_gained"))
    length = _num(play.get("pass_length"))
    desc = play.get("play_description")
    row = {
        "offense": off,
        "defense": dfn,
        "yards_gained": yards,
        "touchdown": "TOUCHDOWN" in str(desc or "").upper(),
        "first_down": yards is not None and togo is not None and yards >= togo,
        "air_yards": length,
        "pass_attempt": True,
        "complete": pr == "C",
        "interception": pr == "IN",
        "dropback": True,
        "desc": desc,
    }
    result = D.result_of(row)
    marks = [
        {
            "kind": "throw",
            "points": [[rel[1], rel[2]], [lx, ly]],
            "t": t_rel,
            "approx": False,
            "text": None,
        },
        {
            "kind": "end" if pr == "C" else ("interception" if pr == "IN" else "incomplete"),
            "points": [[lx, ly]],
            "t": t_land,
            "approx": False,
            "text": result["text"],
        },
    ]
    phases = [
        {"name": "start", "t": 0.0, "text": "Tracking starts (the snap)"},
        {"name": "pass_forward", "t": t_rel, "text": f"Throw: {D.air_text(length) or 'the pass'}"},
        {"name": "pass_arrived", "t": t_land, "text": "The ball lands: " + result["text"]},
    ]
    week = int(play["week"])
    season = int(play.get("season") or SEASON)
    home, away = _team(play.get("home_team_abbr")), _team(play.get("visitor_team_abbr"))
    ytg = round(100 - los) if los is not None else None
    tags = []
    if _truthy(play.get("play_action")):
        tags.append("play_action")
    if _norm(play.get("route_of_targeted_receiver")) == "screen":
        tags.append("screen")
    if length is not None and length >= 20:
        tags.append("deep_shot")
    known = [
        "Where the passer, every route runner and the coverage defenders were, 10 times a "
        "second until the throw (NFL Next Gen Stats tracking)",
        "After the throw: the target and the defenders near the ball until it lands, and where "
        "it landed",
    ]
    if play.get("team_coverage_type"):
        known.append(f"Coverage: {_pretty(play['team_coverage_type'])}")
    if play.get("route_of_targeted_receiver"):
        known.append(f"The target's route: {_pretty(play['route_of_targeted_receiver']).lower()}")
    if play.get("offense_formation"):
        known.append(
            f"Formation: {_pretty(play['offense_formation']).lower()}"
            + (
                f", receivers {play['receiver_alignment']}"
                if play.get("receiver_alignment")
                else ""
            )
        )
    if _truthy(play.get("play_action")):
        known.append("Play-action")
    xs = [k[1] for p in players for k in p["track"]] + [k[1] for k in ball]
    summary = {
        "game_id": f"{season}_{week:02d}_{away}_{home}",
        "play_id": int(play["play_id"]),
        "week": week,
        "offense": off,
        "defense": dfn,
        "quarter": int(play["quarter"]) if play.get("quarter") is not None else None,
        "quarter_text": D.quarter_text(play.get("quarter")),
        "clock": _clock(play.get("game_clock")),
        "down": int(play["down"]) if play.get("down") is not None else None,
        "distance": int(togo) if togo is not None else None,
        "down_text": D.down_text(play.get("down"), togo, first_down is None),
        "yards_to_goal": ytg,
        "spot": D.spot_text(ytg, off, dfn),
        "type": "pass",
        "tags": tags,
        "result": result,
        "epa": round(e, 2) if (e := _num(play.get("expected_points_added"))) is not None else None,
        "explosive": bool(yards is not None and yards >= 20),
        "charted": True,
        "concept": concept,
        "desc": D.clean_desc(desc),
        "game_label": f"{away} at {home}, {season} week {week}",
        "season": season,
    }
    duration = max([k[0] for p in players for k in p["track"]] + [k[0] for k in ball])
    return {
        "version": D.VERSION,
        "kind": "real",
        "label": f"Real play: {season} week {week}, Big Data Bowl data",
        "play": summary,
        "field": {
            "los": los,
            "first_down": first_down,
            "goal": 100.0,
            "ball_y": passer_keys[0][2],
            "hash": None,
            "width": round(D.FIELD_WIDTH, 2),
            "view": [round(max(-10.0, min(xs) - 3.0), 2), round(min(110.0, max(xs) + 3.0), 2)],
        },
        "players": players,
        "ball": ball,
        "marks": marks,
        "phases": phases,
        "duration": round(duration + 0.6, 2),
        "known": known,
        "approximate": [
            "The ball before the throw: drawn with the passer (this edition doesn't track it)",
            "The ball's height in the air (only where it landed is recorded)",
            "Players not tracked after the throw are held where they were at the throw",
        ],
        "unknown": [
            "The linemen and the pass rushers (this edition tracks the passing game only)",
            "What happened after the ball landed (the run after the catch)",
        ],
        "sources": [f"NFL Big Data Bowl 2026 (Next Gen Stats tracking, {season} season)"],
        "concept": concept,
        "real_example": None,
    }


def _install(stage: Path, out_dir: Path) -> None:
    """Put the staged library in place: the old one moved aside, the new one moved in, the old
    one removed; each move retried on Windows' PermissionError (a reader holding a file) and the
    old library restored if the new one can't be moved in (Sol, PC03)."""
    if not out_dir.exists():
        replace_file(stage, out_dir)
        return
    old = out_dir.parent / f".{LIBRARY}.old"
    if old.exists():
        shutil.rmtree(old)
    replace_file(out_dir, old)
    try:
        replace_file(stage, out_dir)
    except OSError:
        replace_file(old, out_dir)  # the old library back; the stage stays for a retry
        raise
    shutil.rmtree(old, ignore_errors=True)


def example_label(d: dict[str, Any]) -> str:
    p = d["play"]
    bits = [f"{p.get('season', SEASON)} week {p['week']}", f"{p['offense']} vs {p['defense']}"]
    if p.get("down_text"):
        bits.append(p["down_text"])
    return " · ".join(bits) + f" · {p['result']['text']}"


def build_library(
    paths: DataPaths,
    per_concept: int = PER_CONCEPT,
    weeks: list[int] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Pick the examples and write `playcalling/bdb_library/` (one JSON per play, then
    `index.json` last; the old library is replaced only when the new one is complete)."""
    raw = load_raw(paths)
    weeks = sorted(set(weeks or raw.weeks) & set(raw.weeks))
    if not weeks:
        raise FileNotFoundError(f"no input / output tracking files under bdb/{EDITION}/raw")
    cand = classify(raw.supp).join(
        tracked_keys(paths, weeks), on=["game_id", "play_id"], how="semi"
    )
    chosen = pick(cand, weeks, per_concept)
    log(f"candidates {cand.height}, chosen {chosen.height} from weeks {weeks}")
    supp = {(int(r["game_id"]), int(r["play_id"])): r for r in raw.supp.iter_rows(named=True)}
    out_dir = library_dir(paths)
    stage = out_dir.parent / f".{LIBRARY}.new"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    index: dict[str, Any] = {
        "edition": EDITION,
        "competition": COMPETITION,
        "season": SEASON,
        "weeks": weeks,
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "note": NOTE,
        "concepts": {},
    }
    for week in weeks:
        wk = chosen.filter(pl.col("week") == week)
        if wk.is_empty():
            continue
        inp, out = read_frames(paths, week, wk)
        log(f"week {week}: {wk.height} plays, {inp.height} + {out.height} frames")
        for r in wk.iter_rows(named=True):
            key = (int(r["game_id"]), int(r["play_id"]))
            sel = (pl.col("game_id") == key[0]) & (pl.col("play_id") == key[1])
            try:
                d = real_diagram(supp[key], inp.filter(sel), out.filter(sel), r["concept"])
            except Exception as e:  # noqa: BLE001 - one bad play never stops the library
                log(f"  skipped {key} ({r['concept']}): {type(e).__name__}")
                continue
            if d is None:
                log(f"  skipped {key} ({r['concept']}): no passer, target or landing spot")
                continue
            name = f"{key[0]}_{key[1]}.json"
            (stage / r["concept"]).mkdir(exist_ok=True)
            (stage / r["concept"] / name).write_text(
                json.dumps(d, separators=(",", ":")), encoding="utf-8"
            )
            entry = index["concepts"].setdefault(
                r["concept"], {"name": D.CONCEPTS[r["concept"]]["name"], "examples": []}
            )
            entry["examples"].append({"file": name, "label": example_label(d)})
    (stage / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    _install(stage, out_dir)
    counts = {c: len(v["examples"]) for c, v in index["concepts"].items()}
    log(f"library: {counts}")
    return index
